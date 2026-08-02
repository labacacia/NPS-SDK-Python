# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Highest-epoch cluster resolution for multi-Anchor HA (NPS-CR-0009 §3.1, NPS-4 §9).

A ``cluster_anchor`` cluster may be served by several Anchor Nodes. The live member
carrying the **highest** ``cluster_epoch`` is the current owner; an absent epoch means
1 (single-Anchor). Two live members tied at the top epoch is split-brain, and the
registry MUST refuse to resolve arbitrarily — it raises
:class:`NdpClusterSplitError` (``NDP-CLUSTER-SPLIT``).

The rule lives here as a free function plus a mixin so that *every* registry
implementation inherits the identical behaviour instead of reimplementing it — the
Python equivalent of the .NET default interface method on ``INdpRegistry``.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from nps_sdk.ndp.error_codes import NDP_CLUSTER_SPLIT
from nps_sdk.ndp.frames import AnnounceFrame

#: Epoch assumed for an announcement that omits ``cluster_epoch`` (NPS-CR-0009 §1.1).
DEFAULT_CLUSTER_EPOCH = 1


class NdpClusterSplitError(Exception):
    """Raised when a cluster has more than one live Anchor at the top epoch."""

    #: Wire error code (``spec/error-codes.md``); maps to ``NPS-CLIENT-CONFLICT`` / HTTP 409.
    error_code: str = NDP_CLUSTER_SPLIT

    def __init__(self, cluster_anchor: str, epoch: int) -> None:
        super().__init__(
            f"{NDP_CLUSTER_SPLIT}: cluster {cluster_anchor!r} has multiple live "
            f"active Anchors at epoch {epoch}."
        )
        self.cluster_anchor = cluster_anchor
        self.epoch = epoch


def effective_cluster_epoch(frame: AnnounceFrame) -> int:
    """Return *frame*'s epoch, coercing an absent value to 1.

    The coercion happens **at comparison time only** — the stored frame keeps its
    ``None`` so its signed canonical bytes never change.
    """
    return DEFAULT_CLUSTER_EPOCH if frame.cluster_epoch is None else int(frame.cluster_epoch)


def resolve_cluster_from(
    members: Iterable[AnnounceFrame],
    cluster_anchor: str,
) -> AnnounceFrame | None:
    """Pick the single highest-epoch Anchor of *cluster_anchor* out of *members*.

    *members* must already be filtered to **live** announcements — TTL expiry and
    ``ttl == 0`` eviction are the registry's job, and an evicted Anchor is simply out
    of the election.

    :returns: the winning :class:`AnnounceFrame`, or ``None`` when the cluster has no
        live members (an empty cluster is **not** an error).
    :raises ValueError: when *cluster_anchor* is empty.
    :raises NdpClusterSplitError: when two or more live members tie at the top epoch —
        including the case where both simply omit ``cluster_epoch`` and coerce to 1.
    """
    if not cluster_anchor:
        raise ValueError("cluster_anchor must be a non-empty NID")

    # Ordinal equality; no role filtering — any live entry naming this cluster runs.
    matching = [f for f in members if f.cluster_anchor == cluster_anchor]
    if not matching:
        return None

    top = max(effective_cluster_epoch(f) for f in matching)
    leaders = [f for f in matching if effective_cluster_epoch(f) == top]
    if len(leaders) > 1:
        raise NdpClusterSplitError(cluster_anchor, top)
    return leaders[0]


class _HasGetAll(Protocol):
    def get_all(self) -> list[AnnounceFrame]: ...


class NdpClusterResolutionMixin:
    """Gives any registry exposing ``get_all()`` the NPS-CR-0009 §3.1 resolution rule."""

    def resolve_cluster(self: _HasGetAll, cluster_anchor: str) -> AnnounceFrame | None:
        """Resolve *cluster_anchor* to its current active Anchor announcement.

        See :func:`resolve_cluster_from` for the exact rule and failure modes.
        """
        return resolve_cluster_from(self.get_all(), cluster_anchor)
