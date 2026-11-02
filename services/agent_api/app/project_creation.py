"""Create an absent empty project under the personal workspace, after approval."""

import ctypes
import os
from contextlib import contextmanager

from pydantic import Field, field_validator

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.project_files import ProjectFiles
from services.agent_api.app.session_security import execution_dispatch_authorized
from services.agent_api.app.tools import Tool, ToolInput


class CreateProject(ToolInput):
    name: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=500)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Project name cannot be blank.")
        return value.strip()


@contextmanager
def _create_owned_directory(target):
    """Atomically create and hold the new inode without delete sharing."""
    from services.agent_api.app.native_replace import _kernel

    class Unicode(ctypes.Structure):
        _fields_ = [("length", ctypes.c_uint16), ("maximum", ctypes.c_uint16), ("buffer", ctypes.c_wchar_p)]

    class Attributes(ctypes.Structure):
        _fields_ = [("length", ctypes.c_uint32), ("root", ctypes.c_void_p), ("name", ctypes.POINTER(Unicode)), ("flags", ctypes.c_uint32), ("security", ctypes.c_void_p), ("quality", ctypes.c_void_p)]

    class Status(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    name = "\\??\\" + str(target)
    text = Unicode(len(name.encode("utf-16-le")), len(name.encode("utf-16-le")) + 2, name)
    attributes = Attributes(ctypes.sizeof(Attributes), None, ctypes.pointer(text), 0x40, None, None)
    handle, status = ctypes.c_void_p(), Status()
    create = ctypes.WinDLL("ntdll").NtCreateFile
    create.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.POINTER(Attributes), ctypes.POINTER(Status), ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    create.restype = ctypes.c_int32
    # FILE_CREATE refuses an existing destination; the returned handle already
    # prevents rename/deletion before any subsequent pathname observation.
    result = create(ctypes.byref(handle), 0x120089, ctypes.byref(attributes), ctypes.byref(status), None, 0x10, 3, 2, 0x200021, None, 0)
    if result < 0:
        raise ValueError("Project creation refused; preserve and inspect the changed destination.")
    try:
        yield handle
    finally:
        _kernel().CloseHandle(handle)


def register_project_creation(registry, foundation):
    files = ProjectFiles(registry.workspace_root)

    def preview(arguments):
        target = files._safe(arguments["path"])
        if target.exists() or not target.parent.is_dir():
            raise ValueError("Use an absent project directory within an existing parent.")
        return {"project_name": arguments["name"], "operation": "Create absent empty directory and register it; no dependency installation or generated code."}

    async def create(value):
        if os.name != "nt":
            raise ValueError("Verified project creation requires Windows.")
        target = files._safe(value.path)
        if target.exists() or not target.parent.is_dir():
            raise ValueError("Use an absent project directory within an existing parent.")
        with files._scan(target.parent, writable=True):
            execution_dispatch_authorized.get()()
            files.guard.resolve(value.path)
            with _create_owned_directory(target), files._scan(target):
                execution_dispatch_authorized.get()()
                files.guard.resolve(value.path, True)
                return foundation.register_project(value.name, str(target))

    registry.register(Tool("studio_create_project", "Create an empty project inside the personal workspace; exact approval required.", CreateProject,
                           RiskLevel.MEDIUM, 5, create, resolve_destination=lambda args: files.guard.resolve(args["path"]), category=ActionCategory.EDIT,
                           preview_details=preview))
