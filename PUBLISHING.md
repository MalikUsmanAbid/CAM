# CAM source publication

Target owner: MalikUsmanAbid. Suggested repository: CAM (private).

This folder is an isolated source snapshot, not the original working repository. Original Git history and personal persistence are excluded. Product display branding is CAM; legacy package identifiers and data paths remain for compatibility.

Do not commit .env, API keys, credentials, databases, personal workspaces or generated binaries. Historical documentation describes earlier versions and is not proof of current verification. CAM project code is licensed under the MIT License; third-party components retain their respective licenses.

Backend checks: python -m pytest -q; python -m ruff check services tests.
Frontend checks from apps/desktop: npm ci; npm test -- --maxWorkers=1; npm run lint; npm run build.

Windows packaging instructions: docs/WINDOWS_RELEASE_QUICKSTART.md.
