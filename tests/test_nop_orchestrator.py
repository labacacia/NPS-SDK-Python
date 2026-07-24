# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Tests for the server-side NOP orchestration engine, mirroring the .NET
NPS.Tests/Nop suite: DAG validation, condition evaluator, input mapper, result
aggregation, DAG execution (linear / diamond / K-of-N), retry, saga
compensation (strict vs best-effort), preflight, timeout, and signed callbacks.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import uuid

import httpx
import pytest
import respx

from nps_sdk.nop.aggregator import aggregate, aggregate_end_nodes, build_array, merge
from nps_sdk.nop.condition import NopConditionError, evaluate
from nps_sdk.nop.constants import NopConstants
from nps_sdk.nop.error_codes import (
    NOP_COMPENSATION_FAILED,
    NOP_COMPENSATION_NOT_SUPPORTED,
    NOP_CONDITION_EVAL_ERROR,
    NOP_DELEGATE_CHAIN_TOO_DEEP,
    NOP_INPUT_MAPPING_ERROR,
    NOP_RESOURCE_INSUFFICIENT,
    NOP_STREAM_NID_MISMATCH,
    NOP_STREAM_SEQ_GAP,
    NOP_TASK_ALREADY_COMPLETED,
    NOP_TASK_DAG_CYCLE,
    NOP_TASK_DAG_INVALID,
    NOP_TASK_DAG_TOO_LARGE,
    NOP_TASK_TIMEOUT,
)
from nps_sdk.nop.frames import AlignStreamFrame, DelegateFrame, StreamError, TaskFrame
from nps_sdk.nop.input_mapper import NopMappingError, build_params, resolve
from nps_sdk.nop.models import (
    AggregateStrategy,
    CompensationPolicy,
    DagEdge,
    DagNode,
    RetryPolicy,
    TaskDag,
    TaskState,
)
from nps_sdk.nop.orchestrator import (
    NopOrchestrator,
    NopOrchestratorOptions,
    _build_callback_signature,
)
from nps_sdk.nop.results import NopTaskResult, SagaCompensationResult
from nps_sdk.nop.store import InMemoryNopTaskStore, NopTaskRecord
from nps_sdk.nop.validation import DagValidator, NopCallbackValidator
from nps_sdk.nop.worker import PreflightResult


# ── Fake worker client ────────────────────────────────────────────────────────

class FakeWorkerClient:
    """
    Configurable in-memory worker. Node ID -> handler producing a list of
    AlignStreamFrames (or callable(frame) -> list).
    """

    def __init__(self) -> None:
        self._handlers: dict[str, object] = {}
        self.preflight_available = True
        self.preflight_unavailable_reason: str | None = None
        self.calls: list[str] = []

    def setup_success(self, node_id: str, result: object, *, delay_s: float = 0.0) -> None:
        async def handler(frame: DelegateFrame):
            if delay_s:
                await asyncio.sleep(delay_s)
            return [_final_frame(node_id, data=result)]

        self._handlers[node_id] = handler

    def setup_failure(self, node_id: str, error_code: str, message: str = "") -> None:
        async def handler(frame: DelegateFrame):
            return [_final_frame(node_id, error=StreamError(error_code, message))]

        self._handlers[node_id] = handler

    def setup_handler(self, node_id: str, handler) -> None:
        self._handlers[node_id] = handler

    async def delegate(self, frame: DelegateFrame):
        self.calls.append(frame.node_id)
        handler = self._handlers.get(frame.node_id)
        if handler is None:
            frames = [_final_frame(frame.node_id, data={"ok": True})]
        else:
            frames = await handler(frame)
        for f in frames:
            yield f

    async def preflight(
        self, agent_nid, action, *, estimated_npt=0, required_capabilities=None
    ) -> PreflightResult:
        return PreflightResult(
            agent_nid=agent_nid,
            available=self.preflight_available,
            unavailable_reason=self.preflight_unavailable_reason,
        )


def _final_frame(node_id, *, data=None, error=None, seq=0) -> AlignStreamFrame:
    return AlignStreamFrame(
        stream_id=str(uuid.uuid4()),
        task_id="task",
        subtask_id=str(uuid.uuid4()),
        seq=seq,
        is_final=True,
        sender_nid=node_id,
        data=data,
        error=error,
    )


def _intermediate(node_id, seq, data) -> AlignStreamFrame:
    return AlignStreamFrame(
        stream_id=str(uuid.uuid4()),
        task_id="task",
        subtask_id=str(uuid.uuid4()),
        seq=seq,
        is_final=False,
        sender_nid=node_id,
        data=data,
    )


def build_orchestrator(**opt_kw):
    opts = NopOrchestratorOptions(validate_sender_nid=False, callback_retry_base_delay_ms=0)
    for k, v in opt_kw.items():
        setattr(opts, k, v)
    worker = FakeWorkerClient()
    store = InMemoryNopTaskStore()
    orch = NopOrchestrator(worker, store, opts)
    return orch, worker, store


def _node(id, *, input_from=(), **kw) -> DagNode:
    return DagNode(id=id, action=f"nwp://node/{id}", agent=id, input_from=tuple(input_from), **kw)


