"""Python sidecar entry point; invoked only by the native desktop supervisor.

No command-line secrets, configuration file loading or implicit migration
consent. Dedicated inherited pipes carry one configuration and one session.
The native supervisor must contain this process in its owned Windows job.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading

from services.agent_api.app.desktop_bootstrap import (
    DesktopBootstrap,
    DesktopConfiguration,
    read_frame,
)


async def serve(configuration, bootstrap, control_input):
    import uvicorn

    from services.agent_api.app.config import get_settings
    from services.agent_api.app.desktop_origins import DesktopCORSMiddleware
    from services.agent_api.app.main import app

    # Explicit child configuration overrides inherited developer/provider
    # settings. In particular an inherited migration opt-in is never honored.
    values = {
        "JARVIS_DATA_DIR": configuration.data_directory,
        "SAFE_WORKSPACE": configuration.workspace_directory,
        "AI_PROVIDER": configuration.provider,
        "AI_MODEL": configuration.model,
        "AI_ENDPOINT": configuration.endpoint or "",
        "AI_API_KEY_REF": configuration.credential_reference or "",
        "BRAIN_PROVIDER": "mock",
        "BRAIN_MODEL": "jarvis-dev",
        "BRAIN_ENDPOINT": "",
        "BRAIN_API_KEY_REF": "",
        "BACKEND_HOST": "127.0.0.1",
        "AI_TIMEOUT_SECONDS": "60",
        "AI_MAX_OUTPUT_TOKENS": "1024",
        "JARVIS_STT_ENABLED": "1" if configuration.voice_enabled else "0",
        "JARVIS_STT_ENDPOINT": configuration.voice_endpoint or "",
    }
    os.environ.update(values)
    if configuration.voice_enabled:
        from services.agent_api.app.config import _ollama_endpoint
        _ollama_endpoint(configuration.voice_endpoint)
    os.environ.pop("DATABASE_PATH", None)
    os.environ.pop("JARVIS_ALLOW_DATABASE_MIGRATION", None)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        os.environ["BACKEND_PORT"] = str(port)
        settings = get_settings(load_env_file=False)
        if configuration.provider == "ollama" and not settings.provider_configured:
            raise ValueError("Local model endpoint is not configured.")
        # Lifespan reads explicit settings, avoiding developer .env files.
        app.state.desktop_bootstrap = bootstrap
        app.state.desktop_settings = settings
        app.add_middleware(DesktopCORSMiddleware)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                              access_log=False, log_level="warning"))
        loop = asyncio.get_running_loop()

        def request_shutdown():
            loop.call_soon_threadsafe(setattr, server, "should_exit", True)

        def request_renew(request_id):
            def renew():
                try:
                    bootstrap.renew(app.state.sessions, port=port, request_id=request_id)
                except (ValueError, OSError):
                    server.should_exit = True
            loop.call_soon_threadsafe(renew)

        controller = threading.Thread(target=bootstrap.control,
                                      args=(control_input, request_shutdown, request_renew), daemon=True,
                                      name="jarvis-desktop-control")
        controller.start()
        await server.serve(sockets=[listener])


def main() -> int:
    if sys.argv[1:] == ['--uia-worker']:
        from services.agent_api.app.uia_worker import main as worker_main
        return worker_main()
    protocol_output = sys.stdout.buffer
    control_input = sys.stdin.buffer
    # Reserve stdout solely for private protocol frames. Application print/log
    # output goes to stderr and cannot be mistaken for a session response.
    sys.stdout = sys.stderr
    try:
        configuration = DesktopConfiguration.model_validate(read_frame(control_input))
        asyncio.run(serve(configuration, DesktopBootstrap(protocol_output), control_input))
    except (ValueError, OSError, RuntimeError):
        # No raw exception or control payload: paths/input may contain secrets.
        print("Desktop backend startup failed; review local configuration and migration requirements.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
