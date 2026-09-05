# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""NWP 0.22 manifest normalization and renewable-subscription policy."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

_PRICE = re.compile(r"^[A-Z]{3} [0-9]+(?:\.[0-9]+)?$")
_TIER_RANK = {"basic": 0, "standard": 1, "premium": 2}


def normalize_manifest_metadata(input_: Mapping[str, Any]) -> dict[str, Any]:
    if "stability" in input_:
        raw = input_.get("stability")
        if raw is None:
            return {"normalized": "stable", "diagnostics": []}
        if raw in {"experimental", "stable", "deprecated"}:
            return {"raw": raw, "normalized": raw, "rank_as_stable": raw == "stable"}
        return {"raw": raw, "normalized": "experimental", "rank_as_stable": False}
    if "sla" in input_:
        normalized, diagnostics = _normalize_sla(input_["sla"])
        return {"manifest_valid": True, "normalized_sla": normalized, "diagnostics": diagnostics}
    if "billing" in input_:
        normalized, diagnostics = _normalize_billing(input_["billing"])
        return {"normalized_billing": normalized, "diagnostics": diagnostics}
    if "top_level" in input_:
        base, _ = _normalize_sla(input_.get("top_level", {}).get("sla", {}))
        override, diagnostics = _normalize_sla(input_.get("action", {}).get("sla", {}), "action.sla.")
        return {"effective_sla": {**base, **override}, "diagnostics": diagnostics}
    raise ValueError("unknown NWP metadata input")


def evaluate_subscription_lease(input_: Mapping[str, Any]) -> dict[str, Any]:
    if "policy" in input_:
        policy, request = input_["policy"], input_["request"]
        default, maximum, renew_before = (int(policy[k]) for k in
                                           ("default_lease_seconds", "max_lease_seconds", "renew_before_seconds"))
        if default <= 0 or maximum <= 0 or default > maximum or renew_before >= maximum:
            return {"accepted": False, "error": "NWP-SUBSCRIBE-LEASE-INVALID", "state_mutated": False}
        requested = int(request.get("lease_seconds", default))
        if requested <= 0:
            return {"accepted": False, "error": "NWP-SUBSCRIBE-LEASE-INVALID", "state_mutated": False}
        lease = min(requested, maximum)
        result = {"lease_seconds": lease, "expires_at": _format_time(_parse_time(input_["accepted_at"]) + timedelta(seconds=lease))}
        if "lease_seconds" not in request:
            result["status"] = "open"
        return result
    if "owner_nid" in input_:
        if input_["owner_nid"] != input_["caller_nid"]:
            return {"accepted": False, "error": "NWP-AUTH-NID-SCOPE-VIOLATION", "state_disclosed": False}
        return {"accepted": True}
    if "prior_seq" in input_:
        return {"expires_at": _format_time(_parse_time(input_["accepted_at"]) + timedelta(seconds=int(input_["lease_seconds"]))),
                "seq": int(input_["prior_seq"]), "cursor": input_["prior_cursor"]}
    if "expires_at" in input_:
        if _parse_time(input_["now"]) >= _parse_time(input_["expires_at"]):
            return {"accepted": False, "status": "closed", "error": "NWP-SUBSCRIBE-LEASE-EXPIRED", "terminal_event_count": 1}
        return {"accepted": True}
    if input_.get("operation") in {"renew", "close"} and any(k in input_ for k in ("anchor_ref", "filter", "type")):
        return {"accepted": False, "error": "NWP-SUBSCRIBE-LEASE-INVALID", "state_mutated": False}
    raise ValueError("unknown NWP subscription input")


def _normalize_sla(sla: Mapping[str, Any], prefix: str = "") -> tuple[dict[str, Any], list[str]]:
    out: dict[str, Any] = {}
    diagnostics: list[str] = []
    if "p95_latency_ms" in sla:
        value = sla["p95_latency_ms"]
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 0xFFFFFFFF:
            out["p95_latency_ms"] = value
        else:
            diagnostics.append(prefix + "p95_latency_ms")
    if "availability" in sla:
        try:
            value = Decimal(str(sla["availability"]))
        except InvalidOperation:
            value = Decimal(0)
        if 0 < value <= 1:
            out["availability"] = str(sla["availability"])
        else:
            diagnostics.append(prefix + "availability")
    if "sla_tier" in sla:
        tier = str(sla["sla_tier"])
        if tier in _TIER_RANK:
            out["sla_tier"] = tier
            out["sla_tier_rank"] = _TIER_RANK[tier]
        else:
            out["sla_tier_raw"] = tier
            out["sla_tier_rank"] = None
    return out, diagnostics


def _normalize_billing(billing: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    raw_profile = str(billing.get("metering_profile", "metered"))
    profile = raw_profile if raw_profile in {"free", "metered"} else "metered"
    out: dict[str, Any] = {"metering_profile": profile}
    diagnostics: list[str] = []
    if profile == "free":
        diagnostics.extend(k for k in ("billing_unit", "price_hint", "currency") if k in billing)
        return out, diagnostics
    unit = billing.get("billing_unit")
    if isinstance(unit, str) and unit:
        out["billing_unit"] = unit
    else:
        diagnostics.append("billing_unit")
    price = billing.get("price_hint")
    currency = billing.get("currency")
    if isinstance(price, str) and _PRICE.fullmatch(price):
        prefix = price[:3]
        if currency is not None and currency != prefix:
            diagnostics.append("currency")
        else:
            out["price_hint"] = price
            if currency is not None:
                out["currency"] = currency
    elif price is not None:
        diagnostics.append("price_hint")
    return out, diagnostics


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