def _linear(*ids) -> TaskFrame:
    nodes = tuple(
        _node(nid, input_from=() if i == 0 else (ids[i - 1],))
        for i, nid in enumerate(ids)
    )
    edges = tuple(DagEdge(from_=a, to=b) for a, b in zip(ids, ids[1:]))
    return TaskFrame(task_id=str(uuid.uuid4()), dag=TaskDag(nodes=nodes, edges=edges))


def _single(id, condition=None) -> TaskFrame:
    return TaskFrame(
        task_id=str(uuid.uuid4()),
        dag=TaskDag(nodes=(_node(id, condition=condition),), edges=()),
    )


# ── DAG validation ────────────────────────────────────────────────────────────

class TestDagValidator:
    def test_empty_dag_invalid(self):
        r = DagValidator.validate(TaskDag(nodes=(), edges=()))
        assert not r.is_valid
        assert r.error_code == NOP_TASK_DAG_INVALID

    def test_too_large(self):
        nodes = tuple(_node(f"n{i}") for i in range(NopConstants.MAX_DAG_NODES + 1))
        r = DagValidator.validate(TaskDag(nodes=nodes, edges=()))
        assert r.error_code == NOP_TASK_DAG_TOO_LARGE

    def test_duplicate_node_id(self):
        r = DagValidator.validate(TaskDag(nodes=(_node("a"), _node("a")), edges=()))
        assert r.error_code == NOP_TASK_DAG_INVALID
        assert "Duplicate" in r.error_message

    def test_edge_unknown_source(self):
        r = DagValidator.validate(
            TaskDag(nodes=(_node("a"),), edges=(DagEdge(from_="x", to="a"),))
        )
        assert r.error_code == NOP_TASK_DAG_INVALID

    def test_edge_unknown_target(self):
        r = DagValidator.validate(
            TaskDag(nodes=(_node("a"),), edges=(DagEdge(from_="a", to="x"),))
        )
        assert r.error_code == NOP_TASK_DAG_INVALID

    def test_input_from_unknown(self):
        r = DagValidator.validate(
            TaskDag(nodes=(_node("a", input_from=("ghost",)),), edges=())
        )
        assert r.error_code == NOP_TASK_DAG_INVALID
        assert "ghost" in r.error_message

    def test_cycle_detected(self):
        nodes = (_node("s"), _node("a"), _node("b"), _node("e"))
        edges = (
            DagEdge(from_="s", to="a"),
            DagEdge(from_="a", to="b"),
            DagEdge(from_="b", to="a"),  # back edge
            DagEdge(from_="a", to="e"),
        )
        r = DagValidator.validate(TaskDag(nodes=nodes, edges=edges))
        assert r.error_code == NOP_TASK_DAG_CYCLE

    def test_no_start_node(self):
        # Two nodes each pointing at the other -> no in-degree 0
        nodes = (_node("a"), _node("b"))
        edges = (DagEdge(from_="a", to="b"), DagEdge(from_="b", to="a"))
        r = DagValidator.validate(TaskDag(nodes=nodes, edges=edges))
        assert not r.is_valid  # start-node check or cycle

    def test_condition_too_long(self):
        long_cond = "$." + "a" * (NopConstants.MAX_CONDITION_LENGTH + 10)
        r = DagValidator.validate(TaskDag(nodes=(_node("a", condition=long_cond),), edges=()))
        assert r.error_code == NOP_CONDITION_EVAL_ERROR

    def test_valid_linear_topo_order(self):
        r = DagValidator.validate(_linear("a", "b", "c").dag)
        assert r.is_valid
        assert r.topological_order == ["a", "b", "c"]


# ── Callback URL validation ────────────────────────────────────────────────────

class TestCallbackValidator:
    def test_valid_https(self):
        assert NopCallbackValidator.validate_callback_url("https://hooks.example.com/x") is None

    def test_empty(self):
        assert NopCallbackValidator.validate_callback_url("") is not None
        assert NopCallbackValidator.validate_callback_url(None) is not None

    def test_http_rejected(self):
        assert "https" in NopCallbackValidator.validate_callback_url("http://x.com/y")

    def test_not_absolute(self):
        assert NopCallbackValidator.validate_callback_url("not-a-url") is not None

    def test_localhost_ssrf(self):
        assert NopCallbackValidator.validate_callback_url("https://localhost/x") is not None

    def test_private_ipv4_ssrf(self):
        assert NopCallbackValidator.validate_callback_url("https://10.0.0.1/x") is not None
        assert NopCallbackValidator.validate_callback_url("https://192.168.1.1/x") is not None
        assert NopCallbackValidator.validate_callback_url("https://172.16.0.5/x") is not None
        assert NopCallbackValidator.validate_callback_url("https://127.0.0.1/x") is not None

    def test_loopback_ipv6_ssrf(self):
        assert NopCallbackValidator.validate_callback_url("https://[::1]/x") is not None

    def test_public_ip_ok(self):
        assert NopCallbackValidator.validate_callback_url("https://8.8.8.8/x") is None

    def test_is_private_host_empty(self):
        assert NopCallbackValidator.is_private_host("") is True


# ── Condition evaluator truth table ────────────────────────────────────────────

