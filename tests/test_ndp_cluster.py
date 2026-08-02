# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Tests — NPS-CR-0009 multi-Anchor HA on the NDP wire.

Covers brief A §5.1 (cluster resolution), §5.5 (signature canonical-form
regressions) and the CR-0010 ``bridge_inbound_protocols`` canonical-form rule.
"""

from __future__ import annotations

import json

import pytest

from nps_sdk.ndp import (
    DEFAULT_CLUSTER_EPOCH,
    AnnounceFrame,
    InMemoryNdpRegistry,
    NdpAddress,
    NdpClusterSplitError,
    effective_cluster_epoch,
    resolve_cluster_from,
)
from nps_sdk.ndp.error_codes import NDP_CLUSTER_SPLIT, NDP_ERROR_TO_NPS_STATUS

CLUSTER = "urn:nps:cluster:api.test:main"


def _anchor(name: str, epoch: int | None, ttl: int = 3600) -> AnnounceFrame:
    """Brief A §5.1 fixture: an Anchor member of the ``api.test:main`` cluster."""
    return AnnounceFrame(
        nid=f"urn:nps:node:api.test:{name}",
        addresses=(NdpAddress(host="10.0.0.1", port=17433, protocol="nwp"),),
        capabilities=("topology.read",),
        ttl=ttl,
        timestamp="2026-07-05T00:00:00Z",
        signature="ed25519:placeholder",
        node_type="anchor",
        node_roles=("anchor",),
        cluster_anchor=CLUSTER,
        cluster_epoch=epoch,
    )


def _canonical(frame: AnnounceFrame) -> str:
    """The exact bytes the Ed25519 signature covers (``nip.identity`` §canonical JSON)."""
    return json.dumps(frame.unsigned_dict(), separators=(",", ":"), sort_keys=True)


# ── §5.1 cluster resolution ───────────────────────────────────────────────────

class TestClusterResolution:
    def test_resolves_the_highest_epoch_active_anchor(self):
        registry = InMemoryNdpRegistry()
        registry.announce(_anchor("anchor-a", 1))
        registry.announce(_anchor("anchor-b", 3))

        winner = registry.resolve_cluster(CLUSTER)

        assert winner is not None
        assert winner.nid == "urn:nps:node:api.test:anchor-b"
        assert winner.cluster_epoch == 3

    def test_absent_epoch_is_treated_as_one(self):
        registry = InMemoryNdpRegistry()
        registry.announce(_anchor("anchor-a", None))

        winner = registry.resolve_cluster(CLUSTER)

        assert winner is not None
        assert winner.cluster_epoch is None          # stored value untouched
        assert effective_cluster_epoch(winner) == DEFAULT_CLUSTER_EPOCH

    def test_split_brain_at_the_top_epoch_raises(self):
        registry = InMemoryNdpRegistry()
        registry.announce(_anchor("anchor-a", 2))
        registry.announce(_anchor("anchor-b", 2))

        with pytest.raises(NdpClusterSplitError) as exc:
            registry.resolve_cluster(CLUSTER)

        assert exc.value.error_code == NDP_CLUSTER_SPLIT
        assert exc.value.epoch == 2
        assert exc.value.cluster_anchor == CLUSTER

    def test_no_live_members_resolves_to_none(self):
        assert InMemoryNdpRegistry().resolve_cluster(CLUSTER) is None

    def test_two_members_both_omitting_the_epoch_split(self):
        # Both coerce to 1 and tie at the top: a real consequence, not special-cased away.
        registry = InMemoryNdpRegistry()
        registry.announce(_anchor("anchor-a", None))
        registry.announce(_anchor("anchor-b", None))

        with pytest.raises(NdpClusterSplitError) as exc:
            registry.resolve_cluster(CLUSTER)
        assert exc.value.epoch == DEFAULT_CLUSTER_EPOCH

    def test_ttl_expired_member_is_excluded_from_the_election(self):
        registry = InMemoryNdpRegistry()
        now = [1000.0]
        registry.clock = lambda: now[0]
        registry.announce(_anchor("anchor-b", 3, ttl=10))
        registry.announce(_anchor("anchor-a", 1, ttl=3600))

        now[0] = 1100.0                               # anchor-b's TTL has elapsed
        winner = registry.resolve_cluster(CLUSTER)

        assert winner is not None
        assert winner.nid == "urn:nps:node:api.test:anchor-a"

    def test_ttl_zero_announce_evicts_and_changes_the_winner(self):
        registry = InMemoryNdpRegistry()
        registry.announce(_anchor("anchor-a", 1))
        registry.announce(_anchor("anchor-b", 3))

        registry.announce(_anchor("anchor-b", 3, ttl=0))   # orderly shutdown

        winner = registry.resolve_cluster(CLUSTER)
        assert winner is not None
        assert winner.nid == "urn:nps:node:api.test:anchor-a"

    def test_other_clusters_do_not_participate(self):
        other = _anchor("anchor-z", 99)
        other = AnnounceFrame(**{**other.__dict__, "cluster_anchor": "urn:nps:cluster:other:main"})
        assert resolve_cluster_from([other], CLUSTER) is None

    def test_empty_cluster_anchor_is_rejected(self):
        with pytest.raises(ValueError):
            resolve_cluster_from([], "")

    def test_error_code_maps_to_conflict(self):
        assert NDP_ERROR_TO_NPS_STATUS[NDP_CLUSTER_SPLIT] == "NPS-CLIENT-CONFLICT"


# ── §5.5 signature canonical-form regression ──────────────────────────────────

class TestAnnounceCanonicalForm:
    def test_cluster_epoch_is_inside_the_signed_body(self):
        assert '"cluster_epoch":3' in _canonical(_anchor("anchor-b", 3))

    def test_absent_cluster_epoch_is_byte_identical_to_pre_cr0009(self):
        without = _anchor("anchor-a", None)
        assert "cluster_epoch" not in _canonical(without)

        # The exact pre-CR-0009 canonical form for this frame, spelled out so the
        # assertion keeps meaning even if the dataclass changes again.
        expected = json.dumps(
            {
                "nid": "urn:nps:node:api.test:anchor-a",
                "addresses": [{"host": "10.0.0.1", "port": 17433, "protocol": "nwp"}],
                "capabilities": ["topology.read"],
                "ttl": 3600,
                "timestamp": "2026-07-05T00:00:00Z",
                "heartbeat_interval_ms": 60_000,
                "node_type": "anchor",
                "node_roles": ["anchor"],
                "cluster_anchor": CLUSTER,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        assert _canonical(without) == expected

    def test_canonical_form_still_excludes_the_wire_only_fields(self):
        frame = AnnounceFrame(
            **{
                **_anchor("anchor-b", 3).__dict__,
                "health": "degraded",
                "last_seen": "2026-07-05T01:00:00Z",
            }
        )
        canonical = _canonical(frame)
        for excluded in ("signature", "health", "last_seen", "frame"):
            assert f'"{excluded}"' not in canonical
        assert '"cluster_epoch":3' in canonical

    def test_null_epoch_is_never_emitted_as_null_or_zero(self):
        d = _anchor("anchor-a", None).to_dict()
        assert "cluster_epoch" not in d

    def test_roundtrip_preserves_the_epoch(self):
        frame = _anchor("anchor-b", 7)
        assert AnnounceFrame.from_dict(frame.to_dict()).cluster_epoch == 7
        assert AnnounceFrame.from_dict(_anchor("a", None).to_dict()).cluster_epoch is None


# ── NPS-CR-0010 bridge_inbound_protocols on the same frame ────────────────────

class TestBridgeInboundProtocolsWire:
    def _bridge(self, inbound: tuple[str, ...] | None) -> AnnounceFrame:
        return AnnounceFrame(
            nid="urn:nps:node:api.test:bridge-1",
            addresses=(NdpAddress(host="10.0.0.9", port=17433, protocol="https"),),
            capabilities=(),
            ttl=3600,
            timestamp="2026-07-05T00:00:00Z",
            signature="ed25519:placeholder",
            node_type="bridge",
            node_roles=("bridge",),
            bridge_protocols=("http",),
            bridge_inbound_protocols=inbound,
        )

    def test_declared_set_round_trips(self):
        frame = self._bridge(("mcp", "a2a"))
        d = frame.to_dict()
        assert d["bridge_inbound_protocols"] == ["mcp", "a2a"]
        assert AnnounceFrame.from_dict(d).bridge_inbound_protocols == ("mcp", "a2a")

    def test_unset_is_omitted_entirely_not_null(self):
        d = self._bridge(None).to_dict()
        assert "bridge_inbound_protocols" not in d
        assert AnnounceFrame.from_dict(d).bridge_inbound_protocols is None

    def test_empty_declared_set_is_distinct_from_absent(self):
        # An outbound-only Bridge Node may declare an explicit empty inbound set.
        d = self._bridge(()).to_dict()
        assert d["bridge_inbound_protocols"] == []
        assert AnnounceFrame.from_dict(d).bridge_inbound_protocols == ()

    def test_is_inside_the_signed_body(self):
        assert '"bridge_inbound_protocols":["mcp","a2a"]' in _canonical(self._bridge(("mcp", "a2a")))

    def test_absent_leaves_canonical_bytes_unchanged(self):
        assert "bridge_inbound_protocols" not in _canonical(self._bridge(None))
