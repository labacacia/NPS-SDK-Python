# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NOP orchestrator telemetry wiring tests (reuses the orchestrator test fakes)."""

from __future__ import annotations

import uuid

import pytest

from nps_sdk.nop import instrumentation as nop_telemetry
from nps_sdk.nop.frames import StreamError, TaskFrame
from nps_sdk.nop.models import DagNode, RetryPolicy, TaskDag

from tests.test_nop_orchestrator import (
    FakeWorkerClient,
    _final_frame,
    _node,
    _single,
    build_orchestrator,
)


@pytest.mark.asyncio
async def test_successful_task_increments_completed_and_duration():
    nop_telemetry.reset()
    orch, worker, _ = build_orchestrator()
    worker.setup_success("a", {"ok": True})
    result = await orch.execute(_single("a"))
    assert result.final_state.value == "completed"

    assert nop_telemetry.tasks_completed.snapshot().total() == 1
    assert nop_telemetry.tasks_failed.snapshot().total() == 0
    assert nop_telemetry.task_duration_ms.snapshot().count(outcome="success") == 1
    assert nop_telemetry.node_duration_ms.snapshot().count(outcome="success") == 1

    spans = [s for s in nop_telemetry.source.recorded_spans()
             if s.name == "nps.nop.task.execute"]
    assert len(spans) == 1
    assert spans[0].attributes.get("task.outcome") == "success"


@pytest.mark.asyncio
async def test_failed_task_increments_failed_counter():
    nop_telemetry.reset()
    orch, worker, _ = build_orchestrator()
    worker.setup_failure("a", "NWP-NODE-UNAVAILABLE", "boom")
    result = await orch.execute(_single("a"))
    assert result.final_state.value == "failed"

    assert nop_telemetry.tasks_failed.snapshot().total() == 1
    assert nop_telemetry.tasks_completed.snapshot().total() == 0
    assert nop_telemetry.task_duration_ms.snapshot().count(outcome="failure") == 1
    assert nop_telemetry.node_duration_ms.snapshot().count(outcome="failure") == 1


@pytest.mark.asyncio
async def test_retry_increments_node_retries_counter():
    nop_telemetry.reset()
    orch, worker, _ = build_orchestrator()

    attempts = {"n": 0}

    async def flaky(frame):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return [_final_frame("a", error=StreamError("NWP-NODE-UNAVAILABLE", "boom"))]
        return [_final_frame("a", data={"ok": True})]

    worker.setup_handler("a", flaky)
    node = _node("a", retry_policy=RetryPolicy(max_retries=2, initial_delay_ms=0))
    task = TaskFrame(task_id=str(uuid.uuid4()), dag=TaskDag(nodes=(node,), edges=()))
    result = await orch.execute(task)
    assert result.final_state.value == "completed"
    assert nop_telemetry.node_retries.snapshot().total() >= 1
