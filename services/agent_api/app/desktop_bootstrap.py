"""Private parent/sidecar protocol. Never expose this channel as an HTTP route.

The parent must provide dedicated inherited stdin/stdout pipes and enforce
ownership of the child process tree. This module does not establish that OS
ownership or authenticate an arbitrary desktop webview.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.agent_api.app.cloud_models import CLOUD_PROVIDERS, cloud_endpoint

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 8192


class DesktopConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int = Field(ge=1, le=1)
    action: str = Field(pattern=r"^configure$")
    data_directory: str = Field(min_length=1, max_length=1000)
    workspace_directory: str = Field(min_length=1, max_length=1000)
    provider: str = Field(default="mock", pattern=r"^(mock|ollama|gemini|anthropic|openai_compatible)$")
    model: str = Field(default="jarvis-dev", min_length=1, max_length=200)
    endpoint: str | None = Field(default=None, max_length=500)
    credential_reference: str | None = Field(default=None, pattern=r"^vault:[a-f0-9]{32}$")
    voice_enabled: bool = False
    voice_endpoint: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def cloud_scope(self):
        if self.provider in CLOUD_PROVIDERS:
            self.endpoint = cloud_endpoint(self.provider, self.endpoint)
        elif self.credential_reference is not None:
            raise ValueError("Local providers do not accept a cloud credential reference.")
        return self

    @field_validator("data_directory", "workspace_directory")
    @classmethod
    def absolute_directory(cls, value: str) -> str:
        path = Path(value)
        if "\x00" in value or not path.is_absolute():
            raise ValueError("Desktop directories must be absolute paths.")
        return str(path.resolve())


def read_frame(channel: BinaryIO) -> dict:
    line = channel.readline(MAX_FRAME_BYTES + 1)
    if not line or len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
        raise ValueError("Desktop control frame is missing or exceeds its bound.")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate desktop control field.")
            result[key] = value
        return result

    value = json.loads(line.decode("utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise ValueError("Desktop control frame must be an object.")
    return value


class DesktopBootstrap:
    def __init__(self, output: BinaryIO):
        self.output = output
        self._lock = threading.Lock()
        self._published = False
        self._token = None

    def publish(self, sessions, *, port: int) -> None:
        # Only the process-local SessionManager issues tokens. No second
        # authentication system or persisted desktop master secret is created.
        with self._lock:
            if self._published:
                raise RuntimeError("Desktop bootstrap was already published.")
            token = sessions.pair(sessions.pairing_code)
            frame = json.dumps({"version": PROTOCOL_VERSION, "event": "ready",
                                "endpoint": f"http://127.0.0.1:{port}", "token": token})
            self.output.write(frame.encode("utf-8") + b"\n")
            self.output.flush()
            self._published = True
            self._token = token

    def renew(self, sessions, *, port, request_id):
        with self._lock:
            if not self._published or not isinstance(request_id, str) or len(request_id) != 32 or any(value not in '0123456789abcdef' for value in request_id):
                raise ValueError('Invalid private session renewal.')
            if self._token:
                sessions.revoke(self._token)
            token = sessions.pair(sessions.rotate_pairing())
            frame = {'version': PROTOCOL_VERSION, 'event': 'session_renewed', 'endpoint': f'http://127.0.0.1:{port}',
                     'token': token, 'request_id': request_id}
            self.output.write(json.dumps(frame).encode() + b'\n')
            self.output.flush()
            self._token = token

    @staticmethod
    def control(channel: BinaryIO, request_shutdown, request_renew=None) -> None:
        # Closing the controlling pipe also requests shutdown. Invalid commands
        # fail closed and are not reflected into logs containing input values.
        try:
            while True:
                frame = read_frame(channel)
                if frame == {"version": PROTOCOL_VERSION, "action": "shutdown"}:
                    break
                if (request_renew is not None and set(frame) == {'version', 'action', 'request_id'}
                        and type(frame['version']) is int and frame['version'] == PROTOCOL_VERSION
                        and frame['action'] == 'renew_session' and isinstance(frame['request_id'], str)
                        and len(frame['request_id']) == 32 and all(value in '0123456789abcdef' for value in frame['request_id'])):
                    request_renew(frame['request_id'])
                else:
                    raise ValueError("Unsupported desktop control action.")
        except (ValueError, UnicodeError, OSError):
            pass
        finally:
            request_shutdown()
