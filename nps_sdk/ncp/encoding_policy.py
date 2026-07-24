# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Negotiated-encoding policy for an established NCP native-mode session.

The default tier is stable for ordinary frames; Tier-3 BinaryVector is an
optional extension for frame classes that explicitly bind to it (currently
QueryFrame). Mirrors the .NET ``NcpEncodingPolicy`` record.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable

from nps_sdk.core.exceptions import NpsEncodingUnsupportedError
from nps_sdk.core.frames import EncodingTier, FrameHeader, FrameType

_BINARY_VECTOR_TOKEN = "binary_vector.v1"


@dataclasses.dataclass(frozen=True)
class NcpEncodingPolicy:
    """Encoding policy negotiated for an established NCP native-mode session."""

    default_tier: EncodingTier
    binary_vector_enabled: bool = False

    @property
    def enabled_encodings(self) -> tuple[str, ...]:
        base = self.encoding_token(self.default_tier)
        if self.binary_vector_enabled:
            return (base, _BINARY_VECTOR_TOKEN)
        return (base,)

    def allows(self, tier: EncodingTier, frame_type: FrameType) -> bool:
        if tier == self.default_tier:
            return True
        return (
            tier == EncodingTier.BINARY_VECTOR
            and self.binary_vector_enabled
            and self._is_binary_vector_frame(frame_type)
        )

    def ensure_allows(self, header: FrameHeader) -> None:
        if self.allows(header.encoding_tier, header.frame_type):
            return
        raise NpsEncodingUnsupportedError(
            f"Frame type 0x{int(header.frame_type):02X} used "
            f"{self.encoding_token(header.encoding_tier)}, but the negotiated "
            f"session policy allows {', '.join(self.enabled_encodings)}."
        )

    @classmethod
    def from_enabled_encodings(
        cls,
        default_tier: EncodingTier,
        enabled_encodings: Iterable[str] | None,
    ) -> "NcpEncodingPolicy":
        enabled = list(enabled_encodings) if enabled_encodings is not None else []
        return cls(default_tier, _BINARY_VECTOR_TOKEN in enabled)

    @staticmethod
    def encoding_token(tier: EncodingTier) -> str:
        if tier == EncodingTier.JSON:
            return "json"
        if tier == EncodingTier.MSGPACK:
            return "msgpack"
        if tier == EncodingTier.BINARY_VECTOR:
            return _BINARY_VECTOR_TOKEN
        return f"unknown:{int(tier)}"

    @staticmethod
    def _is_binary_vector_frame(frame_type: FrameType) -> bool:
        return frame_type == FrameType.QUERY
