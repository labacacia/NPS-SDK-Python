# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
AnchorFrameCache — in-process cache for AnchorFrame instances (NPS-1 §4.1).

Design notes:
- anchor_id is sha256:{64 hex chars} of the canonical (field-sorted) schema JSON.
- Idempotent set(): identical schemas produce the same key; no duplicate entries.
- Anchor poisoning protection: same anchor_id + different schema → NpsAnchorPoisonError.
- TTL is implemented via a simple timestamp-based expiry (no external dependency).
- Thread-safety: not required for single-threaded async use; add a threading.Lock
  if used from multiple OS threads.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import TYPE_CHECKING

from nps_sdk.core.exceptions import NpsAnchorNotFoundError, NpsAnchorPoisonError

if TYPE_CHECKING:
    from nps_sdk.ncp.frames import AnchorFrame, FrameSchema


class AnchorFrameCache:
    """
    In-process cache for AnchorFrame instances, keyed by sha256 anchor_id.
    """

    def __init__(self) -> None:
        # Stored as: anchor_id → (AnchorFrame, expires_at)
        self._store: dict[str, tuple["AnchorFrame", float]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def set(self, frame: "AnchorFrame") -> str:
        """
        Store *frame* and return its canonical anchor_id.

        If an entry already exists for the same anchor_id, the schemas are compared.
        A mismatch raises NpsAnchorPoisonError (NPS-1 §7.2).

        Returns:
            The canonical ``sha256:{64 hex chars}`` anchor_id.
        """
        anchor_id = (
            frame.anchor_id
            if frame.anchor_id.startswith("sha256:")
            else self.compute_anchor_id(frame.schema)
        )

        if anchor_id in self._store:
            existing_frame, expires_at = self._store[anchor_id]
            if time.monotonic() < expires_at:
                if not self._schemas_equal(existing_frame.schema, frame.schema):
                    raise NpsAnchorPoisonError(anchor_id)
                # Same schema — idempotent; refresh TTL

        expires_at = time.monotonic() + frame.ttl
        self._store[anchor_id] = (frame, expires_at)
        return anchor_id

    def get(self, anchor_id: str) -> "AnchorFrame | None":
        """Return the cached frame, or *None* if not present or expired."""
        entry = self._store.get(anchor_id)
        if entry is None:
            return None
        frame, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[anchor_id]
            return None
        return frame

    def get_required(self, anchor_id: str) -> "AnchorFrame":
        """Return the cached frame, or raise NpsAnchorNotFoundError."""
        frame = self.get(anchor_id)
        if frame is None:
            raise NpsAnchorNotFoundError(anchor_id)
        return frame

    def invalidate(self, anchor_id: str) -> None:
        """Remove an entry from the cache (no-op if not present)."""
        self._store.pop(anchor_id, None)

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._store)

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def compute_anchor_id(schema: "FrameSchema") -> str:
        """
        Compute the deterministic ``sha256:{64 hex chars}`` anchor_id for *schema*.
        Fields are sorted by name before hashing to ensure order-independence
        (NPS-1 §4.1).
        """
        sorted_fields = sorted(
            [
                {k: v for k, v in f.items() if v is not None}
                for f in [
                    {
                        "name":     field.name,
                        "type":     field.type,
                        "semantic": field.semantic,
                        "nullable": field.nullable,
                    }
                    for field in schema.fields
                ]
            ],
            key=lambda d: d["name"],
        )
        canonical = json.dumps(sorted_fields, separators=(",", ":"), sort_keys=True)
        digest    = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    # ── Private ───────────────────────────────────────────────────────────────

    def _evict_expired(self) -> None:
        now     = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]

    @staticmethod
    def _schemas_equal(a: "FrameSchema", b: "FrameSchema") -> bool:
        return AnchorFrameCache.compute_anchor_id(a) == AnchorFrameCache.compute_anchor_id(b)
