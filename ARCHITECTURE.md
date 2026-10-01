# Quality-first video review architecture

Status: implemented; automated workflow validation complete, real-video quality evaluation pending. Updated 2026-10-01.

## Purpose and creative authority

Produce a realistic, artistically intentional Seedance video from the creator brief and source media. Evaluate the total cost of an accepted video, including failed generations, rather than minimizing individual agent calls. Strong planning and repair should improve first-pass success; this remains a hypothesis to validate on generated videos.

The immediate use case is a video project evaluated by professors, not a master thesis. Scientific process documentation is not a deliverable. The application remains generic; no scene-specific car, animal or track rule belongs in its code.

Film principles guide choices. Deliberate creative departures from continuity, realism, perspective or conventional rhythm are legitimate when they serve creator intent. Agents distinguish accidental inconsistency from intentional expression and must preserve originality. They explain ambiguous tradeoffs and obtain creator approval for material changes to intent or an approved storyboard.

## Flow and models

1. **GPT-6 Luna:** structure the creator brief without losing or altering must-haves.
2. **Gemini 3.8 Flash, high:** analyze actual image/video/audio sources before the storyboard. Record observations, timestamps, source roles and uncertainty. **GPT-6.1 Sol, high** cross-checks critical sequences using selected dense frames. Shared evidence is versioned and supplied to all downstream agents.
3. **Claude Opus 5.5, high:** develop direction, dramaturgy, composition, hook, sound intention, motivated rule breaks and the storyboard. Propose the appropriate total duration between **4 and 30 seconds** from action, perception, reading time, rhythm and payoff. Creator approves the plan and duration. Reference cards and optional approved AI previews support planning.
4. **GPT-6.1 Sol, high:** translate the approved plan into a concise, actionable Seedance prompt with explicit media roles and aliases.
5. **Parallel specialists:** Sol 6.1 high checks action, timing and hook; Gemini 3.8 Flash high checks camera/visuals and audio/dialogue; Sonnet 5.5 high checks identity, continuity and transformations. All inspect the same prompt/storyboard/evidence version.
6. **GPT-6 Astra, high:** supervise and repair supported findings, reconcile disagreements against source evidence and preserve creator intent. Track each finding, its repair and verified closure. Recheck affected constraints and dependencies after changes.
7. **Deterministic linter plus Claude Opus 5.5, high:** check the actual repaired prompt against original requirements and source evidence. This replaces the proposed routine Sol challenge/final-audit roles; Opus final review occurs after repair. Remaining blockers return to Astra within bounded rounds. Identical stalled rounds must not repeat indefinitely.
8. **Creator decision and handoff:** approve the final prompt and visible residual limitations. Export media aliases and upload order alongside separate duration, aspect-ratio and resolution settings. External studio upload remains necessary; BytePlus generation is a later phase.

## Quality criteria

- Timing is part of realism: plausible acceleration, reactions, body motion, contact, transformations, perception and text-reading time. Shot durations must sum to the approved 4-30 second total for the targeted text/reference workflow. Other task types keep their provider-specific constraints.
- Assess cinematic coherence: composition, camera, light, material, anatomy, montage and sound. Apply physical realism where intended; preserve deliberate surrealism or stylization.
- Assess hook, development and payoff. For ads, also assess product/message clarity. Attention and shareability are useful criteria, not a guarantee of virality or a mandate to use generic clickbait.
- Define visible acceptance criteria per shot, including initial state, action, final state and source fidelity. A clean review means no identified blockers; it does not guarantee generated video quality.
- Generated previews are interpretations, subordinate to original references and creator intent.

## Implementation and operational limits

The runtime uses Opus for direction and final audit, Astra for repairs, explicit effort settings, pre-storyboard source analysis, sampled video-frame cross-checks, director-proposed duration, and parallel specialist calls. All database writes remain on the coordinating thread. Whole-batch reservations prevent parallel calls bypassing the budget gate. First-pass successful specialist results can be reused after a failed review if the input and model configuration signature still matches.

Source observations survive a failed preparation and can be reused while media/roles are unchanged. Derived frames are sampled at up to approximately 8 per second for short clips, capped at 24 per video and 48 total, resized and deleted after each workflow. Sampling can miss events; reviewers receive explicit uncertainty instructions. The final fraction of a clip is excluded from sampling to avoid seeking beyond its last decodable frame. Native video is also sent to Gemini.

New projects default to agent-proposed duration. Existing projects keep their previous fixed duration until the creator switches duration planning. Stored source evidence and director metadata survive shot edits. Legacy storyboards with uploaded media must be regenerated before the new review, so sources are analyzed before planning. Startup adds duration mode and usage effort columns without replacing existing data. Durable queues, comprehensive migrations and production hardening remain separate deployment work.

## Production entry and upload gate

`promptlab.zoyanywhere.com` uses the existing Traefik HTTPS router and external `web-network`. Invitation-only access and project ownership checks precede upload handling. File type, size, pixel count and duration are checked before private storage. Antivirus scanning was removed at the creator's request for the 1-CPU, 1-GB host. Application and PostgreSQL run without root; PostgreSQL exposes no host ports. CI gates commit-tagged image publication and the health-checked SSH deployment. See [deployment details](docs/DEPLOYMENT.md).

## Validation and cost

Keep a ledger covering media analysis, frame checks, all reviews, reasoning tokens where reported, retries and optional image previews. Preserve budget controls without silently substituting a weaker model during a quality-critical decision. Record the actual model and effort used.

Compare first-generation requirement coverage, artistic/film quality assessed by creator or professors, regeneration count and total cost per accepted video. Model benchmarks inform selection but do not prove Seedance-specific success. Evaluate generated video outputs before claiming an improvement. No guarantee of first-pass success or viral reach is made.
