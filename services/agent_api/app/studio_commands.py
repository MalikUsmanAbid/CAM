"""Project-bound prepared commands. Approval authorizes code, not a sandbox."""

import ctypes
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import ExitStack, closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from services.agent_api.app.action_policy import ActionCategory, ActionPolicy
from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.project_files import ProjectFiles
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.tools import (
    EmptyInput,
    Tool,
    ToolExecutionOutcome,
    ToolInput,
    _approved_revision,
    _PolicyDenied,
)
from services.agent_api.app.windows_commands import run_process


class PrepareCommand(ToolInput):
    executable: str = Field(min_length=1, max_length=500)
    arguments: list[str] = Field(default_factory=list, max_length=100)
    cwd: str = Field(default=".", min_length=1, max_length=500)
    timeout_seconds: int = Field(default=60, ge=1, le=120, strict=True)
    output_limit: int = Field(default=64000, ge=1, le=64000, strict=True)


class RunCommand(ToolInput):
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PrepareGit(ToolInput):
    operation: Literal["status", "diff", "log"] = "status"


def contract_hash(contract):
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@contextmanager
def locked_executable(path, *, hash_contents=True, single_link=False):
    if os.name != "nt":
        raise ValueError("Verified commands require Windows.")
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle in {None, ctypes.c_void_p(-1).value}:
        raise ValueError("Executable is unavailable or another writer owns it.")
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, "rb") as file:
        kernel.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not size or size >= len(buffer) or Path(buffer.value.removeprefix("\\\\?\\")).resolve() != path:
            raise ValueError("Opened executable destination changed.")
        if os.fstat(descriptor).st_size > 64 * 1024 * 1024:
            raise ValueError("Executable exceeds the verification limit.")
        if single_link and os.fstat(descriptor).st_nlink != 1:
            raise ValueError("Hard-linked Git metadata is unsupported.")
        digest = hashlib.sha256()
        if hash_contents:
            for block in iter(lambda: file.read(65536), b""):
                digest.update(block)
        yield digest.hexdigest()


