"""Risk policy and stable fingerprints for server-side approval grants."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def is_allowed(risk: RiskLevel, approved: bool = False) -> bool:
    return risk is RiskLevel.LOW or approved


def canonical_arguments(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_fingerprint(tool_name: str, arguments: dict[str, Any], risk: RiskLevel) -> str:
    return hashlib.sha256(
        f"{tool_name}:{risk}:{canonical_arguments(arguments)}".encode("utf-8")
    ).hexdigest()
