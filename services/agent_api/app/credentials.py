"""Windows user-bound credentials. Only opaque references leave the backend."""

import ctypes
import os
import re
import uuid
from ctypes import wintypes
from pathlib import Path

from services.agent_api.app.providers import EnvironmentSecretResolver


class _Blob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(data: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise ValueError("Protected credential storage requires Windows.")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    array = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _Blob(len(data), array)
    output = _Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    # UI forbidden; no LOCAL_MACHINE flag: DPAPI binds to this Windows user.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("Credential protection failed; the current Windows user cannot access this credential.")
    try:
        return ctypes.string_at(output.data, output.length)
    finally:
        kernel.LocalFree(output.data)


class CredentialVault:
    def __init__(self, directory: Path):
        self.directory = directory

    @property
    def available(self):
        return os.name == "nt"

    def _path(self, reference: str):
        if not re.fullmatch(r"vault:[a-f0-9]{32}", reference):
            raise ValueError("Invalid protected credential reference.")
        return self.directory / (reference[6:] + ".dpapi")

    def store(self, secret: str) -> str:
        if not secret.strip() or len(secret.encode()) > 16384:
            raise ValueError("Credential must contain between 1 and 16384 bytes.")
        protected = _protect(secret.encode())
        self.directory.mkdir(parents=True, exist_ok=True)
        reference = "vault:" + uuid.uuid4().hex
        # Exclusive creation; partial files are never returned as usable references.
        path = self._path(reference)
        try:
            with path.open("xb") as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            path.unlink(missing_ok=True)
            raise ValueError("Credential storage failed.") from None
        return reference

    def resolve(self, reference: str | None) -> str | None:
        if reference is None or not reference.startswith("vault:"):
            return EnvironmentSecretResolver().resolve(reference)
        path = self._path(reference)
        if not path.is_file():
            return None
        if path.stat().st_size > 65536:
            raise ValueError("Protected credential file is invalid.")
        return _protect(path.read_bytes(), decrypt=True).decode()

    def revoke(self, reference: str):
        self._path(reference).unlink(missing_ok=True)
