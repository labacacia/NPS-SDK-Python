# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS native status-code constants and HTTP mapping — mirror of spec/status-codes.md."""

from __future__ import annotations

# ── Success (OK) ──────────────────────────────────────────────────────────────
NPS_OK            = "NPS-OK"
NPS_OK_ACCEPTED   = "NPS-OK-ACCEPTED"
NPS_OK_NO_CONTENT = "NPS-OK-NO-CONTENT"

# ── Client Errors (CLIENT) ───────────────────────────────────────────────────
NPS_CLIENT_BAD_FRAME    = "NPS-CLIENT-BAD-FRAME"
NPS_CLIENT_BAD_PARAM    = "NPS-CLIENT-BAD-PARAM"
NPS_CLIENT_NOT_FOUND    = "NPS-CLIENT-NOT-FOUND"
NPS_CLIENT_CONFLICT     = "NPS-CLIENT-CONFLICT"
NPS_CLIENT_GONE         = "NPS-CLIENT-GONE"
NPS_CLIENT_UNPROCESSABLE = "NPS-CLIENT-UNPROCESSABLE"

# ── Authentication & Authorization (AUTH) ────────────────────────────────────
NPS_AUTH_UNAUTHENTICATED = "NPS-AUTH-UNAUTHENTICATED"
NPS_AUTH_FORBIDDEN       = "NPS-AUTH-FORBIDDEN"

# ── Resource Limits (LIMIT) ──────────────────────────────────────────────────
NPS_LIMIT_RATE    = "NPS-LIMIT-RATE"
NPS_LIMIT_BUDGET  = "NPS-LIMIT-BUDGET"
NPS_LIMIT_PAYLOAD = "NPS-LIMIT-PAYLOAD"

# ── Server Errors (SERVER) ───────────────────────────────────────────────────
NPS_SERVER_INTERNAL              = "NPS-SERVER-INTERNAL"
NPS_SERVER_UNSUPPORTED           = "NPS-SERVER-UNSUPPORTED"
NPS_SERVER_UNAVAILABLE           = "NPS-SERVER-UNAVAILABLE"
NPS_SERVER_TIMEOUT               = "NPS-SERVER-TIMEOUT"
NPS_SERVER_ENCODING_UNSUPPORTED  = "NPS-SERVER-ENCODING-UNSUPPORTED"
NPS_DOWNSTREAM_UNAVAILABLE       = "NPS-DOWNSTREAM-UNAVAILABLE"

# ── Stream (STREAM) ──────────────────────────────────────────────────────────
NPS_STREAM_SEQ_GAP   = "NPS-STREAM-SEQ-GAP"
NPS_STREAM_NOT_FOUND = "NPS-STREAM-NOT-FOUND"
NPS_STREAM_LIMIT     = "NPS-STREAM-LIMIT"

# ── Protocol-Level (PROTO) ───────────────────────────────────────────────────
NPS_PROTO_VERSION_INCOMPATIBLE = "NPS-PROTO-VERSION-INCOMPATIBLE"
NPS_PROTO_PREAMBLE_INVALID     = "NPS-PROTO-PREAMBLE-INVALID"

# ── HTTP mapping ─────────────────────────────────────────────────────────────
# Maps each NPS status code to its canonical HTTP status code.
# Where the spec lists two HTTP codes (e.g. 408/504 for SERVER-TIMEOUT,
# 502/503 for DOWNSTREAM-UNAVAILABLE), the first listed in the spec is used.
NPS_STATUS_TO_HTTP: dict[str, int] = {
    NPS_OK:                            200,
    NPS_OK_ACCEPTED:                   202,
    NPS_OK_NO_CONTENT:                 204,
    NPS_CLIENT_BAD_FRAME:              400,
    NPS_CLIENT_BAD_PARAM:              400,
    NPS_CLIENT_NOT_FOUND:              404,
    NPS_CLIENT_CONFLICT:               409,
    NPS_CLIENT_GONE:                   410,
    NPS_CLIENT_UNPROCESSABLE:          422,
    NPS_AUTH_UNAUTHENTICATED:          401,
    NPS_AUTH_FORBIDDEN:                403,
    NPS_LIMIT_RATE:                    429,
    NPS_LIMIT_BUDGET:                  429,
    NPS_LIMIT_PAYLOAD:                 413,
    NPS_SERVER_INTERNAL:               500,
    NPS_SERVER_UNSUPPORTED:            501,
    NPS_SERVER_UNAVAILABLE:            503,
    NPS_SERVER_TIMEOUT:                408,
    NPS_SERVER_ENCODING_UNSUPPORTED:   415,
    NPS_DOWNSTREAM_UNAVAILABLE:        502,
    NPS_STREAM_SEQ_GAP:                422,
    NPS_STREAM_NOT_FOUND:              404,
    NPS_STREAM_LIMIT:                  429,
    NPS_PROTO_VERSION_INCOMPATIBLE:    426,
    NPS_PROTO_PREAMBLE_INVALID:        400,
}


def to_http_status(nps_code: str) -> int:
    """Return the HTTP status code for *nps_code*.

    Falls back to 500 for any unrecognised status string so that callers
    always receive a valid integer even if a new code is not yet in the table.
    """
    return NPS_STATUS_TO_HTTP.get(nps_code, 500)
