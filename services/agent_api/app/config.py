"""Safe application configuration loaded from environment variables."""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from services.agent_api.app.cloud_models import CLOUD_PROVIDERS, cloud_endpoint


def _load_env_file() -> None:
    """Load simple KEY=VALUE pairs without overwriting process environment."""
    repository_root = Path(__file__).resolve().parents[3]
    for path in (Path.cwd() / ".env", repository_root / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", maxsplit=1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return


@dataclass(frozen=True, slots=True)
class Settings:
    ai_provider: str
    ai_model: str
    ai_api_key_ref: str | None
    ai_endpoint: str | None
    ai_timeout_seconds: float
    ai_max_output_tokens: int
    backend_host: str
    backend_port: int
    database_path: Path
    safe_workspace: Path
    brain_provider: str = "mock"
    brain_model: str = "jarvis-dev"
    brain_endpoint: str | None = None
    brain_api_key_ref: str | None = None

    @property
    def provider_configured(self) -> bool:
        if self.ai_provider == "mock":
            return True
        if self.ai_provider == "ollama":
            try:
                return bool(_ollama_endpoint(self.ai_endpoint))
            except ValueError:
                return False
        if self.ai_provider not in CLOUD_PROVIDERS or not self.ai_endpoint:
            return False
        if self.ai_provider != "openai_compatible":
            try:
                cloud_endpoint(self.ai_provider, self.ai_endpoint)
            except ValueError:
                return False
        return self._credential_available(self.ai_api_key_ref)

    @property
    def brain_provider_configured(self) -> bool:
        if self.brain_provider == "mock":
            return True
        if self.brain_provider != "openai_compatible" or not self.brain_endpoint:
            return False
        return self._credential_available(self.brain_api_key_ref)

    def _credential_available(self, reference):
        from services.agent_api.app.credentials import CredentialVault

        try:
            return bool(CredentialVault(self.database_path.parent / "credentials").resolve(reference))
        except (ValueError, OSError):
            return False


def _ollama_endpoint(value: str | None) -> str:
    if value is not None and len(value) > 500:
        raise ValueError("Ollama endpoint exceeds the supported length.")
    parsed = urlparse(value or "")
    try:
        local = ipaddress.ip_address(parsed.hostname or "").is_loopback
        port = parsed.port
    except ValueError:
        local = False
        port = None
    if not local or parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment or (port is not None and not 1 <= port <= 65535):
        raise ValueError("Ollama requires an explicit HTTP numeric loopback endpoint without credentials or query parameters.")
    return (value or "").rstrip("/")


def _bounded_text(name: str, value: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum} characters.")
    return normalized


def _bounded_integer(name: str, value: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid integer.") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return parsed


def _bounded_timeout(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError("AI_TIMEOUT_SECONDS must be a valid number.") from exc
    if not 0.1 <= parsed <= 60:
        raise ValueError("AI_TIMEOUT_SECONDS must be between 0.1 and 60.")
    return parsed


def _optional_endpoint(value: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > 500 or urlparse(normalized).scheme not in {"http", "https"}:
        raise ValueError("AI_ENDPOINT must be an http(s) URL of at most 500 characters.")
    return normalized


def _optional_reference(name: str, value: str, maximum: int = 200) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise ValueError(f"{name} must be at most {maximum} characters.")
    return normalized


def _brain_provider(value: str) -> str:
    normalized = _bounded_text("BRAIN_PROVIDER", value, 50).lower()
    if normalized not in {"mock", "openai_compatible"}:
        raise ValueError("BRAIN_PROVIDER must be mock or openai_compatible.")
    return normalized


def _secret_reference(value: str) -> str | None:
    normalized = value.strip()
    return normalized if re.fullmatch(r"(?:env:[A-Za-z_][A-Za-z0-9_]{0,127}|vault:[a-f0-9]{32})", normalized) else None


def _ai_secret_reference(value: str) -> str | None:
    return _secret_reference(value)


def _brain_endpoint(value: str) -> str | None:
    endpoint = _optional_endpoint(value)
    if endpoint is None:
        return None
    parsed = urlparse(endpoint)
    if parsed.username or parsed.password or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("Provider endpoints must not contain credentials, query, or fragment values.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Provider endpoint port is invalid.") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Provider endpoint port is invalid.")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname.lower() == "localhost"
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("Provider endpoints must use HTTPS except for a loopback development endpoint.")
    return endpoint.rstrip("/")


def _loopback_host(value: str) -> str:
    normalized = _bounded_text("BACKEND_HOST", value, 100)
    try:
        loopback = ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        loopback = normalized.lower() == "localhost"
    if not loopback:
        raise ValueError("BACKEND_HOST must be localhost or a loopback IP; remote access is unsupported.")
    return normalized


def get_settings(*, load_env_file: bool = True) -> Settings:
    if load_env_file:
        _load_env_file()
    repository_root = Path(__file__).resolve().parents[3]
    port = _bounded_integer("BACKEND_PORT", os.getenv("BACKEND_PORT", "8000"), 1, 65535)
    return Settings(
        ai_provider=_bounded_text("AI_PROVIDER", os.getenv("AI_PROVIDER", "mock"), 50).lower(),
        ai_model=_bounded_text("AI_MODEL", os.getenv("AI_MODEL", "jarvis-dev"), 200),
        ai_api_key_ref=_ai_secret_reference(os.getenv("AI_API_KEY_REF", "")),
        ai_endpoint=_brain_endpoint(os.getenv("AI_ENDPOINT", "")),
        ai_timeout_seconds=_bounded_timeout(os.getenv("AI_TIMEOUT_SECONDS", "15")),
        ai_max_output_tokens=_bounded_integer(
            "AI_MAX_OUTPUT_TOKENS", os.getenv("AI_MAX_OUTPUT_TOKENS", "1024"), 1, 4096
        ),
        backend_host=_loopback_host(os.getenv("BACKEND_HOST", "127.0.0.1")),
        backend_port=port,
        database_path=_database_path(repository_root),
        safe_workspace=Path(
            os.getenv("SAFE_WORKSPACE", str(repository_root / "workspace"))
        ).resolve(),
        brain_provider=_brain_provider(os.getenv("BRAIN_PROVIDER", "mock")),
        brain_model=_bounded_text("BRAIN_MODEL", os.getenv("BRAIN_MODEL", os.getenv("AI_MODEL", "jarvis-dev")), 200),
        brain_endpoint=_brain_endpoint(os.getenv("BRAIN_ENDPOINT", "")),
        brain_api_key_ref=_secret_reference(os.getenv("BRAIN_API_KEY_REF", "")),
    )


def _database_path(repository_root: Path) -> Path:
    configured = os.getenv("DATABASE_PATH")
    directory = os.getenv("JARVIS_DATA_DIR")
    if not configured and not directory and (repository_root / "data" / "jarvis.db").exists():
        raise ValueError("Legacy database detected. Select DATABASE_PATH and authorize its migration before startup; no data was moved.")
    default_directory = Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local" / "share"))) / "USMANJARVIS"
    raw = configured or str(Path(directory) / "jarvis.db" if directory else default_directory / "jarvis.db")
    if raw.startswith(("\\\\", "//")):
        raise ValueError("Active databases on network directories are unsupported.")
    path = Path(raw).resolve()
    if any(part.lower().startswith(("onedrive", "dropbox")) for part in path.parts):
        raise ValueError("Active databases in cloud-synced directories are unsupported. Choose a local data directory.")
    for name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer", "DROPBOX_PATH"):
        location = os.getenv(name)
        if location and path.is_relative_to(Path(location).resolve()):
            raise ValueError("Active databases in cloud-synced directories are unsupported. Choose a local data directory.")
    if os.name == "nt":
        import ctypes

        # DRIVE_REMOTE is 4. Mapped network drives need the same restriction as UNC.
        get_drive_type = ctypes.windll.kernel32.GetDriveTypeW
        get_drive_type.argtypes = [ctypes.c_wchar_p]
        get_drive_type.restype = ctypes.c_uint
        if get_drive_type(str(path.anchor)) == 4:
            raise ValueError("Active databases on network drives are unsupported.")
    return path
