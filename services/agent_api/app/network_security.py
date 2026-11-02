"""Pinned-destination forward proxy for browser and MCP network clients."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

Resolver = Callable[[str, int], Awaitable[list[str]]]
Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class NetworkDestinationPolicy:
    def __init__(
        self,
        allowed_domains: tuple[str, ...],
        *,
        exact_hosts: bool = False,
        allow_private_addresses: bool = False,
        allowed_ports: set[int] | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.allowed_domains = tuple(self._normalize_host(host) for host in allowed_domains)
        self.exact_hosts = exact_hosts
        self.allow_private_addresses = allow_private_addresses
        self.allowed_ports = allowed_ports
        self.resolver = resolver or self._resolve

    @staticmethod
    def _normalize_host(host: str) -> str:
        if "%" in host:
            raise ValueError("Scoped IP addresses are not allowed.")
        try:
            return ipaddress.ip_address(host).compressed.lower()
        except ValueError:
            return host.rstrip(".").encode("idna").decode("ascii").lower()

    def _check_host(self, host: str) -> str:
        normalized = self._normalize_host(host)
        allowed = any(
            normalized == domain
            or (not self.exact_hosts and normalized.endswith(f".{domain}"))
            for domain in self.allowed_domains
        )
        if not allowed:
            raise ValueError("Network destination is outside the configured allowlist.")
        return normalized

    async def resolve(self, host: str, port: int, scheme: str) -> list[str]:
        if scheme not in {"http", "https"} or not 1 <= port <= 65535:
            raise ValueError("Network destination scheme or port is not allowed.")
        if self.allowed_ports is not None and port not in self.allowed_ports:
            raise ValueError("Network destination port is not allowed.")
        normalized = self._check_host(host)
        try:
            literal = ipaddress.ip_address(normalized)
            addresses = [literal.compressed]
        except ValueError:
            addresses = await asyncio.wait_for(self.resolver(normalized, port), timeout=2)
        if not addresses:
            raise ValueError("Network destination returned no addresses.")
        normalized_addresses = {ipaddress.ip_address(item).compressed for item in addresses}
        if not self.allow_private_addresses and any(
            self._is_prohibited(ipaddress.ip_address(item)) for item in normalized_addresses
        ):
            raise ValueError("Network destination resolved to a prohibited address.")
        return sorted(normalized_addresses)

    @staticmethod
    def _is_prohibited(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped is not None or address.sixtofour is not None or address.teredo is not None:
                return True
            nat64_prefix = ipaddress.IPv6Network("64:ff9b::/96")
            if address in nat64_prefix:
                embedded_ipv4 = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
                if NetworkDestinationPolicy._is_prohibited(embedded_ipv4):
                    return True
        return (
            not address.is_global
            or address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or getattr(address, "ipv4_mapped", None) is not None
        )

    @staticmethod
    async def _resolve(host: str, port: int) -> list[str]:
        records = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        return list({record[4][0].split("%")[0] for record in records})


class PinnedHTTPProxy:
    """HTTP forward proxy which validates and dials the same numeric destination."""

    def __init__(
        self,
        policy: NetworkDestinationPolicy,
        *,
        connector: Connector | None = None,
    ) -> None:
        self.policy = policy
        self.connector = connector or self._connect_literal
        self._server: asyncio.Server | None = None
        self._semaphore = asyncio.Semaphore(32)
        self.connection_count = 0

    async def start(self) -> str:
        if self._server is not None:
            raise RuntimeError("Network proxy is already running.")
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=65_536)
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _connect(self, host: str, port: int, scheme: str):
        addresses = await self.policy.resolve(host, port, scheme)
        last_error: OSError | None = None
        for address in addresses:
            try:
                connection = await asyncio.wait_for(self.connector(address, port), timeout=5)
                self.connection_count += 1
                return connection
            except OSError as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise ValueError("Network destination could not be connected safely.")

    async def _handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        async with self._semaphore:
            upstream_writer: asyncio.StreamWriter | None = None
            try:
                header = await asyncio.wait_for(client_reader.readuntil(b"\r\n\r\n"), timeout=5)
                if len(header) > 65_536:
                    raise ValueError("Proxy request headers exceeded the allowed size.")
                header_block = header[:-4]
                incoming_lines = header_block.split(b"\r\n")
                parts = incoming_lines[0].decode("ascii").split(" ", maxsplit=2)
                if len(parts) != 3:
                    raise ValueError("Proxy request is invalid.")
                method, target, version = parts
                if method.upper() == "CONNECT":
                    parsed = urlsplit(f"//{target}")
                    host = parsed.hostname
                    port = parsed.port
                    if not host or port is None or parsed.username or parsed.password:
                        raise ValueError("Proxy destination is invalid.")
                    upstream_reader, upstream_writer = await self._connect(host, port, "https")
                    client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await client_writer.drain()
                    await self._pipe_both(client_reader, client_writer, upstream_reader, upstream_writer)
                    return

                parsed = urlsplit(target)
                host = parsed.hostname
                request_port = parsed.port
                if request_port is not None and not 1 <= request_port <= 65535:
                    raise ValueError("Proxy destination port is invalid.")
                port = request_port or (443 if parsed.scheme == "https" else 80)
                if not method or parsed.scheme != "http" or not host or parsed.username or parsed.password:
                    raise ValueError("Proxy request destination is invalid.")
                upstream_reader, upstream_writer = await self._connect(host, port, "http")
                path = parsed.path or "/"
                if parsed.query:
                    path += f"?{parsed.query}"
                outgoing_lines = [f"{method} {path} {version}".encode("ascii")]
                for line in incoming_lines[1:]:
                    if not line or line.lower().startswith((b"proxy-authorization:", b"proxy-connection:")):
                        continue
                    if line.lower().startswith(b"connection:"):
                        continue
                    outgoing_lines.append(line)
                outgoing_lines.append(b"Connection: close")
                upstream_writer.write(b"\r\n".join(outgoing_lines) + b"\r\n\r\n")
                await upstream_writer.drain()
                await self._pipe_both(client_reader, client_writer, upstream_reader, upstream_writer)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, OSError, UnicodeError):
                if not client_writer.is_closing():
                    client_writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
                    try:
                        await client_writer.drain()
                    except OSError:
                        pass
            finally:
                if upstream_writer is not None:
                    upstream_writer.close()
                    try:
                        await upstream_writer.wait_closed()
                    except OSError:
                        pass
                client_writer.close()
                try:
                    await client_writer.wait_closed()
                except OSError:
                    pass

    async def _pipe_both(self, client_reader, client_writer, upstream_reader, upstream_writer) -> None:
        async def pipe(reader, writer):
            while chunk := await reader.read(65_536):
                writer.write(chunk)
                await writer.drain()

        tasks = {
            asyncio.create_task(pipe(client_reader, upstream_writer)),
            asyncio.create_task(pipe(upstream_reader, client_writer)),
        }
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    async def _connect_literal(address: str, port: int):
        destination = ipaddress.ip_address(address)
        family = socket.AF_INET6 if destination.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            peer = (address, port, 0, 0) if destination.version == 6 else (address, port)
            await asyncio.wait_for(asyncio.get_running_loop().sock_connect(sock, peer), timeout=5)
            return await asyncio.open_connection(sock=sock)
        except BaseException:
            sock.close()
            raise
