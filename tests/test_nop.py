# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for NOP frame dataclasses, DAG models, and NopClient."""

import json
import pytest
import respx
import httpx

from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.frames import EncodingTier, FrameType
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.nop.models import (
    AggregateStrategy,
    BackoffStrategy,
    DagEdge,
    DagNode,
    RetryPolicy,
    TaskContext,
    TaskDag,
    TaskPriority,
    TaskState,
)
from nps_sdk.nop.frames import (
    AlignStreamFrame,
    DelegateFrame,
    StreamError,
    SyncFrame,
    TaskFrame,
)
from nps_sdk.nop.client import NopClient, NopTaskStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def registry() -> FrameRegistry:
    return FrameRegistry.create_full()


@pytest.fixture
def codec(registry: FrameRegistry) -> NpsFrameCodec:
    return NpsFrameCodec(registry)


def _simple_dag() -> TaskDag:
    return TaskDag(
        nodes=(
            DagNode(
                id="fetch",
                action="nwp://data.example.com/products/query",
                agent="urn:nps:agent:ca.example.com:data-agent",
            ),
            DagNode(
                id="summarize",
                action="nwp://llm.example.com/summarize",
                agent="urn:nps:agent:ca.example.com:llm-agent",
                input_from=("fetch",),
                condition="fetch.count > 0",
                retry_policy=RetryPolicy(max_retries=2, backoff=BackoffStrategy.EXPONENTIAL),
            ),
        ),
        edges=(DagEdge(from_="fetch", to="summarize"),),
    )


def _simple_task() -> TaskFrame:
    return TaskFrame(
        task_id="550e8400-e29b-41d4-a716-446655440000",
        dag=_simple_dag(),
        timeout_ms=60_000,
        callback_url="https://webhook.example.com/nop/done",
    )


# ── RetryPolicy ───────────────────────────────────────────────────────────────

class TestRetryPolicy:
    def test_exponential_delay(self):
        rp = RetryPolicy(max_retries=3, backoff=BackoffStrategy.EXPONENTIAL, initial_delay_ms=1000)
        assert rp.compute_delay_ms(1) == 1000
        assert rp.compute_delay_ms(2) == 2000
        assert rp.compute_delay_ms(3) == 4000

    def test_linear_delay(self):
        rp = RetryPolicy(backoff=BackoffStrategy.LINEAR, initial_delay_ms=500)
        assert rp.compute_delay_ms(1) == 500
        assert rp.compute_delay_ms(3) == 1500

    def test_fixed_delay(self):
        rp = RetryPolicy(backoff=BackoffStrategy.FIXED, initial_delay_ms=200)
        assert rp.compute_delay_ms(1) == 200
        assert rp.compute_delay_ms(5) == 200

    def test_max_delay_cap(self):
        rp = RetryPolicy(backoff=BackoffStrategy.EXPONENTIAL, initial_delay_ms=1000, max_delay_ms=3000)
        assert rp.compute_delay_ms(10) == 3000

    def test_roundtrip(self):
        rp  = RetryPolicy(max_retries=3, backoff=BackoffStrategy.LINEAR, initial_delay_ms=500,
                          retry_on=("NOP-DELEGATE-TIMEOUT",))
        out = RetryPolicy.from_dict(rp.to_dict())
        assert out.max_retries      == 3
        assert out.backoff          == BackoffStrategy.LINEAR
        assert out.initial_delay_ms == 500
        assert "NOP-DELEGATE-TIMEOUT" in out.retry_on

    def test_defaults(self):
        rp = RetryPolicy()
        assert rp.max_retries      == 2
        assert rp.backoff          == BackoffStrategy.EXPONENTIAL
        assert rp.initial_delay_ms == 1000
        assert rp.max_delay_ms     == 30_000


# ── TaskContext ───────────────────────────────────────────────────────────────

class TestTaskContext:
    def test_empty_roundtrip(self):
        ctx = TaskContext()
        out = TaskContext.from_dict(ctx.to_dict())
        assert out.trace_id   is None
        assert out.session_id is None

    def test_full_roundtrip(self):
        ctx = TaskContext(
            session_id="sess-1",
            trace_id="trace-abc",
            span_id="span-001",
            trace_flags=1,
            baggage={"key": "value"},
        )
        out = TaskContext.from_dict(ctx.to_dict())
        assert out.session_id  == "sess-1"
        assert out.trace_id    == "trace-abc"
        assert out.trace_flags == 1
        assert out.baggage     == {"key": "value"}


# ── DagNode / DagEdge / TaskDag ───────────────────────────────────────────────

