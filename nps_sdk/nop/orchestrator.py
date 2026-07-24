# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Core NOP Orchestrator (NPS-5 §3, §5).

Accepts a :class:`TaskFrame`, runs its DAG by dispatching
:class:`DelegateFrame`s to Worker Agents, handles retries, condition-based
skipping, K-of-N synchronization, saga compensation, result aggregation, and
signed completion callbacks.

Telemetry: task/node spans plus the ``nps.nop.*`` counters and histograms are
emitted via :mod:`nps_sdk.nop.instrumentation` (a dependency-free port of the
.NET ``NopTelemetry`` instruments). This is a faithful async/await port of the
.NET reference engine.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol, Sequence

import httpx

from nps_sdk.nop import aggregator
from nps_sdk.nop import condition as condition_mod
from nps_sdk.nop import input_mapper
from nps_sdk.nop import instrumentation as _telemetry
from nps_sdk.nop.constants import NopConstants
from nps_sdk.nop.error_codes import (
    NOP_COMPENSATION_FAILED,
    NOP_COMPENSATION_NOT_SUPPORTED,
    NOP_CONDITION_EVAL_ERROR,
    NOP_DELEGATE_CHAIN_TOO_DEEP,
    NOP_DELEGATE_TIMEOUT,
    NOP_RESOURCE_INSUFFICIENT,
    NOP_STREAM_NID_MISMATCH,
    NOP_STREAM_SEQ_GAP,
    NOP_SYNC_DEPENDENCY_FAILED,
    NOP_TASK_ALREADY_COMPLETED,
    NOP_TASK_DAG_INVALID,
    NOP_TASK_TIMEOUT,
)
from nps_sdk.nop.frames import DelegateFrame, TaskFrame
from nps_sdk.nop.models import CompensationPolicy, DagNode, TaskState
from nps_sdk.nop.results import NopTaskResult, SagaCompensationResult
from nps_sdk.nop.store import INopTaskStore, NopTaskRecord
from nps_sdk.nop.validation import DagValidator, NopCallbackValidator
from nps_sdk.nop.worker import INopWorkerClient, PreflightResult

_log = logging.getLogger("nps.nop.orchestrator")

_CALLBACK_SIGNATURE_HEADER = "X-NPS-Signature"


# ── Options ───────────────────────────────────────────────────────────────────

@dataclass
class NopOrchestratorOptions:
    """Configuration options for :class:`NopOrchestrator`."""

    #: Maximum DAG nodes that may execute concurrently per task.
    max_concurrent_nodes: int = 16
    #: Validate ``AlignStreamFrame.sender_nid`` against the node agent NID.
    validate_sender_nid: bool = True
    #: POST the result to ``callback_url`` on completion (fire-and-forget).
    enable_callback: bool = True
    #: HTTP client timeout for callback POSTs (milliseconds).
    callback_timeout_ms: int = 10_000
    #: Base delay (ms) for exponential backoff between callback retries.
    #: Set 0 in tests to avoid real delays. Delay(n) = base * 2^(n-1).
    callback_retry_base_delay_ms: int = 1000
    #: Default aggregate strategy applied to end nodes.
    default_aggregate_strategy: str = "merge"


# ── Node outcome ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _NodeOutcome:
    state: TaskState
    result: Any = None
    error_code: str | None = None
    error_message: str | None = None


# ── Orchestrator contract ─────────────────────────────────────────────────────

class INopOrchestrator(Protocol):
    """Core NOP orchestrator contract (NPS-5 §3, §5)."""

    async def execute(self, task: TaskFrame) -> NopTaskResult: ...
    async def cancel(self, task_id: str) -> None: ...
    async def get_status(self, task_id: str) -> NopTaskRecord | None: ...


# ── Implementation ────────────────────────────────────────────────────────────

