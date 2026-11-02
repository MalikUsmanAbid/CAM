"""Windows copy-on-write replacement; existing inode aliases are never mutated.

Unsupported filesystem/metadata contracts fail closed. Two handle-bound renames
preserve original bytes; interruption may require recovery. No in-place fallback.
"""

import ctypes
import os
import uuid
from contextlib import ExitStack


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.SetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel.GetFileInformationByHandleEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    return kernel


def _open(kernel, path, create=False):
    import msvcrt
    handle = kernel.CreateFileW(str(path), 0xC00F0000 if create else 0x80010000, 1, None, 1 if create else 3, 0x80, None)
    if handle in {None, ctypes.c_void_p(-1).value}:
        raise ValueError("File is unavailable, metadata rights denied, or another writer owns it.")
    try:
        fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if create else os.O_RDONLY) | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(fd, "r+b" if create else "rb", buffering=0)


def ensure_directory(files, directory, safety_target):
    if os.name != "nt":
        raise ValueError("Verified directory creation requires Windows.")
    kernel = _kernel()
    kernel.CreateDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    current = files.guard.root
    relative = safety_target.relative_to(current).as_posix()
    for part in directory.relative_to(current).parts:
        child = current / part
        with files._scan(current, writable=True):
            files.guard.resolve(relative)
            if child.exists():
                if child.is_symlink() or child.is_junction() or not child.is_dir():
                    raise ValueError("Directory route changed.")
            elif not kernel.CreateDirectoryW(str(child), None):
                raise ValueError("Directory creation refused; inspect the changed route.")
        current = child


def _metadata(kernel, source, destination):
    import msvcrt
    source_handle = msvcrt.get_osfhandle(source.fileno())
    destination_handle = msvcrt.get_osfhandle(destination.fileno())

    class Basic(ctypes.Structure):
        _fields_ = [("created", ctypes.c_int64), ("accessed", ctypes.c_int64), ("written", ctypes.c_int64), ("changed", ctypes.c_int64), ("attributes", ctypes.c_uint32)]

    basic = Basic()
    if not kernel.GetFileInformationByHandleEx(source_handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
        raise ValueError("Cannot verify original file metadata.")
    if basic.attributes & (1 | 0x200 | 0x400 | 0x800 | 0x4000):
        raise ValueError("Read-only, sparse, linked, compressed or encrypted checkpoint files are unsupported.")
    streams = ctypes.create_string_buffer(65536)
    if not kernel.GetFileInformationByHandleEx(source_handle, 7, streams, len(streams)):
        raise ValueError("Cannot verify original file streams.")
    name_size = ctypes.c_uint32.from_buffer(streams, 4).value
    if ctypes.c_uint32.from_buffer(streams).value or streams.raw[24:24 + name_size].decode("utf-16-le") != "::$DATA":
        raise ValueError("Additional file streams require a preservation workflow.")
    basic.accessed = basic.written = basic.changed = 0
    if not kernel.SetFileInformationByHandle(destination_handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):
        raise ValueError("Cannot preserve original file attributes.")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.GetSecurityInfo.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
    advapi.SetSecurityInfo.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint16), ctypes.POINTER(ctypes.c_uint32)]
    owner, group, dacl, descriptor = (ctypes.c_void_p() for _ in range(4))
    if advapi.GetSecurityInfo(source_handle, 1, 7, ctypes.byref(owner), ctypes.byref(group), ctypes.byref(dacl), None, ctypes.byref(descriptor)):
        raise ValueError("Cannot inspect original security descriptor.")
    try:
        control, revision = ctypes.c_uint16(), ctypes.c_uint32()
        if not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ValueError("Cannot inspect original ACL inheritance.")
        flags = 7 | (0x80000000 if control.value & 0x1000 else 0x20000000)
        if advapi.SetSecurityInfo(destination_handle, 1, flags, owner, group, dacl, None):
            raise ValueError("Cannot preserve original owner, group and ACL.")
    finally:
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(descriptor)


