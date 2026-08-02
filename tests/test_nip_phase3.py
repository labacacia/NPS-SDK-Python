# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Tests — NIP v0.12 §7.5 Phase-3 enforcement (brief B Part 1 §6).

Fixed clock 2026-07-05T12:00:00Z. Certificate fixture: self-signed ECDSA P-256,
``CN=phase3-test``, valid ``now-1d .. now+30d``, with optional non-critical
``id-nps-node-roles`` / ``id-nps-capabilities`` extensions carrying DER
``SEQUENCE OF UTF8String``. Staple fixture: a hand-built minimal RFC 6960
OCSPResponse, base64url without padding.
"""

from __future__ import annotations

import base64
import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from nps_sdk.nip import error_codes
from nps_sdk.nip.frames import IdentFrame
from nps_sdk.nip.phase3 import NipPhase3Enforcer
from nps_sdk.nip.verifier import NipVerifierOptions
from nps_sdk.nip.x509.oids import NpsX509Oids

NOW = dt.datetime(2026, 7, 5, 12, 0, 0, tzinfo=dt.timezone.utc)


# ── DER helpers (test-side encoder mirroring the enforcer's reader) ────────────

def _tlv(tag: int, content: bytes) -> bytes:
    if len(content) < 0x80:
        return bytes([tag, len(content)]) + content
    length = len(content).to_bytes((len(content).bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(length)]) + length + content


def _seq(*items: bytes) -> bytes:
    return _tlv(0x30, b"".join(items))


def _utf8(value: str) -> bytes:
    return _tlv(0x0C, value.encode("utf-8"))


def _gen_time(when: dt.datetime) -> bytes:
    return _tlv(0x18, when.strftime("%Y%m%d%H%M%SZ").encode("ascii"))


def _utf8_sequence(values: list[str]) -> bytes:
    return _seq(*[_utf8(v) for v in values])


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


_OCSP_BASIC_OID = bytes([0x06, 0x09, 0x2B, 0x06, 0x01, 0x05, 0x05, 0x07, 0x30, 0x01, 0x01])


def _ocsp_response(next_update: dt.datetime | None, this_update: dt.datetime | None = None,
                   responses_empty: bool = False, with_response_bytes: bool = True) -> bytes:
    """Build a minimal RFC 6960 OCSPResponse DER carrying one SingleResponse."""
    this_update = this_update or (NOW - dt.timedelta(hours=1))

    single_parts = [
        _seq(),                       # certID
        _tlv(0x80, b""),              # certStatus: good [0] IMPLICIT NULL
        _gen_time(this_update),       # thisUpdate
    ]
    if next_update is not None:
        single_parts.append(_tlv(0xA0, _gen_time(next_update)))   # nextUpdate [0] EXPLICIT

    responses = _seq() if responses_empty else _seq(_seq(*single_parts))
    tbs = _seq(
        _tlv(0xA1, b""),              # responderID byName [1]
        _gen_time(NOW - dt.timedelta(hours=2)),   # producedAt
        responses,
    )
    basic = _seq(tbs)
    parts = [_tlv(0x0A, b"\x00")]     # responseStatus = successful
    if with_response_bytes:
        parts.append(_tlv(0xA0, _seq(_OCSP_BASIC_OID, _tlv(0x04, basic))))
    return _seq(*parts)


def _staple(next_update: dt.datetime | None = None) -> str:
    return _b64u(_ocsp_response(next_update or (NOW + dt.timedelta(hours=6))))


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _cert(roles: list[str] | None = None, caps: list[str] | None = None,
          roles_der: bytes | None = None, caps_der: bytes | None = None) -> x509.Certificate:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "phase3-test")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(NOW - dt.timedelta(days=1))
        .not_valid_after(NOW + dt.timedelta(days=30))
    )
    if roles is not None or roles_der is not None:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(
                NpsX509Oids.ID_NPS_NODE_ROLES,
                roles_der if roles_der is not None else _utf8_sequence(roles or []),
            ),
            critical=False,
        )
    if caps is not None or caps_der is not None:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(
                NpsX509Oids.ID_NPS_CAPABILITIES,
                caps_der if caps_der is not None else _utf8_sequence(caps or []),
            ),
            critical=False,
        )
    return builder.sign(private_key=key, algorithm=hashes.SHA256())


def _frame(node_roles: tuple[str, ...] | None = None,
           capabilities: tuple[str, ...] = ("nwp:query",),
           ocsp_staple: str | None = None) -> IdentFrame:
    """Brief B §6 baseline IdentFrame."""
    return IdentFrame(
        nid="urn:nps:agent:ca.example.com:p3-001",
        pub_key="ed25519:AAAA",
        capabilities=capabilities,
        scope={"nodes": ["nwp://example.com/*"]},
        issued_by="urn:nps:org:example.com",
        issued_at="2026-07-01T00:00:00Z",
        expires_at="2026-08-01T00:00:00Z",
        serial="0x01",
        signature="ed25519:test",
        node_roles=node_roles,
        ocsp_staple=ocsp_staple if ocsp_staple is not None else _staple(),
    )


# ── §6 the eight reference scenarios ──────────────────────────────────────────

class TestPhase3Enforcer:
    def test_subset_claims_with_fresh_staple_pass(self):
        result = NipPhase3Enforcer.enforce(
            _frame(node_roles=("memory",), capabilities=("nwp:query",)),
            _cert(roles=["memory", "anchor"], caps=["nwp:query", "nwp:action"]),
            now=NOW,
        )
        assert result.valid

    def test_unattested_role_fails_with_node_roles_mismatch(self):
        result = NipPhase3Enforcer.enforce(
            _frame(node_roles=("memory", "orchestrator")), _cert(roles=["memory"]), now=NOW)

        assert not result.valid
        assert result.error_code == error_codes.CERT_NODE_ROLES_MISMATCH
        assert result.step_failed == 3
        assert "orchestrator" in result.message

    def test_unattested_capability_fails_with_capabilities_exceeded(self):
        result = NipPhase3Enforcer.enforce(
            _frame(capabilities=("nwp:query", "nop:orchestrate")),
            _cert(caps=["nwp:query"]), now=NOW)

        assert result.error_code == error_codes.CERT_CAPABILITIES_EXCEEDED
        assert "nop:orchestrate" in result.message

    def test_no_extensions_means_attribute_checks_do_not_apply(self):
        result = NipPhase3Enforcer.enforce(
            _frame(node_roles=("anything",), capabilities=("nwp:anything",)),
            _cert(), now=NOW)
        assert result.valid

    def test_missing_staple_fails(self):
        result = NipPhase3Enforcer.enforce(_frame(ocsp_staple=""), _cert(), now=NOW)
        assert result.error_code == error_codes.OCSP_STAPLE_EXPIRED
        assert "none was supplied" in result.message

    def test_expired_staple_fails(self):
        result = NipPhase3Enforcer.enforce(
            _frame(ocsp_staple=_staple(NOW - dt.timedelta(minutes=1))), _cert(), now=NOW)
        assert result.error_code == error_codes.OCSP_STAPLE_EXPIRED
        assert "elapsed" in result.message

    def test_malformed_staple_fails_closed(self):
        result = NipPhase3Enforcer.enforce(
            _frame(ocsp_staple="bm90LWFuLW9jc3A"), _cert(), now=NOW)
        assert result.error_code == error_codes.OCSP_STAPLE_EXPIRED
        assert "could not be parsed" in result.message

    def test_utf8_sequence_extension_parses(self):
        cert = _cert(roles=["memory", "anchor"])
        assert NipPhase3Enforcer.read_utf8_sequence_extension(
            cert, NpsX509Oids.ID_NPS_NODE_ROLES) == ["memory", "anchor"]
        # Absent extension is None, NOT [] — that difference is the "does not apply" rule.
        assert NipPhase3Enforcer.read_utf8_sequence_extension(
            cert, NpsX509Oids.ID_NPS_CAPABILITIES) is None


# ── Additional cases the ports SHOULD add ─────────────────────────────────────

class TestPhase3EdgeCases:
    def test_malformed_role_extension_is_treated_as_an_empty_attestation(self):
        cert = _cert(roles_der=b"\xff\xff\xff")
        assert NipPhase3Enforcer.read_utf8_sequence_extension(
            cert, NpsX509Oids.ID_NPS_NODE_ROLES) == []
        result = NipPhase3Enforcer.enforce(_frame(node_roles=("memory",)), cert, now=NOW)
        assert result.error_code == error_codes.CERT_NODE_ROLES_MISMATCH

    def test_present_but_empty_extension_rejects_any_claim(self):
        result = NipPhase3Enforcer.enforce(
            _frame(capabilities=("nwp:query",)), _cert(caps=[]), now=NOW)
        assert result.error_code == error_codes.CERT_CAPABILITIES_EXCEEDED

    def test_null_node_roles_is_an_empty_set_and_always_a_subset(self):
        result = NipPhase3Enforcer.enforce(
            _frame(node_roles=None, capabilities=()), _cert(roles=["memory"], caps=[]), now=NOW)
        assert result.valid

    def test_role_comparison_is_ordinal_not_case_insensitive(self):
        result = NipPhase3Enforcer.enforce(
            _frame(node_roles=("Memory",)), _cert(roles=["memory"]), now=NOW)
        assert result.error_code == error_codes.CERT_NODE_ROLES_MISMATCH

    def test_next_update_exactly_now_fails(self):
        result = NipPhase3Enforcer.enforce(
            _frame(ocsp_staple=_staple(NOW)), _cert(), now=NOW)
        assert result.error_code == error_codes.OCSP_STAPLE_EXPIRED

    def test_roles_are_evaluated_before_capabilities(self):
        # Both over-claimed: the roles failure must win.
        result = NipPhase3Enforcer.enforce(
            _frame(node_roles=("bogus",), capabilities=("bogus:cap",)),
            _cert(roles=["memory"], caps=["nwp:query"]), now=NOW)
        assert result.error_code == error_codes.CERT_NODE_ROLES_MISMATCH

    def test_non_base64url_staple_fails_closed(self):
        result = NipPhase3Enforcer.enforce(
            _frame(ocsp_staple="!!! not base64 !!!"), _cert(), now=NOW)
        assert result.error_code == error_codes.OCSP_STAPLE_EXPIRED
        assert "not valid base64url" in result.message

    def test_default_clock_is_used_when_now_is_omitted(self):
        far_future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)
        assert NipPhase3Enforcer.enforce(
            _frame(ocsp_staple=_b64u(_ocsp_response(far_future, this_update=far_future))),
            _cert()).valid

    def test_argument_validation(self):
        with pytest.raises(ValueError):
            NipPhase3Enforcer.enforce(None, _cert())
        with pytest.raises(ValueError):
            NipPhase3Enforcer.enforce(_frame(), None)


class TestOcspDerWalk:
    def test_reads_the_next_update(self):
        expected = NOW + dt.timedelta(hours=6)
        ok, value = NipPhase3Enforcer.try_get_ocsp_next_update(_ocsp_response(expected))
        assert ok and value == expected

    def test_absent_response_bytes_returns_false(self):
        ok, _ = NipPhase3Enforcer.try_get_ocsp_next_update(
            _ocsp_response(NOW, with_response_bytes=False))
        assert not ok

    def test_empty_responses_returns_false(self):
        ok, _ = NipPhase3Enforcer.try_get_ocsp_next_update(
            _ocsp_response(NOW, responses_empty=True))
        assert not ok

    def test_absent_next_update_returns_false(self):
        ok, _ = NipPhase3Enforcer.try_get_ocsp_next_update(_ocsp_response(None))
        assert not ok

    def test_garbage_returns_false(self):
        assert NipPhase3Enforcer.try_get_ocsp_next_update(b"\x30\x82\xff\xff")[0] is False
        assert NipPhase3Enforcer.try_get_ocsp_next_update(b"")[0] is False

    def test_long_form_lengths_are_supported(self):
        # A >127-byte SEQUENCE OF UTF8String exercises the multi-byte length branch.
        values = [f"role-{i:03d}" for i in range(30)]
        cert = _cert(roles_der=_utf8_sequence(values))
        assert NipPhase3Enforcer.read_utf8_sequence_extension(
            cert, NpsX509Oids.ID_NPS_NODE_ROLES) == values

    def test_non_utc_generalized_time_is_rejected(self):
        bad = _seq(
            _tlv(0x0A, b"\x00"),
            _tlv(0xA0, _seq(_OCSP_BASIC_OID, _tlv(0x04, _seq(_seq(
                _tlv(0xA1, b""),
                _tlv(0x18, b"20260705120000"),      # no trailing 'Z'
                _seq(),
            ))))),
        )
        assert NipPhase3Enforcer.try_get_ocsp_next_update(bad)[0] is False


# ── IdentFrame.node_roles wire behaviour ──────────────────────────────────────

class TestIdentFrameNodeRoles:
    def test_node_roles_round_trip_on_the_wire(self):
        frame = _frame(node_roles=("memory", "anchor"))
        d = frame.to_dict()
        assert d["node_roles"] == ["memory", "anchor"]
        assert IdentFrame.from_dict(d).node_roles == ("memory", "anchor")

    def test_node_roles_is_excluded_from_the_signed_payload(self):
        with_roles = _frame(node_roles=("memory",))
        without = _frame(node_roles=None)
        assert "node_roles" not in with_roles.unsigned_dict()
        # ...so adding node_roles cannot invalidate a previously-signed frame.
        assert with_roles.unsigned_dict() == without.unsigned_dict()

    def test_unset_node_roles_is_omitted(self):
        assert "node_roles" not in _frame(node_roles=None).to_dict()


class TestPhase3Flag:
    def test_defaults_to_false(self):
        assert NipVerifierOptions().phase3_enforcement is False

    def test_error_code_status_mapping_asymmetry(self):
        from nps_sdk.nip.error_codes import NIP_ERROR_TO_NPS_STATUS

        assert NIP_ERROR_TO_NPS_STATUS[
            error_codes.CERT_CAPABILITIES_EXCEEDED] == "NPS-AUTH-FORBIDDEN"
        assert NIP_ERROR_TO_NPS_STATUS[
            error_codes.CERT_NODE_ROLES_MISMATCH] == "NPS-CLIENT-BAD-FRAME"
