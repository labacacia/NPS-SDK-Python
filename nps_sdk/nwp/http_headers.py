# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NWP HTTP header and MIME constants (port of .NET ``NwpHttpHeaders``).

Header names are matched case-insensitively on the wire; the canonical
spellings below mirror the .NET reference and ``spec/NPS-2-NWP.md``.
"""

from __future__ import annotations

# ── Request headers ────────────────────────────────────────────────────────────
AGENT = "X-NWP-Agent"
BUDGET = "X-NWP-Budget"
IDENT = "X-NWP-Ident"
CAPABILITIES = "X-NWP-Capabilities"
DEPTH = "X-NWP-Depth"
ENCODING = "X-NWP-Encoding"
TOKENIZER = "X-NWP-Tokenizer"

# ── Response headers ────────────────────────────────────────────────────────────
SCHEMA = "X-NWP-Schema"
TOKENS = "X-NWP-Tokens"
TOKENS_NATIVE = "X-NWP-Tokens-Native"
TOKENIZER_USED = "X-NWP-Tokenizer-Used"
CACHED = "X-NWP-Cached"
NODE_TYPE = "X-NWP-Node-Type"
REQUEST_ID = "X-NWP-Request-Id"
REPUTATION_STATUS = "X-NWP-Reputation-Status"
BAN_EXPIRES = "X-NWP-Ban-Expires"

# ── MIME types ──────────────────────────────────────────────────────────────────
MIME_FRAME = "application/nwp-frame"
MIME_CAPSULE = "application/nwp-capsule"
MIME_MANIFEST = "application/nwp-manifest+json"
