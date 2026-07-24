# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Tests for the NPS-3 §7 six-step NipIdentVerifier and the TrustFrameValidator,
mirroring the .NET reference behaviour (each step's pass/fail, OCSP fail-open
vs fail-closed, scope pattern matching, and TrustFrame validation).
"""

from __future__ import annotations

import base64
import datetime
import json

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nps_sdk.nip import error_codes
from nps_sdk.nip.frames import IdentFrame, TrustFrame
from nps_sdk.nip.trust_validator import TrustFrameValidationContext, TrustFrameValidator
from nps_sdk.nip.verifier import (
    NipCertRecord,
    NipIdentVerifier,
    NipVerifierOptions,
    NipVerifyContext,
    nwp_path_matches,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _pub_key_string(pub) -> str:
    der = pub.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return f"ed25519:{_b64u(der)}"


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


_CA_NID = "urn:nps:org:test"


def _build_frame(
    ca_priv: Ed25519PrivateKey,
    *,
    nid: str = "urn:nps:agent:alice:1",
    capabilities: tuple[str, ...] = (),
    scope: object | None = None,
    expires_at: str | None = None,
    serial: str = "0x0A",
    tamper: bool = False,
) -> IdentFrame:
    """Build a v1 IdentFrame signed by *ca_priv* (matching unsigned_dict())."""
    agent = Ed25519PrivateKey.generate()
    now = _iso(_now())
    if expires_at is None:
        expires_at = _iso(_now() + datetime.timedelta(days=30))
    if scope is None:
        scope = {"nodes": ["nwp://api.myapp.com/*"]}

    pub_key_str = _pub_key_string(agent.public_key())
    unsigned: dict = {
        "frame":        "0x20",
        "nid":          nid,
        "pub_key":      pub_key_str,
        "capabilities": list(capabilities),
        "scope":        scope,
        "issued_by":    _CA_NID,
        "issued_at":    now,
        "expires_at":   expires_at,
        "serial":       serial,
    }
    canonical = json.dumps(unsigned, separators=(",", ":"), sort_keys=True)
    sig_bytes = ca_priv.sign(canonical.encode("utf-8"))
    if tamper:
        sig_bytes = bytes(b ^ 0xFF for b in sig_bytes)
    sig_str = "ed25519:" + _b64u(sig_bytes)

    return IdentFrame(
        nid=nid,
        pub_key=pub_key_str,
        capabilities=capabilities,
        scope=scope,
        issued_by=_CA_NID,
        issued_at=now,
        expires_at=expires_at,
        serial=serial,
        signature=sig_str,
    )


@pytest.fixture
def ca() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def opts(ca) -> NipVerifierOptions:
    return NipVerifierOptions(
        trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
    )


# ── Step 1: expiry ─────────────────────────────────────────────────────────────

class TestExpiry:
    async def test_valid_when_not_expired(self, ca, opts):
        frame = _build_frame(ca)
        result = await NipIdentVerifier(opts).verify(frame)
        assert result.valid

    async def test_fail_when_expired(self, ca, opts):
        frame = _build_frame(ca, expires_at=_iso(_now() - datetime.timedelta(days=1)))
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 1
        assert result.error_code == error_codes.CERT_EXPIRED

    async def test_fail_when_expires_at_unparseable(self, ca, opts):
        frame = _build_frame(ca, expires_at="not-a-date")
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 1

    async def test_as_of_clock_override(self, ca, opts):
        # Frame expires 30 days out; as_of in the far future → expired.
        frame = _build_frame(ca)
        ctx = NipVerifyContext(as_of=_now() + datetime.timedelta(days=365))
        result = await NipIdentVerifier(opts).verify(frame, ctx)
        assert not result.valid
        assert result.step_failed == 1


# ── Step 2: trusted issuer ─────────────────────────────────────────────────────

class TestTrustedIssuer:
    async def test_fail_when_issuer_unknown(self, ca):
        frame = _build_frame(ca)
        opts = NipVerifierOptions(trusted_issuers={"urn:nps:org:other": "ed25519:AAAA"})
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 2
        assert result.error_code == error_codes.CERT_UNTRUSTED_ISSUER

    async def test_backward_compat_alias(self, ca):
        frame = _build_frame(ca)
        opts = NipVerifierOptions(
            trusted_ca_public_keys={_CA_NID: _pub_key_string(ca.public_key())},
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert result.valid


# ── Step 3: signature ──────────────────────────────────────────────────────────

class TestSignature:
    async def test_fail_when_signature_tampered(self, ca, opts):
        frame = _build_frame(ca, tamper=True)
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 3
        assert result.error_code == error_codes.CERT_SIGNATURE_INVALID

    async def test_fail_when_signed_by_wrong_key(self, opts):
        wrong_ca = Ed25519PrivateKey.generate()
        frame = _build_frame(wrong_ca)
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 3


# ── Step 4: revocation ─────────────────────────────────────────────────────────

class TestRevocation:
    async def test_local_crl_revokes(self, ca):
        frame = _build_frame(ca, serial="0xDEAD")
        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            local_revoked_serials=frozenset({"0xDEAD"}),
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 4
        assert result.error_code == error_codes.CERT_REVOKED

    async def test_passthrough_when_no_source(self, ca, opts):
        frame = _build_frame(ca)
        result = await NipIdentVerifier(opts).verify(frame)
        assert result.valid

    async def test_callback_can_reject(self, ca):
        frame = _build_frame(ca)

        async def cb(f):
            from nps_sdk.nip.verifier import NipIdentVerifyResult
            return NipIdentVerifyResult.fail(4, error_codes.CERT_REVOKED, "callback revoked")

        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            revocation_check=cb,
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 4

    async def test_callback_returns_none_continues(self, ca):
        frame = _build_frame(ca)

        async def cb(f):
            return None

        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            revocation_check=cb,
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert result.valid

    async def test_store_revoked_record_rejects(self, ca):
        frame = _build_frame(ca, serial="0xBEEF")

        class Store:
            async def get_by_serial(self, serial):
                return NipCertRecord(serial=serial, revoked_at=_iso(_now()), revoke_reason="key_compromise")

        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            revocation_store=Store(),
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 4
        assert result.error_code == error_codes.CERT_REVOKED

    async def test_store_unrevoked_record_passes(self, ca):
        frame = _build_frame(ca, serial="0xBEEF")

        class Store:
            async def get_by_serial(self, serial):
                return NipCertRecord(serial=serial, revoked_at=None)

        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            revocation_store=Store(),
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert result.valid

    @respx.mock
    async def test_ocsp_valid_passes(self, ca):
        frame = _build_frame(ca)
        respx.get(f"https://ca.example.com/ocsp/{frame.nid}").mock(
            return_value=httpx.Response(200, json={"valid": True}))
        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            ocsp_url="https://ca.example.com/ocsp",
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert result.valid

    @respx.mock
    async def test_ocsp_revoked_fails(self, ca):
        frame = _build_frame(ca)
        respx.get(f"https://ca.example.com/ocsp/{frame.nid}").mock(
            return_value=httpx.Response(200, json={"valid": False, "error_code": error_codes.CERT_REVOKED}))
        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            ocsp_url="https://ca.example.com/ocsp",
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 4
        assert result.error_code == error_codes.CERT_REVOKED

    @respx.mock
    async def test_ocsp_non_2xx_unavailable(self, ca):
        frame = _build_frame(ca)
        respx.get(f"https://ca.example.com/ocsp/{frame.nid}").mock(
            return_value=httpx.Response(503))
        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            ocsp_url="https://ca.example.com/ocsp",
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 4
        assert result.error_code == error_codes.OCSP_UNAVAILABLE

    @respx.mock
    async def test_ocsp_transport_error_fail_closed(self, ca):
        frame = _build_frame(ca)
        respx.get(f"https://ca.example.com/ocsp/{frame.nid}").mock(
            side_effect=httpx.ConnectError("boom"))
        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            ocsp_url="https://ca.example.com/ocsp",
            ocsp_fail_open=False,
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 4
        assert result.error_code == error_codes.OCSP_UNAVAILABLE

    @respx.mock
    async def test_ocsp_transport_error_fail_open(self, ca):
        frame = _build_frame(ca)
        respx.get(f"https://ca.example.com/ocsp/{frame.nid}").mock(
            side_effect=httpx.ConnectError("boom"))
        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            ocsp_url="https://ca.example.com/ocsp",
            ocsp_fail_open=True,
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert result.valid

    @respx.mock
    async def test_local_crl_precedes_ocsp(self, ca):
        # Local CRL hit must short-circuit before any OCSP call.
        frame = _build_frame(ca, serial="0xCAFE")
        route = respx.get(f"https://ca.example.com/ocsp/{frame.nid}").mock(
            return_value=httpx.Response(200, json={"valid": True}))
        opts = NipVerifierOptions(
            trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
            local_revoked_serials=frozenset({"0xCAFE"}),
            ocsp_url="https://ca.example.com/ocsp",
        )
        result = await NipIdentVerifier(opts).verify(frame)
        assert not result.valid
        assert result.step_failed == 4
        assert not route.called

    @respx.mock
    async def test_ocsp_injected_client(self, ca):
        frame = _build_frame(ca)
        respx.get(f"https://ca.example.com/ocsp/{frame.nid}").mock(
            return_value=httpx.Response(200, json={"valid": True}))
        async with httpx.AsyncClient() as client:
            opts = NipVerifierOptions(
                trusted_issuers={_CA_NID: _pub_key_string(ca.public_key())},
                ocsp_url="https://ca.example.com/ocsp",
                http_client=client,
            )
            result = await NipIdentVerifier(opts).verify(frame)
        assert result.valid


# ── Step 5: capabilities ───────────────────────────────────────────────────────

class TestCapabilities:
    async def test_pass_when_superset(self, ca, opts):
        frame = _build_frame(ca, capabilities=("nwp:query", "nwp:write"))
        ctx = NipVerifyContext(required_capabilities=["nwp:query"])
        result = await NipIdentVerifier(opts).verify(frame, ctx)
        assert result.valid

    async def test_fail_when_missing(self, ca, opts):
        frame = _build_frame(ca, capabilities=("nwp:query",))
        ctx = NipVerifyContext(required_capabilities=["nwp:query", "nwp:admin"])
        result = await NipIdentVerifier(opts).verify(frame, ctx)
        assert not result.valid
        assert result.step_failed == 5
        assert result.error_code == error_codes.CERT_CAPABILITY_MISSING

    async def test_skipped_when_none_required(self, ca, opts):
        frame = _build_frame(ca, capabilities=())
        result = await NipIdentVerifier(opts).verify(frame, NipVerifyContext())
        assert result.valid


# ── Step 6: scope ──────────────────────────────────────────────────────────────

class TestScope:
    async def test_prefix_match(self, ca, opts):
        frame = _build_frame(ca, scope={"nodes": ["nwp://api.myapp.com/*"]})
        ctx = NipVerifyContext(target_node_path="nwp://api.myapp.com/products")
        result = await NipIdentVerifier(opts).verify(frame, ctx)
        assert result.valid

    async def test_fail_out_of_scope(self, ca, opts):
        frame = _build_frame(ca, scope={"nodes": ["nwp://api.myapp.com/*"]})
        ctx = NipVerifyContext(target_node_path="nwp://other.com/x")
        result = await NipIdentVerifier(opts).verify(frame, ctx)
        assert not result.valid
        assert result.step_failed == 6
        assert result.error_code == error_codes.CERT_SCOPE_VIOLATION

    async def test_missing_nodes_field(self, ca, opts):
        frame = _build_frame(ca, scope={})
        ctx = NipVerifyContext(target_node_path="nwp://api.myapp.com/x")
        result = await NipIdentVerifier(opts).verify(frame, ctx)
        assert not result.valid
        assert result.step_failed == 6

    async def test_scope_skipped_when_no_target(self, ca, opts):
        frame = _build_frame(ca, scope={})
        result = await NipIdentVerifier(opts).verify(frame)
        assert result.valid


# ── Path matcher unit tests (parity with .NET NwpPathMatches) ──────────────────

class TestPathMatches:
    def test_bare_star_matches_all(self):
        assert nwp_path_matches("*", "nwp://anything/here")

    def test_prefix_boundary(self):
        assert nwp_path_matches("nwp://api.myapp.com/*", "nwp://api.myapp.com/products")
        assert nwp_path_matches("nwp://api.myapp.com/*", "nwp://api.myapp.com")
        # Boundary must be at '/': a sibling prefix must NOT match.
        assert not nwp_path_matches("nwp://api.myapp.com/*", "nwp://api.myapp.com.evil/x")

    def test_exact_case_insensitive(self):
        assert nwp_path_matches("nwp://Api.MyApp.com/x", "nwp://api.myapp.com/x")
        assert not nwp_path_matches("nwp://api.myapp.com/x", "nwp://api.myapp.com/y")


# ── TrustFrameValidator ────────────────────────────────────────────────────────

def _trust_frame(**overrides) -> TrustFrame:
    base = dict(
        grantor_nid="urn:nps:org:root-ca",
        grantee_ca="urn:nps:org:sub-ca",
        trust_scope=("nwp:query", "nwp:write"),
        nodes=("nwp://api.myapp.com/*",),
        issued_at=_iso(_now()),
        expires_at=_iso(_now() + datetime.timedelta(days=30)),
        serial="0x01",
        signer_nid="urn:nps:org:root-ca",
        signature="ed25519:AAAA",
    )
    base.update(overrides)
    return TrustFrame(**base)


def _trust_ctx(**overrides) -> TrustFrameValidationContext:
    base = dict(
        trusted_grantors=frozenset({"urn:nps:org:root-ca"}),
        expected_grantee_ca="urn:nps:org:sub-ca",
    )
    base.update(overrides)
    return TrustFrameValidationContext(**base)


class TestTrustFrameValidator:
    def test_valid(self):
        result = TrustFrameValidator.validate(_trust_frame(), _trust_ctx())
        assert result.valid

    def test_missing_field(self):
        result = TrustFrameValidator.validate(_trust_frame(nodes=()), _trust_ctx())
        assert not result.valid
        assert result.step_failed == 3
        assert result.error_code == error_codes.TRUST_FRAME_INVALID

    def test_bad_issued_at(self):
        result = TrustFrameValidator.validate(_trust_frame(issued_at="nope"), _trust_ctx())
        assert not result.valid
        assert result.error_code == error_codes.TRUST_FRAME_INVALID

    def test_bad_expires_at(self):
        result = TrustFrameValidator.validate(_trust_frame(expires_at="nope"), _trust_ctx())
        assert not result.valid
        assert result.error_code == error_codes.TRUST_FRAME_INVALID

    def test_expired(self):
        result = TrustFrameValidator.validate(
            _trust_frame(expires_at=_iso(_now() - datetime.timedelta(days=1))), _trust_ctx())
        assert not result.valid
        assert result.error_code == error_codes.TRUST_FRAME_EXPIRED

    def test_untrusted_grantor(self):
        result = TrustFrameValidator.validate(
            _trust_frame(), _trust_ctx(trusted_grantors=frozenset({"urn:nps:org:someone-else"})))
        assert not result.valid
        assert result.error_code == error_codes.CERT_UNTRUSTED_ISSUER

    def test_grantee_mismatch(self):
        result = TrustFrameValidator.validate(
            _trust_frame(), _trust_ctx(expected_grantee_ca="urn:nps:org:wrong"))
        assert not result.valid
        assert result.error_code == error_codes.TRUST_FRAME_INVALID

    def test_capability_scope_exceeds(self):
        result = TrustFrameValidator.validate(
            _trust_frame(), _trust_ctx(required_capabilities=["nwp:query", "nwp:admin"]))
        assert not result.valid
        assert result.step_failed == 5
        assert result.error_code == error_codes.TRUST_FRAME_SCOPE_EXCEEDS_GRANTOR

    def test_capability_scope_ok(self):
        result = TrustFrameValidator.validate(
            _trust_frame(), _trust_ctx(required_capabilities=["nwp:query"]))
        assert result.valid

    def test_node_scope_covered(self):
        result = TrustFrameValidator.validate(
            _trust_frame(), _trust_ctx(target_node_path="nwp://api.myapp.com/x"))
        assert result.valid

    def test_node_scope_not_covered(self):
        result = TrustFrameValidator.validate(
            _trust_frame(), _trust_ctx(target_node_path="nwp://other.com/x"))
        assert not result.valid
        assert result.step_failed == 6
        assert result.error_code == error_codes.CERT_SCOPE_VIOLATION

    def test_as_of_clock_override(self):
        # Not-yet-expired frame, but as_of far future → expired.
        result = TrustFrameValidator.validate(
            _trust_frame(), _trust_ctx(as_of=_now() + datetime.timedelta(days=365)))
        assert not result.valid
        assert result.error_code == error_codes.TRUST_FRAME_EXPIRED
