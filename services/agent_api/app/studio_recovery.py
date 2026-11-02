"""Freshly approved recovery of known checkpoint states, never blind replay.

Only a verified effect can be finalized, or a retained original moved back into
an absent public path. Changed public files and unsupported states fail closed.
Original checkpoint inodes/streams are retained; recovery never deletes them.
"""

import ctypes
import difflib
import json
import os
import re
import uuid
from contextlib import ExitStack

from pydantic import Field

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.coding_changes import ChangeInput, digest
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.tools import Tool


class RecoveryInput(ChangeInput):
    observation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def phase(status):
    if status == "applying" or re.fullmatch(r"recovery_applying_[a-f0-9]{32}", status):
        return "applying"
    if status == "rolling_back" or re.fullmatch(r"recovery_rolling_back_[a-f0-9]{32}", status):
        return "rolling_back"
    raise ValueError("Only interrupted changes require recovery.")


def checkpoint_path(changes, row, target):
    return target.parent / (".jarvis-backup-" + row["change_id"] + ("-apply" if phase(row["status"]) == "applying" else "-rollback"))


def opened_snapshot(changes, kernel, stack, path):
    from services.agent_api.app.native_replace import _open
    if path.is_symlink() or path.is_junction():
        raise ValueError("Linked recovery files are unsupported.")
    if not path.exists():
        return {"exists": False}, None
    file = stack.enter_context(_open(kernel, path))
    changes.files.verify_opened(file.fileno(), path)
    stat = os.fstat(file.fileno())
    if stat.st_size > 64_000:
        raise ValueError("Recovery file exceeds the checkpoint limit.")
    content = file.read(64_001)
    import msvcrt
    streams = ctypes.create_string_buffer(65536)
    if not kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(file.fileno()), 7, streams, len(streams)):
        raise ValueError("Cannot inspect recovery streams.")
    name_size = ctypes.c_uint32.from_buffer(streams, 4).value
    if ctypes.c_uint32.from_buffer(streams).value or streams.raw[24:24 + name_size].decode("utf-16-le") != "::$DATA":
        raise ValueError("Additional streams require manual recovery; retained originals are preserved.")
    return {"exists": True, "sha256": digest(content), "device": str(stat.st_dev), "inode": str(stat.st_ino)}, file


def describe(changes, row, target, current, retained):
    applying = phase(row["status"]) == "applying"
    created = row["created_id"] is not None
    original_sha = row["before_sha"] if applying else row["after_sha"]
    final_sha = row["after_sha"] if applying else row["before_sha"]
    original_status, final_status = ("proposed", "applied") if applying else ("applied", "rolled_back")
    owned = {"device": row["device"], "inode": row["inode"]}

    def own(snapshot):
        return not created or (snapshot.get("device") == owned["device"] and snapshot.get("inode") == owned["inode"] and owned["inode"] is not None)

    mode = "unsupported"
    status = None
    if created and not applying and not current["exists"] and retained.get("sha256") == original_sha and own(retained):
        mode, status = "finalize", final_status
    elif not (created and not applying) and current.get("sha256") == final_sha and own(current) and (not retained["exists"] or retained.get("sha256") == original_sha):
        mode, status = "finalize", final_status
    elif not current["exists"] and retained.get("sha256") == original_sha and own(retained):
        mode, status = "restore_missing", original_status
    elif not retained["exists"] and ((created and applying and not current["exists"]) or (current.get("sha256") == original_sha and own(current))):
        mode, status = "finalize", original_status
    contract = {"project_id": changes.project_id, "root": row["root"], "change_id": row["change_id"], "path": row["path"], "proposal_sha256": row["proposal_sha"], "status": row["status"], "current": current, "retained": retained, "mode": mode, "final_status": status, "owned": owned}
    observation = digest(json.dumps(contract, sort_keys=True).encode())
    return {**contract, "observation_sha256": observation, "retained_checkpoint": checkpoint_path(changes, row, target).relative_to(changes.files.guard.root).as_posix(), "diff": "".join(difflib.unified_diff([], (row["before_content"] if applying else row["after_content"]).decode().splitlines(keepends=True), fromfile="missing/" + row["path"], tofile="restored/" + row["path"])) if mode == "restore_missing" else "No file-content mutation; reconcile verified checkpoint state."}


