import pytest
from pydantic import ValidationError

from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.phase5_foundations import (
    ActionRegistry,
    BrowserPolicy,
    ControlledAction,
    MCPRegistry,
    MCPServer,
    ScheduledTask,
    Scheduler,
    Workflow,
    WorkflowRegistry,
    WorkflowStatus,
)


def test_mcp_metadata_is_strict_and_disabled_by_default():
    registry = MCPRegistry()
    item = registry.register(MCPServer(server_id="local", name="Local", endpoint="local://safe"))
    assert item.enabled is False
    with pytest.raises(ValidationError):
        MCPServer(server_id="local", name="x", endpoint="x", secret="no")
    registry.record_discovery("local", ["read_data"])
    assert registry.public(item).get("secret_reference") is None


def test_controlled_actions_are_bounded_metadata_only():
    actions = ActionRegistry()
    action = actions.register(ControlledAction(action_id="browser.navigate", domain="browser", description="Navigate", risk=RiskLevel.MEDIUM))
    assert action.requires_approval is True
    assert not hasattr(action, "handler")


def test_scheduler_prevents_duplicate_idempotency_and_supports_cancel():
    scheduler = Scheduler()
    scheduler.schedule(ScheduledTask(schedule_id="one", task_id="t1", idempotency_key="k1"))
    with pytest.raises(ValueError):
        scheduler.schedule(ScheduledTask(schedule_id="two", task_id="t1", idempotency_key="k1"))
    assert scheduler.cancel("one").status == "cancelled"


def test_browser_policy_and_workflow_foundations_do_not_execute_or_fabricate():
    policy = BrowserPolicy(allowed_domains=["example.com"])
    assert policy.permits("https://sub.example.com/page")
    assert not policy.permits("https://evil.example")
    workflows = WorkflowRegistry()
    workflows.create(Workflow(workflow_id="lead.one", kind="client", notes=["draft only"]))
    assert workflows.transition("lead.one", WorkflowStatus.WAITING_FOR_APPROVAL).status == WorkflowStatus.WAITING_FOR_APPROVAL
    with pytest.raises(ValueError):
        workflows.create(Workflow(workflow_id="bad.one", kind="application", fabricated_data=True))
