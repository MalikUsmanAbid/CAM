# CAM status

Updated: 2026-11-02. CAM project source is MIT licensed. Stage: **development preview / source snapshot**.

## What this repository provides

React/TypeScript dashboard, FastAPI service, SQLite persistence, Tauri Windows shell source, project/Coding Studio workflows, scoped execution policy, protected credential references, and first-run Gemini/Claude/NVIDIA setup code. Advanced settings retain Ollama and OpenAI-compatible providers.

Mock mode returns deterministic development responses and performs no model inference. Loading a provider's model list does not verify chat or coding quality. Paid cloud usage requires separate consent, pricing and budgets.

## Recent evidence

- CAM frontend TypeScript and production build passed during the naming/publication work.
- ESLint passed before the final documentation-only updates.
- Dashboard regressions: 22/22 passed using the documented 30-second test deadline.
- The initial run with a 5-second deadline failed; it is not counted as a successful check.
- New branding-related checks and public-source validation are recorded in the publication notes as completed.

These statements cover those checks only. Earlier dated test counts belong to earlier checkpoints; they do not certify this snapshot.

## Still required for a stable CAM release

- Complete current full-suite verification and security/platform acceptance.
- Build and verify a newly branded Windows installer and portable package.
- Verify first-run setup, upgrades, process cleanup and data preservation with actual packaged binaries in disposable profiles.
- Verify live providers using the user's own credentials and explicit usage consent.
- Complete documented browser/network isolation and Windows UI automation gates before enabling their execution.
- Verify connected-account sending/publishing with exact payload approval and service-specific access.

No current CAM installer is advertised as verified. Repository presentation and public source availability do not mean the whole M0–M9 roadmap is complete.
