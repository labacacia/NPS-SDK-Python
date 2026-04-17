# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NdpAnnounceValidator — verifies AnnounceFrame Ed25519 signatures and NID consistency.
"""

from __future__ import annotations

import dataclasses
import threading

from nps_sdk.ndp.frames import AnnounceFrame
from nps_sdk.nip.identity import NipIdentity


# ── NdpAnnounceResult ─────────────────────────────────────────────────────────

@dataclasses.dataclass
class NdpAnnounceResult:
    """Result of validating an AnnounceFrame."""

    is_valid:   bool
    error_code: str | None = None
    message:    str | None = None

    @classmethod
    def ok(cls) -> "NdpAnnounceResult":
        return cls(is_valid=True)

    @classmethod
    def fail(cls, error_code: str, message: str) -> "NdpAnnounceResult":
        return cls(is_valid=False, error_code=error_code, message=message)


# ── NdpAnnounceValidator ──────────────────────────────────────────────────────

class NdpAnnounceValidator:
    """
    Validates incoming AnnounceFrames against registered public keys (NPS-4 §7).

    Usage::

        validator = NdpAnnounceValidator()
        validator.register_public_key(
            "urn:nps:node:api.example.com:products",
            NipIdentity.generate(...).pub_key_string,
        )
        result = validator.validate(announce_frame)
        if result.is_valid:
            registry.announce(announce_frame)
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        # nid → ed25519:{base64url} public key string
        self._keys: dict[str, str] = {}

    def register_public_key(self, nid: str, encoded_pub_key: str) -> None:
        """Register the Ed25519 public key for a given NID."""
        with self._lock:
            self._keys[nid] = encoded_pub_key

    def remove_public_key(self, nid: str) -> None:
        """Remove a previously registered public key."""
        with self._lock:
            self._keys.pop(nid, None)

    @property
    def known_public_keys(self) -> dict[str, str]:
        """Read-only snapshot of all registered NID → public-key mappings."""
        with self._lock:
            return dict(self._keys)

    def validate(self, frame: AnnounceFrame) -> NdpAnnounceResult:
        """
        Validate an AnnounceFrame.

        Checks:
        1. A public key is registered for frame.nid.
        2. The Ed25519 signature over the canonical JSON (excluding 'signature') is valid.

        Returns NdpAnnounceResult.ok() on success, or .fail(error_code, message) on failure.
        """
        with self._lock:
            pub_key = self._keys.get(frame.nid)

        if pub_key is None:
            return NdpAnnounceResult.fail(
                "NDP-ANNOUNCE-NID-MISMATCH",
                f"No public key registered for NID '{frame.nid}'.",
            )

        payload = frame.unsigned_dict()
        if not NipIdentity.verify_signature(pub_key, payload, frame.signature):
            return NdpAnnounceResult.fail(
                "NDP-ANNOUNCE-SIG-INVALID",
                f"Ed25519 signature verification failed for NID '{frame.nid}'.",
            )

        return NdpAnnounceResult.ok()
