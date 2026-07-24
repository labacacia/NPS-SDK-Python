# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Group-JWS verifier for NPS-CR-0003 §3.5 / §5.1.3 session-issue authorisation.

The flattened JWS shape (RFC 7515 §7.2.1)::

    {
      "protected": "<b64url(header)>",
      "payload":   "<b64url(payload)>",
      "signature": "<b64url(Ed25519 signature)>"
    }

where the protected header MUST be
``{ "alg": "EdDSA", "kid": "<group_nid>", "nps-purpose": "session-issue" }``
and the signature is computed over the ASCII bytes of
``protected || "." || payload`` per RFC 7515 §3.
"""

from __future__ import annotations

import base64
import dataclasses
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from nps_sdk.nip import error_codes


@dataclasses.dataclass(frozen=True)
class FlattenedJws:
    """Flattened JWS object as it appears on the wire / in JSON."""

    protected: str | None = None
    payload: str | None = None
    signature: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "FlattenedJws":
        return cls(
            protected=data.get("protected"),
            payload=data.get("payload"),
            signature=data.get("signature"),
        )


@dataclasses.dataclass(frozen=True)
class GroupJwsResult:
    """Outcome of :meth:`NipGroupJws.try_verify`."""

    valid: bool
    payload_json: str | None = None
    kid: str | None = None
    error_code: str | None = None


class NipGroupJws:
    """Static verifier for the flattened group-JWS shape."""

    EXPECTED_ALG = "EdDSA"
    EXPECTED_PURPOSE = "session-issue"

    @staticmethod
    def try_verify(jws: FlattenedJws, group_pub_key: Ed25519PublicKey) -> GroupJwsResult:
        """Parse + verify *jws* against *group_pub_key*.

        On success returns ``valid=True`` with the decoded ``payload_json`` and
        asserted ``kid``; on failure returns ``valid=False`` with the matching
        :mod:`nps_sdk.nip.error_codes` code.
        """
        if not jws.protected or not jws.payload or not jws.signature:
            return GroupJwsResult(False, error_code=error_codes.CA_JWS_INVALID)

        try:
            header_bytes = _b64url_decode(jws.protected)
            payload_bytes = _b64url_decode(jws.payload)
            sig_bytes = _b64url_decode(jws.signature)
        except Exception:
            return GroupJwsResult(False, error_code=error_codes.CA_JWS_INVALID)

        try:
            header = json.loads(header_bytes)
        except Exception:
            return GroupJwsResult(False, error_code=error_codes.CA_JWS_INVALID)

        if (
            not isinstance(header, dict)
            or header.get("alg") != NipGroupJws.EXPECTED_ALG
            or header.get("nps-purpose") != NipGroupJws.EXPECTED_PURPOSE
            or not header.get("kid")
        ):
            return GroupJwsResult(False, error_code=error_codes.CA_JWS_INVALID)

        # RFC 7515 §3 signing input: ASCII(protected) "." ASCII(payload).
        signing_input = (jws.protected + "." + jws.payload).encode("ascii")
        try:
            group_pub_key.verify(sig_bytes, signing_input)
        except InvalidSignature:
            return GroupJwsResult(False, error_code=error_codes.CA_JWS_INVALID)

        return GroupJwsResult(
            valid=True,
            payload_json=payload_bytes.decode("utf-8"),
            kid=header["kid"],
        )


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)
