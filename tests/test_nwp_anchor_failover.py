# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Tests — NPS-CR-0009 multi-Anchor HA on the NWP topology surface.

Covers brief A §5.2 (anchor_state sub-types), §5.6 (the epoch fence, which has no
.NET implementation and therefore no .NET tests), and NPS-2 §12.2 epoch stamping.
"""

from __future__ import annotations

import json

import httpx
import pytest

from nps_sdk.nwp import error_codes
from nps_sdk.nwp.anchor_client import AnchorState, MemberInfo, TopologySnapshot
from nps_sdk.nwp.anchor_server import (
    AnchorActionSpec,
    AnchorEpochGuard,
    AnchorNodeApp,
    AnchorNodeOptions,
    AnchorRole,
    InMemoryAnchorTopologyService,
    TopologyProtocolError,
    TopologyWire,
    _event_to_envelope,
    _snapshot_to_dict,
)


# ── §5.2 anchor_state sub-type wire shapes ────────────────────────────────────

class TestAnchorStateSubTypes:
    def test_failover_event_carries_successor_epoch_reason(self):
        ev = AnchorState.failover(
            "urn:nps:node:x:anchor-b", cluster_epoch=3, reason="active_lost")

        assert ev.field == "anchor_failover"
        assert ev.details["successor_nid"] == "urn:nps:node:x:anchor-b"
        assert ev.details["cluster_epoch"] == 3
        assert ev.details["reason"] == "active_lost"

    def test_quorum_lost_event_carries_counts(self):
        ev = AnchorState.quorum_lost(quorum_size=3, available=1)

        assert ev.field == "anchor_quorum_lost"
        assert ev.details == {"quorum_size": 3, "available": 1}

    def test_failover_reason_defaults_to_planned(self):
        assert AnchorState.failover("urn:nps:node:x:a", 2).details["reason"] == "planned"

    def test_sub_type_tags_live_on_the_event_type(self):
        assert AnchorState.FIELD_VERSION_REBASED == "version_rebased"
        assert AnchorState.FIELD_ANCHOR_FAILOVER == "anchor_failover"
        assert AnchorState.FIELD_ANCHOR_QUORUM_LOST == "anchor_quorum_lost"
        # ...and NOT on the shared topology constant bag.
        assert not hasattr(TopologyWire, "FIELD_ANCHOR_FAILOVER")

    def test_full_envelope_round_trip(self):
        env = _event_to_envelope("s1", AnchorState.failover("urn:nps:node:x:b", 4))
        assert env["event_type"] == "anchor_state"
        assert set(env["payload"]) == {"field", "details"}
        assert env["payload"]["field"] == "anchor_failover"
        assert env["payload"]["details"]["cluster_epoch"] == 4

        # A client decodes it back into the same sub-type.
        from nps_sdk.nwp.anchor_client import _parse_event

        decoded = _parse_event({**env, "seq": 4})
        assert isinstance(decoded, AnchorState)
        assert decoded.field == "anchor_failover"
        assert decoded.details["successor_nid"] == "urn:nps:node:x:b"

    def test_quorum_lost_envelope_round_trip(self):
        from nps_sdk.nwp.anchor_client import _parse_event

        env = _event_to_envelope("s1", AnchorState.quorum_lost(5, 2))
        decoded = _parse_event(env)
        assert decoded.field == "anchor_quorum_lost"
        assert decoded.details == {"quorum_size": 5, "available": 2}


# ── TopologySnapshot.cluster_epoch ────────────────────────────────────────────

class TestSnapshotClusterEpoch:
    def test_epoch_is_emitted_when_present_and_omitted_when_absent(self):
        snap = TopologySnapshot(version=1, anchor_nid="urn:nps:node:x:a",
                                cluster_size=0, members=[])
        assert "cluster_epoch" not in _snapshot_to_dict(snap)

        snap.cluster_epoch = 4
        assert _snapshot_to_dict(snap)["cluster_epoch"] == 4

    def test_client_reads_the_epoch_and_defaults_to_absent(self):
        base = {"version": 2, "anchor_nid": "urn:nps:node:x:a", "cluster_size": 0,
                "members": []}
        assert TopologySnapshot._from_dict(base).cluster_epoch is None
        assert TopologySnapshot._from_dict({**base, "cluster_epoch": 9}).cluster_epoch == 9


# ── §5.6 the epoch fence (no .NET implementation) ─────────────────────────────

class TestAnchorEpochGuard:
    def _guard(self, **kw) -> AnchorEpochGuard:
        return AnchorEpochGuard("urn:nps:node:x:anchor-a", **kw)

    def test_higher_inbound_epoch_fences_the_superseded_leader(self):
        guard = self._guard(own_epoch=2)

        with pytest.raises(TopologyProtocolError) as exc:
            guard.check_inbound(3, sender_anchor_nid="urn:nps:node:x:anchor-b")

        assert exc.value.nwp_error_code == error_codes.ANCHOR_EPOCH_FENCED
        assert exc.value.nps_status == "NPS-CLIENT-CONFLICT"
        assert guard.role == AnchorRole.STANDBY
        assert guard.fenced is True
        # ...and a terminal anchor_failover was emitted naming the new owner.
        ev = guard.events[-1]
        assert ev.field == "anchor_failover"
        assert ev.details == {
            "successor_nid": "urn:nps:node:x:anchor-b",
            "cluster_epoch": 3,
            "reason": "active_lost",
        }

    def test_fencing_closes_topology_streams(self):
        closed: list[bool] = []
        guard = AnchorEpochGuard("urn:nps:node:x:a", own_epoch=1,
                                 close_streams=lambda: closed.append(True))
        with pytest.raises(TopologyProtocolError):
            guard.check_inbound(2)
        assert closed == [True]

    @pytest.mark.parametrize("inbound", [None, 1, 2])
    def test_equal_or_lower_inbound_epoch_is_accepted(self, inbound):
        guard = self._guard(own_epoch=2)
        guard.check_inbound(inbound)                # must not raise
        assert guard.role == AnchorRole.ACTIVE

    def test_standby_rejects_topology_writes(self):
        guard = self._guard(own_epoch=2, role=AnchorRole.STANDBY)

        with pytest.raises(TopologyProtocolError) as exc:
            guard.check_inbound(1, is_topology_write=True)

        assert exc.value.nwp_error_code == error_codes.ANCHOR_NOT_LEADER
        assert exc.value.nps_status == "NPS-CLIENT-CONFLICT"

    def test_standby_still_serves_reads(self):
        guard = self._guard(role=AnchorRole.STANDBY)
        guard.check_inbound(1)                      # must not raise

    def test_quorum_lost_owner_becomes_read_only(self):
        guard = self._guard()
        event = guard.on_quorum_lost(quorum_size=3, available=1)

        assert event.field == "anchor_quorum_lost"
        assert guard.degraded is True
        guard.check_inbound(1)                      # reads still fine
        with pytest.raises(TopologyProtocolError) as exc:
            guard.check_inbound(1, is_topology_write=True)
        assert exc.value.nwp_error_code == error_codes.ANCHOR_NOT_LEADER

        guard.on_quorum_restored()
        guard.check_inbound(1, is_topology_write=True)   # writes accepted again

    def test_active_non_degraded_owner_accepts_writes(self):
        self._guard().check_inbound(1, is_topology_write=True)

    def test_take_ownership_promotes_and_emits_failover(self):
        guard = self._guard(own_epoch=1, role=AnchorRole.STANDBY)
        guard.degraded = True

        event = guard.on_take_ownership(5, reason="active_lost")

        assert guard.own_epoch == 5
        assert guard.role == AnchorRole.ACTIVE
        assert guard.degraded is False
        assert event.details == {
            "successor_nid": "urn:nps:node:x:anchor-a",
            "cluster_epoch": 5,
            "reason": "active_lost",
        }

    @pytest.mark.parametrize("epoch", [1, 0])
    def test_take_ownership_requires_a_strictly_greater_epoch(self, epoch):
        with pytest.raises(ValueError):
            self._guard(own_epoch=1).on_take_ownership(epoch)

    def test_own_epoch_must_start_at_one_or_more(self):
        with pytest.raises(ValueError):
            self._guard(own_epoch=0)

    def test_events_are_forwarded_to_the_callback(self):
        seen: list = []
        guard = AnchorEpochGuard("urn:nps:node:x:a", on_event=seen.append)
        guard.on_quorum_lost(3, 1)
        assert len(seen) == 1 and seen[0].field == "anchor_quorum_lost"

    def test_stamp_writes_the_current_epoch_onto_a_snapshot(self):
        guard = self._guard(own_epoch=6)
        snap = guard.stamp(TopologySnapshot(1, "urn:nps:node:x:a", 0, []))
        assert snap.cluster_epoch == 6


# ── The fence wired into the ASGI Anchor Node ─────────────────────────────────

def _app(guard: AnchorEpochGuard | None, **opts) -> AnchorNodeApp:
    return AnchorNodeApp(
        AnchorNodeOptions(
            node_id="urn:nps:node:x:anchor-a",
            path_prefix="/anchor",
            require_auth=False,
            actions={
                "topology.add_member": AnchorActionSpec(topology_write=True),
                "cluster.read": AnchorActionSpec(),
            },
            **opts,
        ),
        topology_service=InMemoryAnchorTopologyService(
            "urn:nps:node:x:anchor-a",
            members=[MemberInfo(nid="urn:nps:node:x:m1", node_roles=["memory"],
                                activation_mode="resident")],
        ),
        invoke_handler=lambda action_id, frame, ctx: _ok(),
        epoch_guard=guard,
    )


async def _ok():
    return {"ok": True}


def _client(app: AnchorNodeApp) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://anchor.test")


class TestAnchorNodeEpochWiring:
    async def test_every_snapshot_response_carries_the_cluster_epoch(self):
        async with _client(_app(AnchorEpochGuard("urn:nps:node:x:anchor-a", own_epoch=4))) as c:
            resp = await c.post("/anchor/query",
                                json={"type": TopologyWire.TYPE_SNAPSHOT,
                                      "topology": {"scope": "cluster"}})
        assert resp.status_code == 200
        assert resp.json()["data"][0]["cluster_epoch"] == 4

    async def test_stream_ack_carries_the_cluster_epoch(self):
        async with _client(_app(AnchorEpochGuard("urn:nps:node:x:anchor-a", own_epoch=7))) as c:
            resp = await c.post("/anchor/subscribe",
                                json={"type": TopologyWire.TYPE_STREAM,
                                      "topology": {"scope": "cluster"}})
        ack = json.loads(resp.text.splitlines()[0])
        assert ack["cluster_epoch"] == 7

    async def test_a_higher_inbound_epoch_fences_the_query_path_with_409(self):
        guard = AnchorEpochGuard("urn:nps:node:x:anchor-a", own_epoch=1)
        async with _client(_app(guard)) as c:
            resp = await c.post("/anchor/query",
                                json={"type": TopologyWire.TYPE_SNAPSHOT,
                                      "cluster_epoch": 9,
                                      "sender_anchor_nid": "urn:nps:node:x:anchor-b",
                                      "topology": {"scope": "cluster"}})
        assert resp.status_code == 409
        assert resp.json()["error"] == error_codes.ANCHOR_EPOCH_FENCED
        assert guard.role == AnchorRole.STANDBY

    async def test_a_higher_inbound_epoch_fences_the_subscribe_path(self):
        async with _client(_app(AnchorEpochGuard("urn:nps:node:x:anchor-a"))) as c:
            resp = await c.post("/anchor/subscribe",
                                json={"type": TopologyWire.TYPE_STREAM,
                                      "cluster_epoch": 2,
                                      "topology": {"scope": "cluster"}})
        assert resp.status_code == 409
        assert resp.json()["error"] == error_codes.ANCHOR_EPOCH_FENCED

    async def test_a_topology_write_at_a_standby_is_not_leader(self):
        guard = AnchorEpochGuard("urn:nps:node:x:anchor-a", role=AnchorRole.STANDBY)
        async with _client(_app(guard)) as c:
            resp = await c.post("/anchor/invoke",
                                json={"action_id": "topology.add_member", "params": {}})
        assert resp.status_code == 409
        assert resp.json()["error"] == error_codes.ANCHOR_NOT_LEADER

    async def test_a_non_write_action_at_a_standby_still_runs(self):
        guard = AnchorEpochGuard("urn:nps:node:x:anchor-a", role=AnchorRole.STANDBY)
        async with _client(_app(guard)) as c:
            resp = await c.post("/anchor/invoke",
                                json={"action_id": "cluster.read", "params": {}})
        assert resp.status_code == 200

    async def test_without_a_guard_the_node_behaves_exactly_as_before(self):
        async with _client(_app(None)) as c:
            snap = await c.post("/anchor/query",
                                json={"type": TopologyWire.TYPE_SNAPSHOT,
                                      "cluster_epoch": 99,
                                      "topology": {"scope": "cluster"}})
            write = await c.post("/anchor/invoke",
                                 json={"action_id": "topology.add_member", "params": {}})
        assert snap.status_code == 200
        assert "cluster_epoch" not in snap.json()["data"][0]
        assert write.status_code == 200
