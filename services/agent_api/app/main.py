"""FastAPI entry point for the Phase 1 local JARVIS chat service."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.agent_api.app.action_policy import PolicyUpdate
from services.agent_api.app.backend_boundary import backend_database_lock
from services.agent_api.app.backup_service import BackupService, register_backup_tool
from services.agent_api.app.backup_service import router as backup_router
from services.agent_api.app.browser import (
    BrowserManager,
    BrowserNetworkIsolationUnavailable,
    PlaywrightBrowserAdapter,
    register_browser_tools,
)
from services.agent_api.app.capabilities import (
    CapabilityRegistry,
    CapabilityResolver,
    SkillRegistry,
)
from services.agent_api.app.computer import (
    ComputerManager,
    register_computer_tools,
)
from services.agent_api.app.config import Settings, get_settings
from services.agent_api.app.context_builder import ContextBuilder
from services.agent_api.app.credentials import CredentialVault
from services.agent_api.app.logging_config import configure_logging
from services.agent_api.app.loopback_only import LoopbackOnlyMiddleware
from services.agent_api.app.mcp import (
    MCPManager,
    StreamableHTTPMCPTransport,
    validate_streamable_endpoint,
)
from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.model_catalog import router as model_catalog_router
from services.agent_api.app.model_planner import ModelPlanner
from services.agent_api.app.oauth_accounts import OAuthAccounts, OAuthCallbackPrivacy
from services.agent_api.app.oauth_accounts import router as oauth_router
from services.agent_api.app.orchestrator import AgentOrchestrator
from services.agent_api.app.persistence import Phase5Store
from services.agent_api.app.phase5_foundations import (
    ActionRegistry,
    MCPRegistry,
    Scheduler,
    WorkflowRegistry,
)
from services.agent_api.app.planner import DeterministicPlanner
from services.agent_api.app.project_runtime import (
    project_scope,
    runtime_for,
    task_project,
)
from services.agent_api.app.providers import (
    BrainProvider,
    ModelProvider,
    ProviderUnavailableError,
    create_brain_router,
    create_provider,
)
from services.agent_api.app.schemas import (
    AIStatusResponse,
    BrowserSessionCreateRequest,
    ChatRequest,
    ChatResponse,
    ComputerSessionCreateRequest,
    HealthResponse,
    MCPServerCreateRequest,
    MemoryRequest,
    ScheduleCreateRequest,
    TaskCreateRequest,
    ToolExecutionRequest,
    WorkflowCreateRequest,
    WorkflowTransitionRequest,
)
from services.agent_api.app.secure_store import SecureStore
from services.agent_api.app.session_security import SessionManager, SessionMiddleware
from services.agent_api.app.tasks import TaskLimits
from services.agent_api.app.tools import ToolExecutor, _approval_public, build_registry
from services.agent_api.app.usage_policy import CloudPolicy, UsagePolicy
from services.agent_api.app.voice_capture import VoiceCapture, register_voice_tool
from services.agent_api.app.voice_capture import router as voice_router
from services.agent_api.app.workspace_api import router as workspace_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = getattr(app.state, "desktop_settings", None)
    if settings is None:
        settings = get_settings()
    with backend_database_lock(settings.database_path):
        async with _lifespan(app, settings):
            yield


@asynccontextmanager
async def _lifespan(app: FastAPI, settings: Settings):
    configure_logging()
    app.state.settings = settings
    app.state.foundation = SecureStore(settings.database_path)
    app.state.foundation.migrate(allow_existing=os.getenv("JARVIS_ALLOW_DATABASE_MIGRATION") == "1")
    app.state.sessions = SessionManager()
    # A local bootstrap secret is shown once on the controlling terminal, never logged or persisted.
    desktop_bootstrap = getattr(app.state, "desktop_bootstrap", None)
    if desktop_bootstrap is None:
        print("JARVIS pairing code (valid for 10 minutes): " + app.state.sessions.pairing_code)
    app.state.secret_resolver = CredentialVault(settings.database_path.parent / "credentials")
    app.state.provider = create_provider(settings, app.state.secret_resolver)
    app.state.brain_provider = create_brain_router(settings, app.state.secret_resolver)
    app.state.usage = UsagePolicy(settings.database_path)
    app.state.inference_gate = asyncio.Semaphore(1)
    providers = [app.state.provider, *app.state.brain_provider.providers.values()]
    for provider in providers:
        transport = getattr(provider, "_transport", None)
        if transport is not None:
            transport.authorize_dispatch = app.state.usage.authorize
            transport.dispatch_gate = app.state.inference_gate
    memory = MemoryRepository(settings.database_path)
    memory.initialize()
    memory.recover_interrupted_tasks()
    app.state.phase5_store = Phase5Store(settings.database_path)
    app.state.phase5_store.initialize()
    for configured_server in app.state.phase5_store.list_mcp_servers():
        if configured_server["enabled"]:
            app.state.phase5_store.update_mcp_server(
                str(configured_server["server_id"]), status="needs_reconnect"
            )
    app.state.memory = memory
    app.state.project_runtimes = {}
    app.state.executor = ToolExecutor(build_registry(settings.safe_workspace), memory)
    from services.agent_api.app.account_actions import AccountActions, register_account_tools
    app.state.account_actions = AccountActions(settings.database_path, app.state.secret_resolver)
    app.state.oauth_accounts = OAuthAccounts(app)
    register_account_tools(app.state.executor.registry, app.state.account_actions)
    app.state.voice_capture = VoiceCapture(os.getenv("JARVIS_STT_ENDPOINT") if os.getenv("JARVIS_STT_ENABLED") == "1" else None, app.state.inference_gate)
    register_voice_tool(app.state.executor.registry, app.state.voice_capture)
    app.state.backup_service = BackupService(settings.database_path)
    register_backup_tool(app.state.executor.registry, app.state.backup_service)
    from services.agent_api.app.project_creation import register_project_creation
    register_project_creation(app.state.executor.registry, app.state.foundation)
    from services.agent_api.app.isolated_browser import NativeBrowserRuntime
    browser_path, broker_path = os.getenv('JARVIS_BROWSER_EXECUTABLE'), os.getenv('JARVIS_BROWSER_BROKER')
    browser_runtime = NativeBrowserRuntime(broker_path, browser_path) if browser_path and broker_path else None
    app.state.browser_manager = BrowserManager(runtime=browser_runtime)
    register_browser_tools(app.state.executor.registry, app.state.browser_manager)
    app.state.computer_manager = ComputerManager()
    # Thread-backed UIA cannot safely stop a blocking cross-process call or
    # establish exact process/window identity. Production stays unavailable
    # until the owned-worker implementation passes Windows acceptance.
    register_computer_tools(app.state.executor.registry, app.state.computer_manager, enabled=False)
    from services.agent_api.app.owned_uia import register_owned_uia_tools
    register_owned_uia_tools(app.state.executor.registry, app.state.computer_manager, enabled=False, memory=memory)
    app.state.context_builder = ContextBuilder(memory, app.state.executor.registry)
    app.state.capabilities = CapabilityRegistry.from_tools(app.state.executor.registry)
    app.state.skills = SkillRegistry(app.state.capabilities)
    app.state.capability_resolver = CapabilityResolver(app.state.capabilities, app.state.skills)
    app.state.mcp = MCPRegistry()
    app.state.mcp_manager = MCPManager(app.state.executor.registry, app.state.capabilities)
    app.state.controlled_actions = ActionRegistry()
    app.state.workflows = WorkflowRegistry()
    app.state.scheduler = Scheduler()
    app.state.model_planner = ModelPlanner(
        app.state.context_builder,
        app.state.brain_provider,
        app.state.executor.registry,
        fallback=DeterministicPlanner(),
        timeout_seconds=settings.ai_timeout_seconds,
    )
    app.state.orchestrator = AgentOrchestrator(
        app.state.executor, memory, app.state.model_planner, app.state.capability_resolver
    )
    from services.agent_api.app.agent_workspace import AgentWorkspace
    from services.agent_api.app.workspace_records import WorkspaceRecords
    app.state.workspace_records = WorkspaceRecords(settings.database_path)
    app.state.agent_workspace = AgentWorkspace(app)
    app.state.agent_workspace.recover()
    from services.agent_api.app.agent_batches import AgentBatches
    app.state.agent_batches = AgentBatches(app)
    app.state.agent_batches.recover()
    app.state.workspace_scheduler_status = "starting"

    async def workspace_schedule_loop():
        while True:
            try:
                occurrences = app.state.workspace_records.materialize_due()
                app.state.voice_capture.purge()
                app.state.backup_service.purge()
                app.state.workspace_scheduler_status = "running"
                for project_id, identifier in occurrences:
                    memory.record_event("scheduler", "reminder_due", "A durable reminder is ready in the notification inbox.", project_id=project_id or None, task_id=identifier)
            except Exception:
                app.state.workspace_scheduler_status = "unavailable"
                logger.warning("workspace_scheduler_tick_failed")
            await asyncio.sleep(5)

    workspace_schedule_worker = asyncio.create_task(workspace_schedule_loop())
    logger.info(
        "jarvis_backend_started provider=%s model=%s",
        settings.ai_provider,
        settings.ai_model,
    )
    async def pairing_rotation():
        while True:
            await asyncio.sleep(540)
            print("JARVIS pairing code (valid for 10 minutes): " + app.state.sessions.rotate_pairing())

    pairing_worker = asyncio.create_task(pairing_rotation()) if desktop_bootstrap is None else None
    try:
        if desktop_bootstrap is not None:
            desktop_bootstrap.publish(app.state.sessions, port=settings.backend_port)
        yield
    finally:
        app.state.backup_service.clear()
        workspace_schedule_worker.cancel()
        await asyncio.gather(workspace_schedule_worker, return_exceptions=True)
        active_agents = list(app.state.agent_workspace.active.values())
        for agent in active_agents:
            agent.cancel()
        if active_agents:
            await asyncio.gather(*active_agents, return_exceptions=True)
        if pairing_worker is not None:
            pairing_worker.cancel()
            await asyncio.gather(pairing_worker, return_exceptions=True)
        for configured_server in app.state.phase5_store.list_mcp_servers():
            await app.state.mcp_manager.disconnect(str(configured_server["server_id"]))
        await app.state.browser_manager.close_all()
        await app.state.computer_manager.close_all()
        logger.info("jarvis_backend_stopped")


app = FastAPI(title="USMAN JARVIS API", version="0.1.0", lifespan=lifespan)
app.include_router(workspace_router)
app.include_router(voice_router)
app.include_router(oauth_router)
app.include_router(backup_router)
app.include_router(model_catalog_router)
app.add_middleware(SessionMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "Last-Event-ID"],
)
app.add_middleware(
    LoopbackOnlyMiddleware,
    allowed_origins=("http://127.0.0.1:5173", "http://localhost:5173"),
)
app.add_middleware(OAuthCallbackPrivacy)


@app.exception_handler(KeyError)
async def missing_resource_handler(_: Request, __: KeyError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Resource was not found."})


@app.exception_handler(ValueError)
async def invalid_value_handler(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ProviderUnavailableError)
async def provider_unavailable_handler(_: Request, exc: ProviderUnavailableError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc), "code": "provider_unavailable"},
    )


@app.exception_handler(BrowserNetworkIsolationUnavailable)
async def browser_isolation_unavailable_handler(
    _: Request, exc: BrowserNetworkIsolationUnavailable
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc), "code": "browser_network_isolation_unavailable"},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Invalid chat request.",
            "code": "validation_error",
            "errors": jsonable_encoder([
                {key: value for key, value in error.items() if key in {"type", "loc"}}
                for error in exc.errors()
            ]),
        },
    )


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def _provider(request: Request) -> ModelProvider:
    return request.app.state.provider  # type: ignore[no-any-return]


def _brain_provider(request: Request) -> BrainProvider:
    return request.app.state.brain_provider  # type: ignore[no-any-return]


def _memory(request: Request) -> MemoryRepository:
    return runtime_for(request.app, request.query_params.get("project_id")).memory


def _assert_task_scope(request: Request, task_id: str):
    if task_project(request.app.state.settings.database_path, task_id) != request.query_params.get("project_id"):
        raise KeyError(task_id)
    _memory(request).get_task(task_id)


def _mcp_server_public(server: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in server.items() if key != "secret_reference"} | {
        "approved": bool(server.get("approved")),
        "enabled": bool(server.get("enabled")),
        "credential_reference_configured": bool(server.get("secret_reference"))
    }


@app.get("/api/v1/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = _settings(request)
    return HealthResponse(
        status="ok",
        provider=settings.ai_provider,
        model=settings.ai_model,
        provider_configured=settings.provider_configured,
        authentication_required=True,
    )


class PairingRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)


@app.post("/api/v1/session/pair")
async def pair_session(payload: PairingRequest, request: Request):
    if getattr(request.app.state, "desktop_bootstrap", None) is not None:
        return JSONResponse({"detail": "Desktop sessions are issued through the private shell channel."}, status_code=403)
    token = request.app.state.sessions.pair(payload.code)
    print("JARVIS pairing code (valid for 10 minutes): " + request.app.state.sessions.rotate_pairing())
    return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})


@app.delete("/api/v1/session")
async def end_session(request: Request):
    request.app.state.sessions.revoke(request.state.session_token)
    return {"status": "revoked"}


class ProjectRegistration(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    root: str = Field(min_length=1, max_length=1000)


class CredentialInput(BaseModel):
    secret: str = Field(min_length=1, max_length=16384, repr=False)


@app.get("/api/v1/credentials/status")
async def credential_status(request: Request):
    return {"available": request.app.state.secret_resolver.available, "storage": "windows_user_dpapi"}


@app.post("/api/v1/credentials", status_code=201)
async def create_credential(payload: CredentialInput, request: Request):
    reference = request.app.state.secret_resolver.store(payload.secret)
    return JSONResponse({"reference": reference}, status_code=201, headers={"Cache-Control": "no-store"})


@app.delete("/api/v1/credentials/{reference}")
async def revoke_credential(reference: str, request: Request):
    # Close connected MCP clients before removing their credential.
    for server in request.app.state.phase5_store.list_mcp_servers():
        if server.get("secret_reference") == reference and server["enabled"]:
            await request.app.state.mcp_manager.disconnect(str(server["server_id"]))
            request.app.state.phase5_store.update_mcp_server(str(server["server_id"]), enabled=False, status="disabled")
    request.app.state.secret_resolver.revoke(reference)
    return {"status": "revoked"}


@app.get("/api/v1/projects")
async def list_projects(request: Request):
    return request.app.state.foundation.projects()


@app.get("/api/v1/usage")
async def usage_status(request: Request):
    return request.app.state.usage.status()


@app.get("/api/v1/policy")
async def policy_status(request: Request):
    return request.app.state.executor.policy.snapshot()


@app.put("/api/v1/policy")
async def update_policy(payload: PolicyUpdate, request: Request):
    return request.app.state.executor.policy.update(payload)


@app.put("/api/v1/usage/policy")
async def configure_cloud_policy(payload: CloudPolicy, request: Request):
    request.app.state.usage.configure(payload)
    return request.app.state.usage.status()


@app.post("/api/v1/projects", status_code=201)
async def register_project(payload: ProjectRegistration, request: Request):
    return request.app.state.foundation.register_project(payload.name, payload.root)


@app.get("/api/v1/events")
async def events(request: Request, after: int = 0):
    if not 0 <= after <= 9223372036854775807:
        raise ValueError("Event cursor is outside the supported range.")
    project_id = request.query_params.get("project_id")
    runtime_for(request.app, project_id)
    return request.app.state.foundation.events(after, project_id)


@app.get("/api/v1/events/stream")
async def stream_events(request: Request, after: int = 0):
    project_id = request.query_params.get("project_id")
    runtime_for(request.app, project_id)
    cursor_text = request.headers.get("last-event-id", str(after))
    if not cursor_text.isdecimal() or len(cursor_text) > 18:
        raise ValueError("Invalid event cursor.")
    cursor = int(cursor_text)

    async def replay():
        nonlocal cursor
        while not await request.is_disconnected():
            if request.app.state.sessions.authenticate(request.state.session_token) is None:
                break
            batch = request.app.state.foundation.events(cursor, project_id)
            for event in batch:
                if request.app.state.sessions.authenticate(request.state.session_token) is None:
                    return
                import json

                yield f"id: {event['id']}\nevent: activity\ndata: {json.dumps(event)}\n\n"
                cursor = event["id"]
            if len(batch) < 100:
                yield ": heartbeat\n\n"
                await asyncio.sleep(2)

    return StreamingResponse(replay(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/v1/ai/status", response_model=AIStatusResponse)
async def ai_status(request: Request) -> AIStatusResponse:
    status = _brain_provider(request).status()
    selected = request.app.state.brain_provider._selected()
    transport = getattr(selected, "_transport", None)
    budget_blocked = bool(transport and not request.app.state.usage.available(transport.endpoint, status.model))
    return AIStatusResponse(
        provider=status.provider,
        model=status.model,
        available=status.available and not budget_blocked,
        supported_capabilities=list(status.capabilities),
        availability_reason="Cloud consent, pricing and budgets are required." if budget_blocked else status.availability_reason,
    )


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    settings = _settings(request)
    if request.app.state.executor.policy.snapshot()["emergency_stop"]:
        return JSONResponse({"detail": "Execution emergency stop is active."}, status_code=403)
    project_id = request.query_params.get("project_id")
    if project_id:
        with project_scope(project_id):
            result = await runtime_for(request.app, project_id).executor.execute("studio_project_chat", {"message": payload.message})
        if result["status"] != "success":
            return JSONResponse(result, status_code=409 if result["status"] == "approval_required" else 403)
        return ChatResponse(**{key: result["result"][key] for key in ("message", "provider", "model")})
    logger.info(
        "chat_request provider=%s message_length=%s",
        settings.ai_provider,
        len(payload.message),
    )
    try:
        response = await _provider(request).respond(payload.message)
        return ChatResponse(message=response, provider=settings.ai_provider, model=settings.ai_model)
    except ProviderUnavailableError:
        if settings.ai_provider != "openai_compatible" or hasattr(request.app.state, "desktop_settings"):
            raise
        return ChatResponse(
            message=(
                "The configured AI provider is unavailable. This local fallback did not run a model "
                "or use tools. You can still submit a bounded task, which uses deterministic planning "
                "when no live planner is available."
            ),
            provider="deterministic_fallback",
            model="local",
        )


@app.get("/api/v1/tools")
async def list_tools(request: Request) -> list[dict[str, object]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "risk": tool.risk,
            "enabled": tool.enabled,
        }
        for tool in runtime_for(request.app, request.query_params.get("project_id")).executor.registry.list()
    ]


@app.post("/api/v1/browser/sessions", status_code=201)
async def create_browser_session(
    payload: BrowserSessionCreateRequest, request: Request
) -> dict[str, object]:
    manager: BrowserManager = request.app.state.browser_manager
    if request.query_params.get('project_id'):
        raise HTTPException(400, 'Browser sessions currently require Personal scope.')
    session_id = str(uuid.uuid4())
    if manager.runtime is None:
        adapter = await PlaywrightBrowserAdapter.create(tuple(payload.allowed_domains), manager.network_policy)
    else:
        adapter = await PlaywrightBrowserAdapter.create(tuple(payload.allowed_domains), manager.network_policy, runtime=manager.runtime)
    try:
        session = manager.create(session_id, adapter, payload.allowed_domains)
    except Exception:
        await adapter.close()
        raise
    return {
        "session_id": session.session_id,
        "allowed_domains": list(session.allowed_domains),
        "status": "visible_session_open",
        "max_actions": 12,
    }


@app.delete("/api/v1/browser/sessions/{session_id}", status_code=204)
async def close_browser_session(session_id: str, request: Request) -> None:
    if request.query_params.get('project_id'):
        raise HTTPException(400, 'Browser sessions currently require Personal scope.')
    await request.app.state.browser_manager.close(session_id)


@app.post("/api/v1/computer/sessions", status_code=201)
async def create_computer_session(
    payload: ComputerSessionCreateRequest, request: Request
) -> dict[str, object]:
    raise HTTPException(503, "Computer execution is unavailable pending owned UIA workers, exact window/process identity checks and Windows acceptance. The legacy thread adapter cannot be enabled through this API.")


@app.delete("/api/v1/computer/sessions/{session_id}", status_code=204)
async def close_computer_session(session_id: str, request: Request) -> None:
    await request.app.state.computer_manager.close(session_id)


@app.get("/api/v1/capabilities")
async def list_capabilities(request: Request) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in runtime_for(request.app, request.query_params.get("project_id")).capabilities.list()]


@app.get("/api/v1/capabilities/{capability_id}")
async def get_capability(capability_id: str, request: Request) -> dict[str, object]:
    return runtime_for(request.app, request.query_params.get("project_id")).capabilities.get(capability_id).model_dump(mode="json")


@app.get("/api/v1/skills")
async def list_skills(request: Request) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in runtime_for(request.app, request.query_params.get("project_id")).skills.list()]


@app.get("/api/v1/skills/{skill_id}")
async def get_skill(skill_id: str, request: Request) -> dict[str, object]:
    return runtime_for(request.app, request.query_params.get("project_id")).skills.get(skill_id).model_dump(mode="json")


@app.get("/api/v1/mcp")
async def list_mcp(request: Request) -> list[dict[str, object]]:
    return [_mcp_server_public(item) for item in request.app.state.phase5_store.list_mcp_servers()]


@app.post("/api/v1/mcp/servers", status_code=201)
async def create_mcp_server(
    payload: MCPServerCreateRequest, request: Request
) -> dict[str, object]:
    server = request.app.state.phase5_store.create_mcp_server(
        payload.server_id, payload.name, payload.endpoint, payload.secret_reference
    )
    _memory(request).record_event("mcp_server", "mcp_server_configured", f"MCP server {payload.server_id} configured disabled.")
    return _mcp_server_public(server)


@app.post("/api/v1/mcp/servers/{server_id}/approve")
async def approve_mcp_server(server_id: str, request: Request) -> dict[str, object]:
    server = request.app.state.phase5_store.update_mcp_server(
        server_id, approved=True, status="approved"
    )
    _memory(request).record_event("mcp_server", "mcp_server_approved", f"MCP server {server_id} explicitly approved.")
    return _mcp_server_public(server)


@app.post("/api/v1/mcp/servers/{server_id}/enable")
async def enable_mcp_server(server_id: str, request: Request) -> dict[str, object]:
    store: Phase5Store = request.app.state.phase5_store
    server = store.get_mcp_server(server_id)
    if not server["approved"]:
        raise ValueError("MCP server must be explicitly approved before enablement.")
    manager: MCPManager = request.app.state.mcp_manager
    if manager.is_connected(server_id):
        return _mcp_server_public(server)
    await validate_streamable_endpoint(str(server["endpoint"]))
    secret = request.app.state.secret_resolver.resolve(
        str(server["secret_reference"]) if server["secret_reference"] else None
    )
    if server["secret_reference"] and not secret:
        raise ValueError("Configured MCP credential reference is unavailable.")
    transport = await asyncio.wait_for(
        StreamableHTTPMCPTransport.open(str(server["endpoint"]), secret), timeout=8
    )
    try:
        names = await manager.connect(server_id, transport, timeout_seconds=8)
    except Exception as exc:
        await transport.close()
        store.update_mcp_server(server_id, enabled=False, status="unavailable")
        _memory(request).record_event("mcp_server", "mcp_discovery_failed", f"MCP server {server_id} activation failed safely.")
        raise ValueError("MCP server could not be safely enabled.") from exc
    server = store.update_mcp_server(server_id, approved=True, enabled=True, status="connected")
    _memory(request).record_event("mcp_server", "mcp_server_discovered", f"MCP server {server_id} discovered {len(names)} bounded tools.")
    _memory(request).record_event("mcp_server", "mcp_server_enabled", f"MCP server {server_id} enabled with approval-gated tools.")
    return _mcp_server_public(server)


@app.post("/api/v1/mcp/servers/{server_id}/disable")
async def disable_mcp_server(server_id: str, request: Request) -> dict[str, object]:
    await request.app.state.mcp_manager.disconnect(server_id)
    server = request.app.state.phase5_store.update_mcp_server(
        server_id, enabled=False, status="disabled"
    )
    _memory(request).record_event("mcp_server", "mcp_server_disabled", f"MCP server {server_id} disabled and disconnected.")
    return _mcp_server_public(server)


@app.delete("/api/v1/mcp/servers/{server_id}", status_code=204)
async def delete_mcp_server(server_id: str, request: Request) -> None:
    request.app.state.phase5_store.delete_mcp_server(server_id)
    _memory(request).record_event("mcp_server", "mcp_server_removed", f"MCP server {server_id} removed while disabled.")


@app.get("/api/v1/workflows")
async def list_workflows(request: Request, limit: int = 50) -> list[dict[str, object]]:
    return request.app.state.phase5_store.list_workflows(limit)


@app.post("/api/v1/workflows")
async def create_workflow(
    payload: WorkflowCreateRequest, request: Request
) -> dict[str, object]:
    return request.app.state.phase5_store.create_workflow(
        payload.kind, payload.idempotency_key, payload.task_id
    )


@app.post("/api/v1/workflows/{workflow_id}/transition")
async def transition_workflow(
    workflow_id: str, payload: WorkflowTransitionRequest, request: Request
) -> dict[str, object]:
    return request.app.state.phase5_store.transition(
        workflow_id, payload.status, payload.failure
    )


@app.get("/api/v1/schedules")
async def list_schedules(request: Request, limit: int = 50) -> list[dict[str, object]]:
    return request.app.state.phase5_store.list_schedules(limit)


@app.post("/api/v1/schedules")
async def create_schedule(
    payload: ScheduleCreateRequest, request: Request
) -> dict[str, str]:
    schedule_id = request.app.state.phase5_store.create_schedule(
        payload.task_id, payload.schedule_type, payload.next_run, payload.retry_limit
    )
    return {"schedule_id": schedule_id, "status": "scheduled"}


@app.delete("/api/v1/schedules/{schedule_id}", status_code=204)
async def cancel_schedule(schedule_id: str, request: Request) -> None:
    request.app.state.phase5_store.cancel_schedule(schedule_id)


@app.post("/api/v1/tools/execute")
async def execute_tool(payload: ToolExecutionRequest, request: Request) -> dict[str, object]:
    runtime = runtime_for(request.app, payload.project_id)
    with project_scope(payload.project_id):
        return await runtime.executor.execute(payload.name, payload.arguments)


@app.post("/api/v1/tasks", status_code=201)
async def create_task(payload: TaskCreateRequest, request: Request) -> dict[str, object]:
    limits = TaskLimits(**payload.limits.model_dump())
    runtime = runtime_for(request.app, payload.project_id)
    with project_scope(payload.project_id):
        task_id = runtime.memory.create_task(payload.request, limits, project_id=payload.project_id, memory_ids=payload.memory_ids)
        return await runtime.orchestrator.run(task_id)


@app.get("/api/v1/tasks")
async def list_tasks(request: Request, limit: int = 20) -> list[dict[str, object]]:
    import sqlite3
    from contextlib import closing

    memory = _memory(request)
    with closing(sqlite3.connect(memory.database_path)) as db:
        ids = db.execute("SELECT t.task_id FROM tasks t LEFT JOIN task_scopes s ON t.task_id=s.task_id WHERE s.project_id IS ? ORDER BY t.created_at DESC LIMIT ?", (request.query_params.get("project_id"), max(1, min(limit, 100)))).fetchall()
    return [memory.get_task(row[0]) for row in ids]


@app.get("/api/v1/tasks/{task_id}")
async def get_task(task_id: str, request: Request) -> dict[str, object]:
    _assert_task_scope(request, task_id)
    return _memory(request).get_task(task_id)


@app.get("/api/v1/tasks/{task_id}/steps")
async def get_task_steps(task_id: str, request: Request) -> list[dict[str, object]]:
    _assert_task_scope(request, task_id)
    return _memory(request).get_task_steps(task_id)


@app.post("/api/v1/tasks/{task_id}/resume")
async def resume_task(task_id: str, request: Request) -> dict[str, object]:
    _assert_task_scope(request, task_id)
    project_id = request.query_params.get("project_id")
    with project_scope(project_id):
        return await runtime_for(request.app, project_id).orchestrator.resume(task_id)


@app.post("/api/v1/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> dict[str, object]:
    _assert_task_scope(request, task_id)
    project_id = request.query_params.get("project_id")
    with project_scope(project_id):
        return runtime_for(request.app, project_id).orchestrator.cancel(task_id)


@app.post("/api/v1/tools/execute/{request_id}/continue")
async def continue_tool(request_id: str, request: Request) -> dict[str, object]:
    project_id = request.query_params.get("project_id")
    runtime = runtime_for(request.app, project_id)
    approval = runtime.memory.get_approval(request_id)
    if approval.preview.get("project_id") != project_id:
        raise KeyError(request_id)
    with project_scope(project_id):
        job_id = approval.preview.get("agent_job_id")
        if job_id is not None:
            if project_id is None:
                raise KeyError(request_id)
            job = request.app.state.agent_workspace.get(project_id, job_id)
            if job["approval_id"] != request_id:
                raise KeyError(request_id)
            completed = await request.app.state.agent_workspace.run(project_id, job_id, continuing=True)
            return completed["result"]
        return await runtime.executor.continue_approved(request_id)


@app.get("/api/v1/approvals")
async def pending_approvals(request: Request) -> list[dict[str, object]]:
    return [_approval_public(item) for item in _memory(request).pending_approvals() if item.preview.get("project_id") == request.query_params.get("project_id")]


@app.post("/api/v1/approvals/{request_id}/approve")
async def approve(request_id: str, request: Request) -> dict[str, object]:
    if _memory(request).get_approval(request_id).preview.get("project_id") != request.query_params.get("project_id"):
        raise KeyError(request_id)
    return _approval_public(_memory(request).decide_approval(request_id, True))


@app.post("/api/v1/approvals/{request_id}/reject")
async def reject(request_id: str, request: Request) -> dict[str, object]:
    if _memory(request).get_approval(request_id).preview.get("project_id") != request.query_params.get("project_id"):
        raise KeyError(request_id)
    return _approval_public(_memory(request).decide_approval(request_id, False))


@app.get("/api/v1/activity")
async def activity(request: Request) -> list[dict[str, object]]:
    project_id = request.query_params.get("project_id")
    runtime_for(request.app, project_id)
    return request.app.state.foundation.events(0, project_id, descending=True, limit=20)


@app.post("/api/v1/memories")
async def create_memory(payload: MemoryRequest, request: Request) -> object:
    return _memory(request).create(
        payload.memory_type, payload.content, payload.importance, payload.source
    )


@app.get("/api/v1/memories")
async def search_memories(
    request: Request, query: str = "", memory_type: str | None = None
) -> list[object]:
    return _memory(request).search(query, memory_type)


@app.put("/api/v1/memories/{memory_id}")
async def update_memory(memory_id: int, payload: MemoryRequest, request: Request) -> object:
    return _memory(request).update(memory_id, payload.content)


@app.delete("/api/v1/memories/{memory_id}", status_code=204)
async def delete_memory(memory_id: int, request: Request) -> None:
    _memory(request).delete(memory_id)
