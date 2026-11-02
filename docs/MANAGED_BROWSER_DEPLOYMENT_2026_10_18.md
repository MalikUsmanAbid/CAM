# Managed browser deployment and recovery — source implementation

No native compilation, tests, tool installation, administrator action, registry
write, firewall change, deployment or application start was performed here.
All instructions below are for the later explicitly authorized Windows stage.
The accepted broker/browser build set remains empty. Source is not security
acceptance evidence.

## Supported deployment boundary

One elevated controller at a time, on a local Windows machine, using the same
interactive split-token administrator account as the non-elevated backend.
UAC consent occurs per managed session. Using a different administrator's
credentials, LocalSystem/service accounts, multiple simultaneous controllers,
unprotected portable broker binaries, network installs and unattended elevation
are unsupported and refuse. The backend is never elevated by this design.
The threat boundary is a compromised nonprivileged browser, not administrator
policy tampering or arbitrary new package tokens manufactured by another
out-of-band process. External loopback-policy administrators must coordinate
maintenance; no cross-vendor global configuration lock is claimed.

## Source path

1. `scripts/build_browser_native.py` compiles a later explicit x64 native build,
   now with static MSVC runtime selection. It never installs prerequisites.
2. `scripts/deploy_browser_broker.py` is a separately invoked elevated deployment
   recipe. It accepts an exact native build manifest and a separately reviewed
   full Chromium distribution manifest (`jarvis-runtime-assets-v1`, including
   root `chrome.exe`). It verifies copied bytes, rejects links/hard links and
   bounds assets. Hash equality does not establish licensing or authenticity.
   It creates only a NEW OS-known Program Files/USMANJARVISBrowserBrokerV1 tree;
   existing installations cause refusal. Administrators/System own/write the
   new tree; Users and AppContainers receive read/execute. Receipt remains
   activation denied. It does not alter existing browser ACLs, register a
   service/task, change settings or grant network access. A failed deployment
   stays inert for explicit inspection, without automatic recursive deletion.
3. `NativeBrowserRuntime` launches only the accepted, pinned broker/browser
   pair, requesting `--request-managed-browser`. This bridge stays non-elevated
   and presents Windows UAC for its protected executable. The application
   receipt deadline includes a bounded allowance for consent; cancellation
   closes/kills the ordinary relay. No TCP debugging or elevated backend.
4. `elevation_bridge.cpp` creates a fresh local-only first-instance named pipe
   with exact-user/System ACL. Both sides verify the peer PID, same user and
   expected executable identity. The executable and every containing component
   under Program Files must have trusted ownership, no reparse points and no
   untrusted write grants. Private pipes carry a bounded binary port/path
   contract, receipt and bounded CDP traffic. UAC uses the protected executable
   directory. Remote pipe clients, different-account elevation and path aliases
   that cannot establish identity refuse. Only managed session startup is
   exposed; no general elevated shell/RPC commands.
5. `ManagedDeployment` requires protected deployment, elevation and a non-service
   interactive user. A global administrator-only lease serializes all supported
   managed launches and recovery. HKLM recovery records have a protected
   Administrators/System-only DACL; insecure pre-existing keys refuse.
6. `BrowserFence` invokes a journal callback before committing its six persistent
   package-scoped deny filters. Journal identity includes actor SID, profile,
   package SID, sublayer, exact filter GUIDs and boot epoch, flushed before commit.
   Only after successful commit does the controller invoke the fixed OS
   CheckNetIsolation executable with a single package SID. It checks the actual
   resulting exemption. It never passes a shell string to a command processor,
   restores an old global exemption snapshot or requests a global reset.
7. Chromium runs with the existing restricted same-user medium-integrity token,
   AppContainer, fixed proxy, private CDP handles and kill-on-close job limits.
   Browser children receive no privileged controller handles. The OS helper
   used for loopback mutation has its own bounded job and minimal environment.

## Cleanup and restart recovery

Normal shutdown first proves the browser job empty, revokes the exact package's
loopback exemption, removes only its recorded deny filters/sublayer, deletes the
profile, then deletes the journal. Failed profile deletion is retryable and no
longer silently reports successful cleanup.

Controller/relay crashes retain deny policy and durable identity. Current-boot
crash records are not automatically deleted: a process snapshot is insufficient
launch exclusion. A protected volatile registry BootEpoch disappears on reboot.
On the next managed startup after a full kernel restart/shutdown, a new epoch is
generated under the controller lease. Windows Fast Startup/hibernate can preserve
the volatile hive; an unchanged epoch conservatively retains the crash record
until a full restart. No forced hive unload, reboot or power-setting change.
Only completed records for the SAME user from a prior epoch are eligible for
automatic recovery. No wall-clock arithmetic or caller-supplied "rebooted" claim.
Profiles are fresh per session and supported controllers never relaunch an old
profile. Recovery revokes loopback first, verifies the recorded profile-derived
SID and exact package-scoped persistent block filters, then transactionally
removes those rules. Missing already-removed rules are tolerated; unknown
sublayer contents prevent transaction commit. Profile/record cleanup follows.
Interrupted or failing recovery retains evidence and refuses a weaker fallback.
Partially written records with no final prepared marker cannot have committed
managed policy or granted loopback; they remain diagnostic records.

`--recover-managed-browser` runs the same prior-boot recovery explicitly from an
elevated protected broker, with no browser launch. It does not force same-boot
cleanup or bypass the user/epoch/ownership checks. Recovery is on managed startup
after reboot, not a background scheduled administrator task. Accepted external
website effects cannot be undone by process/policy cleanup.

## Deferred evidence

Python regression source checks exact managed-bridge invocation, deployment
inventory, changed-byte refusal, no activation receipt on failure, scoped ACL
targets and preservation of existing files. Injected fixtures establish neither
native ACL correctness nor WFP security. No new passing count is claimed.

The later disposable Windows matrix must include native compilation; UAC cancel,
different-user denial, pipe spoofing/remote/PID mismatch, writable/reparse path
refusal, executable pinning and loader behavior, unrelated exemption preservation,
grant/revoke failures, pre/post-commit crashes, live descendant cleanup, reboot
epoch replacement, other-user/current-boot record preservation, repeated recovery,
partial deletions, full Chromium sandbox/proxy behavior, direct TCP/UDP/IPv6/QUIC
and child escapes, BFE interruption and proxy port reuse. Keep browser disabled
until independent review and this real matrix pass. These are unresolved release
gates, not a claim of completed or verified browser isolation.

Primary API references:
[loopback configuration preservation](https://learn.microsoft.com/en-us/windows/win32/api/netfw/nf-netfw-networkisolationsetappcontainerconfig),
[configuration inspection and memory ownership](https://learn.microsoft.com/en-us/windows/win32/api/networkisolation/nf-networkisolation-networkisolationgetappcontainerconfig),
[single-package loopback operation](https://learn.microsoft.com/en-us/minecraft/creator/documents/bedrockserver/getting-started?view=minecraft-bedrock-stable),
[restricted process tokens](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-createrestrictedtoken).
Volatile hive lifetime follows
[RegCreateKeyEx](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regcreatekeyexw).
