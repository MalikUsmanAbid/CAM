"""Never allow an API test to initialize the user's database or workspace."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_application_environment(monkeypatch, tmp_path, request):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "application.sqlite"))
    monkeypatch.setenv("SAFE_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("BRAIN_PROVIDER", "mock")
    monkeypatch.setenv("JARVIS_ALLOW_DATABASE_MIGRATION", "1")
    original_enter = TestClient.__enter__

    def enter(client):
        original_enter(client)
        if not request.node.get_closest_marker("unpaired"):
            response = client.post("/api/v1/session/pair", json={"code": client.app.state.sessions.pairing_code})
            assert response.status_code == 200
            client.headers["Authorization"] = "Bearer " + response.json()["token"]
        return client

    monkeypatch.setattr(TestClient, "__enter__", enter)


def pytest_configure(config):
    config.addinivalue_line("markers", "unpaired: test real unauthenticated API boundaries without automatic test pairing")


@pytest.fixture
def anyio_backend():
    # Executors and cancellation use asyncio APIs, not Trio.
    return "asyncio"
