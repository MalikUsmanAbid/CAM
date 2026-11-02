"""Deferred M3 regressions; memory-only protocol fixtures, no user persistence."""

import io
import json

import pytest
from pydantic import ValidationError

from services.agent_api.app.desktop_bootstrap import (
    MAX_FRAME_BYTES,
    DesktopBootstrap,
    DesktopConfiguration,
    read_frame,
)
from services.agent_api.app.session_security import SessionManager


@pytest.mark.parametrize("payload", [
    b"", b"[]\n", b'{"version":1,"version":1}\n', b"{}",
    b"x" * (MAX_FRAME_BYTES + 1) + b"\n", b"\xff\n",
])
def test_reject_invalid_control_frames(payload):
    with pytest.raises((ValueError, UnicodeError)):
        read_frame(io.BytesIO(payload))


def test_configuration_requires_explicit_absolute_paths(tmp_path):
    frame = {"version": 1, "action": "configure", "data_directory": str(tmp_path),
             "workspace_directory": str(tmp_path / "workspace")}
    assert DesktopConfiguration.model_validate(frame).provider == "mock"
    for change in ({"data_directory": "relative"}, {"allow_migration": True},
                   {"provider": "openai_compatible"}, {"provider": "unsupported_provider"}, {"version": True}):
        with pytest.raises(ValidationError):
            DesktopConfiguration.model_validate(frame | change)


def test_session_is_process_local_single_publication_and_not_pairing_secret():
    sessions = SessionManager()
    code = sessions.pairing_code
    output = io.BytesIO()
    bootstrap = DesktopBootstrap(output)
    bootstrap.publish(sessions, port=12345)
    result = json.loads(output.getvalue())
    assert result["endpoint"] == "http://127.0.0.1:12345"
    assert sessions.authenticate(result["token"]) is not None
    assert SessionManager().authenticate(result["token"]) is None
    assert code not in output.getvalue().decode()
    assert sessions.pairing_code == ""
    with pytest.raises(RuntimeError):
        bootstrap.publish(sessions, port=12345)


@pytest.mark.parametrize("payload", [
    b'{"version":1,"action":"shutdown"}\n', b"", b'{"action":"execute"}\n',
])
def test_control_disconnect_and_invalid_commands_shutdown_without_execution(payload):
    requests = []
    DesktopBootstrap.control(io.BytesIO(payload), lambda: requests.append("stop"))
    assert requests == ["stop"]


def test_private_session_reissue_revokes_previous_actor_without_persisting_token():
    sessions = SessionManager()
    output = io.BytesIO()
    bootstrap = DesktopBootstrap(output)
    bootstrap.publish(sessions, port=12345)
    original = json.loads(output.getvalue().splitlines()[0])
    original_actor = sessions.authenticate(original['token']).identity
    bootstrap.renew(sessions, port=12345, request_id='a' * 32)
    renewed = json.loads(output.getvalue().splitlines()[1])
    assert renewed['event'] == 'session_renewed'
    assert renewed['request_id'] == 'a' * 32
    assert sessions.authenticate(original['token']) is None
    assert sessions.authenticate(renewed['token']).identity != original_actor
    assert sessions.pairing_code == ''


def test_private_control_handles_renewal_then_shutdown_once():
    requests = []
    renewed = []
    frames = b'{"version":1,"action":"renew_session","request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n{"version":1,"action":"shutdown"}\n'
    DesktopBootstrap.control(io.BytesIO(frames), lambda: requests.append('stop'), renewed.append)
    assert renewed == ['a' * 32]
    assert requests == ['stop']
