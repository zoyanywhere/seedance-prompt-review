# Production profiles and Master library

Promptlab offers Film & Scene and Short & Ad in one application. Both use the same review graph with project-scoped creative choices. Film emphasizes dramaturgy, performance and continuity; Short & Ad emphasizes legibility, hook and payoff. Neither profile forces music, morphing, realism, aspect ratio or frame rate.

## Creator choices

At project creation, choose the profile, purpose/audience, intended feeling, audio intention, transformation policy, realism, visual style and camera movement. Camera body is optional aesthetic context. Auto lets the director select entries per shot. Each shot must carry valid style/camera IDs, a production recommendation with a reason and nonempty visible/audible acceptance criteria; explicit creator selections must be preserved. Explicit reference roles and creator intent outrank recipes. Conflicting creator choices must be reported rather than silently overridden.

## Library access

Six original Master JSON documents are shipped in `app/knowledge`. `app/production.py` adapts them into role-specific context. Camera specialists receive selected movement/style/body entries; audio specialists receive relevant sound profiles; the director receives a compact choice catalog. The supervisor and final auditor receive the relevant combined context. Source observation and frame cross-check remain independent of stylistic presets.

Visual style recipes have camera movement, cut, frame-rate and identity/anatomy example clauses removed. Music-containing sound examples are omitted for SFX/dialogue-only and silent projects. Camera-body specifications do not become API settings. Template rules such as blanket no-morphing, mandatory 3D, fixed 24 fps, absolute coordinate locks, 20/80 attention claims and guaranteed zero drift are not adopted as universal requirements. Raw source claims are not independently verified hardware or model capability guarantees.

A content hash of the six files and the adaptation code identifies the library version. Successful reviews store a production snapshot including the chosen configuration and applicable entries. The checkpoint signature includes production choices and version; specialist results from a different context cannot be reused. Catalog access requires login; project data remains owner-scoped.

## Five-block output contract

Each final generation prompt contains these exact headings, once, in order:

1. `[BLOCK 1: SCENE CONTEXT & CINEMATOGRAPHY]`
2. `[BLOCK 2: ACTIVE REFERENCES & BINDING]`
3. `[BLOCK 3: ACTION BEATS & TIMING]`
4. `[BLOCK 4: LOCKS & CONSTRAINTS]`
5. `[BLOCK 5: ANIMATION & PHYSICS]`

All approved shot beats belong in block 3. Resolution and aspect ratio remain external generation settings. Source aliases are indexed by media ID within each kind. Every uploaded reference must be bound or explicitly identified as unused. The linter rejects missing/empty/repeated/out-of-order blocks, bracket placeholders and invalid/missing aliases. It also checks explicit no-music/silent constraints and selected score/morphing contradictions. These are structural and limited textual checks, not a proof of semantic correctness; specialist review and independent final audit assess the full meaning.

Linter failures are supplied to the repair supervisor before final verification. A clean model verdict cannot bypass deterministic failures. Existing budget and round/stall limits apply.

## Existing projects and changes

Startup adds a nullable JSON production_settings column without rewriting existing projects. Existing API clients and projects remain in the legacy workflow until a profile is chosen; a PUT that omits production_settings preserves it. Changing settings increments the storyboard version, clears approvals and the old final prompt, and requires a new storyboard for a profiled project. Old preview approvals and review results no longer match the new version. A library version change requires a new storyboard before a profiled project can be approved/reviewed. Final approval rechecks the current contract.

## Scope and verification

Production recommendations such as previs, a hero-shot test, separate sound stems or compositing remain planning advice. Promptlab does not execute Blender, video rendering, lip-sync or post-production. Asset-sheet generation and separate immutable reference-asset approvals are not implemented by this change.

Automated tests cover context isolation, audio/style conflict handling, block and alias gates, repair routing, API validation, profile-change invalidation and additive migration. The local verification suite passes 36 tests inside the non-root Docker runtime with 512 MB and one CPU. The browser check covers project creation, profile changes and a mobile layout. Tests use simulated providers; no claim of proven first-pass video quality follows from them. Live creative quality must be evaluated against generated videos.
