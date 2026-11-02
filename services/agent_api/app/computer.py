"""Bounded, injected desktop-control adapter exposed only as registered tools."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, Field

from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.tools import Tool, ToolRegistry

MAX_ACTIONS = 12
MAX_SCREEN_TEXT = 12_000
MAX_COMPUTER_SESSIONS = 3


class ComputerControlAdapter(Protocol):
    async def observe(self) -> dict[str, str]: ...
    async def act(self, action: str, target: str, value: str = "") -> dict[str, str]: ...
    async def close(self) -> None: ...


class ComputerSessionInput(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)


class ComputerActionInput(ComputerSessionInput):
    target: str = Field(default="", max_length=300)
    value: str = Field(default="", max_length=2_000)


@dataclass
class ComputerSession:
    session_id: str
    adapter: ComputerControlAdapter
    allowed_windows: tuple[str, ...]
    task_id: str | None
    actions: int = 0
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))


class ComputerManager:
    def __init__(self) -> None:
        self._sessions: dict[str, ComputerSession] = {}

    def create(
        self,
        session_id: str,
        adapter: ComputerControlAdapter,
        allowed_windows: list[str],
        task_id: str | None = None,
    ) -> ComputerSession:
        if (
            session_id in self._sessions
            or len(self._sessions) >= MAX_COMPUTER_SESSIONS
            or not allowed_windows
            or len(allowed_windows) > 20
            or any(not title or len(title) > 200 for title in allowed_windows)
        ):
            raise ValueError("Computer session is invalid.")
        session = ComputerSession(session_id, adapter, tuple(allowed_windows), task_id)
        self._sessions[session_id] = session
        return session

    async def observe(self, data: ComputerSessionInput) -> dict[str, str]:
        session = await self._get(data.session_id)
        self._tick(session)
        result = await session.adapter.observe()
        text = result.get("text", "")
        if len(text) > MAX_SCREEN_TEXT:
            raise ValueError("Screen extraction exceeded the allowed size.")
        return {"text": text, "trust": "untrusted_data"}

    async def action(self, action: str, data: ComputerActionInput) -> dict[str, str]:
        session = await self._get(data.session_id)
        self._tick(session)
        target_window = data.target.split("::", maxsplit=1)[0]
        if data.target and target_window not in session.allowed_windows:
            raise ValueError("Computer target is not allowed.")
        return await session.adapter.act(action, data.target, data.value)

    async def _get(self, session_id: str) -> ComputerSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError("Computer session is unavailable.")
        if datetime.now(UTC) - session.last_activity > timedelta(minutes=10):
            self._sessions.pop(session_id, None)
            await session.adapter.close()
            raise ValueError("Computer session expired.")
        return session

    async def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            await session.adapter.close()

    async def close_all(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(session.adapter.close() for session in sessions), return_exceptions=True)

    def _tick(self, session: ComputerSession) -> None:
        session.actions += 1
        session.last_activity = datetime.now(UTC)
        if session.actions > MAX_ACTIONS:
            raise ValueError("Computer action limit exceeded.")


class PywinautoUIAAdapter:
    """Attended control of explicitly selected windows through Windows UI Automation."""

    _KEYS = {
        "ENTER": "{ENTER}",
        "TAB": "{TAB}",
        "ESC": "{ESC}",
        "BACKSPACE": "{BACKSPACE}",
        "DELETE": "{DELETE}",
        "UP": "{UP}",
        "DOWN": "{DOWN}",
        "LEFT": "{LEFT}",
        "RIGHT": "{RIGHT}",
    }
    _HOTKEYS = {"CTRL+A": "^a", "CTRL+Z": "^z", "CTRL+Y": "^y", "CTRL+F": "^f"}

    def __init__(self, allowed_windows: tuple[str, ...]) -> None:
        self.allowed_windows = allowed_windows
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-uia")
        self._desktop = None
        self._windows: dict[str, object] = {}
        self._closed = False

    @classmethod
    async def create(cls, allowed_windows: tuple[str, ...]) -> "PywinautoUIAAdapter":
        adapter = cls(allowed_windows)
        try:
            await adapter._call(adapter._attach)
            return adapter
        except Exception:
            await adapter.close()
            raise

    async def _call(self, function, *args):
        if self._closed:
            raise ValueError("Computer-control session is closed.")
        future = self._executor.submit(function, *args)
        return await asyncio.wrap_future(future)

    def _attach(self) -> None:
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise RuntimeError("Install the optional computer dependency to enable Windows control.") from exc
        self._desktop = Desktop(backend="uia")
        visible = {
            window.window_text(): window
            for window in self._desktop.windows()
            if window.is_visible() and window.window_text()
        }
        missing = [title for title in self.allowed_windows if title not in visible]
        if missing:
            raise ValueError("One or more allowed windows are not currently visible.")
        self._windows = {title: visible[title] for title in self.allowed_windows}

    def _window_and_control(self, target: str):
        title, separator, control_name = target.partition("::")
        window = self._windows.get(title)
        if window is None:
            raise ValueError("Computer target is not allowed.")
        if not separator:
            return window, window
        if not control_name or len(control_name) > 200:
            raise ValueError("Computer control target is invalid.")
        control = window.child_window(title=control_name, found_index=0).wrapper_object()
        if not control.is_visible() or not control.is_enabled():
            raise ValueError("Computer control target is unavailable.")
        return window, control

    def _observe(self) -> dict[str, str]:
        lines: list[str] = []
        for title, window in self._windows.items():
            lines.append(f"Window: {title}")
            for control in window.descendants():
                if control.friendly_class_name() == "Edit":
                    continue
                text = control.window_text().strip()
                if text:
                    lines.append(f"{control.friendly_class_name()}: {text}")
                if len("\n".join(lines)) >= MAX_SCREEN_TEXT:
                    break
        return {"text": "\n".join(lines)[:MAX_SCREEN_TEXT]}

    async def observe(self) -> dict[str, str]:
        return await self._call(self._observe)

    def _act(self, action: str, target: str, value: str) -> dict[str, str]:
        window, control = self._window_and_control(target)
        if action == "get_window_state":
            rect = window.rectangle()
            return {
                "title": window.window_text(),
                "visible": str(window.is_visible()).lower(),
                "enabled": str(window.is_enabled()).lower(),
                "bounds": f"{rect.left},{rect.top},{rect.right},{rect.bottom}",
            }
        if action == "locate":
            rect = control.rectangle()
            return {
                "control_type": control.element_info.control_type,
                "name": control.window_text()[:200],
                "bounds": f"{rect.left},{rect.top},{rect.right},{rect.bottom}",
            }
        if action == "focus_window":
            window.set_focus()
        elif action == "click":
            control.click_input()
        elif action == "double_click":
            control.double_click_input()
        elif action == "type_text":
            if len(value) > 2_000 or not value:
                raise ValueError("Computer text input is invalid.")
            control.set_focus()
            control.type_keys(value, with_spaces=True, pause=0.01)
        elif action == "key_press":
            key = value.upper()
            if key not in self._KEYS:
                raise ValueError("Computer key is not on the safe allowlist.")
            control.set_focus()
            control.type_keys(self._KEYS[key])
        elif action == "hotkey":
            chord = value.upper().replace(" ", "")
            if chord not in self._HOTKEYS:
                raise ValueError("Computer hotkey is not on the safe allowlist.")
            control.set_focus()
            control.type_keys(self._HOTKEYS[chord])
        elif action == "scroll":
            try:
                distance = int(value or "1")
            except ValueError as exc:
                raise ValueError("Computer scroll value is invalid.") from exc
            if not -5 <= distance <= 5:
                raise ValueError("Computer scroll value is out of range.")
            rect = control.rectangle()
            from pywinauto import mouse

            mouse.wheel(coords=((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2), wheel_dist=distance)
        elif action == "move_pointer":
            rect = control.rectangle()
            from pywinauto import mouse

            mouse.move(coords=((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2))
        elif action == "close_window":
            window.close()
            self._windows.pop(window.window_text(), None)
        else:
            raise ValueError("Computer action is unsupported by the UI Automation adapter.")
        return {"action": action, "verification": "completed", "window": window.window_text()[:200]}

    async def act(self, action: str, target: str, value: str = "") -> dict[str, str]:
        return await self._call(self._act, action, target, value)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._windows.clear()
        self._desktop = None
        self._executor.shutdown(wait=False, cancel_futures=True)


def register_computer_tools(registry: ToolRegistry, manager: ComputerManager, *, enabled: bool = True) -> None:
    registry.register(
        Tool(
            "computer_inspect_screen",
            "Read bounded untrusted screen text.",
            ComputerSessionInput,
            RiskLevel.LOW,
            10,
            manager.observe,
            enabled=enabled,
        )
    )
    for action in (
        "locate",
        "click",
        "double_click",
        "type_text",
        "key_press",
        "hotkey",
        "scroll",
        "move_pointer",
        "focus_window",
        "get_window_state",
        "close_window",
    ):
        registry.register(
            Tool(
                f"computer_{action}",
                f"Controlled computer {action}.",
                ComputerActionInput,
                RiskLevel.MEDIUM,
                10,
                lambda data, name=action: manager.action(
                    name, ComputerActionInput.model_validate(data)
                ),
                enabled=enabled,
            )
        )
