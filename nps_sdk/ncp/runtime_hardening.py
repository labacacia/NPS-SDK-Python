# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""NCP 0.12 deterministic connection-hardening policy."""

from __future__ import annotations

from typing import Any, Mapping


def evaluate_runtime_hardening(input_: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one portable NCP connection decision without carrier I/O."""
    if "client_ping_ms" in input_:
        offers = [int(input_[key]) for key in ("client_ping_ms", "server_ping_ms")
                  if int(input_[key]) > 0]
        return {"keepalive_enabled": bool(offers),
                "effective_interval_ms": max(1000, min(offers)) if offers else None}
    if "events" in input_:
        clock = int(input_["last_valid_inbound_ms"])
        for event in input_["events"]:
            if event["event"] == "valid_inbound_frame":
                clock = int(event["at_ms"])
        return {"last_valid_inbound_ms": clock}
    if "queued_probe_count" in input_:
        queued = int(input_["queued_probe_count"])
        due = int(input_["evaluate_at_ms"]) - int(input_["last_application_send_ms"]) >= int(input_["effective_interval_ms"])
        result: dict[str, Any] = {"queued_probe_count": queued}
        if due and queued == 0:
            result = {"enqueue": {"frame": "0x07", "payload_length": 0}, "queued_probe_count": 1}
        return result
    if "active_streams" in input_:
        deadline = int(input_["last_valid_inbound_ms"]) + 3 * int(input_["effective_interval_ms"])
        if int(input_["evaluate_at_ms"]) >= deadline:
            return {"state": "closing", "error": "NCP-KEEPALIVE-TIMEOUT", "error_count": 1,
                    "cancelled_streams": int(input_["active_streams"]),
                    "close_by_ms": int(input_["evaluate_at_ms"]) + 500,
                    "allow_later_application_frames": False}
        return {"state": "open"}
    if "payload_length" in input_:
        if int(input_["payload_length"]) != 0:
            return {"accepted": False, "error": "NCP-FRAME-PAYLOAD-TOO-LARGE",
                    "last_valid_inbound_ms": int(input_["last_valid_inbound_ms"])}
        return {"accepted": True, "last_valid_inbound_ms": int(input_["received_at_ms"])}
    if "early_data" in input_:
        rejected = input_.get("carrier") == "quic" and not bool(input_.get("handshake_confirmed"))
        return ({"accepted": False, "error": "NCP-EARLY-DATA-REJECTED", "retry_after_confirmation": True}
                if rejected else {"accepted": True})
    if "bound_nid" in input_:
        if not input_.get("handshake_confirmed") or input_["bound_nid"] != input_["migrated_nid"]:
            return {"migration_allowed": False, "session_preserved": False, "error": "NCP-NID-MISMATCH"}
        send_allowed = int(input_["carrier_credit_bytes"]) > 0 and int(input_["ncp_window_cgn"]) > 0
        result = {"migration_allowed": True, "session_preserved": True, "send_allowed": send_allowed}
        if not send_allowed:
            result["reason"] = "ncp_window_exhausted" if int(input_["ncp_window_cgn"]) <= 0 else "carrier_credit_exhausted"
        return result
    raise ValueError("unknown NCP 0.12 hardening input")
