# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

from nps_sdk.conformance import (
    NODE_L1,
    NODE_L1_CASES,
    NODE_L2_CASES,
    NpsConformanceCaseResult,
    NpsConformanceManifest,
    validate_manifest,
)


def test_catalog_contains_expected_l1_and_l2_cases() -> None:
    assert len(NODE_L1_CASES) == 20
    assert len(NODE_L2_CASES) == 16
    assert NODE_L1_CASES[0].id == "TC-N1-NCP-01"


def test_validator_accepts_complete_l1_manifest() -> None:
    manifest = NpsConformanceManifest.create(
        NODE_L1,
        "node",
        "0.1.0",
        "urn:nps:node:example.test:node-1",
        "reference",
        "1.0.0-alpha.17",
        tuple(NpsConformanceCaseResult(c.id, "na" if c.optional else "pass") for c in NODE_L1_CASES),
    )

    result = validate_manifest(manifest)

    assert result.valid


def test_validator_rejects_missing_case() -> None:
    manifest = NpsConformanceManifest.create(
        NODE_L1,
        "node",
        "0.1.0",
        "urn:nps:node:example.test:node-1",
        "reference",
        "1.0.0-alpha.17",
        tuple(NpsConformanceCaseResult(c.id, "pass") for c in NODE_L1_CASES[:-1]),
    )

    result = validate_manifest(manifest)

    assert not result.valid
    assert "Missing conformance case results" in result.message
