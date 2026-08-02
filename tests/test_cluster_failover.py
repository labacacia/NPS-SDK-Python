# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Tests — NPS-CR-0009 §3.3 NCP failover connector and §3.4 NOP cluster delegation
(brief A §5.3 and §5.4).
"""

from __future__ import annotations

import pytest

from nps_sdk.ncp import NcpFailoverConnector, NcpProtocolError, is_failover_shaped
from nps_sdk.ncp.error_codes import NCP_ERROR_TO_NPS_STATUS, NCP_NID_MISMATCH
from nps_sdk.nop import ClusterAnchorInfo, ClusterDelegationResolver
from nps_sdk.nop.frames import DelegateFrame

CLUSTER = "urn:nps:cluster:x:main"
ANCHOR_A = "urn:nps:node:x:anchor-a"
ANCHOR_B = "urn:nps:node:x:anchor-b"


# ── §5.3 NCP failover connector ───────────────────────────────────────────────

def _resolver(*targets: tuple[str, int]):
    """A resolver that yields *targets* in order and records how often it was called."""
    queue = list(targets)
    calls: list[tuple[str, int]] = []

    async def resolve() -> tuple[str, int]:
        target = queue.pop(0) if queue else targets[-1]
        calls.append(target)
        return target

    return resolve, calls


class TestNcpFailoverConnector:
    async def test_reresolves_and_reconnects_after_nid_mismatch(self):
        resolve, calls = _resolver(("old-anchor", 17433), ("new-anchor", 17433))

        async def connect(host: str, port: int) -> str:
            if host == "old-anchor":
                raise NcpProtocolError(NCP_NID_MISMATCH, "session NID no longer matches")
            return f"session@{host}:{port}"

        session = await NcpFailoverConnector(resolve, connect).connect()

        assert session == "session@new-anchor:17433"
        assert calls == [("old-anchor", 17433), ("new-anchor", 17433)]

    async def test_reresolves_after_socket_loss(self):
        resolve, calls = _resolver(("anchor-1", 17433), ("anchor-2", 17433))

        async def connect(host: str, port: int) -> str:
            if host == "anchor-1":
                raise ConnectionRefusedError("connection refused")
            return f"session@{host}"

        assert await NcpFailoverConnector(resolve, connect).connect() == "session@anchor-2"
        assert len(calls) == 2

    async def test_non_failover_errors_propagate_immediately(self):
        resolve, calls = _resolver(("anchor-1", 17433), ("anchor-2", 17433))

        async def connect(host: str, port: int):
            raise NcpProtocolError("NCP-FRAME-FLAGS-INVALID", "bad flags")

        with pytest.raises(NcpProtocolError) as exc:
            await NcpFailoverConnector(resolve, connect).connect()

        assert exc.value.protocol_error_code == "NCP-FRAME-FLAGS-INVALID"
        assert len(calls) == 1                     # no retry, no second resolution

    async def test_exhausted_attempts_rethrow_the_last_failure(self):
        resolve, calls = _resolver(("anchor-1", 17433))
        failures = [TimeoutError("t1"), TimeoutError("t2"), TimeoutError("t3")]

        async def connect(host: str, port: int):
            raise failures.pop(0)

        with pytest.raises(TimeoutError) as exc:
            await NcpFailoverConnector(resolve, connect, max_attempts=3).connect()

        assert str(exc.value) == "t3"              # the LAST failure, original type
        assert len(calls) == 3

    async def test_a_single_attempt_still_resolves_once(self):
        resolve, calls = _resolver(("anchor-1", 17433))

        async def connect(host: str, port: int) -> str:
            return "s"

        assert await NcpFailoverConnector(resolve, connect, max_attempts=1).connect() == "s"
        assert len(calls) == 1

    async def test_the_first_attempt_also_resolves(self):
        # Re-resolution on every attempt, including the first, is what picks up a new
        # active Anchor rather than reconnecting to the loser.
        resolve, calls = _resolver(("anchor-1", 17433))

        async def connect(host: str, port: int) -> str:
            return "s"

        await NcpFailoverConnector(resolve, connect).connect()
        assert len(calls) == 1

    async def test_the_session_type_is_not_hardcoded(self):
        marker = object()

        async def resolve():
            return "h", 1

        async def connect(host, port):
            return marker

        assert await NcpFailoverConnector(resolve, connect).connect() is marker

    @pytest.mark.parametrize("kwargs", [
        {"resolve_active": None},
        {"connect": None},
        {"max_attempts": 0},
        {"max_attempts": -1},
    ])
    def test_constructor_validation(self, kwargs):
        async def ok(*a):
            return ("h", 1)

        args = {"resolve_active": ok, "connect": ok, **kwargs}
        with pytest.raises(ValueError):
            NcpFailoverConnector(**args)

    @pytest.mark.parametrize("exc,expected", [
        (ConnectionRefusedError("x"), True),
        (TimeoutError("x"), True),
        (OSError("x"), True),
        (NcpProtocolError(NCP_NID_MISMATCH), True),
        (NcpProtocolError("NCP-FRAME-FLAGS-INVALID"), False),
        (ValueError("x"), False),
    ])
    def test_is_failover_shaped(self, exc, expected):
        assert is_failover_shaped(exc) is expected

    def test_nid_mismatch_is_a_registered_code(self):
        assert NCP_NID_MISMATCH == "NCP-NID-MISMATCH"
        assert NCP_ERROR_TO_NPS_STATUS[NCP_NID_MISMATCH] == "NPS-AUTH-UNAUTHENTICATED"


# ── §5.4 NOP cluster delegation ───────────────────────────────────────────────

def _delegate(target_cluster_anchor: str | None = None) -> DelegateFrame:
    return DelegateFrame(
        parent_task_id="t1",
        subtask_id="s1",
        node_id="n1",
        target_agent_nid="urn:nps:agent:x:w1",
        action="do",
        delegated_scope={},
        deadline_at="2026-08-01T00:00:00Z",
        target_cluster_anchor=target_cluster_anchor,
    )


class TestClusterDelegationResolver:
    def test_without_a_cluster_target_it_uses_the_agent_nid(self):
        def never(cluster: str):
            raise AssertionError("NDP lookup must not be invoked")

        resolver = ClusterDelegationResolver(never)
        assert resolver.resolve_delegate_target(_delegate()) == "urn:nps:agent:x:w1"
        assert resolver.resolve_delegate_target(_delegate("")) == "urn:nps:agent:x:w1"

    def test_a_cluster_target_resolves_to_the_active_anchor_and_caches(self):
        calls: list[str] = []

        def lookup(cluster: str) -> ClusterAnchorInfo:
            calls.append(cluster)
            return ClusterAnchorInfo(ANCHOR_A, 1)

        resolver = ClusterDelegationResolver(lookup)
        assert resolver.resolve_delegate_target(_delegate(CLUSTER)) == ANCHOR_A
        assert resolver.resolve_delegate_target(_delegate(CLUSTER)) == ANCHOR_A
        assert len(calls) == 1                     # a cache hit performs no lookup

    def test_a_failover_event_redirects_subsequent_delegations(self):
        resolver = ClusterDelegationResolver(lambda c: ClusterAnchorInfo(ANCHOR_A, 1))
        resolver.resolve_active(CLUSTER)           # warm the cache at epoch 1

        assert resolver.on_anchor_failover(CLUSTER, ANCHOR_B, 2) is True
        assert resolver.resolve_delegate_target(_delegate(CLUSTER)) == ANCHOR_B

    def test_a_stale_failover_event_is_ignored(self):
        resolver = ClusterDelegationResolver(lambda c: ClusterAnchorInfo(ANCHOR_B, 3))
        resolver.resolve_active(CLUSTER)           # cache at epoch 3

        assert resolver.on_anchor_failover(CLUSTER, ANCHOR_A, 3) is False   # equal IS stale
        assert resolver.on_anchor_failover(CLUSTER, ANCHOR_A, 2) is False
        assert resolver.resolve_active(CLUSTER).active_nid == ANCHOR_B

    def test_a_first_observation_is_accepted_unconditionally(self):
        resolver = ClusterDelegationResolver(lambda c: None)
        assert resolver.on_anchor_failover(CLUSTER, ANCHOR_B, 1) is True
        assert resolver.resolve_active(CLUSTER) == ClusterAnchorInfo(ANCHOR_B, 1)

    def test_invalidate_forces_a_fresh_lookup(self):
        queue = [ClusterAnchorInfo(ANCHOR_A, 1), ClusterAnchorInfo(ANCHOR_B, 2)]
        resolver = ClusterDelegationResolver(lambda c: queue.pop(0))

        assert resolver.resolve_active(CLUSTER).active_nid == ANCHOR_A
        resolver.invalidate(CLUSTER)               # the NWP-ANCHOR-NOT-LEADER recovery path
        assert resolver.resolve_active(CLUSTER).active_nid == ANCHOR_B

    def test_invalidating_an_unknown_cluster_is_a_no_op(self):
        ClusterDelegationResolver(lambda c: None).invalidate("urn:nps:cluster:x:none")

    def test_negative_results_are_not_cached(self):
        calls: list[str] = []

        def lookup(cluster: str) -> None:
            calls.append(cluster)
            return None

        resolver = ClusterDelegationResolver(lookup)
        assert resolver.resolve_delegate_target(_delegate(CLUSTER)) is None
        assert resolver.resolve_delegate_target(_delegate(CLUSTER)) is None
        assert len(calls) == 2

    def test_argument_validation(self):
        with pytest.raises(ValueError):
            ClusterDelegationResolver(None)

        resolver = ClusterDelegationResolver(lambda c: None)
        with pytest.raises(ValueError):
            resolver.resolve_delegate_target(None)
        with pytest.raises(ValueError):
            resolver.resolve_active("")
        with pytest.raises(ValueError):
            resolver.on_anchor_failover("", ANCHOR_A, 1)
        with pytest.raises(ValueError):
            resolver.on_anchor_failover(CLUSTER, "", 1)

    def test_an_announce_frame_adapts_to_cluster_anchor_info(self):
        # The documented composition root: NDP resolution → ClusterAnchorInfo.
        from nps_sdk.ndp import AnnounceFrame, NdpAddress, InMemoryNdpRegistry

        registry = InMemoryNdpRegistry()
        for nid, epoch in ((ANCHOR_A, 1), (ANCHOR_B, 4)):
            registry.announce(AnnounceFrame(
                nid=nid,
                addresses=(NdpAddress(host="10.0.0.1", port=17433, protocol="nwp"),),
                capabilities=(), ttl=3600, timestamp="2026-07-05T00:00:00Z",
                signature="ed25519:placeholder", node_type="anchor",
                cluster_anchor=CLUSTER, cluster_epoch=epoch))

        def from_ndp(cluster: str) -> ClusterAnchorInfo | None:
            frame = registry.resolve_cluster(cluster)
            return None if frame is None else ClusterAnchorInfo(
                frame.nid, frame.cluster_epoch or 1)

        resolver = ClusterDelegationResolver(from_ndp)
        assert resolver.resolve_delegate_target(_delegate(CLUSTER)) == ANCHOR_B
