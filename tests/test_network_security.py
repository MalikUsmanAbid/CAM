import asyncio

import pytest

from services.agent_api.app.browser import BrowserManager, BrowserNetworkPolicy, OpenInput
from services.agent_api.app.network_security import NetworkDestinationPolicy, PinnedHTTPProxy


async def _answers(*addresses):
    return list(addresses)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.2",
        "224.0.0.1",
        "192.0.2.2",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "64:ff9b::7f00:1",
        "2002:7f00:1::1",
        "2001:0:7f00:1::",
    ],
)
async def test_network_policy_rejects_prohibited_ipv4_and_ipv6(address):
    policy = NetworkDestinationPolicy(
        ("fixture.example",), resolver=lambda _host, _port: _answers(address)
    )
    with pytest.raises(ValueError):
        await policy.resolve("fixture.example", 443, "https")


@pytest.mark.anyio
async def test_mixed_public_and_private_dns_answers_fail_closed():
    async def resolve(_host, _port):
        return ["93.184.216.34", "10.1.2.3"]

    policy = NetworkDestinationPolicy(("fixture.example",), resolver=resolve)
    with pytest.raises(ValueError, match="prohibited"):
        await policy.resolve("fixture.example", 443, "https")


@pytest.mark.anyio
async def test_connection_time_dns_rebinding_is_revalidated_and_never_dialed():
    answers = iter((["93.184.216.34"], ["127.0.0.1"]))
    connected = []

    async def resolve(_host, _port):
        return next(answers)

    async def connect(address, port):
        connected.append((address, port))
        return object(), object()

    policy = NetworkDestinationPolicy(("fixture.example",), resolver=resolve)
    proxy = PinnedHTTPProxy(policy, connector=connect)
    await proxy._connect("fixture.example", 443, "https")
    with pytest.raises(ValueError, match="prohibited"):
        await proxy._connect("fixture.example", 443, "https")
    assert connected == [("93.184.216.34", 443)]


@pytest.mark.anyio
async def test_proxy_rejects_malicious_mcp_dns_answer_at_connect_time():
    async def resolve(_host, _port):
        return ["169.254.169.254"]

    policy = NetworkDestinationPolicy(
        ("mcp.example",), exact_hosts=True, resolver=resolve
    )
    proxy = PinnedHTTPProxy(policy)
    proxy_url = await proxy.start()
    port = int(proxy_url.rsplit(":", 1)[1])
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"CONNECT mcp.example:443 HTTP/1.1\r\nHost: mcp.example\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.readline(), 2)
    assert b"403" in response
    writer.close()
    await writer.wait_closed()
    await proxy.close()


@pytest.mark.anyio
async def test_proxy_blocks_redirect_connect_to_private_ip():
    policy = NetworkDestinationPolicy(("fixture.example",))
    proxy = PinnedHTTPProxy(policy)
    proxy_url = await proxy.start()
    port = int(proxy_url.rsplit(":", 1)[1])
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.readline(), 2)
    assert b"403" in response
    writer.close()
    await writer.wait_closed()
    await proxy.close()


@pytest.mark.anyio
async def test_mcp_destination_change_between_validation_and_connect_is_blocked():
    answers = iter((["93.184.216.34"], ["10.0.0.5"]))
    connected = []

    async def resolve(_host, _port):
        return next(answers)

    async def connect(address, port):
        connected.append((address, port))
        return object(), object()

    policy = NetworkDestinationPolicy(
        ("mcp.example",), exact_hosts=True, resolver=resolve
    )
    proxy = PinnedHTTPProxy(policy, connector=connect)
    await proxy._connect("mcp.example", 443, "https")
    with pytest.raises(ValueError, match="prohibited"):
        await proxy._connect("mcp.example", 443, "https")
    assert connected == [("93.184.216.34", 443)]


@pytest.mark.anyio
async def test_browser_rejects_redirect_to_private_ip():
    async def resolve(_host, _port):
        return ["93.184.216.34"]

    class RedirectingBrowser:
        async def open_url(self, _url):
            return {"url": "http://127.0.0.1/secret"}

        async def close(self):
            pass

        async def inspect_page(self):
            return {"text": ""}

        async def act(self, _action, _target, _value=""):
            return {}

    manager = BrowserManager(BrowserNetworkPolicy(resolver=resolve))
    manager.create("s", RedirectingBrowser(), ["fixture.example"])
    with pytest.raises(ValueError):
        await manager.open(OpenInput(session_id="s", url="https://fixture.example/"))
