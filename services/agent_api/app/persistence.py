"""SQLite-backed Phase 5 workflow, schedule, event, lead, and document state."""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

WORKFLOW_STATES = {
    "pending",
    "running",
    "waiting_for_approval",
    "waiting_for_capability",
    "paused",
    "completed",
    "failed",
    "cancelled",
}
WORKFLOW_TRANSITIONS = {
    "pending": {"running", "waiting_for_approval", "waiting_for_capability", "paused", "failed", "cancelled"},
    "running": {"waiting_for_approval", "waiting_for_capability", "paused", "completed", "failed", "cancelled"},
    "waiting_for_approval": {"running", "failed", "cancelled"},
    "waiting_for_capability": {"running", "failed", "cancelled"},
    "paused": {"running", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class Phase5Store:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        from services.agent_api.app.migrations import initialize_database

        initialize_database(self.path, allow_existing=os.getenv("JARVIS_ALLOW_DATABASE_MIGRATION") == "1")

    def create_mcp_server(
        self, server_id: str, name: str, endpoint: str, secret_reference: str | None
    ) -> dict[str, object]:
        if not server_id or len(server_id) > 100 or not name or len(name) > 100:
            raise ValueError("Invalid MCP server metadata.")
        if len(endpoint) > 500 or (secret_reference is not None and len(secret_reference) > 200):
            raise ValueError("MCP server configuration is out of range.")
        now = self._now()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO mcp_servers VALUES (?,?,?,?,?,?,?,?,?)",
                (server_id, name, endpoint, secret_reference, 0, 0, "disabled", now, now),
            )
        return self.get_mcp_server(server_id)

    def get_mcp_server(self, server_id: str) -> dict[str, object]:
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "SELECT server_id,name,endpoint,secret_reference,enabled,approved,status,created_at,updated_at FROM mcp_servers WHERE server_id=?",
                (server_id,),
            ).fetchone()
        if row is None:
            raise KeyError(server_id)
        keys = ("server_id", "name", "endpoint", "secret_reference", "enabled", "approved", "status", "created_at", "updated_at")
        return dict(zip(keys, row, strict=True))

    def list_mcp_servers(self) -> list[dict[str, object]]:
        with closing(sqlite3.connect(self.path)) as db, db:
            rows = db.execute(
                "SELECT server_id,name,endpoint,secret_reference,enabled,approved,status,created_at,updated_at FROM mcp_servers ORDER BY server_id"
            ).fetchall()
        keys = ("server_id", "name", "endpoint", "secret_reference", "enabled", "approved", "status", "created_at", "updated_at")
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def update_mcp_server(
        self,
        server_id: str,
        *,
        approved: bool | None = None,
        enabled: bool | None = None,
        status: str | None = None,
    ) -> dict[str, object]:
        current = self.get_mcp_server(server_id)
        next_approved = bool(current["approved"]) if approved is None else approved
        next_enabled = bool(current["enabled"]) if enabled is None else enabled
        if next_enabled and not next_approved:
            raise ValueError("MCP server must be explicitly approved before enablement.")
        next_status = status or ("enabled" if next_enabled else "disabled")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE mcp_servers SET approved=?,enabled=?,status=?,updated_at=? WHERE server_id=?",
                (int(next_approved), int(next_enabled), next_status, self._now(), server_id),
            )
        return self.get_mcp_server(server_id)

    def delete_mcp_server(self, server_id: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor = db.execute(
                "DELETE FROM mcp_servers WHERE server_id=? AND enabled=0", (server_id,)
            )
        if cursor.rowcount == 0:
            raise ValueError("MCP server must be disabled before removal.")

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def create_workflow(
        self, kind: str, idempotency_key: str, task_id: str | None = None
    ) -> dict[str, object]:
        if not kind or len(kind) > 50 or not idempotency_key or len(idempotency_key) > 100:
            raise ValueError("Invalid workflow metadata.")
        now = self._now()
        workflow_id = str(uuid.uuid4())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO workflows VALUES (?,?,?,?,?,?,?,?,?)",
                (workflow_id, kind, "pending", 0, task_id, idempotency_key, None, now, now),
            )
            row = db.execute(
                "SELECT id FROM workflows WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        if row is None:
            raise RuntimeError("Workflow could not be created.")
        workflow_id = row[0]
        return self.get_workflow(workflow_id)

    def get_workflow(self, workflow_id: str) -> dict[str, object]:
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "SELECT id,kind,status,current_step,task_id,idempotency_key,failure,created_at,updated_at FROM workflows WHERE id=?",
                (workflow_id,),
            ).fetchone()
        if not row:
            raise KeyError(workflow_id)
        return dict(
            zip(
                (
                    "id",
                    "kind",
                    "status",
                    "current_step",
                    "task_id",
                    "idempotency_key",
                    "failure",
                    "created_at",
                    "updated_at",
                ),
                row,
                strict=True,
            )
        )

    def list_workflows(self, limit: int = 50) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("Workflow limit is out of range.")
        with closing(sqlite3.connect(self.path)) as db, db:
            rows = db.execute(
                "SELECT id,kind,status,current_step,task_id,idempotency_key,failure,created_at,updated_at "
                "FROM workflows ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        fields = (
            "id", "kind", "status", "current_step", "task_id", "idempotency_key",
            "failure", "created_at", "updated_at",
        )
        return [dict(zip(fields, row, strict=True)) for row in rows]

    def transition(
        self, workflow_id: str, status: str, failure: str | None = None
    ) -> dict[str, object]:
        if status not in WORKFLOW_STATES:
            raise ValueError("Invalid workflow state.")
        now = self._now()
        with closing(sqlite3.connect(self.path)) as db, db:
            current = db.execute(
                "SELECT status FROM workflows WHERE id=?", (workflow_id,)
            ).fetchone()
            if current is None:
                raise KeyError(workflow_id)
            if status not in WORKFLOW_TRANSITIONS[current[0]]:
                raise ValueError("Invalid workflow transition.")
            cursor = db.execute(
                "UPDATE workflows SET status=?,failure=?,updated_at=? WHERE id=?",
                (status, failure, now, workflow_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(workflow_id)
            db.execute(
                "INSERT INTO workflow_events VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    workflow_id,
                    None,
                    None,
                    "workflow_changed",
                    status,
                    failure or status,
                    now,
                ),
            )
        return self.get_workflow(workflow_id)

    def list_schedules(self, limit: int = 50) -> list[dict[str, object]]:
        if not 1 <= limit <= 100:
            raise ValueError("Schedule limit is out of range.")
        with closing(sqlite3.connect(self.path)) as db, db:
            rows = db.execute(
                "SELECT id,task_id,schedule_type,interval_seconds,enabled,next_run,last_run,retry_limit,created_at "
                "FROM schedules ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        fields = (
            "id", "task_id", "schedule_type", "interval_seconds", "enabled", "next_run",
            "last_run", "retry_limit", "created_at",
        )
        return [dict(zip(fields, row, strict=True)) for row in rows]

    def cancel_schedule(self, schedule_id: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            cursor = db.execute("UPDATE schedules SET enabled=0 WHERE id=?", (schedule_id,))
        if cursor.rowcount == 0:
            raise KeyError(schedule_id)

    def create_schedule(
        self, task_id: str, schedule_type: str, next_run: str, retry_limit: int = 0
    ) -> str:
        try:
            scheduled_at = datetime.fromisoformat(next_run)
        except (TypeError, ValueError) as exc:
            raise ValueError("Schedule time must be a valid ISO 8601 timestamp.") from exc
        if (
            schedule_type not in {"one_time", "interval", "daily", "weekly"}
            or not 0 <= retry_limit <= 3
            or not task_id
            or len(task_id) > 100
            or scheduled_at.tzinfo is None
        ):
            raise ValueError("Invalid schedule.")
        schedule_id = str(uuid.uuid4())
        now = self._now()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?,?)",
                (schedule_id, task_id, schedule_type, None, 1, next_run, None, retry_limit, now),
            )
        return schedule_id

    def create_event(self, event_type: str, message: str, workflow_id: str | None = None) -> str:
        if len(message) > 500 or re.search(
            r"(?i)(api[_ -]?key|password|token|cookie)\s*[:=]", message
        ):
            raise ValueError("Sensitive event content rejected.")
        event_id = str(uuid.uuid4())
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO workflow_events VALUES (?,?,?,?,?,?,?,?)",
                (event_id, workflow_id, None, None, event_type, "unread", message, self._now()),
            )
        return event_id

    def create_lead(self, business_name: str, website: str = "", source: str = "") -> str:
        if not business_name or len(business_name) > 200:
            raise ValueError("Invalid lead.")
        lead_id = str(uuid.uuid4())
        now = self._now()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO leads VALUES (?,?,?,?,?,?,?,?,?,?)",
                (lead_id, business_name, website, None, None, source, "", "new", now, now),
            )
        return lead_id

    def create_document(self, doc_type: str, title: str, workflow_id: str | None = None) -> str:
        document_id = str(uuid.uuid4())
        now = self._now()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)",
                (document_id, workflow_id, doc_type, title[:200], None, "draft", now, now),
            )
        return document_id
