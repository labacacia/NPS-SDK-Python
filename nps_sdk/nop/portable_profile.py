# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Transport-independent NOP 0.9 orchestration and runtime profile."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from nps_sdk.nop.condition import NopConditionError, evaluate
from nps_sdk.nop.error_codes import (
    NOP_CALLBACK_HMAC_INVALID,
    NOP_CALLBACK_HMAC_MISSING,
    NOP_CALLBACK_INVALID,
    NOP_COMPENSATION_FAILED,
    NOP_COMPENSATION_NOT_SUPPORTED,
    NOP_CONDITION_EVAL_ERROR,
    NOP_DELEGATE_REJECTED,
    NOP_DELEGATE_SCOPE_VIOLATION,
    NOP_DELEGATE_TIMEOUT,
    NOP_INPUT_MAPPING_ERROR,
    NOP_RESOURCE_INSUFFICIENT,
    NOP_RUNTIME_IDLE_TIMEOUT,
    NOP_RUNTIME_MAX_RUNTIME,
    NOP_SPAWN_SPEC_INVALID,
    NOP_TASK_CANCELLED,
    NOP_TASK_DAG_CYCLE,
)
from nps_sdk.nop.input_mapper import NopMappingError, resolve

_CLUSTER_SPLIT = "NDP-CLUSTER-SPLIT"


def evaluate_orchestration(task: Mapping[str, Any]) -> dict[str, Any]:
    """Run one shared deterministic orchestration transcript."""
    nodes: dict[str, Mapping[str, Any]] = {}
    for raw in task["nodes"]:
        node_id = str(raw["id"])
        if node_id in nodes:
            return _empty_failure("NOP-TASK-DAG-INVALID")
        nodes[node_id] = raw
    topo = _stable_topology(nodes)
    if topo is None:
        return _empty_failure(NOP_TASK_DAG_CYCLE)

    events: list[str] = []
    if task.get("preflight", False):
        events.append("task:preflight")
        if any(not nodes[node_id].get("preflight_available", True) for node_id in topo):
            events.append("task:failed")
            return _result(
                events, "failed", NOP_RESOURCE_INSUFFICIENT, None, {}, {}, {}, []
            )

    events.append("task:running")
    results: dict[str, Any] = {}
    states: dict[str, str] = {}
    attempt_counts: dict[str, int] = {}
    mapped_params: dict[str, Any] = {}
    task_retries = int(task.get("max_retries", 0))

    for node_id in topo:
        node = nodes[node_id]
        if task.get("cancel_before") == node_id:
            events.append("task:cancelled")
            return _result(
                events,
                "cancelled",
                NOP_TASK_CANCELLED,
                None,
                states,
                attempt_counts,
                mapped_params,
                [],
            )

        condition = node.get("condition")
        if condition is not None:
            try:
                condition_value = evaluate(str(condition), results)
            except (NopConditionError, NopMappingError):
                states[node_id] = "failed"
                attempt_counts[node_id] = 0
                events.extend((f"{node_id}:failed", "task:failed"))
                return _result(
                    events,
                    "failed",
                    NOP_CONDITION_EVAL_ERROR,
                    None,
                    states,
                    attempt_counts,
                    mapped_params,
                    [],
                )
            if not condition_value:
                states[node_id] = "skipped"
                attempt_counts[node_id] = 0
                events.append(f"{node_id}:skipped")
                continue

        mapping = node.get("input_mapping")
        if mapping:
            params: dict[str, Any] = {}
            try:
                for name, path in mapping.items():
                    value = resolve(str(path), results)
                    if value is None:
                        raise NopMappingError(f"Missing mapping path: {path}")
                    params[str(name)] = copy.deepcopy(value)
            except NopMappingError:
                states[node_id] = "failed"
                attempt_counts[node_id] = 0
                events.extend((f"{node_id}:failed", "task:failed"))
                return _result(
                    events,
                    "failed",
                    NOP_INPUT_MAPPING_ERROR,
                    None,
                    states,
                    attempt_counts,
                    mapped_params,
                    [],
                )
            mapped_params[node_id] = params

        max_retries = int(node.get("max_retries", task_retries))
        scripted = list(node["attempts"])
        final_error: str | None = None
        completed = False
        count = 0
        for index, outcome in enumerate(scripted[: max_retries + 1]):
            count += 1
            events.append(f"{node_id}:attempt:{count}")
            kind = outcome["kind"]
            if kind == "success":
                results[node_id] = copy.deepcopy(outcome.get("result", {}))
                states[node_id] = "completed"
                events.append(f"{node_id}:completed")
                completed = True
                break
            final_error = (
                NOP_DELEGATE_TIMEOUT
                if kind == "timeout"
                else str(outcome.get("error_code", NOP_DELEGATE_REJECTED))
            )
            retryable = kind == "timeout" or bool(outcome.get("retryable", False))
            retry_on = node.get("retry_on")
            selected = retry_on is None or final_error in retry_on
            if (
                retryable
                and selected
                and count <= max_retries
                and index + 1 < len(scripted)
            ):
                events.append(f"{node_id}:retrying")
                continue
            states[node_id] = "failed"
            events.append(f"{node_id}:failed")
            break

        attempt_counts[node_id] = count
        if completed:
            continue
        compensation_order, compensation_error = _compensate(
            task, node_id, topo, nodes, states, events
        )
        events.append("task:failed")
        return _result(
            events,
            "failed",
            compensation_error or final_error or NOP_DELEGATE_REJECTED,
            None,
            states,
            attempt_counts,
            mapped_params,
            compensation_order,
        )

    aggregate = _aggregate(task, topo, nodes, states, results)
    events.append("task:completed")
    return _result(
        events,
        "completed",
        None,
        aggregate,
        states,
        attempt_counts,
        mapped_params,
        [],
    )


