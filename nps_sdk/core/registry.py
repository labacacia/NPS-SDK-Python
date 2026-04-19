# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Frame registry: maps FrameType byte codes to Python frame classes.
Built once, then used by the codec layer to resolve frame types at decode time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nps_sdk.core.exceptions import NpsFrameError
from nps_sdk.core.frames import FrameType

if TYPE_CHECKING:
    from nps_sdk.core.codec import NpsFrame


class FrameRegistry:
    """
    Maps FrameType byte codes to NpsFrame subclasses.
    Upper-layer protocols (NWP, NIP …) register their frame types via
    register() or the convenience create_default() factory.
    """

    def __init__(self, mapping: dict[FrameType, type["NpsFrame"]]) -> None:
        self._map: dict[FrameType, type["NpsFrame"]] = dict(mapping)

    def resolve(self, frame_type: FrameType) -> type["NpsFrame"]:
        try:
            return self._map[frame_type]
        except KeyError:
            raise NpsFrameError(
                f"No frame class registered for FrameType 0x{int(frame_type):02X} "
                f"({frame_type!r}). Register it via FrameRegistry."
            ) from None

    def register(self, frame_type: FrameType, cls: type["NpsFrame"]) -> None:
        self._map[frame_type] = cls

    @classmethod
    def create_default(cls) -> "FrameRegistry":
        """Registry pre-populated with all NCP core frames."""
        from nps_sdk.ncp.frames import (
            AnchorFrame,
            DiffFrame,
            StreamFrame,
            CapsFrame,
            HelloFrame,
            ErrorFrame,
        )

        return cls(
            {
                FrameType.ANCHOR: AnchorFrame,
                FrameType.DIFF:   DiffFrame,
                FrameType.STREAM: StreamFrame,
                FrameType.CAPS:   CapsFrame,
                FrameType.HELLO:  HelloFrame,
                FrameType.ERROR:  ErrorFrame,
            }
        )

    @classmethod
    def create_full(cls) -> "FrameRegistry":
        """Registry pre-populated with NCP + NWP + NIP + NDP + NOP frames."""
        from nps_sdk.nwp.frames import QueryFrame, ActionFrame
        from nps_sdk.nip.frames import IdentFrame, RevokeFrame, TrustFrame
        from nps_sdk.ndp.frames import AnnounceFrame, ResolveFrame, GraphFrame
        from nps_sdk.nop.frames import TaskFrame, DelegateFrame, SyncFrame, AlignStreamFrame

        registry = cls.create_default()
        registry.register(FrameType.QUERY,        QueryFrame)
        registry.register(FrameType.ACTION,       ActionFrame)
        registry.register(FrameType.IDENT,        IdentFrame)
        registry.register(FrameType.TRUST,        TrustFrame)
        registry.register(FrameType.REVOKE,       RevokeFrame)
        registry.register(FrameType.ANNOUNCE,     AnnounceFrame)
        registry.register(FrameType.RESOLVE,      ResolveFrame)
        registry.register(FrameType.GRAPH,        GraphFrame)
        registry.register(FrameType.TASK,         TaskFrame)
        registry.register(FrameType.DELEGATE,     DelegateFrame)
        registry.register(FrameType.SYNC,         SyncFrame)
        registry.register(FrameType.ALIGN_STREAM, AlignStreamFrame)
        return registry