class TestDagModels:
    def test_dag_node_minimal(self):
        node = DagNode(id="n1", action="nwp://x.com/q", agent="urn:nps:agent:ca:a")
        d    = node.to_dict()
        out  = DagNode.from_dict(d)
        assert out.id     == "n1"
        assert out.action == "nwp://x.com/q"
        assert out.agent  == "urn:nps:agent:ca:a"
        assert out.input_from == ()

    def test_dag_node_full(self):
        node = DagNode(
            id="summarize",
            action="nwp://llm.example.com/summarize",
            agent="urn:nps:agent:ca.example.com:llm-agent",
            input_from=("fetch",),
            input_mapping={"data": "$.fetch.result"},
            condition="fetch.count > 0",
            timeout_ms=5000,
            retry_policy=RetryPolicy(max_retries=2),
            min_required=1,
        )
        out = DagNode.from_dict(node.to_dict())
        assert out.input_from    == ("fetch",)
        assert out.condition     == "fetch.count > 0"
        assert out.timeout_ms    == 5000
        assert out.min_required  == 1
        assert out.retry_policy is not None
        assert out.retry_policy.max_retries == 2
        assert out.input_mapping == {"data": "$.fetch.result"}

    def test_dag_edge_roundtrip(self):
        edge = DagEdge(from_="a", to="b")
        out  = DagEdge.from_dict(edge.to_dict())
        assert out.from_ == "a"
        assert out.to    == "b"

    def test_task_dag_roundtrip(self):
        dag = _simple_dag()
        out = TaskDag.from_dict(dag.to_dict())
        assert len(out.nodes) == 2
        assert len(out.edges) == 1
        assert out.nodes[0].id == "fetch"
        assert out.nodes[1].id == "summarize"
        assert out.edges[0].from_ == "fetch"


# ── TaskFrame ─────────────────────────────────────────────────────────────────

class TestTaskFrame:
    def test_frame_type(self):
        assert _simple_task().frame_type == FrameType.TASK

    def test_defaults(self):
        task = _simple_task()
        assert task.timeout_ms     == 60_000
        assert task.priority       == TaskPriority.NORMAL
        assert task.delegate_depth == 0
        assert task.preflight      is False

    def test_roundtrip_json(self, codec):
        task = _simple_task()
        wire = codec.encode(task, override_tier=EncodingTier.JSON)
        out  = codec.decode(wire)
        assert isinstance(out, TaskFrame)
        assert out.task_id      == task.task_id
        assert out.callback_url == task.callback_url
        assert out.timeout_ms   == task.timeout_ms
        assert len(out.dag.nodes) == 2

    def test_roundtrip_msgpack(self, codec):
        task = _simple_task()
        out  = codec.decode(codec.encode(task))
        assert isinstance(out, TaskFrame)
        assert out.task_id == task.task_id
        assert len(out.dag.edges) == 1

    def test_with_context(self, codec):
        task = TaskFrame(
            task_id="task-001",
            dag=_simple_dag(),
            context=TaskContext(trace_id="trace-xyz", session_id="sess-1"),
        )
        out = codec.decode(codec.encode(task))
        assert out.context is not None
        assert out.context.trace_id   == "trace-xyz"
        assert out.context.session_id == "sess-1"

    def test_high_priority(self, codec):
        task = TaskFrame(task_id="t", dag=_simple_dag(), priority=TaskPriority.HIGH)
        out  = codec.decode(codec.encode(task))
        assert out.priority == TaskPriority.HIGH

    def test_delegate_depth(self, codec):
        task = TaskFrame(task_id="t", dag=_simple_dag(), delegate_depth=2)
        out  = codec.decode(codec.encode(task))
        assert out.delegate_depth == 2


# ── DelegateFrame ─────────────────────────────────────────────────────────────

class TestDelegateFrame:
    def _make(self) -> DelegateFrame:
        return DelegateFrame(
            parent_task_id="task-001",
            subtask_id="sub-001",
            node_id="fetch",
            target_agent_nid="urn:nps:agent:ca.example.com:data-agent",
            action="nwp://data.example.com/products/query",
            delegated_scope={"paths": ["/products/*"]},
            deadline_at="2026-04-16T01:00:00Z",
        )

    def test_frame_type(self):
        assert self._make().frame_type == FrameType.DELEGATE

    def test_roundtrip(self, codec):
        frame = self._make()
        out   = codec.decode(codec.encode(frame))
        assert isinstance(out, DelegateFrame)
        assert out.parent_task_id   == "task-001"
        assert out.subtask_id       == "sub-001"
        assert out.target_agent_nid == "urn:nps:agent:ca.example.com:data-agent"
        assert out.delegate_depth   == 1

    def test_with_params_and_idempotency(self, codec):
        frame = DelegateFrame(
            parent_task_id="task-001",
            subtask_id="sub-002",
            node_id="create",
            target_agent_nid="urn:nps:agent:ca.example.com:action-agent",
            action="nwp://svc.example.com/orders/create",
            delegated_scope={},
            deadline_at="2026-04-16T01:00:00Z",
            params={"product_id": "abc"},
            idempotency_key="idem-xyz",
        )
        out = codec.decode(codec.encode(frame))
        assert out.params          == {"product_id": "abc"}
        assert out.idempotency_key == "idem-xyz"


