# Quick start

CAM is Windows-first. Current delivery is source; a newly verified CAM installer is not included. Keep the API on loopback and use disposable persistence for evaluation.

## Development setup

Install Python 3.12+ and Node.js 20+. In the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

For a new disposable preview profile, choose a new directory outside synced/network storage; do not point at personal databases:

```powershell
$camPreview = Join-Path $env:LOCALAPPDATA ('CAM-Preview-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $camPreview | Out-Null
$env:DATABASE_PATH = Join-Path $camPreview 'preview.sqlite3'
$env:SAFE_WORKSPACE = Join-Path $camPreview 'workspace'
$env:AI_PROVIDER = 'mock'
$env:BRAIN_PROVIDER = 'mock'
.\.venv\Scripts\python.exe -m uvicorn services.agent_api.app.main:app --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
cd apps\desktop
npm ci
npm run dev
```

Open http://127.0.0.1:5173 and pair with the current backend code. Mock mode works without API keys and does not run a real model. Close the backend when finished. The disposable preview directory remains for you to inspect or remove deliberately.

## Desktop model setup

The native desktop first-run screen provides Gemini, Claude and NVIDIA selection, protected key storage, model listing and **Set up later**. Existing users can keep their settings. This native flow is not presented as native functionality in a plain browser development session.

Only obtain keys from the provider's official account portal. Do not paste them into GitHub, issues or chat. Saving a provider configuration does not enable paid usage. Configure cloud consent, known pricing and limits separately; exit the desktop app from its tray and reopen to load changed provider settings.

## Existing users

Legacy internal identifiers and `%LOCALAPPDATA%/USMANJARVIS` remain for compatibility. Do not delete that directory, reset configuration or authorize a real database migration merely to try this source snapshot. Follow [foundation backup/migration instructions](M1_FOUNDATION.md) and [Windows packaging guidance](WINDOWS_RELEASE_QUICKSTART.md).
