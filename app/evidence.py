"""Bounded, timestamped reference frames shared by planning and review."""
import hashlib
import json
import subprocess
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from .providers import MEDIA_ROOT


def source_signature(media):
    payload = [(m.id, m.storage_key, m.size, m.role, m.duration_seconds)
               for m in sorted(media, key=lambda x: x.id)]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


@contextmanager
def reference_frames(media):
    """Files are private temporary derivatives, never new Seedance references.

    Sample every video, with dense frames for short clips and a bounded total.
    The model receives each frame's actual timestamp and sampling limitations.
    """
    videos = [m for m in media if m.kind == "video"]
    if not videos:
        yield []
        return
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="frames-", dir=MEDIA_ROOT) as folder:
        from pathlib import Path
        output = Path(folder)
        frames = []
        per_video = max(3, min(24, 48 // len(videos)))
        for video in videos:
            duration = video.duration_seconds or 0
            if duration <= 0:
                raise ValueError(f"Missing video duration for {video.filename}")
            count = max(2, min(per_video, int(duration * 8) + 1))
            # Stay before the final presentation timestamp, including low-FPS clips.
            end = max(0, duration - min(0.25, duration / 2))
            for i in range(count):
                timestamp = end * i / (count - 1)
                path = output / f"{video.id}-{i}.jpg"
                try:
                    subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1",
                        "-ss", str(timestamp), "-i", str(MEDIA_ROOT / video.storage_key),
                        "-frames:v", "1", "-vf", "scale=1024:1024:force_original_aspect_ratio=decrease",
                        "-threads", "1", "-y", str(path),
                    ], check=True, capture_output=True, timeout=20)
                except (subprocess.SubprocessError, OSError) as exc:
                    raise ValueError(f"Could not inspect video frames for {video.filename}") from exc
                if not path.exists():
                    raise ValueError(f"No frame at {timestamp:.3f}s in {video.filename}")
                frames.append(SimpleNamespace(
                    id=f"video-{video.id}-frame-{i}", kind="image", mime="image/jpeg",
                    filename=f"{video.filename} at {timestamp:.3f}s",
                    role=f"Sampled frame of source media {video.id} at {timestamp:.3f}s. {video.role}. "
                         "Sparse temporal evidence: do not infer unseen events between frames. Not a separate upload.",
                    storage_key=str(path.relative_to(MEDIA_ROOT)),
                ))
        yield frames
