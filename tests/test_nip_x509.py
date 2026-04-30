# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for NPS-RFC-0002 X.509 NID certificate primitives."""

from __future__ import annotations

import base64
import datetime
import json

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from nps_sdk.nip import cert_format, error_codes
from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.frames import IdentFrame
from nps_sdk.nip.verifier import NipIdentVerifier, NipVerifierOptions
from nps_sdk.nip.x509 import LeafRole, NipX509Builder


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _pub_key_string(pub) -> str:
    """Mirror NipIdentity.pub_key_string format ("ed25519:<base64url(DER)>")."""
    der = pub.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return f"ed25519:{_b64u(der)}"


def _build_v2_frame(
    subject_nid: str,
    subject_priv: Ed25519PrivateKey,
    ca_priv: Ed25519PrivateKey,
    ca_nid: str,
    level: AssuranceLevel | None,
    leaf: x509.Certificate,
    root: x509.Certificate,
) -> IdentFrame:
    """Build a v2 IdentFrame with v1 Ed25519 sig + 2-cert chain."""
    now = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    expires = (_now() + datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pub_key_str = _pub_key_string(subject_priv.public_key())

    # v1 unsigned dict (matches IdentFrame.unsigned_dict()).
    unsigned: dict = {
        "frame":        "0x20",
        "nid":          subject_nid,
        "pub_key":      pub_key_str,
        "capabilities": [],
        "scope":        {},
        "issued_by":    ca_nid,
        "issued_at":    now,
        "expires_at":   expires,
        "serial":       "0x01",
    }
    if level is not None:
        unsigned["assurance_level"] = level.wire

    canonical = json.dumps(unsigned, separators=(",", ":"), sort_keys=True)
    sig_bytes = ca_priv.sign(canonical.encode("utf-8"))
    sig_str = "ed25519:" + _b64u(sig_bytes)

    return IdentFrame(
        nid=subject_nid,
        pub_key=pub_key_str,
        capabilities=(),
        scope={},
        issued_by=ca_nid,
        issued_at=now,
        expires_at=expires,
        serial="0x01",
        signature=sig_str,
        metadata=None,
        assurance_level=level,
        cert_format=cert_format.V2_X509,
        cert_chain=(_b64u(leaf.public_bytes(serialization.Encoding.DER)),
                    _b64u(root.public_bytes(serialization.Encoding.DER))),
    )


def _build_leaf_without_eku(
    subject_nid: str,
    subject_pub,
    ca_priv: Ed25519PrivateKey,
    ca_nid: str,
    serial: int,
) -> x509.Certificate:
    """Build a leaf cert WITHOUT the NPS EKU extension — exercises EKU presence check."""
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_nid)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ca_nid)]))
        .public_key(subject_pub)
        .serial_number(serial)
        .not_valid_before(_now() - datetime.timedelta(minutes=1))
        .not_valid_after(_now() + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        # ★ Deliberately NO ExtendedKeyUsage extension.
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(subject_nid)]),
            critical=False,
        )
    )
    return builder.sign(private_key=ca_priv, algorithm=None)


# ── Test cases ───────────────────────────────────────────────────────────────