class StudioCommands:
    def __init__(self, root, database, project):
        self.files = ProjectFiles(root)
        self.database = database
        self.project = project
        self.policy = ActionPolicy(database)

    def directory(self, relative):
        return self.files.guard.resolve(".", True) if relative == "." else self.files._safe(relative)

    def git_identity(self):
        root = self.files.guard.resolve(".", True)
        metadata = root / ".git"
        if not metadata.is_dir() or metadata.is_symlink() or metadata.is_junction() or metadata.resolve() != metadata:
            raise ValueError("Git inspection requires a repository with local .git metadata; linked worktrees are unsupported.")
        if (metadata / "commondir").exists() or (metadata / "objects" / "info" / "alternates").exists():
            raise ValueError("Shared or alternate Git metadata is unsupported.")
        configuration = self.files._text(metadata / "config", 16000)
        normalized = configuration.removeprefix("\ufeff")
        if (metadata / "config.worktree").exists() or re.search(r"(?im)^\s*\[\s*(?:filter|include|includeif|extensions)\b|^\s*(?:promisor|partialclonefilter)\s*=", normalized):
            raise ValueError("Git filters, includes, worktree configuration and partial clones are unsupported for bounded inspection.")
        identity = metadata.stat()
        return {"device": identity.st_dev, "inode": identity.st_ino, "config_sha256": hashlib.sha256(configuration.encode()).hexdigest()}

    def prepare_git(self, value):
        executable = shutil.which("git.exe")
        if not executable:
            raise ValueError("Git is unavailable. Configure an installed Git executable first.")
        identity = self.git_identity()
        operations = {
            "status": ["status", "--porcelain=v1", "--branch", "--untracked-files=normal", "--ignore-submodules=all"],
            "diff": ["diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--", "."],
            "log": ["log", "-10", "--oneline", "--no-decorate", "--no-show-signature"],
        }
        root = self.files.guard.root
        arguments = ["--no-pager", "--no-optional-locks", "--literal-pathspecs", "--git-dir=" + str(root / ".git"), "--work-tree=" + str(root), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=NUL", *operations[value.operation]]
        return self.prepare(PrepareCommand(executable=executable, arguments=arguments), git_scope={"operation": value.operation, **identity})

    def pin_git_metadata(self, stack):
        # Freeze existing refs, configuration and object routes while Git reads
        # them. Shared, linked or large metadata needs a separate implementation.
        pending = [self.files.guard.root / ".git"]
        entries = 0
        inventory = {}
        while pending:
            directory = pending.pop()
            if directory.is_symlink() or directory.is_junction():
                raise ValueError("Linked Git metadata is unsupported.")
            stack.enter_context(self.files._scan(directory))
            names = set()
            for path in directory.iterdir():
                names.add(path.name)
                entries += 1
                if entries > 2000:
                    raise ValueError("Git metadata exceeds the supported inspection limit.")
                if path.is_symlink() or path.is_junction():
                    raise ValueError("Linked Git metadata is unsupported.")
                if path.is_dir():
                    pending.append(path)
                elif path.is_file():
                    if path.stat().st_nlink != 1:
                        raise ValueError("Hard-linked Git metadata is unsupported.")
                    stack.enter_context(locked_executable(path, hash_contents=False, single_link=True))
                else:
                    raise ValueError("Unsupported Git metadata entry.")
            inventory[directory] = names
        return inventory

    def prepare(self, value, *, git_scope=None, managed_copy=None):
        executable = Path(value.executable)
        if not executable.is_absolute() or executable.suffix.lower() != ".exe":
            raise ValueError("Specify an absolute .exe path; shell scripts need an explicit interpreter command.")
        executable = executable.resolve(strict=True)
        if any(len(arg) > 4000 or "\x00" in arg or is_sensitive_content(arg) for arg in value.arguments):
            raise ValueError("Arguments exceed limits or contain potential credentials.")
        cwd = self.directory(value.cwd)
        if not cwd.is_dir():
            raise ValueError("Command working directory is unavailable.")
        with locked_executable(executable) as digest:
            contract = {**value.model_dump(), "executable": str(executable), "executable_sha256": digest, "cwd": cwd.relative_to(self.files.guard.root).as_posix(), "project_id": self.project, "root": str(self.files.guard.root)}
        if git_scope is not None:
            if self.git_identity() != {key: git_scope[key] for key in ("device", "inode", "config_sha256")}:
                raise ValueError("Git metadata changed while preparing the command.")
            contract["git_scope"] = git_scope
        if managed_copy is not None:
            contract['managed_copy'] = dict(managed_copy)
        identifier = uuid.uuid4().hex
        checksum = contract_hash(contract)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO command_runs VALUES(?,?,?,?,?,?,?,?)", (identifier, self.project, contract["root"], json.dumps(contract), checksum, "prepared", None, datetime.now(UTC).isoformat()))
        return {"run_id": identifier, "contract_sha256": checksum, "command": contract, "status": "prepared"}

    def load(self, value):
        with closing(sqlite3.connect(self.database)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM command_runs WHERE run_id=? AND project_id=?", (value.run_id, self.project)).fetchone()
        if row is None or row["root"] != str(self.files.guard.root) or row["contract_sha"] != value.contract_sha256:
            raise ValueError("Command is not bound to this project and exact contract.")
        contract = json.loads(row["contract_json"])
        if contract_hash(contract) != row["contract_sha"] or contract["root"] != row["root"] or contract["project_id"] != self.project:
            raise ValueError("Command contract changed.")
        return row, contract

    def destination(self, arguments):
        _, contract = self.load(RunCommand(**arguments))
        return self.directory(contract["cwd"])

    def preview(self, arguments):
        row, contract = self.load(RunCommand(**arguments))
        if row["status"] != "prepared":
            raise ValueError("Command already claimed; inspect its recorded outcome.")
        return {"command": contract, "warning": "Approved code can access your user files and network. Process supervision is not a sandbox."}

    def record(self, identifier, status, result=None, expected="running"):
        with closing(sqlite3.connect(self.database)) as db, db:
            if db.execute("UPDATE command_runs SET status=?,result_json=? WHERE run_id=? AND project_id=? AND status=?", (status, json.dumps(result) if result is not None else None, identifier, self.project, expected)).rowcount != 1:
                raise ValueError("Command already claimed or state changed; do not retry uncertain execution.")

    async def run(self, value):
        _, contract = self.load(value)
        cwd = self.directory(contract["cwd"])
        executable = Path(contract["executable"])
        revision = _approved_revision.get()
        if revision is None:
            raise _PolicyDenied
        metadata_inventory = None
        inherited_authorize = execution_dispatch_authorized.get()
        managed = None
        if 'managed_copy' in contract:
            # Normal studio_run_command must honor the same durable binding;
            # changing the API/tool entry point cannot bypass draft validation.
            from services.agent_api.app.draft_materializations import (
                DraftMaterializations,
                Materialization,
            )
            copies = DraftMaterializations(self.files.guard.root, self.database, self.project)
            binding = Materialization(**contract['managed_copy'])
            target, _ = copies.ready(binding)
            if target != cwd:
                raise ValueError('Managed command destination changed.')
            copies.check_inputs(binding)
            managed = (copies, binding)

        def authorize():
            inherited_authorize()
            if managed is not None:
                managed[0].ready(managed[1])
            self.files.guard.resolve(contract["cwd"], True)
            if "git_scope" in contract and self.git_identity() != {key: contract["git_scope"][key] for key in ("device", "inode", "config_sha256")}:
                raise ValueError("Git metadata changed; prepare and approve again.")
            if metadata_inventory is not None:
                for directory, names in metadata_inventory.items():
                    if {path.name for path in directory.iterdir()} != names:
                        raise ValueError("Git metadata entries changed; prepare and approve again.")
            policy = self.policy.snapshot()
            if policy["revision"] != revision or ActionCategory.COMMAND in policy["blocked_categories"]:
                raise _PolicyDenied

        # Pin the directory route and executable until owned processes stop.
        with ExitStack() as stack:
            stack.enter_context(self.files._scan(cwd))
            if "git_scope" in contract:
                authorize()
                metadata_inventory = self.pin_git_metadata(stack)
            digest = stack.enter_context(locked_executable(executable))
            if digest != contract["executable_sha256"]:
                raise ValueError("Executable changed; prepare and approve again.")
            authorize()
            self.record(value.run_id, "running", expected="prepared")
            try:
                result = await run_process(executable, contract["arguments"], cwd, timeout=contract["timeout_seconds"], output_limit=contract["output_limit"], authorize=authorize, environment_profile="git_inspection" if "git_scope" in contract else None)
            except BaseException:
                self.record(value.run_id, "interrupted")
                raise
            self.record(value.run_id, result["status"], result)
        result["run_id"] = value.run_id
        if result["status"] != "succeeded":
            raise ToolExecutionOutcome(result)
        return result

    def listing(self):
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute("SELECT run_id,contract_sha,status,result_json,created_at FROM command_runs WHERE project_id=? AND root=? ORDER BY created_at DESC LIMIT 20", (self.project, str(self.files.guard.root))).fetchall()
        return {"runs": [{"run_id": row[0], "contract_sha256": row[1], "status": row[2], "result": json.loads(row[3]) if row[3] else None, "created_at": row[4]} for row in rows]}


def register_studio_commands(registry, database, project):
    commands = StudioCommands(registry.workspace_root, database, project)

    async def prepare(value):
        return commands.prepare(value)

    async def listing(_):
        return commands.listing()

    async def prepare_git(value):
        return commands.prepare_git(value)

    registry.register(Tool("studio_prepare_git", "Prepare bounded Git inspection for separate exact command approval; linked worktrees are unsupported.", PrepareGit, RiskLevel.LOW, 5, prepare_git, category=ActionCategory.READ, resolve_destination=lambda _: commands.files.guard.resolve(".")))
    registry.register(Tool("studio_prepare_command", "Prepare an exact executable, arguments, working directory and limits for approval.", PrepareCommand, RiskLevel.LOW, 5, prepare, category=ActionCategory.READ, resolve_destination=lambda args: commands.directory(args["cwd"])))
    registry.register(Tool("studio_run_command", "Run the exact approved command; nonzero exit is failure and interrupted outcomes cannot retry.", RunCommand, RiskLevel.HIGH, 130, commands.run, category=ActionCategory.COMMAND, resolve_destination=commands.destination, preview_details=commands.preview))
    registry.register(Tool("studio_command_runs", "Inspect this project's recent durable command outcomes.", EmptyInput, RiskLevel.LOW, 5, listing, resolve_destination=lambda _: commands.files.guard.resolve(".")))
