# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
JWS signing helpers for ACME with Ed25519 (`alg: "EdDSA"` per RFC 8037).

Wire shape (RFC 8555 §6.2 + RFC 7515 flattened JWS JSON serialization):

    {
      "protected": base64url(JSON({alg, nonce, url, [jwk|kid]})),
      "payload":   base64url(JSON(payload)),
      "signature": base64url(Ed25519(protected || "." || payload))
    }
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)


ALG_EDDSA   = "EdDSA"     # RFC 8037 §3.1
KTY_OKP     = "OKP"       # RFC 8037 §2
CRV_ED25519 = "Ed25519"   # RFC 8037 §2


# ── DTOs ─────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Jwk:
    kty: str
    crv: str
    x:   str

    def to_dict(self) -> dict[str, Any]:
        return {"kty": self.kty, "crv": self.crv, "x": self.x}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Jwk":
        return cls(kty=d["kty"], crv=d["crv"], x=d["x"])


@dataclasses.dataclass(frozen=True)
class ProtectedHeader:
    alg:   str
    nonce: str
    url:   str
    jwk:   Jwk | None = None
    kid:   str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"alg": self.alg, "nonce": self.nonce, "url": self.url}
        if self.jwk is not None: out["jwk"] = self.jwk.to_dict()
        if self.kid is not None: out["kid"] = self.kid
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProtectedHeader":
        jwk = Jwk.from_dict(d["jwk"]) if d.get("jwk") else None
        return cls(alg=d["alg"], nonce=d["nonce"], url=d["url"], jwk=jwk, kid=d.get("kid"))


@dataclasses.dataclass(frozen=True)
class Envelope:
    protected: str
    payload:   str
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "protected": self.protected,
            "payload":   self.payload,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Envelope":
        return cls(protected=d["protected"], payload=d["payload"], signature=d["signature"])


# ── Public API ───────────────────────────────────────────────────────────────

def jwk_from_public_key(pub: Ed25519PublicKey) -> Jwk:
    """Build a JWK from a cryptography Ed25519PublicKey."""
    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return Jwk(kty=KTY_OKP, crv=CRV_ED25519, x=_b64u_encode(raw))


def public_key_from_jwk(jwk: Jwk) -> Ed25519PublicKey:
    """Build an Ed25519 public key from a JWK (assumes OKP/Ed25519)."""
    if jwk.kty != KTY_OKP or jwk.crv != CRV_ED25519:
        raise ValueError(f"JWK is not OKP/Ed25519: kty={jwk.kty} crv={jwk.crv}")
    return Ed25519PublicKey.from_public_bytes(_b64u_decode(jwk.x))


def thumbprint(jwk: Jwk) -> str:
    """RFC 7638 §3 thumbprint of an Ed25519 JWK (lex-sorted compact JSON, SHA-256, base64url)."""
    canonical = '{"crv":"' + jwk.crv + '","kty":"' + jwk.kty + '","x":"' + jwk.x + '"}'
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return _b64u_encode(digest)


def sign(
    header:   ProtectedHeader,
    payload:  Any | None,
    priv_key: Ed25519PrivateKey,
) -> Envelope:
    """
    Sign a JWS request. `payload=None` produces an empty payload string for POST-as-GET
    (RFC 8555 §6.3).
    """
    header_b64u = _b64u_encode(_compact_json(header.to_dict()).encode("utf-8"))
    if payload is None:
        payload_b64u = ""
    else:
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        payload_b64u = _b64u_encode(_compact_json(payload).encode("utf-8"))
    signing_input = (header_b64u + "." + payload_b64u).encode("ascii")
    sig_bytes = priv_key.sign(signing_input)
    return Envelope(protected=header_b64u, payload=payload_b64u, signature=_b64u_encode(sig_bytes))


def verify(envelope: Envelope, pub_key: Ed25519PublicKey) -> ProtectedHeader | None:
    """Verify a JWS envelope. Returns the parsed protected header on success, else None."""
    try:
        signing_input = (envelope.protected + "." + envelope.payload).encode("ascii")
        sig = _b64u_decode(envelope.signature)
        pub_key.verify(sig, signing_input)
        header_dict = json.loads(_b64u_decode(envelope.protected))
        return ProtectedHeader.from_dict(header_dict)
    except Exception:
        return None


def decode_payload(envelope: Envelope) -> dict[str, Any] | None:
    """Decode the payload portion of an envelope to a JSON object."""
    if not envelope.payload:
        return None
    return json.loads(_b64u_decode(envelope.payload))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _compact_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=False, ensure_ascii=False)
