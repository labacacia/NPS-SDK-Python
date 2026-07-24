# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Pure-ASGI HTTP router for the NIP CA service (NPS-3 §8).

Port of the .NET ``NPS.NIP.Http.NipCaRouter``. Maps all CA endpoints +
``/.well-known/nps-ca`` as a plain ASGI 3.0 callable — run under any ASGI
server (uvicorn, hypercorn) or drive it in-process with ``httpx.ASGITransport``.

Wire field names, NID format, error codes, and HTTP status codes match the
.NET reference exactly for cross-SDK interop.
"""

from __future__ import annotations

import datetime
import hmac
import json
import re
from typing import Any, Awaitable, Callable

from nps_sdk.nip import error_codes
from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nip.frames import IdentLineageRole
from nps_sdk.nip.identity import NipIdentity

from nps_sdk.nip.ca.errors import NipCaException
from nps_sdk.nip.ca.group_jws import FlattenedJws, NipGroupJws
from nps_sdk.nip.ca.options import NipCaOptions
from nps_sdk.nip.ca.ra import (
    IBootstrapTokenStore,
    IPendingStore,
    NipRaPendingException,
    PendingStatus,
    create_enrollment_policy,
)
from nps_sdk.nip.ca.service import NipCaService, NipVerifyResult


_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9._:@/\-]{1,256}$")

_VALID_REVOCATION_REASONS = frozenset(
    {
        "key_compromise",
        "ca_compromise",
        "affiliation_changed",
        "superseded",
        "cessation_of_operation",
        "parent_revoked",  # NPS-CR-0003 §5.3
    }
)

_BAD_REQUEST = "NIP-CA-BAD-REQUEST"
_UNAUTHORIZED = "NIP-CA-UNAUTHORIZED"

# NipCaException.error_code → HTTP status (mirror of .NET NipCaRouter.ErrorResult).
_ERROR_STATUS: dict[str, int] = {
    error_codes.CA_NID_NOT_FOUND: 404,
    error_codes.CA_PARENT_NOT_FOUND: 404,
    error_codes.CA_NID_ALREADY_EXISTS: 409,
    error_codes.CA_SERIAL_DUPLICATE: 409,
    error_codes.CA_RENEWAL_TOO_EARLY: 400,
    error_codes.CA_SESSION_VALIDITY_INVALID: 400,
    error_codes.CA_PARENT_NOT_GROUP: 400,
    error_codes.CA_SCOPE_EXPANSION_DENIED: 403,
    error_codes.CERT_CAPABILITY_MISSING: 403,
    error_codes.CA_GROUP_REVOKED: 403,
    error_codes.CA_JWS_INVALID: 401,
    error_codes.CA_JWS_EXPIRED: 401,
    error_codes.CERT_EXPIRED: 401,
    error_codes.CERT_REVOKED: 401,
    error_codes.CERT_PARENT_REVOKED: 401,
    error_codes.RA_TOKEN_INVALID: 401,
    error_codes.RA_TOKEN_EXPIRED: 401,
    error_codes.RA_NID_NOT_ALLOWED: 403,
    error_codes.RA_PENDING_REJECTED: 403,
}


class NipCaRouterApp:
    """Pure-ASGI NIP CA server."""

    def __init__(
        self,
        opts: NipCaOptions,
        ca: NipCaService,
        bootstrap_token_store: IBootstrapTokenStore | None = None,
        pending_store: IPendingStore | None = None,
    ) -> None:
        self._opts = opts
        self._ca = ca
        self._bootstrap_store = bootstrap_token_store
        self._pending_store = pending_store
        self._pfx = opts.route_prefix.rstrip("/")
        self._policy = create_enrollment_policy(opts, bootstrap_token_store, pending_store)

    # ── ASGI entrypoint ──────────────────────────────────────────────────────────

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
        }
        try:
            await self._route(method, path, headers, receive, send)
        except NipCaException as ex:
            await self._error_result(send, ex)

    async def _route(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        receive: Callable,
        send: Callable,
    ) -> None:
        pfx = self._pfx

        if method == "GET" and path == "/.well-known/nps-ca":
            await self._well_known(send)
            return
        if method == "GET" and path == f"{pfx}/v1/ca/cert":
            await self._json(send, 200, {
                "public_key": self._ca.get_ca_public_key(),
                "algorithm": "ed25519",
            })
            return
        if method == "GET" and path == f"{pfx}/v1/crl":
            await self._crl(send)
            return

        if method == "POST" and path == f"{pfx}/v1/agents/register":
            await self._register(send, headers, receive, "agent")
            return
        if method == "POST" and path == f"{pfx}/v1/nodes/register":
            await self._register(send, headers, receive, "node")
            return
        if method == "POST" and path == f"{pfx}/v1/agents/register-x509":
            await self._register_x509(send, headers, receive, "agent")
            return
        if method == "POST" and path == f"{pfx}/v1/nodes/register-x509":
            await self._register_x509(send, headers, receive, "node")
            return

        # Group register (NPS-CR-0003)
        if method == "POST" and path == f"{pfx}/v1/orchestrators/groups/register":
            await self._register_group(send, headers, receive)
            return

        # Enrollment (NPS-CR-0005)
        if method == "POST" and path == f"{pfx}/v1/enrollment/tokens":
            await self._create_token(send, headers, receive)
            return
        if method == "GET" and path == f"{pfx}/v1/enrollment/pending":
            await self._list_pending(send, headers)
            return

        # Parameterised routes.
        m = re.fullmatch(rf"{re.escape(pfx)}/v1/(agents|nodes)/(.+)/renew", path)
        if m and method == "POST":
            await self._renew(send, headers, _unquote(m.group(2)))
            return
        m = re.fullmatch(rf"{re.escape(pfx)}/v1/(agents|nodes)/(.+)/revoke", path)
        if m and method == "POST":
            await self._revoke(send, headers, receive, _unquote(m.group(2)))
            return
        m = re.fullmatch(rf"{re.escape(pfx)}/v1/(agents|nodes)/(.+)/verify", path)
        if m and method == "GET":
            await self._verify(send, _unquote(m.group(2)))
            return
        m = re.fullmatch(rf"{re.escape(pfx)}/v1/orchestrators/groups/(.+)/sessions/issue", path)
        if m and method == "POST":
            await self._issue_session(send, headers, receive, _unquote(m.group(1)))
            return
        m = re.fullmatch(rf"{re.escape(pfx)}/v1/orchestrators/groups/(.+)/revoke", path)
        if m and method == "POST":
            await self._revoke(send, headers, receive, _unquote(m.group(1)))
            return
        m = re.fullmatch(rf"{re.escape(pfx)}/v1/orchestrators/groups/(.+)/sessions", path)
        if m and method == "GET":
            await self._list_sessions(send, headers, _unquote(m.group(1)))
            return
        m = re.fullmatch(rf"{re.escape(pfx)}/v1/enrollment/pending/(.+)/approve", path)
        if m and method == "POST":
            await self._approve_pending(send, headers, receive, _unquote(m.group(1)))
            return
        m = re.fullmatch(rf"{re.escape(pfx)}/v1/enrollment/pending/(.+)/reject", path)
        if m and method == "POST":
            await self._reject_pending(send, headers, receive, _unquote(m.group(1)))
            return

        await self._json(send, 404, {"error_code": error_codes.CA_NID_NOT_FOUND,
                                     "message": "No CA endpoint at this path."})

    # ── Discovery ────────────────────────────────────────────────────────────────

    async def _well_known(self, send: Callable) -> None:
        pfx = self._pfx
        base = self._opts.base_url
        body = {
            "nps_ca": "0.1",
            "issuer": self._opts.ca_nid,
            "display_name": self._opts.display_name,
            "public_key": self._ca.get_ca_public_key(),
            "algorithms": list(self._opts.algorithms),
            "endpoints": {
                "register": f"{base}{pfx}/v1/agents/register",
                "verify": f"{base}{pfx}/v1/agents/{{nid}}/verify",
                "ocsp": f"{base}{pfx}/v1/agents/{{nid}}/verify",
                "node_ocsp": f"{base}{pfx}/v1/nodes/{{nid}}/verify",
                "crl": f"{base}{pfx}/v1/crl",
            },
            "capabilities": [
                "agent", "node", "orchestrator-group",
                f"ra-tier-{int(self._opts.enrollment_tier)}",
            ],
            "max_cert_validity_days": self._opts.agent_cert_validity_days,
        }
        await self._json(send, 200, body)

    # ── CRL ──────────────────────────────────────────────────────────────────────

    async def _crl(self, send: Callable) -> None:
        revoked = await self._ca.get_crl()
        entries = [
            {
                "nid": r.nid,
                "serial": r.serial,
                "revoked_at": _iso(r.revoked_at),
                "reason": r.revoke_reason,
            }
            for r in revoked
        ]
        body = {
            "issued_by": self._opts.ca_nid,
            "issued_at": _iso_now(),
            "entries": entries,
        }
        signed = dict(body)
        signed["signature"] = self._ca.sign_artifact(body)
        await self._json(send, 200, signed)

    # ── Register (agent / node) ──────────────────────────────────────────────────

    async def _register(
        self, send: Callable, headers: dict[str, str], receive: Callable, entity_type: str
    ) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        req = await _read_json(receive)
        if req is None:
            await self._bad_request(send, "Invalid JSON body.")
            return
        err = _validate_register_request(req)
        if err is not None:
            await self._bad_request(send, err)
            return

        default_caps = ["nwp:query", "nwp:stream"] if entity_type == "node" else []
        enroll_token = headers.get("x-nps-enrollment-token")
        try:
            frame = await self._ca.register_with_ra(
                entity_type,
                req["identifier"],
                req["pub_key"],
                req.get("capabilities") or default_caps,
                req.get("scope_json") or "{}",
                req.get("metadata_json"),
                enroll_token,
                self._policy,
            )
        except NipRaPendingException as ex:
            await self._json(send, 202, {"pending_id": ex.pending_id, "status": "queued"})
            return
        except NipCaException as ex:
            await self._error_result(send, ex)
            return
        await self._json(send, 201, frame.to_dict())

    async def _register_x509(
        self, send: Callable, headers: dict[str, str], receive: Callable, entity_type: str
    ) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        req = await _read_json(receive)
        if req is None:
            await self._bad_request(send, "Invalid JSON body.")
            return
        err = _validate_register_request(req)
        if err is not None:
            await self._bad_request(send, err)
            return

        default_caps = ["nwp:query", "nwp:stream"] if entity_type == "node" else []
        try:
            frame = await self._ca.register_x509(
                entity_type,
                req["identifier"],
                req["pub_key"],
                req.get("capabilities") or default_caps,
                req.get("scope_json") or "{}",
                assurance_level=_parse_assurance(req.get("assurance_level")),
                metadata_json=req.get("metadata_json"),
            )
        except NipCaException as ex:
            await self._error_result(send, ex)
            return
        await self._json(send, 201, frame.to_dict())

    # ── Register group (NPS-CR-0003) ─────────────────────────────────────────────

    async def _register_group(
        self, send: Callable, headers: dict[str, str], receive: Callable
    ) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        req = await _read_json(receive)
        if req is None:
            await self._bad_request(send, "Invalid JSON body.")
            return
        identifier = req.get("identifier")
        if identifier and not _IDENTIFIER_RE.match(identifier):
            await self._bad_request(
                send, "identifier contains invalid characters. Allowed: a-z A-Z 0-9 . _ : @ / -"
            )
            return
        pub_key = req.get("pub_key")
        if not _valid_pubkey(pub_key):
            await self._bad_request(send, "pub_key must be 'ed25519:<base64url>'.")
            return
        try:
            frame = await self._ca.register_group(
                identifier=identifier,
                pub_key=pub_key,
                capabilities=req.get("capabilities") or [],
                scope_json=req.get("scope_json") or "{}",
                owner_user_id=req.get("owner_user_id"),
                owner_key_id=req.get("owner_key_id"),
                metadata_json=req.get("metadata_json"),
            )
        except NipCaException as ex:
            await self._error_result(send, ex)
            return
        await self._json(send, 201, frame.to_dict())

    # ── Issue session (NPS-CR-0003) ──────────────────────────────────────────────

    async def _issue_session(
        self, send: Callable, headers: dict[str, str], receive: Callable, group_nid: str
    ) -> None:
        ctype = headers.get("content-type", "")
        is_jws_body = "jose+json" in ctype.lower()

        if is_jws_body:
            raw = await _read_json(receive)
            if raw is None:
                await self._bad_request(send, "Invalid JWS body.")
                return
            jws = FlattenedJws.from_dict(raw)

            group_record = await self._ca.get_cert(group_nid)
            if group_record is None:
                await self._error_result(send, NipCaException(
                    f"Group {group_nid} not found.", error_codes.CA_PARENT_NOT_FOUND))
                return
            if group_record.nid_role != IdentLineageRole.GROUP:
                await self._error_result(send, NipCaException(
                    f"NID {group_nid} is not a group.", error_codes.CA_PARENT_NOT_GROUP))
                return
            if group_record.revoked_at is not None:
                await self._error_result(send, NipCaException(
                    f"Group {group_nid} revoked.", error_codes.CA_GROUP_REVOKED))
                return

            try:
                group_pub = NipIdentity._parse_pub_key(group_record.pub_key)  # noqa: SLF001
            except Exception:
                await self._json(send, 401, {
                    "error_code": error_codes.CA_JWS_INVALID,
                    "message": "Group public key could not be decoded.",
                })
                return

            result = NipGroupJws.try_verify(jws, group_pub)
            if not result.valid:
                await self._json(send, 401, {
                    "error_code": result.error_code,
                    "message": "Group-JWS verification failed.",
                })
                return
            if result.kid != group_nid:
                await self._json(send, 401, {
                    "error_code": error_codes.CA_JWS_INVALID,
                    "message": f"JWS kid '{result.kid}' does not match URL group_nid '{group_nid}'.",
                })
                return

            try:
                payload = json.loads(result.payload_json)
            except Exception:
                await self._json(send, 401, {
                    "error_code": error_codes.CA_JWS_INVALID,
                    "message": "JWS payload is not valid JSON.",
                })
                return

            skew = self._opts.session_jws_clock_skew_seconds
            now_epoch = int(_utcnow().timestamp())
            iat = payload.get("iat", 0)
            if not isinstance(iat, int) or iat == 0 or abs(now_epoch - iat) > skew:
                await self._json(send, 401, {
                    "error_code": error_codes.CA_JWS_EXPIRED,
                    "message": f"JWS iat outside ±{skew}s window.",
                })
                return
            req = payload
        else:
            if not self._authorized(headers):
                await self._unauthorized(send)
                return
            req = await _read_json(receive)
            if req is None:
                await self._bad_request(send, "Invalid JSON body.")
                return

        session_pub = req.get("session_pub_key")
        if not _valid_pubkey(session_pub):
            await self._bad_request(send, "session_pub_key must be 'ed25519:<base64url>'.")
            return

        validity_seconds = req.get("validity_seconds")
        validity = validity_seconds if isinstance(validity_seconds, int) and validity_seconds > 0 else None

        try:
            frame = await self._ca.issue_session(
                group_nid=group_nid,
                session_pub_key=session_pub,
                validity_seconds=validity,
                purpose=req.get("purpose"),
                capabilities=req.get("capabilities"),
                scope_json=req.get("scope_json"),
                metadata_json=req.get("metadata_json"),
            )
        except NipCaException as ex:
            await self._error_result(send, ex)
            return
        await self._json(send, 201, frame.to_dict())

    # ── Renew / revoke / verify ──────────────────────────────────────────────────

    async def _renew(self, send: Callable, headers: dict[str, str], nid: str) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        try:
            frame = await self._ca.renew(nid)
        except NipCaException as ex:
            await self._error_result(send, ex)
            return
        await self._json(send, 200, frame.to_dict())

    async def _revoke(
        self, send: Callable, headers: dict[str, str], receive: Callable, nid: str
    ) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        req = await _read_json(receive)
        reason = (req or {}).get("reason") or "cessation_of_operation"
        if reason not in _VALID_REVOCATION_REASONS:
            await self._bad_request(
                send,
                f"Invalid revocation reason '{reason}'. "
                f"Allowed: {', '.join(sorted(_VALID_REVOCATION_REASONS))}.",
            )
            return
        try:
            frame = await self._ca.revoke(nid, reason)
        except NipCaException as ex:
            await self._error_result(send, ex)
            return
        await self._json(send, 200, frame.to_dict())

    async def _verify(self, send: Callable, nid: str) -> None:
        result = await self._ca.verify(nid)
        await self._ocsp_result(send, result)

    async def _list_sessions(self, send: Callable, headers: dict[str, str], group_nid: str) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        sessions = await self._ca.list_sessions(group_nid)
        await self._json(send, 200, {
            "group_nid": group_nid,
            "count": len(sessions),
            "sessions": [
                {
                    "nid": s.nid,
                    "serial": s.serial,
                    "issued_at": _iso(s.issued_at),
                    "expires_at": _iso(s.expires_at),
                    "revoked_at": _iso(s.revoked_at),
                    "revoke_reason": s.revoke_reason,
                }
                for s in sessions
            ],
        })

    # ── Enrollment (NPS-CR-0005) ─────────────────────────────────────────────────

    async def _create_token(
        self, send: Callable, headers: dict[str, str], receive: Callable
    ) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        if self._bootstrap_store is None:
            await self._json(send, 400, {
                "error_code": _BAD_REQUEST,
                "message": "Bootstrap token enrollment is not enabled on this CA.",
            })
            return
        req = await _read_json(receive)
        ttl = (req or {}).get("ttl_seconds")
        ttl_seconds = ttl if isinstance(ttl, int) and ttl > 0 else self._opts.bootstrap_token_max_ttl_seconds
        ttl_seconds = min(ttl_seconds, self._opts.bootstrap_token_max_ttl_seconds)
        expires_at = _utcnow() + datetime.timedelta(seconds=ttl_seconds)
        label = (req or {}).get("label")
        raw = await self._bootstrap_store.create(label, expires_at)
        await self._json(send, 201, {
            "token": raw, "expires_at": _iso(expires_at), "label": label,
        })

    async def _list_pending(self, send: Callable, headers: dict[str, str]) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        if self._pending_store is None:
            await self._json(send, 400, {
                "error_code": _BAD_REQUEST,
                "message": "Pending-queue enrollment is not enabled on this CA.",
            })
            return
        records = await self._pending_store.list()
        items = [
            {
                "id": r.id,
                "entity_type": r.entity_type,
                "identifier": r.identifier,
                "pub_key": r.pub_key,
                "capabilities": list(r.capabilities),
                "scope_json": r.scope_json,
                "requested_at": _iso(r.requested_at),
                "status": r.status.name.lower(),
                "reject_reason": r.reject_reason,
            }
            for r in records
        ]
        await self._json(send, 200, {"count": len(records), "items": items})

    async def _approve_pending(
        self, send: Callable, headers: dict[str, str], receive: Callable, id: str
    ) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        if self._pending_store is None:
            await self._json(send, 400, {
                "error_code": _BAD_REQUEST,
                "message": "Pending-queue enrollment is not enabled on this CA.",
            })
            return
        record = await self._pending_store.get(id)
        if record is None:
            await self._json(send, 404, {
                "error_code": error_codes.CA_NID_NOT_FOUND,
                "message": f"Pending registration '{id}' not found.",
            })
            return
        if record.status != PendingStatus.PENDING:
            await self._json(send, 409, {
                "error_code": _BAD_REQUEST,
                "message": f"Record '{id}' is already {record.status.name.lower()}.",
            })
            return
        try:
            frame = await self._ca.register(
                record.entity_type, record.identifier, record.pub_key,
                record.capabilities, record.scope_json, record.metadata_json,
            )
            await self._pending_store.approve(id)
        except NipCaException as ex:
            await self._error_result(send, ex)
            return
        await self._json(send, 201, frame.to_dict())

    async def _reject_pending(
        self, send: Callable, headers: dict[str, str], receive: Callable, id: str
    ) -> None:
        if not self._authorized(headers):
            await self._unauthorized(send)
            return
        if self._pending_store is None:
            await self._json(send, 400, {
                "error_code": _BAD_REQUEST,
                "message": "Pending-queue enrollment is not enabled on this CA.",
            })
            return
        req = await _read_json(receive)
        reason = (req or {}).get("reason") or "rejected_by_operator"
        ok = await self._pending_store.reject(id, reason)
        if not ok:
            record = await self._pending_store.get(id)
            if record is None:
                await self._json(send, 404, {
                    "error_code": _BAD_REQUEST,
                    "message": f"Pending registration '{id}' not found.",
                })
            else:
                await self._json(send, 409, {
                    "error_code": _BAD_REQUEST,
                    "message": f"Record '{id}' is already {record.status.name.lower()}.",
                })
            return
        await self._json(send, 200, {"id": id, "status": "rejected", "reason": reason})

    # ── Response helpers ─────────────────────────────────────────────────────────

    async def _ocsp_result(self, send: Callable, r: NipVerifyResult) -> None:
        if r.valid:
            await self._json(send, 200, {
                "valid": True,
                "nid": r.record.nid,
                "expires_at": _iso(r.record.expires_at),
                "serial": r.record.serial,
            })
            return
        status = 404 if r.error_code == error_codes.CA_NID_NOT_FOUND else 200
        await self._json(send, status, {
            "valid": False,
            "error_code": r.error_code,
            "message": r.message,
        })

    async def _error_result(self, send: Callable, ex: NipCaException) -> None:
        status = _ERROR_STATUS.get(ex.error_code, 400)
        await self._json(send, status, {"error_code": ex.error_code, "message": ex.message})

    async def _bad_request(self, send: Callable, msg: str) -> None:
        await self._json(send, 400, {"error_code": _BAD_REQUEST, "message": msg})

    async def _unauthorized(self, send: Callable) -> None:
        await self._json(send, 401, {
            "error_code": _UNAUTHORIZED,
            "message": "Valid operator Bearer token required.",
        })

    def _authorized(self, headers: dict[str, str]) -> bool:
        if self._opts.operator_api_key is None:
            return True
        header = headers.get("authorization")
        if header is None or not header.lower().startswith("bearer "):
            return False
        provided = header[len("bearer "):].strip()
        return hmac.compare_digest(
            provided.encode("utf-8"), self._opts.operator_api_key.encode("utf-8")
        )

    async def _json(self, send: Callable, status: int, body: Any) -> None:
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": data})


# ── Module helpers ──────────────────────────────────────────────────────────────

def _validate_register_request(req: dict[str, Any]) -> str | None:
    identifier = req.get("identifier")
    pub_key = req.get("pub_key")
    if not identifier or not pub_key:
        return "identifier and pub_key are required."
    if not _IDENTIFIER_RE.match(identifier):
        return "identifier contains invalid characters. Allowed: a-z A-Z 0-9 . _ : @ / -"
    if not _valid_pubkey(pub_key):
        return "pub_key must be 'ed25519:<base64url>'."
    return None


def _valid_pubkey(pub_key: Any) -> bool:
    return isinstance(pub_key, str) and pub_key.startswith("ed25519:") and len(pub_key) > 8


def _parse_assurance(raw: Any) -> AssuranceLevel:
    if isinstance(raw, str):
        low = raw.lower()
        if low == "attested":
            return AssuranceLevel.ATTESTED
        if low == "verified":
            return AssuranceLevel.VERIFIED
    return AssuranceLevel.ANONYMOUS


def _unquote(value: str) -> str:
    from urllib.parse import unquote
    return unquote(value)


async def _read_json(receive: Callable) -> dict[str, Any] | None:
    body = await _read_body(receive)
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def _read_body(receive: Callable) -> bytes:
    chunks: list[bytes] = []
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            break
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body", False):
            break
    return b"".join(chunks)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="microseconds") + "Z"


def _iso_now() -> str:
    return _iso(_utcnow())
