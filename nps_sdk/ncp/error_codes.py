# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NCP error code wire constants — mirror of `spec/error-codes.md` NCP section."""

from __future__ import annotations

# ── Anchor ────────────────────────────────────────────────────────────────────
NCP_ANCHOR_NOT_FOUND    = "NCP-ANCHOR-NOT-FOUND"
NCP_ANCHOR_SCHEMA_INVALID = "NCP-ANCHOR-SCHEMA-INVALID"
NCP_ANCHOR_ID_MISMATCH  = "NCP-ANCHOR-ID-MISMATCH"
NCP_ANCHOR_STALE        = "NCP-ANCHOR-STALE"

# ── Frame ─────────────────────────────────────────────────────────────────────
NCP_FRAME_UNKNOWN_TYPE    = "NCP-FRAME-UNKNOWN-TYPE"
NCP_FRAME_PAYLOAD_TOO_LARGE = "NCP-FRAME-PAYLOAD-TOO-LARGE"
NCP_FRAME_FLAGS_INVALID   = "NCP-FRAME-FLAGS-INVALID"

# ── Stream ────────────────────────────────────────────────────────────────────
NCP_STREAM_SEQ_GAP        = "NCP-STREAM-SEQ-GAP"
NCP_STREAM_NOT_FOUND      = "NCP-STREAM-NOT-FOUND"
NCP_STREAM_LIMIT_EXCEEDED = "NCP-STREAM-LIMIT-EXCEEDED"
NCP_STREAM_WINDOW_OVERFLOW = "NCP-STREAM-WINDOW-OVERFLOW"

# ── Encoding ──────────────────────────────────────────────────────────────────
NCP_ENCODING_UNSUPPORTED  = "NCP-ENCODING-UNSUPPORTED"

# ── Diff ──────────────────────────────────────────────────────────────────────
NCP_DIFF_FORMAT_UNSUPPORTED = "NCP-DIFF-FORMAT-UNSUPPORTED"

# ── E2E Encryption ────────────────────────────────────────────────────────────
NCP_ENC_NOT_NEGOTIATED = "NCP-ENC-NOT-NEGOTIATED"
NCP_ENC_AUTH_FAILED    = "NCP-ENC-AUTH-FAILED"

# ── Protocol / Preamble ───────────────────────────────────────────────────────
NCP_VERSION_INCOMPATIBLE = "NCP-VERSION-INCOMPATIBLE"
NCP_PREAMBLE_INVALID     = "NCP-PREAMBLE-INVALID"

# ── Native-mode session binding (NPS-RFC-0006 §6.3–§6.4) ─────────────────────
# Raised when the mTLS client-certificate NID does not match the session IdentFrame
# NID, or a resumed TLS session's certificate NID differs from the ticket-bound NID.
# NPS-CR-0009 §3.3 reuses it as the native-path failover trigger: it is what a client
# sees when it reconnects to an Anchor that no longer owns the cluster.
NCP_NID_MISMATCH         = "NCP-NID-MISMATCH"
NCP_EARLY_DATA_REJECTED  = "NCP-EARLY-DATA-REJECTED"

# ── NPS status mapping ────────────────────────────────────────────────────────
from nps_sdk.core.status_codes import (  # noqa: E402
    NPS_AUTH_UNAUTHENTICATED,
    NPS_CLIENT_NOT_FOUND,
    NPS_CLIENT_BAD_FRAME,
    NPS_CLIENT_CONFLICT,
    NPS_LIMIT_PAYLOAD,
    NPS_STREAM_SEQ_GAP,
    NPS_STREAM_NOT_FOUND,
    NPS_STREAM_LIMIT,
    NPS_SERVER_ENCODING_UNSUPPORTED,
    NPS_PROTO_VERSION_INCOMPATIBLE,
    NPS_PROTO_PREAMBLE_INVALID,
)

NCP_ERROR_TO_NPS_STATUS: dict[str, str] = {
    NCP_ANCHOR_NOT_FOUND:         NPS_CLIENT_NOT_FOUND,
    NCP_ANCHOR_SCHEMA_INVALID:    NPS_CLIENT_BAD_FRAME,
    NCP_ANCHOR_ID_MISMATCH:       NPS_CLIENT_CONFLICT,
    NCP_ANCHOR_STALE:             NPS_CLIENT_CONFLICT,
    NCP_FRAME_UNKNOWN_TYPE:       NPS_CLIENT_BAD_FRAME,
    NCP_FRAME_PAYLOAD_TOO_LARGE:  NPS_LIMIT_PAYLOAD,
    NCP_FRAME_FLAGS_INVALID:      NPS_CLIENT_BAD_FRAME,
    NCP_STREAM_SEQ_GAP:           NPS_STREAM_SEQ_GAP,
    NCP_STREAM_NOT_FOUND:         NPS_STREAM_NOT_FOUND,
    NCP_STREAM_LIMIT_EXCEEDED:    NPS_STREAM_LIMIT,
    NCP_STREAM_WINDOW_OVERFLOW:   NPS_STREAM_LIMIT,
    NCP_ENCODING_UNSUPPORTED:     NPS_SERVER_ENCODING_UNSUPPORTED,
    NCP_DIFF_FORMAT_UNSUPPORTED:  NPS_CLIENT_BAD_FRAME,
    NCP_ENC_NOT_NEGOTIATED:       NPS_CLIENT_BAD_FRAME,
    NCP_ENC_AUTH_FAILED:          NPS_CLIENT_BAD_FRAME,
    NCP_VERSION_INCOMPATIBLE:     NPS_PROTO_VERSION_INCOMPATIBLE,
    NCP_PREAMBLE_INVALID:         NPS_PROTO_PREAMBLE_INVALID,
    NCP_NID_MISMATCH:             NPS_AUTH_UNAUTHENTICATED,
    NCP_EARLY_DATA_REJECTED:      NPS_PROTO_VERSION_INCOMPATIBLE,
}
