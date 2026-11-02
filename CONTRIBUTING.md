# Contributing to CAM

Thanks for helping improve CAM. This is a Windows-first development preview, not a completed stable release. Read [current status](docs/STATUS.md) and [security guidance](SECURITY.md) before proposing changes.

## Start with a focused change

1. Search existing issues; describe the problem and expected behavior before a substantial feature.
2. Use a branch and a pull request. Preserve existing user changes and compatibility with stored data.
3. Follow `AGENTS.md` for repository layout and development commands.
4. Add a meaningful regression for behavior or security changes. Use temporary databases and fixture workspaces only.
5. Run the relevant backend/frontend checks and include exact results. Mark unverified Windows or provider behavior explicitly.

## Local checks

```powershell
py -m pytest -q
py -m ruff check services tests
cd apps\desktop
npm ci
npm test -- --pool=forks --maxWorkers=1 --testTimeout=30000
npm run lint
npm run build
```

Run heavy checks sequentially on low-memory devices. Do not connect tests to personal persistence or paid providers.

## Review requirements

- Keep execution behind current policy, scoped approvals and audit. Model output is untrusted.
- Keep API access on loopback and preserve authentication, Host/Origin validation, path boundaries and cancellation checks.
- Explain migration, rollback and data-preservation implications.
- Never include API keys, raw diagnostic exports, personal databases, credentials, build caches or unrelated changes.
- Do not label mocks, model listings or unverified integrations as live execution evidence.
- Do not install or update dependencies without explaining the need and updating relevant lockfiles.

## Contributions and rights

CAM is licensed under the [MIT License](LICENSE). Contributions submitted for inclusion are expected to use the same license; retain applicable copyright and third-party notices. Contributions do not transfer copyright ownership.

## Conduct

Be respectful and specific. Discuss the code and its behavior, avoid personal attacks, protect other people's privacy, and give actionable reproduction steps. The maintainer may close abusive or unrelated discussions.
