# Windows deployment continuation — source, not verified

The subsequent [managed deployment continuation](MANAGED_BROWSER_DEPLOYMENT_2026_10_18.md)
adds the privileged bridge, exact-SID loopback and prior-boot automatic recovery
source that was pending in this historical increment. No native acceptance has
run and activation remains denied.

No build, test, application start, package install, migration or live AI run was
performed in this continuation. Existing user changes were preserved.

## Added source

- `python -m scripts.build_browser_native --output-directory <absent-directory>`:
  later explicit MSVC x64/CMake recipe for the native broker, network probe and
  recovery inventory. Uses a fresh temporary build tree, bounded subprocesses,
  filtered build environment, PE/hash inspection and exact asset manifest.
  Manifest explicitly records activation denied and acceptance not performed.
  Does not download Chromium, change firewall policy, install or launch tools
  other than the explicitly requested build toolchain. Do not run during the
  deferred-verification stage.
- `jarvis-browser-recovery-inventory.exe --inventory`: later explicit read-only
  WFP candidate inventory. Bounded pages, GUIDs, package SIDs and deny/persistence
  flags; no arbitrary display text echoed, no deletion, no global reset. Names
  are candidate labels, not proof of ownership or authority to remove rules.
  Output is visible objects only; access failure or traversal bound is an error.
- Desktop assembly now uses a fresh Cargo target directory and publishes its
  selected output directory only after exact package integrity inspection.
  Stale installers in the repository target tree cannot be selected. Failed
  assembly does not publish a partial release directory. Existing generated
  sidecar files still cause explicit refusal rather than replacement.
- `python -m scripts.release_readiness`: later read-only prerequisite inventory.
  No executable invocation or persistence access. Missing PATH entries do not
  prove a toolchain is absent; versions and developer-prompt setup remain to be
  verified. This inventory is not acceptance evidence.

New regression source uses inert PE-shaped fixtures and injected build runners.
It checks fresh installer selection, exact denied native manifests, inherited
persistence exclusion and refusal to replace an existing destination. Not run.

## Remaining release boundaries

Installer and portable assembly recipes exist; packages have not been built or
accepted. Browser production deployment is not complete: a least-privilege
privileged controller and safe per-profile loopback provisioning are still
required. Crash deny policy stays installed. The inventory supports diagnosis;
automatic stale-filter deletion is deliberately not implemented because an
empty process snapshot cannot prevent another process starting during removal.
A recovery design must coordinate launch exclusion and authenticate ownership
before mutation. No broad firewall reset or loopback-exemption replacement is
an acceptable substitute. Accepted browser hashes remain empty.

WFP enumeration follows the official Win32 contracts:
[filter enumeration](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmfilterenum0),
[engine sessions](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmengineopen0).
Compile/runtime validation is deferred; mocks will not establish OS isolation.
