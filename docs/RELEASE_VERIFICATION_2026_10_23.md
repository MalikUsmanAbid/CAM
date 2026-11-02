# Windows release verification - 2026-10-23

**Verdict: PASS WITH FINDINGS for current-device installer/portable launch.**
This does not declare M0-M9 or the complete product accepted.

## Delivered packages

Final output: `%LOCALAPPDATA%/USMANJARVIS/releases/2026-10-23-r2/`.
Use **r2**, not the initial package that reproduced the startup failure.

| Artifact | SHA256 |
| --- | --- |
| `USMAN JARVIS_0.1.0_x64-setup.exe` | `62712cdeff27f09fbaa2b2549a58b36849f32654126b02aa63df3a226e8af314` |
| `USMAN-JARVIS-portable.zip` | `251940d2b3a292c945f45a811d465dfb31b4cd98442ad0614a295ff02a4110ed` |

[Release manifest](evidence/release-manifest-2026-10-23.json).
[Package usage/local-model setup](WINDOWS_RELEASE_QUICKSTART.md).
Both packages are unsigned. Portable mode requires an installed Microsoft WebView2
runtime; model runtime/weights are configured separately. It stores application
and WebView data under its extracted `data` directory.

## Actual launch evidence

- [Portable executable acceptance](evidence/portable-desktop-2026-10-23.json):
  **5 checks passed**. Real owned WebView2, scoped profile, private session startup,
  authenticated dashboard/mock chat, and owned backend termination on shell crash.
  [Actual screenshot](evidence/portable-desktop-2026-10-23.png).
- [Installer acceptance](evidence/installer-desktop-2026-10-23.json):
  **7 checks passed**. Actual silent current-user NSIS install into a temporary
  directory, exact payload verification, real installed executable in its default
  data mode with temporary LOCALAPPDATA, the same UI/process checks, then actual
  uninstall. Fixture product registrations and payloads were removed.
  [Actual screenshot](evidence/installer-desktop-2026-10-23.png).
- Release/archive inventories and SHA256 checks pass after final assembly.
- Existing installation/autostart registrations are rejected before testing.
  Archive, installer and manifest handles exclude competing writers during tests.
  The final default user database path still does not exist; real user persistence
  was not opened or migrated.

The UI tests explicitly use **Mock**. Separate [October 21 live CPU coding evidence](evidence/local-coding-2026-10-21.json)
proves a real model proposal, approved apply, actual fixture test and selective
rollback. That earlier passing workflow was reused, not rerun or mislabeled as a
new desktop AI acceptance test.

## Reproduced defects and corrections

1. The initial native desktop exited **101** before private startup because its
   restricted backend environment omitted USERPROFILE. The Python home-directory
   lookup raised RuntimeError before data initialization. The supervisor now passes
   standard APPDATA/USERPROFILE directory metadata while continuing to exclude
   provider tokens, Python injection variables and migration opt-ins.
   [Original failed package/exit evidence](evidence/portable-launch-initial-failure-2026-10-23.json).
   The actual corrected portable and installed launches above are regressions that
   fail against the defective supervisor; the backend executable was not rebuilt.
2. The default WebView cache could escape portable app data. Automatic main-window
   creation is disabled; the supervisor creates it after binding its WebView profile
   to the selected app data directory. Both real launch tests require a populated
   WebView profile inside their disposable app data.
3. The installed Visual Studio CMake generator could not discover the completed
   installation. Native recipes now support Ninja with an explicit x64 MSVC
   developer environment, with generator/output-layout regression coverage.
4. The SDK has no standalone FirewallAPI import library. The network isolation
   query now resolves FirewallAPI.dll **only from System32**; missing DLL/export or
   failed query still denies deployment. This follows [Microsoft's documented API lookup](https://learn.microsoft.com/en-us/windows/win32/api/networkisolation/nf-networkisolation-networkisolationgetappcontainerconfig).
5. A strict installer verifier initially rejected the expected Tauri NSIS marker
   patch. It now computes the exact installed SHA256 from the portable bytes plus
   that single known marker change, rejecting all other differences or ambiguous
   markers. [Tauri implementation](https://github.com/tauri-apps/tauri/blob/dev/crates/tauri-bundler/src/bundle.rs).
   The actual installed hash matches that calculation; this was a verifier false
   positive, not permission to accept arbitrary changed executable bytes.

## Build/check results and preserved work

- Microsoft Build Tools 2022 **17.14.42**, MSVC **14.44.35207**, SDK **10.0.26100.0**.
  Existing managed Rust/Cargo and official Tauri CLI reused. Cargo generated its
  first lockfile; no declared dependency versions were edited.
- Frontend type checks/production build passed within the release recipe.
- Rust release compilation passed; interrupted dependency compilation was resumed
  from its original cache. The corrected supervisor rebuilt alone in **7m 23s**.
  Official NSIS/tool plugin downloads passed the bundler's hash checks.
- Native browser binaries: **all 3 actual x64 targets compile/link and pass PE/SHA
  inspection**. [Native build evidence](evidence/native-build-2026-10-23.json).
- Changed native recipe regressions: **7 passed**. Final release-verifier guards:
  **5 passed**. One existing Starlette/Anyio dependency deprecation warning.
- Ruff for changed release scripts/regression files passes. `git diff --check`
  passes, with existing line-ending warnings only.
- The prior full isolated backend suite **611 passed** and prior frontend **99
  passed** remain dated evidence for their unchanged scope, not a newly rerun full
  suite including these release harness additions. No unrelated broad checks were
  repeated. [Earlier verification details](RELEASE_VERIFICATION_2026_10_21.md).
- Final backend SHA256 remains
  `4b4880c87cda93507d6129d45d656579fa60ac6cd38c598de45fc82541803d97`.
  Its prior actual packaged authentication/renewal/shutdown proof is unchanged.
- Existing dirty/untracked project work remains in place. No reset, unrelated
  rewrite, staging, commit or push was performed. Builds/cache and packages stay
  outside the repository. This continuation changed native build/linkage, desktop
  environment/profile routing, release verifiers/regressions, lockfile and evidence.

## Remaining findings and release boundaries

- **Medium - platform acceptance incomplete:** these are current-device results
  with existing WebView2, not a clean-machine/offline installer acceptance. Tray
  Exit and all startup/recovery failure paths were not accepted by these tests;
  shell crash cleanup was tested explicitly.
- **Medium - browser/computer release gates open:** WFP/AppContainer, loopback,
  privileged deployment/recovery and UIA security acceptance remain unverified.
  The read-only native recovery inventory returned **70 (incomplete)**. A separate
  [read-only System32 API query](evidence/network-api-query-2026-10-23.json) returned
  success; that is availability evidence only. Browser accepted-build hashes stay
  empty and activation denied. No firewall/profile/loopback changes were applied.
- **Low - native compiler warning:** C4244 remains for the existing numeric SID
  wide-to-ASCII receipt conversion at `probe_host.cpp:156`; recorded in native
  evidence. Compilation does not establish browser security acceptance.
- **Release limitation:** packages are unsigned; account login, external publishing,
  paid usage, optional voice services and complete M0-M9 acceptance are separate
  gates. No user database migration, account connection or publishing occurred.

Recovery: preserve the relevant data directory before any separately authorized
migration/restore; use settings reset only for model configuration. The installer
verification uninstalled its fixture. Neither a real installation nor a running
real-data application was left behind.
