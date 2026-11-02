import asyncio
from pathlib import Path

from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.tools import ToolExecutor, build_registry


def test_filesystem_tools_and_approval_replay(tmp_path: Path) -> None:
    root = tmp_path / "safe"
    root.mkdir()
    (root / "readme.txt").write_text("hello", encoding="utf-8")
    repo = MemoryRepository(tmp_path / "jarvis.db")
    repo.initialize()
    executor = ToolExecutor(build_registry(root), repo)

    read = asyncio.run(executor.execute("read_file", {"path": "readme.txt"}))
    assert read["status"] == "success"
    assert read["result"]["content"] == "hello"

    pending = asyncio.run(executor.execute("write_file", {"path": "new.txt", "content": "safe"}))
    assert pending["status"] == "approval_required"
    request_id = pending["approval"]["request_id"]
    assert not (root / "new.txt").exists()
    repo.decide_approval(request_id, True)
    completed = asyncio.run(executor.continue_approved(request_id))
    assert completed["status"] == "success"
    assert (root / "new.txt").read_text(encoding="utf-8") == "safe"
    replay = asyncio.run(executor.continue_approved(request_id))
    assert replay["status"] == "consumed"


def test_rejection_and_path_protection(tmp_path: Path) -> None:
    root = tmp_path / "safe"
    root.mkdir()
    repo = MemoryRepository(tmp_path / "jarvis.db")
    repo.initialize()
    executor = ToolExecutor(build_registry(root), repo)
    pending = asyncio.run(executor.execute("create_directory", {"path": "child"}))
    request_id = pending["approval"]["request_id"]
    repo.decide_approval(request_id, False)
    assert asyncio.run(executor.continue_approved(request_id))["status"] == "rejected"
    assert not (root / "child").exists()
    for path in ("..", "../outside.txt", "C:\\Windows\\win.ini", "\\\\server\\share"):
        result = asyncio.run(executor.execute("read_file", {"path": path}))
        assert result["status"] == "invalid_arguments"


def test_symlink_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "safe"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    repo = MemoryRepository(tmp_path / "jarvis.db")
    repo.initialize()
    result = asyncio.run(
        ToolExecutor(build_registry(root), repo).execute("read_file", {"path": "link/secret.txt"})
    )
    assert result["status"] == "invalid_arguments"
