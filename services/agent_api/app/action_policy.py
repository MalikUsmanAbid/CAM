"""Backend-owned action classification and durable policy revisions."""

import json
import sqlite3
from contextlib import closing
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ActionCategory(StrEnum):
    READ = "read_only"
    EDIT = "local_edit"
    COMMAND = "command"
    ACCOUNT = "account_write"
    PUBLISH = "publish"
    FINANCIAL = "financial"
    DESTRUCTIVE = "destructive"


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocked_categories: list[ActionCategory] = Field(default_factory=list, max_length=7)
    require_read_approval: bool = False


def category_for(tool):
    if tool.category is not None:
        return ActionCategory(tool.category)
    if tool.name in {"write_file", "create_directory"}:
        return ActionCategory.EDIT
    if str(tool.risk) == "low":
        return ActionCategory.READ
    return ActionCategory.ACCOUNT


class ActionPolicy:
    def __init__(self, path):
        self.path = path

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT revision,blocked_json,read_approval FROM action_policy WHERE id=1").fetchone()
            stopped = db.execute("SELECT value FROM runtime_flags WHERE name='emergency_stop'").fetchone()
        if row is None:
            raise ValueError("Action policy is unavailable.")
        return {"revision": row[0], "blocked_categories": json.loads(row[1]), "require_read_approval": bool(row[2]), "emergency_stop": bool(stopped and stopped[0] == "1")}

    def update(self, policy):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE action_policy SET revision=revision+1,blocked_json=?,read_approval=? WHERE id=1",
                       (json.dumps(sorted(set(policy.blocked_categories))), int(policy.require_read_approval)))
        return self.snapshot()

    def denied(self, category):
        policy = self.snapshot()
        return policy["emergency_stop"] or category in policy["blocked_categories"]

    def allowed(self, category, approved=False):
        policy = self.snapshot()
        return not policy["emergency_stop"] and category not in policy["blocked_categories"] and (approved or (category == ActionCategory.READ and not policy["require_read_approval"]))

    def emergency_stop(self, stopped):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO runtime_flags VALUES ('emergency_stop',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", ("1" if stopped else "0",))
            db.execute("UPDATE action_policy SET revision=revision+1 WHERE id=1")
        return self.snapshot()
