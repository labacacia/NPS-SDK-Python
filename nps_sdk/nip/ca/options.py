# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Configuration for the NIP CA service (NPS-3 §8) and the enrollment-tier
selector (NPS-CR-0005 §3). Port of the .NET ``NipCaOptions`` /
``EnrollmentTier``.
"""

from __future__ import annotations

import dataclasses
import enum


class EnrollmentTier(enum.IntEnum):
    """Registration-Authority enrollment tier (NPS-CR-0005 §3).

    Governs which gate an inbound registration request must pass before the CA
    issues an IdentFrame.
    """

    #: Tier 1 — operator-configured glob allowlist. Default.
    ALLOWLIST = 1
    #: Tier 2 — single-use bootstrap token (prefix ``nps-bootstrap-``).
    BOOTSTRAP_TOKEN = 2
    #: Tier 3 — all registrations queued as pending; operator approves/rejects.
    PENDING_QUEUE = 3


@dataclasses.dataclass
class NipCaOptions:
    """Configuration for the NIP CA service.

    Time-window fields (session validity, JWS skew) are expressed in **seconds**
    to stay Pythonic; the .NET reference uses ``TimeSpan`` with identical
    defaults (1 h / 24 h / 60 s / 5 min).
    """

    # ── Identity ───────────────────────────────────────────────────────────────
    ca_nid: str
    display_name: str | None = None
    base_url: str = "http://localhost"
    route_prefix: str = ""

    # ── Certificate lifetimes ──────────────────────────────────────────────────
    agent_cert_validity_days: int = 30
    node_cert_validity_days: int = 90
    renewal_window_days: int = 7
    group_cert_validity_days: int = 365

    # ── Session (NPS-CR-0003 §5.1.3) — seconds ─────────────────────────────────
    session_default_validity_seconds: int = 60 * 60          # 1 hour
    session_max_validity_seconds: int = 24 * 60 * 60         # 24 hours
    session_min_validity_seconds: int = 60                   # 60 seconds
    session_jws_clock_skew_seconds: int = 5 * 60             # ±5 minutes

    # ── Security ────────────────────────────────────────────────────────────────
    normalize_ocsp_response_time: bool = True
    algorithms: tuple[str, ...] = ("ed25519",)
    operator_api_key: str | None = None
    #: When non-None, only capabilities in this set may be requested at register.
    allowed_capabilities: frozenset[str] | None = None

    # ── Enrollment / RA (NPS-CR-0005 §3) ───────────────────────────────────────
    enrollment_tier: EnrollmentTier = EnrollmentTier.ALLOWLIST
    #: Glob patterns for Tier 1. ``["*"]`` = open CA (all identifiers allowed).
    enrollment_allowlist_patterns: tuple[str, ...] = ("*",)
    bootstrap_token_max_ttl_seconds: int = 24 * 60 * 60
    pending_queue_max_size: int = 1000
    pending_queue_max_age_seconds: int = 7 * 24 * 60 * 60
