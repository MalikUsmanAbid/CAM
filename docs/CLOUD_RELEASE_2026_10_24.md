# Cloud models release — 2026-10-24

## Installed-upgrade closure — 2026-10-26

The later user-authorized actual 0.1.0 to 0.1.1 upgrade and original-data launch passed on October 24. Its remaining installed-backend API/authentication checks are now complete. Data/settings are preserved, with a protected recoverable backup. [Final upgrade verification](UPGRADE_VERIFICATION_2026_10_26.md). The earlier installer-fixture refusal below records the initial build stage and is superseded for actual installed-upgrade acceptance; no clean-machine/uninstall or live-vendor acceptance is implied.

Application version: 0.1.1. This continuation adds Google Gemini compatible-chat and Anthropic native Messages adapters plus user-configured HTTPS compatible APIs. No dependency versions changed. Existing user changes and old release artifacts are preserved. No user database migration, real API key or vendor call was performed.

## Implemented scope

Named providers bind their exact official endpoints before dispatch. Gemini sends Bearer authentication and compatible chat requests; Claude sends x-api-key, a pinned API version and native system/messages/max_tokens. Responses are bounded and locally validated. Claude text-only completed responses are accepted; truncated, tool-use and malformed proposal responses fail. JSON proposals use local contract validation, not a claim of native guaranteed structured output.

Windows settings permit cloud selection with no key. Explicit key submission uses the existing protected credential vault; native configuration/pipe stores only its reference. Selecting another provider/custom endpoint clears the selected credential reference. Revocation is checked again at dispatch. All adapters retain current session/action-policy checks, bounded inference queue and cloud reservation checks. Consent, exact endpoint/model, prices and positive budgets are required; no key alone enables paid calls. Packaged missing-key chat returns an error without deterministic/mock fallback. The historical development compatible-provider fallback remains explicitly labeled.

Selection applies to chat and Coding Studio proposals. Task planning retains its separate configuration. Generic compatibility requires the vendor's chat/JSON contract, not arbitrary proprietary APIs.

## Source verification

- Final isolated backend suite: **646 passed**, 1 existing Starlette/AnyIO deprecation warning, 219.57 seconds. [Log](evidence/backend-cloud-final-2026-10-24.log).
- Final frontend suite: **105 passed across 28 files**, one thread worker, 219.95 seconds. [Log](evidence/frontend-cloud-final-2026-10-24.log).
- Backend Ruff, frontend ESLint, TypeScript and production Vite build pass.
- Actual rebuilt packaged Python backend: **4 checks pass** (private startup, auth/real registry, renewal invalidation and shutdown). [Evidence](evidence/cloud-packaged-backend-2026-10-24.json).
- Release integrity/verification guards: **19 passed**.

Earlier attempts are not reported as passes: initial Vitest workers timed out under low RAM; an obsolete App live-label assertion failed and was replaced with company-specific response assertions; the first backend full run had 642 passes and a five-second Windows child-start timeout. The targeted Windows check and final full suite pass. Cloud tests inspect outgoing fake transport headers/payloads and synchronized policy revocation, not live vendor responses. Every backend test uses temporary persistence/workspaces through tests/conftest.py; package fixtures use disposable directories and owned process jobs.

## Packaging acceptance

**PASS WITH FINDINGS for this device and keyless provider scope.** New artifacts: `%LOCALAPPDATA%/USMANJARVIS/releases/2026-10-24-cloud-0.1.1`.

| Artifact | SHA-256 |
|---|---|
| USMAN JARVIS_0.1.1_x64-setup.exe | ad247f50736516054f8cf8ce37089b20e7d7b1b3199af5a00384cb6f5786ffa4 |
| USMAN-JARVIS-portable.zip | ede92a59898f08374ed5ab95c40f4f00708bfbc717e5c58ab0e524cf3f261ffc |
| Packaged backend | 9471aa61fcf0b55cda3e3e85fa54fd951e344a8e962728fe24eda64f220d31c9 |

Native release build succeeds using existing dependency caches, 7m32s compilation, with `--locked`, one compiler worker and unchanged dependency versions. Windows executable metadata is 0.1.1. The NSIS bootstrapper contains x64 payload; x64 PE checks apply to the shell/backend, not the NSIS bootstrapper. Exact portable inventory/hashes and final release inventory pass. [Manifest](evidence/cloud-release-manifest-2026-10-24.json).

- Real portable UI: **5 checks passed**, including owned WebView/private bootstrap, actual mock request and owned-backend termination after shell crash. [Evidence](evidence/cloud-portable-desktop-2026-10-24.json).
- Real cloud UI: **3 provider scenarios passed**. Each starts the actual packaged shell/backend in separate temporary data, verifies native selected settings, commits a keyless configuration, submits an actual chat request, checks explicit unavailable error/no mock fallback, and reads the disposable DB read-only to confirm zero reservations. [Evidence](evidence/cloud-providers-keyless-2026-10-24.json).
- A pre-existing JARVIS registration prevents the installer fixture from running safely. Existing installed registration/data were preserved. Installer integrity passes, but **new installed-app upgrade/launch/uninstall acceptance was not performed**. [Preflight evidence](evidence/cloud-installer-preflight-2026-10-24.json).

The native UI harness was corrected after initial failed navigation/label assertions and an incorrect expectation that an emptied Send button becomes enabled. Final checks assert the actual HTTP response and persisted native configuration; failed harness attempts are not passes. An observed existing low-severity layout issue leaves the sidebar Settings icon outside the fixture viewport. The visible Overview → Provider settings route is tested and documented. Recommendation: separately make the dock scrollable and move the always-visible private-session notice into settings; neither workaround weakens execution controls.

## Limits and recovery

No real Gemini/Claude key, account/model access, billing or live response is verified. API key setup is a later user action; see [Roman Urdu setup](CLOUD_MODEL_SETUP_ROMAN_URDU.md). Cloud starts disabled. Windows DPAPI credentials remain machine/user bound. Packages are unsigned and require installed WebView2. Browser OS-isolation and UIA acceptance gates remain unchanged. This is not acceptance of the complete M0–M9 roadmap.

Prior 0.1.0 release packages remain available. The previous task-built sidecar was hash-verified then archived outside the repository before replacing the ignored build input. No unrelated Git reset/staging/commit/push occurred. Settings use append-only generations; explicit Reset-Settings.cmd recovers default model selection without deleting database records. Do not broad-reset application data to recover provider settings.


## Changed source and release files

Cloud adapters/contracts: `services/agent_api/app/cloud_models.py`, `providers.py`, `config.py`, `usage_policy.py`, and the desktop-only error branch in `main.py`. Private bootstrap: `desktop_bootstrap.py`, `services/agent_api/desktop_sidecar.py`, `apps/desktop/src-tauri/src/model_configuration.rs` and `main.rs`. UI: `DesktopModelPanel.tsx`, `FoundationPanel.tsx`, `api.ts`, provider status/help in `App.tsx`. Regressions: `test_cloud_models.py`, `test_desktop_bootstrap.py`, `DesktopModelPanel.test.tsx`, provider-specific `App.test.tsx` cases and `scripts/verify_cloud_desktop.py`. Application-only metadata was bumped to 0.1.1 in npm/Cargo/Tauri files, including only own-package lock entries. Setup/manual, this report and dated evidence were updated. Large earlier tracked/untracked implementation changes remain preserved; Git HEAD diff includes those pre-existing changes.
