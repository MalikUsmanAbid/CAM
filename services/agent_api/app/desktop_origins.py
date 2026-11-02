"""Packaged origins are enabled only for private desktop bootstrap instances."""

from fastapi.middleware.cors import CORSMiddleware

DESKTOP_ORIGINS = ("http://tauri.localhost", "tauri://localhost")


class DesktopCORSMiddleware:
    def __init__(self, app):
        self.app = app
        self.desktop = CORSMiddleware(
            app, allow_origins=list(DESKTOP_ORIGINS), allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type", "Authorization", "Last-Event-ID"],
        )

    async def __call__(self, scope, receive, send):
        root = scope.get("app")
        if root is not None and getattr(root.state, "desktop_bootstrap", None) is not None:
            await self.desktop(scope, receive, send)
        else:
            await self.app(scope, receive, send)
