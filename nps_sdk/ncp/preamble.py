# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NCP native-mode connection preamble — the 8-byte ASCII constant
``b"NPS/1.0\\n"`` that every native-mode client MUST emit immediately
after the transport handshake and before its first HelloFrame.
Defined by NPS-RFC-0001 and NPS-1 NCP §2.6.1.

HTTP-mode connections do not use the preamble.
"""

from __future__ import annotations

LITERAL: str = "NPS/1.0\n"
LENGTH: int = 8
BYTES: bytes = LITERAL.encode("ascii")
READ_TIMEOUT: float = 10.0    # seconds — NPS-RFC-0001 §4.1
CLOSE_DEADLINE: float = 0.5   # seconds — max delay before closing on mismatch

ERROR_CODE: str = "NCP-PREAMBLE-INVALID"
STATUS_CODE: str = "NPS-PROTO-PREAMBLE-INVALID"


class NcpPreambleInvalidError(Exception):
    """Raised by :func:`validate` when the received preamble does not match."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.error_code = ERROR_CODE
        self.status_code = STATUS_CODE


def matches(buf: bytes) -> bool:
    """Return ``True`` iff *buf* starts with the 8-byte NPS/1.0 preamble."""
    return len(buf) >= LENGTH and buf[:LENGTH] == BYTES


def try_validate(buf: bytes) -> tuple[bool, str]:
    """
    Validate a presumed-preamble buffer.

    Returns ``(True, "")`` on success or ``(False, reason)`` on failure.
    Safe to call with shorter buffers.
    """
    if len(buf) < LENGTH:
        return False, f"short read ({len(buf)}/{LENGTH} bytes); peer is not speaking NCP"
    if buf[:LENGTH] != BYTES:
        if buf[:4] == b"NPS/":
            return False, "future-major-version NPS preamble; close with NPS-PREAMBLE-UNSUPPORTED-VERSION diagnostic"
        return False, "preamble mismatch; peer is not speaking NPS/1.x"
    return True, ""


def validate(buf: bytes) -> None:
    """Validate a presumed-preamble buffer, raising :exc:`NcpPreambleInvalidError` on mismatch."""
    ok, reason = try_validate(buf)
    if not ok:
        raise NcpPreambleInvalidError(reason)


def write(stream) -> None:
    """Write the preamble bytes to *stream* (a writable file-like object)."""
    stream.write(BYTES)


async def write_async(stream) -> None:
    """Write the preamble bytes to *stream* (an :class:`asyncio.StreamWriter`)."""
    stream.write(BYTES)
    await stream.drain()
