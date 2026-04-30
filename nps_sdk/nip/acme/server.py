# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
In-process ACME server implementing the `agent-01` challenge for NPS-RFC-0002 §4.4.

Backed by stdlib `http.server.ThreadingHTTPServer` running in a daemon thread.
Suitable for tests and reference deployments. State is kept in memory.

Usage::

    server = AcmeServer(ca_nid, ca_priv_key, ca_root_cert,
                        cert_validity=datetime.timedelta(days=30))
    server.start()
    directory_url = server.directory_url
    ... drive client ...
    server.close()
"""

from __future__ import annotations

import base64
import datetime
import json
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nps_sdk.nip import error_codes
from nps_sdk.nip.acme import jws as _jws
from nps_sdk.nip.acme import wire
from nps_sdk.nip.acme.messages import (
    Account, Authorization, Challenge, ChallengeRespondPayload,
    Directory, FinalizePayload, Identifier, NewOrderPayload, Order,
    ProblemDetail, Status,
)
from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.x509.builder import LeafRole, NipX509Builder


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ── State dataclasses ────────────────────────────────────────────────────────

@dataclass
class _OrderState:
    id:              str
    identifier:      Identifier
    status:          str
    authz_id:        str
    finalize_url:    str
    account_url:     str
    certificate_url: str | None = None


@dataclass
class _AuthzState:
    id:            str
    identifier:    Identifier
    status:        str
    challenge_ids: list[str]
    account_url:   str


@dataclass
class _ChallengeState:
    id:           str
    type:         str
    status:       str
    token:        str
    authz_id:     str
    account_url:  str


@dataclass
class _ServerState:
    ca_nid:           str
    ca_priv_key:      Ed25519PrivateKey
    ca_root_cert:     x509.Certificate
    cert_validity:    datetime.timedelta
    base_url:         str = ""
    nonces:           set[str] = field(default_factory=set)
    account_jwks:     dict[str, _jws.Jwk] = field(default_factory=dict)
    orders:           dict[str, _OrderState] = field(default_factory=dict)
    authzs:           dict[str, _AuthzState] = field(default_factory=dict)
    challenges:       dict[str, _ChallengeState] = field(default_factory=dict)
    certs:            dict[str, str] = field(default_factory=dict)
    lock:             threading.Lock = field(default_factory=threading.Lock)

    def mint_nonce(self) -> str:
        n = base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode("ascii")
        with self.lock:
            self.nonces.add(n)
        return n

    def consume_nonce(self, nonce: str) -> bool:
        with self.lock:
            if nonce in self.nonces:
                self.nonces.discard(nonce)
                return True
            return False


# ── HTTP server ──────────────────────────────────────────────────────────────

class AcmeServer:
    """
    Thread-backed in-process ACME server. Bind happens at construction;
    `start()` begins serving in a daemon thread.
    """

    def __init__(
        self,
        ca_nid:        str,
        ca_priv_key:   Ed25519PrivateKey,
        ca_root_cert:  x509.Certificate,
        cert_validity: datetime.timedelta,
    ) -> None:
        self._state = _ServerState(
            ca_nid=ca_nid, ca_priv_key=ca_priv_key,
            ca_root_cert=ca_root_cert, cert_validity=cert_validity,
        )
        # Bind ephemeral loopback port. ThreadingHTTPServer handles concurrent requests.
        handler = _make_handler(self._state)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._state.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return self._state.base_url

    @property
    def directory_url(self) -> str:
        return self._state.base_url + "/directory"

    def start(self) -> "AcmeServer":
        if self._thread is None:
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
        return self

    def close(self) -> None:
        try:
            self._httpd.shutdown()
        finally:
            self._httpd.server_close()

    def __enter__(self) -> "AcmeServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _make_handler(state: _ServerState):
    base_url = lambda: state.base_url

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # silence stderr noise during tests
            pass

        # ── Routing ──────────────────────────────────────────────────────────

        def do_GET(self) -> None:
            if self.path == "/directory":
                self._handle_directory()
            elif self.path == "/new-nonce":
                self._handle_new_nonce()
            else:
                self._send_problem(404, "urn:ietf:params:acme:error:malformed", "no such resource")

        def do_HEAD(self) -> None:
            if self.path == "/new-nonce":
                self._handle_new_nonce()
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if self.path == "/new-account":   self._handle_new_account()
            elif self.path == "/new-order":   self._handle_new_order()
            elif self.path.startswith("/authz/"):    self._handle_authz()
            elif self.path.startswith("/chall/"):    self._handle_challenge()
            elif self.path.startswith("/finalize/"): self._handle_finalize()
            elif self.path.startswith("/cert/"):     self._handle_cert()
            elif self.path.startswith("/order/"):    self._handle_order()
            else:
                self._send_problem(404, "urn:ietf:params:acme:error:malformed", "no such resource")

        # ── Endpoint handlers ────────────────────────────────────────────────

        def _handle_directory(self) -> None:
            d = Directory(
                new_nonce=base_url() + "/new-nonce",
                new_account=base_url() + "/new-account",
                new_order=base_url() + "/new-order",
            )
            self._send_json(200, d.to_dict())

        def _handle_new_nonce(self) -> None:
            self.send_response(200 if self.command == "HEAD" else 204)
            self.send_header("Replay-Nonce", state.mint_nonce())
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _handle_new_account(self) -> None:
            env, header = self._read_envelope()
            if env is None:
                return
            if header.jwk is None:
                self._send_problem(400, "urn:ietf:params:acme:error:malformed",
                    "newAccount must include a 'jwk' member")
                return
            if not state.consume_nonce(header.nonce):
                self._send_problem(400, "urn:ietf:params:acme:error:badNonce", "invalid nonce")
                return
            pub = _jws.public_key_from_jwk(header.jwk)
            if _jws.verify(env, pub) is None:
                self._send_problem(400, "urn:ietf:params:acme:error:malformed",
                    "JWS signature verify failed")
                return

            account_id  = "acc-" + _short_id()
            account_url = base_url() + "/account/" + account_id
            with state.lock:
                state.account_jwks[account_url] = header.jwk

            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Location", account_url)
            self.send_header("Replay-Nonce", state.mint_nonce())
            body = json.dumps(Account(status=Status.VALID).to_dict()).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_new_order(self) -> None:
            env, header = self._read_envelope()
            if env is None: return
            if not state.consume_nonce(header.nonce):
                self._send_problem(400, "urn:ietf:params:acme:error:badNonce", "invalid nonce")
                return
            if not self._verify_account(env, header):
                self._send_problem(401, "urn:ietf:params:acme:error:accountDoesNotExist",
                    f"unknown kid: {header.kid}")
                return

            payload = _jws.decode_payload(env) or {}
            try:
                # Reuse NewOrderPayload-like parsing.
                identifiers = [Identifier.from_dict(i) for i in payload.get("identifiers", [])]
            except Exception:
                self._send_problem(400, "urn:ietf:params:acme:error:malformed", "missing identifiers")
                return
            if not identifiers:
                self._send_problem(400, "urn:ietf:params:acme:error:malformed", "missing identifiers")
                return
            ident = identifiers[0]

            order_id = "ord-" + _short_id()
            authz_id = "az-"  + _short_id()
            chall_id = "ch-"  + _short_id()
            token    = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")

            order_url    = base_url() + "/order/"    + order_id
            authz_url    = base_url() + "/authz/"    + authz_id
            chall_url    = base_url() + "/chall/"    + chall_id
            finalize_url = base_url() + "/finalize/" + order_id

            with state.lock:
                state.challenges[chall_id] = _ChallengeState(
                    id=chall_id, type=wire.CHALLENGE_AGENT_01, status=Status.PENDING,
                    token=token, authz_id=authz_id, account_url=header.kid or "")
                state.authzs[authz_id] = _AuthzState(
                    id=authz_id, identifier=ident, status=Status.PENDING,
                    challenge_ids=[chall_id], account_url=header.kid or "")
                state.orders[order_id] = _OrderState(
                    id=order_id, identifier=ident, status=Status.PENDING,
                    authz_id=authz_id, finalize_url=finalize_url,
                    account_url=header.kid or "")

            order = Order(
                status=Status.PENDING,
                identifiers=[ident],
                authorizations=[authz_url],
                finalize=finalize_url,
            )
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Location", order_url)
            self.send_header("Replay-Nonce", state.mint_nonce())
            body = json.dumps(order.to_dict()).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_authz(self) -> None:
            env, header = self._read_envelope()
            if env is None: return
            if not state.consume_nonce(header.nonce):
                self._send_problem(400, "urn:ietf:params:acme:error:badNonce", "invalid nonce"); return
            if not self._verify_account(env, header):
                self._send_problem(401, "urn:ietf:params:acme:error:unauthorized", "bad sig"); return

            authz_id = self.path[len("/authz/"):]
            az = state.authzs.get(authz_id)
            if az is None:
                self._send_problem(404, "urn:ietf:params:acme:error:malformed", "no authz"); return

            challenges = []
            for cid in az.challenge_ids:
                cs = state.challenges[cid]
                challenges.append(Challenge(
                    type=cs.type, url=base_url() + "/chall/" + cs.id,
                    status=cs.status, token=cs.token,
                ))
            self._send_json(200, Authorization(
                status=az.status, identifier=az.identifier, challenges=challenges,
            ).to_dict(), nonce=state.mint_nonce())

        def _handle_challenge(self) -> None:
            env, header = self._read_envelope()
            if env is None: return
            if not state.consume_nonce(header.nonce):
                self._send_problem(400, "urn:ietf:params:acme:error:badNonce", "invalid nonce"); return
            account_jwk = state.account_jwks.get(header.kid or "")
            if account_jwk is None:
                self._send_problem(401, "urn:ietf:params:acme:error:accountDoesNotExist", "unknown kid")
                return
            account_pub = _jws.public_key_from_jwk(account_jwk)
            if _jws.verify(env, account_pub) is None:
                self._send_problem(400, "urn:ietf:params:acme:error:malformed", "JWS sig fail"); return

            chall_id = self.path[len("/chall/"):]
            ch = state.challenges.get(chall_id)
            if ch is None:
                self._send_problem(404, "urn:ietf:params:acme:error:malformed", "no chall"); return

            payload = _jws.decode_payload(env) or {}
            agent_sig_b64u = payload.get("agent_signature")
            if not agent_sig_b64u:
                with state.lock:
                    ch.status = Status.INVALID
                self._send_problem(400, error_codes.ACME_CHALLENGE_FAILED,
                    "missing agent_signature in challenge response")
                return
            try:
                sig_bytes = _b64u_decode(agent_sig_b64u)
                account_pub.verify(sig_bytes, ch.token.encode("utf-8"))
            except Exception as e:
                with state.lock:
                    ch.status = Status.INVALID
                self._send_problem(400, error_codes.ACME_CHALLENGE_FAILED,
                    f"agent-01 signature did not verify: {e}")
                return

            # Mark challenge + authz valid; promote dependent order to "ready".
            with state.lock:
                ch.status = Status.VALID
                az = state.authzs.get(ch.authz_id)
                if az is not None: az.status = Status.VALID
                for o in state.orders.values():
                    if o.authz_id == ch.authz_id:
                        o.status = Status.READY

            self._send_json(200, Challenge(
                type=ch.type, url=base_url() + "/chall/" + ch.id,
                status=ch.status, token=ch.token,
            ).to_dict(), nonce=state.mint_nonce())

        def _handle_finalize(self) -> None:
            env, header = self._read_envelope()
            if env is None: return
            if not state.consume_nonce(header.nonce):
                self._send_problem(400, "urn:ietf:params:acme:error:badNonce", "invalid nonce"); return
            if not self._verify_account(env, header):
                self._send_problem(401, "urn:ietf:params:acme:error:unauthorized", "bad sig"); return

            order_id = self.path[len("/finalize/"):]
            os_ = state.orders.get(order_id)
            if os_ is None:
                self._send_problem(404, "urn:ietf:params:acme:error:malformed", "no order"); return
            if os_.status != Status.READY:
                self._send_problem(403, "urn:ietf:params:acme:error:orderNotReady",
                    f"order is in state '{os_.status}', not 'ready'")
                return

            payload = _jws.decode_payload(env) or {}
            csr_b64u = payload.get("csr")
            if not csr_b64u:
                self._send_problem(400, "urn:ietf:params:acme:error:malformed", "missing csr")
                return
            try:
                csr_der = _b64u_decode(csr_b64u)
                csr = x509.load_der_x509_csr(csr_der)
                cn_attrs = csr.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
                subject_cn = cn_attrs[0].value if cn_attrs else None
                if subject_cn != os_.identifier.value:
                    self._send_problem(400, error_codes.CERT_SUBJECT_NID_MISMATCH,
                        f"CSR subject CN '{subject_cn or ''}' does not match "
                        f"order identifier '{os_.identifier.value}'")
                    return
                subject_pub = csr.public_key()
                serial = x509.random_serial_number()
                now = datetime.datetime.now(datetime.timezone.utc)
                leaf = NipX509Builder.issue_leaf(
                    subject_nid=os_.identifier.value,
                    subject_pub_key=subject_pub,
                    ca_priv_key=state.ca_priv_key,
                    issuer_nid=state.ca_nid,
                    role=LeafRole.AGENT,
                    assurance_level=AssuranceLevel.ANONYMOUS,
                    not_before=now - datetime.timedelta(minutes=1),
                    not_after=now + state.cert_validity,
                    serial_number=serial,
                )
                cert_id = "crt-" + _short_id()
                cert_url = base_url() + "/cert/" + cert_id
                pem_chain = (
                    leaf.public_bytes(serialization.Encoding.PEM).decode("ascii")
                    + state.ca_root_cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
                )
                with state.lock:
                    state.certs[cert_id] = pem_chain
                    os_.status          = Status.VALID
                    os_.certificate_url = cert_url
            except Exception as e:
                self._send_problem(400, "urn:ietf:params:acme:error:badCSR",
                    f"CSR processing failed: {e}")
                return

            authz_url = base_url() + "/authz/" + os_.authz_id
            self._send_json(200, Order(
                status=os_.status, identifiers=[os_.identifier],
                authorizations=[authz_url], finalize=os_.finalize_url,
                certificate=os_.certificate_url,
            ).to_dict(), nonce=state.mint_nonce())

        def _handle_cert(self) -> None:
            env, header = self._read_envelope()
            if env is None: return
            if not state.consume_nonce(header.nonce):
                self._send_problem(400, "urn:ietf:params:acme:error:badNonce", "invalid nonce"); return
            if not self._verify_account(env, header):
                self._send_problem(401, "urn:ietf:params:acme:error:unauthorized", "bad sig"); return

            cert_id = self.path[len("/cert/"):]
            pem = state.certs.get(cert_id)
            if pem is None:
                self._send_problem(404, "urn:ietf:params:acme:error:malformed", "no cert"); return

            body = pem.encode("ascii")
            self.send_response(200)
            self.send_header("Content-Type", wire.CONTENT_TYPE_PEM_CERT)
            self.send_header("Replay-Nonce", state.mint_nonce())
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_order(self) -> None:
            env, header = self._read_envelope()
            if env is None: return
            if not state.consume_nonce(header.nonce):
                self._send_problem(400, "urn:ietf:params:acme:error:badNonce", "invalid nonce"); return
            if not self._verify_account(env, header):
                self._send_problem(401, "urn:ietf:params:acme:error:unauthorized", "bad sig"); return

            order_id = self.path[len("/order/"):]
            os_ = state.orders.get(order_id)
            if os_ is None:
                self._send_problem(404, "urn:ietf:params:acme:error:malformed", "no order"); return

            authz_url = base_url() + "/authz/" + os_.authz_id
            self._send_json(200, Order(
                status=os_.status, identifiers=[os_.identifier],
                authorizations=[authz_url], finalize=os_.finalize_url,
                certificate=os_.certificate_url,
            ).to_dict(), nonce=state.mint_nonce())

        # ── Helpers ──────────────────────────────────────────────────────────

        def _read_envelope(self) -> tuple[_jws.Envelope, _jws.ProtectedHeader] | tuple[None, None]:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                env = _jws.Envelope.from_dict(json.loads(body))
                header = _jws.ProtectedHeader.from_dict(json.loads(_b64u_decode(env.protected)))
                return env, header
            except Exception as e:
                self._send_problem(400, "urn:ietf:params:acme:error:malformed",
                    f"malformed JWS: {e}")
                return None, None

        def _verify_account(self, env: _jws.Envelope, header: _jws.ProtectedHeader) -> bool:
            if header.kid is None: return False
            jwk = state.account_jwks.get(header.kid)
            if jwk is None: return False
            return _jws.verify(env, _jws.public_key_from_jwk(jwk)) is not None

        def _send_json(self, status: int, body: Any, nonce: str | None = None) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if nonce is not None:
                self.send_header("Replay-Nonce", nonce)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_problem(self, status: int, type_: str, detail: str) -> None:
            body = json.dumps(ProblemDetail(type=type_, detail=detail, status=status).to_dict()).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", wire.CONTENT_TYPE_PROBLEM)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _short_id() -> str:
    return uuid.uuid4().hex[:16]
