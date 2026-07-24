# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
TrustFrameValidator — basic open TrustFrame validator for self-hosted
deployments that pin trusted grantor anchors explicitly (parity with the .NET
reference ``NPS.NIP.Verification.TrustFrameValidator``).

It checks frame shape, expiry, grantor/grantee membership, required-capability
scope, and target-node scope. It does NOT check the TrustFrame signature — that
is layered on by deployments or NPS Cloud federation policy.
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Sequence

from nps_sdk.nip import error_codes
from nps_sdk.nip.frames import TrustFrame
from nps_sdk.nip.verifier import NipIdentVerifyResult, _parse_timestamp, _utcnow, nwp_path_matches


@dataclasses.dataclass(frozen=True)
class TrustFrameValidationContext:
    """Inputs for :func:`TrustFrameValidator.validate`."""

    # Grantor CA NIDs that this node trusts as anchors.
    trusted_grantors: frozenset[str]

    # The CA NID expected to be authorized by the TrustFrame.
    expected_grantee_ca: str

    # Capabilities required for the current request.
    required_capabilities: Sequence[str] | None = None

    # Target NWP path required for the current request.
    target_node_path: str | None = None

    # Clock override for tests.
    as_of: datetime.datetime | None = None


class TrustFrameValidator:
    """Static validator; namespace class for parity with the .NET reference."""

    @staticmethod
    def validate(
        frame: TrustFrame,
        context: TrustFrameValidationContext,
    ) -> NipIdentVerifyResult:
        # ── Shape ──────────────────────────────────────────────────────────────
        if (not _nonblank(frame.grantor_nid)
                or not _nonblank(frame.grantee_ca)
                or not _nonblank(frame.issued_at)
                or not _nonblank(frame.expires_at)
                or not _nonblank(frame.serial)
                or not _nonblank(frame.signer_nid)
                or not _nonblank(frame.signature)
                or len(frame.trust_scope) == 0
                or len(frame.nodes) == 0):
            return NipIdentVerifyResult.fail(
                3, error_codes.TRUST_FRAME_INVALID,
                "TrustFrame is missing grantor, grantee, issued_at, expires_at, "
                "serial, signer_nid, signature, trust_scope, or nodes.")

        if _parse_timestamp(frame.issued_at) is None:
            return NipIdentVerifyResult.fail(
                3, error_codes.TRUST_FRAME_INVALID,
                f"TrustFrame issued_at is not a valid timestamp: {frame.issued_at}.")

        expires_at = _parse_timestamp(frame.expires_at)
        if expires_at is None:
            return NipIdentVerifyResult.fail(
                3, error_codes.TRUST_FRAME_INVALID,
                f"TrustFrame expires_at is not a valid timestamp: {frame.expires_at}.")

        # ── Expiry ─────────────────────────────────────────────────────────────
        now = context.as_of or _utcnow()
        if expires_at <= now:
            return NipIdentVerifyResult.fail(
                3, error_codes.TRUST_FRAME_EXPIRED,
                f"TrustFrame expired at {frame.expires_at}.")

        # ── Grantor / grantee ──────────────────────────────────────────────────
        if frame.grantor_nid not in context.trusted_grantors:
            return NipIdentVerifyResult.fail(
                3, error_codes.CERT_UNTRUSTED_ISSUER,
                f"TrustFrame grantor {frame.grantor_nid!r} is not a trusted grantor.")

        if frame.grantee_ca != context.expected_grantee_ca:
            return NipIdentVerifyResult.fail(
                3, error_codes.TRUST_FRAME_INVALID,
                f"TrustFrame grantee {frame.grantee_ca!r} does not match "
                f"expected CA {context.expected_grantee_ca!r}.")

        # ── Capability scope ───────────────────────────────────────────────────
        if context.required_capabilities:
            granted = set(frame.trust_scope)
            missing = [c for c in context.required_capabilities if c not in granted]
            if missing:
                return NipIdentVerifyResult.fail(
                    5, error_codes.TRUST_FRAME_SCOPE_EXCEEDS_GRANTOR,
                    f"TrustFrame is missing required capabilities: {', '.join(missing)}.")

        # ── Node scope ─────────────────────────────────────────────────────────
        if context.target_node_path is not None:
            covered = any(
                nwp_path_matches(pattern, context.target_node_path)
                for pattern in frame.nodes)
            if not covered:
                return NipIdentVerifyResult.fail(
                    6, error_codes.CERT_SCOPE_VIOLATION,
                    f"Target path {context.target_node_path!r} is not covered by "
                    "the TrustFrame node scope.")

        return NipIdentVerifyResult.ok()


def _nonblank(value: str | None) -> bool:
    return bool(value and value.strip())
