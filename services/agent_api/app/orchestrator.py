"""Bounded agent loop that delegates all execution to ToolExecutor."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from services.agent_api.app.backend_boundary import ProcessCancellation
from services.agent_api.app.capabilities import CapabilityResolver, ResolutionStatus
from services.agent_api.app.memory import MemoryRepository
from services.agent_api.app.planner import DeterministicPlanner, PlannedStep, ToolSelector
from services.agent_api.app.tasks import TaskLimits, TaskStatus
from services.agent_api.app.tools import ToolExecutor
from services.agent_api.app.usage_policy import budget_task


@dataclass
class ExecutionContext:
    task_id: str
    request: str
    limits: TaskLimits
    memory_ids: tuple[int, ...] = ()


class _TaskCancelled(Exception):
    pass


class AgentOrchestrator:
    def __init__(self, executor: ToolExecutor, memory: MemoryRepository, planner=None, resolver: CapabilityResolver | None = None) -> None:
        self.executor, self.memory = executor, memory
        self.planner = planner or DeterministicPlanner()
        self.selector = ToolSelector()
        self.resolver = resolver

    async def create(self, request: str, limits: TaskLimits | None = None) -> dict[str, object]:
        task_id = self.memory.create_task(request, (limits or TaskLimits()).bounded())
        return await self.run(task_id)

    async def run(self, task_id: str) -> dict[str, object]:
        return await self._claimed(task_id, self._run)

    async def _claimed(self, task_id: str, operation) -> dict[str, object]:
        self.memory.get_task(task_id)
        owner = str(uuid.uuid4())
        if not self.memory.claim_task(task_id, owner):
            return self.memory.get_task(task_id)
        budget_scope = budget_task.set(task_id)
        try:
            return await operation(task_id)
        except asyncio.CancelledError:
            self._fail(task_id, "Execution interrupted; inspect audit before retrying.")
            raise
        finally:
            budget_task.reset(budget_scope)
            self.memory.release_task(task_id, owner)

    async def _run(self, task_id: str) -> dict[str, object]:
        task = self.memory.get_task(task_id)
        if task["status"] not in {TaskStatus.QUEUED, TaskStatus.WAITING_FOR_CAPABILITY}:
            return task
        context = ExecutionContext(task_id, task["request"], TaskLimits(**task["limits"]), self.memory.task_memory_ids(task_id))
        deadline = asyncio.get_running_loop().time() + context.limits.timeout_seconds
        if self.resolver:
            resolution = self.resolver.resolve(context.request)
            if resolution.status == ResolutionStatus.WAITING_FOR_CAPABILITY:
                self.memory.update_task(task_id, TaskStatus.WAITING_FOR_CAPABILITY, 0, resolution.reason)
                return self.memory.get_task(task_id)
        self.memory.update_task(task_id, TaskStatus.RUNNING)
        try:
            steps = await self._controlled(
                self.planner.plan(context.request, context), task_id, deadline
            )
            if len(steps) > context.limits.max_steps:
                return self._fail(task_id, "Maximum step limit exceeded.")
            for index, step in enumerate(steps, 1):
                self.memory.create_task_step(
                    task_id, index, step.description, step.tool_name, step.arguments
                )
            for index, step in enumerate(steps, 1):
                if index > context.limits.max_executions:
                    return self._fail(task_id, "Maximum execution limit exceeded.")
                try:
                    tool, validated = self.selector.select(step, self.executor.registry)
                    result = await self._controlled(
                        self.executor.execute(tool.name, validated.model_dump(), task_id=task_id, step_number=index), task_id, deadline
                    )
                except _TaskCancelled:
                    raise
                except asyncio.TimeoutError:
                    raise
                except Exception:
                    result = {
                        "status": "invalid_arguments",
                        "detail": "Tool selection was invalid.",
                    }
                self.memory.update_task_step(task_id, index, result.get("status", "failed"), result)
                status = result.get("status")
                if status == "approval_required":
                    self.memory.update_task(
                        task_id, TaskStatus.WAITING_FOR_APPROVAL, index, result.get("detail")
                    )
                    return self.memory.get_task(task_id)
                if status != "success":
                    return self._fail(task_id, str(result.get("detail", "Task failed.")), index)
            self.memory.update_task(task_id, TaskStatus.COMPLETED, len(steps), "Task completed.")
            return self.memory.get_task(task_id)
        except _TaskCancelled:
            return self.memory.get_task(task_id)
        except asyncio.TimeoutError:
            return self._fail(task_id, "Task exceeded its execution time limit.")
        except Exception:
            return self._fail(task_id, "Task failed safely.")

    async def resume(self, task_id: str) -> dict[str, object]:
        return await self._claimed(task_id, self._resume)

    async def _resume(self, task_id: str) -> dict[str, object]:
        task = self.memory.get_task(task_id)
        if task["status"] == TaskStatus.WAITING_FOR_CAPABILITY:
            return await self._run(task_id)
        if task["status"] != TaskStatus.WAITING_FOR_APPROVAL:
            return task
        steps = self.memory.get_task_steps(task_id)
        step = next((item for item in steps if item["status"] == "approval_required"), None)
        if step is None:
            return self._fail(task_id, "Approval step is missing.")
        request_id = step.get("approval_id")
        if not request_id:
            return self._fail(task_id, "Approval reference is missing.")
        limits = TaskLimits(**task["limits"])
        deadline = asyncio.get_running_loop().time() + limits.timeout_seconds
        self.memory.update_task(task_id, TaskStatus.RUNNING, step["step_number"])
        try:
            result = await self._controlled(
                self.executor.continue_approved(request_id, task_id=task_id), task_id, deadline
            )
        except _TaskCancelled:
            return self.memory.get_task(task_id)
        except asyncio.TimeoutError:
            return self._fail(task_id, "Task exceeded its execution time limit.")
        if result.get("status") == "pending":
            self.memory.update_task(task_id, TaskStatus.WAITING_FOR_APPROVAL, step["step_number"], "Approval is required.")
            return self.memory.get_task(task_id)
        self.memory.update_task_step(
            task_id, step["step_number"], result.get("status", "failed"), result
        )
        if result.get("status") != "success":
            return self._fail(
                task_id, str(result.get("detail", "Approved step failed.")), step["step_number"]
            )
        remaining = [
            PlannedStep(item["description"], item["tool_name"], item["arguments"])
            for item in steps
            if item["status"] == "planned" and item["step_number"] > step["step_number"]
        ]
        if remaining:
            return await self._run_steps(task_id, remaining, step["step_number"])
        self.memory.update_task(task_id, TaskStatus.COMPLETED, step["step_number"], "Task completed.")
        return self.memory.get_task(task_id)

    async def _run_steps(self, task_id: str, steps: list[object], offset: int) -> dict[str, object]:
        task = self.memory.get_task(task_id)
        limits = TaskLimits(**task["limits"])
        deadline = asyncio.get_running_loop().time() + limits.timeout_seconds
        for index, step in enumerate(steps, offset + 1):
            if index > limits.max_executions:
                return self._fail(task_id, "Maximum execution limit exceeded.", index)
            try:
                tool, validated = self.selector.select(step, self.executor.registry)
                result = await self._controlled(
                    self.executor.execute(tool.name, validated.model_dump(), task_id=task_id, step_number=index), task_id, deadline
                )
            except _TaskCancelled:
                return self.memory.get_task(task_id)
            except asyncio.TimeoutError:
                return self._fail(task_id, "Task exceeded its execution time limit.", index)
            except Exception:
                result = {"status": "invalid_arguments", "detail": "Tool selection was invalid."}
            self.memory.update_task_step(task_id, index, result.get("status", "failed"), result)
            if result.get("status") == "approval_required":
                self.memory.update_task(task_id, TaskStatus.WAITING_FOR_APPROVAL, index, result.get("detail"))
                return self.memory.get_task(task_id)
            if result.get("status") != "success":
                return self._fail(task_id, str(result.get("detail", "Task failed.")), index)
        self.memory.update_task(task_id, TaskStatus.COMPLETED, offset + len(steps), "Task completed.")
        return self.memory.get_task(task_id)

    def cancel(self, task_id: str) -> dict[str, object]:
        task = self.memory.get_task(task_id)
        if task["status"] not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            self.memory.update_task(
                task_id, TaskStatus.CANCELLED, task.get("current_step", 0), "Task cancelled."
            )
            ProcessCancellation.cancel(ProcessCancellation.key(self.memory.database_path, task_id))
        return self.memory.get_task(task_id)

    async def _controlled(self, awaitable, task_id: str, deadline: float):
        loop = asyncio.get_running_loop()
        remaining = deadline - loop.time()
        if remaining <= 0:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise asyncio.TimeoutError
        if self.memory.get_task(task_id)["status"] == TaskStatus.CANCELLED:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise _TaskCancelled
        action = asyncio.ensure_future(awaitable)
        key = ProcessCancellation.key(self.memory.database_path, task_id)
        ProcessCancellation.register(key, action)
        # Cancellation may arrive from another thread between precheck and registration.
        if self.memory.get_task(task_id)["status"] == TaskStatus.CANCELLED:
            action.cancel()
        try:
            done, _ = await asyncio.wait(
                {action}, timeout=remaining
            )
            if self.memory.get_task(task_id)["status"] == TaskStatus.CANCELLED:
                action.cancel()
                await asyncio.gather(action, return_exceptions=True)
                raise _TaskCancelled
            if action in done:
                return action.result()
            action.cancel()
            await asyncio.gather(action, return_exceptions=True)
            raise asyncio.TimeoutError
        except asyncio.CancelledError:
            action.cancel()
            await asyncio.gather(action, return_exceptions=True)
            raise
        finally:
            ProcessCancellation.remove(key, action)

    def _fail(self, task_id: str, detail: str, step: int = 0) -> dict[str, object]:
        self.memory.update_task(task_id, TaskStatus.FAILED, step, detail)
        return self.memory.get_task(task_id)