class TestConditionEvaluator:
    CTX = {
        "fetch": {"count": 5, "status": "ok", "ratio": 0.75, "flag": True, "empty": ""},
        "other": {"nested": {"deep": 3}},
    }

    def test_empty_is_true(self):
        assert evaluate("", self.CTX) is True
        assert evaluate("   ", self.CTX) is True

    @pytest.mark.parametrize("expr,expected", [
        ("$.fetch.count > 0", True),
        ("$.fetch.count > 10", False),
        ("$.fetch.count >= 5", True),
        ("$.fetch.count < 10", True),
        ("$.fetch.count <= 4", False),
        ("$.fetch.count == 5", True),
        ("$.fetch.count != 5", False),
        ('$.fetch.status == "ok"', True),
        ('$.fetch.status != "ok"', False),
        ('$.fetch.status == "bad"', False),
        ("$.fetch.ratio > 0.7", True),
        ("$.fetch.ratio < 0.7", False),
        ("$.fetch.flag", True),
        ("$.fetch.empty", False),
        ("$.fetch.missing == null", True),
        ("$.fetch.count == null", False),
        ("true", True),
        ("false", False),
        ("!false", True),
        ("!true", False),
        ("$.fetch.count > 0 && $.fetch.status == \"ok\"", True),
        ("$.fetch.count > 10 && $.fetch.status == \"ok\"", False),
        ("$.fetch.count > 10 || $.fetch.status == \"ok\"", True),
        ("$.fetch.count > 10 || $.fetch.status == \"bad\"", False),
        ("(  $.fetch.count > 0 || false ) && true", True),
        ("!( $.fetch.count > 10 )", True),
        ("$.other.nested.deep == 3", True),
        ('$.fetch.status > "a"', True),
        ('$.fetch.status < "a"', False),
    ])
    def test_truth_table(self, expr, expected):
        assert evaluate(expr, self.CTX) is expected

    def test_string_ordinal_gte_lte(self):
        assert evaluate('$.fetch.status >= "ok"', self.CTX) is True
        assert evaluate('$.fetch.status <= "ok"', self.CTX) is True

    def test_syntax_error_raises(self):
        with pytest.raises(NopConditionError):
            evaluate("$.fetch.count >", self.CTX)

    def test_unknown_token_raises(self):
        with pytest.raises(NopConditionError):
            evaluate("foobar", self.CTX)

    def test_unexpected_char_raises(self):
        with pytest.raises(NopConditionError):
            evaluate("$.fetch.count @ 3", self.CTX)

    def test_unbalanced_paren_raises(self):
        with pytest.raises(NopConditionError):
            evaluate("( $.fetch.count > 0", self.CTX)

    def test_negative_number(self):
        assert evaluate("$.fetch.count > -1", self.CTX) is True

    def test_missing_path_gt_is_false(self):
        # null > number -> false (null short-circuit)
        assert evaluate("$.fetch.missing > 0", self.CTX) is False


# ── Input mapper ───────────────────────────────────────────────────────────────

class TestInputMapper:
    CTX = {"fetch": {"result": {"rows": [1, 2]}, "count": 3}, "b": {"x": "y"}}

    def test_resolve_whole_context(self):
        out = resolve("$.", self.CTX)
        assert out == self.CTX

    def test_resolve_node(self):
        assert resolve("$.fetch", self.CTX) == self.CTX["fetch"]

    def test_resolve_field(self):
        assert resolve("$.fetch.count", self.CTX) == 3

    def test_resolve_nested(self):
        assert resolve("$.fetch.result.rows", self.CTX) == [1, 2]

    def test_resolve_missing_node(self):
        assert resolve("$.ghost", self.CTX) is None

    def test_resolve_missing_field(self):
        assert resolve("$.fetch.nope", self.CTX) is None

    def test_resolve_into_scalar(self):
        assert resolve("$.fetch.count.deeper", self.CTX) is None

    def test_empty_path_raises(self):
        with pytest.raises(NopMappingError):
            resolve("", self.CTX)

    def test_bad_prefix_raises(self):
        with pytest.raises(NopMappingError):
            resolve("fetch.count", self.CTX)

    def test_depth_limit_raises(self):
        path = "$." + ".".join(["a"] * (NopConstants.MAX_INPUT_MAPPING_DEPTH + 2))
        with pytest.raises(NopMappingError) as ei:
            resolve(path, self.CTX)
        assert ei.value.error_code == NOP_INPUT_MAPPING_ERROR

    def test_build_params_empty(self):
        assert build_params(None, self.CTX) == {}
        assert build_params({}, self.CTX) == {}

    def test_build_params_string(self):
        out = build_params({"c": "$.fetch.count"}, self.CTX)
        assert out == {"c": 3}

    def test_build_params_list(self):
        # String list elements are treated as JSONPaths; non-string elements pass through.
        out = build_params({"vals": ["$.fetch.count", "$.b.x", 99]}, self.CTX)
        assert out == {"vals": [3, "y", 99]}

    def test_build_params_literal(self):
        out = build_params({"n": 42}, self.CTX)
        assert out == {"n": 42}


# ── Aggregator ─────────────────────────────────────────────────────────────────

