"""Authenticated workspace resources; consequential actions stay in ToolExecutor."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from services.agent_api.app.account_actions import AccountInput, refresh_account_tools
from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.agent_workspace import AgentJobInput
from services.agent_api.app.capabilities import Skill
from services.agent_api.app.project_runtime import project_scope, runtime_for, task_project
from services.agent_api.app.studio_ai import AIProposalInput
from services.agent_api.app.workspace_records import (
    RecordInput,
    RescheduleInput,
    ScheduleContract,
    ScheduleInput,
)

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace"])


@router.get("/agent-batches")
async def agent_batches(request: Request):
    project_id, _ = scope(request, project_required=True)
    return request.app.state.agent_batches.list(project_id)


@router.post("/agent-batches", status_code=201)
async def create_agent_batch(value: AIProposalInput, request: Request):
    project_id, _ = scope(request, project_required=True, write=True)
    result = request.app.state.agent_batches.create(project_id, value)
    audit(request, project_id, "agent_batch_created")
    return result


@router.post("/agent-batches/{batch_id}/run")
async def run_agent_batch(batch_id: str, request: Request):
    project_id = request.query_params.get("project_id")
    if not project_id:
        raise HTTPException(400, "Select a registered project first.")
    return await request.app.state.agent_batches.run(project_id, batch_id)


@router.post("/agent-batches/{batch_id}/cancel")
async def cancel_agent_batch(batch_id: str, request: Request):
    project_id, _ = scope(request, project_required=True, write=True)
    request.app.state.agent_batches.cancel(project_id, batch_id)
    audit(request, project_id, "agent_batch_cancelled")
    return {"status": "cancelled"}


@router.get("/control/status")
async def control_status(request: Request):
    policy = request.app.state.executor.policy.snapshot()
    return {"emergency_stop": policy["emergency_stop"],
            "browser": "disabled_pending_verified_os_network_isolation",
            "computer": "disabled_pending_owned_uia_workers_and_windows_acceptance",
            "accounts": "configured_unverified" if any(row["status"] == "connected" for row in request.app.state.account_actions.accounts()) else "disconnected",
            "voice": "configured_local_transcription" if request.app.state.voice_capture.endpoint else "unconfigured"}


@router.get("/diagnostics")
async def diagnostics(request: Request):
    # Deliberately omit environment values, paths, prompts, transcripts, user
    # records and credential references. This remains readable during a stop.
    import platform

    from services.agent_api.app.migrations import MIGRATIONS
    state = request.app.state
    return {"format_version": 1, "platform": platform.system(), "python": platform.python_version(),
            "schema_version": max(MIGRATIONS), "provider": state.settings.ai_provider if state.settings.ai_provider in {'mock', 'ollama', 'openai_compatible'} else 'unavailable',
            "provider_configured": state.settings.provider_configured,
            "scheduler": state.workspace_scheduler_status,
            "emergency_stop": state.executor.policy.snapshot()["emergency_stop"],
            "verification": "No acceptance result is inferred from runtime configuration."}


@router.post("/control/stop")
async def stop_control(request: Request):
    import asyncio

    from services.agent_api.app.backend_boundary import ProcessCancellation

    state = request.app.state
    state.executor.policy.emergency_stop(True)
    state.backup_service.clear()
    state.memory.record_event("emergency_stop", "stopped", "Execution stopped; already accepted external effects may remain.", project_id=None)
    ProcessCancellation.cancel_database(state.settings.database_path)
    for job_id in list(state.agent_workspace.active):
        active = state.agent_workspace.active.get(job_id)
        if active is not None:
            active.cancel()
    cleanup = [asyncio.create_task(state.browser_manager.close_all()), asyncio.create_task(state.computer_manager.close_all())]
    done, pending = await asyncio.wait(cleanup, timeout=5)
    for task in pending:
        task.cancel()
        task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
    incomplete = bool(pending) or any(task.cancelled() or task.exception() is not None for task in done)
    return {"status": "stopped", "session_cleanup": "incomplete" if incomplete else "completed",
            "limitation": "Cooperative cancellation and owned process control cannot undo accepted external effects."}


@router.post("/control/reset")
async def reset_control(request: Request):
    request.app.state.executor.policy.emergency_stop(False)
    request.app.state.memory.record_event("emergency_stop", "reset", "Authenticated user reset the execution stop; old grants remain invalidated.", project_id=None)
    return {"status": "ready", "execution": "No cancelled job or external action was restarted."}


def scope(request, *, project_required=False, write=False):
    project_id = request.query_params.get("project_id")
    if project_required and not project_id:
        raise HTTPException(400, "Select a registered project first.")
    runtime = runtime_for(request.app, project_id)
    category = ActionCategory.EDIT if write else ActionCategory.READ
    if runtime.executor.policy.denied(category):
        raise HTTPException(403, "The current action policy blocks this category.")
    if not write and not runtime.executor.policy.allowed(category):
        raise HTTPException(403, "Read approval is required; use the approved project tools.")
    return project_id or "", runtime


def audit(request, project_id, event):
    request.app.state.memory.record_event("workspace", event, "Authenticated workspace metadata changed.", project_id=project_id or None)


@router.get("/agents")
async def jobs(request: Request):
    project_id, _ = scope(request, project_required=True)
    return request.app.state.agent_workspace.list(project_id)


@router.post("/agents", status_code=201)
async def create_job(value: AgentJobInput, request: Request):
    project_id, _ = scope(request, project_required=True, write=True)
    result = request.app.state.agent_workspace.create(project_id, value)
    audit(request, project_id, "agent_queued")
    return result


@router.post("/agents/{job_id}/run")
async def run_job(job_id: str, request: Request):
    project_id = request.query_params.get("project_id")
    if not project_id:
        raise HTTPException(400, "Select a registered project first.")
    return await request.app.state.agent_workspace.run(project_id, job_id)


@router.post("/agents/{job_id}/continue")
async def continue_job(job_id: str, request: Request):
    project_id = request.query_params.get("project_id")
    if not project_id:
        raise HTTPException(400, "Select a registered project first.")
    return await request.app.state.agent_workspace.run(project_id, job_id, continuing=True)


@router.post("/agents/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    project_id, _ = scope(request, project_required=True, write=True)
    request.app.state.agent_workspace.cancel(project_id, job_id)
    audit(request, project_id, "agent_cancelled")
    return {"status": "cancelled"}


@router.get("/records")
async def records(request: Request):
    project_id, _ = scope(request)
    return request.app.state.workspace_records.list_records(project_id)


@router.post("/records", status_code=201)
async def create_record(value: RecordInput, request: Request):
    project_id, _ = scope(request, write=True)
    result = request.app.state.workspace_records.save_record(project_id, value)
    audit(request, project_id, "record_created")
    return result


@router.get('/records/page')
async def record_page(request: Request, kind: str | None = Query(default=None, max_length=30),
                      status: str | None = Query(default=None, max_length=20), query: str = Query(default='', max_length=200),
                      cursor: str | None = Query(default=None, max_length=1000)):
    project_id, _ = scope(request)
    return request.app.state.workspace_records.record_page(project_id, kind=kind, status=status, query=query, cursor=cursor)


class UpdateRecord(RecordInput):
    expected_revision: int = Field(ge=1)


@router.put("/records/{record_id}")
async def update_record(record_id: str, value: UpdateRecord, request: Request):
    project_id, _ = scope(request, write=True)
    result = request.app.state.workspace_records.save_record(project_id, value, record_id, value.expected_revision)
    audit(request, project_id, "record_updated")
    return result


class QuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    task_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class AnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=4000)


class DismissQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_question_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class BriefingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Reuse schedule timestamp validation, including its timezone requirement.
    start: str = Field(min_length=1, max_length=50)
    end: str = Field(min_length=1, max_length=50)


@router.post("/briefing")
async def briefing(value: BriefingInput, request: Request):
    from datetime import datetime
    project_id, _ = scope(request)
    try:
        start = datetime.fromisoformat(value.start.replace("Z", "+00:00"))
        end = datetime.fromisoformat(value.end.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Use ISO timestamps with explicit timezones.") from None
    return request.app.state.workspace_records.briefing(project_id, start, end)


@router.get("/decisions")
async def decisions(request: Request):
    project_id, _ = scope(request)
    return request.app.state.workspace_records.decisions(project_id)


@router.post("/decisions", status_code=201)
async def ask(value: QuestionInput, request: Request):
    project_id, runtime = scope(request, write=True)
    if value.task_id:
        if task_project(runtime.memory.database_path, value.task_id) != (project_id or None):
            raise HTTPException(404, "Task is unavailable in this scope.")
        runtime.memory.get_task(value.task_id)
    identifier = request.app.state.workspace_records.ask(project_id, value.question, value.task_id)
    audit(request, project_id, "question_created")
    return {"decision_id": identifier}


@router.post("/decisions/{decision_id}/answer")
async def answer(decision_id: str, value: AnswerInput, request: Request):
    project_id, _ = scope(request, write=True)
    request.app.state.workspace_records.answer(project_id, decision_id, value.answer)
    audit(request, project_id, "question_answered")
    return {"status": "answered", "execution": "answer recorded; no task was automatically resumed"}


@router.post('/decisions/{decision_id}/dismiss')
async def dismiss_question(decision_id: str, value: DismissQuestion, request: Request):
    project_id, _ = scope(request, write=True)
    result = request.app.state.workspace_records.dismiss_decision(project_id, decision_id, value.expected_question_sha256)
    audit(request, project_id, 'question_dismissed')
    return result


@router.get("/reminders")
async def schedules(request: Request):
    project_id, _ = scope(request)
    return request.app.state.workspace_records.schedules(project_id)


@router.post("/reminders", status_code=201)
async def schedule(value: ScheduleInput, request: Request):
    project_id, _ = scope(request, write=True)
    identifier = request.app.state.workspace_records.schedule(project_id, value)
    audit(request, project_id, "reminder_created")
    return {"schedule_id": identifier}


@router.delete("/reminders/{schedule_id}")
async def cancel_schedule(schedule_id: str, request: Request):
    project_id, _ = scope(request, write=True)
    request.app.state.workspace_records.cancel_schedule(project_id, schedule_id)
    audit(request, project_id, "reminder_cancelled")
    return {"status": "cancelled"}


@router.put('/reminders/{schedule_id}')
async def reschedule(schedule_id: str, value: RescheduleInput, request: Request):
    project_id, _ = scope(request, write=True)
    result = request.app.state.workspace_records.reschedule(project_id, schedule_id, value)
    audit(request, project_id, 'reminder_rescheduled')
    return result


@router.post('/reminders/{schedule_id}/cancel')
async def cancel_exact_schedule(schedule_id: str, value: ScheduleContract, request: Request):
    project_id, _ = scope(request, write=True)
    request.app.state.workspace_records.cancel_schedule(project_id, schedule_id, value.expected_contract_sha256)
    audit(request, project_id, 'reminder_cancelled')
    return {'status': 'cancelled', 'execution': 'Pending notifications cancelled; no task or external action executed.'}


@router.get("/notifications")
async def notifications(request: Request):
    project_id, _ = scope(request)
    return {"scheduler": request.app.state.workspace_scheduler_status,
            "items": request.app.state.workspace_records.occurrences(project_id)}


@router.post("/notifications/{occurrence_id}/acknowledge")
async def acknowledge(occurrence_id: str, request: Request):
    project_id, _ = scope(request, write=True)
    request.app.state.workspace_records.acknowledge(project_id, occurrence_id)
    audit(request, project_id, "notification_acknowledged")
    return {"status": "acknowledged"}


class ProjectToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(pattern=r"^(knowledge_(import|import_docx|search|documents|document|document_page|forget)|library_(create|artifacts|artifact|artifact_page|history|export)|skill_(inspect|uninstall)|research_fetch|draft_(create|inspect|list|edit|merge|close|ai_edit|copy_(prepare|create|inspect|list|command|run|collect))|social_(catalog|prepare))$")
    arguments: dict = Field(default_factory=dict)


@router.post("/knowledge/execute")
async def knowledge(value: ProjectToolInput, request: Request):
    # Do not require unapproved READ access here: the executor is the approval
    # path and must be able to issue a grant even when reads require approval.
    project_id = request.query_params.get("project_id")
    if not project_id:
        raise HTTPException(400, "Select a registered project first.")
    runtime = runtime_for(request.app, project_id)
    with project_scope(project_id):
        return await runtime.executor.execute(value.tool, value.arguments)


@router.get("/accounts")
async def accounts(request: Request):
    scope(request)
    if request.query_params.get("project_id"):
        raise HTTPException(400, "Connected accounts are managed in Personal scope.")
    return {"accounts": request.app.state.account_actions.accounts(), "login": "Google/Microsoft authorization-code PKCE and explicit refresh require registered client configuration; GitHub uses protected token references. Live provider verification is pending."}


@router.post("/accounts", status_code=201)
async def connect_account(value: AccountInput, request: Request):
    scope(request, write=True)
    if request.query_params.get("project_id"):
        raise HTTPException(400, "Connected accounts are managed in Personal scope.")
    if request.app.state.executor.policy.denied(ActionCategory.ACCOUNT):
        raise HTTPException(403, "Account changes are blocked by policy.")
    identifier = request.app.state.account_actions.connect(value)
    refresh_account_availability(request.app)
    audit(request, "", "account_connected")
    return {"account_id": identifier, "status": "connected", "remote_verification": "not performed"}


@router.delete("/accounts/{account_id}")
async def revoke_account(account_id: str, request: Request):
    if request.query_params.get("project_id"):
        raise HTTPException(400, "Connected accounts are managed in Personal scope.")
    # Revocation remains possible even when the execution stop is active.
    request.app.state.account_actions.revoke(account_id)
    refresh_account_availability(request.app)
    audit(request, "", "account_revoked")
    return {"status": "revoked"}


def refresh_account_availability(app):
    registry = app.state.executor.registry
    refresh_account_tools(registry, app.state.account_actions)
    for name in ("account_send_email", "account_github_issue", "account_calendar_create", 'account_linkedin_post', 'account_mastodon_post', 'account_bluesky_post', 'account_threads_post', 'account_pinterest_pin', 'account_facebook_post', 'account_instagram_post', 'account_x_post', 'account_youtube_upload', 'account_tiktok_creator', 'account_tiktok_post', 'account_x_budget'):
        app.state.capabilities.set_enabled("tool." + name, registry.get(name).enabled)


class AccountExecutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(pattern=r"^account_(send_email|github_issue|calendar_create|linkedin_post|mastodon_post|bluesky_post|threads_post|pinterest_pin|facebook_post|instagram_post|x_post|youtube_upload|tiktok_creator|tiktok_post|x_budget|publishing_status|media_inspect)$")
    arguments: dict


@router.post("/accounts/execute")
async def execute_account(value: AccountExecutionInput, request: Request):
    if request.query_params.get("project_id"):
        raise HTTPException(400, "Account actions require Personal scope.")
    with project_scope(None):
        return await request.app.state.executor.execute(value.tool, value.arguments)


class InstallSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: Skill
    consent: bool = False


class EnableSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    expected_version: str | None = Field(default=None, pattern=r'^[0-9]+\.[0-9]+\.[0-9]+$', max_length=60)
    manifest_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


@router.get("/skill-manifests")
async def manifests(request: Request):
    from services.agent_api.app.skill_manifests import list_manifests
    _, runtime = scope(request, project_required=True)
    return list_manifests(runtime)


@router.post("/skill-manifests", status_code=201)
async def install_manifest(value: InstallSkill, request: Request):
    from services.agent_api.app.skill_manifests import install
    project_id, runtime = scope(request, project_required=True, write=True)
    install(runtime, value.manifest, consent=value.consent)
    request.app.state.project_runtimes.pop(project_id, None)
    audit(request, project_id, "skill_installed_disabled")
    return {"status": "installed_disabled", "activation": "explicit enable required"}


@router.put("/skill-manifests/{skill_id}")
async def enable_manifest(skill_id: str, value: EnableSkill, request: Request):
    from services.agent_api.app.skill_manifests import set_enabled
    project_id, runtime = scope(request, project_required=True, write=True)
    set_enabled(runtime, skill_id, value.enabled, expected_version=value.expected_version, manifest_sha256=value.manifest_sha256)
    request.app.state.project_runtimes.pop(project_id, None)
    audit(request, project_id, "skill_state_changed")
    return {"status": "enabled" if value.enabled else "disabled"}
