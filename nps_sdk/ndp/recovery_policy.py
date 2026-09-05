# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""NDP 0.13 durable recovery-fence decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def evaluate_recovery(input_: Mapping[str, Any]) -> dict[str, Any]:
    if "commit" in input_:
        old, incoming = int(input_["persisted_seq"]), int(input_["incoming_seq"])
        if input_["commit"] != "success":
            return {"acknowledged": False, "served_seq": old, "persisted_seq": old, "error": "NDP-STATE-UNAVAILABLE"}
        return {"acknowledged": True, "served_seq": incoming, "persisted_seq": incoming}
    if "record" in input_ and "now" in input_:
        record = input_["record"]
        return {"live_entry": _time(record["fresh_until"]) > _time(input_["now"]), "highest_seq": int(record["highest_seq"]), "ready": True}
    if "restored_highest_seq" in input_:
        high, incoming = int(input_["restored_highest_seq"]), int(input_["incoming_seq"])
        return ({"accepted": False, "highest_seq": high, "error": "NDP-GRAPH-SEQ-ROLLBACK"}
                if incoming < high else {"accepted": True, "highest_seq": incoming})
    if "owners" in input_:
        live = [owner for owner in input_["owners"] if owner.get("live")]
        top = max(int(owner["epoch"]) for owner in live)
        leaders = sorted(owner["nid"] for owner in live if int(owner["epoch"]) == top)
        return ({"resolved_nid": leaders[0]} if len(leaders) == 1
                else {"resolved_nid": None, "error": "NDP-CLUSTER-SPLIT"})
    if "snapshot_validation" in input_:
        return ({"ready": True, "started_empty": False} if input_["snapshot_validation"] == "valid"
                else {"ready": False, "started_empty": False, "error": "NDP-STATE-CORRUPT"})
    if "profiles" in input_:
        return {"recovery": ["volatile" if profile == "local-dev" else "durable" for profile in input_["profiles"]]}
    if "revoked_origin" in input_:
        record = input_["record"]
        return {"live": bool(record.get("live")) and record.get("origin") != input_["revoked_origin"], "highest_seq": int(record["highest_seq"])}
    raise ValueError("unknown NDP recovery input")


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
