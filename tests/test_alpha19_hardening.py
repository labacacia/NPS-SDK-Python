# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

from nps_sdk.ncp.runtime_hardening import evaluate_runtime_hardening
from nps_sdk.ndp.recovery_policy import evaluate_recovery
from nps_sdk.nip.alpha19_policy import evaluate_phase3_advisory, evaluate_renewal, evaluate_revocation
from nps_sdk.nop.replay_policy import evaluate_replay_retention
from nps_sdk.nwp.alpha19_policy import evaluate_subscription_lease, normalize_manifest_metadata


def _vectors(protocol: str, name: str) -> list[dict]:
    relative = Path("spec") / "conformance" / protocol / name
    for root in Path(__file__).resolve().parents:
        candidate = root / relative
        if candidate.is_file():
            return json.loads(candidate.read_text())["vectors"]
    raise FileNotFoundError(relative)


def test_alpha19_shared_vectors_are_executable() -> None:
    suites = [
        ("ncp", "runtime_hardening_vectors.json", lambda vector: evaluate_runtime_hardening(vector["input"])),
        ("nwp", "alpha19_hardening_vectors.json", _nwp),
        ("nip", "renewal_revocation_vectors.json", _nip),
        ("ndp", "recovery_fence_vectors.json", lambda vector: evaluate_recovery(vector["input"])),
        ("nop", "replay_retention_vectors.json", lambda vector: evaluate_replay_retention(vector["input"])),
    ]
    seen: set[str] = set()
    for protocol, name, evaluate in suites:
        for vector in _vectors(protocol, name):
            assert vector["id"] not in seen
            seen.add(vector["id"])
            assert evaluate(vector) == vector["expected"], vector["id"]
    assert len(seen) == 47


def _nwp(vector: dict) -> dict:
    return (normalize_manifest_metadata(vector["input"])
            if ".metadata." in vector["id"] else evaluate_subscription_lease(vector["input"]))


def _nip(vector: dict) -> dict:
    if ".renewal." in vector["id"]:
        return evaluate_renewal(vector["input"])
    if ".revocation." in vector["id"]:
        return evaluate_revocation(vector["input"])
    return evaluate_phase3_advisory(vector["input"])
