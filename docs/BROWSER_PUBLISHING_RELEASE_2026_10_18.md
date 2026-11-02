# Browser, publishing and release source increment — October 18

Implementation only. **No tests, lint, type checks, native compile, build, live AI,
application launch, account login, publishing or Windows policy modification was
performed.** User persistence and existing dirty files were preserved. No package
installation, dependency-version change, staging, commit or push occurred.

## Browser isolation

`native/browser_isolation` now contains actual Windows SDK C++ source for a
package-specific WFP fence, fresh AppContainer creation, a suspended restricted
probe launcher, package/elevation validation, bounded kill-on-close process job
and a disposable loopback TCP/UDP/IPv4/IPv6 probe. Policy installation is
transactional and precedes child launch. Persistent deny rules survive controller
death. Teardown removes rules only after the owned job reports zero processes.
No loopback exemption, elevated service or firewall permission is installed by
the application. These helpers are **experimental and not shipped/activated**.

[Native implementation and remaining acceptance boundaries](../native/browser_isolation/README.md)
describe privilege requirements, stale-policy recovery, IPC/DNS/BFE limitations
and missing real Chromium transport integration. This is progress on isolation,
not completion of M7. Production browser remains disabled. A passing loopback
probe alone would not meet the complete browser acceptance matrix.

## Publishing adapters

- Bluesky: fixed `com.atproto.repo.createRecord` route on the saved public HTTPS
  PDS. Exact origin equality is checked before dispatch and after async proxy
  startup. All custom PDS traffic uses the public-address pinned proxy; redirects,
  environment proxies and private addresses remain disallowed. Explicit DID,
  timestamp, TID record key, public consent and text are approval arguments.
  Conservative 300-codepoint/3000-byte text bound; no facets/media/password login,
  automatic token refresh or handle discovery. The configured credential is an
  already obtained access JWT; stored scope labels do not prove token privileges.
- Threads: official single-request text auto-publishing endpoint, fixed
  `graph.threads.net/v1.0/me/threads`, form-encoded `TEXT`, exact text,
  `auto_publish_text=true`, replies open to everyone. Requires separately
  configured `threads_basic` and `threads_content_publish` access and explicit
  public-post approval. URLs may cause provider-generated previews. No uploads,
  scheduling, OAuth setup or silent two-step fallback.
- Pinterest: fixed official v5 create-Pin endpoint with an exact existing board,
  title/description/alternative text and externally fetched HTTPS image URL.
  Explicit board-publication consent; no ads, local upload, image preview or video.
  Rejects literal private/local addresses, nonpublic host syntax, credentials,
  query and fragment. This is syntactic URL validation, not control of Pinterest's
  DNS/fetching, image-byte pinning, ownership or the board's current visibility.
  Current official scope set is exposed in Connections. Both documented 200/201
  acceptances use the existing durable operation handling.
- All three use the existing one-operation durable ledger, pre-transport credential
  checks, protected vault references, exact payload approval and no automatic
  replay after accepted/uncertain outcomes. HTTP acceptance is not asserted to be
  successful live publication. Connections UI, capability refresh, execute-route
  allowlist and honest source-only social catalog are integrated.

Regression source inspects real injected HTTP request bodies/headers/destinations,
credential revocation at dispatch, duplicate suppression and uncertain failures.
Pinterest regression source includes exact media/board payload, both accepted
status codes, insufficient-scope rejection and unsafe image URL rejection.
Frontend source covers exact Bluesky/Pinterest approval data and consent reset. All fixtures
are temporary and synthetic; none were run or connected to real accounts.

References used for source contracts: [Bluesky createRecord lexicon](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/com/atproto/repo/createRecord.json),
[Meta's official Threads API collection](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api),
[Pinterest's official API schema](https://raw.githubusercontent.com/pinterest/api-description/main/v5/openapi.yaml).
Facebook's official pages were rate-limited during inspection; no speculative
Facebook adapter was enabled. Facebook/Instagram/X/YouTube/TikTok actual
publication/media transports and paid-provider budget controls remain pending.

## Desktop release

The existing explicit release builder now emits an exact per-member portable
manifest, hashes every runtime/launch/executable member and invokes a separate
bounded read-only package inspector as part of a future build. Installer and
portable outer checksums retain their separate release manifest.

`scripts/release_integrity.py` and `python -m scripts.inspect_release <directory>`
inspect ZIP inventory, Windows aliases/traversal/device names, symlinks/encryption,
expansion bounds, exact member hashes, required files, excluded persistence/secrets
and x64 executable headers without extraction or execution. Undeclared release
directory files and changed installer/archive bytes reject the package. The
central directory/footer is bounded before ZIP inventory allocation; multi-disk
and ZIP64 packages are deliberately unsupported by this conservative inspector.
Manifest reads remain bounded even if a file grows after metadata inspection. The
manifest is not signed; these checks do not establish publisher authenticity,
installer behavior, startup or clean-machine acceptance.

Regression source uses inert PE-shaped bytes and temporary ZIPs, rejects checksum
changes, extra user-data files and duplicate aliases. No real installer/portable
package was built. Missing native prerequisites must be resolved explicitly when
the user resumes build/verification; no toolchain or runtime was installed here.
