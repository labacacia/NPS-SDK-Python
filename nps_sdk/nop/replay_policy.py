# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""NOP 0.10 bounded replay, retention, and aggregation policy."""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping


def evaluate_replay_retention(input_: Mapping[str, Any]) -> dict[str, Any]:
    if "recorded" in input_:
        record = input_["recorded"]
        if record["digest"] == input_["digest"]:
            return {"state": record["state"], "dispatch_count": int(record["dispatch_count"]), "replayed": True}
    if "recorded_digest" in input_:
        return ({"accepted": False, "error": "NOP-REPLAY-CONFLICT", "record_mutated": False}
                if input_["digest"] != input_["recorded_digest"] else {"accepted": True})
    if "incoming" in input_:
        key = (input_["incoming"]["caller_nid"], input_["incoming"]["task_id"])
        existing = {(record["caller_nid"], record["task_id"]) for record in input_["records"]}
        return {"new_key": key not in existing, "accepted": key not in existing}
    if "terminal_commit_ms" in input_:
        deadline = int(input_["terminal_commit_ms"]) + 1000 * int(input_["result_ttl_seconds"])
        return ({"result": None, "error": "NOP-TASK-RESULT-EXPIRED"}
                if int(input_["query_at_ms"]) >= deadline else {"result": "retained"})
    if "result_expired_at_ms" in input_:
        retained = int(input_["duplicate_at_ms"]) < int(input_["result_expired_at_ms"]) + 1000 * int(input_["replay_tombstone_seconds"])
        return ({"dispatch": False, "error": "NOP-TASK-RESULT-EXPIRED", "tombstone_retained": True}
                if retained else {"dispatch": True, "tombstone_retained": False})
    if "capacity" in input_:
        safe = [record for record in input_["records"] if record.get("state") != "running"]
        if len(input_["records"]) >= int(input_["capacity"]) and not safe:
            return {"accepted": False, "evicted": [], "error": "NOP-REPLAY-LIMIT"}
        return {"accepted": True, "evicted": [safe[0]["key"]] if safe else []}
    if "committed" in input_:
        return {"state": input_["committed"]["state"], "late_event": "audit_only", "ttl_extended": False}
    if "min_required" in input_:
        results = input_["results"]
        if any(not isinstance(item.get("score"), (int, float)) or not math.isfinite(float(item["score"])) for item in results):
            return {"error": "NOP-AGGREGATION-INVALID"}
        selected = sorted(results, key=lambda item: (-float(item["score"]), str(item["node_id"])))[:int(input_["min_required"])]
        return {"selected_node_ids": [item["node_id"] for item in selected]}
    if "topology_order" in input_:
        by_id = {item["node_id"]: item for item in input_["results"]}
        aggregate: dict[str, Any] = {}
        for node_id in input_["topology_order"]:
            item = by_id.get(node_id)
            if not item or item.get("state") != "completed":
                continue
            for key, value in item.get("value", {}).items():
                if isinstance(aggregate.get(key), list) and isinstance(value, list):
                    aggregate[key] = [*aggregate[key], *copy.deepcopy(value)]
                else:
                    aggregate[key] = copy.deepcopy(value)
        return {"aggregated": aggregate, "inputs_mutated": False}
    raise ValueError("unknown NOP replay input")
