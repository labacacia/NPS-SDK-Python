# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Tests — the ``phase3_enforcement`` flag wired into ``NipIdentVerifier`` step 3c
(NIP v0.12 §7.5). Covers the scope gate: v1 frames, v1-only verifiers and a
Phase-1–2 verifier must all be unaffected.
"""

from __future__ import annotations

import base64
import datetime as dt
import json

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from nps_sdk.nip import cert_format, error_codes
from nps_sdk.nip.frames import IdentFrame
from nps_sdk.nip.verifier import NipIdentVerifier, NipVerifierOptions
from nps_sdk.nip.x509.oids import NpsX509Oids

from tests.test_nip_phase3 import _staple, _utf8_sequence

CA_NID = "urn:nps:org:ca.example.com"
AGENT_NID = "urn:nps:agent:ca.example.com:p3-001"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _name(nid: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nid)])


def _chain(roles: list[str] | None, caps: list[str] | None):
    """Issue a root + leaf Ed25519 chain, optionally carrying the id-nps-* extensions."""
    ca_key = Ed25519PrivateKey.generate()
    leaf_key = Ed25519PrivateKey.generate()
    now = dt.datetime.now(dt.timezone.utc)

    root = (
        x509.CertificateBuilder()
        .subject_name(_name(CA_NID)).issuer_name(_name(CA_NID))
        .public_key(ca_key.public_key()).serial_number(1)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=ca_key, algorithm=None)
    )

    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(AGENT_NID)).issuer_name(_name(CA_NID))
        .public_key(leaf_key.public_key()).serial_number(2)
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([NpsX509Oids.EKU_AGENT_IDENTITY]), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(AGENT_NID)]),
            critical=False)
    )
    if roles is not None:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(NpsX509Oids.ID_NPS_NODE_ROLES, _utf8_sequence(roles)),
            critical=False)
    if caps is not None:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(NpsX509Oids.ID_NPS_CAPABILITIES, _utf8_sequence(caps)),
            critical=False)
    leaf = builder.sign(private_key=ca_key, algorithm=None)
    return ca_key, leaf_key, root, leaf


def _frame(ca_key, leaf_key, root, leaf, *, v2: bool = True,
           node_roles=("memory",), capabilities=("nwp:query",),
           ocsp_staple: str | None = None, cert_chain=None) -> IdentFrame:
    pub_der = leaf_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    frame = IdentFrame(
        nid=AGENT_NID,
        pub_key=f"ed25519:{_b64u(pub_der)}",
        capabilities=capabilities,
        scope={},
        issued_by=CA_NID,
        issued_at="2026-07-01T00:00:00Z",
        expires_at="2036-08-01T00:00:00Z",
        serial="0x01",
        signature="",
        node_roles=node_roles,
        cert_format=cert_format.V2_X509 if v2 else None,
        cert_chain=(cert_chain if cert_chain is not None else (
            _b64u(leaf.public_bytes(serialization.Encoding.DER)),
            _b64u(root.public_bytes(serialization.Encoding.DER)),
        )) if v2 else None,
        ocsp_staple=ocsp_staple if ocsp_staple is not None else _staple(
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=6)),
    )
    canonical = json.dumps(frame.unsigned_dict(), separators=(",", ":"), sort_keys=True)
    signature = "ed25519:" + _b64u(ca_key.sign(canonical.encode("utf-8")))
    return IdentFrame(**{**frame.__dict__, "signature": signature})


def _verifier(ca_key, root, *, phase3: bool, roots=True) -> NipIdentVerifier:
    ca_pub = ca_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    return NipIdentVerifier(NipVerifierOptions(
        trusted_ca_public_keys={CA_NID: f"ed25519:{_b64u(ca_pub)}"},
        trusted_x509_roots=(root,) if roots else (),
        phase3_enforcement=phase3,
    ))


class TestStep3cScopeGate:
    @pytest.mark.asyncio
    async def test_enforcement_on_rejects_an_over_claimed_capability(self):
        ca, lk, root, leaf = _chain(roles=["memory"], caps=["nwp:query"])
        frame = _frame(ca, lk, root, leaf, capabilities=("nwp:query", "nop:orchestrate"))

        result = await _verifier(ca, root, phase3=True).verify(frame)

        assert not result.valid
        assert result.error_code == error_codes.CERT_CAPABILITIES_EXCEEDED
        assert result.step_failed == 3

    @pytest.mark.asyncio
    async def test_enforcement_off_is_advisory_only(self):
        ca, lk, root, leaf = _chain(roles=["memory"], caps=["nwp:query"])
        frame = _frame(ca, lk, root, leaf, capabilities=("nwp:query", "nop:orchestrate"),
                       ocsp_staple="")           # would also fail the staple check

        assert (await _verifier(ca, root, phase3=False).verify(frame)).valid

    @pytest.mark.asyncio
    async def test_a_conformant_v2_frame_passes_under_enforcement(self):
        ca, lk, root, leaf = _chain(roles=["memory", "anchor"], caps=["nwp:query"])
        frame = _frame(ca, lk, root, leaf)

        assert (await _verifier(ca, root, phase3=True).verify(frame)).valid

    @pytest.mark.asyncio
    async def test_a_v1_frame_is_untouched_by_enforcement(self):
        ca, lk, root, leaf = _chain(roles=["memory"], caps=["nwp:query"])
        frame = _frame(ca, lk, root, leaf, v2=False,
                       capabilities=("nwp:query", "nop:orchestrate"))

        assert (await _verifier(ca, root, phase3=True).verify(frame)).valid

    @pytest.mark.asyncio
    async def test_a_v1_only_verifier_ignores_the_cert_chain(self):
        ca, lk, root, leaf = _chain(roles=["memory"], caps=["nwp:query"])
        frame = _frame(ca, lk, root, leaf, capabilities=("nwp:query", "nop:orchestrate"))

        assert (await _verifier(ca, root, phase3=True, roots=False).verify(frame)).valid

    @pytest.mark.asyncio
    async def test_missing_staple_fails_under_enforcement(self):
        ca, lk, root, leaf = _chain(roles=None, caps=None)
        frame = _frame(ca, lk, root, leaf, ocsp_staple="")

        result = await _verifier(ca, root, phase3=True).verify(frame)
        assert result.error_code == error_codes.OCSP_STAPLE_EXPIRED

    @pytest.mark.asyncio
    async def test_undecodable_leaf_fails_with_cert_format_invalid(self):
        ca, lk, root, leaf = _chain(roles=None, caps=None)
        good = _frame(ca, lk, root, leaf)
        broken = IdentFrame(**{**good.__dict__, "cert_chain": ("!!!!",)})
        result = await _verifier(ca, root, phase3=True).verify(broken)
        assert result.error_code == error_codes.CERT_FORMAT_INVALID
        assert result.step_failed == 3
