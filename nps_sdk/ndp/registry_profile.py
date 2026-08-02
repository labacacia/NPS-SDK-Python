# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Transport-independent NDP 0.12 registry conformance profile."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from nps_sdk.ndp.error_codes import (
    NDP_ANNOUNCE_CONFLICT,
    NDP_ANNOUNCE_PROFILE_VIOLATION,
    NDP_ANNOUNCE_SIGNATURE_INVALID,
    NDP_ANNOUNCE_STALE,
    NDP_CLUSTER_SPLIT,
    NDP_GRAPH_SEQ_ROLLBACK,
)

_EXCLUDED = {"frame", "signature", "health", "last_seen"}


class NdpRegistryDecision(str, Enum):
    """Portable Announce admission outcomes."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REFRESHED = "refreshed"
    REMOVED = "removed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class NdpRegistryAdmission:
    decision: NdpRegistryDecision
    error_code: str | None = None


@dataclass(frozen=True)
class NdpClusterSelection:
    nid: str | None
    epoch: int | None
    error_code: str | None = None


@dataclass
class _Entry:
    frame: dict[str, Any]
    signed_digest: str
    expires_at: datetime


def canonical_announce_json(frame: dict[str, Any]) -> str:
    """Return the NDP-specific canonical signed body."""

    root = {
        key: _without_nulls(value)
        for key, value in frame.items()
        if key not in _EXCLUDED and value is not None
    }
    root.setdefault("heartbeat_interval_ms", 60_000)
    return json.dumps(
        root,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def verify_announce_signature(
    frame: dict[str, Any],
    encoded_public_key: str,
    encoded_signature: str,
) -> bool:
    """Verify an ``ed25519:<base64url>`` NDP Announce signature."""

    prefix = "ed25519:"
    if not encoded_public_key.startswith(prefix) or not encoded_signature.startswith(prefix):
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            _decode_base64url(encoded_public_key[len(prefix):]),
        )
        public_key.verify(
            _decode_base64url(encoded_signature[len(prefix):]),
            canonical_announce_json(frame).encode("utf-8"),
        )
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


class NdpRegistryProfile:
    """NDP 0.12 in-memory registry state machine."""

    def __init__(self, security_profile: str = "local-dev") -> None:
        self.security_profile = security_profile
        self._entries: dict[str, _Entry] = {}
        self._highest_sequences: dict[str, int] = {}

    def apply_announce(
        self,
        frame: dict[str, Any],
        *,
        signature_valid: bool,
        received_at: datetime,
    ) -> NdpRegistryAdmission:
        if not signature_valid:
            return self._reject(NDP_ANNOUNCE_SIGNATURE_INVALID)

        nid = frame.get("nid")
        timestamp = _parse_time(frame.get("timestamp"))
        if not isinstance(nid, str) or not nid or timestamp is None:
            return self._reject(NDP_ANNOUNCE_PROFILE_VIOLATION)

        sequence_present = "graph_seq" in frame
        sequence = _uint_value(frame.get("graph_seq"))
        ttl = _uint_value(frame.get("ttl"), maximum=(1 << 32) - 1)
        if (
            (sequence_present and sequence is None)
            or (not sequence_present and self.security_profile != "local-dev")
            or ttl is None
        ):
            return self._reject(NDP_ANNOUNCE_PROFILE_VIOLATION)
        sequence = sequence or 0
        if not self._bridge_shape_is_valid(frame):
            return self._reject(NDP_ANNOUNCE_PROFILE_VIOLATION)
        if self.security_profile != "local-dev" and abs(
            (received_at - timestamp).total_seconds()
        ) > 300:
            return self._reject(NDP_ANNOUNCE_SIGNATURE_INVALID)

        digest = hashlib.sha256(
            canonical_announce_json(frame).encode("utf-8"),
        ).hexdigest()
        highest = self._highest_sequences.get(nid)
        if highest is not None:
            if sequence < highest:
                return self._reject(NDP_GRAPH_SEQ_ROLLBACK)
            if sequence == highest:
                current = self._entries.get(nid)
                if current is None:
                    return NdpRegistryAdmission(NdpRegistryDecision.DUPLICATE)
                if current.signed_digest != digest:
                    return self._reject(NDP_ANNOUNCE_CONFLICT)
                if self._same_liveness(current.frame, frame):
                    return NdpRegistryAdmission(NdpRegistryDecision.DUPLICATE)
                expires_at = _freshness_deadline(frame)
                if expires_at is None or expires_at <= received_at:
                    return self._reject(NDP_ANNOUNCE_STALE)
                self._entries[nid] = _Entry(copy.deepcopy(frame), digest, expires_at)
                return NdpRegistryAdmission(NdpRegistryDecision.REFRESHED)

        if ttl == 0:
            self._highest_sequences[nid] = sequence
            self._entries.pop(nid, None)
            return NdpRegistryAdmission(NdpRegistryDecision.REMOVED)

        expires_at = _freshness_deadline(frame)
        if expires_at is None or expires_at <= received_at:
            return self._reject(NDP_ANNOUNCE_STALE)
        self._highest_sequences[nid] = sequence
        self._entries[nid] = _Entry(copy.deepcopy(frame), digest, expires_at)
        return NdpRegistryAdmission(NdpRegistryDecision.ACCEPTED)

    def live_nids(self, now: datetime) -> list[str]:
        return sorted(
            nid for nid, entry in self._entries.items() if entry.expires_at > now
        )

    @property
    def highest_sequences(self) -> dict[str, int]:
        return dict(sorted(self._highest_sequences.items()))

    def has_stale_entry(self, now: datetime) -> bool:
        return any(entry.expires_at <= now for entry in self._entries.values())

    def resolve_cluster(self, cluster_anchor: str, now: datetime) -> NdpClusterSelection:
        members = [
            (
                nid,
                int(entry.frame.get("cluster_epoch", 1)),
            )
            for nid, entry in self._entries.items()
            if entry.expires_at > now
            and entry.frame.get("cluster_anchor") == cluster_anchor
            and "anchor" in self._roles(entry.frame)
        ]
        if not members:
            return NdpClusterSelection(None, None)
        top = max(epoch for _, epoch in members)
        leaders = sorted(nid for nid, epoch in members if epoch == top)
        if len(leaders) != 1:
            return NdpClusterSelection(None, None, NDP_CLUSTER_SPLIT)
        return NdpClusterSelection(leaders[0], top)

    def discover_bridges(
        self,
        direction: str,
        protocol: str,
        now: datetime,
    ) -> list[str]:
        if direction not in {"inbound", "outbound"}:
            raise ValueError("Bridge direction must be 'inbound' or 'outbound'.")
        field = (
            "bridge_inbound_protocols"
            if direction == "inbound"
            else "bridge_protocols"
        )
        return sorted(
            nid
            for nid, entry in self._entries.items()
            if entry.expires_at > now
            and entry.frame.get("health") != "draining"
            and self._is_bridge(entry.frame)
            and protocol in entry.frame.get(field, [])
        )

    @staticmethod
    def _reject(error_code: str) -> NdpRegistryAdmission:
        return NdpRegistryAdmission(NdpRegistryDecision.REJECTED, error_code)

    @classmethod
    def _bridge_shape_is_valid(cls, frame: dict[str, Any]) -> bool:
        outbound = _protocol_list(frame, "bridge_protocols")
        inbound = _protocol_list(frame, "bridge_inbound_protocols")
        if outbound is None or inbound is None:
            return False
        if cls._is_bridge(frame):
            return bool(outbound[1] or inbound[1])
        return not outbound[0] and not inbound[0]

    @classmethod
    def _is_bridge(cls, frame: dict[str, Any]) -> bool:
        return "bridge" in cls._roles(frame) or frame.get("node_type") == "bridge"

    @staticmethod
    def _roles(frame: dict[str, Any]) -> list[str]:
        roles = frame.get("node_roles", [])
        return roles if isinstance(roles, list) else []

    @staticmethod
    def _same_liveness(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return (
            left.get("health") == right.get("health")
            and left.get("last_seen") == right.get("last_seen")
        )


def _without_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_nulls(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


def _freshness_deadline(frame: dict[str, Any]) -> datetime | None:
    source = _parse_time(frame.get("last_seen") or frame.get("timestamp"))
    ttl = _uint_value(frame.get("ttl"), maximum=(1 << 32) - 1)
    return source + timedelta(seconds=ttl) if source and ttl is not None else None


def _uint_value(value: Any, *, maximum: int = (1 << 64) - 1) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


def _protocol_list(
    frame: dict[str, Any], field: str
) -> tuple[bool, list[str]] | None:
    if field not in frame:
        return False, []
    value = frame[field]
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        return None
    return True, value


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
