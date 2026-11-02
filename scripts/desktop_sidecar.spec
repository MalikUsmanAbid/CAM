# PyInstaller source specification; build execution is deliberately deferred.
from pathlib import Path

root = Path(SPECPATH).parent
analysis = Analysis(
    [str(root / "services" / "agent_api" / "desktop_sidecar.py")],
    pathex=[str(root)],
    binaries=[], datas=[],
    hiddenimports=["uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.protocols.http.h11_impl",
                   "uvicorn.lifespan.on"],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, analysis.binaries, analysis.datas, [],
    name="jarvis-backend", debug=False, strip=False, upx=False,
    console=True, disable_windowed_traceback=True,
)
