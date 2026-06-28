# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Helpers for NDP cross-registry federation forwarding (NPS-4 §7.6)."""

from __future__ import annotations

from nps_sdk.ndp.error_codes import NDP_FEDERATION_LOOP


NDP_FORWARDED_BY_HEADER = "ndp-forwarded-by"
MAX_FEDERATION_HOPS = 3


class NdpFederationLoopError(ValueError):
    """Raised when a registry would forward a frame back to itself."""


def parse_forwarded_by(header: str | None) -> tuple[str, ...]:
    if not header:
        return ()
    return tuple(part.strip() for part in header.split(",") if part.strip())


def append_forwarded_by(own_nid: str, header: str | None) -> str | None:
    """Return the next header value, or ``None`` when the 3-hop limit drops it."""

    hops = parse_forwarded_by(header)
    if own_nid in hops:
        raise NdpFederationLoopError(f"{NDP_FEDERATION_LOOP}: own NID already appears in ndp-forwarded-by")
    if len(hops) >= MAX_FEDERATION_HOPS:
        return None
    return ", ".join((*hops, own_nid))