# ── SyncFrame ─────────────────────────────────────────────────────────────────

class TestSyncFrame:
    def test_frame_type(self):
        frame = SyncFrame(task_id="t", sync_id="s", wait_for=("sub-1", "sub-2"))
        assert frame.frame_type == FrameType.SYNC

    def test_roundtrip(self, codec):
        frame = SyncFrame(
            task_id="task-001",
            sync_id="sync-001",
            wait_for=("sub-1", "sub-2", "sub-3"),
            min_required=2,
            aggregate=AggregateStrategy.FASTEST_K,
            timeout_ms=5000,
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, SyncFrame)
        assert out.task_id      == "task-001"
        assert out.wait_for     == ("sub-1", "sub-2", "sub-3")
        assert out.min_required == 2
        assert out.aggregate    == AggregateStrategy.FASTEST_K
        assert out.timeout_ms   == 5000

    def test_defaults(self, codec):
        frame = SyncFrame(task_id="t", sync_id="s", wait_for=("sub-1",))
        out   = codec.decode(codec.encode(frame))
        assert out.min_required == 0
        assert out.aggregate    == "merge"
        assert out.timeout_ms   is None


# ── AlignStreamFrame ──────────────────────────────────────────────────────────

class TestAlignStreamFrame:
    def test_frame_type(self):
        frame = AlignStreamFrame(
            stream_id="st", task_id="t", subtask_id="s",
            seq=0, is_final=False, sender_nid="urn:nps:agent:ca:a",
        )
        assert frame.frame_type == FrameType.ALIGN_STREAM

    def test_intermediate_roundtrip(self, codec):
        frame = AlignStreamFrame(
            stream_id="st-001",
            task_id="task-001",
            subtask_id="sub-001",
            seq=0,
            is_final=False,
            sender_nid="urn:nps:agent:ca.example.com:data-agent",
            data={"rows": [1, 2, 3]},
            window_size=10,
        )
        out = codec.decode(codec.encode(frame))
        assert isinstance(out, AlignStreamFrame)
        assert out.seq        == 0
        assert out.is_final   is False
        assert out.data       == {"rows": [1, 2, 3]}
        assert out.window_size == 10
        assert out.error      is None

    def test_final_success_roundtrip(self, codec):
        frame = AlignStreamFrame(
            stream_id="st-001",
            task_id="task-001",
            subtask_id="sub-001",
            seq=1,
            is_final=True,
            sender_nid="urn:nps:agent:ca.example.com:data-agent",
            data={"summary": "done"},
        )
        out = codec.decode(codec.encode(frame))
        assert out.is_final is True
        assert out.error    is None

    def test_final_error_roundtrip(self, codec):
        frame = AlignStreamFrame(
            stream_id="st-001",
            task_id="task-001",
            subtask_id="sub-001",
            seq=1,
            is_final=True,
            sender_nid="urn:nps:agent:ca.example.com:data-agent",
            error=StreamError(error_code="NOP-DELEGATE-TIMEOUT", message="timed out"),
        )
        out = codec.decode(codec.encode(frame))
        assert out.is_final       is True
        assert out.error          is not None
        assert out.error.error_code == "NOP-DELEGATE-TIMEOUT"
        assert out.error.message    == "timed out"


# ── TaskState ─────────────────────────────────────────────────────────────────

class TestTaskState:
    def test_values(self):
        assert TaskState.COMPLETED == "completed"
        assert TaskState.FAILED    == "failed"
        assert TaskState.CANCELLED == "cancelled"
        assert TaskState.SKIPPED   == "skipped"

    def test_from_string(self):
        assert TaskState("running")  == TaskState.RUNNING
        assert TaskState("pending")  == TaskState.PENDING


# ── NopClient ─────────────────────────────────────────────────────────────────

