"""Small SQLite repository for durable memory and task execution history."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from services.agent_api.app.tasks import TaskLimits

_INHERIT_SCOPE = object()


@dataclass(frozen=True)
class Memory:
    id: int
    memory_type: str
    content: str
    importance: int | None
    source: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Approval:
    request_id: str
    tool_name: str
    arguments: dict[str, object]
    risk: str
    status: str
    expires_at: str
    preview: dict[str, object]


class MemoryRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        from services.agent_api.app.migrations import initialize_database

        initialize_database(self.database_path, allow_existing=os.getenv("JARVIS_ALLOW_DATABASE_MIGRATION") == "1")

    def create(
        self, memory_type: str, content: str, importance: int | None, source: str | None
    ) -> Memory:
        self._reject_sensitive_content(content)
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO memories VALUES (NULL,?,?,?,?,?,?)",
                (memory_type, content, importance, source, now, now),
            )
            memory_id = cursor.lastrowid
        return self.get(memory_id)

    def get(self, memory_id: int) -> Memory:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(memory_id)
        return Memory(*row)

    def search(self, query: str = "", memory_type: str | None = None) -> list[Memory]:
        sql, values = "SELECT * FROM memories WHERE content LIKE ?", [f"%{query}%"]
        if memory_type:
            sql += " AND memory_type=?"
            values.append(memory_type)
        sql += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            return [Memory(*row) for row in connection.execute(sql, values).fetchall()]

    def update(self, memory_id: int, content: str) -> Memory:
        self._reject_sensitive_content(content)
        with self._connect() as connection:
            connection.execute(
                "UPDATE memories SET content=?, updated_at=? WHERE id=?",
                (content, datetime.now(UTC).isoformat(), memory_id),
            )
        return self.get(memory_id)

    def delete(self, memory_id: int) -> None:
        with self._connect() as connection:
            if connection.execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount == 0:
                raise KeyError(memory_id)

    def record_event(self, tool_name: str, status: str, detail: str, *, project_id=_INHERIT_SCOPE, task_id=None) -> None:
        with self._connect() as connection:
            self._insert_event(connection, tool_name, status, detail, project_id=project_id, task_id=task_id)

    def _insert_event(self, connection, tool_name, status, detail, *, project_id=_INHERIT_SCOPE, task_id=None):
        from services.agent_api.app.project_runtime import execution_project
        from services.agent_api.app.secure_store import redact
        from services.agent_api.app.usage_policy import budget_task

        cursor = connection.execute(
            "INSERT INTO task_events VALUES (NULL,?,?,?,?)",
            (tool_name, status, redact(detail), datetime.now(UTC).isoformat()),
        )
        project = execution_project.get() if project_id is _INHERIT_SCOPE else project_id
        identity = task_id or (tool_name if status.startswith("task_") else budget_task.get())
        if identity and project_id is _INHERIT_SCOPE and project is None:
            row = connection.execute("SELECT project_id FROM task_scopes WHERE task_id=?", (identity,)).fetchone()
            if row:
                project = row[0]
        connection.execute("INSERT INTO event_scopes VALUES (?,?,?)", (cursor.lastrowid, project, identity))

    def task_memory_ids(self, task_id):
        with self._connect() as connection:
            row = connection.execute("SELECT memory_ids_json FROM task_inputs WHERE task_id=?", (task_id,)).fetchone()
        return tuple(json.loads(row[0])) if row else ()

    def recent_events(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT tool_name,status,detail,created_at FROM task_events ORDER BY id DESC LIMIT 20"
            ).fetchall()
        return [
            dict(zip(("tool_name", "status", "detail", "timestamp"), row, strict=True))
            for row in rows
        ]

    def create_approval(
        self,
        tool_name: str,
        arguments: dict[str, object],
        risk: str,
        preview: dict[str, object],
        seconds: int = 300,
        task_id: str | None = None,
        step_number: int | None = None,
    ) -> Approval:
        request_id = str(uuid.uuid4())
        expires_at = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
        with self._connect() as connection:
            if task_id is not None:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if row is None or row[0] != "running" or step_number is None:
                    raise ValueError("Task is not available for execution.")
            connection.execute(
                "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    tool_name,
                    json.dumps(arguments),
                    risk,
                    "pending",
                    expires_at,
                    json.dumps(preview),
                    datetime.now(UTC).isoformat(),
                ),
            )
            if task_id is not None:
                connection.execute("INSERT INTO approval_tasks VALUES (?,?,?)", (request_id, task_id, step_number))
        self.record_event(tool_name, "approval_requested", f"Approval requested ({risk}).")
        return self.get_approval(request_id)

    def get_approval(self, request_id: str) -> Approval:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_id,tool_name,arguments_json,risk,status,expires_at,preview_json "
                "FROM approvals WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            raise KeyError(request_id)
        approval = Approval(
            row[0], row[1], json.loads(row[2]), row[3], row[4], row[5], json.loads(row[6])
        )
        if approval.status in {"pending", "approved"} and datetime.fromisoformat(
            approval.expires_at
        ) <= datetime.now(UTC):
            self._set_approval_status(request_id, "expired", approval.tool_name, "approval_expired", ("pending", "approved"))
            return self.get_approval(request_id)
        return approval

    def pending_approvals(self) -> list[Approval]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT request_id FROM approvals WHERE status='pending'"
            ).fetchall()
        return [
            item for item in (self.get_approval(row[0]) for row in rows) if item.status == "pending"
        ]

    def decide_approval(self, request_id: str, approved: bool) -> Approval:
        approval = self.get_approval(request_id)
        if approval.status == "pending":
            status = "approved" if approved else "rejected"
            event = "approval_approved" if approved else "approval_rejected"
            self._set_approval_status(request_id, status, approval.tool_name, event, ("pending",))
        return self.get_approval(request_id)

    def consume_approval(self, request_id: str, task_id: str | None = None) -> Approval | None:
        """Only the transaction winner receives an executable claim."""
        approval = self.get_approval(request_id)
        if approval.status != "approved":
            return None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            owner = self._approval_owner(connection, request_id)
            if owner != task_id:
                return None
            if owner is not None:
                task = connection.execute("SELECT status FROM tasks WHERE task_id=?", (owner,)).fetchone()
                if task is None or task[0] != "running":
                    return None
            cursor = connection.execute(
                "UPDATE approvals SET status='consumed' WHERE request_id=? AND status='approved' AND expires_at>?",
                (request_id, datetime.now(UTC).isoformat()),
            )
        if cursor.rowcount == 1:
            self.record_event(
                approval.tool_name, "tool_execution_started", "Approved execution started."
            )
            return replace(approval, status="consumed")
        return None

    @staticmethod
    def _approval_owner(connection: sqlite3.Connection, request_id: str) -> str | None:
        # Also protect task approvals created by older versions.
        row = connection.execute("SELECT task_id FROM approval_tasks WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            row = connection.execute("SELECT task_id FROM task_steps WHERE approval_id=?", (request_id,)).fetchone()
        return row[0] if row else None

    def approval_owner(self, request_id: str) -> str | None:
        with self._connect() as connection:
            return self._approval_owner(connection, request_id)

    def claim_task(self, task_id: str, owner: str) -> bool:
        with self._connect() as connection:
            return connection.execute("INSERT OR IGNORE INTO task_claims VALUES (?,?)", (task_id, owner)).rowcount == 1

    def release_task(self, task_id: str, owner: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM task_claims WHERE task_id=? AND owner=?", (task_id, owner))

    def _set_approval_status(
        self, request_id: str, status: str, tool_name: str, event: str, expected: tuple[str, ...]
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE approvals SET status=? WHERE request_id=? AND status IN ({','.join('?' for _ in expected)})",
                (status, request_id, *expected),
            )
        if cursor.rowcount == 1:
            with self._connect() as connection:
                preview = json.loads(connection.execute("SELECT preview_json FROM approvals WHERE request_id=?", (request_id,)).fetchone()[0])
                owner = self._approval_owner(connection, request_id)
            self.record_event(tool_name, event, f"Approval {status}.", project_id=preview.get("project_id"), task_id=owner)

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            yield connection

    def create_task(self, request: str, limits: TaskLimits, *, project_id=None, memory_ids=()) -> str:
        project_id = project_id or getattr(self, "project_id", None)
        task_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            if project_id is not None and not connection.execute("SELECT 1 FROM projects WHERE project_id=?", (project_id,)).fetchone():
                raise KeyError(project_id)
            connection.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?)",
                (task_id, request, "queued", 0, json.dumps(limits.__dict__), None, now, now),
            )
            if project_id is not None:
                connection.execute("INSERT INTO task_scopes VALUES (?,?)", (task_id, project_id))
            connection.execute("INSERT INTO task_inputs VALUES (?,?)", (task_id, json.dumps(list(memory_ids))))
            self._insert_event(connection, task_id, "task_created", "Task created.", project_id=project_id, task_id=task_id)
        return task_id

    def get_task(self, task_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return {
            "task_id": row[0],
            "request": row[1],
            "status": row[2],
            "current_step": row[3],
            "limits": json.loads(row[4]),
            "error": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }

    def list_tasks(self, limit: int = 20) -> list[dict[str, object]]:
        with self._connect() as connection:
            ids = connection.execute(
                "SELECT task_id FROM tasks ORDER BY updated_at DESC LIMIT ?",
                (min(max(limit, 1), 100),),
            ).fetchall()
        return [self.get_task(row[0]) for row in ids]

    def recover_interrupted_tasks(self) -> list[str]:
        """Mark process-local running tasks failed after restart; never replay side effects."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT task_id,current_step FROM tasks WHERE status=?", ("running",)
            ).fetchall()
        recovered: list[str] = []
        for task_id, current_step in rows:
            self.update_task(
                task_id,
                "failed",
                current_step,
                "Application restarted during execution; inspect audit before retrying.",
            )
            recovered.append(task_id)
        # Startup recovery assumes the supported single-backend deployment.
        with self._connect() as connection:
            connection.execute("DELETE FROM task_claims")
        return recovered

    def update_task(
        self, task_id: str, status: str, current_step: int = 0, error: str | None = None
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status=?, current_step=?, error=?, updated_at=? WHERE task_id=? AND status NOT IN ('cancelled','failed','completed')",
                (status, current_step, error, now, task_id),
            )
            if cursor.rowcount and status in {"cancelled", "failed", "completed"}:
                connection.execute(
                    "UPDATE approvals SET status='cancelled' WHERE status IN ('pending','approved') AND request_id IN "
                    "(SELECT request_id FROM approval_tasks WHERE task_id=? UNION SELECT approval_id FROM task_steps WHERE task_id=?)",
                    (task_id, task_id),
                )
        if cursor.rowcount:
            self.record_event(task_id, f"task_{status}", error or f"Task {status}.")

    def create_task_step(
        self,
        task_id: str,
        number: int,
        description: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO task_steps VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    number,
                    description,
                    tool_name,
                    json.dumps(arguments),
                    "planned",
                    None,
                    None,
                    now,
                    now,
                ),
            )

    def update_task_step(
        self, task_id: str, number: int, status: str, result: dict[str, object]
    ) -> None:
        approval = result.get("approval") or {}
        approval_id = approval.get("request_id") if isinstance(approval, dict) else None
        now = datetime.now(UTC).isoformat()
        safe = {
            k: v
            for k, v in result.items()
            if k not in {"result"} or not isinstance(v, dict) or "content" not in v
        }
        with self._connect() as connection:
            connection.execute(
                "UPDATE task_steps SET status=?, approval_id=?, result_json=?, updated_at=? WHERE task_id=? AND step_number=?",
                (status, approval_id, json.dumps(safe), now, task_id, number),
            )

    def get_task_steps(self, task_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT step_number,description,tool_name,arguments_json,status,approval_id,result_json,created_at,updated_at FROM task_steps WHERE task_id=? ORDER BY step_number",
                (task_id,),
            ).fetchall()
        return [
            {
                "step_number": r[0],
                "description": r[1],
                "tool_name": r[2],
                "arguments": json.loads(r[3]),
                "status": r[4],
                "approval_id": r[5],
                "result": json.loads(r[6]) if r[6] else None,
                "created_at": r[7],
                "updated_at": r[8],
            }
            for r in rows
        ]

    @staticmethod
    def _reject_sensitive_content(content: str) -> None:
        """Keep credential-like values out of long-term memory."""
        if is_sensitive_content(content):
            raise ValueError("Credential-like content cannot be stored in memory.")


def is_sensitive_content(content: str) -> bool:
    """Shared conservative check for values that must not enter model context."""
    sensitive_patterns = (
        r"\b(?:api[_ -]?key|password|access[_ -]?token|refresh[_ -]?token|"
        r"session[_ -]?cookie|client[_ -]?secret|private[_ -]?key|secret|token)"
        r"[\"']?\s*[:=]",
        r"\bsk-[A-Za-z0-9_-]{10,}",
        r"-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----",
        r"\bBearer\s+[A-Za-z0-9._~+/-]{8,}",
        r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^@\s]+@",
        r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{10,}",
    )
    return any(re.search(pattern, content, re.IGNORECASE) for pattern in sensitive_patterns)
