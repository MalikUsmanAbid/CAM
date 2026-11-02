"""Bounded MCP bridge. Transport calls are reachable only through registered tools."""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Protocol
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model

from services.agent_api.app.capabilities import (
    Capability,
    CapabilityRegistry,
    CapabilitySource,
    CapabilityStatus,
)
from services.agent_api.app.network_security import NetworkDestinationPolicy, PinnedHTTPProxy
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.tools import Tool, ToolRegistry

MAX_MCP_TOOLS = 12
MAX_MCP_SCHEMA_BYTES = 8_000
MAX_MCP_RESPONSE_BYTES = 16_000


async def validate_streamable_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("MCP endpoint is invalid.")
    policy = NetworkDestinationPolicy(
        (parsed.hostname,),
        exact_hosts=True,
        allowed_ports={parsed.port or 443},
    )
    await policy.resolve(parsed.hostname, parsed.port or 443, parsed.scheme)


class MCPTransport(Protocol):
    async def discover(self) -> list["MCPToolDefinition"]: ...

    async def invoke(self, tool_id: str, arguments: dict[str, object]) -> dict[str, object]: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class MCPToolDefinition:
    tool_id: str
    description: str
    risk: RiskLevel
    input_model: type[BaseModel]


_omitted_properties: WeakKeyDictionary[type[BaseModel], frozenset[str]] = WeakKeyDictionary()


class MCPInput(BaseModel):
    """Keep optional properties without defaults absent across persistence and transport."""

    def model_dump(self, *args, **kwargs):
        result = super().model_dump(*args, **kwargs)
        for name in _omitted_properties.get(type(self), frozenset()) - self.model_fields_set:
            result.pop(name, None)
        return result


