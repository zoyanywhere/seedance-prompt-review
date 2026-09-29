import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from google import genai
from google.genai import types
from openai import OpenAI

from .db import Media

MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", "./data/media"))

MODEL_IDS = {
    "brief_intake": os.getenv("MODEL_BRIEF", "gpt-6-luna"),
    "storyboard": os.getenv("MODEL_STORYBOARD", "gpt-6-sol"),
    "prompt_draft": os.getenv("MODEL_DRAFT", "gpt-6-sol"),
    "action_timing": os.getenv("MODEL_ACTION", "gpt-6-sol"),
    "camera_visuals": os.getenv("MODEL_VISUAL", "gemini-3.8-flash"),
    "audio_dialogue": os.getenv("MODEL_AUDIO", "gemini-3.8-flash"),
    "continuity": os.getenv("MODEL_CONTINUITY", "claude-sonnet-5-5"),
    "challenge_review": os.getenv("MODEL_CHALLENGE", "claude-opus-5-5"),
    "supervisor": os.getenv("MODEL_SUPERVISOR", "gpt-6-sol"),
    "critical_escalation": os.getenv("MODEL_ESCALATION", "gpt-6-astra"),
    "preview": os.getenv("MODEL_PREVIEW", "gemini-3.1-flash-image"),
}

# USD per million tokens, standard paid tier, checked 2026-09-29.
# Keep this versioned table current before production deployment.
RATES = {
    "gpt-6-luna": (0.10, 0.50, 0.01),
    "gpt-6-sol": (2.00, 10.00, 0.20),
    "gpt-6-astra": (10.00, 50.00, 1.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "gemini-3.8-flash": (0.75, 3.75, 0.075),
}


@dataclass
class ModelResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float | None:
    rate = RATES.get(model)
    if not rate:
        return None
    ordinary = max(0, input_tokens - cached_tokens)
    return round((ordinary * rate[0] + output_tokens * rate[1] + cached_tokens * rate[2]) / 1_000_000, 6)


def preflight_estimate(model: str, text: str, max_output_tokens: int, media_count: int = 0) -> float | None:
    # Heuristic reserve. Provider-reported usage is recorded after the call.
    return estimate_cost(model, max(1000, len(text) // 3) + media_count * 20000, max_output_tokens)


def parse_json(text: str) -> Any:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = min((p for p in (cleaned.find("{"), cleaned.find("[")) if p >= 0), default=-1)
        if start < 0:
            raise ValueError("Model did not return JSON")
        decoder = json.JSONDecoder()
        return decoder.raw_decode(cleaned[start:])[0]


def _image_media(media: list[Media]) -> list[Media]:
    return [m for m in media if m.kind == "image"]


def _read_media(media: Media) -> bytes:
    return (MEDIA_ROOT / media.storage_key).read_bytes()


def call_model(phase: str, prompt: str, media: list[Media] | None = None, *, max_output_tokens: int = 2200) -> ModelResult:
    model = MODEL_IDS[phase]
    media = media or []
    if model.startswith("gpt-"):
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=90, max_retries=1)
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for item in _image_media(media):
            data = base64.b64encode(_read_media(item)).decode("ascii")
            content.append({"type": "input_image", "image_url": f"data:{item.mime};base64,{data}", "detail": "low"})
        response = client.responses.create(model=model, input=[{"role": "user", "content": content}], max_output_tokens=max_output_tokens, store=False)
        usage = response.usage
        details = getattr(usage, "input_tokens_details", None)
        out_details = getattr(usage, "output_tokens_details", None)
        return ModelResult(
            response.output_text or "",
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
            getattr(details, "cached_tokens", 0) or 0,
            getattr(out_details, "reasoning_tokens", 0) or 0,
        )
    if model.startswith("claude-"):
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=90, max_retries=1)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for item in _image_media(media):
            data = base64.b64encode(_read_media(item)).decode("ascii")
            content.append({"type": "image", "source": {"type": "base64", "media_type": item.mime, "data": data}})
        response = client.messages.create(model=model, max_tokens=max_output_tokens, messages=[{"role": "user", "content": content}])
        usage = response.usage
        return ModelResult(
            "\n".join(block.text for block in response.content if getattr(block, "type", "") == "text"),
            usage.input_tokens or 0,
            usage.output_tokens or 0,
            getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
    if model.startswith("gemini-"):
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        parts: list[Any] = [prompt]
        uploaded = []
        try:
            for item in media:
                remote = client.files.upload(file=MEDIA_ROOT / item.storage_key, config={"mime_type": item.mime})
                for _ in range(18):
                    if not remote.state or remote.state.name == "ACTIVE":
                        break
                    if remote.state.name == "FAILED":
                        raise ValueError(f"Media processing failed for {item.filename}")
                    time.sleep(2)
                    remote = client.files.get(name=remote.name)
                else:
                    raise TimeoutError(f"Media processing timed out for {item.filename}")
                uploaded.append(remote.name)
                parts.append(remote)
            response = client.models.generate_content(
                model=model,
                contents=parts,
                config=types.GenerateContentConfig(max_output_tokens=max_output_tokens, response_mime_type="application/json"),
            )
            usage = response.usage_metadata
            return ModelResult(
                response.text or "",
                getattr(usage, "prompt_token_count", 0) or 0,
                getattr(usage, "candidates_token_count", 0) or 0,
                getattr(usage, "cached_content_token_count", 0) or 0,
                getattr(usage, "thoughts_token_count", 0) or 0,
            )
        finally:
            for name in uploaded:
                try:
                    client.files.delete(name=name)
                except Exception:
                    pass
    raise ValueError(f"Unsupported model: {model}")
