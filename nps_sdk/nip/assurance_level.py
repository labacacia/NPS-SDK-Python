# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Agent identity assurance level per NPS-RFC-0003 §5.1.1."""

from __future__ import annotations

import enum


class AssuranceLevel(enum.Enum):
    """Ordered: ANONYMOUS < ATTESTED < VERIFIED."""

    ANONYMOUS = ("anonymous", 0)
    ATTESTED  = ("attested",  1)
    VERIFIED  = ("verified",  2)

    def __init__(self, wire: str, rank: int) -> None:
        self._wire = wire
        self._rank = rank

    @property
    def wire(self) -> str:
        """Wire-form string ("anonymous" / "attested" / "verified")."""
        return self._wire

    @property
    def rank(self) -> int:
        """Numeric rank for ordering and ASN.1 ENUMERATED encoding (0..2)."""
        return self._rank

    def meets_or_exceeds(self, required: "AssuranceLevel") -> bool:
        return self._rank >= required._rank

    @classmethod
    def from_wire(cls, wire: str | None) -> "AssuranceLevel":
        if wire is None:
            return cls.ANONYMOUS
        for level in cls:
            if level._wire == wire:
                return level
        raise ValueError(f"Unknown assurance_level: {wire!r}")

    @classmethod
    def from_rank(cls, rank: int) -> "AssuranceLevel":
        for level in cls:
            if level._rank == rank:
                return level
        raise ValueError(f"Unknown assurance_level rank: {rank}")