class TestNopClient:
    @pytest.mark.asyncio
    @respx.mock
    async def test_submit_returns_task_id(self):
        respx.post("https://gateway.example.com/task").mock(
            return_value=httpx.Response(202, json={"task_id": "task-abc"})
        )
        async with NopClient("https://gateway.example.com") as client:
            task_id = await client.submit(_simple_task())
        assert task_id == "task-abc"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_status_completed(self):
        respx.get("https://gateway.example.com/task/task-abc").mock(
            return_value=httpx.Response(200, json={
                "task_id": "task-abc",
                "state":   "completed",
                "aggregated_result": {"summary": "ok"},
                "node_results": {},
            })
        )
        async with NopClient("https://gateway.example.com") as client:
            status = await client.get_status("task-abc")
        assert status.state             == TaskState.COMPLETED
        assert status.is_terminal       is True
        assert status.aggregated_result == {"summary": "ok"}

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_status_failed(self):
        respx.get("https://gateway.example.com/task/task-abc").mock(
            return_value=httpx.Response(200, json={
                "task_id":      "task-abc",
                "state":        "failed",
                "error_code":   "NOP-TASK-TIMEOUT",
                "error_message": "timed out after 30s",
            })
        )
        async with NopClient("https://gateway.example.com") as client:
            status = await client.get_status("task-abc")
        assert status.state         == TaskState.FAILED
        assert status.error_code    == "NOP-TASK-TIMEOUT"
        assert status.is_terminal   is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_cancel(self):
        respx.post("https://gateway.example.com/task/task-abc/cancel").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        async with NopClient("https://gateway.example.com") as client:
            await client.cancel("task-abc")  # should not raise

    @pytest.mark.asyncio
    @respx.mock
    async def test_wait_polls_until_terminal(self):
        responses = [
            httpx.Response(200, json={"task_id": "t", "state": "running"}),
            httpx.Response(200, json={"task_id": "t", "state": "running"}),
            httpx.Response(200, json={"task_id": "t", "state": "completed",
                                      "aggregated_result": {"done": True}, "node_results": {}}),
        ]
        respx.get("https://gateway.example.com/task/t").mock(side_effect=responses)

        async with NopClient("https://gateway.example.com") as client:
            status = await client.wait("t", poll_interval=0.01, timeout=5.0)
        assert status.state == TaskState.COMPLETED
        assert status.aggregated_result == {"done": True}

    @pytest.mark.asyncio
    @respx.mock
    async def test_wait_raises_on_timeout(self):
        respx.get("https://gateway.example.com/task/t").mock(
            return_value=httpx.Response(200, json={"task_id": "t", "state": "running"})
        )
        import asyncio
        async with NopClient("https://gateway.example.com") as client:
            with pytest.raises(asyncio.TimeoutError):
                await client.wait("t", poll_interval=0.01, timeout=0.05)

    @pytest.mark.asyncio
    @respx.mock
    async def test_http_error_propagates(self):
        respx.post("https://gateway.example.com/task").mock(
            return_value=httpx.Response(503, json={"error": "service unavailable"})
        )
        async with NopClient("https://gateway.example.com") as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.submit(_simple_task())


# ── NopTaskStatus ─────────────────────────────────────────────────────────────

class TestNopTaskStatus:
    def test_is_terminal_states(self):
        for state in ("completed", "failed", "cancelled"):
            s = NopTaskStatus({"task_id": "t", "state": state})
            assert s.is_terminal is True

    def test_non_terminal_states(self):
        for state in ("pending", "running", "preflight", "waiting_sync"):
            s = NopTaskStatus({"task_id": "t", "state": state})
            assert s.is_terminal is False

    def test_node_results_defaults_empty(self):
        s = NopTaskStatus({"task_id": "t", "state": "completed"})
        assert s.node_results == {}

    def test_task_id_property(self):
        s = NopTaskStatus({"task_id": "task-xyz", "state": "running"})
        assert s.task_id == "task-xyz"

    def test_error_message_property(self):
        s = NopTaskStatus({
            "task_id": "t", "state": "failed",
            "error_code": "NOP-TASK-TIMEOUT",
            "error_message": "exceeded 30s",
        })
        assert s.error_message == "exceeded 30s"
        # Returns None when absent
        s2 = NopTaskStatus({"task_id": "t", "state": "completed"})
        assert s2.error_message is None

    def test_raw_property(self):
        data = {"task_id": "t", "state": "running", "extra": 42}
        s    = NopTaskStatus(data)
        assert s.raw is data

    def test_repr(self):
        s = NopTaskStatus({"task_id": "task-repr", "state": "completed"})
        r = repr(s)
        assert "task-repr" in r
        assert "completed" in r


# ── NopClient.close ───────────────────────────────────────────────────────────

class TestNopClientClose:
    @pytest.mark.asyncio
    async def test_explicit_close(self):
        client = NopClient("https://gateway.example.com")
        await client.close()  # should not raise

    @pytest.mark.asyncio
    async def test_external_client_not_closed(self):
        http   = httpx.AsyncClient()
        client = NopClient("https://gateway.example.com", http_client=http)
        await client.close()  # no-op: NopClient does not own the external client
        assert not http.is_closed
        await http.aclose()
