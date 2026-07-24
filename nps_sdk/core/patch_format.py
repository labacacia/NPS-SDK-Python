# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
DiffFrame ``patch_format`` value constants and helpers (NPS-1 §4.2).

Mirrors the .NET ``NcpPatchFormat`` static class.
"""

from __future__ import annotations

from nps_sdk.core.frames import EncodingTier

#: Default format. ``patch`` is an RFC 6902 JSON Patch array.
#: Compatible with all encoding tiers.
JSON_PATCH: str = "json_patch"

#: Compact binary format. ``binary_patch`` contains a changed-fields bitset
#: followed by MsgPack-encoded new values. MUST only be used in Tier-2 frames.
BINARY_BITSET: str = "binary_bitset"

#: The set of all recognised patch-format tokens.
ALL: frozenset[str] = frozenset({JSON_PATCH, BINARY_BITSET})


def is_valid(patch_format: str) -> bool:
    """Return ``True`` iff *patch_format* is a recognised patch-format token."""
    return patch_format in ALL


def validate(patch_format: str, tier: EncodingTier) -> None:
    """
    Validate a *patch_format* token against the frame's encoding *tier*.

    ``binary_bitset`` is only permitted in Tier-2 (MsgPack) frames; ``json_patch``
    is compatible with all tiers. Raises :exc:`ValueError` on violation.
    """
    if not is_valid(patch_format):
        raise ValueError(
            f"Unknown DiffFrame patch_format {patch_format!r}; "
            f"expected one of {sorted(ALL)}."
        )
    if patch_format == BINARY_BITSET and tier != EncodingTier.MSGPACK:
        raise ValueError(
            f"patch_format {BINARY_BITSET!r} MUST only be used in Tier-2 "
            f"(MsgPack) frames, got {NcpPatchFormatTier(tier)}."
        )


def NcpPatchFormatTier(tier: EncodingTier) -> str:  # noqa: N802 — internal helper
    return {
        EncodingTier.JSON: "json",
        EncodingTier.MSGPACK: "msgpack",
        EncodingTier.BINARY_VECTOR: "binary_vector.v1",
    }.get(tier, f"unknown:{int(tier)}")