def inspect_recovery(changes, value):
    if os.name != "nt":
        raise ValueError("Verified recovery requires Windows.")
    from services.agent_api.app.native_replace import _kernel
    row = changes._load(value)
    target = changes.files._safe(value.path)
    backup = checkpoint_path(changes, row, target)
    with changes.files._scan(target.parent, writable=True), ExitStack() as stack:
        kernel = _kernel()
        current, _ = opened_snapshot(changes, kernel, stack, target)
        retained, _ = opened_snapshot(changes, kernel, stack, backup)
        changes.files.guard.resolve(value.path)
        return describe(changes, row, target, current, retained)


def recover(changes, value):
    if os.name != "nt":
        raise ValueError("Verified recovery requires Windows.")
    from services.agent_api.app.native_replace import _kernel, rename_replacement
    row = changes._load(value)
    target = changes.files._safe(value.path)
    backup = checkpoint_path(changes, row, target)
    with changes.files._scan(target.parent, writable=True) as pinned, ExitStack() as stack:
        kernel = _kernel()
        current, _ = opened_snapshot(changes, kernel, stack, target)
        retained, source = opened_snapshot(changes, kernel, stack, backup)
        observed = describe(changes, row, target, current, retained)
        if observed["observation_sha256"] != value.observation_sha256 or observed["mode"] == "unsupported":
            raise ValueError("Recovery observation changed or is unsupported; inspect and approve again.")
        changes.files.guard.resolve(value.path)
        working = "recovery_" + phase(row["status"]) + "_" + uuid.uuid4().hex
        changes._claim(value.change_id, row["status"], working)
        changes.files.guard.resolve(value.path)
        if observed["mode"] == "restore_missing":
            # The immutable proposal determines this one retained path. The
            # opened handle and fresh approved observation bind its identity.
            rename_replacement(kernel, source, target, False, pinned.handle)
            changes.files.verify_opened(source.fileno(), target)
            source.seek(0)
            if digest(source.read(64_001)) != retained["sha256"]:
                raise RuntimeError("Restored checkpoint is uncertain; inspect again.")
        else:
            # Absent paths cannot be locked: recheck after durable claim before
            # finalizing; a later public file never becomes a completed rollback.
            checked, _ = opened_snapshot(changes, kernel, stack, target) if not current["exists"] else (current, None)
            if checked != current:
                raise ValueError("A later public file exists; recovery is uncertain.")
        changes.files.guard.resolve(value.path)
        changes._claim(value.change_id, working, observed["final_status"])
        return {"change_id": value.change_id, "path": value.path, "status": observed["final_status"], "mode": observed["mode"], "retained_checkpoint": observed["retained_checkpoint"] if observed["mode"] == "finalize" and retained["exists"] else None}


def register_recovery(registry, changes):
    async def inspect(value):
        return inspect_recovery(changes, value)

    async def recovery(value):
        return recover(changes, value)

    registry.register(Tool("studio_inspect_recovery", "Inspect actual files for an interrupted checkpoint; never retries it.", ChangeInput, RiskLevel.LOW, 5, inspect, resolve_destination=lambda args: changes.files.guard.resolve(args["path"]), category=ActionCategory.READ))
    registry.register(Tool("studio_recover", "Recover only a freshly inspected interrupted checkpoint, preserving later user files.", RecoveryInput, RiskLevel.HIGH, 5, recovery, resolve_destination=lambda args: changes.files.guard.resolve(args["path"]), category=ActionCategory.EDIT, preview_details=lambda args: {"recovery": inspect_recovery(changes, RecoveryInput(**args))}))
