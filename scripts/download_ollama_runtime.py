"""Resume an authorized official runtime download without truncating progress.

No extraction/execution before the complete asset matches the official digest.
Does not install a service, change PATH, or configure user persistence.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    directory = Path(os.environ["LOCALAPPDATA"]) / "USMANJARVIS/runtimes/downloads"
    manifest = json.loads((directory / "ollama-source.json").read_text())
    if manifest["url"] != "https://github.com/ollama/ollama/releases/download/v0.40.3/ollama-windows-amd64.zip" or manifest["sha256"] != "sha256:e5ecff02dfc7cae8ddf6d6b7a7ac5f727ffeedf4491f44ae75ff5b0820dbbbd5" or manifest["size"] != 1468053171:
        raise RuntimeError("Runtime source differs from the verified official release.")
    path = directory / "ollama-v0.40.3-windows-amd64.zip"
    failures = 0
    while (position := path.stat().st_size if path.exists() else 0) < manifest["size"]:
        end = min(position + 16 * 1024 * 1024 - 1, manifest["size"] - 1)
        request = urllib.request.Request(manifest["url"], headers={"Range": f"bytes={position}-{end}", "User-Agent": "USMANJARVIS-development-verification"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                expected = f"bytes {position}-{end}/{manifest['size']}"
                if response.status != 206 or response.headers.get("Content-Range") != expected:
                    raise RuntimeError("Server did not honor the exact resume range; retained existing progress.")
                received = 0
                with path.open("ab") as output:
                    while chunk := response.read(min(64 * 1024, end - position + 1 - received)):
                        if received + len(chunk) > end - position + 1:
                            raise RuntimeError("Download range exceeded its declared length.")
                        output.write(chunk)
                        output.flush()
                        received += len(chunk)
                        if received == end - position + 1:
                            break
                if received != end - position + 1:
                    raise OSError("Incomplete range; resume preserves all received bytes.")
            failures = 0
            print(f"Runtime download: {path.stat().st_size}/{manifest['size']} bytes", flush=True)
        except (OSError, urllib.error.URLError) as error:
            failures += 1
            print(f"Network interrupted ({type(error).__name__}); retained {path.stat().st_size} bytes.", flush=True)
            if failures >= 8:
                raise RuntimeError("Network prevented runtime download; partial asset retained.") from error
            time.sleep(min(5 * failures, 30))
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != manifest["sha256"].removeprefix("sha256:"):
        raise RuntimeError("Runtime checksum failed; do not extract or execute the asset.")
    print("Official runtime SHA-256 verified: " + actual, flush=True)


if __name__ == "__main__":
    main()