class TestNipX509:
    """Mirror of .NET / Java NipX509Tests (5 cases)."""

    def test_register_x509_round_trip_verifier_accepts(self):
        """Happy path: dual-trust v2 frame, both v1 + X.509 verify."""
        ca = Ed25519PrivateKey.generate()
        agent = Ed25519PrivateKey.generate()
        now = _now()

        root = NipX509Builder.issue_root(
            "urn:nps:ca:test", ca,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=365),
            1,
        )
        leaf = NipX509Builder.issue_leaf(
            "urn:nps:agent:happy:1", agent.public_key(), ca, "urn:nps:ca:test",
            LeafRole.AGENT, AssuranceLevel.ATTESTED,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=30),
            2,
        )
        frame = _build_v2_frame("urn:nps:agent:happy:1", agent, ca, "urn:nps:ca:test",
                                AssuranceLevel.ATTESTED, leaf, root)

        opts = NipVerifierOptions(
            trusted_ca_public_keys={"urn:nps:ca:test": _pub_key_string(ca.public_key())},
            trusted_x509_roots=(root,),
        )
        result = NipIdentVerifier(opts).verify(frame, "urn:nps:ca:test")

        assert result.valid, f"step={result.step_failed} err={result.error_code} msg={result.message}"

    def test_register_x509_leaf_eku_stripped_verifier_rejects(self):
        """Tampered chain whose leaf has no EKU triggers NIP-CERT-EKU-MISSING."""
        ca = Ed25519PrivateKey.generate()
        agent = Ed25519PrivateKey.generate()
        now = _now()

        root = NipX509Builder.issue_root(
            "urn:nps:ca:test", ca,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=365),
            1,
        )
        tampered = _build_leaf_without_eku(
            "urn:nps:agent:eku:1", agent.public_key(), ca, "urn:nps:ca:test", 99)
        frame = _build_v2_frame("urn:nps:agent:eku:1", agent, ca, "urn:nps:ca:test",
                                None, tampered, root)

        opts = NipVerifierOptions(
            trusted_ca_public_keys={"urn:nps:ca:test": _pub_key_string(ca.public_key())},
            trusted_x509_roots=(root,),
        )
        result = NipIdentVerifier(opts).verify(frame, "urn:nps:ca:test")

        assert not result.valid
        assert result.error_code == error_codes.CERT_EKU_MISSING
        assert result.step_failed == 3

    def test_register_x509_leaf_for_different_nid_verifier_rejects_subject_mismatch(self):
        """Forged leaf for different NID triggers NIP-CERT-SUBJECT-NID-MISMATCH."""
        ca = Ed25519PrivateKey.generate()
        agent = Ed25519PrivateKey.generate()
        now = _now()

        root = NipX509Builder.issue_root(
            "urn:nps:ca:test", ca,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=365),
            1,
        )
        # Issue leaf for the FORGED nid; splice into frame asserting the VICTIM nid.
        forged_leaf = NipX509Builder.issue_leaf(
            "urn:nps:agent:attacker:9", agent.public_key(), ca, "urn:nps:ca:test",
            LeafRole.AGENT, AssuranceLevel.ANONYMOUS,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=30),
            77,
        )
        frame = _build_v2_frame("urn:nps:agent:victim:1", agent, ca, "urn:nps:ca:test",
                                None, forged_leaf, root)

        opts = NipVerifierOptions(
            trusted_ca_public_keys={"urn:nps:ca:test": _pub_key_string(ca.public_key())},
            trusted_x509_roots=(root,),
        )
        result = NipIdentVerifier(opts).verify(frame, "urn:nps:ca:test")

        assert not result.valid
        assert result.error_code == error_codes.CERT_SUBJECT_NID_MISMATCH
        assert result.step_failed == 3

    def test_v1_only_verifier_accepts_v2_frame_by_ignoring_cert_chain(self):
        """Phase 1 backward compat: v1-only verifier ignores cert_chain."""
        ca = Ed25519PrivateKey.generate()
        agent = Ed25519PrivateKey.generate()
        now = _now()

        root = NipX509Builder.issue_root(
            "urn:nps:ca:test", ca,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=365),
            1,
        )
        leaf = NipX509Builder.issue_leaf(
            "urn:nps:agent:v1compat:1", agent.public_key(), ca, "urn:nps:ca:test",
            LeafRole.AGENT, AssuranceLevel.ANONYMOUS,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=30),
            2,
        )
        frame = _build_v2_frame("urn:nps:agent:v1compat:1", agent, ca, "urn:nps:ca:test",
                                None, leaf, root)

        # Verifier WITHOUT trusted_x509_roots — Step 3b is skipped.
        opts = NipVerifierOptions(
            trusted_ca_public_keys={"urn:nps:ca:test": _pub_key_string(ca.public_key())},
        )
        result = NipIdentVerifier(opts).verify(frame, "urn:nps:ca:test")
        assert result.valid, f"v1-only verifier MUST accept v2 frames; got {result.error_code}"

    def test_v2_verifier_rejects_v2_frame_when_trusted_roots_missing(self):
        """v2 verifier with wrong trust roots rejects the chain."""
        ca = Ed25519PrivateKey.generate()
        agent = Ed25519PrivateKey.generate()
        now = _now()

        root = NipX509Builder.issue_root(
            "urn:nps:ca:test", ca,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=365),
            1,
        )
        leaf = NipX509Builder.issue_leaf(
            "urn:nps:agent:wrongtrust:1", agent.public_key(), ca, "urn:nps:ca:test",
            LeafRole.AGENT, AssuranceLevel.ANONYMOUS,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=30),
            2,
        )
        frame = _build_v2_frame("urn:nps:agent:wrongtrust:1", agent, ca, "urn:nps:ca:test",
                                None, leaf, root)

        # Different unrelated CA root.
        other_ca = Ed25519PrivateKey.generate()
        other_root = NipX509Builder.issue_root(
            "urn:nps:ca:other", other_ca,
            now - datetime.timedelta(minutes=1),
            now + datetime.timedelta(days=365),
            1,
        )

        opts = NipVerifierOptions(
            trusted_ca_public_keys={"urn:nps:ca:test": _pub_key_string(ca.public_key())},
            trusted_x509_roots=(other_root,),
        )
        result = NipIdentVerifier(opts).verify(frame, "urn:nps:ca:test")

        assert not result.valid
        assert result.error_code == error_codes.CERT_FORMAT_INVALID
        assert result.step_failed == 3
