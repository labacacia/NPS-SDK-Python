# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from nps_sdk.nop.portable_profile import (
    evaluate_orchestration,
    evaluate_runtime,
)

def _repo_file(relative: str) -> Path:
    for root in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Unable to locate repository file: {relative}")


def _fixture(name: str) -> dict:
    path = _repo_file(f"spec/conformance/nop/{name}")
    return json.loads(path.read_text(encoding="utf-8"))


def test_shared_orchestrator_transcripts() -> None:
    vectors = _fixture("orchestrator_transcripts.json")["vectors"]
    assert len(vectors) == 10
    for vector in vectors:
        assert evaluate_orchestration(vector["input"]) == vector["expected"], vector["id"]


def test_shared_runtime_security_vectors() -> None:
    vectors = _fixture("runtime_security_vectors.json")["vectors"]
    assert len(vectors) == 22
    for vector in vectors:
        assert (
            evaluate_runtime(vector["category"], vector["input"])
            == vector["expected"]
        ), vector["id"]
