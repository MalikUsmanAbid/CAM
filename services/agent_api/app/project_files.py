"""Bounded project text access; ignore rules and secrets are exclusions, not authority."""

import ctypes
import hashlib
import os
import re
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from pathspec import GitIgnoreSpec

from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.tools import (
    DirectoryInput,
    ReadFileInput,
    Tool,
    WorkspaceGuard,
)

MAX_ENTRIES = 1000
MAX_IGNORE_BYTES = 16_000
EXCLUDED = {".git", ".venv", "node_modules", "credentials", "__pycache__", ".ssh"}
SECRET_NAME = re.compile(r"(?:^\.env(?:\.|$)|\.(?:pem|key|p12|pfx|dpapi)$|^id_(?:rsa|ed25519)$)", re.I)


def excluded_name(part):
    return part.lower() in EXCLUDED or part.lower().startswith((".jarvis-stage-", ".jarvis-backup-")) or SECRET_NAME.search(part)


class ProjectFiles:
    def __init__(self, root: Path):
        self.guard = WorkspaceGuard(root)

    def _relative(self, relative: str) -> str:
        # Reject ambiguous Windows names, alternate streams and traversal on every host.
        relative = relative.replace("\\", "/")
        parts = PurePosixPath(relative).parts
        if not parts or any(p in {"..", "."} or ":" in p or p.endswith((".", " ")) for p in parts):
            raise ValueError("Use a canonical relative project path.")
        return "/".join(parts)

    def _safe(self, relative: str) -> Path:
        relative = self._relative(relative)
        parts = PurePosixPath(relative).parts
        if any(excluded_name(p) for p in parts):
            raise ValueError("Project path is excluded from context.")
        current = self.guard.root
        for part in parts:
            current = current / part
            if current.is_symlink() or current.is_junction():
                raise ValueError("Linked project paths are excluded.")
        target = self.guard.resolve(relative)
        if self._ignored(relative, target.is_dir()):
            raise ValueError("Project path is ignored.")
        return target

    def verify_opened(self, fd: int, path: Path):
        if os.fstat(fd).st_nlink != 1:
            raise ValueError("Hard-linked project files are excluded.")
        if os.name == "nt":
            import msvcrt
            final_name = ctypes.windll.kernel32.GetFinalPathNameByHandleW
            final_name.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
            final_name.restype = ctypes.c_uint32
            buffer = ctypes.create_unicode_buffer(32768)
            size = final_name(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
            if not size or size >= len(buffer):
                raise ValueError("Cannot verify opened project file.")
            actual = Path(buffer.value.removeprefix("\\\\?\\")).resolve()
        else:
            actual = Path(f"/proc/self/fd/{fd}").resolve(strict=True)
        if actual != path or not actual.is_relative_to(self.guard.root):
            raise ValueError("Opened project destination changed.")

    def _text(self, path: Path, limit: int) -> str:
        # Check the opened handle, not just a path checked before open (link-swap race).
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            self.verify_opened(fd, path)
            if os.fstat(fd).st_size > limit:
                raise ValueError("Project file exceeds the context limit.")
            raw = os.read(fd, limit + 1)
            if len(raw) > limit or b"\x00" in raw:
                raise ValueError("Project file exceeds limits or is binary.")
            return raw.decode("utf-8")
        finally:
            os.close(fd)

    def _ignored(self, relative: str, directory: bool) -> bool:
        parts = PurePosixPath(relative).parts
        specs = []
        # Evaluate each ancestor before descending. Later nested rules can
        # override an ordinary file pattern, but cannot enter an excluded parent.
        for end in range(1, len(parts) + 1):
            base = self.guard.root.joinpath(*parts[:end - 1])
            ignore = base / ".gitignore"
            if ignore.is_symlink() or ignore.is_junction():
                raise ValueError("Linked ignore files are unsupported.")
            if ignore.is_file():
                specs.append((end - 1, GitIgnoreSpec.from_lines(self._text(ignore, MAX_IGNORE_BYTES).splitlines())))
            ignored = False
            for depth, spec in specs:
                candidate = "/".join(parts[depth:end]) + ("/" if end < len(parts) or directory else "")
                match = spec.check_file(candidate)
                if match.include is not None:
                    ignored = match.include
            if ignored:
                return True
        return False

    @contextmanager
    def _scan(self, target, writable=False):
        if os.name != "nt":
            fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                if Path(f"/proc/self/fd/{fd}").resolve(strict=True) != target:
                    raise ValueError("Project directory destination changed.")
                with os.scandir(fd) as items:
                    yield items
            finally:
                os.close(fd)
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        create.restype = ctypes.c_void_p
        final = kernel.GetFinalPathNameByHandleW
        final.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        final.restype = ctypes.c_uint32
        close = kernel.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        handles = []
        try:
            # Keep the whole route pinned while scandir uses a pathname.
            # Omitting FILE_SHARE_DELETE prevents rename/junction substitution.
            for directory in reversed([target, *target.parents]):
                handle = create(str(directory), 0x12008B if writable and directory == target else 0x120089, 3, None, 3, 0x02000000, None)
                if handle in {None, ctypes.c_void_p(-1).value}:
                    raise ValueError("Cannot pin project directory for inspection.")
                handles.append(handle)
                buffer = ctypes.create_unicode_buffer(32768)
                size = final(handle, buffer, len(buffer), 0)
                if not size or size >= len(buffer) or Path(buffer.value.removeprefix("\\\\?\\")).resolve() != directory:
                    raise ValueError("Project directory destination changed.")
            with os.scandir(target) as items:
                class PinnedEntries:
                    handle = handles[-1]

                    def __iter__(self):
                        return iter(items)

                yield PinnedEntries()
        finally:
            for handle in reversed(handles):
                close(handle)

    def read(self, relative: str, limit: int = 64_000) -> dict:
        target = self._safe(relative)
        if not target.is_file():
            raise ValueError("Project text file is unavailable.")
        content = self._text(target, limit)
        if is_sensitive_content(content):
            raise ValueError("Potential credential content is excluded.")
        return {"path": self._relative(relative), "content": content, "bytes": len(content.encode()), "sha256": hashlib.sha256(content.encode()).hexdigest()}

    def listing(self, relative: str = "") -> dict:
        target = self._safe(relative) if relative else self.guard.resolve(".")
        if not target.is_dir():
            raise ValueError("Project directory is unavailable.")
        entries = []
        scanned = 0
        # Scandir stops at the bound; never materialize an unbounded directory.
        with self._scan(target) as items:
            for item in items:
                scanned += 1
                if scanned > MAX_ENTRIES:
                    break
                child = target / item.name
                name = child.relative_to(self.guard.root).as_posix()
                try:
                    # Listing is scoped to its directory; inspect children without
                    # broadening a read approval to a different file's contents.
                    if item.is_symlink() or child.is_junction():
                        continue
                    if any(excluded_name(p) for p in PurePosixPath(name).parts):
                        continue
                    directory = item.is_dir(follow_symlinks=False)
                    if not self._ignored(name, directory):
                        entries.append({"path": name, "name": item.name, "directory": directory})
                except (OSError, ValueError, UnicodeError):
                    continue
        return {"entries": sorted(entries, key=lambda e: (not e["directory"], e["name"].lower())), "truncated": scanned > MAX_ENTRIES}


def register_project_files(registry):
    files = ProjectFiles(registry.workspace_root)

    async def listing(value):
        return files.listing(value.path)

    async def reading(value):
        return files.read(value.path, value.max_bytes)

    registry.register(Tool("studio_list", "Lists bounded, non-secret project entries respecting ignore rules.", DirectoryInput, RiskLevel.LOW, 3, listing,
                           resolve_destination=lambda args: files.guard.resolve(args.get("path") or ".")))
    registry.register(Tool("studio_read", "Reads bounded non-secret UTF-8 project context respecting ignore rules.", ReadFileInput, RiskLevel.LOW, 3, reading,
                           resolve_destination=lambda args: files.guard.resolve(args["path"])))
    # The planner can request legacy names; they must enforce the same project
    # context contract instead of bypassing it through the original handlers.
    for name, model, handler, destination in (
        ("read_file", ReadFileInput, reading, lambda args: files.guard.resolve(args["path"])),
        ("list_directory", DirectoryInput, listing, lambda args: files.guard.resolve(args.get("path") or ".")),
    ):
        registry.unregister(name)
        registry.register(Tool(name, "Inspects bounded non-secret project context respecting ignore rules.", model, RiskLevel.LOW, 3, handler, resolve_destination=destination))
