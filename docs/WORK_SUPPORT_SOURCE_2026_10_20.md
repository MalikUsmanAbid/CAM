# Work records, support export and question dismissal — source continuation

This completes the interrupted source increment from October 19 and adds question
dismissal on October 20. No tests, lint/type checks, builds, application startup,
AI verification, real-data changes, installation or migrations ran. Existing user
changes were preserved. No dependency versions or database schema changed.

## Work records

The existing lead/proposal/order/follow-up/content-draft storage now has a usable
editor and archive/restore workflow. Writes keep the existing observed-revision
contract. A newer tab's edit is not overwritten; a failed stale save preserves
the local unsaved form. Row actions are disabled while unsaved content exists
and clearing/discarding is explicit. Archiving is not permanent deletion, sending,
publishing, payment or an external order.

`GET /api/v1/workspace/records/page` adds bounded, literal title/content search,
kind/status filters and keyset pages of fifty records. Timestamp/ID ordering
handles ties deterministically. Cursor data is validated, bounded and bound to
the selected project/filters; it grants no authority. SQL always retains the
server-selected scope. Existing list/create/update routes remain compatible.
Pages observe current data rather than a locked snapshot; concurrent edits can
reorder results. The interface deduplicates displayed IDs and retains only the
last 200 loaded records to bound memory. Refresh returns to recent results.

## Redacted support download

The Execution Control panel downloads an explicitly requested JSON support
report from authenticated diagnostics. The report serializes only known platform,
runtime/schema version, recognized provider/scheduler labels and boolean status.
Future/unknown response fields and arbitrary label strings are excluded. The
backend also replaces unknown configured provider labels instead of returning
raw environment-derived text. No prompts, record contents, project paths,
credentials, endpoint URLs or account details are exported. Late responses after
logout/unmount cannot produce a download. This report does not perform tests or
establish release/security acceptance.

## Questions

Pending questions can be dismissed without inventing an answer. The request binds
the observed question hash and current server-selected scope. Dismissal validates
the pending row inside a writer transaction and retains question/history data.
Answering and dismissal compete on the pending state; exactly one can win.
Dismissal neither resumes nor cancels the associated task. Previously answered
or dismissed questions cannot be overwritten by a late answer/dismissal.

## Deferred regression source

Temporary backend fixtures cover timestamp-tied pages, literal search, cursor
scope/filter changes, malformed tokens, stale revisions, API paging/archiving,
question scope/hash mismatch, synchronized answer-versus-dismissal and actual
task-count preservation. Frontend fixtures cover observed-revision transport,
unsaved draft preservation, paging scope, protection against destructive row
switching, diagnostics field exclusion and logout-safe downloads. These tests
are source only; no passing count is claimed.

The global Spec Kit preference was read. This is ongoing work in the existing
JARVICS project, not a new project or an explicit speckit command; no .specify
bootstrap, project reinitialization or unrelated scaffolding was performed.

Combined verification, security review, Windows packaging/acceptance, actual model
workflows and remaining native integration gates are still open. Browser/UIA
production gates remain closed. Source presence does not mean the complete
project is ready for release.
