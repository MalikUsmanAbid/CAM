"""Disposable Windows Edge acceptance; never starts against user persistence.

Uses already-installed Playwright, Edge, Node and repository dependencies.
All backend data is temporary; browser requests are restricted to the fixture
loopback API and dashboard. This is dashboard acceptance, not production
browser-isolation or live-model acceptance.
"""

import json
import os
import queue
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def free_port(port=0):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))
        return sock.getsockname()[1]


def main():
    if os.name != "nt":
        raise RuntimeError("This acceptance check requires native Windows and Edge.")
    free_port(5173)  # Refuse to replace an existing dashboard.
    api_port = free_port()
    api = f"http://127.0.0.1:{api_port}"
    codes = queue.Queue()
    owned = []
    evidence = []
    output = Path(tempfile.mkdtemp(prefix="jarvis-foundation-evidence-"))
    with tempfile.TemporaryDirectory(prefix="jarvis-foundation-fixture-") as temporary:
        fixture = Path(temporary)
        environment = os.environ.copy()
        environment.update(DATABASE_PATH=str(fixture / "application.sqlite"), SAFE_WORKSPACE=str(fixture / "personal"),
                           AI_PROVIDER="mock", BRAIN_PROVIDER="mock", JARVIS_ALLOW_DATABASE_MIGRATION="0",
                           VITE_API_BASE_URL=api, PYTHONUNBUFFERED="1")

        def consume(stream, pairing=False):
            for line in stream:
                if pairing and line.startswith("JARVIS pairing code (valid for 10 minutes): "):
                    codes.put(line.split(": ", 1)[1].strip())

        def launch(arguments, directory, pairing=False):
            process = subprocess.Popen(arguments, cwd=directory, env=environment, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            owned.append(process)
            threading.Thread(target=consume, args=(process.stdout, pairing), daemon=True).start()
            return process

        def record(step):
            evidence.append(step)
            print("PASS: " + step, flush=True)

        try:
            launch([sys.executable, "-u", "-m", "uvicorn", "services.agent_api.app.main:app", "--host", "127.0.0.1", "--port", str(api_port)], ROOT, True)
            launch([shutil.which("node"), str(ROOT / "apps/desktop/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5173", "--strictPort"], ROOT / "apps/desktop")
            with httpx.Client(timeout=2, trust_env=False) as client:
                for target in (api + "/api/v1/health", "http://127.0.0.1:5173"):
                    deadline = time.monotonic() + 25
                    while True:
                        try:
                            if client.get(target).status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Fixture service startup timed out.")
                        time.sleep(.1)
                assert client.get(api + "/api/v1/tasks").status_code == 401
                record("unauthenticated fixture API denied")

            with sync_playwright() as driver:
                browser = driver.chromium.launch(channel="msedge", headless=True, args=["--disable-background-networking"])
                context = browser.new_context(viewport={"width": 1440, "height": 1000})

                def restrict(route):
                    parsed = urlparse(route.request.url)
                    if parsed.hostname == "127.0.0.1" and parsed.port in {5173, api_port}:
                        route.continue_()
                    else:
                        route.abort()

                context.route("**/*", restrict)
                page = context.new_page()
                page.set_default_timeout(20000)
                page.goto("http://127.0.0.1:5173")
                page.get_by_label("Pairing code", exact=True).fill(codes.get(timeout=10))
                with page.expect_response(lambda response: response.url.endswith("/session/pair") and response.request.method == "POST") as response:
                    page.get_by_role("button", name="Pair dashboard", exact=True).click()
                token = response.value.json()["token"]  # fixture only, never printed/persisted
                expect(page.get_by_label("Selected workspace")).to_be_enabled()
                record("actual dashboard pairing and in-memory session")
                page.get_by_role("button", name="System settings", exact=True).click()
                new_root = fixture / "personal" / "new-approved-project"
                page.get_by_label("New project name", exact=True).fill("Created fixture")
                page.get_by_label("New project folder", exact=True).fill("new-approved-project")
                page.get_by_role("button", name="Review new project", exact=True).click()
                expect(page.get_by_role("button", name="Approve empty project creation", exact=True)).to_be_visible()
                assert not new_root.exists()
                page.get_by_role("button", name="Approve empty project creation", exact=True).click()
                expect(page.get_by_role("option", name="Created fixture", exact=True)).to_have_count(1)
                assert new_root.is_dir() and not list(new_root.iterdir())
                record("actual new empty project review, exact approval and native owned-directory registration")
                for name in ("Alpha fixture", "Beta fixture"):
                    root = fixture / name
                    root.mkdir()
                    page.get_by_label("Project name", exact=True).fill(name)
                    page.get_by_label("Existing directory", exact=True).fill(str(root))
                    page.get_by_role("button", name="Register project", exact=True).click()
                    expect(page.get_by_role("option", name=name, exact=True)).to_have_count(1)
                record("project registration through actual dashboard")
                selector = page.get_by_label("Selected workspace")
                selector.select_option(label="Alpha fixture")
                page.get_by_role("button", name="Tasks", exact=True).click()
                request = 'tool:write_file {"path":"accepted.txt","content":"fixture result"}'
                page.get_by_label("Task request", exact=True).fill(request)
                page.get_by_role("button", name="Create task", exact=False).click()
                expect(selector).to_be_enabled()
                assert not (fixture / "Alpha fixture" / "accepted.txt").exists()
                page.get_by_role("button", name="Approvals", exact=True).click()
                expect(page.get_by_role("button", name="Approve once", exact=True)).to_have_count(1)
                selector.select_option(label="Beta fixture")
                expect(page.get_by_role("button", name="Approve once", exact=True)).to_have_count(0)
                record("project approval isolation before execution")
                selector.select_option(label="Alpha fixture")
                page.get_by_role("button", name="Approve once", exact=True).click()
                expect(selector).to_be_enabled()
                assert (fixture / "Alpha fixture" / "accepted.txt").read_text() == "fixture result"
                assert not (fixture / "Beta fixture" / "accepted.txt").exists()
                record("exact approved task resumes and writes only selected fixture")
                page.get_by_role("button", name="System settings", exact=True).click()
                page.get_by_label("Require approval for read-only actions", exact=True).check()
                page.get_by_role("button", name="Save action policy", exact=True).click()
                expect(page.get_by_text("Policy revision: 2", exact=True)).to_be_visible()
                record("actual action-policy revision update")
                (fixture / "Alpha fixture" / ".env").write_text("fixture secret excluded", encoding="utf-8")
                page.get_by_role("button", name="Coding Studio", exact=True).click()
                page.get_by_role("button", name="Approve this read", exact=True).click()
                expect(page.get_by_role("button", name="File: accepted.txt", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="File: .env", exact=True)).to_have_count(0)
                page.get_by_role("button", name="File: accepted.txt", exact=True).click()
                page.get_by_role("button", name="Approve this read", exact=True).click()
                expect(page.get_by_label("Proposed file content", exact=True)).to_have_value("fixture result")
                record("actual Coding Studio approved explorer/read excludes fixture credential")
                page.get_by_label("Proposed file content", exact=True).fill("fixture result updated")
                page.get_by_role("button", name="Review proposed patch", exact=True).click()
                page.get_by_role("button", name="Approve this read", exact=True).click()
                expect(page.get_by_text("Exact diff", exact=True)).to_be_visible()
                assert (fixture / "Alpha fixture" / "accepted.txt").read_text() == "fixture result"
                page.get_by_role("button", name="Request apply approval", exact=True).click()
                page.get_by_role("button", name="Approve exact patch", exact=True).click()
                expect(page.get_by_role("button", name="Request checkpoint rollback", exact=True)).to_be_visible()
                assert (fixture / "Alpha fixture" / "accepted.txt").read_text() == "fixture result updated"
                record("actual editor diff and exact approved checkpoint apply")
                page.screenshot(path=str(output / "studio.png"), full_page=True)
                page.get_by_role("button", name="Request checkpoint rollback", exact=True).click()
                page.get_by_role("button", name="Approve checkpoint rollback", exact=True).click()
                expect(page.get_by_text("Checkpoint rollback verified.", exact=True)).to_be_visible()
                assert (fixture / "Alpha fixture" / "accepted.txt").read_text() == "fixture result"
                record("actual separate approved selective checkpoint rollback")
                page.get_by_label("New file path", exact=True).fill("created-proof.py")
                page.get_by_role("button", name="Draft new file", exact=True).click()
                page.get_by_label("Proposed file content", exact=True).fill("print('actual created file')\n")
                page.get_by_role("button", name="Review proposed patch", exact=True).click()
                page.get_by_role("button", name="Approve this read", exact=True).click()
                expect(page.get_by_text("Exact diff", exact=True)).to_be_visible()
                assert not (fixture / "Alpha fixture" / "created-proof.py").exists()
                page.get_by_role("button", name="Request apply approval", exact=True).click()
                page.get_by_role("button", name="Approve exact patch", exact=True).click()
                expect(page.get_by_role("button", name="Request checkpoint rollback", exact=True)).to_be_visible()
                created = fixture / "Alpha fixture" / "created-proof.py"
                identity = created.stat().st_ino
                assert created.read_bytes() == b"print('actual created file')\n"
                page.screenshot(path=str(output / "create.png"), full_page=True)
                record("actual new-file diff and exact approved creation")
                page.get_by_role("button", name="Request checkpoint rollback", exact=True).click()
                page.get_by_role("button", name="Approve checkpoint rollback", exact=True).click()
                expect(page.get_by_text("Checkpoint rollback verified.", exact=True)).to_be_visible()
                assert not created.exists()
                retained = [path for path in created.parent.glob(".jarvis-backup-*") if path.stat().st_ino == identity]
                assert len(retained) == 1 and retained[0].read_bytes() == b"print('actual created file')\n"
                assert (fixture / "Alpha fixture" / "accepted.txt").read_text() == "fixture result"
                record("actual separately approved created-file rollback retains its inode and bytes")
                recovery_target = fixture / "Alpha fixture" / "recovery-fixture.txt"
                recovery_target.write_bytes(b"original recovery fixture")
                page.get_by_label("Selected workspace").select_option(label="Beta fixture")
                page.get_by_label("Selected workspace").select_option(label="Alpha fixture")
                page.get_by_role("button", name="Approve this read", exact=True).click()
                page.get_by_role("button", name="File: recovery-fixture.txt", exact=True).click()
                page.get_by_role("button", name="Approve this read", exact=True).click()
                page.get_by_label("Proposed file content", exact=True).fill("proposed recovery fixture")
                page.get_by_role("button", name="Review proposed patch", exact=True).click()
                page.get_by_role("button", name="Approve this read", exact=True).click()
                expect(page.get_by_text("Exact diff", exact=True)).to_be_visible()
                with closing(sqlite3.connect(fixture / "application.sqlite")) as database, database:
                    change_id = database.execute("SELECT change_id FROM coding_changes WHERE path='recovery-fixture.txt'").fetchone()[0]
                    database.execute("UPDATE coding_changes SET status='applying' WHERE change_id=?", (change_id,))
                recovery_inode = recovery_target.stat().st_ino
                recovery_backup = recovery_target.parent / (".jarvis-backup-" + change_id + "-apply")
                recovery_target.rename(recovery_backup)  # Disposable simulation of interruption after first native rename.
                page.get_by_role("button", name="Load checkpoint history", exact=True).click()
                page.get_by_role("button", name="Approve checkpoint read", exact=True).click()
                page.get_by_role("button", name="Inspect interrupted recovery-fixture.txt", exact=True).click()
                page.get_by_role("button", name="Approve checkpoint read", exact=True).click()
                expect(page.get_by_text("Recovery action: restore_missing. Resulting checkpoint state: proposed.", exact=True)).to_be_visible()
                page.get_by_role("button", name="Request recovery approval", exact=True).click()
                expect(page.get_by_role("button", name="Approve exact recovery", exact=True)).to_be_visible()
                assert not recovery_target.exists() and recovery_backup.exists()
                page.screenshot(path=str(output / "recovery.png"), full_page=True)
                page.get_by_role("button", name="Approve exact recovery", exact=True).click()
                expect(page.get_by_text("Recovery verified. Checkpoint state: proposed.", exact=True)).to_be_visible()
                assert recovery_target.read_bytes() == b"original recovery fixture"
                assert recovery_target.stat().st_ino == recovery_inode and not recovery_backup.exists()
                record("actual interrupted-state fixture inspection, fresh exact recovery approval and owned-inode restoration")
                page.get_by_label("New file path", exact=True).fill("second-proposal.py")
                page.get_by_role("button", name="Draft new file", exact=True).click()
                page.get_by_label("Proposed file content", exact=True).fill("print('second proposed file')\n")
                page.get_by_role("button", name="Review proposed patch", exact=True).click()
                page.get_by_role("button", name="Approve this read", exact=True).click()
                expect(page.get_by_text("Exact diff", exact=True)).to_be_visible()
                page.get_by_role("button", name="Load checkpoint history", exact=True).click()
                page.get_by_role("button", name="Approve checkpoint read", exact=True).click()
                page.get_by_role("button", name=re.compile(r"^Review recovery-fixture\.txt checkpoint ")).click()
                page.get_by_role("button", name="Approve checkpoint read", exact=True).click()
                expect(page.get_by_label("Proposed file content", exact=True)).to_have_value("proposed recovery fixture")
                page.get_by_role("button", name="Request apply approval", exact=True).click()
                page.get_by_role("button", name="Approve exact patch", exact=True).click()
                expect(page.get_by_role("button", name="Request checkpoint rollback", exact=True)).to_be_visible()
                assert recovery_target.read_text() == "proposed recovery fixture"
                assert not (recovery_target.parent / "second-proposal.py").exists()
                record("actual multiple durable proposals reopen one exact diff and apply only its separately approved file")
                selector.select_option(label="Beta fixture")
                selector.select_option(label="Alpha fixture")
                page.get_by_role("button", name="Approve this read", exact=True).click()
                # Await the actual selected-project listing before another action;
                # approving starts asynchronous continuation, not synchronous UI completion.
                expect(page.get_by_role("button", name="File: recovery-fixture.txt", exact=True)).to_be_visible()
                expect(page.get_by_role("button", name="Load checkpoint history", exact=True)).to_be_enabled()
                page.get_by_role("button", name="Load checkpoint history", exact=True).click()
                page.get_by_role("button", name="Approve checkpoint read", exact=True).click()
                page.get_by_role("button", name=re.compile(r"^Review recovery-fixture\.txt checkpoint ")).click()
                page.get_by_role("button", name="Approve checkpoint read", exact=True).click()
                page.get_by_role("button", name="Request checkpoint rollback", exact=True).click()
                page.get_by_role("button", name="Approve checkpoint rollback", exact=True).click()
                expect(page.get_by_text("Checkpoint rollback verified.", exact=True)).to_be_visible()
                assert recovery_target.read_bytes() == b"original recovery fixture"
                assert not (recovery_target.parent / "second-proposal.py").exists()
                page.screenshot(path=str(output / "multifile.png"), full_page=True)
                record("actual project reconnect preserves pending proposals and reopens an applied checkpoint for selective rollback")
                page.get_by_label("Command executable path", exact=True).fill(sys.executable)
                page.get_by_label("Command arguments JSON", exact=True).fill(json.dumps(["-c", "from pathlib import Path; Path('command-proof.txt').write_text('actual command'); print('fixture command failure'); raise SystemExit(7)"]))
                page.get_by_role("button", name="Review command", exact=True).click()
                page.get_by_role("button", name="Approve command preparation read", exact=True).click()
                page.get_by_role("button", name="Request command approval", exact=True).click()
                expect(page.get_by_role("button", name="Approve and run exact command", exact=True)).to_be_visible()
                assert not (fixture / "Alpha fixture" / "command-proof.txt").exists()
                page.get_by_role("button", name="Approve and run exact command", exact=True).click()
                expect(page.get_by_text("Command outcome: failed. Exit code: 7.", exact=True)).to_be_visible()
                expect(page.get_by_label("Command output", exact=True)).to_contain_text("fixture command failure")
                assert (fixture / "Alpha fixture" / "command-proof.txt").read_text() == "actual command"
                assert not (fixture / "Beta fixture" / "command-proof.txt").exists()
                page.get_by_role("heading", name="Test and build commands", exact=True).scroll_into_view_if_needed()
                page.screenshot(path=str(output / "command.png"), full_page=True)
                record("actual exact command approval executed selected fixture and reported nonzero exit as failure")
                subprocess.run([shutil.which("git.exe"), "-C", str(fixture / "Alpha fixture"), "init", "-q"], check=True, capture_output=True)
                page.get_by_role("button", name="Review Git status", exact=True).click()
                page.get_by_role("button", name="Approve command preparation read", exact=True).click()
                page.get_by_role("button", name="Request command approval", exact=True).click()
                expect(page.get_by_role("button", name="Approve and run exact command", exact=True)).to_be_visible()
                page.get_by_role("button", name="Approve and run exact command", exact=True).click()
                expect(page.get_by_text("Command outcome: success. Exit code: 0.", exact=True)).to_be_visible()
                expect(page.get_by_label("Command output", exact=True)).to_contain_text("accepted.txt")
                expect(page.get_by_label("Command output", exact=True)).to_contain_text("command-proof.txt")
                assert (fixture / "Alpha fixture" / "accepted.txt").read_text() == "fixture result"
                assert not (fixture / "Alpha fixture" / ".git" / "index.lock").exists()
                page.screenshot(path=str(output / "git.png"), full_page=True)
                record("actual Git preset, separate preparation/read approval and exact execution inspection preserved project files")
                selector.select_option(label="Beta fixture")
                expect(page.get_by_label("Proposed file content", exact=True)).to_have_count(0)
                expect(page.get_by_role("button", name="Approve this read", exact=True)).to_be_visible()
                selector.select_option(label="Alpha fixture")
                record("Coding Studio clears prior project content on workspace switch")
                page.get_by_role("button", name="JARVIS overview", exact=True).click()
                page.screenshot(path=str(output / "dashboard.png"), full_page=True)
                page.reload()
                expect(page.get_by_label("Pairing code", exact=True)).to_be_visible()
                page.get_by_label("Pairing code", exact=True).fill(codes.get(timeout=10))
                page.get_by_role("button", name="Pair dashboard", exact=True).click()
                expect(page.get_by_label("Selected workspace")).to_be_enabled()
                record("dashboard reload re-pairs with replacement code")
                with httpx.Client(timeout=5, trust_env=False) as client:
                    assert client.delete(api + "/api/v1/session", headers={"Authorization": "Bearer " + token}).status_code == 200
                    assert client.get(api + "/api/v1/tasks", headers={"Authorization": "Bearer " + token}).status_code == 401
                record("explicit session revocation denied subsequent requests")
                context.close()
                browser.close()
        finally:
            for process in reversed(owned):
                if process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
                process.wait(timeout=10)
                process.stdout.close()
    (output / "acceptance.json").write_text(json.dumps({"platform": "Windows", "browser": "installed Edge headless", "model": "mock (no live inference)", "persistence": "temporary fixture removed", "steps": evidence}, indent=2), encoding="utf-8")
    print("Evidence directory: " + str(output), flush=True)


if __name__ == "__main__":
    main()
