# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS NIP — Neural Identity Protocol frame dataclasses.

  IdentFrame   0x20 — Agent identity declaration and certificate carrier.
  TrustFrame   0x21 — Cross-CA trust chain and capability grant. The OSS
                      library carries the wire frame; deployments may layer
                      local pinned-grantor validation or NPS Cloud federation.
  RevokeFrame  0x22 — Certificate revocation.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from nps_sdk.core.codec import NpsFrame
from nps_sdk.core.frames import EncodingTier, FrameType
from nps_sdk.nip.error_codes import REVOKE_FRAME_INVALID
from nps_sdk.nip.assurance_level import AssuranceLevel


_REASON_PARENT_REVOKED = "parent_revoked"


def _validate_revoke_parent_rule(reason: str, parent_nid: str | None) -> None:
    if reason == _REASON_PARENT_REVOKED:
        if not parent_nid:
            raise ValueError(
                f"{REVOKE_FRAME_INVALID}: parent_nid is required when reason=parent_revoked"
            )
    elif parent_nid is not None:
        raise ValueError(
            f"{REVOKE_FRAME_INVALID}: parent_nid must be omitted unless reason=parent_revoked"
        )


# ── IdentReputationPolicyHint ─────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class IdentReputationPolicyHint:
    """Unsigned advisory reputation hint in IdentFrame.metadata (RFC-0005 §4.2)."""
    log_sources: tuple[str, ...] = ()   # URLs of log operators the agent has registered with
    consent:     bool = False           # True if agent consents to reputation logging

    def to_dict(self) -> dict[str, Any]:
        return {"log_sources": list(self.log_sources), "consent": self.consent}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IdentReputationPolicyHint":
        return cls(
            log_sources=tuple(data.get("log_sources", [])),
            consent=bool(data.get("consent", False)),
        )


# ── IdentMetadata ─────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class IdentMetadata:
    """
    Optional metadata carried in an IdentFrame (NPS-3 §5.1).
    Not included in signature computation — Agents may set dynamically at runtime.
    """

    model_family: str | None = None
    tokenizer:    str | None = None
    runtime:      str | None = None
    reputation_policy: IdentReputationPolicyHint | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.model_family      is not None: d["model_family"]      = self.model_family
        if self.tokenizer         is not None: d["tokenizer"]         = self.tokenizer
        if self.runtime           is not None: d["runtime"]           = self.runtime
        if self.reputation_policy is not None: d["reputation_policy"] = self.reputation_policy.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IdentMetadata":
        rp_raw = data.get("reputation_policy")
        return cls(
            model_family=data.get("model_family"),
            tokenizer=data.get("tokenizer"),
            runtime=data.get("runtime"),
            reputation_policy=IdentReputationPolicyHint.from_dict(rp_raw) if rp_raw else None,
        )


# ── IdentFrame (0x20) ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class IdentFrame(NpsFrame):
    """
    Agent identity declaration and certificate carrier (NPS-3 §5.1).
    Sent as a handshake frame when establishing a connection.
    Nodes verify it before granting access.
    """

    nid:          str
    pub_key:      str
    capabilities: tuple[str, ...]
    scope:        Any
    issued_by:    str
    issued_at:    str
    expires_at:   str
    serial:       str
    signature:    str
    metadata:     IdentMetadata | None = None

    # NPS-RFC-0003 §5.1.1 — Agent identity assurance level (optional).
    assurance_level: AssuranceLevel | None = None

    # NPS-RFC-0002 §4.5 — Optional dual-trust X.509 chain (Phase 1 backward compatible).
    # `cert_format`: "v1-proprietary" (default when None) | "v2-x509".
    # `cert_chain`:  base64url-encoded DER, ordered [leaf, intermediates..., root].
    cert_format:  str | None = None
    cert_chain:   tuple[str, ...] | None = None

    # NIP v0.9 §5.1 — Optional OCSP staple (base64url DER-encoded OCSP response).
    ocsp_staple:  str | None = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.IDENT

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "frame":        "0x20",
            "nid":          self.nid,
            "pub_key":      self.pub_key,
            "capabilities": list(self.capabilities),
            "scope":        self.scope,
            "issued_by":    self.issued_by,
            "issued_at":    self.issued_at,
            "expires_at":   self.expires_at,
            "serial":       self.serial,
            "signature":    self.signature,
        }
        if self.metadata is not None:
            d["metadata"] = self.metadata.to_dict()
        if self.assurance_level is not None:
            d["assurance_level"] = self.assurance_level.wire
        if self.cert_format is not None:
            d["cert_format"] = self.cert_format
        if self.cert_chain is not None:
            d["cert_chain"] = list(self.cert_chain)
        if self.ocsp_staple is not None:
            d["ocsp_staple"] = self.ocsp_staple
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IdentFrame":
        meta = None
        if data.get("metadata"):
            meta = IdentMetadata.from_dict(data["metadata"])
        level = None
        lvl_raw = data.get("assurance_level")
        if isinstance(lvl_raw, str):
            level = AssuranceLevel.from_wire(lvl_raw)
        chain_raw = data.get("cert_chain")
        chain = tuple(chain_raw) if isinstance(chain_raw, list) else None
        return cls(
            nid=data["nid"],
            pub_key=data["pub_key"],
            capabilities=tuple(data.get("capabilities", [])),
            scope=data["scope"],
            issued_by=data["issued_by"],
            issued_at=data["issued_at"],
            expires_at=data["expires_at"],
            serial=data["serial"],
            signature=data["signature"],
            metadata=meta,
            assurance_level=level,
            cert_format=data.get("cert_format"),
            cert_chain=chain,
            ocsp_staple=data.get("ocsp_staple"),
        )

    def unsigned_dict(self) -> dict[str, Any]:
        """
        Return the dict representation without the 'signature' field, for signing.

        Per NPS-RFC-0002 §8.1, the v1 Ed25519 signature deliberately does NOT cover
        cert_format / cert_chain — those are dual-trust additions, validated by the
        X.509 chain check (Step 3b) instead. v1 verifiers continue to ignore them.
        """
        d = self.to_dict()
        d.pop("signature", None)
        d.pop("cert_format", None)
        d.pop("cert_chain", None)
        return d


