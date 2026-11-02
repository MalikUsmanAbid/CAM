# Windows package quick start

Use only the installer/ZIP listed with SHA256 in the dated release manifest.
A successful build alone is not clean-machine or browser-isolation acceptance.

## Installer and portable modes

The per-user installer does not need a developer terminal. Installed persistence
belongs under `%LOCALAPPDATA%\USMANJARVIS`. Existing databases needing migration
are not automatically migrated; authorize a consistent backup and migration first.

Extract the portable ZIP into a writable local directory, then run
`Start-Portable.cmd`. Portable persistence belongs in that extracted directory's
`data` folder. Do not run directly from the ZIP. Do not keep an active database
in OneDrive, another synchronized directory, or a network share. The current
package requires Microsoft WebView2 to be installed. Model weights are separate.

Closing the main window hides it to the tray. Choose **Exit JARVIS** from the tray
to stop the owned backend. `Reset-Settings.cmd` affects future model settings;
it retains previous settings generations and database records.

## The installed CPU model

The separately installed model is `usman-jarvis-coder-cpu:latest`.
Start the existing local Ollama server, then use **Settings > Desktop local model**:

- Provider: **Local Ollama**
- Model: `usman-jarvis-coder-cpu:latest`
- Endpoint: `http://127.0.0.1:11434/v1`

Save, choose **Exit JARVIS**, and relaunch. The default development provider is
clearly labeled **Mock**; it is not AI proof. Changing settings does not install
or start Ollama. There is no automatic paid/cloud fallback.

The October 21 live CPU evidence verifies one arithmetic coding fixture with
approved apply, actual tests and selective rollback. It is not a general coding
quality or performance guarantee. Browser and production computer control remain
disabled until their separate Windows security gates pass. Account connections,
publishing and paid usage require their relevant explicit approval.

## Startup and data recovery

If startup fails, close another JARVIS instance; check the bundled backend and
WebView2. Use settings reset only for configuration problems. Do not delete a
database or run broad Git resets to recover. Preserve the data directory before
an explicitly authorized migration/restore. Unsigned packages can trigger Windows
reputation prompts; verify the published hash before deciding whether to run.
