"""Real transport scheduling boundaries with deterministic queued requests."""

import asyncio

import httpx
import pytest

from services.agent_api.app.providers import OpenAICompatibleHTTPTransport, ProviderUnavailableError
from services.agent_api.app.session_security import execution_authorized


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["none", "credential", "policy", "session"])
async def test_shared_inference_queue_rechecks_authority_after_wait(change):
    started = asyncio.Event()
    release = asyncio.Event()
    second_queued = asyncio.Event()
    calls = []
    active = 0
    peak = 0
    permitted = True

    class Gate:
        def __init__(self):
            self.lock = asyncio.Semaphore(1)
            self.attempts = 0

        async def __aenter__(self):
            self.attempts += 1
            if self.attempts == 2:
                second_queued.set()
            await self.lock.acquire()

        async def __aexit__(self, *_):
            self.lock.release()

    async def send(request):
        nonlocal active, peak
        calls.append(request.headers["authorization"])
        active += 1
        peak = max(peak, active)
        started.set()
        await release.wait()
        active -= 1
        return httpx.Response(200, json={"fixture": True})

    def authorize(*_):
        if change == "policy" and not permitted:
            raise ProviderUnavailableError("Current policy denied queued request.")

    validity = execution_authorized.set(lambda: permitted or change != "session")
    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        first = OpenAICompatibleHTTPTransport("https://fixture.example", "fixture-key", 5, client)
        second = OpenAICompatibleHTTPTransport("https://fixture.example", "fixture-key", 5, client)
        gate = Gate()
        for transport in (first, second):
            transport.dispatch_gate = gate
            transport.resolve_credential = lambda: "fixture-key" if permitted or change != "credential" else None
            transport.authorize_dispatch = authorize
        tasks = []
        try:
            tasks.append(asyncio.create_task(first.post({"model": "fixture"})))
            await asyncio.wait_for(started.wait(), 2)
            tasks.append(asyncio.create_task(second.post({"model": "fixture"})))
            await asyncio.wait_for(second_queued.wait(), 2)
            assert len(calls) == 1
            permitted = False
            release.set()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert results[0] == {"fixture": True}
            if change == "none":
                assert results[1] == {"fixture": True} and len(calls) == 2
            else:
                assert isinstance(results[1], ProviderUnavailableError)
                assert len(calls) == 1
            assert peak == 1
        finally:
            release.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            execution_authorized.reset(validity)
