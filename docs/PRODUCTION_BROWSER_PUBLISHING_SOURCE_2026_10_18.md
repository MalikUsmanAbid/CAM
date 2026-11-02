# Browser integration and remaining publishing transport source — October 18

This record covers source additions, not release acceptance. **Tests, lint,
type checks, native compilation, builds and AI/live verification were deferred.**
No application, browser, native policy helper, account login or provider request
was run. No user database migration, dependency installation, stage/commit/push or
reset occurred. Existing source and user edits were preserved through scoped patches.

## Browser integration path

- The native host now has `--isolated-browser-cdp` in addition to the existing
  disposable probe mode. It uses fixed Chromium flags, a fresh AppContainer
  profile directory, private inherited CDP handles, no TCP debugging listener,
  the existing persistent WFP fence and restricted non-elevated token. No
  `--no-sandbox`, unrestricted browser fallback, user-profile reuse or automatic
  loopback exemption. Native job limits bound process count and memory.
- Native pipe relays bound individual frames and session traffic. Controller EOF,
  child exit, timeout or relay errors terminate the owned job. IO cleanup is
  bounded; a stuck relay exits the helper with persistent deny policy retained.
- `isolated_browser.py` starts only an approved broker/browser binary pair,
  retains executable hash locks, owns a pinned public-destination proxy and
  speaks bounded, serialized CDP over private stdin/stdout. Session-owner
  revocation, cancellation, malformed messages, excessive events or timeouts
  terminate the owned broker. No raw renderer error text is returned.
- Fixed CDP operations cover navigation with loader-commit evidence, bounded
  page text, unique visible CSS targets, click/type/select/scroll, ready-state
  observation, refresh and bounded history selection. Model arguments remain
  JSON data inside fixed code. Password/file inputs and file transfer are denied;
  downloads are disabled at initialization. Browser call return does not prove
  downstream website effects or undo them.
- Browser sessions now bind to authenticated owners and serialize operations.
  Cross-owner inspect/navigate/close denies. The API remains Personal-only;
  runtime creation is wired through the existing routes and registry.

**Production activation is still denied.** `ACCEPTED_BROWSER_BUILDS` is empty.
`JARVIS_BROWSER_BROKER` and `JARVIS_BROWSER_EXECUTABLE` provide paths only and
cannot bypass acceptance. No native binary pair, dependency inventory, privilege
delegation, loopback setup, Chromium compatibility or escape matrix has been
accepted. The registry reports unavailable tools in this default state. Completing
the source path is not a claim that M7 or safe browser deployment is complete.

Remaining acceptance includes WFP/IPC/DNS/COM/child escape probes, broker and proxy
failure/port reuse, OS privileges and ACLs, full runtime dependency integrity,
redirects, renderer termination, actual visible windows and cleanup. See the
[native isolation boundary](../native/browser_isolation/README.md). No environment
setting should be used as a substitute for these checks.

## Remaining publication adapters

`extended_publishing.py` adds five provider-specific source workflows, all through
the existing schema → policy/approval → ToolExecutor → durable account-action
ledger. Connections and publication forms now expose them; no real account was
connected. Current social catalog labels all ten platforms as source/unverified,
with separate limits for text, image URL, paid text, video URL and bounded upload.

| Provider | Implemented source contract | Limits/boundary |
|---|---|---|
| Facebook | Fixed Graph v26 Page feed POST; exact numeric Page and text | Page access token/scopes configured separately; no personal profile posts, ads or scheduling |
| Instagram | Exact business user/image URL/caption/alt text; create container, bounded readiness reads, then publish exact returned container | Rechecks credentials and policy before each request. Unready/uncertain containers are retained as receipts; no consequential retries, videos or carousel |
| X | Fixed v2 text-post endpoint, exact text and pricing-policy revision | Default paid allowance absent/zero. Separate financial approval configures conservative per-request price and UTC daily limit. Reservations serialize and remain on uncertain failure; not a verified price or invoice guarantee |
| YouTube | Fixed upload endpoint; approved video hash, immutable bounded bytes, exact metadata, visibility/kids/notification flags; multipart upload | Personal workspace only, MP4/WebM/MOV extension, single-link file and 32 MiB bound. No codec/content verification, resumable uploads or large-file support |
| TikTok | Current creator query rendered in UI; explicit privacy selection; current settings hash rechecked before video URL export | Checks current allowed audience, interactions and stated duration. User must own/control provider-verified URL. Provider fetches media; JARVIS does not verify actual duration/bytes. No file upload, auto-selected privacy or publication-completed claim |

