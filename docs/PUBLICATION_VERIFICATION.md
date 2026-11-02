# Publication verification

Prepared: 2026-11-02. This record concerns source presentation and the CAM naming changes; it does not certify the whole product.

## Checks and evidence

| Check | Result | Scope |
| --- | --- | --- |
| Frontend production build | PASS | `npm run build`, including TypeScript; final UI source built before documentation captures |
| ESLint | PASS | Completed during the naming/publication work; no subsequent behavior change beyond display labels |
| Dashboard regressions | 22/22 PASS | `npm test -- --pool=forks --maxWorkers=1 --testTimeout=30000 src/App.test.tsx` |
| Setup-related test files | Three files passed in the earlier focused run | Welcome, model setup and advanced provider panel; this is not full-suite evidence |
| Initial default-timeout test run | FAILED | 16 failures with a 5-second deadline; retained here for transparency; dashboard rerun passed with the documented deadline |
| Documentation links | PASS | Ten new/updated Markdown entry points; local links and image targets checked |
| Banner | PASS | SVG parsed as XML and referenced locally; original artwork, no script or external asset dependency |
| GitHub presentation | PASS | README images loaded on GitHub; architecture diagram uses a local SVG after the rich-diagram renderer failed in the preview browser |
| Screenshot format | PASS | JPEG signatures verified; images inspected visually |
| Current files secret-pattern scan | No matches | Common GitHub, Gemini, Anthropic, NVIDIA and private-key-body patterns |
| Published history secret-pattern scan | No matches | All 309 blobs reachable from the initial published snapshot checked |
| Whitespace check | PASS | `git diff --check` after source-copy whitespace cleanup |

Pattern scanning is not a guarantee that no sensitive information exists. Review before changing repository visibility; GitHub security features and third-party dependency notices remain separate gates.

## Screenshot provenance and hashes

The captures use the compiled frontend with an isolated loopback documentation fixture, a simulated desktop bridge and intercepted fixture responses. No application backend, real persistence, API keys or live provider calls were used. See [SCREENSHOTS.md](SCREENSHOTS.md).

| File | SHA-256 |
| --- | --- |
| `cam-overview.jpg` | `de4b08d5439494de04995047c3b53c5c25159149702a8f1cd09f641999ff560a` |
| `cam-setup.jpg` | `d4537d58f4ca064f7730bb52168f0ad4bc69f5f1476e6977786f0a4ecfb34109` |
| Compiled frontend JS | `043e5fe6c505462d1c65827e0e0128dad3ddfeda4e792469bdb11e1352694bce` |
| Compiled frontend CSS | `40b998d235585a699b7ce26595e4f83ce341f757bf057433e74f4cde77328c86` |

The 74 frontend files in the Desktop source copy were compared with the built working source. The only difference was an extra trailing blank line removed from `bluesky-record-key.ts`; no frontend logic differed. The original working repository's history and personal persistence were not staged, pushed or reset.

## Remaining decisions and limitations

Repository visibility remains private while the owner reviews public publication and reuse/license rights. No license was selected automatically, no CI success badge was fabricated, and no paid workflow or live AI run was started.

The initial source commit retains the existing Git author/contact metadata. The owner should review that metadata before making the repository public. New documentation commits use a GitHub noreply identity; earlier history was not rewritten or force-pushed.

Fresh CAM native packaging, full-suite verification, live providers and platform/security acceptance remain pending as described in [STATUS.md](STATUS.md). Historical package names and persistence paths remain for upgrade compatibility.
