# Complete library browsing — source continuation

The prior library could store 1,000 sources but list only the newest 100, leaving
older items inaccessible through normal browsing. This increment adds source and
artifact metadata pages without recreating import, search, history or forget
workflows. No application, test, build, AI verification, migration, dependency
installation or real-user-data operation ran.

`knowledge_document_page` and `library_artifact_page` use the existing project
executor. Current read policy/approvals still apply. Each call returns at most
50 metadata entries; content is read separately. Fixed internal table/column
contracts and bound SQL values preserve project/root isolation. Cursor size and
schema are bounded, timezone is required, and scope binds project, canonical
workspace root, collection and normalized literal query. Changing any binding
requires a first-page request. IDs break timestamp ties deterministically.

Both lists support literal title/content filters, including literal percent and
underscore characters. The old bounded context-search/list tools remain
compatible. Pages observe live metadata; they are not locked database snapshots.
New imports/deletions may require refresh. No factual verification is inferred
from stored text or recorded source hashes.

The library interface supports loading further source/artifact pages, retains
only the last 200 loaded metadata entries per list, and clears stale cursors
when the filter changes. A next-page read requiring approval retains its append
intent across approval/continuation, preserving already displayed results.
Existing project/session guards remain; no automatic read approval is added.

Deferred regression source covers all 121 fixture entries beyond the old limit,
tied timestamps, no duplicate pages, metadata-only responses, wrong project/root/
collection/query cursors, literal filtering, malformed tokens, real API read
approval/continuation, approval-mediated page append and clearing stale query
cursors. Temporary databases only. Tests have not executed; no passing count or
completed release gate is claimed.

This fixes a concrete remaining source gap. Full project completion still needs
the deferred combined verification and open native/model/Windows release gates.
