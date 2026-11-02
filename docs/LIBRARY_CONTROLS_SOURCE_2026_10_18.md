# Knowledge controls and artifact history — source continuation

No test, lint, type check, build, live AI run, application start, user migration,
deployment or package installation was performed. Existing work was preserved.

## Implemented

- `knowledge_forget`: HIGH-risk destructive tool through the existing project
  executor and exact approvals. Document ID, source SHA and canonical project
  root bind the action. A transaction rechecks snapshot identity and current
  dispatch authorization before removal. Cross-project/root, changed hash,
  revoked dispatch and repeated removal refuse. No project file is deleted.
- Snapshot removal excludes the document from active knowledge lookup/search.
  Immutable artifact text and recorded source-attribution metadata remain, as
  do original files, audit records and backups. SQLite free pages and outside
  copies are not securely erased. No vacuum, backup deletion or cascade erasure.
  Artifact creation/import/removal serialize through the existing SQLite writer
  transactions, preserving already-recorded attribution.
- `library_history`: scoped, consistent predecessor-history pages, twenty
  versions at most, content hashes and a next-page ID. Bodies are omitted from
  history output. Missing/cross-scope predecessors, cycles and inconsistent
  version sequences refuse rather than fabricating a complete history.
- `LibraryBrowser`: project-keyed source/artifact browsing, bounded reads,
  separate consent and exact approve/reject/continuation for snapshot removal,
  version inspection/history paging and session-generation guards. Read actions
  also support explicit approvals when current policy requires them.
- Artifact creation form accepts an optional previous-version ID, using the
  existing immutable versioning contract. A superseded previous version cannot
  be silently overwritten. Existing import/research/export forms remain.

No migration or dependency changes were needed.

## Deferred regression source

Temporary databases/projects only: original-file and artifact-attribution
preservation; retrieval exclusion; stale hash; wrong project/root; revoked
dispatch; bounded version pages/cycles; actual API approval plus generic
continuation. Frontend source asserts exact removal arguments, no continuation
before user approval, and rejection of a late read after a session-epoch change
on the same mounted component. These tests have not been executed, so no passing
counts or production-readiness claim is made.

## Completion accounting

Source coverage and verified product completion are separate. Earlier milestone
documents contain historical pending items superseded by later source increments;
they must not be counted as new work without inspecting the current code. A
percentage based on file/line counts or navigation panels would be misleading.
Combined verification and failed-gate fixes are still required before reporting
a reliable release completion measure. The browser build allowlist and UIA
production gate remain closed. Installer/portable output and live integrations
remain unverified; this library increment does not close those release gates.
