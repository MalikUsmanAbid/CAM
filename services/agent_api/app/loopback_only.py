"""Restrict the unauthenticated local API to loopback peers and local origins."""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit


class LoopbackOnlyMiddleware:
    def __init__(self, app, allowed_origins: tuple[str, ...]) -> None:
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        client = scope.get("client")
        origin = headers.get(b"origin")
        host_header = headers.get(b"host", b"").decode("latin-1")
        try:
            peer = (
                ipaddress.ip_address(client[0].split("%")[0])
                if client and client[0] != "testclient"
                else None
            )
            peer_loopback = bool(
                client
                and (
                    client[0] == "testclient"
                    or peer
                    and (
                        peer.is_loopback
                        or getattr(peer, "ipv4_mapped", None)
                        and peer.ipv4_mapped.is_loopback
                    )
                )
            )
            host = urlsplit(f"//{host_header}").hostname
            host_loopback = bool(
                host
                and (
                    host.lower() == "testserver"
                    or host.lower() == "localhost"
                    or ipaddress.ip_address(host).is_loopback
                )
            )
        except ValueError:
            host_loopback = False
            peer_loopback = False
        origin_value = origin.decode("latin-1") if origin else None
        allowed_origins = self.allowed_origins
        root = scope.get("app")
        if root is not None and getattr(root.state, "desktop_bootstrap", None) is not None:
            from services.agent_api.app.desktop_origins import DESKTOP_ORIGINS
            allowed_origins = allowed_origins | frozenset(DESKTOP_ORIGINS)
        if (
            not peer_loopback
            or not host_loopback
            or (origin_value is not None and origin_value not in allowed_origins)
        ):
            body = json.dumps({"detail": "This API is available only to local clients."}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
