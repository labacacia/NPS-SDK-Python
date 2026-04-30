# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""ACME wire constants (RFC 8555 + NPS-RFC-0002 §4.4)."""

# RFC 8555 ──────────────────────────────────────────────────────────────────
CONTENT_TYPE_JOSE_JSON = "application/jose+json"
CONTENT_TYPE_PROBLEM   = "application/problem+json"
CONTENT_TYPE_PEM_CERT  = "application/pem-certificate-chain"

# NPS-RFC-0002 §4.4 ────────────────────────────────────────────────────────
CHALLENGE_AGENT_01  = "agent-01"
IDENTIFIER_TYPE_NID = "nid"
