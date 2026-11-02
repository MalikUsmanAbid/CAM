"""Integration-contract fixtures only; no browser/WFP/process is started."""

import asyncio
import json
from contextlib import ExitStack, contextmanager

import pytest

from services.agent_api.app import isolated_browser
from services.agent_api.app.browser import BrowserManager, OpenInput, SessionInput
from services.agent_api.app.isolated_browser import (
    ACCEPTED_BROWSER_BUILDS,
    CDPChannel,
    IsolatedBrowserAdapter,
    NativeBrowserRuntime,
    release_broker,
)
from services.agent_api.app.session_security import execution_session


class Policy:
    allow_private_addresses = False

    async def validate(self, url, domains):
        if (
            not isinstance(url, str)
            or not url.startswith("https://allowed.example/")
            or domains != ("allowed.example",)
        ):
            raise ValueError("URL denied")


@pytest.mark.anyio
async def test_repeated_cancellation_finishes_broker_proxy_and_pin_cleanup():
    effects = []
    waiting, killed, proxy_started, proxy_finish = (asyncio.Event() for _ in range(4))

    class Input:
        def close(self):
            effects.append("stdin closed")

    class Process:
        returncode = None
        stdin = Input()

        async def wait(self):
            waiting.set()
            await killed.wait()
            self.returncode = -1

        def kill(self):
            effects.append("broker killed")
            killed.set()

    class Proxy:
        async def close(self):
            proxy_started.set()
            await proxy_finish.wait()
            effects.append("proxy closed")

    stack = ExitStack()
    stack.callback(lambda: effects.append("pins released"))
    task = asyncio.create_task(release_broker(Process(), Proxy(), stack))
    await waiting.wait()
    task.cancel()
    await proxy_started.wait()
    task.cancel()
    # Let the second cancellation reach the shield before releasing the proxy.
    await asyncio.sleep(0)
    assert "pins released" not in effects
    proxy_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert effects == ["stdin closed", "broker killed", "proxy closed", "pins released"]


@pytest.mark.anyio
async def test_startup_cleanup_releases_pins_when_proxy_shutdown_fails():
    effects = []

    class Proxy:
        async def close(self):
            raise OSError("fixture close failure")

    stack = ExitStack()
    stack.callback(lambda: effects.append("pins released"))
    with pytest.raises(OSError, match="fixture close failure"):
        await release_broker(None, Proxy(), stack)
    assert effects == ["pins released"]


@pytest.mark.anyio
async def test_paths_and_environment_cannot_enable_an_unaccepted_build(tmp_path, monkeypatch):
    assert not ACCEPTED_BROWSER_BUILDS
    monkeypatch.setenv("JARVIS_BROWSER_BROKER", str(tmp_path / "fixture.exe"))
    runtime = NativeBrowserRuntime(tmp_path / "fixture.exe", tmp_path / "browser.exe")
    assert runtime.available is False
    with pytest.raises(ValueError, match="accepted"):
        await runtime.create(("allowed.example",), Policy())


@pytest.mark.anyio
async def test_accepted_runtime_requests_separate_managed_bridge_without_elevating_backend(
    tmp_path, monkeypatch
):
    calls = []
    broker, browser = tmp_path / "broker.exe", tmp_path / "chrome.exe"
    broker.write_bytes(b"inert broker")
    browser.write_bytes(b"inert browser")

    @contextmanager
    def pin(path):
        try:
            yield "broker-hash" if path == broker else "browser-hash"
        finally:
            calls.append(("unpin", path.name))

    class Proxy:
        def __init__(self, _):
            pass

        async def start(self):
            return "http://127.0.0.1:12345"

        async def close(self):
            calls.append(("proxy closed",))

    class Input:
        def close(self):
            calls.append(("stdin closed",))

    class Output:
        async def readline(self):
            return (
                json.dumps(
                    {
                        "protocol": 1,
                        "pid": 42,
                        "proxy_port": 12345,
                        "package_sid": "S-1-15-2-1-2-3-4-5-6-7",
                    }
                ).encode()
                + b"\n"
            )

    class Process:
        returncode = None
        stdin, stdout = Input(), Output()

        async def wait(self):
            self.returncode = 0

    async def spawn(*arguments, **options):
        calls.append(("spawn", arguments, options))
        return Process()

    async def initialize(_):
        calls.append(("initialize",))

    monkeypatch.setattr(NativeBrowserRuntime, "available", property(lambda _: True))
    monkeypatch.setattr(
        isolated_browser, "ACCEPTED_BROWSER_BUILDS", frozenset({("broker-hash", "browser-hash")})
    )
    monkeypatch.setattr(isolated_browser, "locked_executable", pin)
    monkeypatch.setattr(isolated_browser, "PinnedHTTPProxy", Proxy)
    monkeypatch.setattr(isolated_browser.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(IsolatedBrowserAdapter, "initialize", initialize)
    token = isolated_browser.execution_authorized.set(lambda: True)
    try:
        adapter = await NativeBrowserRuntime(broker, browser).create(("allowed.example",), Policy())
        invocation = next(call for call in calls if call[0] == "spawn")
        assert invocation[1] == (str(broker), "--request-managed-browser", "12345", str(browser))
        assert invocation[2]["stdin"] == asyncio.subprocess.PIPE
        assert invocation[2]["stdout"] == asyncio.subprocess.PIPE
        assert "shell" not in invocation[2]
        await adapter.close()
        assert ("proxy closed",) in calls and ("unpin", "broker.exe") in calls
    finally:
        isolated_browser.execution_authorized.reset(token)


@pytest.mark.anyio
async def test_adapter_uses_cdp_navigation_commit_and_download_denial_not_echo():
    calls = []

    class Channel:
        async def call(self, method, params=None, session_id=None):
            calls.append((method, params, session_id))
            if method == "Target.createTarget":
                return {"targetId": "actual-target"}
            if method == "Target.attachToTarget":
                return {"sessionId": "actual-session"}
            if method == "Page.navigate":
                return {"loaderId": "new-loader"}
            if method == "Page.getFrameTree":
                return {
                    "frameTree": {
                        "frame": {"loaderId": "new-loader", "url": "https://allowed.example/page"}
                    }
                }
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "value": "https://allowed.example/page"
                        if params["expression"] == "location.href"
                        else "fixture text"
                    }
                }
            return {}

        async def close(self):
            pass

    adapter = IsolatedBrowserAdapter(Channel(), ("allowed.example",), Policy())
    await adapter.initialize()
    assert calls[0] == ("Browser.setDownloadBehavior", {"behavior": "deny"}, None)
    assert await adapter.open_url("https://allowed.example/page") == {
        "url": "https://allowed.example/page"
    }
    assert ("Page.navigate", {"url": "https://allowed.example/page"}, "actual-session") in calls
    assert (await adapter.inspect_page())["text"] == "fixture text"
    with pytest.raises(ValueError, match="file transfer"):
        await adapter.act("upload", "input", "user-selected:anything")


