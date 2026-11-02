"""Scoped durable work records. No model, file mutation or external transport here."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.agent_api.app.memory import is_sensitive_content


def now():
    return datetime.now(UTC).isoformat()


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=32000)
    kind: Literal["lead", "proposal", "order", "follow_up", "content_draft"]
    status: Literal["draft", "active", "done", "archived"] = "draft"

    @field_validator("title", "content")
    @classmethod
    def safe_text(cls, value):
        if is_sensitive_content(value):
            raise ValueError("Potential credentials are excluded from work records.")
        return value


class RecordCursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filter_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    record_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Record cursor requires a timezone.")
        return value.astimezone(UTC)


class ScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["reminder", "task_review"] = "reminder"
    due_at: datetime
    interval_seconds: int | None = Field(default=None, ge=60, le=31536000)
    task_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @field_validator("due_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Schedules require an explicit timezone.")
        return value.astimezone(UTC)

    @field_validator("title")
    @classmethod
    def safe_title(cls, value):
        if is_sensitive_content(value):
            raise ValueError("Potential credentials are excluded from reminders.")
        return value


class ScheduleContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RescheduleInput(ScheduleContract):
    due_at: datetime
    interval_seconds: int | None = Field(default=None, ge=60, le=31536000)

    @field_validator("due_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Reminder timing requires an explicit timezone.")
        return value.astimezone(UTC)


def schedule_contract(row):
    item = dict(row)
    item.pop("contract_sha256", None)
    return hashlib.sha256(
        json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class WorkspaceRecords:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list_records(self, project_id):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM work_records WHERE project_id=? ORDER BY updated_at DESC LIMIT 100",
                    (project_id,),
                )
            ]

    def record_page(self, project_id, *, kind=None, status=None, query="", cursor=None):
        if kind not in {
            None,
            "lead",
            "proposal",
            "order",
            "follow_up",
            "content_draft",
        } or status not in {None, "draft", "active", "done", "archived"}:
            raise ValueError("Unsupported work record filter.")
        if not isinstance(query, str) or len(query) > 200 or is_sensitive_content(query):
            raise ValueError("Search text is oversized or contains potential credentials.")
        query = query.strip()
        filters = {"project_id": project_id, "kind": kind, "status": status, "query": query}
        binding = hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()
        clauses, arguments = ["project_id=?"], [project_id]
        if kind is not None:
            clauses.append("kind=?")
            arguments.append(kind)
        if status is not None:
            clauses.append("status=?")
            arguments.append(status)
        if query:
            clauses.append("(instr(lower(title),lower(?))>0 OR instr(lower(content),lower(?))>0)")
            arguments.extend([query, query])
        if cursor is not None:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1000:
                raise ValueError("Record cursor is invalid.")
            try:
                decoded = base64.b64decode(cursor, altchars=b"-_", validate=True)
                selected = RecordCursor.model_validate_json(decoded)
            except (ValueError, TypeError) as error:
                raise ValueError("Record cursor is invalid.") from error
            if selected.filter_sha256 != binding:
                raise ValueError("Record scope or filters changed; restart from the first page.")
            timestamp = selected.updated_at.isoformat()
            clauses.append("(updated_at<? OR (updated_at=? AND record_id<?))")
            arguments.extend([timestamp, timestamp, selected.record_id])
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM work_records WHERE "
                + " AND ".join(clauses)
                + " ORDER BY updated_at DESC,record_id DESC LIMIT 51",
                arguments,
            ).fetchall()
        items = [dict(row) for row in rows[:50]]
        next_cursor = None
        if len(rows) > 50:
            last = items[-1]
            token = RecordCursor(
                filter_sha256=binding, record_id=last["record_id"], updated_at=last["updated_at"]
            )
            next_cursor = base64.urlsafe_b64encode(token.model_dump_json().encode()).decode("ascii")
        return {
            "items": items,
            "next_cursor": next_cursor,
            "limit": 50,
            "consistency": "Pages reflect current records; concurrent edits can reorder results. Refresh to see records updated between pages.",
        }

    def save_record(self, project_id, value, record_id=None, expected_revision=None):
        identifier, timestamp = record_id or uuid.uuid4().hex, now()
        with self.connect() as db:
            if record_id:
                changed = db.execute(
                    "UPDATE work_records SET title=?,content=?,kind=?,status=?,revision=revision+1,updated_at=? WHERE record_id=? AND project_id=? AND revision=?",
                    (
                        value.title,
                        value.content,
                        value.kind,
                        value.status,
                        timestamp,
                        record_id,
                        project_id,
                        expected_revision,
                    ),
                ).rowcount
                if changed != 1:
                    raise ValueError(
                        "Record changed or is outside this scope; refresh before editing."
                    )
            else:
                db.execute(
                    "INSERT INTO work_records VALUES (?,?,?,?,?,?,1,?,?)",
                    (
                        identifier,
                        project_id,
                        value.kind,
                        value.title,
                        value.content,
                        value.status,
                        timestamp,
                        timestamp,
                    ),
                )
            return dict(
                db.execute(
                    "SELECT * FROM work_records WHERE record_id=? AND project_id=?",
                    (identifier, project_id),
                ).fetchone()
            )

    def ask(self, project_id, question, task_id=None):
        if not question.strip() or len(question) > 2000 or is_sensitive_content(question):
            raise ValueError("Question is empty, oversized or contains potential credentials.")
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute(
                "INSERT INTO decision_items VALUES (?,?,?,?,?,?,?,?)",
                (identifier, project_id, task_id, question, "pending", None, now(), None),
            )
        return identifier

    def decisions(self, project_id):
        with self.connect() as db:
            items = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM decision_items WHERE project_id=? ORDER BY created_at DESC LIMIT 100",
                    (project_id,),
                )
            ]
        for item in items:
            item["question_sha256"] = hashlib.sha256(item["question"].encode()).hexdigest()
        return items

    def dismiss_decision(self, project_id, decision_id, expected_question_sha256):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT question,status FROM decision_items WHERE decision_id=? AND project_id=?",
                (decision_id, project_id),
            ).fetchone()
            if (
                row is None
                or row["status"] != "pending"
                or hashlib.sha256(row["question"].encode()).hexdigest() != expected_question_sha256
            ):
                raise ValueError(
                    "Question changed, was answered/dismissed or is outside this scope; refresh first."
                )
            db.execute(
                "UPDATE decision_items SET status='dismissed',answered_at=? WHERE decision_id=? AND project_id=? AND status='pending'",
                (now(), decision_id, project_id),
            )
        return {
            "status": "dismissed",
            "decision_id": decision_id,
            "execution": "No task resumed or cancelled; question history retained.",
        }

    def answer(self, project_id, decision_id, answer):
        if not answer.strip() or len(answer) > 4000 or is_sensitive_content(answer):
            raise ValueError("Answer is empty, oversized or contains potential credentials.")
        with self.connect() as db:
            if (
                db.execute(
                    "UPDATE decision_items SET status='answered',answer=?,answered_at=? WHERE decision_id=? AND project_id=? AND status='pending'",
                    (answer, now(), decision_id, project_id),
                ).rowcount
                != 1
            ):
                raise ValueError("Decision is closed or outside this scope.")

    def schedule(self, project_id, value):
        if (value.kind == "task_review") != bool(value.task_id):
            raise ValueError(
                "Task-review reminders require an exact task; ordinary reminders do not."
            )
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            if value.task_id:
                row = db.execute(
                    "SELECT t.task_id FROM tasks t LEFT JOIN task_scopes s ON s.task_id=t.task_id WHERE t.task_id=? AND COALESCE(s.project_id,'')=?",
                    (value.task_id, project_id),
                ).fetchone()
                if row is None:
                    raise ValueError("Task is unavailable in this scope.")
            db.execute(
                "INSERT INTO durable_schedules VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    project_id,
                    value.title,
                    value.kind,
                    json.dumps({"task_id": value.task_id}),
                    value.due_at.isoformat(),
                    value.interval_seconds,
                    "scheduled",
                    now(),
                ),
            )
        return identifier

    def schedules(self, project_id):
        with self.connect() as db:
            items = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM durable_schedules WHERE project_id=? ORDER BY next_due LIMIT 100",
                    (project_id,),
                )
            ]
        for item in items:
            item["contract_sha256"] = schedule_contract(item)
        return items

    def reschedule(self, project_id, schedule_id, value):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM durable_schedules WHERE schedule_id=? AND project_id=?",
                (schedule_id, project_id),
            ).fetchone()
            if row is None:
                raise KeyError(schedule_id)
            if (
                row["status"] != "scheduled"
                or schedule_contract(row) != value.expected_contract_sha256
            ):
                raise ValueError(
                    "Reminder changed, fired or was cancelled; refresh before rescheduling."
                )
            latest = db.execute(
                "SELECT MAX(due_at) FROM schedule_occurrences WHERE schedule_id=?", (schedule_id,)
            ).fetchone()[0]
            if latest and value.due_at <= datetime.fromisoformat(latest):
                raise ValueError(
                    "Choose a due time after the last recorded occurrence; history cannot be replayed."
                )
            db.execute(
                "UPDATE durable_schedules SET next_due=?,interval_seconds=? WHERE schedule_id=? AND project_id=?",
                (value.due_at.isoformat(), value.interval_seconds, schedule_id, project_id),
            )
            result = dict(
                db.execute(
                    "SELECT * FROM durable_schedules WHERE schedule_id=? AND project_id=?",
                    (schedule_id, project_id),
                ).fetchone()
            )
        result["contract_sha256"] = schedule_contract(result)
        return result

    def cancel_schedule(self, project_id, schedule_id, expected_contract_sha256=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if expected_contract_sha256 is not None:
                row = db.execute(
                    "SELECT * FROM durable_schedules WHERE schedule_id=? AND project_id=?",
                    (schedule_id, project_id),
                ).fetchone()
                if row is None:
                    raise KeyError(schedule_id)
                if schedule_contract(row) != expected_contract_sha256:
                    raise ValueError("Reminder changed; refresh before cancelling.")
            if (
                db.execute(
                    "UPDATE durable_schedules SET status='cancelled' WHERE schedule_id=? AND project_id=?",
                    (schedule_id, project_id),
                ).rowcount
                != 1
            ):
                raise KeyError(schedule_id)
            db.execute(
                "UPDATE schedule_occurrences SET status='cancelled' WHERE schedule_id=? AND project_id=? AND status IN ('pending','waiting_for_review')",
                (schedule_id, project_id),
            )

    def materialize_due(self, timestamp=None):
        """One summarized overdue occurrence per schedule, no action execution.

        The transaction makes cancellation and duplicate ticks serialize. A
        repeated schedule advances past *now*, rather than emitting a storm of
        missed notifications. Consequential work stays waiting for review.
        """
        instant = timestamp or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError("Scheduler clock must be timezone aware.")
        instant = instant.astimezone(UTC)
        created = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM durable_schedules WHERE status='scheduled' AND next_due<=? ORDER BY next_due LIMIT 100",
                (instant.isoformat(),),
            ).fetchall()
            for row in rows:
                due = datetime.fromisoformat(row["next_due"])
                identifier = uuid.uuid5(
                    uuid.NAMESPACE_URL, row["schedule_id"] + ":" + due.isoformat()
                ).hex
                status = "pending" if row["kind"] == "reminder" else "waiting_for_review"
                if db.execute(
                    "INSERT OR IGNORE INTO schedule_occurrences VALUES (?,?,?,?,?,?)",
                    (
                        identifier,
                        row["schedule_id"],
                        row["project_id"],
                        due.isoformat(),
                        status,
                        instant.isoformat(),
                    ),
                ).rowcount:
                    created.append((row["project_id"], identifier))
                interval = row["interval_seconds"]
                if interval is None:
                    db.execute(
                        "UPDATE durable_schedules SET status='delivered' WHERE schedule_id=?",
                        (row["schedule_id"],),
                    )
                else:
                    missed = int((instant - due).total_seconds() // interval) + 1
                    next_due = due + timedelta(seconds=missed * interval)
                    db.execute(
                        "UPDATE durable_schedules SET next_due=? WHERE schedule_id=?",
                        (next_due.isoformat(), row["schedule_id"]),
                    )
        return created

    def occurrences(self, project_id):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT o.*,s.title,s.kind FROM schedule_occurrences o JOIN durable_schedules s ON s.schedule_id=o.schedule_id WHERE o.project_id=? ORDER BY o.created_at DESC LIMIT 100",
                    (project_id,),
                )
            ]

    def acknowledge(self, project_id, occurrence_id):
        with self.connect() as db:
            if (
                db.execute(
                    "UPDATE schedule_occurrences SET status='acknowledged' WHERE occurrence_id=? AND project_id=? AND status IN ('pending','waiting_for_review')",
                    (occurrence_id, project_id),
                ).rowcount
                != 1
            ):
                raise ValueError("Occurrence is no longer actionable.")

    def briefing(self, project_id, start, end):
        """A deterministic scoped digest, without model claims or action dispatch."""
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Briefing boundaries require explicit timezones.")
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if not timedelta(0) < end - start <= timedelta(hours=48):
            raise ValueError("Briefing interval must be positive and at most 48 hours.")
        with self.connect() as db:
            decisions = [
                dict(row)
                for row in db.execute(
                    "SELECT decision_id,question,task_id,created_at FROM decision_items WHERE project_id=? AND status='pending' ORDER BY created_at LIMIT 20",
                    (project_id,),
                )
            ]
            reminders = [
                dict(row)
                for row in db.execute(
                    "SELECT o.occurrence_id,o.due_at,o.status,s.title,s.kind FROM schedule_occurrences o JOIN durable_schedules s ON s.schedule_id=o.schedule_id WHERE o.project_id=? AND o.due_at>=? AND o.due_at<? AND o.status IN ('pending','waiting_for_review') ORDER BY o.due_at LIMIT 50",
                    (project_id, start.isoformat(), end.isoformat()),
                )
            ]
            upcoming = [
                dict(row)
                for row in db.execute(
                    "SELECT schedule_id,title,kind,next_due FROM durable_schedules WHERE project_id=? AND status='scheduled' AND next_due>=? AND next_due<? ORDER BY next_due LIMIT 50",
                    (project_id, start.isoformat(), end.isoformat()),
                )
            ]
            work = [
                dict(row)
                for row in db.execute(
                    "SELECT record_id,title,kind,status,updated_at FROM work_records WHERE project_id=? AND status IN ('draft','active') ORDER BY updated_at DESC LIMIT 20",
                    (project_id,),
                )
            ]
            agents = [
                dict(row)
                for row in db.execute(
                    "SELECT job_id,role,status,updated_at FROM agent_jobs WHERE project_id=? AND status IN ('queued','interrupted','waiting_for_approval','failed') ORDER BY updated_at DESC LIMIT 20",
                    (project_id,),
                )
            ]
        return {
            "interval": {"start": start.isoformat(), "end": end.isoformat()},
            "questions": decisions,
            "due_notifications": reminders,
            "upcoming_reminders": upcoming,
            "open_work": work,
            "agents_needing_attention": agents,
            "limits": {
                "questions": 20,
                "notifications": 50,
                "upcoming": 50,
                "work": 20,
                "agents": 20,
            },
            "source": "bounded database records; no model inference or automatic actions",
        }
