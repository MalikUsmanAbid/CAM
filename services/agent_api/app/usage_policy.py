"""Atomic conservative model reservations. They are estimates, not provider invoices."""

from __future__ import annotations

import ipaddress
import json
import sqlite3
import uuid
from contextlib import closing
from contextvars import ContextVar
from datetime import UTC, datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.agent_api.app.providers import ProviderUnavailableError, model_endpoint

budget_task: ContextVar[str | None] = ContextVar("budget_task", default=None)


def local_endpoint(endpoint: str) -> bool:
    host = urlsplit(endpoint).hostname or ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


class CloudPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    endpoint: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1, max_length=200)
    enabled: bool = False
    transmission_consent: bool = False
    daily_micro_usd: int = Field(default=0, ge=0, le=1_000_000_000)
    task_micro_usd: int = Field(default=0, ge=0, le=1_000_000_000)
    input_micro_usd_per_million_tokens: int | None = Field(default=None, ge=1, le=1_000_000_000)
    output_micro_usd_per_million_tokens: int | None = Field(default=None, ge=1, le=1_000_000_000)

    @model_validator(mode="after")
    def explicit_cloud_configuration(self):
        self.endpoint = model_endpoint(self.endpoint)
        if self.enabled and (not self.transmission_consent or not self.daily_micro_usd or not self.task_micro_usd
                             or self.input_micro_usd_per_million_tokens is None
                             or self.output_micro_usd_per_million_tokens is None):
            raise ValueError("Cloud access requires transmission consent, positive limits and explicit pricing.")
        return self


class UsagePolicy:
    def __init__(self, path):
        self.path = path

    def configure(self, policy: CloudPolicy):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("INSERT INTO cloud_policies VALUES (?,?,?) ON CONFLICT(endpoint,model) DO UPDATE SET policy_json=excluded.policy_json",
                               (policy.endpoint, policy.model, policy.model_dump_json()))

    def status(self):
        with closing(sqlite3.connect(self.path)) as connection:
            policies = [json.loads(row[0]) for row in connection.execute("SELECT policy_json FROM cloud_policies")]
            spent = connection.execute("SELECT COALESCE(SUM(reserved_micro_usd),0) FROM model_reservations WHERE day=?", (datetime.now(UTC).date().isoformat(),)).fetchone()[0]
        return {"policies": policies, "reserved_today_micro_usd": spent, "default_cloud_enabled": False,
                "accounting": "Conservative reservations; uncertain or failed requests retain their reservation. Not a provider invoice."}

    def available(self, endpoint: str, model: str) -> bool:
        if local_endpoint(endpoint):
            return True
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT policy_json FROM cloud_policies WHERE endpoint=? AND model=?", (endpoint, model)).fetchone()
        return bool(row and CloudPolicy.model_validate_json(row[0]).enabled)

    def authorize(self, endpoint: str, payload: dict[str, object]):
        from services.agent_api.app.action_policy import ActionCategory, ActionPolicy
        if ActionPolicy(self.path).denied(ActionCategory.READ):
            raise ProviderUnavailableError("Current policy or emergency stop blocks model dispatch.")
        if local_endpoint(endpoint):
            return
        model = payload.get("model")
        output = payload.get("max_completion_tokens", payload.get("max_tokens"))
        if not isinstance(model, str) or type(output) is not int or not 1 <= output <= 32768:
            raise ProviderUnavailableError("Model cost cannot be bounded for this request.")
        # Byte-based overestimate includes serialized request and framing overhead.
        input_tokens = len(json.dumps(payload, ensure_ascii=False).encode()) * 2 + 8192
        task = budget_task.get() or "chat-" + uuid.uuid4().hex
        day = datetime.now(UTC).date().isoformat()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT policy_json FROM cloud_policies WHERE endpoint=? AND model=?", (endpoint, model)).fetchone()
            if row is None:
                raise ProviderUnavailableError("Cloud access is disabled until consent, pricing and budgets are configured.")
            policy = CloudPolicy.model_validate_json(row[0])
            if not policy.enabled:
                raise ProviderUnavailableError("Cloud access is disabled.")
            active = [CloudPolicy.model_validate_json(row[0]) for row in connection.execute("SELECT policy_json FROM cloud_policies")]
            active = [entry for entry in active if entry.enabled]
            daily_limit = min(entry.daily_micro_usd for entry in active)
            task_limit = min(entry.task_micro_usd for entry in active)
            price = input_tokens * policy.input_micro_usd_per_million_tokens + output * policy.output_micro_usd_per_million_tokens
            cost = (price + 999999) // 1000000
            # Limits apply across all configured providers, preventing alternate-provider bypasses.
            daily = connection.execute("SELECT COALESCE(SUM(reserved_micro_usd),0) FROM model_reservations WHERE day=?", (day,)).fetchone()[0]
            per_task = connection.execute("SELECT COALESCE(SUM(reserved_micro_usd),0) FROM model_reservations WHERE task_id=?", (task,)).fetchone()[0]
            if daily + cost > daily_limit or per_task + cost > task_limit:
                raise ProviderUnavailableError("The configured model budget would be exceeded.")
            connection.execute("INSERT INTO model_reservations VALUES (?,?,?,?,?,?)", (uuid.uuid4().hex, day, task, endpoint, model, cost))
