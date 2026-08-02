# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NCP v0.11 portable native-server admission and negotiation policy."""

from __future__ import annotations

import dataclasses
import enum

from nps_sdk.core import status_codes
from nps_sdk.core.frames import DEFAULT_MAX_PAYLOAD, EncodingTier, FrameHeader, FrameType
from nps_sdk.ncp import error_codes, preamble
from nps_sdk.ncp.frames import HelloFrame


@dataclasses.dataclass(frozen=True)
class NcpHandshakeProfile:
    min_version: str = "0.1"
    nps_version: str = "0.11"
    supported_encodings: tuple[str, ...] = (
        "msgpack", "json", "binary_vector.v1")
    supported_protocols: tuple[str, ...] = (
        "ncp", "nwp", "nip", "ndp", "nop")
    max_frame_payload: int = DEFAULT_MAX_PAYLOAD
    ext_support: bool = False
    max_concurrent_streams: int = 32


class NcpHandshakeAction(enum.Enum):
    CONTINUE = "continue"
    ACCEPT = "accept"
    SILENT_CLOSE = "silent_close"
    ERROR_CLOSE = "error_close"


@dataclasses.dataclass(frozen=True)
class NcpHandshakeDecision:
    action: NcpHandshakeAction
    status: str | None = None
    error: str | None = None
    diagnostic_error: str | None = None
    session_version: str | None = None
    negotiated_encoding: str | None = None
    enabled_encodings: tuple[str, ...] | None = None
    supported_protocols: tuple[str, ...] | None = None
    max_frame_payload: int | None = None
    ext_support: bool | None = None
    max_concurrent_streams: int | None = None


def evaluate_preamble(
    received: bytes,
    elapsed_ms: int,
    timeout_ms: int,
) -> NcpHandshakeDecision:
    if timeout_ms > 0 and elapsed_ms >= timeout_ms:
        return NcpHandshakeDecision(NcpHandshakeAction.SILENT_CLOSE)
    if len(received) < preamble.LENGTH:
        return NcpHandshakeDecision(NcpHandshakeAction.CONTINUE)
    if received[:preamble.LENGTH] != preamble.BYTES:
        return NcpHandshakeDecision(
            NcpHandshakeAction.SILENT_CLOSE,
            diagnostic_error=error_codes.NCP_PREAMBLE_INVALID)
    return NcpHandshakeDecision(NcpHandshakeAction.CONTINUE)


def evaluate_hello_header(
    header: FrameHeader,
    elapsed_ms: int,
    timeout_ms: int,
    max_hello_payload: int,
) -> NcpHandshakeDecision:
    if timeout_ms > 0 and elapsed_ms >= timeout_ms:
        return NcpHandshakeDecision(NcpHandshakeAction.SILENT_CLOSE)
    if (
        header.frame_type != FrameType.HELLO
        or header.encoding_tier != EncodingTier.JSON
        or header.is_encrypted
        or header.is_extended
        or header.payload_length > max_hello_payload
    ):
        return NcpHandshakeDecision(NcpHandshakeAction.SILENT_CLOSE)
    return NcpHandshakeDecision(NcpHandshakeAction.CONTINUE)


def negotiate_handshake(
    server: NcpHandshakeProfile,
    client: HelloFrame,
) -> NcpHandshakeDecision:
    try:
        server_min = _parse_version(server.min_version)
        server_max = _parse_version(server.nps_version)
        client_min = _parse_version(client.min_version or client.nps_version)
        client_max = _parse_version(client.nps_version)
    except ValueError:
        return _version_error()
    if server_min > server_max or client_min > client_max:
        return _version_error()
    overlap_min = max(server_min, client_min)
    overlap_max = min(server_max, client_max)
    if overlap_min > overlap_max:
        return _version_error()

    server_encodings = set(server.supported_encodings)
    stable = next(
        (token for token in client.supported_encodings
         if token in ("msgpack", "json") and token in server_encodings),
        None)
    if stable is None:
        return NcpHandshakeDecision(
            NcpHandshakeAction.ERROR_CLOSE,
            status=status_codes.NPS_SERVER_ENCODING_UNSUPPORTED,
            error=error_codes.NCP_ENCODING_UNSUPPORTED)

    server_protocols = set(server.supported_protocols)
    protocols = tuple(dict.fromkeys(
        token for token in client.supported_protocols
        if token in server_protocols))
    if (
        "ncp" not in protocols
        or client.max_frame_payload <= 0
        or server.max_frame_payload <= 0
        or client.max_concurrent_streams <= 0
        or server.max_concurrent_streams <= 0
    ):
        return _version_error()

    enabled = [stable]
    if (
        "binary_vector.v1" in server_encodings
        and "binary_vector.v1" in client.supported_encodings
    ):
        enabled.append("binary_vector.v1")

    return NcpHandshakeDecision(
        NcpHandshakeAction.ACCEPT,
        session_version=f"{overlap_max[0]}.{overlap_max[1]}",
        negotiated_encoding=stable,
        enabled_encodings=tuple(enabled),
        supported_protocols=protocols,
        max_frame_payload=min(
            server.max_frame_payload, client.max_frame_payload),
        ext_support=server.ext_support and client.ext_support,
        max_concurrent_streams=min(
            server.max_concurrent_streams, client.max_concurrent_streams),
    )


def _parse_version(value: str) -> tuple[int, int]:
    parts = value.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(value)
    return int(parts[0]), int(parts[1])


def _version_error() -> NcpHandshakeDecision:
    return NcpHandshakeDecision(
        NcpHandshakeAction.ERROR_CLOSE,
        status=status_codes.NPS_PROTO_VERSION_INCOMPATIBLE,
        error=error_codes.NCP_VERSION_INCOMPATIBLE)