@pytest.mark.anyio
async def test_broker_owner_revocation_stops_channel_before_any_command_write():
    effects = []

    class Input:
        def write(self, _):
            effects.append("must not write")

        def close(self):
            effects.append("stdin closed")

    class Process:
        returncode = None
        stdin = Input()

        async def wait(self):
            self.returncode = 0

    class Proxy:
        async def close(self):
            effects.append("proxy closed")

    stack = ExitStack()
    stack.callback(lambda: effects.append("executable pins released"))
    channel = CDPChannel(Process(), Proxy(), stack)
    channel.owner_active = lambda: False
    with pytest.raises(ValueError, match="owner session"):
        await channel.call("Page.navigate", {"url": "https://allowed.example/"})
    assert effects == ["stdin closed", "proxy closed", "executable pins released"]


@pytest.mark.anyio
async def test_browser_session_is_owned_by_authenticated_actor():
    calls = []

    class Adapter:
        async def open_url(self, url):
            calls.append("open")
            return {"url": url}

        async def inspect_page(self):
            calls.append("inspect")
            return {"text": "fixture"}

        async def close(self):
            calls.append("close")

    manager = BrowserManager(Policy())
    token = execution_session.set("owner-A")
    try:
        manager.create("fixture-session", Adapter(), ["allowed.example"])
        other = execution_session.set("owner-B")
        try:
            with pytest.raises(ValueError, match="another authenticated"):
                await manager.open(
                    OpenInput(session_id="fixture-session", url="https://allowed.example/page")
                )
            with pytest.raises(ValueError, match="another authenticated"):
                await manager.inspect(SessionInput(session_id="fixture-session"))
            with pytest.raises(ValueError, match="another authenticated"):
                await manager.close("fixture-session")
            assert calls == []
        finally:
            execution_session.reset(other)
        await manager.close("fixture-session")
        assert calls == ["close"]
    finally:
        execution_session.reset(token)


@pytest.mark.anyio
async def test_cdp_method_response_must_match_request_id_and_result_shape():
    effects = []

    class Input:
        def write(self, data):
            effects.append(json.loads(data[:-1]))

        async def drain(self):
            pass

        def close(self):
            effects.append("closed")

    class Output:
        async def readuntil(self, _):
            return b'{"id":1,"error":{"message":"untrusted provider error"}}\0'

    class Process:
        returncode = None
        stdin, stdout = Input(), Output()

        async def wait(self):
            self.returncode = 0

    class Proxy:
        async def close(self):
            pass

    channel = CDPChannel(Process(), Proxy(), ExitStack())
    with pytest.raises(ValueError, match="Browser rejected"):
        await channel.call("Page.enable")
    assert effects[0]["method"] == "Page.enable" and effects[1] == "closed"


@pytest.mark.anyio
@pytest.mark.parametrize("response_id", [True, 2, "1", None])
async def test_cdp_rejects_wrong_or_coerced_response_identifiers(response_id):
    effects = []

    class Input:
        def write(self, _):
            pass

        async def drain(self):
            pass

        def close(self):
            effects.append("closed")

    class Output:
        async def readuntil(self, _):
            return json.dumps({"id": response_id, "result": {}}).encode() + b"\0"

    class Process:
        returncode = None
        stdin, stdout = Input(), Output()

        async def wait(self):
            self.returncode = 0

    class Proxy:
        async def close(self):
            effects.append("proxy closed")

    channel = CDPChannel(Process(), Proxy(), ExitStack())
    with pytest.raises(ValueError, match="pending command"):
        await channel.call("Page.enable")
    assert effects == ["closed", "proxy closed"]
