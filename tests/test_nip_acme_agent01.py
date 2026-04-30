# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests for NPS-RFC-0002 ACME `agent-01` challenge — full round-trip + tampered."""

from __future__ import annotations

import asyncio
import base64
import datetime
import json

import httpx
import pytest

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from nps_sdk.nip import error_codes
from nps_sdk.nip.acme import jws as _jws
from nps_sdk.nip.acme import wire
from nps_sdk.nip.acme.client import AcmeClient
from nps_sdk.nip.acme.messages import (
    Directory, Identifier, NewAccountPayload, NewOrderPayload,
    Authorization, Order, ProblemDetail, ChallengeRespondPayload,
)
from nps_sdk.nip.acme.server import AcmeServer
from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.x509 import NipX509Builder, NipX509Verifier


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


@pytest.fixture
def ca_setup():
    """Build CA keypair + self-signed root."""
    ca_priv = Ed25519PrivateKey.generate()
    root = NipX509Builder.issue_root(
        "urn:nps:ca:acme-test", ca_priv,
        _now() - datetime.timedelta(minutes=1),
        _now() + datetime.timedelta(days=365),
        1,
    )
    return ca_priv, root


# ── Test 1: round-trip ───────────────────────────────────────────────────────

async def test_issue_agent_cert_round_trip_returns_valid_pem_chain(ca_setup):
    """Full flow: client → server → issued PEM → NipX509Verifier accepts."""
    ca_priv, root = ca_setup
    agent_priv = Ed25519PrivateKey.generate()
    agent_nid = "urn:nps:agent:acme-roundtrip:1"

    server = AcmeServer(
        ca_nid="urn:nps:ca:acme-test",
        ca_priv_key=ca_priv,
        ca_root_cert=root,
        cert_validity=datetime.timedelta(days=30),
    )
    with server:
        async with httpx.AsyncClient(timeout=10.0) as http:
            client = AcmeClient(http, server.directory_url, agent_priv)
            pem = await client.issue_agent_cert(agent_nid)

        assert "BEGIN CERTIFICATE" in pem

        # Parse PEM chain and verify with NipX509Verifier.
        chain_certs = _parse_pem_chain(pem)
        assert len(chain_certs) >= 1

        chain_b64u = [_b64u(c.public_bytes(serialization.Encoding.DER)) for c in chain_certs]
        result = NipX509Verifier.verify(
            cert_chain_b64u_der=chain_b64u,
            asserted_nid=agent_nid,
            asserted_assurance_level=AssuranceLevel.ANONYMOUS,
            trusted_root_certs=[root],
        )
        assert result.valid, f"err={result.error_code} msg={result.message}"
        leaf_cn = result.leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        assert leaf_cn == agent_nid


# ── Test 2: tampered signature ───────────────────────────────────────────────

async def test_respond_agent01_tampered_signature_server_returns_challenge_failed(ca_setup):
    """Forged challenge signature triggers NIP-ACME-CHALLENGE-FAILED."""
    ca_priv, root = ca_setup
    agent_priv = Ed25519PrivateKey.generate()
    agent_nid = "urn:nps:agent:acme-tampered:1"

    server = AcmeServer(
        ca_nid="urn:nps:ca:acme-test",
        ca_priv_key=ca_priv,
        ca_root_cert=root,
        cert_validity=datetime.timedelta(days=30),
    )
    with server:
        async with httpx.AsyncClient(timeout=10.0) as http:
            # Drive newNonce/newAccount/newOrder by hand so we can splice in a forged challenge.
            dir_resp = await http.get(server.directory_url)
            directory = Directory.from_dict(dir_resp.json())

            nonce_resp = await http.head(directory.new_nonce)
            nonce = nonce_resp.headers["Replay-Nonce"]

            jwk = _jws.jwk_from_public_key(agent_priv.public_key())
            acct_env = _jws.sign(
                _jws.ProtectedHeader(_jws.ALG_EDDSA, nonce, directory.new_account, jwk=jwk),
                NewAccountPayload(terms_of_service_agreed=True),
                agent_priv,
            )
            acct_resp = await http.post(
                directory.new_account,
                headers={"Content-Type": wire.CONTENT_TYPE_JOSE_JSON},
                json=acct_env.to_dict(),
            )
            assert acct_resp.status_code == 201
            account_url = acct_resp.headers["Location"]
            nonce = acct_resp.headers["Replay-Nonce"]

            order_env = _jws.sign(
                _jws.ProtectedHeader(_jws.ALG_EDDSA, nonce, directory.new_order, kid=account_url),
                NewOrderPayload(identifiers=[Identifier(type=wire.IDENTIFIER_TYPE_NID, value=agent_nid)]),
                agent_priv,
            )
            order_resp = await http.post(
                directory.new_order,
                headers={"Content-Type": wire.CONTENT_TYPE_JOSE_JSON},
                json=order_env.to_dict(),
            )
            assert order_resp.status_code == 201
            order = Order.from_dict(order_resp.json())
            nonce = order_resp.headers["Replay-Nonce"]

            # POST-as-GET on authz to discover challenge.
            authz_url = order.authorizations[0]
            authz_env = _jws.sign(
                _jws.ProtectedHeader(_jws.ALG_EDDSA, nonce, authz_url, kid=account_url),
                None, agent_priv,
            )
            authz_resp = await http.post(
                authz_url,
                headers={"Content-Type": wire.CONTENT_TYPE_JOSE_JSON},
                json=authz_env.to_dict(),
            )
            assert authz_resp.status_code == 200
            authz = Authorization.from_dict(authz_resp.json())
            nonce = authz_resp.headers["Replay-Nonce"]

            challenge = next(c for c in authz.challenges if c.type == wire.CHALLENGE_AGENT_01)

            # ★ Tampered: sign challenge token with a *different* keypair.
            forger = Ed25519PrivateKey.generate()
            forged_sig = _b64u(forger.sign(challenge.token.encode("utf-8")))

            # Submit JWS under the registered account JWK.
            chall_env = _jws.sign(
                _jws.ProtectedHeader(_jws.ALG_EDDSA, nonce, challenge.url, kid=account_url),
                ChallengeRespondPayload(agent_signature=forged_sig),
                agent_priv,
            )
            chall_resp = await http.post(
                challenge.url,
                headers={"Content-Type": wire.CONTENT_TYPE_JOSE_JSON},
                json=chall_env.to_dict(),
            )
            assert chall_resp.status_code == 400
            problem = ProblemDetail.from_dict(chall_resp.json())
            assert problem.type == error_codes.ACME_CHALLENGE_FAILED


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_pem_chain(pem: str) -> list[x509.Certificate]:
    """Split a concatenated PEM blob and return all X.509 certs in order."""
    certs: list[x509.Certificate] = []
    marker_begin = "-----BEGIN CERTIFICATE-----"
    marker_end   = "-----END CERTIFICATE-----"
    idx = 0
    while True:
        begin = pem.find(marker_begin, idx)
        if begin < 0: break
        end = pem.find(marker_end, begin)
        if end < 0: break
        block = pem[begin: end + len(marker_end)]
        certs.append(x509.load_pem_x509_certificate(block.encode("ascii")))
        idx = end + len(marker_end)
    return certs
