"""Task and step domain models for the bounded Phase 4A loop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_CAPABILITY = "waiting_for_capability"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskLimits:
    max_steps: int = 8
    max_executions: int = 8
    max_failures: int = 2
    timeout_seconds: int = 60

    def bounded(self) -> "TaskLimits":
        return TaskLimits(
            min(max(self.max_steps, 1), 8),
            min(max(self.max_executions, 1), 8),
            min(max(self.max_failures, 0), 2),
            min(max(self.timeout_seconds, 1), 60),
        )
