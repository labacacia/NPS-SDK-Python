# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""NIP 0.15 certificate-renewal and revocation-freshness policy."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping


def evaluate_renewal(input_: Mapping[str, Any]) -> dict[str, Any]:
    if input_.get("profile") == "standard":
        open_ = _time(input_["not_after"]) - _time(input_["now"]) <= timedelta(days=7)
        return {"renewal_open": open_, "error": None if open_ else "NIP-CA-RENEWAL-TOO-EARLY"}
    if input_.get("profile") == "short-lived-edge":
        window = int(input_["original_validity_seconds"]) // 4
        return {"renewal_open": int(input_["remaining_seconds"]) <= window, "window_seconds": window}
    if "current" in input_:
        current, requested = input_["current"], input_["requested"]
        allowed = set(requested.get("capabilities", ())).issubset(current.get("capabilities", ())) and set(requested.get("scope", ())).issubset(current.get("scope", ()))
        return ({"issued": True} if allowed else {"issued": False, "error": "NIP-CA-SCOPE-EXPANSION-DENIED"})
    if "recorded" in input_:
        recorded = input_["recorded"]
        if recorded.get("committed") and recorded.get("canonical_digest") == input_.get("canonical_digest"):
            return {"serial": recorded["serial"], "new_issue_count": 0}
        return {"error": "NIP-CA-SERIAL-DUPLICATE", "new_issue_count": 0}
    if "old_ticket_not_after" in input_:
        return {"old_ticket_not_after": input_["old_ticket_not_after"]}
    raise ValueError("unknown NIP renewal input")


def evaluate_revocation(input_: Mapping[str, Any]) -> dict[str, Any]:
    if "cached" in input_:
        replace = bool(input_["incoming"].get("signature_valid")) and _time(input_["incoming"]["this_update"]) > _time(input_["cached"]["this_update"])
        return {"cache_replaced": replace, "effective_outcome": input_["incoming" if replace else "cached"]["outcome"]}
    now = _time(input_["now"]) if "now" in input_ else None
    consulted: list[str] = []
    diagnostics: list[str] = []
    for source in input_.get("sources", ()):
        name, outcome = source["source"], source["outcome"]
        consulted.append(name)
        if outcome == "unknown":
            return {"valid": False, "error": "NIP-OCSP-UNKNOWN"}
        stale = now is not None and "next_update" in source and now >= _time(source["next_update"])
        if stale:
            diagnostics.append(f"{name}_stale")
            continue
        if outcome == "revoked":
            return {"valid": False, "error": "NIP-CERT-REVOKED"}
        if outcome == "good":
            result: dict[str, Any] = {"valid": True, "consulted_sources": consulted}
            if diagnostics:
                result["diagnostics"] = diagnostics
            return result
    if input_.get("revocation_mode") == "required":
        return {"valid": False, "error": "NIP-REVOCATION-STATE-STALE"}
    return {"valid": True, "consulted_sources": consulted}


def evaluate_phase3_advisory(input_: Mapping[str, Any]) -> dict[str, Any]:
    ident, extensions = input_["ident"], input_["certificate_extensions"]
    findings: list[dict[str, str]] = []
    if ident.get("assurance_level") != extensions.get("assurance_level"):
        findings.append({"field": "assurance_level", "error": "NIP-ASSURANCE-MISMATCH"})
    if not set(ident.get("capabilities", ())).issubset(extensions.get("capabilities", ())):
        findings.append({"field": "capabilities", "error": "NIP-CERT-CAPABILITIES-EXCEEDED"})
    if not set(ident.get("node_roles", ())).issubset(extensions.get("node_roles", ())):
        findings.append({"field": "node_roles", "error": "NIP-CERT-NODE-ROLES-MISMATCH"})
    if ident.get("ocsp_staple") is None:
        findings.append({"field": "ocsp_staple", "error": "NIP-OCSP-STAPLE-EXPIRED"})
    findings.sort(key=lambda item: item["field"])
    return {"accepted_current_request": not bool(input_.get("phase3_enforcement")), "findings": findings, "state_mutated": False}


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
