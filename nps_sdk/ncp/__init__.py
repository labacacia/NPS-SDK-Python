# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS NCP — Neural Communication Protocol frames and native-mode transport."""

from nps_sdk.ncp.frames import (
    SchemaField,
    FrameSchema,
    JsonPatchOperation,
    AnchorFrame,
    DiffFrame,
    StreamFrame,
    CapsFrame,
    HelloFrame,
    NcpHandshakeCapsFrame,
    ErrorFrame,
)
from nps_sdk.ncp import preamble
from nps_sdk.ncp.preamble import NcpPreambleInvalidError
from nps_sdk.ncp.failover import (
    NcpFailoverConnector,
    NcpProtocolError,
    is_failover_shaped,
)
from nps_sdk.ncp import error_codes
from nps_sdk.ncp.error_codes import NCP_ERROR_TO_NPS_STATUS
from nps_sdk.ncp.runtime_hardening import evaluate_runtime_hardening
from nps_sdk.ncp.encoding_policy import NcpEncodingPolicy
from nps_sdk.ncp.session import NcpSession, read_frame_header
from nps_sdk.ncp.client import NcpNativeClient, NcpHandshakeError
from nps_sdk.ncp.server import NcpServer, NcpServerConnection
from nps_sdk.ncp.server_options import NcpServerOptions
from nps_sdk.ncp.handshake_profile import (
    NcpHandshakeAction,
    NcpHandshakeDecision,
    NcpHandshakeProfile,
    evaluate_hello_header,
    evaluate_preamble,
    negotiate_handshake,
)

__all__ = [
    "SchemaField",
    "FrameSchema",
    "JsonPatchOperation",
    "AnchorFrame",
    "DiffFrame",
    "StreamFrame",
    "CapsFrame",
    "HelloFrame",
    "NcpHandshakeCapsFrame",
    "ErrorFrame",
    "preamble",
    "NcpPreambleInvalidError",
    "NcpFailoverConnector",
    "NcpProtocolError",
    "is_failover_shaped",
    "error_codes",
    "NCP_ERROR_TO_NPS_STATUS",
    "evaluate_runtime_hardening",
    "NcpEncodingPolicy",
    "NcpSession",
    "read_frame_header",
    "NcpNativeClient",
    "NcpHandshakeError",
    "NcpServer",
    "NcpServerConnection",
    "NcpServerOptions",
    "NcpHandshakeAction",
    "NcpHandshakeDecision",
    "NcpHandshakeProfile",
    "evaluate_hello_header",
    "evaluate_preamble",
    "negotiate_handshake",
]
