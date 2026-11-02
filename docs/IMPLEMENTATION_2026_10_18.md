# October 18 implementation — managed draft file workspaces

Source implementation only. Tests, lint, type checks, builds, live AI and Windows
acceptance remain deferred at the user's explicit request. No application was
started, user database migrated, account connected, dependency installed or Git
checkpoint created during this increment. Existing dirty/untracked work remains.

## Added workflow

Virtual snapshot → prepare exact copy contract → approve creation of an absent
project subdirectory → inspect retained state → prepare structured command →
approve exact execution → inspect durable results → collect selected current text
as ordinary Coding Studio proposals → separately approve source application.

- `draft_materializations.py` implements project/root/revision/hash-bound copies.
  Up to four explicitly selected snapshot files are copied, retaining their
  relative paths, into a new `jarvis-draft-<opaque-id>` directory **inside the
  selected project**. Preparing a contract writes internal metadata only.
  No full repository, Git metadata, dependencies or implicit environment setup.
- Windows directory handles pin newly owned directory routes; exclusive file
  creation refuses overwrites. A transactional claim prevents duplicate creation.
  Partial/interrupted directories remain for explicit inspection. No automatic
  delete, overwrite, cleanup or uncertain replay. A process crash can leave the
  durable status `materializing`; this also refuses retry and needs maintenance.
- Directory device/inode, current virtual revision and snapshot hashes are checked
  before command preparation/execution. Changed selected inputs require a fresh
  copy. Commands retain existing executable hashes, policy/session/cancellation
  guards, exact approvals, bounded output and owned process supervision.
- Managed binding is part of the durable command contract. Both the managed tool
  and ordinary `studio_run_command` enforce it, so another tool entry point cannot
  bypass a closed/stale draft or replaced directory check.
- Commands are **not sandboxed**. Explicitly approved executable code can access
  user files and network. Selected-file fixture results are not proof that a full
  repository's tests ran. External concurrent writes are not generally prevented.
- Output collection accepts only originally selected bounded UTF-8 text, rejects
  links/credential-shaped content and prepares normal hash-bound diffs against
  unchanged original source. It never applies source edits. Preparation may leave
  earlier unapplied proposals on a later-file conflict. Current collected bytes
  are not automatically attributed to a prior command result.
- `DraftCommandPanel.tsx` adds contract inspection, explicit copy/command approval
  requests and output collection to the existing draft panel. Session/generation
  checks reject stale async responses. Inspect again after generic approval
  completion to retrieve durable state and results.
  The snapshot selector now accepts up to four explicitly inspected paths/hashes,
  allowing source and fixture-test files in one copy contract.
- Immutable additive migration **12** stores copy contracts/status/identity.
  Earlier migration sources/checksums remain unchanged. No real migration ran.

## Regression source added, not executed

`test_draft_materializations.py`: database-only preparation; project/hash mismatch;
stale draft revision; changed copied input; replaced directory identity; selected
output proposals and unrelated edits; exact command arguments/destination;
foreign-command rejection; cooperative cancellation/context restoration;
alternate ordinary-command entry-point rejection; native Windows exclusive copy
and interrupted-copy retention; synchronized competing copy creation; exact
executor approval destination/manifest. All paths/databases are `tmp_path` fixtures.

`DraftCommandPanel.test.tsx`: preparation does not automatically materialize or
execute; exact inspected copy binding and separate structured-command approval.
`AgentDraftPanel.test.tsx` adds multiple selected source/hash request coverage.

## Still open

This bounded copy workflow does not complete general multi-agent Git worktrees,
dependency provisioning, cleanup/reconciliation UI or complete M4 acceptance.
Installer/portable assembly, OS browser network isolation, remaining social-media
publication adapters, credentialed live integrations and release verification
also remain open. Browser/production computer activation stays gated. No new
passing-test counts or full-project completion are claimed.
