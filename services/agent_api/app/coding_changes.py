"""Exact existing-file proposals with durable checkpoints and selective rollback.

Writes require native Windows exclusive sharing. An interrupted `applying` or
`rolling_back` record is an uncertain outcome, never an automatically retried action.
"""

import difflib
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import PurePosixPath

from pydantic import Field

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.project_files import ProjectFiles
from services.agent_api.app.tools import EmptyInput, Tool, ToolInput


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ProposeInput(ToolInput):
    path: str = Field(min_length=1, max_length=500)
    before_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: str = Field(max_length=64_000)


class ChangeInput(ToolInput):
    change_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    path: str = Field(min_length=1, max_length=500)
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CreateInput(ToolInput):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=64_000)


class CodingChanges:
    def __init__(self, root, database, project_id):
        self.files = ProjectFiles(root)
        self.database = database
        self.project_id = project_id

    def propose(self, value, *, create=False):
        from services.agent_api.app.memory import is_sensitive_content

        if create:
            target = self.files._safe(value.path)
            if target.exists() or not target.parent.is_dir():
                raise ValueError("New-file proposals require an absent file in an existing directory.")
            source = {"path": target.relative_to(self.files.guard.root).as_posix(), "content": ""}
        else:
            source = self.files.read(value.path)
        before = source["content"].encode()
        after = value.content.encode()
        if not create and digest(before) != value.before_sha256:
            raise ValueError("File changed since inspection; read it and propose again.")
        if len(after) > 64_000 or is_sensitive_content(value.content) or (not create and before == after):
            raise ValueError("Proposal is unchanged, oversized or contains potential credentials.")
        path = source["path"]
        contract = {
            "project_id": self.project_id,
            "root": str(self.files.guard.root),
            "path": path,
            "before": digest(before),
            "after": digest(after),
        }
        if create:
            contract["before_exists"] = False
        proposal = digest(json.dumps(contract, sort_keys=True).encode())
        identifier = uuid.uuid4().hex
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "INSERT INTO coding_changes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    self.project_id,
                    str(self.files.guard.root),
                    path,
                    digest(before),
                    before,
                    digest(after),
                    after,
                    proposal,
                    "proposed",
                    datetime.now(UTC).isoformat(),
                ),
            )
            if create:
                db.execute("INSERT INTO coding_created_files VALUES (?,NULL,NULL)", (identifier,))
        return {
            "change_id": identifier,
            "path": path,
            "proposal_sha256": proposal,
            "before_sha256": digest(before),
            "after_sha256": digest(after),
            "diff": "".join(
                difflib.unified_diff(
                    source["content"].splitlines(keepends=True),
                    value.content.splitlines(keepends=True),
                    fromfile="a/" + path,
                    tofile="b/" + path,
                )
            ),
            "status": "proposed",
            "before_exists": not create,
        }

    def _load(self, value):
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT c.*, n.change_id AS created_id, n.device, n.inode FROM coding_changes c LEFT JOIN coding_created_files n ON c.change_id=n.change_id WHERE c.change_id=? AND c.project_id=?",
                (value.change_id, self.project_id),
            ).fetchone()
        if (
            row is None
            or row["path"] != value.path
            or row["root"] != str(self.files.guard.root)
            or row["proposal_sha"] != value.proposal_sha256
        ):
            raise ValueError("Change does not match this exact project and proposal.")
        contract = {
            "project_id": self.project_id,
            "root": row["root"],
            "path": row["path"],
            "before": row["before_sha"],
            "after": row["after_sha"],
        }
        if row["created_id"] is not None:
            contract["before_exists"] = False
        if (
            digest(row["before_content"]) != row["before_sha"]
            or digest(row["after_content"]) != row["after_sha"]
            or digest(json.dumps(contract, sort_keys=True).encode()) != row["proposal_sha"]
        ):
            raise ValueError("Checkpoint contract is invalid.")
        return row

    def _claim(self, identifier, before, after, identity=None):
        with closing(sqlite3.connect(self.database)) as db, db:
            if (
                db.execute(
                    "UPDATE coding_changes SET status=? WHERE change_id=? AND project_id=? AND status=?",
                    (after, identifier, self.project_id, before),
                ).rowcount
                != 1
            ):
                raise ValueError(
                    "Change already claimed, stale or uncertain; inspect before recovery."
                )
            if identity is not None:
                db.execute("UPDATE coding_created_files SET device=?,inode=? WHERE change_id=?", (*(str(part) for part in identity), identifier))

    def preview(self, arguments, rollback=False):
        row = self._load(ChangeInput(**arguments))
        before = row["after_content"] if rollback else row["before_content"]
        after = row["before_content"] if rollback else row["after_content"]
        return {"diff": "".join(difflib.unified_diff(before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True), fromfile="a/" + row["path"], tofile="b/" + row["path"])), "before_sha256": digest(before), "after_sha256": digest(after), "checkpoint_id": row["change_id"], "before_exists": row["created_id"] is None, "deletes_created_file": rollback and row["created_id"] is not None}

    def inspect(self, value):
        row = self._load(value)
        if row["status"] not in {"proposed", "applied"}:
            raise ValueError("Only pending or applied checkpoints can reopen for review.")
        created = row["created_id"] is not None
        target = self.files._safe(value.path)
        if created and row["status"] == "proposed":
            if target.exists():
                raise ValueError("A later file exists; prepare a fresh proposal.")
        else:
            expected = row["before_sha"] if row["status"] == "proposed" else row["after_sha"]
            if self.files.read(value.path)["sha256"] != expected:
                raise ValueError("Checkpoint is stale; inspect the actual file before a fresh proposal.")
            if created:
                identity = target.stat()
                if (str(identity.st_dev), str(identity.st_ino)) != (row["device"], row["inode"]):
                    raise ValueError("Created-file ownership changed; later files are preserved.")
        return {**self.preview(value.model_dump()), "change_id": row["change_id"], "path": row["path"], "proposal_sha256": row["proposal_sha"], "status": row["status"], "before_content": row["before_content"].decode(), "content": row["after_content"].decode()}

    def list(self):
        self.files.guard.resolve(".", True)
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT change_id,path,proposal_sha,after_sha,status,created_at FROM coding_changes WHERE project_id=? AND root=? ORDER BY created_at DESC LIMIT 20",
                (self.project_id, str(self.files.guard.root)),
            ).fetchall()
        return {
            "changes": [
                {
                    "change_id": row["change_id"],
                    "path": row["path"],
                    "proposal_sha256": row["proposal_sha"],
                    "after_sha256": row["after_sha"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "checkpoint_locations": [str(PurePosixPath(row["path"]).parent / (".jarvis-backup-" + row["change_id"] + suffix)) for suffix in ("-apply", "-rollback")],
                }
                for row in rows
            ]
        }

    def apply(self, value, rollback=False, created_rollback=False):
        if os.name != "nt":
            raise ValueError("Verified checkpoint writes currently require Windows.")
        row = self._load(value)
        created = row["created_id"] is not None
        if rollback and created != created_rollback:
            raise ValueError("Created-file removal requires its separate destructive rollback approval.")
        initial, working, final = (
            ("applied", "rolling_back", "rolled_back")
            if rollback
            else ("proposed", "applying", "applied")
        )
        if row["status"] != initial:
            raise ValueError("Change is stale or already executed.")
        target = self.files._safe(value.path)
        expected = row["after_content"] if rollback else row["before_content"]
        replacement = row["before_content"] if rollback else row["after_content"]
        retirement = target.parent / (".jarvis-backup-" + value.change_id + ("-rollback" if rollback else "-apply"))
        from services.agent_api.app.native_replace import remove_created, replace
        if created and rollback:
            if row["device"] is None or row["inode"] is None:
                raise ValueError("Created-file ownership is uncertain; inspect before recovery.")
            remove_created(self.files, target, expected, (int(row["device"]), int(row["inode"])), before_commit=lambda: self._claim(value.change_id, initial, working), retirement=retirement)
            identity = None
        else:
            if created and target.exists():
                raise ValueError("A later file exists; new-file changes never overwrite it.")
            staged = []
            identity = replace(self.files, target, replacement, expected=expected, create_new=created,
                               before_commit=(lambda: self._claim(value.change_id, initial, working, identity=staged[0])) if created else (lambda: self._claim(value.change_id, initial, working)),
                               retirement=retirement, staged_identity=staged.append)
        self._claim(value.change_id, working, final, identity=identity if created else None)
        return {
            "change_id": value.change_id,
            "path": value.path,
            "status": final,
            "sha256": digest(replacement),
            "retained_checkpoint": retirement.relative_to(self.files.guard.root).as_posix() if rollback or not created else None,
        }


def register_coding_changes(registry, database, project_id, provider=None, model_timeout=15):
    changes = CodingChanges(registry.workspace_root, database, project_id)
    from services.agent_api.app.studio_recovery import register_recovery
    register_recovery(registry, changes)
    from services.agent_api.app.studio_ai import register_ai_proposals
    register_ai_proposals(registry, changes, provider, model_timeout)

    async def propose(value):
        return changes.propose(value)

    async def apply(value):
        return changes.apply(value)

    async def rollback(value):
        return changes.apply(value, rollback=True)

    async def propose_create(value):
        return changes.propose(value, create=True)

    async def rollback_create(value):
        return changes.apply(value, rollback=True, created_rollback=True)

    async def listing(_):
        return changes.list()

    async def inspection(value):
        return changes.inspect(value)

    registry.register(
        Tool(
            "studio_changes",
            "Lists this project's recent checkpoint identities and recovery states.",
            EmptyInput,
            RiskLevel.LOW,
            3,
            listing,
            resolve_destination=lambda _: changes.files.guard.resolve("."),
        )
    )

    for name, model, risk, handler, category in (
        ("studio_propose", ProposeInput, RiskLevel.LOW, propose, ActionCategory.READ),
        ("studio_propose_create", CreateInput, RiskLevel.LOW, propose_create, ActionCategory.READ),
        ("studio_inspect_change", ChangeInput, RiskLevel.LOW, inspection, ActionCategory.READ),
        ("studio_apply", ChangeInput, RiskLevel.MEDIUM, apply, ActionCategory.EDIT),
        ("studio_rollback", ChangeInput, RiskLevel.MEDIUM, rollback, ActionCategory.EDIT),
        ("studio_rollback_create", ChangeInput, RiskLevel.HIGH, rollback_create, ActionCategory.DESTRUCTIVE),
    ):
        registry.register(
            Tool(
                name,
                "Prepares a hash-bound existing-file diff."
                if name == "studio_propose"
                else "Applies the exact checkpoint change; stale files are rejected.",
                model,
                risk,
                5,
                handler,
                resolve_destination=lambda args: changes.files.guard.resolve(args["path"]),
                category=category,
                preview_details=(lambda args, rollback=name in {"studio_rollback", "studio_rollback_create"}: changes.preview(args, rollback)) if name in {"studio_apply", "studio_rollback", "studio_rollback_create"} else None,
            )
        )