# ── TrustFrame (0x21) ────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class TrustFrame(NpsFrame):
    """
    Cross-CA trust chain and capability grant frame (NPS-3 §5.2).

    The open SDK carries the complete wire frame. Deployments may layer
    local pinned-grantor validation or NPS Cloud managed federation policy
    on top.
    """

    grantor_nid: str
    grantee_ca:  str
    trust_scope: tuple[str, ...]
    nodes:       tuple[str, ...]
    issued_at:   str
    expires_at:  str
    serial:      str
    signer_nid:  str
    signature:   str

    @property
    def frame_type(self) -> FrameType:
        return FrameType.TRUST

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame":       "0x21",
            "grantor_nid": self.grantor_nid,
            "grantee_ca":  self.grantee_ca,
            "trust_scope": list(self.trust_scope),
            "nodes":       list(self.nodes),
            "issued_at":   self.issued_at,
            "expires_at":  self.expires_at,
            "serial":      self.serial,
            "signer_nid":  self.signer_nid,
            "signature":   self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrustFrame":
        grantor_nid = data.get("grantor_nid", data.get("issuer_nid", ""))
        return cls(
            grantor_nid=grantor_nid,
            grantee_ca=data.get("grantee_ca", data.get("subject_nid")),
            trust_scope=tuple(data.get("trust_scope", data.get("scopes", []))),
            nodes=tuple(data.get("nodes", [])),
            issued_at=data.get("issued_at", ""),
            expires_at=data["expires_at"],
            serial=data.get("serial", ""),
            signer_nid=data.get("signer_nid", grantor_nid or ""),
            signature=data["signature"],
        )

    def unsigned_dict(self) -> dict[str, Any]:
        """Return the dict representation without the 'signature' field, for signing."""
        d = self.to_dict()
        d.pop("signature", None)
        return d


# ── RevokeFrame (0x22) ───────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class RevokeFrame(NpsFrame):
    """
    Certificate revocation frame (NPS-3 §5.3).
    Issued by the CA or an Operator to immediately invalidate a NID.
    """

    target_nid: str
    reason:     str   # key_compromise | ca_compromise | affiliation_changed | superseded | cessation_of_operation
    revoked_at: str
    signer_nid: str
    signature:  str
    serial:     str | None = None
    parent_nid: str | None = None

    def __post_init__(self) -> None:
        _validate_revoke_parent_rule(self.reason, self.parent_nid)

    @property
    def frame_type(self) -> FrameType:
        return FrameType.REVOKE

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d = {
            "frame":      "0x22",
            "target_nid": self.target_nid,
            "reason":     self.reason,
            "revoked_at": self.revoked_at,
            "signer_nid": self.signer_nid,
            "signature":  self.signature,
        }
        if self.serial is not None:
            d["serial"] = self.serial
        if self.parent_nid is not None:
            d["parent_nid"] = self.parent_nid
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RevokeFrame":
        return cls(
            target_nid=data.get("target_nid", data.get("nid")),
            reason=data["reason"],
            revoked_at=data["revoked_at"],
            signer_nid=data["signer_nid"],
            signature=data["signature"],
            serial=data.get("serial"),
            parent_nid=data.get("parent_nid"),
        )

    def unsigned_dict(self) -> dict[str, Any]:
        """Return the dict representation without the 'signature' field, for signing."""
        d = self.to_dict()
        d.pop("signature", None)
        return d
