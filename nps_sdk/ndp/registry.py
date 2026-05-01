# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
InMemoryNdpRegistry — thread-safe in-memory NDP node registry.

Stores AnnounceFrames keyed by NID, evicts entries whose TTL has expired,
and resolves nwp:// URLs to physical endpoints via NID prefix matching.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Callable

from nps_sdk.ndp.frames import AnnounceFrame, NdpResolveResult

if TYPE_CHECKING:
    from nps_sdk.ndp.dns_txt import DnsTxtLookup


class InMemoryNdpRegistry:
    """
    Thread-safe, TTL-aware in-memory registry for NDP announcements (NPS-4 §6).

    Usage::

        registry = InMemoryNdpRegistry()
        registry.announce(frame)
        result = registry.resolve("nwp://api.example.com/products")

    The ``clock`` property can be replaced in unit tests::

        registry.clock = lambda: fixed_datetime
    """

    def __init__(self) -> None:
        self._lock:    threading.Lock = threading.Lock()
        # nid → (frame, expiry_timestamp_seconds)
        self._store:   dict[str, tuple[AnnounceFrame, float]] = {}
        self.clock:    Callable[[], float] = time.time

    # ── Public API ────────────────────────────────────────────────────────────

    def announce(self, frame: AnnounceFrame) -> None:
        """
        Register or refresh a node announcement.
        TTL=0 immediately evicts the NID from the registry.
        """
        with self._lock:
            if frame.ttl == 0:
                self._store.pop(frame.nid, None)
                return
            expiry = self.clock() + frame.ttl
            self._store[frame.nid] = (frame, expiry)

    def resolve(self, target: str) -> NdpResolveResult | None:
        """
        Resolve a nwp:// target URL to a physical endpoint.

        Scans all live announcements and returns the first whose NID covers
        the given target (via authority+path prefix matching).
        Returns None if no live entry matches.
        """
        now = self.clock()
        with self._lock:
            snapshot = list(self._store.values())

        for frame, expiry in snapshot:
            if expiry <= now:
                continue
            if self.nwp_target_matches_nid(frame.nid, target):
                # Return the first https address if available, else the first address
                addr = next(
                    (a for a in frame.addresses if a.protocol == "https"),
                    frame.addresses[0] if frame.addresses else None,
                )
                if addr is None:
                    continue
                remaining_ttl = max(0, int(expiry - now))
                return NdpResolveResult(
                    host=addr.host,
                    port=addr.port,
                    ttl=remaining_ttl,
                )
        return None

    async def resolve_via_dns(
        self,
        target: str,
        dns_lookup: "DnsTxtLookup | None" = None,
    ) -> NdpResolveResult | None:
        """
        Resolve a nwp:// target URL to a physical endpoint, falling back to
        DNS TXT discovery (NPS-4 §5) when the in-memory registry has no match.

        Resolution order:

        1. Call :meth:`resolve` — return immediately if a live entry is found.
        2. Extract the hostname from *target* and query
           ``_nps-node.<hostname>`` TXT records.
        3. Parse each record with
           :func:`~nps_sdk.ndp.dns_txt.parse_nps_txt_record` and return the
           first valid result.

        :param target: A ``nwp://`` URL, e.g. ``nwp://api.example.com/products``.
        :param dns_lookup: Optional custom DNS lookup implementation
            (defaults to :class:`~nps_sdk.ndp.dns_txt.SystemDnsTxtLookup`).
        :returns: An :class:`NdpResolveResult`, or ``None`` if resolution fails.
        """
        # 1. Try in-memory registry first
        cached = self.resolve(target)
        if cached is not None:
            return cached

        # 2. Extract host and build the DNS label
        from nps_sdk.ndp.dns_txt import (  # local import to avoid circular dep
            SystemDnsTxtLookup,
            _extract_host_from_target,
            parse_nps_txt_record,
        )

        host = _extract_host_from_target(target)
        if not host:
            return None

        dns_label = f"_nps-node.{host}"

        if dns_lookup is None:
            dns_lookup = SystemDnsTxtLookup()

        try:
            records = await dns_lookup.lookup(dns_label)
        except Exception:
            return None

        # 3. Parse each TXT record, return first valid result
        for record_strings in records:
            txt = " ".join(record_strings)
            result = parse_nps_txt_record(txt, host)
            if result is not None:
                return result

        return None

    def get_all(self) -> list[AnnounceFrame]:
        """Return a snapshot of all currently live announcements."""
        now = self.clock()
        with self._lock:
            snapshot = list(self._store.values())
        return [frame for frame, expiry in snapshot if expiry > now]

    def get_by_nid(self, nid: str) -> AnnounceFrame | None:
        """Return the announcement for a given NID, or None if expired/absent."""
        now = self.clock()
        with self._lock:
            entry = self._store.get(nid)
        if entry is None:
            return None
        frame, expiry = entry
        return frame if expiry > now else None

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def nwp_target_matches_nid(nid: str, target: str) -> bool:
        """
        Check whether *nid* covers *target* via authority-segment + path-prefix matching.

        NID format: ``urn:nps:node:{authority}:{path-segment}``
        Target format: ``nwp://{authority}/{path}``

        The authority in the NID must equal the target authority, and the path
        segment in the NID must be a prefix of the target path.

        Examples::

            nwp_target_matches_nid(
                "urn:nps:node:api.example.com:products",
                "nwp://api.example.com/products/123")  # True

            nwp_target_matches_nid(
                "urn:nps:node:api.example.com:products",
                "nwp://api.example.com/orders")         # False
        """
        # NID: urn:nps:node:{authority}:{id-segment}
        parts = nid.split(":", 4)
        if len(parts) < 5 or parts[0] != "urn" or parts[1] != "nps":
            return False

        nid_authority   = parts[3]
        nid_path_prefix = parts[4].replace(":", "/")  # colons → slashes for sub-paths

        # Target: nwp://{authority}/{path}
        if not target.startswith("nwp://"):
            return False
        rest = target[len("nwp://"):]
        if "/" in rest:
            target_authority, target_path = rest.split("/", 1)
        else:
            target_authority, target_path = rest, ""

        if target_authority != nid_authority:
            return False

        # Path prefix check: nid_path_prefix must be a prefix of target_path
        if target_path == nid_path_prefix:
            return True
        if target_path.startswith(nid_path_prefix + "/"):
            return True
        return False