def _input_model_from_schema(name: str, schema: dict[str, object]) -> type[BaseModel]:
    annotations = {"title", "description", "examples", "$schema"}
    if set(schema) - annotations - {"type", "properties", "required", "additionalProperties"}:
        raise ValueError("MCP input schema contains unsupported constraints.")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("MCP input schema must be a closed object.")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if (
        not isinstance(properties, dict) or len(properties) > 20
        or not isinstance(required, list) or any(not isinstance(item, str) for item in required)
        or len(set(required)) != len(required)
    ):
        raise ValueError("MCP input schema is invalid.")
    fields: dict[str, tuple[object, object]] = {}
    type_map = {"string": str, "integer": int, "number": float, "boolean": bool}
    for field_name, metadata in properties.items():
        if not isinstance(field_name, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,63}", field_name):
            raise ValueError("MCP input field name is invalid.")
        if hasattr(MCPInput, field_name):
            raise ValueError("MCP input field name is reserved by the supported validator.")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("type"), str) or metadata["type"] not in type_map:
            raise ValueError("MCP input field type is unsupported.")
        field_type = type_map[metadata["type"]]
        constraints = {"maxLength", "minLength"} if field_type is str else (
            {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
            if field_type in {int, float} else set()
        )
        if set(metadata) - annotations - {"type", "default"} - constraints:
            raise ValueError("MCP input field contains unsupported constraints.")
        field_kwargs: dict[str, object] = {"strict": True}
        if field_type is str:
            maximum = metadata.get("maxLength", 2_000)
            minimum = metadata.get("minLength", 0)
            if type(maximum) is not int or type(minimum) is not int or not 0 <= minimum <= maximum or minimum > 2_000:
                raise ValueError("MCP string bounds are invalid.")
            field_kwargs.update(max_length=min(maximum, 2_000), min_length=minimum)
        elif field_type in {int, float}:
            for bound, constraint in {"minimum": "ge", "maximum": "le", "exclusiveMinimum": "gt", "exclusiveMaximum": "lt", "multipleOf": "multiple_of"}.items():
                if bound in metadata:
                    value = metadata[bound]
                    if type(value) not in {int, float} or (type(value) is float and not math.isfinite(value)) or (bound == "multipleOf" and value <= 0):
                        raise ValueError("MCP numeric bounds are invalid.")
                    field_kwargs[constraint] = value
            field_kwargs["allow_inf_nan"] = False
            lower = [(metadata[key], key == "exclusiveMinimum") for key in ("minimum", "exclusiveMinimum") if key in metadata]
            upper = [(metadata[key], key == "exclusiveMaximum") for key in ("maximum", "exclusiveMaximum") if key in metadata]
            if any(low > high or (low == high and (low_exclusive or high_exclusive)) for low, low_exclusive in lower for high, high_exclusive in upper):
                raise ValueError("MCP numeric bounds are contradictory.")
        default: object = ... if field_name in required else metadata.get("default", None)
        fields[field_name] = (field_type, Field(default, **field_kwargs))
    if set(required) - set(properties):
        raise ValueError("MCP required field is missing from its schema.")
    model = create_model(
        f"MCP_{re.sub(r'[^a-zA-Z0-9_]', '_', name)}_Input",
        __config__=ConfigDict(extra="forbid"),
        __base__=MCPInput,
        **fields,
    )
    _omitted_properties[model] = frozenset(name for name, metadata in properties.items() if name not in required and "default" not in metadata)
    for field_name, metadata in properties.items():
        if "default" in metadata:
            TypeAdapter(Annotated[type_map[metadata["type"]], *model.model_fields[field_name].metadata]).validate_python(metadata["default"])
    return model


class StreamableHTTPMCPTransport:
    """Official MCP Python SDK client over explicitly configured Streamable HTTP."""

    def __init__(self, client, transport_context, http_client, proxy: PinnedHTTPProxy) -> None:
        self.client = client
        self.transport_context = transport_context
        self.http_client = http_client
        self._proxy = proxy
        self.definitions: dict[str, MCPToolDefinition] = {}
        self._closed = False

    @classmethod
    async def open(cls, endpoint: str, secret: str | None = None) -> "StreamableHTTPMCPTransport":
        try:
            import httpx2
            from mcp import Client
            from mcp.client.streamable_http import streamable_http_client
        except ImportError as exc:
            raise RuntimeError("Install the optional MCP SDK to enable MCP servers.") from exc
        await validate_streamable_endpoint(endpoint)
        parsed = urlparse(endpoint)
        policy = NetworkDestinationPolicy(
            (parsed.hostname or "",),
            exact_hosts=True,
            allowed_ports={parsed.port or 443},
        )
        proxy = PinnedHTTPProxy(policy)
        proxy_url = await proxy.start()
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        http_client = httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(15, connect=5, read=15),
            follow_redirects=False,
            proxy=proxy_url,
            trust_env=False,
        )
        transport_context = streamable_http_client(endpoint, http_client=http_client)
        try:
            streams = await transport_context.__aenter__()
            client = Client(streams)
            await client.__aenter__()
            return cls(client, transport_context, http_client, proxy)
        except Exception:
            await http_client.aclose()
            await proxy.close()
            raise

    async def discover(self) -> list[MCPToolDefinition]:
        result = await self.client.list_tools()
        tools = list(result.tools)
        cursor = result.next_cursor
        cursors = {cursor} if cursor else set()
        while cursor:
            if len(tools) > MAX_MCP_TOOLS:
                raise ValueError("MCP discovery exceeded the tool limit.")
            result = await self.client.list_tools(cursor=cursor)
            tools.extend(result.tools)
            cursor = result.next_cursor
            if cursor and cursor in cursors:
                raise ValueError("MCP discovery returned a repeated page cursor.")
            if cursor:
                cursors.add(cursor)
        definitions: list[MCPToolDefinition] = []
        for item in tools:
            schema = item.input_schema
            model = _input_model_from_schema(item.name, schema)
            definition = MCPToolDefinition(
                tool_id=item.name,
                description=(item.description or item.name)[:500],
                risk=RiskLevel.HIGH,
                input_model=model,
            )
            definitions.append(definition)
            self.definitions[item.name] = definition
        return definitions

    async def invoke(self, tool_id: str, arguments: dict[str, object]) -> dict[str, object]:
        result = await self.client.call_tool(tool_id, arguments)
        if result.is_error:
            raise ValueError("MCP tool reported a safe execution failure.")
        structured = result.structured_content
        if isinstance(structured, dict):
            value = structured
        else:
            value = {"content": [getattr(block, "text", "[non-text MCP content]") for block in result.content]}
        if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_MCP_RESPONSE_BYTES:
            raise ValueError("MCP response exceeded the allowed size.")
        return value

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.client.__aexit__(None, None, None)
        finally:
            try:
                await self.transport_context.__aexit__(None, None, None)
            finally:
                try:
                    await self.http_client.aclose()
                finally:
                    await self._proxy.close()


