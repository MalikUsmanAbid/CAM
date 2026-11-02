"""Consistent bounded database bundles; restore only into a new local directory.

Credentials, project working trees, runtime models and microphone audio are not
included. DPAPI encryption is Windows-user/machine bound, not a portable cloud
recovery key. Callers must obtain explicit backup/restore authorization.
"""

import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path

from services.agent_api.app.migrations import MIGRATIONS

LIMIT = 32 * 1024 * 1024
BUNDLE_LIMIT = LIMIT + 1024 * 1024
DPAPI_HEADER = b"USMAN-JARVIS-DPAPI-1\n"


def _snapshot_contract(path, *, authorize=lambda: None):
    authorize()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        def progress():
            try:
                authorize()
                return 0
            except Exception:
                return 1
        db.set_progress_handler(progress, 1000)
        db.execute("PRAGMA trusted_schema=OFF")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database integrity check failed.")
        versions = [row[0] for row in db.execute("SELECT version FROM schema_versions ORDER BY version")]
        if not versions or versions != list(range(1, max(versions) + 1)) or max(versions) > max(MIGRATIONS):
            raise ValueError("Backup migration ledger is unsupported or incomplete.")
        version = max(versions)
        with closing(sqlite3.connect(":memory:")) as expected:
            expected.execute("CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            for number in versions:
                expected.executescript(MIGRATIONS[number])
            def objects(connection):
                return {(kind, name): " ".join(sql.split()) for kind, name, sql in connection.execute("SELECT type,name,sql FROM sqlite_schema WHERE sql IS NOT NULL")}
            if objects(expected) != objects(db):
                raise ValueError("Backup contains unsupported schema objects or altered schema definitions.")
        if version >= 2:
            checksums = dict(db.execute("SELECT version,sha256 FROM migration_checksums"))
            if any(checksums.get(number) != hashlib.sha256(MIGRATIONS[number].encode()).hexdigest() for number in versions):
                raise ValueError("Backup migration checksums do not match.")
        return version


def create_bundle(database, destination, *, encrypt=False, passphrase=None, authorize=lambda: None):
    authorize()
    if encrypt and passphrase is not None:
        raise ValueError('Choose either Windows-user encryption or portable password encryption.')
    database, destination = Path(database).resolve(strict=True), Path(destination).absolute()
    if not destination.parent.is_dir() or destination.exists() or destination.is_symlink():
        raise ValueError("Choose an absent backup file in an existing directory.")
    if database.stat().st_size > LIMIT:
        raise ValueError("Database exceeds the current 32 MiB backup bound.")
    with tempfile.TemporaryDirectory(prefix="jarvis-backup-") as temporary:
        snapshot = Path(temporary) / "database.sqlite"
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(snapshot)) as output:
            page_size = source.execute("PRAGMA page_size").fetchone()[0]
            def bounded_progress(status, remaining, total):
                authorize()
                if total * page_size > LIMIT:
                    raise ValueError("Database grew beyond the backup bound.")
            source.backup(output, pages=16, sleep=0.01, progress=bounded_progress)
        if snapshot.stat().st_size > LIMIT:
            raise ValueError("Database exceeds the current 32 MiB backup bound.")
        version = _snapshot_contract(snapshot, authorize=authorize)
        data = snapshot.read_bytes()
        manifest = {"format": "usman-jarvis-backup", "version": 1, "schema_version": version,
                    "database_sha256": hashlib.sha256(data).hexdigest(),
                    "includes_credentials": False, "includes_workspaces": False}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("database.sqlite", data)
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        payload = buffer.getvalue()
        if encrypt:
            from services.agent_api.app.credentials import _protect
            payload = DPAPI_HEADER + _protect(payload)
        elif passphrase is not None:
            from services.agent_api.app.portable_backup import encrypt_payload
            payload = encrypt_payload(payload, passphrase)
        authorize()
        if len(payload) > BUNDLE_LIMIT:
            raise ValueError("Backup bundle exceeds its bound.")
        # Never overwrite a previous bundle. A partial new file is preserved for
        # inspection after an I/O error; no pathname-based cleanup deletes it.
        with destination.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    return {"path": str(destination), "sha256": hashlib.sha256(payload).hexdigest(), "schema_version": version,
            "encryption": "windows_user_dpapi" if encrypt else ("portable_scrypt_aes256gcm" if passphrase is not None else "none"), "credentials_included": False}


@contextmanager
def _new_directory(target):
    if os.name == "nt":
        from services.agent_api.app.project_creation import _create_owned_directory
        with _create_owned_directory(target):
            yield
    else:
        target.mkdir(exist_ok=False)
        yield


def restore_bundle(bundle, destination, *, passphrase=None):
    bundle = Path(bundle).resolve(strict=True)
    target = Path(destination).absolute()
    parent = target.parent.resolve(strict=True)
    target = parent / target.name
    if target.exists() or target.is_symlink() or target == parent or str(target).startswith(("\\\\", "//")):
        raise ValueError("Restore requires an absent local directory, never an active database.")
    if any(part.lower().startswith(("onedrive", "dropbox")) for part in target.parts):
        raise ValueError("Active restored databases in synced directories are unsupported.")
    for name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer", "DROPBOX_PATH"):
        location = os.getenv(name)
        if location and target.is_relative_to(Path(location).resolve()):
            raise ValueError("Active restored databases in synced directories are unsupported.")
    if os.name == "nt":
        import ctypes
        get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint
        if get_drive_type(str(target.anchor)) == 4:
            raise ValueError("Active restored databases on mapped network drives are unsupported.")
    if bundle.stat().st_size > BUNDLE_LIMIT:
        raise ValueError("Backup bundle exceeds its bound.")
    payload = bundle.read_bytes()
    from services.agent_api.app.portable_backup import HEADER, decrypt_payload
    if payload.startswith(HEADER):
        if passphrase is None:
            raise ValueError('This backup requires its separately retained recovery passphrase.')
        payload = decrypt_payload(payload, passphrase)
    if payload.startswith(DPAPI_HEADER):
        from services.agent_api.app.credentials import _protect
        payload = _protect(payload[len(DPAPI_HEADER):], decrypt=True)
    if len(payload) > BUNDLE_LIMIT:
        raise ValueError("Decrypted backup exceeds its bound.")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) != 2 or {entry.filename for entry in entries} != {"database.sqlite", "manifest.json"}:
            raise ValueError("Backup entries are unsupported or duplicated.")
        if archive.getinfo("database.sqlite").file_size > LIMIT or archive.getinfo("manifest.json").file_size > 4096:
            raise ValueError("Backup entries exceed their limits.")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict) or manifest.get("format") != "usman-jarvis-backup" or manifest.get("version") != 1:
            raise ValueError("Unsupported backup format.")
        data = archive.read("database.sqlite")
        if hashlib.sha256(data).hexdigest() != manifest.get("database_sha256"):
            raise ValueError("Backup hash does not match its manifest.")
    # Validate the untrusted snapshot before creating any destination directory.
    with tempfile.TemporaryDirectory(prefix="jarvis-restore-check-") as temporary:
        checked = Path(temporary) / "database.sqlite"
        checked.write_bytes(data)
        version = _snapshot_contract(checked)
        if version != manifest.get("schema_version"):
            raise ValueError("Backup schema version does not match its manifest.")
    with _new_directory(target):
        with (target / "jarvis.db").open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    return {"database_path": str(target / "jarvis.db"), "schema_version": version,
            "migration_required": version < max(MIGRATIONS), "credentials_restored": False,
            "startup": "Select the restored data directory explicitly; existing project paths may require re-registration."}
