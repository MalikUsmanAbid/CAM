# Experimental AppContainer network fence source

**Not compiled, executed, accepted or enabled in production.** The default application
still raises `BrowserNetworkIsolationUnavailable`. Native CDP integration source is
now wired, but its accepted-build allowlist is empty. Path configuration cannot
activate an unaccepted build. The release builder does not ship/invoke privileged probes.

The Windows SDK C++ library commits persistent WFP rules scoped to a newly
created AppContainer SID before the helper launches any process. IPv6 and inbound
accepts are denied. IPv4 is denied unless protocol is TCP and destination is
exactly `127.0.0.1:<owned proxy port>`. These are deny rules, not a bypass of the
Windows firewall/AppContainer's own policy. No Internet/private-network capability,
loopback exemption, service installation or elevation is performed automatically.

The disposable host creates a suspended child with `SECURITY_CAPABILITIES`,
assigns it to a kill-on-close job without breakaway, checks its package SID and
uses a restricted normal-user/medium-integrity token before rejecting any elevated
child at the final pre-resume check. No administrator token launch fallback.
Environment inheritance is restricted
to SystemRoot. Timeout kills the job. Filter removal requires an observed empty
owned job. Controller crash retains package-specific deny rules/profile; no
destructor removes security policy while children might still exist. Such stale
profiles need explicit administrative recovery. Other applications are not
selected by these rules.

## Later disposable Windows acceptance

An explicitly authorized administrator must build using an installed MSVC/Windows
SDK and CMake, provision only necessary read/execute ACLs for fixture binaries,
and arrange approved loopback access separately. No current-machine policy was
changed during source implementation. The source intentionally fails if required
permissions, profile creation, WFP transaction, child SID or job ownership fails.

`jarvis-isolation-probe-host --disposable-network-probe <proxy-port> <absolute-probe.exe>
--disposable-loopback-fixtures <proxy-port> <other-listening-port>` launches the
bounded socket fixture in the fenced package. Ports must be distinct and >=1024.
Use actual disposable listeners. It requires WSAEACCES for prohibited sockets;
connection refused, absent IPv6, unavailable proxy and timeout are failures, not
passing evidence. No DNS/public Internet requests are made by this fixture.

This loopback probe is only part of the required matrix. Child-process token
inheritance, direct remote IPv4/IPv6 TCP/UDP/QUIC, DNS rebinding, redirects,
broker/engine interruption, IPC/COM escape, executable/profile ACLs, native API
failures, proxy lifetime/port reuse and real Chromium sandbox compatibility remain
unverified. Persistent BFE rules are not boot-time filters; privileged OS-policy
tampering and BFE shutdown are outside a nonprivileged-browser threat boundary.
Do not call this complete browser isolation or enable the browser before those
gates pass and the integrated broker/private CDP path is accepted with an explicit
least-privilege deployment and per-profile loopback setup. The helper does not
silently edit the machine's loopback-exemption list; without appropriate setup,
even the allowed proxy connection may remain denied by AppContainer policy.

The new `--isolated-browser-cdp <proxy-port> <absolute-browser.exe>` mode exposes
only private inherited CDP pipes, a fresh profile, fixed proxy/launch flags and a
bounded protocol receipt. The default application cannot invoke it successfully
until native acceptance/deployment is resolved. See
[integration source record](../../docs/PRODUCTION_BROWSER_PUBLISHING_SOURCE_2026_10_18.md).

## Managed deployment continuation (source only)

The earlier disposable mode above still makes no loopback grant. Managed mode
now has a separate source path: protected Program Files deployment, an explicit
same-user UAC/private-pipe bridge, exact-package loopback setup, flushed protected
journal before WFP commit, and prior-boot recovery on managed startup. Current
boot crash denies remain. No service or different-account elevation is supported.
The backend stays non-elevated. The accepted-build gate remains empty. Native compilation passed on 2026-10-23;
disposable Windows security acceptance has not run. See the
[managed deployment/recovery record](../../docs/MANAGED_BROWSER_DEPLOYMENT_2026_10_18.md)
for supported boundaries, later recipes and the required verification matrix.
Earlier statements that loopback/deployment source was absent are historical;
they are not proof that this new implementation has passed its release gates.

References: [WFP package SID condition](https://learn.microsoft.com/en-us/windows/win32/fwp/filtering-condition-identifiers-),
[persistent WFP filters](https://learn.microsoft.com/en-us/windows/win32/api/fwpmu/nf-fwpmu-fwpmfilteradd0),
[process security attributes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute).


## Native build verified 2026-10-23

The existing MSVC 14.44 / Windows SDK 10.0.26100.0 toolchain compiled all three
x64 executables. If the Visual Studio CMake generator cannot discover the installed
instance, use an x64 MSVC developer environment with the installed Ninja directory
on PATH, then run `python -m scripts.build_browser_native --generator Ninja
--cmake <existing-cmake.exe> --output-directory <absent-output-directory>`.
The recipe does not install tools or deploy policy. The System32-only dynamic
FirewallAPI lookup follows [Microsoft's API documentation](https://learn.microsoft.com/en-us/windows/win32/api/networkisolation/nf-networkisolation-networkisolationgetappcontainerconfig).
A missing DLL/export or failed query still denies deployment. Compilation and
SHA evidence are in [native build evidence](../../docs/evidence/native-build-2026-10-23.json).
The read-only recovery inventory returned 70 (incomplete) on this session; it is
not successful recovery evidence. No filters, profiles or exemptions were changed.
