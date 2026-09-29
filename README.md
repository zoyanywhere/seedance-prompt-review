# Seedance Prompt Review Pipeline

An invitation-only web application for reviewing Seedance 2.5 prompts **before** an expensive video generation. The source repository is public; the deployed application and uploaded media are restricted to invited users.

> Status: architecture and product brief. The application is not implemented yet. BytePlus video generation is outside the first release.

![Agent architecture and workflow](agent-architecture-en.svg)

## Workflow

1. Human creators provide the brief, must-have requirements, optional image/video/audio references, target duration, resolution, and aspect ratio.
2. A deterministic validator checks media and task-specific BytePlus constraints. GPT-6 Luna structures the creator brief; GPT-6 Sol drafts a shot-by-shot storyboard with visual previews.
3. Creators edit and approve the storyboard, media roles, and output settings.
4. GPT-6 Sol drafts a prompt bound to that approved storyboard version.
5. Four independent specialist reviews examine action/timing, camera/visuals, audio/dialogue, and continuity.
6. Claude Opus 5.5 independently challenges the full context and specialist findings.
7. GPT-6 Sol supervises revisions. Unresolved critical conflicts are escalated to GPT-6 Astra. A deterministic Seedance linter checks structural rules.
8. Creators review the findings, remaining risks, and final prompt before approval.

The review loop has a hard round limit and a cost ceiling. Passing review reduces avoidable prompt errors; it cannot guarantee Seedance's output.

## Planned technology

- LangGraph for the review state machine, persistence, and human approval gates.
- OpenAI GPT-6 Luna, Sol, and Astra; Anthropic Claude Sonnet and Opus 5.5; Google Gemini 3.8 Flash.
- Docker deployment, invitation-only accounts, per-project access, server-side API keys, and local media storage with deletion and configurable retention.
- A usage dashboard showing input, output, cache, and reasoning tokens where available; estimated cost per call, agent, round, and complete review; and pre-call budget checks.
- BytePlus Seedance 2.5 API integration in a later phase.

See [the machine-readable project brief](project-brief.en.json) for roles, gates, and source boundaries.

## Seedance-specific constraints

The active task type determines valid settings. Text-to-video and reference-to-video can use creator-selected ratios such as 16:9 and 9:16. Seedance 2.5 editing, extension, and first-frame workflows require `adaptive` ratio; editing requires automatic duration. The first release validates these rules and media references without starting video generation. Current [BytePlus documentation](https://docs.byteplus.com/en/docs/modelark/video-generation-tutorial) is the authority for API and model constraints. Its official Seedance 2.5 prompt guide and skill take priority over community templates.

## Secrets and public repository

Never commit `.env`, API keys, uploaded media, or user prompts. Use `.env.example` for variable names only. The application must load keys on the server and keep all review and media data behind authentication.
