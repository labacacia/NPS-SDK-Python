# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Verifies NPS X.509 NID certificate chains per NPS-RFC-0002 §4.

Verification stages (RFC §4.6):
1. Decode chain (base64url DER → cryptography.x509.Certificate).
2. Leaf EKU check — critical, contains agent-identity OR node-identity OID.
3. Subject CN / SAN URI match against asserted NID.
4. Assurance-level extension match against asserted level (if both present).
5. Chain signature verification — leaf → intermediates → trusted root.
"""

from __future__ import annotations

import base64
import dataclasses
from typing import Sequence

from cryptography import exceptions as crypto_exceptions
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from nps_sdk.nip import error_codes
from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.x509.oids import NpsX509Oids


@dataclasses.dataclass(frozen=True)
class NipX509VerifyResult:
    valid:       bool
    error_code:  str | None = None
    message:     str | None = None
    leaf:        x509.Certificate | None = None

    @classmethod
    def ok(cls, leaf: x509.Certificate) -> "NipX509VerifyResult":
        return cls(valid=True, leaf=leaf)

    @classmethod
    def fail(cls, error_code: str, message: str) -> "NipX509VerifyResult":
        return cls(valid=False, error_code=error_code, message=message)


class NipX509Verifier:
    """Static verifier; namespace class for parity with .NET / Java references."""

    @staticmethod
    def verify(
        cert_chain_b64u_der:        Sequence[str],
        asserted_nid:               str,
        asserted_assurance_level:   AssuranceLevel | None,
        trusted_root_certs:         Sequence[x509.Certificate],
    ) -> NipX509VerifyResult:
        # Stage 1: decode chain ───────────────────────────────────────────────
        if not cert_chain_b64u_der:
            return NipX509VerifyResult.fail(error_codes.CERT_FORMAT_INVALID,
                "cert_chain is empty")

        chain: list[x509.Certificate] = []
        try:
            for entry in cert_chain_b64u_der:
                der = _b64u_decode(entry)
                chain.append(x509.load_der_x509_certificate(der))
        except Exception as e:
            return NipX509VerifyResult.fail(error_codes.CERT_FORMAT_INVALID,
                f"DER decode failed: {e}")

        leaf = chain[0]

        # Stage 2: EKU check ──────────────────────────────────────────────────
        eku_result = _check_leaf_eku(leaf)
        if not eku_result.valid:
            return eku_result

        # Stage 3: subject CN / SAN URI match ────────────────────────────────
        subject_result = _check_subject_nid(leaf, asserted_nid)
        if not subject_result.valid:
            return subject_result

        # Stage 4: assurance-level extension ─────────────────────────────────
        assurance_result = _check_assurance_level(leaf, asserted_assurance_level)
        if not assurance_result.valid:
            return assurance_result

        # Stage 5: chain signature verification ──────────────────────────────
        chain_result = _check_chain_signature(chain, trusted_root_certs)
        if not chain_result.valid:
            return chain_result

        return NipX509VerifyResult.ok(leaf)


# ── Stage helpers ────────────────────────────────────────────────────────────

def _check_leaf_eku(leaf: x509.Certificate) -> NipX509VerifyResult:
    try:
        eku_ext = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound:
        return NipX509VerifyResult.fail(error_codes.CERT_EKU_MISSING,
            "leaf certificate has no ExtendedKeyUsage extension")
    if not eku_ext.critical:
        return NipX509VerifyResult.fail(error_codes.CERT_EKU_MISSING,
            "ExtendedKeyUsage extension is not marked critical")
    eku_oids = list(eku_ext.value)
    if (NpsX509Oids.EKU_AGENT_IDENTITY not in eku_oids
            and NpsX509Oids.EKU_NODE_IDENTITY not in eku_oids):
        return NipX509VerifyResult.fail(error_codes.CERT_EKU_MISSING,
            "ExtendedKeyUsage does not contain agent-identity or node-identity OID")
    return NipX509VerifyResult.ok(leaf)


def _check_subject_nid(leaf: x509.Certificate, asserted_nid: str) -> NipX509VerifyResult:
    cn_attrs = leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    cn = cn_attrs[0].value if cn_attrs else None
    if cn != asserted_nid:
        return NipX509VerifyResult.fail(error_codes.CERT_SUBJECT_NID_MISMATCH,
            f"leaf subject CN ({cn!r}) does not match asserted NID ({asserted_nid!r})")
    try:
        san_ext = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return NipX509VerifyResult.fail(error_codes.CERT_SUBJECT_NID_MISMATCH,
            "leaf has no Subject Alternative Name extension")
    uris = san_ext.value.get_values_for_type(x509.UniformResourceIdentifier)
    if asserted_nid not in uris:
        return NipX509VerifyResult.fail(error_codes.CERT_SUBJECT_NID_MISMATCH,
            "no SAN URI matches asserted NID")
    return NipX509VerifyResult.ok(leaf)


def _check_assurance_level(
    leaf: x509.Certificate, asserted: AssuranceLevel | None
) -> NipX509VerifyResult:
    if asserted is None:
        return NipX509VerifyResult.ok(leaf)
    try:
        ext = leaf.extensions.get_extension_for_oid(NpsX509Oids.NID_ASSURANCE_LEVEL)
    except x509.ExtensionNotFound:
        # Optional in v0.1 — pass silently when extension is absent.
        return NipX509VerifyResult.ok(leaf)
    der = ext.value.value if hasattr(ext.value, "value") else None
    if not isinstance(der, (bytes, bytearray)):
        return NipX509VerifyResult.fail(error_codes.CERT_FORMAT_INVALID,
            "assurance-level extension has no DER bytes")
    # Decode hand-rolled ASN.1 ENUMERATED: tag=0x0A, len=0x01, content=<rank>.
    if len(der) != 3 or der[0] != 0x0A or der[1] != 0x01:
        return NipX509VerifyResult.fail(error_codes.CERT_FORMAT_INVALID,
            f"malformed assurance-level extension: {der.hex()}")
    rank = der[2]
    try:
        cert_level = AssuranceLevel.from_rank(rank)
    except ValueError:
        return NipX509VerifyResult.fail(error_codes.ASSURANCE_UNKNOWN,
            f"assurance-level extension contains unknown value: {rank}")
    if cert_level is not asserted:
        return NipX509VerifyResult.fail(error_codes.ASSURANCE_MISMATCH,
            f"cert assurance-level ({cert_level.wire}) does not match asserted ({asserted.wire})")
    return NipX509VerifyResult.ok(leaf)


def _check_chain_signature(
    chain: Sequence[x509.Certificate],
    trusted_roots: Sequence[x509.Certificate],
) -> NipX509VerifyResult:
    if not trusted_roots:
        return NipX509VerifyResult.fail(error_codes.CERT_FORMAT_INVALID,
            "no trusted X.509 roots configured")
    try:
        # Walk leaf → intermediates: each must be signed by its successor.
        for i in range(len(chain) - 1):
            _verify_signed_by(chain[i], chain[i + 1].public_key())
        # The last cert in the chain MUST chain to a trusted root.
        last = chain[-1]
        for root in trusted_roots:
            if _certs_equal(last, root):
                return NipX509VerifyResult.ok(chain[0])
            try:
                _verify_signed_by(last, root.public_key())
                return NipX509VerifyResult.ok(chain[0])
            except Exception:
                continue
        return NipX509VerifyResult.fail(error_codes.CERT_FORMAT_INVALID,
            "chain does not anchor to any trusted root")
    except crypto_exceptions.InvalidSignature as e:
        return NipX509VerifyResult.fail(error_codes.CERT_FORMAT_INVALID,
            f"chain signature verification failed: {e}")
    except Exception as e:
        return NipX509VerifyResult.fail(error_codes.CERT_FORMAT_INVALID,
            f"chain signature verification error: {e}")


def _verify_signed_by(child: x509.Certificate, parent_pub: object) -> None:
    """Raise InvalidSignature if child's signature doesn't verify under parent_pub."""
    if not isinstance(parent_pub, ed25519.Ed25519PublicKey):
        raise ValueError(f"parent public key is not Ed25519: {type(parent_pub).__name__}")
    parent_pub.verify(child.signature, child.tbs_certificate_bytes)


def _certs_equal(a: x509.Certificate, b: x509.Certificate) -> bool:
    return a.public_bytes(_DER) == b.public_bytes(_DER)


def _b64u_decode(s: str) -> bytes:
    # base64url with optional padding restoration.
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


# Avoid importing serialization.Encoding at module top to keep this file
# small; the value is stable so we set it once here.
from cryptography.hazmat.primitives import serialization as _serialization  # noqa: E402
_DER = _serialization.Encoding.DER
