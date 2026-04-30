# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
ACME wire-level DTOs (RFC 8555 + NPS-RFC-0002 §4.4).

All records are dataclasses with `to_dict()` / `from_dict()` helpers that
omit None fields on serialization. Wire shapes match the .NET / Java
references byte-for-byte.
"""

from __future__ import annotations

import dataclasses
from typing import Any


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# ── ACME status enumeration values (RFC 8555 §7.1.6) ─────────────────────────

class Status:
    PENDING     = "pending"
    READY       = "ready"
    PROCESSING  = "processing"
    VALID       = "valid"
    INVALID     = "invalid"
    EXPIRED     = "expired"
    DEACTIVATED = "deactivated"
    REVOKED     = "revoked"
    SUBMITTED   = "submitted"


# ── Directory (RFC 8555 §7.1.1) ──────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class DirectoryMeta:
    terms_of_service:          str | None       = None
    website:                   str | None       = None
    caa_identities:            list[str] | None = None
    external_account_required: bool | None      = None

    def to_dict(self) -> dict[str, Any]:
        return _strip_none({
            "termsOfService":          self.terms_of_service,
            "website":                 self.website,
            "caaIdentities":           self.caa_identities,
            "externalAccountRequired": self.external_account_required,
        })

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DirectoryMeta":
        return cls(
            terms_of_service=d.get("termsOfService"),
            website=d.get("website"),
            caa_identities=d.get("caaIdentities"),
            external_account_required=d.get("externalAccountRequired"),
        )


@dataclasses.dataclass(frozen=True)
class Directory:
    new_nonce:   str
    new_account: str
    new_order:   str
    revoke_cert: str | None           = None
    key_change:  str | None           = None
    meta:        DirectoryMeta | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "newNonce":   self.new_nonce,
            "newAccount": self.new_account,
            "newOrder":   self.new_order,
        }
        if self.revoke_cert is not None: out["revokeCert"] = self.revoke_cert
        if self.key_change is not None:  out["keyChange"]  = self.key_change
        if self.meta is not None:        out["meta"]       = self.meta.to_dict()
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Directory":
        meta = DirectoryMeta.from_dict(d["meta"]) if d.get("meta") else None
        return cls(
            new_nonce=d["newNonce"],
            new_account=d["newAccount"],
            new_order=d["newOrder"],
            revoke_cert=d.get("revokeCert"),
            key_change=d.get("keyChange"),
            meta=meta,
        )


# ── Account (RFC 8555 §7.3) ──────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class NewAccountPayload:
    terms_of_service_agreed: bool | None      = None
    contact:                 list[str] | None = None
    only_return_existing:    bool | None      = None

    def to_dict(self) -> dict[str, Any]:
        return _strip_none({
            "termsOfServiceAgreed": self.terms_of_service_agreed,
            "contact":              self.contact,
            "onlyReturnExisting":   self.only_return_existing,
        })


@dataclasses.dataclass(frozen=True)
class Account:
    status:  str
    contact: list[str] | None = None
    orders:  str | None       = None

    def to_dict(self) -> dict[str, Any]:
        return _strip_none({
            "status":  self.status,
            "contact": self.contact,
            "orders":  self.orders,
        })


# ── Order (RFC 8555 §7.1.3) ──────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Identifier:
    type:  str
    value: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Identifier":
        return cls(type=d["type"], value=d["value"])


@dataclasses.dataclass(frozen=True)
class NewOrderPayload:
    identifiers: list[Identifier]
    not_before:  str | None = None
    not_after:   str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"identifiers": [i.to_dict() for i in self.identifiers]}
        if self.not_before is not None: out["notBefore"] = self.not_before
        if self.not_after is not None:  out["notAfter"]  = self.not_after
        return out


@dataclasses.dataclass(frozen=True)
class ProblemDetail:
    type:   str
    detail: str | None = None
    status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return _strip_none({"type": self.type, "detail": self.detail, "status": self.status})

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProblemDetail":
        return cls(type=d["type"], detail=d.get("detail"), status=d.get("status"))


@dataclasses.dataclass(frozen=True)
class Order:
    status:         str
    identifiers:    list[Identifier]
    authorizations: list[str]
    finalize:       str
    expires:        str | None           = None
    certificate:    str | None           = None
    error:          ProblemDetail | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status":         self.status,
            "identifiers":    [i.to_dict() for i in self.identifiers],
            "authorizations": self.authorizations,
            "finalize":       self.finalize,
        }
        if self.expires is not None:     out["expires"]     = self.expires
        if self.certificate is not None: out["certificate"] = self.certificate
        if self.error is not None:       out["error"]       = self.error.to_dict()
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Order":
        return cls(
            status=d["status"],
            identifiers=[Identifier.from_dict(i) for i in d["identifiers"]],
            authorizations=list(d["authorizations"]),
            finalize=d["finalize"],
            expires=d.get("expires"),
            certificate=d.get("certificate"),
            error=ProblemDetail.from_dict(d["error"]) if d.get("error") else None,
        )


# ── Authorization & Challenge (RFC 8555 §7.5) ────────────────────────────────

@dataclasses.dataclass(frozen=True)
class Challenge:
    type:      str
    url:       str
    status:    str
    token:     str
    validated: str | None           = None
    error:     ProblemDetail | None = None

    def to_dict(self) -> dict[str, Any]:
        return _strip_none({
            "type":      self.type,
            "url":       self.url,
            "status":    self.status,
            "token":     self.token,
            "validated": self.validated,
            "error":     self.error.to_dict() if self.error else None,
        })

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Challenge":
        return cls(
            type=d["type"], url=d["url"], status=d["status"], token=d["token"],
            validated=d.get("validated"),
            error=ProblemDetail.from_dict(d["error"]) if d.get("error") else None,
        )


@dataclasses.dataclass(frozen=True)
class Authorization:
    status:     str
    identifier: Identifier
    challenges: list[Challenge]
    expires:    str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status":     self.status,
            "identifier": self.identifier.to_dict(),
            "challenges": [c.to_dict() for c in self.challenges],
        }
        if self.expires is not None: out["expires"] = self.expires
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Authorization":
        return cls(
            status=d["status"],
            identifier=Identifier.from_dict(d["identifier"]),
            challenges=[Challenge.from_dict(c) for c in d["challenges"]],
            expires=d.get("expires"),
        )


@dataclasses.dataclass(frozen=True)
class ChallengeRespondPayload:
    """`agent_signature` = base64url(Ed25519(token)). RFC-0002 §4.4."""

    agent_signature: str

    def to_dict(self) -> dict[str, Any]:
        return {"agent_signature": self.agent_signature}


# ── Finalize (RFC 8555 §7.4) ─────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class FinalizePayload:
    csr: str   # base64url(CSR DER)

    def to_dict(self) -> dict[str, Any]:
        return {"csr": self.csr}
