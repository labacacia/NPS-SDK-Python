# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Delegation re-resolution against a multi-Anchor cluster (NPS-CR-0009 §3.4, NPS-5 §4.2).

``DelegateFrame.target_cluster_anchor`` names a *cluster*, not a node, so it must
resolve to that cluster's current active Anchor. On an ``anchor_failover`` the cached
answer is superseded and in-flight delegations must re-resolve to the successor before
retrying.

``resolve_cluster`` is injected, so NOP carries no NDP dependency: a composition root
adapts an ``AnnounceFrame`` to :class:`ClusterAnchorInfo` with
``(frame.nid, frame.cluster_epoch or 1)``.
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Callable

from nps_sdk.nop.frames import DelegateFrame

__all__ = ["ClusterAnchorInfo", "ClusterDelegationResolver"]


@dataclasses.dataclass(frozen=True)
class ClusterAnchorInfo:
    """The active owner of one cluster, at a known epoch."""

    active_nid: str
    cluster_epoch: int


#: ``(cluster_anchor) -> ClusterAnchorInfo | None`` — typically an NDP lookup.
ResolveCluster = Callable[[str], "ClusterAnchorInfo | None"]


class ClusterDelegationResolver:
    """Caches each cluster's active Anchor and keeps the cache monotonic per cluster.

    Thread-safe: a single lock makes every compare-then-set atomic.
    """

    def __init__(self, resolve_cluster: ResolveCluster) -> None:
        if resolve_cluster is None:
            raise ValueError("resolve_cluster is required")
        self._resolve_cluster = resolve_cluster
        self._lock = threading.Lock()
        self._active: dict[str, ClusterAnchorInfo] = {}

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve_delegate_target(self, frame: DelegateFrame) -> str | None:
        """Return the NID a delegation should actually be sent to.

        With no ``target_cluster_anchor`` this falls straight back to
        ``target_agent_nid`` and performs **no** cluster lookup at all.

        ``None`` means "cannot resolve" — the caller decides retry versus fail; the
        resolver never raises for this.
        """
        if frame is None:
            raise ValueError("frame is required")
        if not frame.target_cluster_anchor:
            return frame.target_agent_nid
        info = self.resolve_active(frame.target_cluster_anchor)
        return info.active_nid if info is not None else None

    def resolve_active(self, cluster_anchor: str) -> ClusterAnchorInfo | None:
        """Return the cached active Anchor of *cluster_anchor*, looking it up on a miss.

        A cache hit performs no lookup. Negative results are **not** cached, so a
        cluster that has not announced yet is retried on the next call.
        """
        if not cluster_anchor:
            raise ValueError("cluster_anchor must be a non-empty NID")

        with self._lock:
            cached = self._active.get(cluster_anchor)
        if cached is not None:
            return cached

        fresh = self._resolve_cluster(cluster_anchor)
        if fresh is not None:
            with self._lock:
                self._active[cluster_anchor] = fresh
        return fresh

    # ── Cache maintenance ─────────────────────────────────────────────────────

    def on_anchor_failover(
        self,
        cluster_anchor: str,
        successor_nid: str,
        cluster_epoch: int,
    ) -> bool:
        """Record an observed ``anchor_failover``. Returns whether it was applied.

        **Monotonic per cluster**: an epoch less than *or equal to* the cached one is
        stale and ignored. Equal is stale, not idempotent-accept — the epoch strictly
        increases on every ownership transfer, so a repeat cannot be new information.
        A first observation of an unseen cluster is accepted unconditionally.
        """
        if not cluster_anchor:
            raise ValueError("cluster_anchor must be a non-empty NID")
        if not successor_nid:
            raise ValueError("successor_nid must be a non-empty NID")

        with self._lock:
            current = self._active.get(cluster_anchor)
            if current is not None and cluster_epoch <= current.cluster_epoch:
                return False
            self._active[cluster_anchor] = ClusterAnchorInfo(successor_nid, cluster_epoch)
            return True

    def invalidate(self, cluster_anchor: str) -> None:
        """Drop the cached entry, forcing a fresh lookup on the next resolution.

        This is the documented recovery path after a dispatch is rejected with
        ``NWP-ANCHOR-NOT-LEADER``: invalidate, re-resolve, retry. Nothing else expires
        the cache — no TTL is modelled.
        """
        with self._lock:
            self._active.pop(cluster_anchor, None)
