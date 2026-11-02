# M3 desktop implementation — unverified

Code has been written for a Tauri 2 Windows shell, a packaged Python sidecar entry point, inherited-pipe configuration/session bootstrap, process-memory frontend credentials, and Windows Job ownership. Tests, dependency resolution, compilation, packaging and launch verification are deferred at the user's request. This is not an installer delivery or a completed M3 milestone.

## Launch contract

The native shell chooses the bundled `jarvis-backend.exe` beside its executable. It creates private pipes, launches suspended, assigns the process to a kill-on-close Job, and resumes only after assignment. Only the three declared standard handles are inherited. A bounded configure frame selects absolute data/workspace directories and a local/mock provider. The sidecar binds and retains a random loopback socket; no fixed-port reservation race is introduced.

Installed data is `%LOCALAPPDATA%/USMANJARVIS`; explicit `--portable` selects the executable's `data` directory. Existing database location restrictions still apply. Existing databases requiring a migration are refused; neither an inherited opt-in nor a pipe message grants migration consent. The entry point skips developer `.env` files. The native shell currently selects **mock**, explicitly not live inference. A local-provider configuration UI is still pending.

After backend initialization, a process-local session is sent over stdout dedicated to the private protocol. Normal stdout is redirected to stderr. The native supervisor currently discards stderr instead of retaining unredacted diagnostics. The shell allows the `desktop_session` command only for the packaged main-window URL. The frontend validates the response and stores its token only in memory. Ordinary browser development pairing is retained. HTTP pairing is denied in desktop mode. Packaged origins are admitted only when the private desktop bootstrap instance exists; peer and Host checks still apply.

Pipe EOF, invalid control input and an exact shutdown frame request graceful shutdown. Native exit attempts a five-second stop and then closes the Job to terminate remaining owned descendants. This does not undo external effects or prove OS network/filesystem isolation.

## Remaining M3 work and deferred checks

- Resolve Rust/Tauri dependencies and compile on the actual MSVC toolchain; current native code is not compiler-verified.
- Build the Python executable with `scripts/desktop_sidecar.spec`, copy the target-suffixed executable for Tauri externalBin assembly, and verify bundled dynamic imports.
- Native tray Show/Exit actions are implemented in source; actual behavior remains unverified. Add local-model configuration, startup supervision diagnostics and a practical session-expiry/re-pair workflow. Current twelve-hour expiry requires application restart.
- Assemble and validate NSIS and portable ZIP artifacts, required runtime assets and clean-machine launch.
- Test private handle inheritance, startup failure/deadline, native close/kill, no background orphan, port conflicts, unauthorized origin/window denial and existing-database refusal using temporary paths.
- Re-run M2's still-open verification gate in the combined verification stage.

MSVC/PyInstaller installation and packaging have not been performed in this implementation increment. No application was launched against real persistence.
