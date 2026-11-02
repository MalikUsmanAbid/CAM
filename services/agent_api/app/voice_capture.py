"""Ephemeral, session-bound push-to-talk audio. No audio enters SQLite or logs."""

import hashlib
import io
import json
import threading
import time
import uuid
import wave

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from services.agent_api.app.action_policy import ActionCategory
from services.agent_api.app.config import _ollama_endpoint
from services.agent_api.app.memory import is_sensitive_content
from services.agent_api.app.permissions import RiskLevel
from services.agent_api.app.project_runtime import project_scope
from services.agent_api.app.session_security import execution_dispatch_authorized, execution_session
from services.agent_api.app.tools import Tool, ToolInput

MAX_AUDIO_BYTES = 650000
CAPTURE_SECONDS = 20
CAPTURE_TTL = 120


class CaptureInput(ToolInput):
    capture_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class VoiceCapture:
    def __init__(self, endpoint, inference_gate):
        self.endpoint = _ollama_endpoint(endpoint) if endpoint else None
        self.gate = inference_gate
        self.captures = {}
        self.lock = threading.Lock()

    def purge(self):
        with self.lock:
            expired = [key for key, row in self.captures.items() if time.monotonic() - row["created"] > CAPTURE_TTL]
            for key in expired:
                self.captures.pop(key, None)

    def stage(self, data):
        if not self.endpoint or not 44 <= len(data) <= MAX_AUDIO_BYTES:
            raise ValueError("Local transcription is unavailable or capture exceeds its bound.")
        try:
            with wave.open(io.BytesIO(data), "rb") as audio:
                frames = audio.getnframes()
                if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000 or not 1 <= frames <= 16000 * CAPTURE_SECONDS:
                    raise ValueError("Capture must be mono 16-bit 16kHz PCM, at most twenty seconds.")
                if len(audio.readframes(frames + 1)) != frames * 2:
                    raise ValueError("Capture frame count is inconsistent.")
        except (wave.Error, EOFError):
            raise ValueError("Capture must contain a valid bounded PCM WAV.") from None
        self.purge()
        identifier = uuid.uuid4().hex
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            if len(self.captures) >= 2:
                raise ValueError("Finish or cancel the pending voice capture first.")
            self.captures[identifier] = {"created": time.monotonic(), "owner": execution_session.get(), "data": data, "sha256": digest}
        return {"capture_id": identifier, "sha256": digest}

    def discard(self, identifier):
        with self.lock:
            row = self.captures.get(identifier)
            if row and row["owner"] == execution_session.get():
                self.captures.pop(identifier)

    async def transcribe(self, value):
        authorize = execution_dispatch_authorized.get()
        async with self.gate:
            authorize()
            with self.lock:
                row = self.captures.get(value.capture_id)
                if row is None or row["owner"] != execution_session.get() or row["sha256"] != value.sha256 or time.monotonic() - row["created"] > CAPTURE_TTL:
                    raise ValueError("Capture expired, changed or belongs to another session; record again.")
                row = self.captures.pop(value.capture_id)
            endpoint = _ollama_endpoint(self.endpoint)
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=60) as client:
                authorize()
                async with client.stream("POST", endpoint, files={"file": ("capture.wav", row["data"], "audio/wav")}, data={"model": "local-whisper"}) as response:
                    if response.status_code != 200:
                        raise ValueError("Local transcription failed; audio was not retained for retry.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 16000:
                            raise ValueError("Local transcription response exceeds its bound.")
                        body.extend(chunk)
            authorize()
            result = json.loads(body)
            transcript = result.get("text") if isinstance(result, dict) else None
            if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 4000 or is_sensitive_content(transcript):
                raise ValueError("Transcript is empty, oversized or contains potential credentials.")
            return {"text": transcript.strip(), "provider": "configured_local_transcription", "audio_persisted": False,
                    "execution": "transcription only; no chat request or tool action was automatically submitted"}


def register_voice_tool(registry, voice):
    registry.register(Tool("voice_transcribe", "Transcribe an exact ephemeral microphone capture through the configured local endpoint.", CaptureInput, RiskLevel.LOW, 65, voice.transcribe,
                           enabled=bool(voice.endpoint), category=ActionCategory.READ))


router = APIRouter(prefix="/api/v1/voice", tags=["voice"])


@router.get("/status")
async def voice_status(request: Request):
    return {"available": bool(request.app.state.voice_capture.endpoint), "capture": "push_to_talk", "max_seconds": CAPTURE_SECONDS,
            "audio_storage": "ephemeral memory only; pending captures expire after 120 seconds",
            "privacy": "Configured loopback service must be trusted; loopback alone does not prove its downstream behavior."}


@router.post("/capture")
async def capture(request: Request):
    if request.app.state.executor.policy.denied(ActionCategory.READ):
        raise HTTPException(403, "Current policy blocks voice transcription.")
    if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
        raise HTTPException(415, "Use bounded mono PCM WAV.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Capture exceeds its bound.")
        body.extend(chunk)
    value = request.app.state.voice_capture.stage(bytes(body))
    with project_scope(None):
        return await request.app.state.executor.execute("voice_transcribe", value)


@router.delete("/capture/{capture_id}")
async def discard(capture_id: str, request: Request):
    request.app.state.voice_capture.discard(capture_id)
    return {"status": "discarded_if_owned"}
