# Skill lifecycle and reminder management — source continuation

This increment writes implementation and regression source only. Tests, lint,
type checks, native/frontend builds, application startup and AI verification were
not run. No real database/workspace, installed skill, schedule, account, OS policy
or dependency was changed. Existing repository work was preserved. No migration
or dependency version change was needed.

## Project skill lifecycle

- Installed inventory now supplies exact SHA-256 fingerprints of stored manifest
  JSON. `skill_inspect` reads a scoped manifest/version/hash through the existing
  executor, including read approvals when current policy requires them.
- `skill_uninstall` is HIGH-risk/destructive and binds project root, skill ID,
  semantic version, exact manifest hash and explicit confirmation. It rechecks
  the current row and dispatch authorization in a SQLite writer transaction.
  Replacement versions, wrong scope and revoked dispatch refuse. Successful
  removal drops loaded guidance from that runtime and invalidates the project
  runtime cache. Existing task text/history, user files, tools and account access
  remain. Uninstalling guidance does not retroactively erase model outputs or
  revoke independent tool permissions.
- Activation validation and the subsequent update now hold the same writer
  transaction. A competing update cannot swap in unvalidated guidance between
  those operations. The interface supplies expected version/hash; stale bindings
  refuse. Existing clients omitting these additive fields remain compatible but
  still receive transactionally current validation.
- New installations are bounded at 100 per project, matching the loaded/listed
  inventory limit. Semantic version strings are bounded before integer parsing.
- The interface offers inspect, version-bound enable/disable and a separate exact
  uninstall approval. Editing install JSON clears prior consent; new installs
  and upgrades remain disabled. No executable plugin loader was introduced.

Deferred regression source: scoped inspection/removal, original-file and tool
preservation, removal of loaded guidance, stale activation/removal, revoked
dispatch, a deterministic competing-writer probe *during* validation, and an
actual API approval that cannot remove a later replacement version. UI source
asserts exact version/hash arguments, explicit approval and rejection without
continuation. These are not passing results.

## Saved reminders

- Schedule inventory includes an exact current-content fingerprint. Timing edits
  change only next due time/repeat interval, retaining title, task binding and
  previous occurrence history. Datetimes require a timezone and normalize to UTC.
- `PUT /api/v1/workspace/reminders/{id}` compares the observed fingerprint while
  holding the same writer transaction used by scheduler ticks/cancellation.
  Changed, fired, cancelled and cross-scope schedules refuse. A new due time
  cannot precede/reuse the last recorded occurrence, preventing history replay.
- `POST /api/v1/workspace/reminders/{id}/cancel` performs an exact-content-bound
  cancellation and cancels pending notifications atomically. The older DELETE
  route remains compatible. Neither path executes a task or sends externally.
- The Decisions workspace now displays saved schedules, explicit local-time
  editing and cancellation controls instead of the old JSON-only schedule view.
  Old notifications are retained when changing future timing; cancellation is
  explicit. Failed/stale edits are not silently retried with a new fingerprint.

Deferred source tests use temporary SQLite only: one occurrence at the new due
time, duplicate-tick suppression, preserved bindings/history, tick-invalidated
edits/cancellation, replay and cross-scope refusal, and synchronized competing
reschedules with one winner. UI fixtures inspect exact fingerprints, timezone
conversion and Personal/project scope. No scheduler or test was executed here.

## Open release gates

Source additions do not establish product completion. Combined verification,
failure fixes, independent security review, disposable Windows browser/UIA
acceptance, installer/portable launch/restore and real configured-model workflows
remain open. Native browser accepted hashes and production UIA enablement remain
closed. No reliable whole-project completion percentage is claimed.
