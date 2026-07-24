# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NOP task/subtask persistence (NPS-5 §5).

  * :class:`NopTaskRecord` / :class:`NopSubtaskRecord` — state records.
  * :class:`INopTaskStore` — persistence Protocol.
  * :class:`InMemoryNopTaskStore` — volatile in-memory implementation.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from nps_sdk.nop.frames import TaskFrame
from nps_sdk.nop.models import TaskState


@dataclass
class NopSubtaskRecord:
    """State and result for a single DAG node (subtask)."""

    node_id: str
    subtask_id: str
    state: TaskState = TaskState.PENDING
    result: Any = None
    error_code: str | None = None
    error_message: str | None = None
    attempt_count: int = 0


@dataclass
class NopTaskRecord:
    """Persistent record of a running or completed NOP task."""

    task_id: str
    frame: TaskFrame
    state: TaskState = TaskState.PENDING
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    subtasks: dict[str, NopSubtaskRecord] = field(default_factory=dict)


@runtime_checkable
class INopTaskStore(Protocol):
    """Persistence abstraction for NOP task and subtask state (NPS-5 §5)."""

    async def save(self, record: NopTaskRecord) -> None:
        """Persist a new task record. Raises if ``task_id`` already exists."""
        ...

    async def get(self, task_id: str) -> NopTaskRecord | None:
        """Return the task record, or ``None`` if not found."""
        ...

    async def update_state(self, task_id: str, state: TaskState) -> None:
        """Update the overall task state."""
        ...

    async def update_subtask(
        self,
        task_id: str,
        node_id: str,
        subtask_id: str,
        state: TaskState,
        *,
        result: Any = None,
        error_code: str | None = None,
        error_message: str | None = None,
        attempt: int = 1,
    ) -> None:
        """Create or update a subtask record within the task."""
        ...


class InMemoryNopTaskStore:
    """Volatile, in-memory :class:`INopTaskStore`. Not durable across restarts."""

    def __init__(self) -> None:
        self._tasks: dict[str, NopTaskRecord] = {}
        self._lock = threading.Lock()

    async def save(self, record: NopTaskRecord) -> None:
        with self._lock:
            if record.task_id in self._tasks:
                raise ValueError(f"Task already exists: {record.task_id}")
            self._tasks[record.task_id] = record

    async def get(self, task_id: str) -> NopTaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    async def update_state(self, task_id: str, state: TaskState) -> None:
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is not None:
                rec.state = state

    async def update_subtask(
        self,
        task_id: str,
        node_id: str,
        subtask_id: str,
        state: TaskState,
        *,
        result: Any = None,
        error_code: str | None = None,
        error_message: str | None = None,
        attempt: int = 1,
    ) -> None:
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None:
                return
            sub = rec.subtasks.get(node_id)
            if sub is None:
                sub = NopSubtaskRecord(node_id=node_id, subtask_id=subtask_id)
                rec.subtasks[node_id] = sub
            sub.state = state
            sub.attempt_count = attempt
            if result is not None:
                sub.result = result
            if error_code is not None:
                sub.error_code = error_code
            if error_message is not None:
                sub.error_message = error_message