All requests use fixed official credential destinations, no environment proxies
or redirects, bounded identity-encoded responses, protected token references and
pre-request checks. HTTP 200 alone is insufficient: new adapters validate response
identities and TikTok's semantic error code. Partial Instagram state retains its
container identity. Reusing an operation ID never issues another consequential
request. Already accepted requests cannot always be cancelled or undone.

Migration **13** adds only publishing budgets/reservations; prior migration
sources remain unchanged. The UI can inspect durable receipts and current budget
revisions after approval continuation. No real migration was performed.

Provider references: [Meta Page SDK contract](https://raw.githubusercontent.com/facebook/facebook-python-business-sdk/main/facebook_business/adobjects/page.py),
[Meta IG SDK contract](https://raw.githubusercontent.com/facebook/facebook-python-business-sdk/main/facebook_business/adobjects/iguser.py),
[Meta SDK API version](https://raw.githubusercontent.com/facebook/facebook-python-business-sdk/main/facebook_business/apiconfig.py),
[X create-post API](https://docs.x.com/x-api/posts/create-post),
[YouTube upload](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert),
[TikTok creator information](https://developers.tiktok.com/doc/content-posting-api-reference-query-creator-info),
[TikTok video export](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post),
[Chromium Windows private pipe transport](https://chromium.googlesource.com/external/github.com/chromedp/chromedp/+/refs/tags/remote/v0.2.0/pipe_windows.go).

## Regression source, not executed

### Browser cleanup continuation

Startup failures and established CDP sessions now share owned-broker cleanup.
Repeated caller cancellation cannot interrupt proxy shutdown or executable-pin
release: a shielded, awaited cleanup task completes first. Cancellation requests
kill the owned broker; ordinary shutdown permits two seconds before killing it,
then bounds process reaping separately. Pin release also occurs if proxy shutdown
raises. Persistent WFP denies remain in place; this is not firewall recovery or
an undo mechanism for already accepted page actions.

Native receipts require real integer protocol/port/PID fields and an object
shape. CDP replies require the exact pending integer request ID; booleans, strings
and unexpected IDs close the channel instead of being accepted or ignored.
New regression source covers repeated cancellation during process and proxy
cleanup, proxy-close failure, and malformed/coerced response IDs. These tests
have not been run. Browser deployment, per-profile loopback provisioning,
persistent-policy recovery and independent Windows acceptance remain pending;
the accepted-build allowlist remains empty.

- Publishing: actual injected method/URL/body contracts; duplicate suppression;
  Instagram revocation between steps and partial receipt; default-zero X pricing,
  exact reservation/daily limit; immutable YouTube media bytes/hash/metadata;
  TikTok changed creator settings and semantic-error rejection. Temporary DBs and
  fixture media only; injected transport results are not live publication proof.
- Browser: absent acceptance denies despite path configuration; actual CDP method
  contracts and navigation loader; download/file-transfer denial; owner/session
  revocation before command write; cross-actor session denial; malformed CDP
  response closes resources. Fake channels validate orchestration, not native OS
  isolation or real browser execution.
- Frontend: exact Facebook approval arguments, live-choice-shaped TikTok UI with
  no default audience, and financial-consent reset on pricing edits.

The complete project, production browser security and Windows release acceptance
are still not declared complete. Source workflows have intentionally bounded
initial scopes; provider registrations, consent, credentials, paid prices and live
capabilities must be configured and verified separately.
