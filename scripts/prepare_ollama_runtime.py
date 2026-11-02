"""Verify and unpack the authorized official standalone runtime into local app data.

No global installation, PATH edit, service or user-database access. Existing
files must match the verified archive; unrelated/changed files are never replaced.
"""

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath

DIGEST = "e5ecff02dfc7cae8ddf6d6b7a7ac5f727ffeedf4491f44ae75ff5b0820dbbbd5"


def main():
    parent = Path(os.environ["LOCALAPPDATA"]) / "USMANJARVIS/runtimes"
    asset = parent / "downloads/ollama-v0.40.3-windows-amd64.zip"
    if asset.stat().st_size != 1468053171:
        raise RuntimeError("Runtime download is incomplete; do not extract it.")
    with asset.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != DIGEST:
            raise RuntimeError("Official runtime SHA-256 verification failed.")
    destination = parent / "ollama-v0.40.3"
    if destination.is_symlink() or destination.is_junction():
        raise RuntimeError("Linked runtime destination is unsupported.")
    destination.mkdir(exist_ok=True)
    inventory = {}
    with zipfile.ZipFile(asset) as archive:
        required = sum(item.file_size for item in archive.infolist())
        if shutil.disk_usage(parent).free < required + 512 * 1024 * 1024:
            raise RuntimeError("Insufficient free space for the standalone runtime.")
        seen = set()
        for entry in archive.infolist():
            relative = PurePosixPath(entry.filename.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts or any(":" in part for part in relative.parts) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError("Unsafe runtime archive entry.")
            key = str(relative).casefold()
            if key in seen:
                raise RuntimeError("Duplicate runtime archive entry.")
            seen.add(key)
            target = destination.joinpath(*relative.parts)
            if target.resolve() != target or not target.is_relative_to(destination):
                raise RuntimeError("Runtime extraction route changed.")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = target.exists()
            checksum = hashlib.sha256()
            with archive.open(entry) as source:
                if existing:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        checksum.update(chunk)
                    with target.open("rb") as file:
                        if hashlib.file_digest(file, "sha256").hexdigest() != checksum.hexdigest():
                            raise RuntimeError("Existing runtime file differs; preserved without overwrite.")
                else:
                    with target.open("xb") as output:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            checksum.update(chunk)
                            output.write(chunk)
            inventory[str(relative)] = checksum.hexdigest()
    if "ollama.exe" not in inventory:
        raise RuntimeError("Verified runtime archive did not contain its expected executable.")
    marker = {"asset_sha256": DIGEST, "executable_sha256": inventory["ollama.exe"], "files": inventory}
    (destination / "verified-runtime.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
    print("Verified standalone runtime prepared: " + str(destination), flush=True)


if __name__ == "__main__":
    main()
