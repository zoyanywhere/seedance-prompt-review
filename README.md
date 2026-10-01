# Seedance Prompt Review Pipeline

An invitation-only web application for reviewing Seedance 2.5 prompts **before** an expensive video generation. The source repository is public; the deployed application and uploaded media are restricted to invited users.

> Status: first working application version. BytePlus video generation remains outside this release. Server/domain deployment and the creator video concept will follow later.

![Target agent architecture and workflow](agent-architecture-en.svg)

The diagram shows the implemented quality-first workflow. See [the architecture specification](ARCHITECTURE.md) for model roles, evidence handling and operational limits. Video-output quality still requires evaluation on real generated videos.

## Workflow

1. Creators provide intent, must-haves and optional image/video/audio references, plus resolution and aspect ratio. For new projects the director proposes a duration from 4–30 seconds; creators approve it with the storyboard. Existing fixed-duration projects retain their setting.
2. Technical validation checks inputs. Gemini 3.8 Flash (high) analyzes references before planning; Sol 6.1 (high) cross-checks sampled video frames. Source observations are cached by source identity/role and shared downstream.
3. Luna structures the original brief. Claude Opus 5.5 (high) develops direction, hook/payoff, purposeful creative rule breaks, shot timing and acceptance criteria. Continuous shot windows must add up to the proposed duration.
4. Creators edit and approve the storyboard and optional image previews. Sol 6.1 (high) drafts the prompt from the approved plan and source evidence.
5. Four specialists run in parallel: Sol action/timing/hook, Gemini visuals and audio, and Sonnet continuity. All use high effort and the same prompt version. Completed results are checkpointed so a failed first round can reuse them.
6. GPT-6 Astra (high) repairs supported findings. Claude Opus 5.5 (high) checks the repaired prompt and explicitly closes each major finding with evidence. A clean list alone cannot erase earlier blockers. Supported intentional creative choices remain intact.
7. Unresolved findings return to Astra within a bounded repair loop. Source conflicts, exhausted budgets and stalled unchanged prompts stop repetition. The linter checks settings and timing; creators approve the final result.
8. Handoff maps files to `@ImageN`, `@VideoN` and `@AudioN` in upload order per media type. Upload those files to the external studio and set duration, ratio and resolution separately. This release does not generate BytePlus video.

`MAX_REVIEW_ROUNDS` bounds repair/audit rounds (default 3, range 2–5). `DEFAULT_REVIEW_BUDGET_USD` and `PREPARATION_BUDGET_USD` default to $5 each. Budget estimates reserve the entire parallel batch before dispatch; actual provider usage replaces each reservation. Failed calls with unknown billing retain a conservative reservation. Critical reviews retry the same model instead of silently falling back to a weaker one. These estimates cannot enforce an exact provider invoice cap.

Creator instructions and uploaded references have priority over generated storyboard previews. Image generation is optional and starts only for selected shots or a creator-requested batch. Gemini 3.1 Flash Image is the initial candidate, pending access, pricing, and quality checks. The dashboard includes preview generation costs. Creators approve, replace, or reject previews before reviewers use them; rejection deletes the preview file while keeping its cost record. Previews are not automatically sent to Seedance as reference assets.

## Run locally

The application uses Python 3.14 in Docker (CI also checks Python 3.12), FastAPI, LangGraph, PostgreSQL in Docker, and a dependency-free browser frontend. Keep the existing `.env` with API keys private. Add a strong random `POSTGRES_PASSWORD` to it; `.env.example` lists all variables. Docker must be running.

```sh
docker compose up --build -d
docker compose run --rm app python -m app.cli create-admin --email you@example.com --name "Your Name"
```

Open `http://127.0.0.1:8080` on the server itself. The Compose port binds only to loopback. Put a TLS reverse proxy in front of it before providing remote access, and keep `SECURE_COOKIES=true` in production. Domain and proxy configuration are intentionally deferred until server details are known.

For development without Docker, create a Python environment, install `requirements-dev.txt`, set `SECURE_COOKIES=false`, create an admin with the same CLI command, and run `uvicorn app.main:app --reload`. SQLite is used when `DATABASE_URL` and `POSTGRES_PASSWORD` are absent. Video/audio validation requires `ffprobe`; it is included in the Docker image.

Run checks with `python -m pytest -q` and `node --check app/static/app.js`. The CI workflow also builds the container, audits Python dependencies, and checks for obvious accidentally tracked secrets.

## Access and data flow

- Admins create one-time invitation links in the dashboard and send them manually. Passwords use Argon2id; sessions are server-side and use secure, HTTP-only cookies with CSRF protection.
- Each project belongs to one user. API routes check ownership or administrator access before returning briefs, reviews, or media. Shared-project roles are not implemented yet.
- Image, video, and audio uploads are validated and stored on the server. Gemini receives uploaded media and timestamped video frames. Other agents receive images, sampled frames and shared source observations. Frames are private temporary derivatives, not additional Seedance references. Generated storyboard previews are stored separately from source references and enter review only after creator approval.
- The review runs in the application process with one parallel specialist pass and up to three repair/audit rounds by default. A restart marks interrupted reviews as failed so they can be rerun. A durable worker queue and a full migration framework are planned before multi-instance production deployment. Startup currently performs two additive column upgrades, preserving existing fixed durations.
- The usage ledger records provider-reported tokens and a versioned rate estimate. The pre-call budget uses a conservative heuristic; actual invoices can differ for multimodal inputs, retries, and provider price changes. Preview images use an initial 1K-image estimate of about $0.067 each.

No model call starts just by opening a project. Creators explicitly trigger storyboard drafting, optional preview images, and prompt review.

## Interface and responsive design

