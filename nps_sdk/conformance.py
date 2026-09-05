# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS conformance catalog, run manifest, and validation helpers."""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
from typing import Any

NODE_L1 = "NPS-Node-L1"
NODE_L2 = "NPS-Node-L2"


@dataclasses.dataclass(frozen=True)
class NpsConformanceCase:
    id: str
    profile: str
    requirement: str
    title: str
    optional: bool = False


@dataclasses.dataclass(frozen=True)
class NpsConformanceCaseResult:
    id: str
    result: str
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "result": self.result}
        if self.message is not None:
            d["message"] = self.message
        return d


@dataclasses.dataclass(frozen=True)
class NpsConformanceActor:
    name: str
    version: str
    nid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.nid is not None:
            d["nid"] = self.nid
        return d


@dataclasses.dataclass(frozen=True)
class NpsConformanceManifest:
    profile: str
    profile_version: str
    iut: NpsConformanceActor
    peer: NpsConformanceActor
    run: dict[str, str]
    cases: tuple[NpsConformanceCaseResult, ...]
    summary: dict[str, int]

    @classmethod
    def create(
        cls,
        profile: str,
        iut_name: str,
        iut_version: str,
        iut_nid: str,
        peer_name: str,
        peer_version: str,
        results: list[NpsConformanceCaseResult] | tuple[NpsConformanceCaseResult, ...],
        *,
        environment: str = "unspecified",
    ) -> "NpsConformanceManifest":
        cases = tuple(results)
        return cls(
            profile=profile,
            profile_version="0.7" if profile == NODE_L2 else "0.1",
            iut=NpsConformanceActor(iut_name, iut_version, iut_nid),
            peer=NpsConformanceActor(peer_name, peer_version),
            run={"date": _dt.datetime.now(_dt.UTC).isoformat(), "environment": environment},
            cases=cases,
            summary={
                "pass": sum(1 for c in cases if c.result == "pass"),
                "fail": sum(1 for c in cases if c.result == "fail"),
                "skip": sum(1 for c in cases if c.result == "skip"),
                "na": sum(1 for c in cases if c.result == "na"),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "profile_version": self.profile_version,
            "iut": self.iut.to_dict(),
            "peer": self.peer.to_dict(),
            "run": self.run,
            "cases": [c.to_dict() for c in self.cases],
            "summary": self.summary,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, separators=(",", ": "))


def catalog_for_profile(profile: str) -> tuple[NpsConformanceCase, ...]:
    if profile == NODE_L1:
        return NODE_L1_CASES
    if profile == NODE_L2:
        return NODE_L2_CASES
    raise ValueError(f"Unknown NPS conformance profile: {profile}")


@dataclasses.dataclass(frozen=True)
class NpsConformanceValidation:
    valid: bool
    message: str


def validate_manifest(manifest: NpsConformanceManifest) -> NpsConformanceValidation:
    catalog = catalog_for_profile(manifest.profile)
    known = {case.id: case for case in catalog}
    seen: set[str] = set()
    valid_results = {"pass", "fail", "skip", "na"}

    for result in manifest.cases:
        if result.id not in known:
            return NpsConformanceValidation(False, f"Unknown conformance case id '{result.id}'.")
        if result.id in seen:
            return NpsConformanceValidation(False, f"Duplicate conformance case id '{result.id}'.")
        seen.add(result.id)
        if result.result not in valid_results:
            return NpsConformanceValidation(False, f"Case '{result.id}' has invalid result '{result.result}'.")
        if result.result == "na" and not known[result.id].optional:
            return NpsConformanceValidation(False, f"Case '{result.id}' is required and cannot be marked na.")
        if (
            result.result == "na"
            and result.id in {"TC-N2-AaaS-06", "TC-N2-AaaS-07"}
            and not (result.message or "").strip()
        ):
            return NpsConformanceValidation(
                False,
                f"Case '{result.id}' requires a non-empty message for a SHOULD exception.",
            )

    missing = [case.id for case in catalog if case.id not in seen]
    if missing:
        return NpsConformanceValidation(False, f"Missing conformance case results: {', '.join(missing)}.")
    if any(case.result in {"fail", "skip"} for case in manifest.cases):
        return NpsConformanceValidation(False, "Conformance manifest contains fail or skip results.")
    expected_version = "0.7" if manifest.profile == NODE_L2 else "0.1"
    if manifest.profile_version != expected_version:
        return NpsConformanceValidation(
            False,
            f"Profile '{manifest.profile}' requires manifest version '{expected_version}'.",
        )
    expected_summary = {
        "pass": sum(1 for case in manifest.cases if case.result == "pass"),
        "fail": sum(1 for case in manifest.cases if case.result == "fail"),
        "skip": sum(1 for case in manifest.cases if case.result == "skip"),
        "na": sum(1 for case in manifest.cases if case.result == "na"),
    }
    if manifest.summary != expected_summary:
        return NpsConformanceValidation(False, "Conformance manifest summary does not match case results.")
    if manifest.profile == NODE_L2:
        results = {case.id: case.result for case in manifest.cases}
        families = (
            ("TC-N2-Tls-01", "TC-N2-Tls-02", "TC-N2-Tls-03", "TC-N2-Tls-04"),
            tuple(f"TC-N2-BridgeIn-0{i}" for i in range(1, 7)),
            tuple(f"TC-N2-HA-0{i}" for i in range(1, 7)),
            ("TC-N2-HA-07", "TC-N2-HA-08"),
        )
        for family in families:
            family_results = {results[case_id] for case_id in family}
            if len(family_results) != 1 or not family_results <= {"pass", "na"}:
                return NpsConformanceValidation(
                    False,
                    f"L2 case family '{family[0]}' must be all pass or all na.",
                )
        if (results["TC-N2-HA-01"] == "na") == (results["TC-N2-HA-09"] == "na"):
            return NpsConformanceValidation(
                False,
                "L2 multi-Anchor HA and single-Anchor compatibility cases must have opposite applicability.",
            )
    return NpsConformanceValidation(True, "Conformance manifest is valid.")


def _c(id_: str, profile: str, requirement: str, title: str, optional: bool = False) -> NpsConformanceCase:
    return NpsConformanceCase(id_, profile, requirement, title, optional)


NODE_L1_CASES: tuple[NpsConformanceCase, ...] = (
    _c("TC-N1-NCP-01", NODE_L1, "N1-NCP-01", "Tier-1 JSON frame round-trip"),
    _c("TC-N1-NCP-02", NODE_L1, "N1-NCP-02", "Hello + Anchor handshake"),
    _c("TC-N1-NCP-03", NODE_L1, "N1-NCP-03", "Loopback listener default"),
    _c("TC-N1-NCP-04", NODE_L1, "N1-NCP-04", "Tier-2 negotiation hygiene"),
    _c("TC-N1-NIP-01", NODE_L1, "N1-NIP-01", "Root keypair generation and permission"),
    _c("TC-N1-NIP-02", NODE_L1, "N1-NIP-02", "IdentFrame sign and verify"),
    _c("TC-N1-NIP-03", NODE_L1, "N1-NIP-03", "NID format"),
    _c("TC-N1-NIP-04", NODE_L1, "N1-NIP-04", "Sub-NID issuance", True),
    _c("TC-N1-NDP-01", NODE_L1, "N1-NDP-01", "AnnounceFrame carries activation_mode"),
    _c("TC-N1-NDP-02", NODE_L1, "N1-NDP-02", "AnnounceFrame signature"),
    _c("TC-N1-NDP-03", NODE_L1, "N1-NDP-03", "ResolveFrame response"),
    _c("TC-N1-NDP-04", NODE_L1, "N1-NDP-04", "GraphFrame topology snapshot", True),
    _c("TC-N1-NWP-01", NODE_L1, "N1-NWP-01", "Inbox accepts ActionFrame"),
    _c("TC-N1-NWP-02", NODE_L1, "N1-NWP-02", "Inbox persists across restart"),
    _c("TC-N1-NWP-03", NODE_L1, "N1-NWP-03", "NWP pull serves inbox"),
    _c("TC-N1-NWP-04", NODE_L1, "N1-NWP-04", "100 QPS baseline"),
    _c("TC-N1-NWP-05", NODE_L1, "N1-NWP-05", "Push path", True),
    _c("TC-N1-OBS-01", NODE_L1, "N1-OBS-01", "Frame log entry per direction"),
    _c("TC-N1-OBS-02", NODE_L1, "N1-OBS-02", "Log entry fields"),
    _c("TC-N1-OBS-03", NODE_L1, "N1-OBS-03", "Log destination flexibility"),
)

NODE_L2_CASES: tuple[NpsConformanceCase, ...] = (
    _c("TC-N2-AaaS-01", NODE_L2, "L2-01", "Internal work uses NOP TaskFrame"),
    _c("TC-N2-AaaS-02", NODE_L2, "L2-02", "OpenTelemetry TaskFrame context injection"),
    _c("TC-N2-AaaS-03", NODE_L2, "L2-03", "CGN-Estimate budget and token_est response"),
    _c("TC-N2-AaaS-04", NODE_L2, "L2-04", "NOP preflight gates worker dispatch"),
    _c("TC-N2-AaaS-05", NODE_L2, "L2-05", "NOP retry and timeout semantics"),
    _c("TC-N2-AaaS-06", NODE_L2, "L2-06", "Asynchronous Action lifecycle", True),
    _c("TC-N2-AaaS-07", NODE_L2, "L2-07", "AlignStream CGN back-pressure", True),
    _c("TC-N2-AnchorTopo-01", NODE_L2, "L2-08", "Snapshot of a 3-member cluster"),
    _c("TC-N2-AnchorTopo-02", NODE_L2, "L2-08", "Version monotonicity across joins"),
    _c("TC-N2-AnchorTopo-03", NODE_L2, "L2-08", "Sub-Anchor member surfaces"),
    _c("TC-N2-AnchorStream-01", NODE_L2, "L2-08", "member_joined on NDP Announce"),
    _c("TC-N2-AnchorStream-02", NODE_L2, "L2-08", "member_left on NDP TTL expiry"),
    _c("TC-N2-AnchorStream-03", NODE_L2, "L2-08", "Resume from topology.since_version"),
    _c("TC-N2-AnchorTopo-04", NODE_L2, "L2-08", "Unauthorized topology access"),
    _c("TC-N2-AnchorTopo-05", NODE_L2, "L2-08", "Depth cap exceeded"),
    _c("TC-N2-AnchorTopo-06", NODE_L2, "L2-08", "Unsupported topology scope"),
    _c("TC-N2-AnchorTopo-07", NODE_L2, "L2-08", "Unsupported topology filter"),
    _c("TC-N2-AnchorTopo-08", NODE_L2, "L2-08", "Unsupported reserved topology type"),
    _c("TC-N2-AnchorStream-04", NODE_L2, "L2-08", "resync_required when version is too old"),
    _c("TC-N2-Tls-01", NODE_L2, "NPS-RFC-0006", "ALPN nps/1.0 negotiated over TLS 1.3", True),
    _c("TC-N2-Tls-02", NODE_L2, "NPS-RFC-0006", "Mutual TLS required", True),
    _c("TC-N2-Tls-03", NODE_L2, "NPS-RFC-0006", "Client cert trust anchor and NID binding", True),
    _c("TC-N2-Tls-04", NODE_L2, "NPS-RFC-0006", "IdentFrame/certificate NID mismatch", True),
    _c("TC-N2-BridgeIn-01", NODE_L2, "NPS-CR-0010", "MCP inbound required method set", True),
    _c("TC-N2-BridgeIn-02", NODE_L2, "NPS-CR-0010", "gRPC inbound round-trip", True),
    _c("TC-N2-BridgeIn-03", NODE_L2, "NPS-CR-0010", "A2A inbound round-trip", True),
    _c("TC-N2-BridgeIn-04", NODE_L2, "NPS-CR-0010", "Bare action resolution and ambiguity rejection", True),
    _c("TC-N2-BridgeIn-05", NODE_L2, "NPS-CR-0010", "Foreign-protocol error mapping", True),
    _c("TC-N2-BridgeIn-06", NODE_L2, "NPS-CR-0010", "Undeclared protocol or direction refusal", True),
    _c("TC-N2-HA-01", NODE_L2, "NPS-CR-0009", "cluster_epoch on topology read surfaces", True),
    _c("TC-N2-HA-02", NODE_L2, "NPS-CR-0009", "Planned anchor_failover wire shape", True),
    _c("TC-N2-HA-03", NODE_L2, "NPS-CR-0009", "Active-loss failover is terminal", True),
    _c("TC-N2-HA-04", NODE_L2, "NPS-CR-0009", "Quorum-loss wire shape and read-only mode", True),
    _c("TC-N2-HA-05", NODE_L2, "NPS-CR-0009", "Standby rejects topology writes", True),
    _c("TC-N2-HA-06", NODE_L2, "NPS-CR-0009", "Superseded leader is epoch fenced", True),
    _c("TC-N2-HA-07", NODE_L2, "NPS-CR-0009", "Registry resolves highest cluster_epoch", True),
    _c("TC-N2-HA-08", NODE_L2, "NPS-CR-0009", "Equal-epoch split-brain rejection", True),
    _c("TC-N2-HA-09", NODE_L2, "NPS-CR-0009", "Single-Anchor epoch-one compatibility", True),
)
