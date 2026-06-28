# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""End-to-end tests for the pure-ASGI Anchor Node server.

The server is driven both with raw httpx requests and with the real
``AnchorNodeClient`` (the wire consumer) over ``httpx.ASGITransport``.
"""

import json

import httpx
import pytest

from nps_sdk.nwp import error_codes
from nps_sdk.nwp.anchor_client import AnchorNodeClient, MemberInfo, ResyncRequired
from nps_sdk.nwp.anchor_server import (
    AnchorActionError,
    AnchorActionSpec,
    AnchorNodeApp,
    AnchorNodeOptions,
    InMemoryAnchorTopologyService,
)
from nps_sdk.nwp.reputation import (
    DefaultReputationEvaluator,
    ReputationPolicy,
    ReputationRule,
)

PREFIX = "/gw"
ANCHOR_NID = "urn:nps:node:anchor.example.com:svc"


def _client_for(app: AnchorNodeApp, *, agent: str | None = "urn:nps:agent:tester") -> httpx.AsyncClient:
    headers = {"X-NWP-Agent": agent} if agent else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://anchor",
        headers=headers,
    )


def _members() -> list[MemberInfo]:
    return [
        MemberInfo(nid="urn:nps:node:w1", node_roles=["worker"], activation_mode="resident"),
        MemberInfo(nid="urn:nps:node:w2", node_roles=["worker"], activation_mode="ephemeral",
                   tags=["gpu"]),
    ]


def _base_options(**kw) -> AnchorNodeOptions:
    opts = dict(
        node_id=ANCHOR_NID,
        path_prefix=PREFIX,
        actions={"orders.create": AnchorActionSpec(description="create order",
                                                   result_anchor="nps:orders:result",
                                                   estimated_cgn=10)},
    )
    opts.update(kw)
    return AnchorNodeOptions(**opts)


# ── Manifest ────────────────────────────────────────────────────────────────────

class TestManifest:
    async def test_nwm_basic_shape(self) -> None:
        app = AnchorNodeApp(_base_options(display_name="Svc"))
        async with _client_for(app) as http:
            resp = await http.get(f"{PREFIX}/.nwm")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/nwp-manifest+json"
        assert resp.headers["x-nwp-node-type"] == "anchor"
        m = resp.json()
        assert m["nwp"] == "0.4"
        assert m["node_id"] == ANCHOR_NID
        assert m["node_type"] == "anchor"
        assert m["display_name"] == "Svc"
        assert m["auth"]["required"] is True
        assert m["auth"]["identity_type"] == "nip-cert"
        assert m["endpoints"] == {"invoke": f"{PREFIX}/invoke", "schema": f"{PREFIX}/.schema"}
        assert m["actions"][0]["action_id"] == "orders.create"
        assert m["actions"][0]["async"] is False

    async def test_nwm_splices_cgn_reputation_trust(self) -> None:
        policy = ReputationPolicy(
            log_sources=["https://log.example.com"],
            ban_on=[ReputationRule(incident="*", severity=">=critical")],
        )
        app = AnchorNodeApp(_base_options(cgn_limit=500, reputation_policy=policy,
                                          trust_anchors=["urn:nps:org:root"]))
        async with _client_for(app) as http:
            m = (await http.get(f"{PREFIX}/.nwm")).json()
        assert m["token_budget"] == {"cgn_limit": 500, "profile": "cgn.v1"}
        assert m["reputation_policy"]["log_sources"] == ["https://log.example.com"]
        assert m["trust_anchors"] == ["urn:nps:org:root"]

    async def test_actions_endpoint(self) -> None:
        app = AnchorNodeApp(_base_options())
        async with _client_for(app) as http:
            resp = await http.get(f"{PREFIX}/actions")
        assert resp.status_code == 200
        assert "orders.create" in resp.json()["actions"]


# ── Auth gate ───────────────────────────────────────────────────────────────────

class TestAuthGate:
    async def test_missing_agent_header_401(self) -> None:
        app = AnchorNodeApp(_base_options())
        async with _client_for(app, agent=None) as http:
            resp = await http.get(f"{PREFIX}/.nwm")
        assert resp.status_code == 401
        assert resp.json()["error"] == error_codes.AUTH_NID_SCOPE_VIOLATION

    async def test_auth_disabled_allows_anonymous(self) -> None:
        app = AnchorNodeApp(_base_options(require_auth=False))
        async with _client_for(app, agent=None) as http:
            resp = await http.get(f"{PREFIX}/.nwm")
        assert resp.status_code == 200
        assert resp.json()["auth"]["identity_type"] == "none"

    async def test_unknown_path_404(self) -> None:
        app = AnchorNodeApp(_base_options())
        async with _client_for(app) as http:
            resp = await http.get(f"{PREFIX}/nope")
        assert resp.status_code == 404

    async def test_unknown_path_404_before_auth(self) -> None:
        # An unknown sub-path must be 404 regardless of auth — a missing X-NWP-Agent on a route
        # with no resource must NOT leak a 401 (auth state).
        app = AnchorNodeApp(_base_options())
        async with _client_for(app, agent=None) as http:
            resp = await http.get(f"{PREFIX}/nope")
        assert resp.status_code == 404
        assert resp.json()["error"] == error_codes.ACTION_NOT_FOUND


# ── topology.snapshot via the real AnchorNodeClient ──────────────────────────────

class TestTopologySnapshot:
    async def test_snapshot_round_trip_with_client(self) -> None:
        topo = InMemoryAnchorTopologyService(ANCHOR_NID, members=_members(), version=7)
        app = AnchorNodeApp(_base_options(), topology_service=topo)
        async with _client_for(app) as http:
            client = AnchorNodeClient("http://anchor", path_prefix=PREFIX, http_client=http)
            snap = await client.get_snapshot()
        assert snap.version == 7
        assert snap.anchor_nid == ANCHOR_NID
        assert snap.cluster_size == 2
        assert {m.nid for m in snap.members} == {"urn:nps:node:w1", "urn:nps:node:w2"}

    async def test_reserved_type_unsupported_501(self) -> None:
        topo = InMemoryAnchorTopologyService(ANCHOR_NID, members=_members())
        app = AnchorNodeApp(_base_options(), topology_service=topo)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/query", json={"type": "topology.bogus", "topology": {}})
        assert resp.status_code == 501
        assert resp.json()["error"] == error_codes.RESERVED_TYPE_UNSUPPORTED

    async def test_no_topology_service_501(self) -> None:
        app = AnchorNodeApp(_base_options())
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/query",
                                   json={"type": "topology.snapshot", "topology": {"scope": "cluster"}})
        assert resp.status_code == 501
        assert resp.json()["error"] == error_codes.NODE_UNAVAILABLE

    async def test_member_scope_requires_target_nid(self) -> None:
        topo = InMemoryAnchorTopologyService(ANCHOR_NID, members=_members())
        app = AnchorNodeApp(_base_options(), topology_service=topo)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/query",
                                   json={"type": "topology.snapshot", "topology": {"scope": "member"}})
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.TOPOLOGY_UNSUPPORTED_SCOPE

    async def test_capability_gate(self) -> None:
        topo = InMemoryAnchorTopologyService(ANCHOR_NID, members=_members())
        app = AnchorNodeApp(_base_options(require_topology_capability=True), topology_service=topo)
        async with _client_for(app) as http:
            denied = await http.post(f"{PREFIX}/query",
                                     json={"type": "topology.snapshot", "topology": {}})
            assert denied.status_code == 403
            assert denied.json()["error"] == error_codes.TOPOLOGY_UNAUTHORIZED
            ok = await http.post(f"{PREFIX}/query",
                                 json={"type": "topology.snapshot", "topology": {}},
                                 headers={"X-NWP-Capabilities": "topology:read"})
            assert ok.status_code == 200


# ── topology.stream ─────────────────────────────────────────────────────────────

class TestTopologyStream:
    async def test_stream_yields_events_then_resync(self) -> None:
        from nps_sdk.nwp.anchor_client import MemberJoined

        events = [
            MemberJoined(version=8, member=_members()[0]),
            ResyncRequired(version=0, reason="rebased"),
        ]
        topo = InMemoryAnchorTopologyService(ANCHOR_NID, members=_members(), events=events)
        app = AnchorNodeApp(_base_options(), topology_service=topo)
        async with _client_for(app) as http:
            client = AnchorNodeClient("http://anchor", path_prefix=PREFIX, http_client=http)
            received = [ev async for ev in client.subscribe()]
        assert len(received) == 2
        assert isinstance(received[0], MemberJoined)
        assert received[0].member.nid == "urn:nps:node:w1"
        assert isinstance(received[1], ResyncRequired)


# ── /invoke gates ────────────────────────────────────────────────────────────────

async def _ok_handler(action_id, frame, ctx):
    return {"order_id": "o-123", "action": action_id, "agent": ctx.agent_nid}


class TestInvoke:
    async def test_sync_invoke_returns_caps(self) -> None:
        app = AnchorNodeApp(_base_options(), invoke_handler=_ok_handler)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create", "params": {"x": 1}})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/nwp-capsule"
        body = resp.json()
        assert body["count"] == 1
        assert body["data"][0]["order_id"] == "o-123"
        assert body["data"][0]["agent"] == "urn:nps:agent:tester"

    async def test_unknown_action_404(self) -> None:
        app = AnchorNodeApp(_base_options(), invoke_handler=_ok_handler)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/invoke", json={"action_id": "nope.verb"})
        assert resp.status_code == 404
        assert resp.json()["error"] == error_codes.ACTION_NOT_FOUND

    async def test_handler_error_envelope(self) -> None:
        async def bad_handler(action_id, frame, ctx):
            raise AnchorActionError(422, "NPS-CLIENT-BAD-REQUEST",
                                    error_codes.ACTION_PARAMS_INVALID, "bad params")

        app = AnchorNodeApp(_base_options(), invoke_handler=bad_handler)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/invoke", json={"action_id": "orders.create"})
        assert resp.status_code == 422
        assert resp.json()["error"] == error_codes.ACTION_PARAMS_INVALID

    async def test_cgn_limit_pre_check(self) -> None:
        # estimated_cgn=10 (default spec) but budget header forces effective budget 5.
        app = AnchorNodeApp(_base_options(), invoke_handler=_ok_handler)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create"},
                                   headers={"X-NWP-Budget": "5"})
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.CGN_LIMIT_EXCEEDED

    async def test_no_handler_501(self) -> None:
        app = AnchorNodeApp(_base_options())
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/invoke", json={"action_id": "orders.create"})
        assert resp.status_code == 501

    async def test_async_invoke_returns_202(self) -> None:
        app = AnchorNodeApp(
            AnchorNodeOptions(
                node_id=ANCHOR_NID, path_prefix=PREFIX,
                actions={"orders.create": AnchorActionSpec(async_=True)},
            ),
            invoke_handler=_ok_handler,
        )
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create", "async": True})
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending"
        assert body["poll_url"] == f"{PREFIX}/invoke"

    async def test_async_on_sync_only_action_400(self) -> None:
        app = AnchorNodeApp(_base_options(), invoke_handler=_ok_handler)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create", "async": True})
        assert resp.status_code == 400

    async def test_method_not_allowed_405(self) -> None:
        app = AnchorNodeApp(_base_options(), invoke_handler=_ok_handler)
        async with _client_for(app) as http:
            resp = await http.get(f"{PREFIX}/invoke")
        assert resp.status_code == 405

    async def test_bad_json_400(self) -> None:
        app = AnchorNodeApp(_base_options(), invoke_handler=_ok_handler)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/invoke", content=b"{not json",
                                   headers={"content-type": "application/json"})
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.ACTION_PARAMS_INVALID

    async def test_filter_node_kind_rejected(self) -> None:
        topo = InMemoryAnchorTopologyService(ANCHOR_NID, members=_members())
        app = AnchorNodeApp(_base_options(), topology_service=topo)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/subscribe", json={
                "type": "topology.stream",
                "topology": {"scope": "cluster", "filter": {"node_kind": "worker"}},
            })
        # Stream starts 200; mid-stream error envelope is not used here because the
        # filter is parsed before the stream begins → a normal 400 error response.
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.TOPOLOGY_FILTER_UNSUPPORTED

    async def test_reputation_reject_and_throttle(self) -> None:
        import time

        def seed(sev, incident):
            # Seed the reference evaluator's in-process cache with a raw log entry dict so the
            # NID resolves without an HTTP log query (cache shape: (expiry, [entry dicts])).
            e = DefaultReputationEvaluator()
            e._cache["urn:nps:agent:tester"] = (
                time.monotonic() + 3600,
                [{"incident": incident, "severity": sev, "timestamp": "2026-06-12T00:00:00Z"}],
            )
            return e

        # reject
        ev = seed("major", "tos-violation")
        policy = ReputationPolicy(cache_ttl_seconds=300,
                                  reject_on=[ReputationRule(incident="tos-violation", severity=">=major")])
        app = AnchorNodeApp(_base_options(reputation_policy=policy), invoke_handler=_ok_handler,
                            reputation_evaluator=ev)
        async with _client_for(app) as http:
            r = await http.post(f"{PREFIX}/invoke", json={"action_id": "orders.create"})
        assert r.status_code == 403
        assert r.json()["error"] == error_codes.REPUTATION_REJECTED

        # throttle
        ev2 = seed("minor", "scraping-pattern")
        policy2 = ReputationPolicy(cache_ttl_seconds=300,
                                   throttle_on=[ReputationRule(incident="*", severity=">=minor")])
        app2 = AnchorNodeApp(_base_options(reputation_policy=policy2), invoke_handler=_ok_handler,
                             reputation_evaluator=ev2)
        async with _client_for(app2) as http:
            r2 = await http.post(f"{PREFIX}/invoke", json={"action_id": "orders.create"})
        assert r2.status_code == 429
        assert r2.json()["error"] == error_codes.REPUTATION_THROTTLED
        assert r2.headers.get("retry-after") == "60"

    async def test_reputation_assurance_too_low(self) -> None:
        ev = DefaultReputationEvaluator()
        policy = ReputationPolicy(min_assurance_level="verified")
        app = AnchorNodeApp(_base_options(reputation_policy=policy, assurance_hint_url="https://ca/enroll"),
                            invoke_handler=_ok_handler, reputation_evaluator=ev)
        async with _client_for(app) as http:
            r = await http.post(f"{PREFIX}/invoke", json={"action_id": "orders.create"})
        assert r.status_code == 403
        assert r.json()["error"] == error_codes.AUTH_ASSURANCE_TOO_LOW
        assert r.json()["details"]["hint"] == "https://ca/enroll"

    async def test_reputation_ban_blocks_invoke(self) -> None:
        import time

        evaluator = DefaultReputationEvaluator()
        # Seed the evaluator's cache so the NID is seen as banned-worthy without an HTTP log query.
        evaluator._cache["urn:nps:agent:tester"] = (
            time.monotonic() + 3600,
            [{"incident": "impersonation-claim", "severity": "critical",
              "timestamp": "2026-06-12T00:00:00Z"}],
        )
        policy = ReputationPolicy(
            cache_ttl_seconds=300,
            ban_on=[ReputationRule(incident="*", severity=">=critical")],
        )
        app = AnchorNodeApp(_base_options(reputation_policy=policy), invoke_handler=_ok_handler,
                            reputation_evaluator=evaluator)
        async with _client_for(app) as http:
            resp = await http.post(f"{PREFIX}/invoke", json={"action_id": "orders.create"})
        assert resp.status_code == 403
        assert resp.json()["error"] == error_codes.REPUTATION_BANNED
