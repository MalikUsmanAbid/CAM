# Windows release verification — 2026-10-21

Status: in progress; no installer or portable acceptance is claimed yet.
This continues the October 20 baseline of 608 backend and 99 frontend tests,
without rerunning unaffected dashboard work.

## Actual live local coding acceptance

Evidence: [local-coding-2026-10-21.json](evidence/local-coding-2026-10-21.json).
Verified managed Ollama 0.40.3 and Qwen2.5-Coder 1.5B, CPU only, one inference,
2,048 context. Proposal latency: **29.94 seconds** on this laptop.

The real command reproduces the original arithmetic defect (exit 1), the model
proposes a reviewable correction without applying it, explicit exact approval
applies that correction, and the real command passes (exit 0). Approved selective
rollback restores original bytes and the original failing test (exit 1).
An unrelated fixture edit remains unchanged. No mock/echo replaces inference.
Application database/projects are temporary and removed after verification.
This demonstrates one bounded arithmetic workflow, not general model quality.

Initial real attempts exposed Markdown fences around otherwise valid model JSON.
Coding Studio now explicitly requests the provider's JSON-object response mode.
Strict schema validation, policy checks, approval and source-hash checks remain.
Ordinary chat keeps its text response contract. Regression assertions inspect
actual transport arguments for both behaviors. Diagnostic raw model text is
captured only inside this fixed disposable fixture, never production logging.

The cached, verified weights/template/license were also imported through the
existing local Ollama API as `usman-jarvis-coder-cpu`, with CPU/context limits.
No second Internet model download or existing model replacement was needed.
Existing desktop settings and real databases were not changed.

## Tools and packaging

- PyInstaller **6.22.3** installed in the existing development virtual environment.
- Official Tauri CLI **2.12.4** installed in managed local application tools,
  without changing frontend dependency versions.
- A complete existing managed Rust/Cargo **1.99.0** installation was discovered
  outside PATH and verified. A redundant default Rust download was stopped after
  this discovery; build processes use explicit managed toolchain paths.
- Microsoft Visual Studio 2022 Build Tools installation was started using the
  official winget package with C++ tools/SDK workload. An observed Windows UAC
  prompt caused an asynchronous user action request. Subsequent setup logs show
  successful component installations; the complete MSVC/SDK instance is not yet
  registered and must not be treated as ready.
- A combined CMake/PyInstaller download was stopped to prioritize the already
  cached Python packaging prerequisites. CMake is not installed by that attempt;
  native C++ tooling still depends on successful Microsoft setup.
- Rust dependencies are being resolved/fetched; first-use Cargo lock generation
  belongs to this release build, not a successful compiler/acceptance claim.
- Python sidecar compilation completed in a dedicated managed build directory.

The build recipe accepts an explicitly installed official Tauri CLI as an
alternative to compiling cargo-tauri, and limits Cargo builds to one job. Its
regressions exercise both CLI routes, explicit existing-sidecar reuse with byte
hash verification, stale installer exclusion, fresh target directories and
persistence environment exclusion: **6 passed**.

The new `scripts/verify_packaged_backend.py` runs the actual sidecar executable
under an owned Windows job, with temporary configuration and data. It checks
authentication, the real tool registry, private session renewal/old-token denial,
clean shutdown and process cleanup. It never records the private session token.
The final actual executable passed all four checks. Evidence:
[packaged-backend-final-2026-10-21.json](evidence/packaged-backend-final-2026-10-21.json).
SHA-256: `4b4880c87cda93507d6129d45d656579fa60ac6cd38c598de45fc82541803d97`.
The initial executable was rebuilt after the isolated-agent JSON contract was
corrected too; the final evidence pins the rebuilt executable, not the old one.

## Current check evidence

- Focused model/context/dispatch/fixture-safety suite: **53 passed**, one existing
  Starlette/Anyio dependency deprecation warning.
- Provider/Ollama/recipe/fixture-safety suite: **52 passed**, same warning.
- Draft/agent/context/studio/recipe suite: **41 passed**, same warning.
- Final full backend suite: **611 passed**, one dependency deprecation warning,
  **284.62 seconds**. This includes the final source and expanded recipe routes.
- Ruff for services, tests and changed release scripts: passed.
- A broader optional check of all historical scripts also reports four existing
  formatting/import findings in `deploy_browser_broker.py` and `manage_backup.py`;
  those unrelated sources were not rewritten during installation.

## Remaining release gates

Rust/native compilation, installer/portable
integrity and disposable Windows launch remain pending while tools finish.
Native AppContainer/WFP deployment, loopback/recovery matrix and owned UIA
acceptance are not established by the live coding fixture. Browser accepted-build
allowlist and production computer execution remain disabled. No privileged policy
deployment, real user database migration, account connection or publishing ran.
