"""Explicit account model discovery. No prompts, inference or arbitrary endpoints."""

import asyncio
import json
import re
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.session_security import (
    execution_authorized,
    execution_dispatch_authorized,
)

router = APIRouter(prefix="/api/v1/models", tags=["model setup"])
CATALOGS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/models",
    "anthropic": "https://api.anthropic.com/v1/models?limit=100",
    "nvidia": "https://integrate.api.nvidia.com/v1/models",
}


class CatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Literal["gemini", "anthropic", "nvidia"]
    credential_reference: str = Field(pattern=r"^vault:[a-f0-9]{32}$")


async def discover_models(payload, resolver, *, client):
    if not execution_authorized.get()():
        raise HTTPException(401, "Your session expired. Reconnect before loading models.")
    execution_dispatch_authorized.get()()
    try:
        key = resolver.resolve(payload.credential_reference)
    except (ValueError, OSError):
        key = None
    if not key:
        raise HTTPException(400, "Add an available API key before loading models.")
    headers = ({"x-api-key": key, "anthropic-version": "2023-06-01"} if payload.provider == "anthropic"
               else {"Authorization": "Bearer " + key})
    try:
        async with client.stream("GET", CATALOGS[payload.provider], headers=headers, follow_redirects=False) as response:
            if response.status_code != 200:
                raise HTTPException(400, "Cannot load models. Check this provider's key, workspace access and account status.")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 1_000_000:
                    raise HTTPException(400, "The model list is too large. Use a model ID from your provider instead.")
        value = json.loads(body)
        rows = value.get("data") if isinstance(value, dict) else None
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError
        models = {}
        for row in rows:
            model = row.get("id") if isinstance(row, dict) else None
            if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9:/._-]{1,200}", model):
                continue
            label = row.get("display_name", model)
            models[model] = {"id": model, "name": label if isinstance(label, str) and len(label) <= 200 else model}
        return {"models": sorted(models.values(), key=lambda item: item["id"]),
                "partial": bool(value.get("has_more")),
                "notice": "Listed models may have different capabilities. Listing does not verify chat/coding or enable spending."}
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(400, "The provider's model list is unavailable. Retry or enter a model ID in Advanced settings.") from None


@router.post("/discover")
async def discover(payload: CatalogRequest, request: Request):
    async def run():
        async with request.app.state.inference_gate:
            if not request.app.state.executor.policy.allowed(ActionCategory.READ):
                raise HTTPException(403, "Current action policy blocks model discovery.")
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10) as client:
                return await discover_models(payload, request.app.state.secret_resolver, client=client)
    try:
        return await asyncio.wait_for(run(), timeout=15)
    except TimeoutError:
        raise HTTPException(408, "Loading models took too long. Retry when the connection is ready.") from None