class MCPManager:
    """Registers MCP tools into ToolRegistry; it never offers a direct public invoke API."""

    def __init__(self, tools: ToolRegistry, capabilities: CapabilityRegistry) -> None:
        self.tools, self.capabilities = tools, capabilities
        self._transports: dict[str, MCPTransport] = {}
        self._registered: dict[str, set[str]] = {}

    def is_connected(self, server_id: str) -> bool:
        return server_id in self._transports

    async def connect(self, server_id: str, transport: MCPTransport, timeout_seconds: float = 5) -> list[str]:
        if server_id in self._transports or not re.fullmatch(r"[a-z][a-z0-9_-]{0,99}", server_id):
            raise ValueError("MCP server identifier is invalid or already connected.")
        definitions = await asyncio.wait_for(transport.discover(), timeout=timeout_seconds)
        if len(definitions) > MAX_MCP_TOOLS:
            raise ValueError("MCP discovery exceeded the tool limit.")
        prepared: list[tuple[str, str, MCPToolDefinition, RiskLevel]] = []
        existing_tools = {tool.name for tool in self.tools.list()}
        existing_capabilities = {item.capability_id for item in self.capabilities.list()}
        for definition in definitions:
            name = f"mcp_{server_id}_{definition.tool_id}".replace("-", "_")
            if not name.replace("_", "").isalnum() or len(name) > 100:
                raise ValueError("MCP tool identifier is invalid.")
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,99}", definition.tool_id):
                raise ValueError("MCP tool identifier is invalid.")
            schema = json.dumps(definition.input_model.model_json_schema()).encode()
            capability_id = f"mcp.{server_id}.{definition.tool_id}"
            if (
                len(definition.description) > 500
                or len(schema) > MAX_MCP_SCHEMA_BYTES
                or name in existing_tools
                or capability_id in existing_capabilities
                or any(item[0] == name or item[1] == capability_id for item in prepared)
            ):
                raise ValueError("MCP tool metadata is invalid.")
            # A remote server cannot lower the local policy by declaring a tool low risk.
            risk = RiskLevel.HIGH
            prepared.append((name, capability_id, definition, risk))

        names: list[str] = []
        for name, capability_id, definition, risk in prepared:
            self.tools.register(
                Tool(
                    name,
                    definition.description[:500],
                    definition.input_model,
                    risk,
                    timeout_seconds,
                    self._handler(server_id, definition.tool_id, definition.input_model),
                )
            )
            self.capabilities.register(
                Capability(
                    capability_id=capability_id,
                    name=name,
                    description=definition.description[:500],
                    category="mcp",
                    source_type=CapabilitySource.MCP,
                    version="1.0.0",
                    risk_level=risk,
                    required_permission=str(risk),
                    available_tools=[name],
                    status=CapabilityStatus.AVAILABLE,
                )
            )
            names.append(name)
        self._transports[server_id] = transport
        self._registered[server_id] = set(names)
        return names

    async def disconnect(self, server_id: str) -> None:
        transport = self._transports.pop(server_id, None)
        for name in self._registered.pop(server_id, set()):
            self.tools.unregister(name)
            for capability in self.capabilities.list():
                if name in capability.available_tools and capability.source_type is CapabilitySource.MCP:
                    self.capabilities.unregister(capability.capability_id)
        if transport:
            await transport.close()

    def _handler(self, server_id: str, tool_id: str, input_model: type[BaseModel]) -> Callable[[BaseModel], Awaitable[dict[str, object]]]:
        async def invoke(value: BaseModel) -> dict[str, object]:
            transport = self._transports.get(server_id)
            if transport is None:
                raise ValueError("MCP server is unavailable.")
            # Validate the actual outgoing dictionary, even if a model was mutated.
            arguments = input_model.model_validate(value.model_dump()).model_dump()
            result = await transport.invoke(tool_id, arguments)
            encoded = json.dumps(result, ensure_ascii=False).encode()
            if len(encoded) > MAX_MCP_RESPONSE_BYTES:
                raise ValueError("MCP response exceeded the allowed size.")
            return {
                "mcp_server": server_id,
                "mcp_tool": tool_id,
                "result": result,
                "trust": "untrusted_data",
            }
        return invoke
