# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NDP error code wire constants — mirror of `spec/error-codes.md` NDP section."""

from __future__ import annotations

# ── Resolve ───────────────────────────────────────────────────────────────────
NDP_RESOLVE_NOT_FOUND  = "NDP-RESOLVE-NOT-FOUND"
NDP_RESOLVE_AMBIGUOUS  = "NDP-RESOLVE-AMBIGUOUS"
NDP_RESOLVE_TIMEOUT    = "NDP-RESOLVE-TIMEOUT"

# ── Announce ──────────────────────────────────────────────────────────────────
NDP_ANNOUNCE_SIGNATURE_INVALID = "NDP-ANNOUNCE-SIGNATURE-INVALID"
NDP_ANNOUNCE_NID_MISMATCH      = "NDP-ANNOUNCE-NID-MISMATCH"
NDP_ANNOUNCE_ROLE_REMOVED      = "NDP-ANNOUNCE-ROLE-REMOVED"
NDP_ANNOUNCE_ROLE_UNKNOWN      = "NDP-ANNOUNCE-ROLE-UNKNOWN"
NDP_ANNOUNCE_CONFLICT          = "NDP-ANNOUNCE-CONFLICT"

# ── Graph ─────────────────────────────────────────────────────────────────────
NDP_GRAPH_SEQ_ROLLBACK = "NDP-GRAPH-SEQ-ROLLBACK"
NDP_GRAPH_SEQ_GAP      = "NDP-GRAPH-SEQ-GAP"
# alpha.11 additions (GraphFrame §5 topology-snapshot format):
NDP_GRAPH_INVALID      = "NDP-GRAPH-INVALID"
NDP_GRAPH_TOO_LARGE    = "NDP-GRAPH-TOO-LARGE"

# ── Federation (alpha.11+) ────────────────────────────────────────────────────
NDP_FEDERATION_LOOP    = "NDP-FEDERATION-LOOP"

# ── Registry / Auth ───────────────────────────────────────────────────────────
NDP_ISSUER_NOT_ALLOWED   = "NDP-ISSUER-NOT-ALLOWED"
NDP_CA_ATTEST_REQUIRED   = "NDP-CA-ATTEST-REQUIRED"
NDP_REGISTRY_UNAVAILABLE = "NDP-REGISTRY-UNAVAILABLE"

# ── NPS status mapping ────────────────────────────────────────────────────────
from nps_sdk.core.status_codes import (  # noqa: E402
    NPS_AUTH_FORBIDDEN,
    NPS_AUTH_UNAUTHENTICATED,
    NPS_CLIENT_BAD_FRAME,
    NPS_CLIENT_CONFLICT,
    NPS_CLIENT_NOT_FOUND,
    NPS_LIMIT_PAYLOAD,
    NPS_SERVER_TIMEOUT,
    NPS_SERVER_UNAVAILABLE,
    NPS_STREAM_SEQ_GAP,
)

NDP_ERROR_TO_NPS_STATUS: dict[str, str] = {
    NDP_RESOLVE_NOT_FOUND:            NPS_CLIENT_NOT_FOUND,
    NDP_RESOLVE_AMBIGUOUS:            NPS_CLIENT_CONFLICT,
    NDP_RESOLVE_TIMEOUT:              NPS_SERVER_TIMEOUT,
    NDP_ANNOUNCE_SIGNATURE_INVALID:   NPS_AUTH_UNAUTHENTICATED,
    NDP_ANNOUNCE_NID_MISMATCH:        NPS_CLIENT_BAD_FRAME,
    NDP_ANNOUNCE_ROLE_REMOVED:        NPS_CLIENT_BAD_FRAME,
    NDP_ANNOUNCE_ROLE_UNKNOWN:        NPS_CLIENT_BAD_FRAME,
    NDP_ANNOUNCE_CONFLICT:            NPS_CLIENT_CONFLICT,
    NDP_GRAPH_SEQ_ROLLBACK:           NPS_CLIENT_BAD_FRAME,
    NDP_GRAPH_SEQ_GAP:                NPS_STREAM_SEQ_GAP,
    NDP_GRAPH_INVALID:                NPS_CLIENT_BAD_FRAME,
    NDP_GRAPH_TOO_LARGE:              NPS_LIMIT_PAYLOAD,
    NDP_FEDERATION_LOOP:              NPS_CLIENT_CONFLICT,
    NDP_ISSUER_NOT_ALLOWED:           NPS_AUTH_FORBIDDEN,
    NDP_CA_ATTEST_REQUIRED:           NPS_AUTH_UNAUTHENTICATED,
    NDP_REGISTRY_UNAVAILABLE:         NPS_SERVER_UNAVAILABLE,
}