def evaluate_runtime(category: str, input_: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one runtime/security vector category."""
    if category == "callback":
        return _evaluate_callback(input_)
    if category == "hmac":
        return _evaluate_hmac(input_)
    if category == "lease":
        return _evaluate_lease(input_)
    if category == "delegation":
        return _evaluate_delegation(input_)
    if category == "spawn_spec":
        return _evaluate_spawn_spec(input_)
    if category == "lifecycle":
        return _evaluate_lifecycle(input_)
    if category == "dedup_key":
        return {
            "value": compute_dedup_key(
                str(input_["task_id"]), str(input_["dag_hash"])
            )
        }
    raise ValueError(f"Unknown NOP profile category: {category}")


def compute_dedup_key(task_id: str, dag_hash: str) -> str:
    """Return SHA-256(task_id + NUL + dag_hash) as lowercase hex."""
    return hashlib.sha256(
        task_id.encode() + b"\0" + dag_hash.encode()
    ).hexdigest()


def _stable_topology(
    nodes: Mapping[str, Mapping[str, Any]],
) -> list[str] | None:
    indegree = {node_id: 0 for node_id in nodes}
    outgoing = {node_id: [] for node_id in nodes}
    for node_id, node in nodes.items():
        for dependency in node["depends_on"]:
            if dependency not in nodes:
                return None
            indegree[node_id] += 1
            outgoing[dependency].append(node_id)
    ready = sorted(node_id for node_id, value in indegree.items() if value == 0)
    order: list[str] = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for next_id in sorted(outgoing[node_id]):
            indegree[next_id] -= 1
            if indegree[next_id] == 0:
                ready.append(next_id)
                ready.sort()
    return order if len(order) == len(nodes) else None


def _compensate(
    task: Mapping[str, Any],
    failed_id: str,
    topo: list[str],
    nodes: Mapping[str, Mapping[str, Any]],
    states: dict[str, str],
    events: list[str],
) -> tuple[list[str], str | None]:
    policy = task.get("compensation_policy")
    if policy not in {"best_effort", "strict"}:
        return [], None
    ancestors: set[str] = set()

    def collect(node_id: str) -> None:
        for dependency in nodes[node_id]["depends_on"]:
            if dependency not in ancestors:
                ancestors.add(dependency)
                collect(dependency)

    collect(failed_id)
    candidates = [
        node_id
        for node_id in reversed(topo)
        if node_id in ancestors and states.get(node_id) == "completed"
    ]
    if policy == "strict" and any(
        "compensate_action" not in nodes[node_id] for node_id in candidates
    ):
        return [], NOP_COMPENSATION_NOT_SUPPORTED
    order: list[str] = []
    for node_id in candidates:
        node = nodes[node_id]
        if "compensate_action" not in node:
            continue
        order.append(node_id)
        events.append(f"{node_id}:compensating")
        if node.get("compensation_outcome") == "failure":
            states[node_id] = "compensation_failed"
            events.append(f"{node_id}:compensation_failed")
            if policy == "strict":
                return order, NOP_COMPENSATION_FAILED
        else:
            states[node_id] = "compensated"
            events.append(f"{node_id}:compensated")
    return order, None


def _aggregate(
    task: Mapping[str, Any],
    topo: list[str],
    nodes: Mapping[str, Mapping[str, Any]],
    states: Mapping[str, str],
    results: Mapping[str, Any],
) -> Any:
    has_outgoing = {
        dependency for node in nodes.values() for dependency in node["depends_on"]
    }
    values = [
        copy.deepcopy(results[node_id])
        for node_id in topo
        if node_id not in has_outgoing
        and states.get(node_id) == "completed"
        and node_id in results
    ]
    if not values:
        return None
    strategy = task.get("aggregate", "merge")
    if strategy == "all":
        return values
    merged: dict[str, Any] = {}
    for value in values:
        if not isinstance(value, Mapping):
            continue
        for key, item in value.items():
            if (
                strategy == "merge_all"
                and isinstance(merged.get(key), list)
                and isinstance(item, list)
            ):
                merged[key].extend(copy.deepcopy(item))
            else:
                merged[key] = copy.deepcopy(item)
    return merged


def _evaluate_callback(input_: Mapping[str, Any]) -> dict[str, Any]:
    allowed = _callback_destination_allowed(
        str(input_["url"]), input_["resolved_ips"]
    )
    if allowed and "redirect_url" in input_:
        allowed = _callback_destination_allowed(
            str(input_["redirect_url"]), input_["redirect_resolved_ips"]
        )
    return {
        "allowed": allowed,
        "error": None if allowed else NOP_CALLBACK_INVALID,
    }


def _callback_destination_allowed(url: str, addresses: Any) -> bool:
    parts = urlsplit(url)
    if (
        parts.scheme.lower() != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or not addresses
    ):
        return False
    try:
        return all(ipaddress.ip_address(value).is_global for value in addresses)
    except ValueError:
        return False


def _evaluate_hmac(input_: Mapping[str, Any]) -> dict[str, Any]:
    signature = input_.get("signature")
    if signature is None:
        return {"valid": False, "error": NOP_CALLBACK_HMAC_MISSING}
    try:
        padded = str(input_["secret_base64url"]) + "=" * (
            -len(str(input_["secret_base64url"])) % 4
        )
        key = base64.urlsafe_b64decode(padded)
        expected = "sha256=" + hmac.new(
            key, str(input_["raw_body"]).encode(), hashlib.sha256
        ).hexdigest()
        valid = len(key) == 32 and hmac.compare_digest(expected, str(signature))
    except (ValueError, TypeError):
        valid = False
    return {
        "valid": valid,
        "error": None if valid else NOP_CALLBACK_HMAC_INVALID,
    }


@dataclass
class _Lease:
    runner_nid: str
    expires_at: int


def _evaluate_lease(input_: Mapping[str, Any]) -> dict[str, Any]:
    leases: dict[str, _Lease] = {}
    terminal: set[str] = set()
    outcomes: list[str] = []
    for event in input_["events"]:
        at = int(event["at"])
        op = event["op"]
        if op == "claim":
            task_id = str(event["task_id"])
            runner = str(event["runner_nid"])
            seconds = min(600, max(10, int(event["lease_seconds"])))
            lease = leases.get(task_id)
            if lease is not None and lease.expires_at > at:
                if lease.runner_nid == runner:
                    leases[task_id] = _Lease(runner, at + seconds)
                    outcomes.append("granted")
                else:
                    outcomes.append("conflict")
            else:
                leases[task_id] = _Lease(runner, at + seconds)
                outcomes.append("reclaimed" if lease is not None else "granted")
        elif op == "renew":
            task_id = str(event["task_id"])
            runner = str(event["runner_nid"])
            seconds = min(600, max(10, int(event["lease_seconds"])))
            lease = leases.get(task_id)
            if (
                lease is not None
                and lease.expires_at > at
                and lease.runner_nid == runner
            ):
                leases[task_id] = _Lease(runner, at + seconds)
                outcomes.append("granted")
            else:
                outcomes.append("conflict")
        elif op == "mark_terminal":
            terminal.add(_terminal_key(event))
            outcomes.append("recorded")
        elif op == "is_terminal":
            outcomes.append(
                "terminal" if _terminal_key(event) in terminal else "pending"
            )
        else:
            raise ValueError(f"Unknown lease operation: {op}")
    return {"outcomes": outcomes}


def _terminal_key(event: Mapping[str, Any]) -> str:
    return f"{event['dedup_key']}\0{event['node_id']}"


def _evaluate_delegation(input_: Mapping[str, Any]) -> dict[str, Any]:
    parent = input_["parent_scope"]
    delegated = input_["delegated_scope"]
    if (
        not set(delegated["nodes"]).issubset(parent["nodes"])
        or not set(delegated["actions"]).issubset(parent["actions"])
        or int(delegated["max_token_budget"]) > int(parent["max_token_budget"])
    ):
        return {"targets": [], "error": NOP_DELEGATE_SCOPE_VIOLATION}
    targets: list[str] = []
    for attempt in input_["attempts"]:
        live = [candidate for candidate in attempt["candidates"] if candidate["live"]]
        if not live:
            return {"targets": targets, "error": NOP_DELEGATE_REJECTED}
        highest = max(int(candidate["cluster_epoch"]) for candidate in live)
        leaders = [
            candidate for candidate in live
            if int(candidate["cluster_epoch"]) == highest
        ]
        if len(leaders) != 1:
            return {"targets": targets, "error": _CLUSTER_SPLIT}
        targets.append(str(leaders[0]["nid"]))
    return {"targets": targets, "error": None}


def _evaluate_spawn_spec(input_: Mapping[str, Any]) -> dict[str, Any]:
    spec = input_["spawn_spec"]
    valid = bool(str(spec.get("image", "")).strip())
    if (
        valid
        and "idle_timeout_seconds" in spec
        and "max_runtime_seconds" in spec
        and int(spec["idle_timeout_seconds"]) > int(spec["max_runtime_seconds"])
    ):
        valid = False
    return {"error": None if valid else NOP_SPAWN_SPEC_INVALID}


def _evaluate_lifecycle(input_: Mapping[str, Any]) -> dict[str, Any]:
    if int(input_["elapsed_seconds"]) >= int(input_["max_runtime_seconds"]):
        return {"state": "failed", "error": NOP_RUNTIME_MAX_RUNTIME}
    if int(input_["idle_seconds"]) >= int(input_["idle_timeout_seconds"]):
        return {"state": "failed", "error": NOP_RUNTIME_IDLE_TIMEOUT}
    if input_.get("worker_terminal") == "done":
        return {"state": "completed", "error": None}
    return {"state": "failed", "error": NOP_DELEGATE_REJECTED}


def _empty_failure(error: str) -> dict[str, Any]:
    return _result(["task:failed"], "failed", error, None, {}, {}, {}, [])


def _result(
    events: list[str],
    state: str,
    error: str | None,
    aggregate: Any,
    states: Mapping[str, str],
    attempts: Mapping[str, int],
    mapped: Mapping[str, Any],
    compensation: list[str],
) -> dict[str, Any]:
    return {
        "events": list(events),
        "terminal_state": state,
        "error_code": error,
        "aggregate": copy.deepcopy(aggregate),
        "node_states": dict(sorted(states.items())),
        "attempt_counts": dict(sorted(attempts.items())),
        "mapped_params": copy.deepcopy(dict(sorted(mapped.items()))),
        "compensation_order": list(compensation),
    }