The application follows the current [Zoyanywhere website](https://zoyanywhere.com/) visual language: charcoal `#161616`, vivid orange `#FB4E04`, warm cream `#FFF7F2`, white, Anton display headings, and Inter body text. Design tokens stay configurable, and contrast and focus states must remain accessible. The interface should feel like a modern creator workspace: generous spacing, clear hierarchy, calm surfaces, restrained motion, and focused orange accents. Review status, critical findings, and cost remain easy to scan; advanced detail appears when needed.

Selective 3D depth is part of the visual direction: a spatial dashboard illustration, subtle perspective on storyboard shot cards, and an optional interactive view when it helps explain framing or camera motion. Forms, findings, costs, and approvals stay flat and readable. Heavy scenes load on demand, reduced-motion preferences are respected, and a static 2D fallback keeps the full workflow usable on phones and low-power devices.

The complete workflow must work on desktop and mobile. On phones, brief entry, reference uploads, storyboard shots, preview approval, agent findings, cost dashboard, and final approval use a single-column layout without horizontal page overflow. Desktop uses its wider space for shot timelines and side-by-side reference and preview comparison. Verify 360, 390, 768, and 1280 CSS-pixel widths and touch targets of at least 44 × 44 CSS pixels.

## Implementation and later phases

- LangGraph for the bounded review graph; the application database stores storyboard versions, reviews, usage, and human approval decisions.
- OpenAI GPT-6 Luna, GPT-6.1 Sol and GPT-6 Astra; Anthropic Claude Opus 5.5 and Sonnet 5.5; Google Gemini 3.8 Flash. Model and effort settings are recorded for each call.
- Docker deployment, invitation-only accounts, project ownership checks, server-side API keys, and private local media storage. Admins create expiring invitation links and send them manually.
- A usage dashboard showing input, output, cache, and reasoning tokens where available; estimated cost per call, agent, round, and complete review; and pre-call budget checks.
- BytePlus Seedance 2.5 API integration in a later phase.

Configurable media retention, account/project sharing, durable background workers, and database migrations are follow-up implementation work before production deployment. The current server/domain deployment remains undecided.

Before accepting public-server uploads, add an isolated antivirus scanner (for example ClamAV) that scans every file before storage and fails closed when unavailable. Current limits, content inspection, private storage, and container restrictions remain in place. Antivirus signatures cannot detect prompt injection in a brief or media; agents must treat embedded text and speech in references as untrusted source content, and media decoders still need isolation and updates.

See [the machine-readable project brief](project-brief.en.json) for roles, gates, and source boundaries.

## Seedance-specific constraints

The internal media database ID is not a Seedance reference token. The final handoff displays `@ImageN`, `@VideoN`, and `@AudioN` aliases and a matching file list. These numbers count independently by media type in upload order: five images and one video yield `@Image1`–`@Image5` and `@Video1`. Text alone cannot attach a file; the creator must upload the listed source files in that order to the external studio. Duration, ratio, and resolution are separate video-generation settings, while shot timing and visual instructions remain in the prompt.

Creators can edit an attached reference's role after upload. Mark a video as authoritative for camera path, blocking, action, or timing when those properties must be preserved; state explicitly when a subject transforms and the original must disappear. Changing a role clears storyboard and prompt approval so the agents review the revised source instruction. A reference-to-video prompt remains a generative request, not a guarantee of exact motion or frame reproduction; source duration and storyboard shot duration should agree before approval.

If a media specialist finds that an uploaded reference lacks an event required by the approved storyboard, the finding is marked as a source conflict. A prompt-only final audit cannot clear it. The creator must replace the source or revise and reapprove the storyboard before another paid review. The external handoff replaces internal media IDs with the corresponding `@ImageN`, `@VideoN`, or `@AudioN` alias.

The active task type determines valid settings. Text-to-video and reference-to-video can use creator-selected ratios such as 16:9 and 9:16. Seedance 2.5 editing, extension, and first-frame workflows require `adaptive` ratio; editing requires automatic duration. The first release validates these rules and media references without starting video generation. Current [BytePlus documentation](https://docs.byteplus.com/en/docs/modelark/video-generation-tutorial) is the authority for API and model constraints. Its official Seedance 2.5 prompt guide and skill take priority over community templates.

## Secrets and public repository

Never commit `.env`, API keys, uploaded media, or user prompts. Use `.env.example` for variable names only. The application must load keys on the server and keep all review and media data behind authentication.

## Container security and upgrades

Production deployment for **promptlab.zoyanywhere.com** uses the existing Traefik `web-network`, commit-tagged GHCR images, and a separate ClamAV container. Uploads must pass scanning before media decoding or storage. See [deployment and first-admin instructions](docs/DEPLOYMENT.md) for server environment variables, SSH secrets, backups, and rollout behavior.

The app image uses a multi-stage build and version-pinned Python base image. The app runs as a dedicated non-root user with a read-only root filesystem, an explicit media volume, dropped Linux capabilities, `no-new-privileges`, a health check, and resource limits. PostgreSQL is reachable only on an internal network. The app listens on the host loopback interface, ready for a TLS reverse proxy at deployment. Containers do not receive the Docker socket or privileged mode.

Dependencies and base images receive weekly Dependabot update PRs. CI checks builds, tests, dependency advisories, container vulnerabilities, and obvious accidental secrets. Production upgrades should use reviewed immutable images with a rollback path; application containers do not silently self-update at startup. See the [project brief](project-brief.en.json) for the full security and maintenance requirements.

Dependabot PRs can auto-merge after both required CI jobs (`test` and `container`) pass for the current PR head. Branch protection must require those checks; the auto-merge workflow does not check out or execute PR code with write permissions.
