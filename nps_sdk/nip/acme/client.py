# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
ACME client implementing the `agent-01` challenge type per NPS-RFC-0002 §4.4.

Flow: newNonce → newAccount → newOrder → fetch authz → sign challenge token →
finalize with CSR → fetch leaf cert.

Usage::

    async with httpx.AsyncClient() as http:
        client = AcmeClient(http, directory_url, account_keypair)
        pem = await client.issue_agent_cert("urn:nps:agent:foo:1")
"""

from __future__ import annotations

import base64

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.x509.oid import NameOID

from nps_sdk.nip.acme import jws as _jws
from nps_sdk.nip.acme import wire
from nps_sdk.nip.acme.messages import (
    Authorization, Directory, FinalizePayload, Identifier,
    NewAccountPayload, NewOrderPayload, Order, ChallengeRespondPayload,
)


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class AcmeClient:
    """ACME client driving the full agent-01 issuance flow."""

    def __init__(
        self,
        http:        httpx.AsyncClient,
        directory_url: str,
        priv_key:    Ed25519PrivateKey,
    ) -> None:
        self._http          = http
        self._directory_url = directory_url
        self._priv_key      = priv_key
        self._pub_key       = priv_key.public_key()
        self._directory:    Directory | None = None
        self._account_url:  str | None       = None
        self._last_nonce:   str | None       = None

    @property
    def account_url(self) -> str | None:
        return self._account_url

    async def issue_agent_cert(self, nid: str) -> str:
        """Drive the full agent-01 flow. Returns issued PEM cert chain."""
        await self._ensure_directory()
        if self._account_url is None:
            await self._new_account()
        order = await self._new_order(nid)
        authz = await self._fetch_authz(order.authorizations[0])
        await self._respond_agent01(authz)
        finalized = await self._finalize_order(order, nid)
        return await self._download_pem(finalized.certificate)

    # ── Stages ───────────────────────────────────────────────────────────────

    async def _ensure_directory(self) -> None:
        if self._directory is not None:
            return
        resp = await self._http.get(self._directory_url)
        _ensure_success(resp)
        self._directory = Directory.from_dict(resp.json())
        await self._refresh_nonce()

    async def _refresh_nonce(self) -> None:
        assert self._directory is not None
        resp = await self._http.head(self._directory.new_nonce)
        _ensure_success(resp)
        self._last_nonce = resp.headers["Replay-Nonce"]

    async def _new_account(self) -> None:
        assert self._directory is not None and self._last_nonce is not None
        jwk = _jws.jwk_from_public_key(self._pub_key)
        header = _jws.ProtectedHeader(
            _jws.ALG_EDDSA, self._last_nonce, self._directory.new_account, jwk=jwk)
        env = _jws.sign(header, NewAccountPayload(terms_of_service_agreed=True), self._priv_key)
        resp = await self._post(self._directory.new_account, env)
        _ensure_success(resp)
        self._account_url = resp.headers["Location"]
        self._capture_nonce(resp)

    async def _new_order(self, nid: str) -> Order:
        assert self._directory is not None and self._last_nonce is not None
        header = _jws.ProtectedHeader(
            _jws.ALG_EDDSA, self._last_nonce, self._directory.new_order, kid=self._account_url)
        payload = NewOrderPayload(
            identifiers=[Identifier(type=wire.IDENTIFIER_TYPE_NID, value=nid)])
        env = _jws.sign(header, payload, self._priv_key)
        resp = await self._post(self._directory.new_order, env)
        _ensure_success(resp)
        self._capture_nonce(resp)
        return Order.from_dict(resp.json())

    async def _fetch_authz(self, url: str) -> Authorization:
        # POST-as-GET (RFC 8555 §6.3).
        header = _jws.ProtectedHeader(
            _jws.ALG_EDDSA, self._last_nonce, url, kid=self._account_url)
        env = _jws.sign(header, None, self._priv_key)
        resp = await self._post(url, env)
        _ensure_success(resp)
        self._capture_nonce(resp)
        return Authorization.from_dict(resp.json())

    async def _respond_agent01(self, authz: Authorization) -> None:
        challenge = next(
            (c for c in authz.challenges if c.type == wire.CHALLENGE_AGENT_01),
            None)
        if challenge is None:
            raise RuntimeError("authz has no agent-01 challenge")

        # Sign challenge token with the account/NID private key.
        agent_sig = _b64u(self._priv_key.sign(challenge.token.encode("utf-8")))

        header = _jws.ProtectedHeader(
            _jws.ALG_EDDSA, self._last_nonce, challenge.url, kid=self._account_url)
        env = _jws.sign(header, ChallengeRespondPayload(agent_signature=agent_sig), self._priv_key)
        resp = await self._post(challenge.url, env)
        _ensure_success(resp)
        self._capture_nonce(resp)

    async def _finalize_order(self, order: Order, nid: str) -> Order:
        csr_der = self._build_csr(nid)
        header = _jws.ProtectedHeader(
            _jws.ALG_EDDSA, self._last_nonce, order.finalize, kid=self._account_url)
        env = _jws.sign(header, FinalizePayload(csr=_b64u(csr_der)), self._priv_key)
        resp = await self._post(order.finalize, env)
        _ensure_success(resp)
        self._capture_nonce(resp)
        return Order.from_dict(resp.json())

    async def _download_pem(self, cert_url: str | None) -> str:
        if cert_url is None:
            raise RuntimeError("order has no certificate URL after finalize")
        header = _jws.ProtectedHeader(
            _jws.ALG_EDDSA, self._last_nonce, cert_url, kid=self._account_url)
        env = _jws.sign(header, None, self._priv_key)
        resp = await self._post(cert_url, env)
        _ensure_success(resp)
        self._capture_nonce(resp)
        return resp.text

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _post(self, url: str, env: _jws.Envelope) -> httpx.Response:
        return await self._http.post(
            url,
            headers={"Content-Type": wire.CONTENT_TYPE_JOSE_JSON},
            json=env.to_dict(),
        )

    def _capture_nonce(self, resp: httpx.Response) -> None:
        nonce = resp.headers.get("Replay-Nonce")
        if nonce:
            self._last_nonce = nonce

    def _build_csr(self, nid: str) -> bytes:
        # Build CSR with subject CN = NID, SAN URI = NID, signed with account key.
        builder = (
            x509.CertificateSigningRequestBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nid)]))
            .add_extension(
                x509.SubjectAlternativeName([x509.UniformResourceIdentifier(nid)]),
                critical=False,
            )
        )
        csr = builder.sign(private_key=self._priv_key, algorithm=None)
        return csr.public_bytes(serialization.Encoding.DER)


def _ensure_success(resp: httpx.Response) -> None:
    if not (200 <= resp.status_code < 300):
        raise RuntimeError(f"ACME {resp.url} HTTP {resp.status_code}: {resp.text}")