class NopOrchestrator:
    """
    Core NOP Orchestrator: accepts a :class:`TaskFrame`, runs its DAG by
    dispatching :class:`DelegateFrame`s to Worker Agents, and returns a
    :class:`NopTaskResult` when the task reaches a terminal state.
    """

    def __init__(
        self,
        worker: INopWorkerClient,
        store: INopTaskStore,
        options: NopOrchestratorOptions | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._worker = worker
        self._store = store
        self._opts = options or NopOrchestratorOptions()
        self._http = http_client
        # Per-task cancellation events keyed by task_id.
        self._cancel_events: dict[str, asyncio.Event] = {}

    # ── INopOrchestrator ──────────────────────────────────────────────────────

    async def execute(self, task: TaskFrame) -> NopTaskResult:
        with _telemetry.source.start_span(
            "nps.nop.task.execute", **{"task.id": task.task_id}
        ) as span:
            started = time.perf_counter()
            try:
                result = await self._execute_instrumented(task, span)
            except BaseException:
                span.set_status("error")
                raise
            succeeded = result.final_state == TaskState.COMPLETED
            outcome = "success" if succeeded else "failure"
            _telemetry.task_duration_ms.record(
                (time.perf_counter() - started) * 1000.0, outcome=outcome)
            if succeeded:
                _telemetry.tasks_completed.add(1)
            else:
                _telemetry.tasks_failed.add(1)
            span.set_attribute("task.outcome", outcome)
            return result

    async def _execute_instrumented(
        self, task: TaskFrame, span: Any
    ) -> NopTaskResult:
        # 1a. Delegation chain depth
        if task.delegate_depth >= NopConstants.MAX_DELEGATE_CHAIN_DEPTH:
            _log.warning(
                "Task %s rejected: delegation chain depth %d >= max %d",
                task.task_id, task.delegate_depth, NopConstants.MAX_DELEGATE_CHAIN_DEPTH,
            )
            return NopTaskResult.failure(
                task.task_id, NOP_DELEGATE_CHAIN_TOO_DEEP,
                f"Delegation chain depth {task.delegate_depth} exceeds the "
                f"maximum of {NopConstants.MAX_DELEGATE_CHAIN_DEPTH}.",
            )

        # 1b. callback_url (MUST https://, SHOULD NOT be private IP)
        if task.callback_url:
            url_error = NopCallbackValidator.validate_callback_url(task.callback_url)
            if url_error is not None:
                _log.warning("Task %s rejected: invalid callback_url — %s",
                             task.task_id, url_error)
                return NopTaskResult.failure(
                    task.task_id, NOP_TASK_DAG_INVALID, url_error
                )

        # 1c. DAG validation
        validation = DagValidator.validate(task.dag)
        if not validation.is_valid:
            _log.warning("DAG validation failed for task %s: %s",
                         task.task_id, validation.error_message)
            return NopTaskResult.failure(
                task.task_id, validation.error_code, validation.error_message
            )

        # 2. Reject already-known tasks
        if await self._store.get(task.task_id) is not None:
            return NopTaskResult.failure(
                task.task_id, NOP_TASK_ALREADY_COMPLETED,
                f"Task '{task.task_id}' already exists.",
            )

        # 3. Persist initial record
        record = NopTaskRecord(task_id=task.task_id, frame=task, state=TaskState.PENDING)
        await self._store.save(record)

        # 4. Register a cancellation event; compute the effective timeout.
        cancel_event = asyncio.Event()
        self._cancel_events[task.task_id] = cancel_event
        timeout_ms = min(task.timeout_ms, NopConstants.MAX_TIMEOUT_MS)

        try:
            result = await asyncio.wait_for(
                self._run_task(task, record, validation.topological_order, cancel_event),
                timeout=timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            _log.warning("Task %s exceeded timeout of %dms", task.task_id, timeout_ms)
            await self._store.update_state(task.task_id, TaskState.FAILED)
            return NopTaskResult.failure(
                task.task_id, NOP_TASK_TIMEOUT,
                f"Task exceeded timeout of {timeout_ms}ms.",
            )
        finally:
            self._cancel_events.pop(task.task_id, None)

        # 7. Finalise state in store
        record.completed_at = datetime.now(timezone.utc)
        await self._store.update_state(task.task_id, result.final_state)

        # 8. Fire callback (fire-and-forget)
        if self._opts.enable_callback and task.callback_url:
            asyncio.ensure_future(
                self._fire_callback(task.callback_url, task.callback_secret, result)
            )

        _log.info("Task %s finished as %s", task.task_id, result.final_state)
        return result

    async def cancel(self, task_id: str) -> None:
        event = self._cancel_events.get(task_id)
        if event is not None:
            event.set()
        await self._store.update_state(task_id, TaskState.CANCELLED)

    async def get_status(self, task_id: str) -> NopTaskRecord | None:
        return await self._store.get(task_id)

    # ── Task body (preflight + DAG) ───────────────────────────────────────────

    async def _run_task(
        self,
        task: TaskFrame,
        record: NopTaskRecord,
        topo_order: list[str],
        cancel_event: asyncio.Event,
    ) -> NopTaskResult:
        # 5. Optional preflight
        if task.preflight:
            await self._store.update_state(task.task_id, TaskState.PREFLIGHT)
            preflight_fail = await self._run_preflight(task)
            if preflight_fail is not None:
                _log.warning("Preflight failed for task %s: %s",
                             task.task_id, preflight_fail)
                await self._store.update_state(task.task_id, TaskState.FAILED)
                return NopTaskResult.failure(
                    task.task_id, NOP_RESOURCE_INSUFFICIENT, preflight_fail
                )

        await self._store.update_state(task.task_id, TaskState.RUNNING)

        # 6. Execute DAG
        return await self._run_dag(task, record, topo_order, cancel_event)

    # ── DAG execution ─────────────────────────────────────────────────────────

    async def _run_dag(
        self,
        task: TaskFrame,
        record: NopTaskRecord,
        topo_order: list[str],
        cancel_event: asyncio.Event,
    ) -> NopTaskResult:
        all_nodes: dict[str, DagNode] = {n.id: n for n in task.dag.nodes}
        node_results: dict[str, Any] = {}   # nodeId -> result (completed only)
        node_states: dict[str, TaskState] = {}   # nodeId -> terminal state
        in_flight: dict[str, asyncio.Task[_NodeOutcome]] = {}   # nodeId -> running task

        has_outgoing = {e.from_ for e in task.dag.edges}
        end_node_ids = [nid for nid in all_nodes if nid not in has_outgoing]

        while len(node_states) < len(all_nodes):
            if cancel_event.is_set():
                raise asyncio.CancelledError()

            # Ready nodes: deps done, not started, not in flight
            ready_nodes = [
                n for n in all_nodes.values()
                if n.id not in node_states
                and n.id not in in_flight
                and _are_deps_done(n, node_states)
            ]

            # K-of-N: fail any ready node whose K can never be met.
            for n in list(ready_nodes):
                if not n.input_from:
                    continue
                total = len(n.input_from)
                k = n.min_required if n.min_required > 0 else total
                success = sum(
                    1 for d in n.input_from
                    if node_states.get(d) in (TaskState.COMPLETED, TaskState.SKIPPED)
                )
                if success < k:
                    node_states[n.id] = TaskState.FAILED
                    await self._store.update_subtask(
                        task.task_id, n.id, _new_id(), TaskState.FAILED,
                        error_code=NOP_SYNC_DEPENDENCY_FAILED,
                        error_message=f"Only {success}/{k} required dependencies succeeded.",
                    )
                    ready_nodes.remove(n)

            # Launch ready nodes up to max_concurrent_nodes.
            for node in ready_nodes:
                if len(in_flight) >= self._opts.max_concurrent_nodes:
                    break
                in_flight[node.id] = asyncio.ensure_future(
                    self._execute_node_with_retry(task, node, node_results, cancel_event)
                )

            if not in_flight:
                break  # stuck or finished

            # Wait for the next completion.
            done, _pending = await asyncio.wait(
                in_flight.values(), return_when=asyncio.FIRST_COMPLETED
            )
            finished_task = next(iter(done))
            finished_node_id = next(
                nid for nid, t in in_flight.items() if t is finished_task
            )
            del in_flight[finished_node_id]

            outcome = finished_task.result()  # may raise CancelledError -> propagate
            node_states[finished_node_id] = outcome.state
            if outcome.state == TaskState.COMPLETED and outcome.result is not None:
                node_results[finished_node_id] = outcome.result

            # Abort only if an end node can no longer satisfy its K.
            if outcome.state == TaskState.FAILED:
                must_abort = any(
                    _can_reach_end_node(e, finished_node_id, task.dag.edges)
                    and not _can_end_node_still_succeed(e, all_nodes, node_states)
                    for e in end_node_ids
                )
                if must_abort:
                    _log.warning(
                        "Node %s failed; end node(s) cannot recover — aborting task %s",
                        finished_node_id, task.task_id,
                    )
                    await _wait_and_abort_in_flight(in_flight)
                    compensation = (
                        await self._run_saga_compensation(
                            task, all_nodes, topo_order, node_results, node_states
                        )
                        if CompensationPolicy.runs_on_failure(task.compensation_policy)
                        else None
                    )
                    error_code = (
                        _compensation_failure_error_code(task, compensation)
                        or NOP_SYNC_DEPENDENCY_FAILED
                    )
                    return NopTaskResult.failure(
                        task.task_id, error_code,
                        f"Node '{finished_node_id}' failed: {outcome.error_code}",
                        compensation,
                    )

        # All nodes done — check for end-node failures.
        failed_nodes = [nid for nid, s in node_states.items() if s == TaskState.FAILED]
        if failed_nodes and any(
            node_states.get(e) == TaskState.FAILED for e in end_node_ids
        ):
            compensation = (
                await self._run_saga_compensation(
                    task, all_nodes, topo_order, node_results, node_states
                )
                if CompensationPolicy.runs_on_failure(task.compensation_policy)
                else None
            )
            error_code = (
                _compensation_failure_error_code(task, compensation)
                or NOP_SYNC_DEPENDENCY_FAILED
            )
            return NopTaskResult.failure(
                task.task_id, error_code,
                f"End node(s) failed: {', '.join(failed_nodes)}",
                compensation,
            )

        aggregated = aggregator.aggregate_end_nodes(
            end_node_ids, node_results, self._opts.default_aggregate_strategy
        )

        success_compensation = (
            await self._run_saga_compensation(
                task, all_nodes, topo_order, node_results, node_states
            )
            if CompensationPolicy.runs_on_success(task.compensation_policy)
            else None
        )

        return NopTaskResult.success(
            task.task_id, aggregated, node_results, success_compensation
        )

    # ── Node execution + retry ────────────────────────────────────────────────

    async def _execute_node_with_retry(
        self,
        task: TaskFrame,
        node: DagNode,
        context: Mapping[str, Any],
        cancel_event: asyncio.Event,
    ) -> _NodeOutcome:
        subtask_id = _new_id()
        idempotency_key = _new_id()   # same across retries
        max_retries = node.retry_policy.max_retries if node.retry_policy else task.max_retries
        node_started = time.perf_counter()

        def _record_node(outcome: str) -> None:
            _telemetry.node_duration_ms.record(
                (time.perf_counter() - node_started) * 1000.0, outcome=outcome)

        for attempt in range(1, max_retries + 2):   # 1 .. max_retries+1
            if cancel_event.is_set():
                raise asyncio.CancelledError()

            # Evaluate condition once, before the first attempt.
            if attempt == 1 and node.condition:
                try:
                    if not condition_mod.evaluate(node.condition, context):
                        _log.debug("Node %s skipped (condition=false)", node.id)
                        await self._store.update_subtask(
                            task.task_id, node.id, subtask_id, TaskState.SKIPPED
                        )
                        _record_node("skipped")
                        return _NodeOutcome(TaskState.SKIPPED)
                except condition_mod.NopConditionError as ex:
                    _log.error("Condition evaluation error for node %s: %s", node.id, ex)
                    await self._store.update_subtask(
                        task.task_id, node.id, subtask_id, TaskState.FAILED,
                        error_code=NOP_CONDITION_EVAL_ERROR, error_message=str(ex),
                        attempt=attempt,
                    )
                    _record_node("failure")
                    return _NodeOutcome(
                        TaskState.FAILED, error_code=NOP_CONDITION_EVAL_ERROR,
                        error_message=str(ex),
                    )

            await self._store.update_subtask(
                task.task_id, node.id, subtask_id, TaskState.RUNNING, attempt=attempt
            )

            outcome = await self._execute_node_once(
                task, node, subtask_id, idempotency_key, context, cancel_event
            )

            if outcome.state == TaskState.COMPLETED:
                await self._store.update_subtask(
                    task.task_id, node.id, subtask_id, TaskState.COMPLETED,
                    result=outcome.result, attempt=attempt,
                )
                _record_node("success")
                return outcome

            # Failed — retryable?
            if not _should_retry(node.retry_policy, outcome.error_code, attempt, max_retries):
                _log.warning("Node %s failed after %d attempt(s): %s",
                             node.id, attempt, outcome.error_code)
                await self._store.update_subtask(
                    task.task_id, node.id, subtask_id, TaskState.FAILED,
                    error_code=outcome.error_code, error_message=outcome.error_message,
                    attempt=attempt,
                )
                _record_node("failure")
                return outcome

            _telemetry.node_retries.add(1)
            delay_ms = (
                node.retry_policy.compute_delay_ms(attempt) if node.retry_policy else 1000
            )
            _log.debug("Node %s retrying in %dms (attempt %d/%d)",
                       node.id, delay_ms, attempt, max_retries + 1)
            await asyncio.sleep(delay_ms / 1000.0)

        # Exhausted retries
        await self._store.update_subtask(
            task.task_id, node.id, subtask_id, TaskState.FAILED,
            error_code=NOP_DELEGATE_TIMEOUT,
            error_message=f"Node '{node.id}' exhausted {max_retries} retries.",
        )
        _record_node("exhausted")
        return _NodeOutcome(TaskState.FAILED, error_code=NOP_DELEGATE_TIMEOUT)

    async def _execute_node_once(
        self,
        task: TaskFrame,
        node: DagNode,
        subtask_id: str,
        idempotency_key: str,
        context: Mapping[str, Any],
        cancel_event: asyncio.Event,
    ) -> _NodeOutcome:
        # Resolve input_mapping -> params
        try:
            resolved_params = input_mapper.build_params(node.input_mapping, context)
        except input_mapper.NopMappingError as ex:
            return _NodeOutcome(
                TaskState.FAILED, error_code=ex.error_code, error_message=str(ex)
            )

        node_timeout_ms = node.timeout_ms if node.timeout_ms is not None else task.timeout_ms
        node_timeout_ms = min(node_timeout_ms, NopConstants.MAX_TIMEOUT_MS)
        deadline = datetime.now(timezone.utc) + timedelta(milliseconds=node_timeout_ms)

        delegate_frame = DelegateFrame(
            parent_task_id=task.task_id,
            subtask_id=subtask_id,
            node_id=node.id,
            target_agent_nid=node.agent,
            action=node.action,
            params=resolved_params,
            delegated_scope={},   # scope handled by the NIP layer
            deadline_at=deadline.isoformat(),
            idempotency_key=idempotency_key,
            priority=task.priority,
            context=task.context,
            delegate_depth=task.delegate_depth + 1,
        )

        try:
            return await asyncio.wait_for(
                self._consume_stream(node, delegate_frame),
                timeout=node_timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            return _NodeOutcome(
                TaskState.FAILED, error_code=NOP_DELEGATE_TIMEOUT,
                error_message=f"Node '{node.id}' timed out after {node_timeout_ms}ms.",
            )

    async def _consume_stream(
        self, node: DagNode, delegate_frame: DelegateFrame
    ) -> _NodeOutcome:
        final_result: Any = None
        error_code: str | None = None
        error_msg: str | None = None
        last_seq = 0
        got_final = False

        async for frame in self._worker.delegate(delegate_frame):
            # Sequence-gap check (0 is exempt, as in the reference).
            if frame.seq != last_seq and frame.seq != 0:
                if frame.seq != last_seq + 1:
                    _log.warning("Node %s: seq gap %d -> %d", node.id, last_seq, frame.seq)
                    return _NodeOutcome(TaskState.FAILED, error_code=NOP_STREAM_SEQ_GAP)
            last_seq = frame.seq

            # Sender NID validation
            if self._opts.validate_sender_nid and frame.sender_nid != node.agent:
                _log.warning(
                    "Node %s: sender_nid mismatch (expected %s, got %s)",
                    node.id, node.agent, frame.sender_nid,
                )
                return _NodeOutcome(TaskState.FAILED, error_code=NOP_STREAM_NID_MISMATCH)

            if frame.is_final:
                got_final = True
                if frame.error is not None:
                    error_code = frame.error.error_code
                    error_msg = frame.error.message
                else:
                    final_result = frame.data
                break

        if not got_final:
            return _NodeOutcome(
                TaskState.FAILED, error_code=NOP_DELEGATE_TIMEOUT,
                error_message="Stream ended without final frame.",
            )
        if error_code is not None:
            return _NodeOutcome(TaskState.FAILED, error_code=error_code, error_message=error_msg)
        return _NodeOutcome(TaskState.COMPLETED, result=final_result)

    # ── Preflight ─────────────────────────────────────────────────────────────

    async def _run_preflight(self, task: TaskFrame) -> str | None:
        """Run preflight probes; return a failure message, or ``None`` on success."""
        # Deduplicate by agent NID (one probe per unique agent).
        agent_actions: dict[str, str] = {}
        for n in task.dag.nodes:
            agent_actions.setdefault(n.agent, n.action)

        _log.debug("Running preflight for task %s against %d agent(s)",
                   task.task_id, len(agent_actions))

        probes = [
            self._worker.preflight(agent, action)
            for agent, action in agent_actions.items()
        ]
        try:
            results: Sequence[PreflightResult] = await asyncio.gather(*probes)
        except Exception as ex:   # noqa: BLE001
            return f"Preflight probe failed: {ex}"

        for r in results:
            if not r.available:
                return (
                    f"Agent '{r.agent_nid}' is unavailable: "
                    f"{r.unavailable_reason or 'no reason given'}"
                )
        return None

    # ── Saga compensation ─────────────────────────────────────────────────────

    async def _run_saga_compensation(
        self,
        task: TaskFrame,
        all_nodes: Mapping[str, DagNode],
        topo_order: Sequence[str],
        node_results: Mapping[str, Any],
        node_states: Mapping[str, TaskState],
    ) -> SagaCompensationResult:
        # Completed nodes in reverse topological order.
        completed = [
            nid for nid in reversed(list(topo_order))
            if node_states.get(nid) == TaskState.COMPLETED and nid in all_nodes
        ]

        if CompensationPolicy.is_strict(task.compensation_policy):
            missing = [
                nid for nid in completed
                if not (all_nodes[nid].compensate_action or "").strip()
            ]
            if missing:
                _log.warning(
                    "Strict saga compensation for task %s cannot proceed; "
                    "node(s) lack compensate_action: %s",
                    task.task_id, ", ".join(missing),
                )
                return SagaCompensationResult(0, 0, len(missing), missing)

        to_compensate = [
            nid for nid in completed
            if (all_nodes[nid].compensate_action or "").strip()
        ]
        if not to_compensate:
            return SagaCompensationResult(0, 0, 0, [])

        _log.info("Saga compensation: %d node(s) to compensate for task %s",
                  len(to_compensate), task.task_id)
        await self._store.update_state(task.task_id, TaskState.COMPENSATING)

        cancel_event = asyncio.Event()  # compensation is not externally cancellable here
        succeeded = 0
        failed_ids: list[str] = []

        for node_id in to_compensate:
            node = all_nodes[node_id]
            comp_node = _replace_node(
                node,
                action=node.compensate_action,
                input_mapping=node.compensate_params_mapping,
            )
            outcome = await self._execute_node_once(
                task, comp_node, _new_id(), _new_id(), node_results, cancel_event
            )
            if outcome.state == TaskState.COMPLETED:
                succeeded += 1
                _log.info("Compensation for node %s succeeded", node_id)
            else:
                failed_ids.append(node_id)
                _log.warning("Compensation for node %s failed: %s — %s",
                             node_id, outcome.error_code, outcome.error_message)

        failed = len(failed_ids)
        if failed == 0:
            _log.info("Saga compensation for task %s complete (%d succeeded)",
                      task.task_id, succeeded)
        else:
            _log.warning("Saga compensation for task %s: %d/%d failed",
                         task.task_id, failed, len(to_compensate))
        return SagaCompensationResult(len(to_compensate), succeeded, failed, failed_ids)

    # ── Callback ──────────────────────────────────────────────────────────────

    async def _fire_callback(
        self, callback_url: str, callback_secret: str | None, result: NopTaskResult
    ) -> None:
        payload = json.dumps(result.to_dict(), separators=(",", ":"))
        signature = _build_callback_signature(callback_secret, payload)
        if callback_secret and callback_secret.strip() and signature is None:
            _log.warning(
                "callback_secret is not a valid base64url-encoded 32-byte HMAC key; "
                "callback will be sent without %s.", _CALLBACK_SIGNATURE_HEADER,
            )

        headers = {"Content-Type": "application/json"}
        if signature is not None:
            headers[_CALLBACK_SIGNATURE_HEADER] = signature

        own_client = self._http is None
        client = self._http or httpx.AsyncClient(
            timeout=self._opts.callback_timeout_ms / 1000.0
        )
        try:
            for attempt in range(1, NopConstants.CALLBACK_MAX_RETRIES + 1):
                try:
                    response = await client.post(
                        callback_url, content=payload, headers=headers
                    )
                    if response.is_success:
                        _log.info("Callback to %s succeeded on attempt %d (%d)",
                                  callback_url, attempt, response.status_code)
                        return
                    _log.warning(
                        "Callback to %s returned non-success %d (attempt %d/%d)",
                        callback_url, response.status_code, attempt,
                        NopConstants.CALLBACK_MAX_RETRIES,
                    )
                except httpx.HTTPError as ex:
                    _log.warning("Callback to %s failed (attempt %d/%d): %s",
                                 callback_url, attempt,
                                 NopConstants.CALLBACK_MAX_RETRIES, ex)

                if (attempt < NopConstants.CALLBACK_MAX_RETRIES
                        and self._opts.callback_retry_base_delay_ms > 0):
                    delay_ms = self._opts.callback_retry_base_delay_ms * (2 ** (attempt - 1))
                    await asyncio.sleep(delay_ms / 1000.0)

            _log.warning("Callback to %s gave up after %d attempt(s) — non-fatal.",
                         callback_url, NopConstants.CALLBACK_MAX_RETRIES)
        finally:
            if own_client:
                await client.aclose()


# ── Module-level helpers ──────────────────────────────────────────────────────

def _new_id() -> str:
    return str(uuid.uuid4())


def _are_deps_done(node: DagNode, states: Mapping[str, TaskState]) -> bool:
    """
    True when a node's dependencies are terminal enough to either proceed
    (K satisfied) or be marked failed (K impossible). Supports K-of-N.
    """
    if not node.input_from:
        return True
    total = len(node.input_from)
    k = node.min_required if node.min_required > 0 else total
    success = sum(
        1 for d in node.input_from
        if states.get(d) in (TaskState.COMPLETED, TaskState.SKIPPED)
    )
    failed = sum(1 for d in node.input_from if states.get(d) == TaskState.FAILED)
    if success >= k:
        return True   # K already satisfied
    if total - failed < k:
        return True   # impossible to satisfy K
    return False      # still waiting


def _can_end_node_still_succeed(
    end_node_id: str,
    all_nodes: Mapping[str, DagNode],
    node_states: Mapping[str, TaskState],
) -> bool:
    """
    Optimistic: in-flight / not-started deps are assumed to eventually succeed.
    Returns True when the end node can still satisfy its K.
    """
    node = all_nodes[end_node_id]
    if not node.input_from:
        return False   # no deps but reachable from a failure -> unrecoverable
    total = len(node.input_from)
    k = node.min_required if node.min_required > 0 else total
    failed = sum(1 for d in node.input_from if node_states.get(d) == TaskState.FAILED)
    optimistic = total - failed
    return optimistic >= k


def _can_reach_end_node(
    end_node_id: str, failed_node_id: str, edges: Sequence[Any]
) -> bool:
    """BFS: can ``end_node_id`` be reached from ``failed_node_id`` via edges?"""
    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e.from_, []).append(e.to)

    visited: set[str] = set()
    queue: deque[str] = deque([failed_node_id])
    while queue:
        cur = queue.popleft()
        if cur == end_node_id:
            return True
        if cur in visited:
            continue
        visited.add(cur)
        for n in adj.get(cur, []):
            queue.append(n)
    return False


async def _wait_and_abort_in_flight(
    in_flight: Mapping[str, asyncio.Task[_NodeOutcome]]
) -> None:
    if not in_flight:
        return
    try:
        await asyncio.gather(*in_flight.values(), return_exceptions=True)
    except Exception:   # noqa: BLE001
        pass


def _should_retry(
    policy: Any, error_code: str | None, attempt: int, max_retries: int
) -> bool:
    if attempt > max_retries:
        return False
    if policy is not None and policy.retry_on and error_code is not None:
        return error_code in policy.retry_on
    return True


def _compensation_failure_error_code(
    task: TaskFrame, compensation: SagaCompensationResult | None
) -> str | None:
    if not CompensationPolicy.is_strict(task.compensation_policy):
        return None
    if compensation is None or compensation.failed <= 0:
        return None
    return (
        NOP_COMPENSATION_NOT_SUPPORTED
        if compensation.attempted == 0
        else NOP_COMPENSATION_FAILED
    )


def _build_callback_signature(callback_secret: str | None, payload: str) -> str | None:
    if not callback_secret or not callback_secret.strip():
        return None
    key = _try_decode_base64url(callback_secret)
    if key is None or len(key) != 32:
        return None
    digest = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return "sha256=" + digest


def _try_decode_base64url(value: str) -> bytes | None:
    try:
        normalized = value.strip().replace("-", "+").replace("_", "/")
        pad = (4 - len(normalized) % 4) % 4
        normalized += "=" * pad
        return base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError):
        return None


def _replace_node(node: DagNode, *, action: str, input_mapping: Any) -> DagNode:
    import dataclasses
    return dataclasses.replace(node, action=action, input_mapping=input_mapping)
