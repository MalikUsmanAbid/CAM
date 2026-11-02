"""Additive versioned foundation storage; no implicit migration of user databases."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path


def redact(value: str) -> str:
    value = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(\b(?:password|token|api[_-]?key|secret)\b[\"']?\s*[=:]\s*[\"']?)([^\s,;\"'}]+)", r"\1[REDACTED]", value)
    return re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)[:2000]


class SecureStore:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def migrate(self, *, allow_existing=False):
        from services.agent_api.app.migrations import initialize_database

        initialize_database(self.path, allow_existing=allow_existing)

    def register_project(self, name: str, root: str):
        path = Path(root).resolve(strict=True)
        if not path.is_dir() or path == Path(path.anchor):
            raise ValueError("Choose an existing project directory, not a drive root.")
        name = name.strip()
        if not name:
            raise ValueError("Project name is required.")
        canonical = os.path.normcase(str(path))
        project = {"project_id": uuid.uuid4().hex, "name": name, "root": canonical, "created_at": datetime.now(UTC).isoformat()}
        with closing(sqlite3.connect(self.path)) as connection, connection:
            existing = connection.execute("SELECT project_id,name,root,created_at FROM projects WHERE root=?", (canonical,)).fetchone()
            if existing:
                return dict(zip(project, existing, strict=True))
            connection.execute("INSERT INTO projects VALUES (?,?,?,?)", tuple(project.values()))
        return project

    def projects(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return [dict(zip(("project_id", "name", "root", "created_at"), row, strict=True))
                    for row in connection.execute("SELECT * FROM projects ORDER BY created_at")]

    def events(self, after: int, project_id=None, *, descending=False, limit=100):
        with closing(sqlite3.connect(self.path)) as connection:
            order = "DESC" if descending else "ASC"
            rows = connection.execute("SELECT e.id,e.tool_name,e.status,e.detail,e.created_at,s.task_id FROM task_events e LEFT JOIN event_scopes s ON e.id=s.event_id WHERE e.id>? AND s.project_id IS ? ORDER BY e.id " + order + " LIMIT ?", (after, project_id, max(1, min(limit, 100)))).fetchall()
        def event_type(status):
            if status.startswith("task_"):
                return "task." + status[5:]
            if status.startswith("approval_"):
                return "approval." + status[9:]
            return "tool." + status

        return [{"id": row[0], "version": 1, "type": event_type(row[2]), "project_id": project_id, "task_id": row[5], "tool_name": row[1], "status": row[2], "detail": redact(row[3]), "timestamp": row[4]} for row in rows]

    def event_payload(self, after: int) -> str:
        return "".join(f"id: {event['id']}\nevent: activity\ndata: {json.dumps(event)}\n\n" for event in self.events(after))
