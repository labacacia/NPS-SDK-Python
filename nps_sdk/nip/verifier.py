# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NipIdentVerifier — Phase 1 dual-trust IdentFrame verifier per NPS-RFC-0002 §8.1.

Steps:
1. v1 Ed25519 signature check against the issuer's CA public key.
2. Optional minimum assurance level check.
3b. X.509 chain validation (only if `cert_format == "v2-x509"` AND
    `trusted_x509_roots` is configured).

Verifiers without trusted X.509 roots configured remain v1-compatible — they
ignore the cert_chain field on incoming v2 frames.
"""

from __future__ import annotations

import dataclasses
from typing import Sequence

from cryptography import x509

from nps_sdk.nip import cert_format, error_codes
from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.frames import IdentFrame
from nps_sdk.nip.identity import NipIdentity
from nps_sdk.nip.x509.verifier import NipX509Verifier


@dataclasses.dataclass(frozen=True)
class NipVerifierOptions:
    """Configuration for `NipIdentVerifier`."""

    # Map of issuer NID → CA public key string ("ed25519:<base64url(DER)>").
    trusted_ca_public_keys: dict[str, str] = dataclasses.field(default_factory=dict)

    # X.509 trust anchors. None/empty causes v2 frames to be rejected at Step 3b
    # (no trust possible) but v1 frames continue to verify per Step 1.
    trusted_x509_roots: tuple[x509.Certificate, ...] = dataclasses.field(default_factory=tuple)

    # Minimum required assurance level (NPS-RFC-0003). None disables Step 2.
    min_assurance_level: AssuranceLevel | None = None


@dataclasses.dataclass(frozen=True)
class NipIdentVerifyResult:
    valid:        bool
    step_failed:  int = 0          # 0 = none, 1 = sig, 2 = assurance, 3 = X.509
    error_code:   str | None = None
    message:      str | None = None

    @classmethod
    def ok(cls) -> "NipIdentVerifyResult":
        return cls(valid=True)

    @classmethod
    def fail(cls, step: int, error_code: str, message: str) -> "NipIdentVerifyResult":
        return cls(valid=False, step_failed=step, error_code=error_code, message=message)


class NipIdentVerifier:
    """Phase 1 dual-trust IdentFrame verifier."""

    def __init__(self, options: NipVerifierOptions) -> None:
        self._opts = options

    def verify(self, frame: IdentFrame, issuer_nid: str) -> NipIdentVerifyResult:
        # Step 1: v1 Ed25519 signature check ─────────────────────────────────
        ca_pub_key_str = self._opts.trusted_ca_public_keys.get(issuer_nid)
        if ca_pub_key_str is None:
            return NipIdentVerifyResult.fail(
                1, error_codes.CERT_UNTRUSTED_ISSUER,
                f"no trusted CA public key for issuer: {issuer_nid!r}")
        if not NipIdentity.verify_signature(
                ca_pub_key_str, frame.unsigned_dict(), frame.signature):
            return NipIdentVerifyResult.fail(
                1, error_codes.CERT_SIGNATURE_INVALID,
                "v1 Ed25519 signature did not verify against issuer CA key")

        # Step 2: minimum assurance level ────────────────────────────────────
        min_level = self._opts.min_assurance_level
        if min_level is not None:
            got = frame.assurance_level or AssuranceLevel.ANONYMOUS
            if not got.meets_or_exceeds(min_level):
                return NipIdentVerifyResult.fail(
                    2, error_codes.ASSURANCE_MISMATCH,
                    f"assurance_level ({got.wire}) below required minimum ({min_level.wire})")

        # Step 3b: X.509 chain check (only if both opt-ins present) ──────────
        has_v2_trust = bool(self._opts.trusted_x509_roots)
        is_v2_frame  = frame.cert_format == cert_format.V2_X509
        if has_v2_trust and is_v2_frame:
            x509_result = NipX509Verifier.verify(
                cert_chain_b64u_der=frame.cert_chain or (),
                asserted_nid=frame.nid,
                asserted_assurance_level=frame.assurance_level,
                trusted_root_certs=self._opts.trusted_x509_roots,
            )
            if not x509_result.valid:
                return NipIdentVerifyResult.fail(
                    3,
                    x509_result.error_code or error_codes.CERT_FORMAT_INVALID,
                    x509_result.message or "X.509 chain validation failed",
                )

        return NipIdentVerifyResult.ok()