def rename_replacement(kernel, file, target, exists, parent_handle):
    import msvcrt

    class Rename(ctypes.Structure):
        _fields_ = [("flags", ctypes.c_uint32), ("root", ctypes.c_void_p), ("length", ctypes.c_uint32), ("name", ctypes.c_wchar * 1)]

    encoded = target.name.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(max(ctypes.sizeof(Rename), Rename.name.offset + len(encoded)))
    info = Rename.from_buffer(buffer)
    info.flags = 3 if exists else 0
    info.root = parent_handle
    info.length = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + Rename.name.offset, encoded, len(encoded))
    class IOStatus(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    ntdll = ctypes.WinDLL("ntdll")
    rename = ntdll.NtSetInformationFile
    rename.argtypes = [ctypes.c_void_p, ctypes.POINTER(IOStatus), ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
    rename.restype = ctypes.c_int32
    status = rename(msvcrt.get_osfhandle(file.fileno()), ctypes.byref(IOStatus()), buffer, len(buffer), 65)
    if status < 0:
        raise ValueError(f"Verified copy-on-write replacement failed (NT status {status & 0xffffffff:08x}).")


def retirement_path(target, path=None):
    path = path or target.parent / (".jarvis-backup-" + uuid.uuid4().hex)
    if path.parent != target.parent or not path.name.startswith(".jarvis-backup-") or path.exists():
        raise ValueError("Checkpoint retirement destination is occupied or invalid.")
    return path


def replace(files, target, content, *, expected=None, create_new=False, before_commit=lambda: None, retirement=None, staged_identity=lambda identity: None):
    if os.name != "nt":
        raise ValueError("Verified copy-on-write replacement requires Windows.")
    kernel = _kernel()
    stage = target.parent / (".jarvis-stage-" + uuid.uuid4().hex)
    backup = retirement_path(target, retirement)
    renamed = False
    moved_original = False
    with files._scan(target.parent, writable=True) as pinned, ExitStack() as stack:
        relative = target.relative_to(files.guard.root).as_posix()
        files.guard.resolve(relative)
        source = None if create_new else stack.enter_context(_open(kernel, target))
        if source:
            files.verify_opened(source.fileno(), target)
            if expected is not None and source.read(64001) != expected:
                raise ValueError("File changed; replacement would overwrite later edits.")
        files.guard.resolve(relative)
        replacement = None
        try:
            replacement = stack.enter_context(_open(kernel, stage, True))
            files.verify_opened(replacement.fileno(), stage)
            if source:
                _metadata(kernel, source, replacement)
            if replacement.write(content) != len(content):
                raise RuntimeError("Incomplete staging write.")
            os.fsync(replacement.fileno())
            staged = os.fstat(replacement.fileno())
            staged_identity((staged.st_dev, staged.st_ino))
            before_commit()
            files.guard.resolve(relative)
            # Original content stays untouched even if another alias appears
            # now. Its route and writes remain locked until after replacement.
            if source:
                # Move the original by its locked handle. No content mutation:
                # aliases created at this boundary retain the original bytes.
                rename_replacement(kernel, source, backup, False, pinned.handle)
                moved_original = True
            try:
                # Never replace a file another application creates in the gap.
                files.guard.resolve(relative)
                rename_replacement(kernel, replacement, target, False, pinned.handle)
            except BaseException:
                if moved_original:
                    try:
                        rename_replacement(kernel, source, target, False, pinned.handle)
                        moved_original = False
                    except ValueError:
                        pass  # Retain original backup; never overwrite a later file.
                raise
            renamed = True
            files.verify_opened(replacement.fileno(), target)
            replacement.seek(0)
            if replacement.read(64001) != content:
                raise RuntimeError("Replacement verification failed; inspect checkpoint.")
            if moved_original:
                import msvcrt
                streams = ctypes.create_string_buffer(65536)
                if not kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(source.fileno()), 7, streams, len(streams)) or ctypes.c_uint32.from_buffer(streams).value:
                    raise RuntimeError("Later original file streams require recovery; backup retained.")
                # Retain the original inode and every stream. Physical deletion
                # cannot safely compensate a failed delete-pending cancellation.
            identity = os.fstat(replacement.fileno())
            return identity.st_dev, identity.st_ino
        finally:
            if not renamed and replacement is not None and not replacement.closed:
                import msvcrt
                disposition = ctypes.c_ubyte(1)
                # Ownership survives until close. If marking fails, retain the
                # stage for recovery rather than deleting a pathname later.
                kernel.SetFileInformationByHandle(msvcrt.get_osfhandle(replacement.fileno()), 4, ctypes.byref(disposition), ctypes.sizeof(disposition))
            stack.close()


def remove_created(files, target, expected, identity, *, before_commit, retirement=None):
    """Retire the still-owned newly created file; never physically delete it."""
    if os.name != "nt":
        raise ValueError("Verified selective deletion requires Windows.")
    import msvcrt
    kernel = _kernel()
    relative = target.relative_to(files.guard.root).as_posix()
    backup = retirement_path(target, retirement)
    with files._scan(target.parent, writable=True) as pinned, _open(kernel, target) as source:
        files.verify_opened(source.fileno(), target)
        actual = os.fstat(source.fileno())
        if (actual.st_dev, actual.st_ino) != identity or source.read(64001) != expected:
            raise ValueError("Created file changed or was replaced; later user files are preserved.")
        streams = ctypes.create_string_buffer(65536)
        if not kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(source.fileno()), 7, streams, len(streams)):
            raise ValueError("Cannot verify created file streams.")
        name_size = ctypes.c_uint32.from_buffer(streams, 4).value
        if ctypes.c_uint32.from_buffer(streams).value or streams.raw[24:24 + name_size].decode("utf-16-le") != "::$DATA":
            raise ValueError("Later additional streams are preserved.")
        files.guard.resolve(relative)
        before_commit()
        files.guard.resolve(relative)
        # A different data stream can be created while the base stream is
        # locked. Recheck after the durable claim and final authorization.
        if not kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(source.fileno()), 7, streams, len(streams)) or ctypes.c_uint32.from_buffer(streams).value:
            raise ValueError("Later additional streams are preserved.")
        files.guard.resolve(relative)
        rename_replacement(kernel, source, backup, False, pinned.handle)
        # An ADS added at the native rename boundary stays with the original.
        # Restore its public name if possible, otherwise retain the checkpoint.
        try:
            if not kernel.GetFileInformationByHandleEx(msvcrt.get_osfhandle(source.fileno()), 7, streams, len(streams)) or ctypes.c_uint32.from_buffer(streams).value:
                raise ValueError("Later streams require recovery; checkpoint retained.")
        except BaseException:
            try:
                rename_replacement(kernel, source, target, False, pinned.handle)
            except ValueError:
                pass
            raise
    if target.exists():
        raise ValueError("A file remains at the rollback path; inspect the uncertain result.")
    return backup
