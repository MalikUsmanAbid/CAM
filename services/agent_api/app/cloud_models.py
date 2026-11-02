"""Credential-free cloud endpoint contracts shared by bootstrap and adapters."""

from urllib.parse import urlsplit

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
ANTHROPIC_ENDPOINT = "https://api.anthropic.com/v1/messages"
CLOUD_PROVIDERS = frozenset({"gemini", "anthropic", "openai_compatible"})


def cloud_endpoint(provider: str, endpoint: str | None) -> str:
    value = (endpoint or "").strip().rstrip("/")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid cloud endpoint port.") from None
    if (provider not in CLOUD_PROVIDERS or parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)):
        raise ValueError("Cloud models require an explicit HTTPS endpoint without credentials or query parameters.")
    fixed = {"gemini": GEMINI_ENDPOINT, "anthropic": ANTHROPIC_ENDPOINT}.get(provider)
    if fixed and value != fixed:
        raise ValueError("Named cloud providers require their exact official endpoint.")
    return value
