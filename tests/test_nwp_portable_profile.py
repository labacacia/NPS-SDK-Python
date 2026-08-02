# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nps_sdk.nwp.portable_profile import (
    BridgeLifecycleRequest,
    PortableNodeRequest,
    evaluate_bridge_lifecycle,
    evaluate_portable_node,
)

def _repo_file(relative: str) -> Path:
    for root in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Unable to locate repository file: {relative}")


def _vectors(name: str) -> list[dict[str, Any]]:
    path = _repo_file(f"spec/conformance/nwp/{name}")
    return json.loads(path.read_text(encoding="utf-8"))["vectors"]


def _assert_expected(actual: object, expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        if key == "response":
            continue
        assert getattr(actual, key) == value, key


def test_portable_node_server_vectors() -> None:
    for vector in _vectors("portable_node_server_vectors.json"):
        raw = vector["input"]
        request = PortableNodeRequest(
            transport=raw["transport"],
            node_role=raw["node_role"],
            method=raw.get("method"),
            path=raw.get("path"),
            content_type=raw.get("content_type"),
            accept=raw.get("accept"),
            body_bytes=raw.get("body_bytes", 0),
            max_body_bytes=raw.get("max_body_bytes", 1024 * 1024),
            frame_kind=raw.get("frame_kind"),
            body_valid=raw.get("body_valid", True),
            cancelled=raw.get("cancelled", False),
            correlation_id=raw.get("correlation_id"),
        )
        _assert_expected(evaluate_portable_node(request), vector["expected"])


def test_bridge_lifecycle_vectors() -> None:
    for vector in _vectors("bridge_lifecycle_vectors.json"):
        raw = vector["input"]
        request = BridgeLifecycleRequest(
            protocol=raw["protocol"],
            endpoint=raw["endpoint"],
            registered_protocols=raw["registered_protocols"],
            allow_http=raw.get("allow_http", True),
            reject_private=raw.get("reject_private", True),
            allowed_prefixes=raw.get("allowed_prefixes", ()),
            timeout_ms=raw.get("timeout_ms", 0),
            elapsed_ms=raw.get("elapsed_ms", 0),
            cancelled=raw.get("cancelled", False),
            correlation_id=raw.get("correlation_id"),
            task_mode=raw.get("task_mode", "sync"),
        )
        _assert_expected(evaluate_bridge_lifecycle(request), vector["expected"])