class TestAggregator:
    def test_empty(self):
        assert aggregate(AggregateStrategy.MERGE, []) == {}

    def test_first(self):
        assert aggregate(AggregateStrategy.FIRST, [{"a": 1}, {"b": 2}]) == {"a": 1}

    def test_all(self):
        assert aggregate(AggregateStrategy.ALL, [{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]

    def test_fastest_k(self):
        out = aggregate(AggregateStrategy.FASTEST_K, [{"a": 1}, {"b": 2}, {"c": 3}], min_required=2)
        assert out == [{"a": 1}, {"b": 2}]

    def test_fastest_k_zero_takes_all(self):
        out = aggregate(AggregateStrategy.FASTEST_K, [{"a": 1}, {"b": 2}], min_required=0)
        assert out == [{"a": 1}, {"b": 2}]

    def test_merge_objects(self):
        assert merge([{"a": 1}, {"b": 2}]) == {"a": 1, "b": 2}

    def test_merge_last_write_wins(self):
        assert merge([{"a": 1}, {"a": 2}]) == {"a": 2}

    def test_merge_non_object(self):
        out = merge([{"a": 1}, 99])
        assert out == {"a": 1, "_result_1": 99}

    def test_build_array(self):
        assert build_array([1, 2, 3]) == [1, 2, 3]

    def test_aggregate_end_nodes_filters(self):
        results = {"a": {"x": 1}, "b": {"y": 2}, "internal": {"z": 3}}
        out = aggregate_end_nodes(["a", "b"], results)
        assert out == {"x": 1, "y": 2}

    def test_merge_all_default(self):
        out = aggregate(AggregateStrategy.MERGE_ALL, [{"a": 1}, {"b": 2}])
        assert out == {"a": 1, "b": 2}


# ── Orchestrator: happy paths ──────────────────────────────────────────────────

class TestOrchestratorHappyPath:
    async def test_single_node_succeeds(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("a", {"value": 42})
        result = await orch.execute(_single("a"))
        assert result.final_state == TaskState.COMPLETED
        assert result.node_results["a"] == {"value": 42}

    async def test_linear_chain(self):
        orch, worker, _ = build_orchestrator()
        for nid in ("fetch", "analyze", "report"):
            worker.setup_success(nid, {"step": nid})
        result = await orch.execute(_linear("fetch", "analyze", "report"))
        assert result.final_state == TaskState.COMPLETED
        assert len(result.node_results) == 3
        # topo order preserved in call order
        assert worker.calls == ["fetch", "analyze", "report"]

    async def test_diamond_dag(self):
        orch, worker, _ = build_orchestrator()
        for nid, data in (("start", {"x": 1}), ("left", {"l": 10}),
                          ("right", {"r": 20}), ("end", {"done": True})):
            worker.setup_success(nid, data)
        nodes = (
            _node("start"),
            _node("left", input_from=("start",)),
            _node("right", input_from=("start",)),
            _node("end", input_from=("left", "right")),
        )
        edges = (
            DagEdge(from_="start", to="left"),
            DagEdge(from_="start", to="right"),
            DagEdge(from_="left", to="end"),
            DagEdge(from_="right", to="end"),
        )
        task = TaskFrame(task_id=str(uuid.uuid4()), dag=TaskDag(nodes=nodes, edges=edges))
        result = await orch.execute(task)
        assert result.final_state == TaskState.COMPLETED
        assert len(result.node_results) == 4

    async def test_aggregated_merge_of_end_nodes(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("a", {"field_a": "hello"})
        worker.setup_success("b", {"field_b": "world"})
        task = TaskFrame(task_id=str(uuid.uuid4()), dag=TaskDag(nodes=(_node("a"), _node("b")), edges=()))
        result = await orch.execute(task)
        assert result.final_state == TaskState.COMPLETED
        assert result.aggregated_result == {"field_a": "hello", "field_b": "world"}

    async def test_input_mapping_flows_downstream(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("fetch", {"count": 7})
        captured = {}

        async def report_handler(frame: DelegateFrame):
            captured["params"] = frame.params
            return [_final_frame("report", data={"done": True})]

        worker.setup_handler("report", report_handler)
        nodes = (
            _node("fetch"),
            _node("report", input_from=("fetch",), input_mapping={"n": "$.fetch.count"}),
        )
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(nodes=nodes, edges=(DagEdge(from_="fetch", to="report"),)),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.COMPLETED
        assert captured["params"] == {"n": 7}


# ── Orchestrator: condition skip ───────────────────────────────────────────────

class TestOrchestratorCondition:
    async def test_condition_false_skips(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("fetch", {"count": 0})
        worker.setup_success("report", {"done": True})
        nodes = (
            _node("fetch"),
            _node("report", input_from=("fetch",), condition="$.fetch.count > 0"),
        )
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(nodes=nodes, edges=(DagEdge(from_="fetch", to="report"),)),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.COMPLETED
        assert "fetch" in result.node_results
        assert "report" not in result.node_results

    async def test_condition_true_executes(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("fetch", {"count": 5})
        worker.setup_success("report", {"done": True})
        nodes = (
            _node("fetch"),
            _node("report", input_from=("fetch",), condition="$.fetch.count > 0"),
        )
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(nodes=nodes, edges=(DagEdge(from_="fetch", to="report"),)),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.COMPLETED
        assert "report" in result.node_results

    async def test_condition_error_fails_node(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("fetch", {"count": 1})
        # invalid condition -> NopConditionError -> node fails
        nodes = (
            _node("fetch"),
            _node("report", input_from=("fetch",), condition="$.fetch.count >"),
        )
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(nodes=nodes, edges=(DagEdge(from_="fetch", to="report"),)),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED


# ── Orchestrator: failure + K-of-N ─────────────────────────────────────────────

class TestOrchestratorFailure:
    async def test_node_failure_task_fails(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_failure("fetch", "NOP-DELEGATE-REJECTED", "capacity")
        result = await orch.execute(_single("fetch"))
        assert result.final_state == TaskState.FAILED
        assert result.error_code is not None

    async def test_failure_propagates_to_dependent(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_failure("fetch", "NOP-DELEGATE-REJECTED")
        worker.setup_success("analyze", {"ok": True})
        result = await orch.execute(_linear("fetch", "analyze"))
        assert result.final_state == TaskState.FAILED

    async def test_input_mapping_error_fails_node(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("fetch", {"count": 1})
        nodes = (
            _node("fetch"),
            _node("report", input_from=("fetch",), input_mapping={"n": "bad-path"}),
        )
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(nodes=nodes, edges=(DagEdge(from_="fetch", to="report"),)),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED

    async def test_k_of_n_succeeds_with_one_failure(self):
        # fan-in end node needs only 2 of 3 branches; one fails.
        orch, worker, _ = build_orchestrator()
        worker.setup_success("start", {"x": 1})
        worker.setup_success("a", {"a": 1})
        worker.setup_success("b", {"b": 2})
        worker.setup_failure("c", "BRANCH-FAIL")
        worker.setup_success("end", {"done": True})
        nodes = (
            _node("start"),
            _node("a", input_from=("start",)),
            _node("b", input_from=("start",)),
            _node("c", input_from=("start",)),
            _node("end", input_from=("a", "b", "c"), min_required=2),
        )
        edges = tuple(
            DagEdge(from_=f, to=t) for f, t in [
                ("start", "a"), ("start", "b"), ("start", "c"),
                ("a", "end"), ("b", "end"), ("c", "end"),
            ]
        )
        task = TaskFrame(task_id=str(uuid.uuid4()), dag=TaskDag(nodes=nodes, edges=edges))
        result = await orch.execute(task)
        assert result.final_state == TaskState.COMPLETED
        assert "end" in result.node_results

    async def test_k_of_n_fails_with_too_many_failures(self):
        # end needs 2 of 2; one dep fails -> unrecoverable
        orch, worker, _ = build_orchestrator()
        worker.setup_success("start", {"x": 1})
        worker.setup_success("a", {"a": 1})
        worker.setup_failure("b", "BRANCH-FAIL")
        worker.setup_success("end", {"done": True})
        nodes = (
            _node("start"),
            _node("a", input_from=("start",)),
            _node("b", input_from=("start",)),
            _node("end", input_from=("a", "b"), min_required=2),
        )
        edges = tuple(
            DagEdge(from_=f, to=t) for f, t in [
                ("start", "a"), ("start", "b"), ("a", "end"), ("b", "end"),
            ]
        )
        task = TaskFrame(task_id=str(uuid.uuid4()), dag=TaskDag(nodes=nodes, edges=edges))
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED


# ── Orchestrator: retry ────────────────────────────────────────────────────────

class TestOrchestratorRetry:
    async def test_succeeds_on_second_attempt(self):
        orch, worker, _ = build_orchestrator()
        counter = {"n": 0}

        async def handler(frame: DelegateFrame):
            counter["n"] += 1
            if counter["n"] == 1:
                return [_final_frame("op", error=StreamError("ERR", "transient"))]
            return [_final_frame("op", data={"ok": True})]

        worker.setup_handler("op", handler)
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(
                nodes=(_node("op", retry_policy=RetryPolicy(max_retries=2, initial_delay_ms=1)),),
                edges=(),
            ),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.COMPLETED
        assert counter["n"] == 2

    async def test_retry_on_allowlist_blocks_other_codes(self):
        orch, worker, _ = build_orchestrator()
        counter = {"n": 0}

        async def handler(frame: DelegateFrame):
            counter["n"] += 1
            return [_final_frame("op", error=StreamError("OTHER-CODE", "nope"))]

        worker.setup_handler("op", handler)
        # retry_on only allows a different code, so no retry happens
        rp = RetryPolicy(max_retries=3, initial_delay_ms=1, retry_on=("NOP-DELEGATE-TIMEOUT",))
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(nodes=(_node("op", retry_policy=rp),), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert counter["n"] == 1  # no retries

    async def test_exhausts_retries(self):
        orch, worker, _ = build_orchestrator()

        async def handler(frame: DelegateFrame):
            return [_final_frame("op", error=StreamError("ERR", "always"))]

        worker.setup_handler("op", handler)
        rp = RetryPolicy(max_retries=2, initial_delay_ms=1)
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(nodes=(_node("op", retry_policy=rp),), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED


# ── Orchestrator: saga compensation ────────────────────────────────────────────

class TestOrchestratorSaga:
    async def test_best_effort_compensates_completed_predecessor(self):
        orch, worker, _ = build_orchestrator()
        refund = {"calls": 0, "params": None}

        async def charge_handler(frame: DelegateFrame):
            if frame.action == "nwp://payments/refund":
                refund["calls"] += 1
                refund["params"] = frame.params
                return [_final_frame("charge", data={"refunded": True})]
            return [_final_frame("charge", data={"charge_id": "ch_1", "amount": 25})]

        worker.setup_handler("charge", charge_handler)
        worker.setup_failure("ship", "SHIP-FAILED")

        charge = _node(
            "charge",
            compensate_action="nwp://payments/refund",
            compensate_params_mapping={"charge_id": "$.charge.charge_id"},
        )
        ship = _node("ship", input_from=("charge",))
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            compensation_policy=CompensationPolicy.BEST_EFFORT,
            dag=TaskDag(nodes=(charge, ship), edges=(DagEdge(from_="charge", to="ship"),)),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert refund["calls"] == 1
        assert result.compensation is not None
        assert result.compensation.attempted == 1
        assert result.compensation.succeeded == 1
        assert refund["params"] == {"charge_id": "ch_1"}

    async def test_strict_missing_compensate_action_returns_not_supported(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("charge", {"charge_id": "ch_1"})
        worker.setup_failure("ship", "SHIP-FAILED")
        charge = _node("charge")
        ship = _node("ship", input_from=("charge",))
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            compensation_policy=CompensationPolicy.STRICT,
            dag=TaskDag(nodes=(charge, ship), edges=(DagEdge(from_="charge", to="ship"),)),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert result.error_code == NOP_COMPENSATION_NOT_SUPPORTED
        assert result.compensation is not None
        assert result.compensation.attempted == 0
        assert result.compensation.failed == 1
        assert result.compensation.failed_node_ids == ["charge"]

    async def test_strict_compensation_failure_returns_failed(self):
        orch, worker, _ = build_orchestrator()

        async def charge_handler(frame: DelegateFrame):
            if frame.action == "nwp://payments/refund":
                return [_final_frame("charge", error=StreamError("REFUND-FAIL", "declined"))]
            return [_final_frame("charge", data={"charge_id": "ch_1"})]

        worker.setup_handler("charge", charge_handler)
        worker.setup_failure("ship", "SHIP-FAILED")
        charge = _node("charge", compensate_action="nwp://payments/refund")
        ship = _node("ship", input_from=("charge",))
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            compensation_policy=CompensationPolicy.STRICT,
            dag=TaskDag(nodes=(charge, ship), edges=(DagEdge(from_="charge", to="ship"),)),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert result.error_code == NOP_COMPENSATION_FAILED
        assert result.compensation.attempted == 1
        assert result.compensation.failed == 1

    async def test_best_effort_no_compensate_action_no_compensation(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("charge", {"id": 1})
        worker.setup_failure("ship", "SHIP-FAILED")
        charge = _node("charge")
        ship = _node("ship", input_from=("charge",))
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            compensation_policy=CompensationPolicy.BEST_EFFORT,
            dag=TaskDag(nodes=(charge, ship), edges=(DagEdge(from_="charge", to="ship"),)),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        # attempted 0 -> non-strict -> no compensation-specific error
        assert result.compensation is not None
        assert result.compensation.attempted == 0

    async def test_always_policy_compensates_on_success(self):
        orch, worker, _ = build_orchestrator()
        comp = {"calls": 0}

        async def a_handler(frame: DelegateFrame):
            if frame.action == "nwp://undo":
                comp["calls"] += 1
                return [_final_frame("a", data={"undone": True})]
            return [_final_frame("a", data={"v": 1})]

        worker.setup_handler("a", a_handler)
        node = _node("a", compensate_action="nwp://undo")
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            compensation_policy=CompensationPolicy.ALWAYS,
            dag=TaskDag(nodes=(node,), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.COMPLETED
        assert comp["calls"] == 1
        assert result.compensation.succeeded == 1


# ── Orchestrator: validation rejections ────────────────────────────────────────

class TestOrchestratorRejections:
    async def test_cycle_returns_failed(self):
        orch, _, _ = build_orchestrator()
        nodes = (_node("s"), _node("a"), _node("b"), _node("e"))
        edges = (
            DagEdge(from_="s", to="a"),
            DagEdge(from_="a", to="b"),
            DagEdge(from_="b", to="a"),
            DagEdge(from_="a", to="e"),
        )
        task = TaskFrame(task_id=str(uuid.uuid4()), dag=TaskDag(nodes=nodes, edges=edges))
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert result.error_code == NOP_TASK_DAG_CYCLE

    async def test_duplicate_task_id(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("a", {})
        task = _single("a")
        await orch.execute(task)
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert result.error_code == NOP_TASK_ALREADY_COMPLETED

    async def test_delegate_depth_too_deep(self):
        orch, _, _ = build_orchestrator()
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            delegate_depth=NopConstants.MAX_DELEGATE_CHAIN_DEPTH,
            dag=TaskDag(nodes=(_node("a"),), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert result.error_code == NOP_DELEGATE_CHAIN_TOO_DEEP

    async def test_invalid_callback_url(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("a", {})
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            callback_url="http://insecure.example.com/hook",
            dag=TaskDag(nodes=(_node("a"),), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert result.error_code == NOP_TASK_DAG_INVALID


# ── Orchestrator: preflight ────────────────────────────────────────────────────

class TestOrchestratorPreflight:
    async def test_preflight_pass(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("a", {"v": 1})
        task = TaskFrame(
            task_id=str(uuid.uuid4()), preflight=True,
            dag=TaskDag(nodes=(_node("a"),), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.COMPLETED

    async def test_preflight_unavailable_fails(self):
        orch, worker, _ = build_orchestrator()
        worker.preflight_available = False
        worker.preflight_unavailable_reason = "no capacity"
        task = TaskFrame(
            task_id=str(uuid.uuid4()), preflight=True,
            dag=TaskDag(nodes=(_node("a"),), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert result.error_code == NOP_RESOURCE_INSUFFICIENT


# ── Orchestrator: timeout, cancel, status ──────────────────────────────────────

class TestOrchestratorLifecycle:
    async def test_timeout(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("slow", {}, delay_s=5.0)
        task = TaskFrame(
            task_id=str(uuid.uuid4()), timeout_ms=50,
            dag=TaskDag(nodes=(_node("slow"),), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert result.error_code == NOP_TASK_TIMEOUT

    async def test_get_status_after_execution(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("a", {})
        task = _single("a")
        await orch.execute(task)
        record = await orch.get_status(task.task_id)
        assert record is not None
        assert record.task_id == task.task_id
        assert record.state == TaskState.COMPLETED

    async def test_get_status_unknown_returns_none(self):
        orch, _, _ = build_orchestrator()
        assert await orch.get_status("no-such-id") is None

    async def test_cancel_sets_state(self):
        orch, worker, store = build_orchestrator()
        worker.setup_success("a", {})
        task = _single("a")
        await orch.execute(task)
        await orch.cancel(task.task_id)
        record = await store.get(task.task_id)
        assert record.state == TaskState.CANCELLED

    async def test_cancel_unknown_task_no_error(self):
        orch, _, _ = build_orchestrator()
        await orch.cancel("ghost")  # should not raise


# ── Orchestrator: stream validation ────────────────────────────────────────────

class TestOrchestratorStreams:
    async def test_seq_gap_fails(self):
        orch, worker, _ = build_orchestrator()

        async def handler(frame: DelegateFrame):
            return [
                _intermediate("a", 1, {"partial": True}),
                _final_frame("a", data={"done": True}, seq=5),  # gap 1 -> 5
            ]

        worker.setup_handler("a", handler)
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(nodes=(_node("a", retry_policy=RetryPolicy(max_retries=0)),), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        # The node fails with the seq-gap code (recorded on the subtask + in the message).
        assert NOP_STREAM_SEQ_GAP in result.error_message
        record = await orch.get_status(task.task_id)
        assert record.subtasks["a"].error_code == NOP_STREAM_SEQ_GAP

    async def test_intermediate_then_final_ok(self):
        orch, worker, _ = build_orchestrator()

        async def handler(frame: DelegateFrame):
            return [
                _intermediate("a", 0, {"partial": 1}),
                _intermediate("a", 1, {"partial": 2}),
                _final_frame("a", data={"done": True}, seq=2),
            ]

        worker.setup_handler("a", handler)
        result = await orch.execute(_single("a"))
        assert result.final_state == TaskState.COMPLETED
        assert result.node_results["a"] == {"done": True}

    async def test_sender_nid_mismatch_fails(self):
        orch, worker, _ = build_orchestrator(validate_sender_nid=True)

        async def handler(frame: DelegateFrame):
            return [_final_frame("wrong-nid", data={"done": True})]

        worker.setup_handler("a", handler)
        task = TaskFrame(
            task_id=str(uuid.uuid4()),
            dag=TaskDag(nodes=(_node("a", retry_policy=RetryPolicy(max_retries=0)),), edges=()),
        )
        result = await orch.execute(task)
        assert result.final_state == TaskState.FAILED
        assert NOP_STREAM_NID_MISMATCH in result.error_message
        record = await orch.get_status(task.task_id)
        assert record.subtasks["a"].error_code == NOP_STREAM_NID_MISMATCH

    async def test_stream_without_final_fails(self):
        orch, worker, _ = build_orchestrator()

        async def handler(frame: DelegateFrame):
            return [_intermediate("a", 0, {"partial": True})]  # no final frame

        worker.setup_handler("a", handler)
        result = await orch.execute(_single("a"))
        assert result.final_state == TaskState.FAILED


# ── Callback signature + delivery ──────────────────────────────────────────────

class TestCallbackSignature:
    def _key_b64url(self) -> tuple[bytes, str]:
        key = b"\x01" * 32
        b64 = base64.urlsafe_b64encode(key).rstrip(b"=").decode()
        return key, b64

    def test_signature_matches_hmac(self):
        key, b64 = self._key_b64url()
        payload = '{"task_id":"t"}'
        sig = _build_callback_signature(b64, payload)
        expected = "sha256=" + hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        assert sig == expected

    def test_signature_none_without_secret(self):
        assert _build_callback_signature(None, "x") is None
        assert _build_callback_signature("   ", "x") is None

    def test_signature_none_for_bad_key_length(self):
        short = base64.urlsafe_b64encode(b"short").rstrip(b"=").decode()
        assert _build_callback_signature(short, "x") is None

    def test_signature_none_for_invalid_base64(self):
        assert _build_callback_signature("!!!not-base64!!!", "x") is None

    async def test_callback_fired_with_signature(self):
        orch, worker, _ = build_orchestrator()
        worker.setup_success("a", {"v": 1})
        _, b64 = self._key_b64url()
        captured = {}

        with respx.mock:
            route = respx.post("https://hooks.example.com/done").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            task = TaskFrame(
                task_id=str(uuid.uuid4()),
                callback_url="https://hooks.example.com/done",
                callback_secret=b64,
                dag=TaskDag(nodes=(_node("a"),), edges=()),
            )
            result = await orch.execute(task)
            assert result.final_state == TaskState.COMPLETED
            # Allow the fire-and-forget callback task to run.
            for _ in range(20):
                if route.called:
                    break
                await asyncio.sleep(0.01)
            assert route.called
            request = route.calls.last.request
            captured["sig"] = request.headers.get("X-NPS-Signature")
            body = request.content.decode()

        assert captured["sig"] is not None
        assert captured["sig"].startswith("sha256=")
        expected = "sha256=" + hmac.new(
            base64.urlsafe_b64decode(b64 + "="), body.encode(), hashlib.sha256
        ).hexdigest()
        assert captured["sig"] == expected

    async def test_callback_retries_then_gives_up(self):
        orch, worker, _ = build_orchestrator(callback_retry_base_delay_ms=0)
        worker.setup_success("a", {"v": 1})
        with respx.mock:
            route = respx.post("https://hooks.example.com/fail").mock(
                return_value=httpx.Response(503)
            )
            task = TaskFrame(
                task_id=str(uuid.uuid4()),
                callback_url="https://hooks.example.com/fail",
                dag=TaskDag(nodes=(_node("a"),), edges=()),
            )
            await orch.execute(task)
            for _ in range(50):
                if route.call_count >= NopConstants.CALLBACK_MAX_RETRIES:
                    break
                await asyncio.sleep(0.01)
            assert route.call_count == NopConstants.CALLBACK_MAX_RETRIES


# ── Result serialization ───────────────────────────────────────────────────────

class TestResults:
    def test_success_to_dict(self):
        r = NopTaskResult.success("t", {"agg": 1}, {"n": {"x": 1}})
        d = r.to_dict()
        assert d["task_id"] == "t"
        assert d["final_state"] == "completed"
        assert d["aggregated_result"] == {"agg": 1}
        assert d["node_results"] == {"n": {"x": 1}}

    def test_failure_to_dict(self):
        r = NopTaskResult.failure("t", "CODE", "msg")
        d = r.to_dict()
        assert d["error_code"] == "CODE"
        assert d["error_message"] == "msg"
        assert d["final_state"] == "failed"

    def test_cancelled(self):
        r = NopTaskResult.cancelled("t", "user")
        assert r.final_state == TaskState.CANCELLED

    def test_to_dict_with_compensation(self):
        comp = SagaCompensationResult(2, 1, 1, ["x"])
        r = NopTaskResult.failure("t", "C", "m", comp)
        d = r.to_dict()
        assert d["compensation"]["attempted"] == 2
        assert d["compensation"]["failed_node_ids"] == ["x"]


# ── CompensationPolicy helpers ─────────────────────────────────────────────────

class TestCompensationPolicy:
    def test_runs_on_failure(self):
        assert CompensationPolicy.runs_on_failure(CompensationPolicy.BEST_EFFORT)
        assert CompensationPolicy.runs_on_failure(CompensationPolicy.STRICT)
        assert CompensationPolicy.runs_on_failure(CompensationPolicy.ON_FAILURE)
        assert CompensationPolicy.runs_on_failure(CompensationPolicy.ALWAYS)
        assert not CompensationPolicy.runs_on_failure(CompensationPolicy.NONE)

    def test_runs_on_success(self):
        assert CompensationPolicy.runs_on_success(CompensationPolicy.ALWAYS)
        assert not CompensationPolicy.runs_on_success(CompensationPolicy.BEST_EFFORT)

    def test_is_strict(self):
        assert CompensationPolicy.is_strict(CompensationPolicy.STRICT)
        assert not CompensationPolicy.is_strict(CompensationPolicy.BEST_EFFORT)


# ── Store ──────────────────────────────────────────────────────────────────────

class TestStore:
    async def test_save_duplicate_raises(self):
        store = InMemoryNopTaskStore()
        frame = _single("a")
        rec = NopTaskRecord(task_id="t", frame=frame)
        await store.save(rec)
        with pytest.raises(ValueError):
            await store.save(NopTaskRecord(task_id="t", frame=frame))

    async def test_update_state_unknown_no_error(self):
        store = InMemoryNopTaskStore()
        await store.update_state("ghost", TaskState.RUNNING)  # no-op

    async def test_update_subtask_creates_and_updates(self):
        store = InMemoryNopTaskStore()
        frame = _single("a")
        await store.save(NopTaskRecord(task_id="t", frame=frame))
        await store.update_subtask("t", "a", "sub-1", TaskState.RUNNING, attempt=1)
        await store.update_subtask(
            "t", "a", "sub-1", TaskState.COMPLETED, result={"x": 1}, attempt=2
        )
        rec = await store.get("t")
        assert rec.subtasks["a"].state == TaskState.COMPLETED
        assert rec.subtasks["a"].result == {"x": 1}
        assert rec.subtasks["a"].attempt_count == 2

    async def test_update_subtask_unknown_task_no_error(self):
        store = InMemoryNopTaskStore()
        await store.update_subtask("ghost", "a", "s", TaskState.RUNNING)
