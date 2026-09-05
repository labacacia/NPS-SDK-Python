# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

from nps_sdk.conformance import (
    NODE_L1,
    NODE_L1_CASES,
    NODE_L2,
    NODE_L2_CASES,
    NpsConformanceCaseResult,
    NpsConformanceManifest,
    validate_manifest,
)


def test_catalog_contains_expected_l1_and_l2_cases() -> None:
    assert len(NODE_L1_CASES) == 20
    assert len(NODE_L2_CASES) == 38
    assert NODE_L1_CASES[0].id == "TC-N1-NCP-01"


def test_validator_accepts_complete_l1_manifest() -> None:
    manifest = NpsConformanceManifest.create(
        NODE_L1,
        "node",
        "0.1.0",
        "urn:nps:node:example.test:node-1",
        "reference",
        "1.0.0-alpha.18",
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
        "1.0.0-alpha.18",
        tuple(NpsConformanceCaseResult(c.id, "pass") for c in NODE_L1_CASES[:-1]),
    )

    result = validate_manifest(manifest)

    assert not result.valid
    assert "Missing conformance case results" in result.message


def test_validator_enforces_l2_all_or_nothing_families() -> None:
    results = [
        NpsConformanceCaseResult(
            case.id,
            "pass"
            if case.id.startswith(("TC-N2-AaaS-", "TC-N2-Anchor")) or case.id == "TC-N2-HA-09"
            else "na",
        )
        for case in NODE_L2_CASES
    ]
    manifest = NpsConformanceManifest.create(
        NODE_L2,
        "single-anchor",
        "0.1.0",
        "urn:nps:node:example.test:anchor-1",
        "reference",
        "1.0.0-alpha.18",
        results,
    )

    assert manifest.profile_version == "0.7"
    assert validate_manifest(manifest).valid

    results[19] = NpsConformanceCaseResult("TC-N2-Tls-01", "pass")
    partial = NpsConformanceManifest.create(
        NODE_L2,
        "single-anchor",
        "0.1.0",
        "urn:nps:node:example.test:anchor-1",
        "reference",
        "1.0.0-alpha.18",
        results,
    )
    validation = validate_manifest(partial)
    assert not validation.valid
    assert "must be all pass or all na" in validation.message

    results[19] = NpsConformanceCaseResult("TC-N2-Tls-01", "na")
    results[-1] = NpsConformanceCaseResult("TC-N2-HA-09", "na")
    no_anchor_mode = NpsConformanceManifest.create(
        NODE_L2,
        "invalid-anchor",
        "0.1.0",
        "urn:nps:node:example.test:anchor-1",
        "reference",
        "1.0.0-alpha.18",
        results,
    )
    applicability = validate_manifest(no_anchor_mode)
    assert not applicability.valid
    assert "opposite applicability" in applicability.message


def test_validator_requires_reason_for_aaas_should_exception() -> None:
    results = [
        NpsConformanceCaseResult(
            case.id,
            "pass"
            if case.id.startswith(("TC-N2-AaaS-", "TC-N2-Anchor")) or case.id == "TC-N2-HA-09"
            else "na",
        )
        for case in NODE_L2_CASES
    ]
    results[5] = NpsConformanceCaseResult("TC-N2-AaaS-06", "na")
    missing_reason = NpsConformanceManifest.create(
        NODE_L2, "service", "0.1.0", "urn:nps:node:example.test:anchor-1",
        "reference", "1.0.0-alpha.18", results,
    )
    assert "requires a non-empty message" in validate_manifest(missing_reason).message

    results[5] = NpsConformanceCaseResult("TC-N2-AaaS-06", "na", "Synchronous-only deployment")
    reasoned = NpsConformanceManifest.create(
        NODE_L2, "service", "0.1.0", "urn:nps:node:example.test:anchor-1",
        "reference", "1.0.0-alpha.18", results,
    )
    assert validate_manifest(reasoned).valid
