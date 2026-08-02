# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Failover reconnect / session continuity on the NCP native path (NPS-CR-0009 §3.3).

When an Anchor transfers cluster ownership it MAY close its native connections, and the
fenced prior leader sends a terminal ``anchor_failover`` before closing its streams. A
client therefore needs to *re-resolve* the active Anchor and reconnect — resolving once
up front and retrying the same address would just reconnect to the loser.

Both the resolver and the connect step are injected, so this composes with either NDP
highest-epoch resolution (§3.1) or a ``successor_nid`` lifted from a received
``anchor_failover`` event, and carries no NDP dependency of its own.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Generic, TypeVar

from nps_sdk.core.exceptions import NpsError
from nps_sdk.ncp.error_codes import NCP_NID_MISMATCH

__all__ = ["NcpFailoverConnector", "NcpProtocolError", "is_failover_shaped"]

TSession = TypeVar("TSession")

#: ``() -> (host, port)``, awaited once per attempt.
ResolveActive = Callable[[], Awaitable[tuple[str, int]]]
#: ``(host, port) -> session``.
Connect = Callable[[str, int], Awaitable[TSession]]


class NcpProtocolError(NpsError):
    """An NCP protocol error carrying its wire error code.

    ``protocol_error_code`` is what :func:`is_failover_shaped` inspects, so any error
    type exposing that attribute participates without subclassing this one.
    """

    def __init__(self, protocol_error_code: str, message: str | None = None) -> None:
        super().__init__(message or protocol_error_code)
        self.protocol_error_code = protocol_error_code


def is_failover_shaped(exc: BaseException) -> bool:
    """Whether *exc* means "this Anchor is gone; re-resolve and try the next one".

    Transport faults (``OSError`` — which in Python 3 subsumes ``IOError``, ``socket
    .error``, ``ConnectionRefusedError`` and ``TimeoutError``) and a protocol-level
    ``NCP-NID-MISMATCH`` qualify. Everything else propagates untouched.
    """
    if isinstance(exc, OSError):
        return True
    return getattr(exc, "protocol_error_code", None) == NCP_NID_MISMATCH


class NcpFailoverConnector(Generic[TSession]):
    """Connects to the currently-active Anchor, re-resolving on each attempt.

    ``resolve_active`` is called **once per attempt, before connect — including the
    first**. That re-resolution is the whole point: it is what picks up the new active
    Anchor after a failover. With the default ``max_attempts=2`` a single failure
    therefore performs exactly two resolutions.

    Generic in the session type, so the same connector wraps TCP, TLS, or a test double.
    """

    def __init__(
        self,
        resolve_active: ResolveActive,
        connect: Connect,
        max_attempts: int = 2,
    ) -> None:
        if resolve_active is None:
            raise ValueError("resolve_active is required")
        if connect is None:
            raise ValueError("connect is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._resolve = resolve_active
        self._connect = connect
        self._max_attempts = max_attempts

    async def connect(self) -> TSession:
        """Resolve and connect, retrying failover-shaped failures.

        :raises: the **last** captured failure, with its original type preserved, once
            the attempts are exhausted. A non-failover-shaped error propagates
            immediately, unwrapped and without a retry.
        """
        last_error: BaseException | None = None
        for _ in range(self._max_attempts):
            host, port = await self._resolve()
            try:
                return await self._connect(host, port)
            except BaseException as exc:  # noqa: BLE001 — re-raised below when not ours
                if not is_failover_shaped(exc):
                    raise
                last_error = exc
        assert last_error is not None      # max_attempts >= 1, so the loop ran
        raise last_error
