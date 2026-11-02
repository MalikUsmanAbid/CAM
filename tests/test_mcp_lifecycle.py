from fastapi.testclient import TestClient

from services.agent_api.app.main import app


def test_mcp_server_lifecycle_is_disabled_approved_and_redacted(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "jarvis.db"))
    monkeypatch.setenv("SAFE_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("MCP_TEST_TOKEN", "a-test-only-secret")

    class FakeTransport:
        async def discover(self):
            return []

        async def close(self):
            pass

    async def open_transport(_endpoint, secret=None):
        assert secret == "a-test-only-secret"
        return FakeTransport()

    async def validate(_endpoint):
        pass

    monkeypatch.setattr(
        "services.agent_api.app.main.StreamableHTTPMCPTransport.open", open_transport
    )
    monkeypatch.setattr("services.agent_api.app.main.validate_streamable_endpoint", validate)
    with TestClient(app) as client:
        configured = client.post(
            "/api/v1/mcp/servers",
            json={
                "server_id": "fixture",
                "name": "Local fixture",
                "endpoint": "https://mcp.example.com/mcp",
                "secret_reference": "env:MCP_TEST_TOKEN",
            },
        )
        assert configured.status_code == 201
        assert configured.json()["enabled"] is False
        assert configured.json()["credential_reference_configured"] is True
        assert "secret_reference" not in configured.json()
        assert "a-test-only-secret" not in configured.text

        refused = client.post("/api/v1/mcp/servers/fixture/enable")
        assert refused.status_code == 400

        assert client.post("/api/v1/mcp/servers/fixture/approve").status_code == 200
        enabled = client.post("/api/v1/mcp/servers/fixture/enable")
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True
        assert client.get("/api/v1/mcp").json()[0]["enabled"] is True
        assert client.post("/api/v1/mcp/servers/fixture/disable").json()["enabled"] is False
        assert client.delete("/api/v1/mcp/servers/fixture").status_code == 204
