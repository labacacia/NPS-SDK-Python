# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Locate repository-owned shared vectors in canonical or standalone SDK layouts."""
from pathlib import Path


def conformance_fixture(*segments: str) -> Path:
    """Return the nearest owned conformance file, failing if none is distributed."""
    for root in Path(__file__).resolve().parents:
        candidate = root / "spec" / "conformance"
        candidate = candidate.joinpath(*segments)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(Path("spec", "conformance", *segments))
