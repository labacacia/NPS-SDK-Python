# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Worker-client abstraction for dispatching :class:`DelegateFrame`s and receiving
:class:`AlignStreamFrame` result streams (NPS-5 §3.2, §3.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, Sequence, runtime_checkable

from nps_sdk.nop.frames import AlignStreamFrame, DelegateFrame


@dataclass(frozen=True)
class PreflightResult:
    """Result returned by a Worker Agent for a preflight probe (NPS-5 §4.3)."""

    agent_nid: str
    available: bool
    available_cgn: int | None = None
    estimated_queue_ms: int | None = None
    capabilities: Sequence[str] | None = None
    unavailable_reason: str | None = None


@runtime_checkable
class INopWorkerClient(Protocol):
    """
    Dispatches :class:`DelegateFrame`s to Worker Agents and yields
    :class:`AlignStreamFrame` results. Implement to connect the orchestrator
    to real agents (HTTP/NWP, in-process, or a mock in tests).
    """

    def delegate(self, frame: DelegateFrame) -> AsyncIterator[AlignStreamFrame]:
        """
        Dispatch ``frame`` to the target Worker Agent and return an async stream
        of :class:`AlignStreamFrame`s. The final frame has ``is_final=True``.
        """
        ...

    async def preflight(
        self,
        agent_nid: str,
        action: str,
        *,
        estimated_npt: int = 0,
        required_capabilities: Sequence[str] | None = None,
    ) -> PreflightResult:
        """Send a lightweight preflight probe to ``agent_nid`` (NPS-5 §4)."""
        ...
