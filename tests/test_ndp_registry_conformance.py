# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from nps_sdk.ndp.registry_profile import (
    NdpRegistryProfile,
    canonical_announce_json,
    verify_announce_signature,
)
from nps_sdk.ndp.frames import AnnounceFrame
from nps_sdk.ndp.validator import NdpAnnounceValidator

def _repo_file(relative: str) -> Path:
    for root in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Unable to locate repository file: {relative}")


def _load(relative: str) -> dict[str, Any]:
    return json.loads(_repo_file(relative).read_text(encoding="utf-8"))


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_shared_announce_canonicalization_vectors() -> None:
    suite = _load("spec/conformance/ndp/announce_canonicalization_vectors.json")
    assert len(suite["vectors"]) == 3
    for vector in suite["vectors"]:
        item = vector["input"]
        assert canonical_announce_json(item["frame"]) == vector["expected"]["canonical_json"]
        assert verify_announce_signature(
            item["frame"],
            item["public_key"],
            item["signature"],
        ) is vector["expected"]["signature_valid"]
        wire = {**item["frame"], "signature": item["signature"]}
        validator = NdpAnnounceValidator()
        validator.register_public_key(wire["nid"], item["public_key"])
        assert validator.validate(
            AnnounceFrame.from_dict(wire),
        ).is_valid is vector["expected"]["signature_valid"]


def test_shared_registry_consistency_vectors() -> None:
    suite = _load("spec/conformance/ndp/registry_consistency_vectors.json")
    assert len(suite["vectors"]) == 16
    for vector in suite["vectors"]:
        item = vector["input"]
        expected = vector["expected"]
        now = _time(item["now"])
        registry = NdpRegistryProfile(item["profile"])
        outcomes = [
            registry.apply_announce(
                announce["frame"],
                signature_valid=announce["signature_valid"],
                received_at=_time(announce.get("received_at", item["now"])),
            )
            for announce in item["announces"]
        ]

        assert [result.decision.value for result in outcomes] == expected["decisions"]
        assert [result.error_code for result in outcomes] == expected["errors"]
        assert registry.live_nids(now) == expected["live_nids"]
        assert registry.highest_sequences == expected["highest_sequences"]

        if "cluster_query" in item:
            selected = registry.resolve_cluster(item["cluster_query"], now)
            assert selected.nid == expected.get("selected_nid")
            assert selected.epoch == expected.get("selected_epoch")
            assert selected.error_code == expected.get("cluster_error")

        if "bridge_queries" in item:
            assert [
                registry.discover_bridges(
                    query["direction"],
                    query["protocol"],
                    now,
                )
                for query in item["bridge_queries"]
            ] == expected["bridge_results"]

        if "resolve_error" in expected:
            assert registry.has_stale_entry(now)
