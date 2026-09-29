# Seedance Prompt Review Pipeline

An invitation-only web application for reviewing Seedance 2.5 prompts **before** an expensive video generation. The source repository is public; the deployed application and uploaded media are restricted to invited users.

> Status: architecture and product brief. The application is not implemented yet. BytePlus video generation is outside the first release.

![Agent architecture and workflow](agent-architecture-en.svg)

## Workflow

1. Human creators provide the brief, must-have requirements, optional image/video/audio references, target duration, resolution, and aspect ratio.
2. A deterministic validator checks media and task-specific BytePlus constraints. GPT-6 Luna structures the creator brief; GPT-6 Sol drafts a shot-by-shot storyboard. Each shot gets a visual scene card from the brief and available references; creators can request AI-generated previews for selected shots or the whole storyboard after seeing an estimated cost.
3. Creators edit and approve the storyboard, preview images, media roles, and output settings.
4. GPT-6 Sol drafts a prompt bound to that approved storyboard version.
5. Four independent specialist reviews examine action/timing, camera/visuals, audio/dialogue, and continuity.
6. Claude Opus 5.5 independently challenges the full context and specialist findings.
7. GPT-6 Sol supervises revisions. Unresolved critical conflicts are escalated to GPT-6 Astra. A deterministic Seedance linter checks structural rules.
8. Creators review the findings, remaining risks, and final prompt before approval.

The review loop has a hard round limit and a cost ceiling. Passing review reduces avoidable prompt errors; it cannot guarantee Seedance's output.

Creator instructions and uploaded references have priority over generated storyboard previews. Image generation is optional and starts only for selected shots or a creator-requested batch. Gemini 3.1 Flash Image is the initial candidate, pending access, pricing, and quality checks. The dashboard includes preview generation costs. Creators approve, replace, or reject previews before reviewers use them; previews are not automatically sent to Seedance as reference assets.

## Interface and responsive design

The application follows the current [Zoyanywhere website](https://zoyanywhere.com/) visual language: charcoal `#161616`, vivid orange `#FB4E04`, warm cream `#FFF7F2`, white, Anton display headings, and Inter body text. Design tokens stay configurable, and contrast and focus states must remain accessible. The interface should feel like a modern creator workspace: generous spacing, clear hierarchy, calm surfaces, restrained motion, and focused orange accents. Review status, critical findings, and cost remain easy to scan; advanced detail appears when needed.

Selective 3D depth is part of the visual direction: a spatial dashboard illustration, subtle perspective on storyboard shot cards, and an optional interactive view when it helps explain framing or camera motion. Forms, findings, costs, and approvals stay flat and readable. Heavy scenes load on demand, reduced-motion preferences are respected, and a static 2D fallback keeps the full workflow usable on phones and low-power devices.

The complete workflow must work on desktop and mobile. On phones, brief entry, reference uploads, storyboard shots, preview approval, agent findings, cost dashboard, and final approval use a single-column layout without horizontal page overflow. Desktop uses its wider space for shot timelines and side-by-side reference and preview comparison. Verify 360, 390, 768, and 1280 CSS-pixel widths and touch targets of at least 44 × 44 CSS pixels.

## Planned technology

- LangGraph for the review state machine, persistence, and human approval gates.
- OpenAI GPT-6 Luna, Sol, and Astra; Anthropic Claude Sonnet and Opus 5.5; Google Gemini 3.8 Flash.
- Docker deployment, invitation-only accounts, per-project access, server-side API keys, and local media storage with deletion and configurable retention. Admins create expiring invitation links and send them manually in the first release.
- A usage dashboard showing input, output, cache, and reasoning tokens where available; estimated cost per call, agent, round, and complete review; and pre-call budget checks.
- BytePlus Seedance 2.5 API integration in a later phase.

See [the machine-readable project brief](project-brief.en.json) for roles, gates, and source boundaries.

## Seedance-specific constraints

The active task type determines valid settings. Text-to-video and reference-to-video can use creator-selected ratios such as 16:9 and 9:16. Seedance 2.5 editing, extension, and first-frame workflows require `adaptive` ratio; editing requires automatic duration. The first release validates these rules and media references without starting video generation. Current [BytePlus documentation](https://docs.byteplus.com/en/docs/modelark/video-generation-tutorial) is the authority for API and model constraints. Its official Seedance 2.5 prompt guide and skill take priority over community templates.

## Secrets and public repository

Never commit `.env`, API keys, uploaded media, or user prompts. Use `.env.example` for variable names only. The application must load keys on the server and keep all review and media data behind authentication.

## Container security and upgrades

The implementation will use multi-stage Docker builds and minimal, version-pinned runtime images. Application containers run as dedicated non-root users with a read-only root filesystem where practical, explicit writable volumes, dropped Linux capabilities, `no-new-privileges`, health checks, and resource limits. The database and workers stay on an internal network; only a TLS reverse proxy is exposed. Containers will not receive the Docker socket or privileged mode.

Dependencies and base images will receive weekly Dependabot update PRs. CI will check builds, tests, dependency advisories, container vulnerabilities, and accidental secrets. Production upgrades will use reviewed immutable images with a rollback path; application containers will not silently self-update at startup. See the [project brief](project-brief.en.json) for the full security and maintenance requirements.
