# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Server-side native NCP transport options. Mirrors the .NET ``NcpServerOptions``.
"""

from __future__ import annotations

import dataclasses
from typing import Awaitable, Callable, Optional, Tuple

import asyncio

from nps_sdk.core.frames import DEFAULT_MAX_PAYLOAD
from nps_sdk.ncp import preamble
from nps_sdk.ncp.handshake_profile import NcpHandshakeProfile

#: Hook signature: given the accepted (reader, writer), return a possibly-wrapped
#: (reader, writer) pair — e.g. after a TLS upgrade. Async.
AuthenticateHook = Callable[
    [asyncio.StreamReader, asyncio.StreamWriter],
    Awaitable[Tuple[asyncio.StreamReader, asyncio.StreamWriter]],
]


@dataclasses.dataclass(frozen=True)
class NcpServerOptions:
    """Server-side native NCP transport options."""

    #: Optional hook that wraps or authenticates the accepted streams before the
    #: NCP preamble is read. Use this to install TLS/mTLS.
    authenticate: Optional[AuthenticateHook] = None

    #: When true, :attr:`authenticate` must return a *different* stream pair,
    #: making accidental plaintext native mode fail fast.
    require_authenticated_stream: bool = False

    #: Maximum payload accepted for the initial HelloFrame. Defaults to the
    #: non-extended frame payload ceiling (65 535 bytes).
    max_hello_payload: int = DEFAULT_MAX_PAYLOAD

    #: Wall-clock budget for the preamble read.
    handshake_read_timeout: float = preamble.READ_TIMEOUT

    #: Separate wall-clock budget for the Hello header and payload.
    hello_read_timeout: float = 5.0

    #: Server capabilities used for deterministic negotiation.
    handshake_profile: NcpHandshakeProfile = dataclasses.field(
        default_factory=NcpHandshakeProfile)
