# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Tests — the inbound Bridge backend abstraction (NPS-CR-0010 §1)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from nps_sdk.ncp.frames import CapsFrame, ErrorFrame
from nps_sdk.nwp.frames import ActionFrame, QueryFrame
from nps_sdk.nwp.inbound import (
    BridgeInboundOptions,
    HttpNwpBackend,
    InProcessNwpBackend,
    McpInboundServer,
    NwpActionDescriptor,
    NwpNodeDescriptor,
    NwpNodeRole,
    NwpResult,
    NwpUpstream,
    create_backends,
)
from nps_sdk.nwp.inbound.error_map import BridgeErrorCodes

BASE = "https://upstream.test"


class TestNwpNodeRole:
    @pytest.mark.parametrize("node_type,expected", [
        ("memory", NwpNodeRole.MEMORY),
        ("Action", NwpNodeRole.ACTION),
        ("COMPLEX", NwpNodeRole.COMPLEX),
        ("anchor", NwpNodeRole.ANCHOR),
        ("bridge", NwpNodeRole.BRIDGE),
        ("gateway", NwpNodeRole.UNKNOWN),
        (None, NwpNodeRole.UNKNOWN),
    ])
    def test_parse(self, node_type, expected):
        assert NwpNodeRole.parse(node_type) == expected

    @pytest.mark.parametrize("role,queryable,invokable", [
        (NwpNodeRole.MEMORY, True, False),
        (NwpNodeRole.ACTION, False, True),
        (NwpNodeRole.COMPLEX, True, True),
        (NwpNodeRole.ANCHOR, False, False),
        (NwpNodeRole.BRIDGE, False, False),
        (NwpNodeRole.UNKNOWN, False, False),
    ])
    def test_projection_surfaces(self, role, queryable, invokable):
        d = NwpNodeDescriptor(name="n", role=role)
        assert d.is_queryable is queryable
        assert d.is_invokable is invokable


class TestNwpResult:
    def test_success_carries_the_payload(self):
        r = NwpResult.success({"a": 1})
        assert r.ok and r.raw_payload_json() == '{"a":1}'

    def test_a_null_payload_renders_as_an_empty_object(self):
        assert NwpResult.success(None).raw_payload_json() == "{}"

    def test_failure_carries_the_nps_status_forward(self):
        r = NwpResult.failure("NPS-CLIENT-CONFLICT", "NWP-X", "boom")
        assert not r.ok
        assert r.failure_payload() == {"status": "NPS-CLIENT-CONFLICT",
                                       "error": "NWP-X", "message": "boom"}

    def test_dispatch_failed_uses_the_registered_code(self):
        r = NwpResult.dispatch_failed("bad")
        assert r.nps_status == "NPS-SERVER-INTERNAL"
        assert r.nwp_error == BridgeErrorCodes.SERVER_DISPATCH_FAILED


class TestInProcessBackend:
    async def test_manifest_is_synthesised_from_the_descriptor(self):
        backend = InProcessNwpBackend(NwpNodeDescriptor(
            name="n", role=NwpNodeRole.COMPLEX, display_name="N", description="d"))
        assert (await backend.get_manifest()).payload == {
            "node_type": "complex", "display_name": "N", "description": "d"}

    async def test_manifest_display_name_falls_back_to_the_name(self):
        backend = InProcessNwpBackend(NwpNodeDescriptor(name="n", role=NwpNodeRole.ACTION))
        assert (await backend.get_manifest()).payload["display_name"] == "n"

    async def test_actions_are_empty_for_a_non_invokable_node(self):
        backend = InProcessNwpBackend(
            NwpNodeDescriptor(name="n", role=NwpNodeRole.MEMORY),
            [NwpActionDescriptor("a")])
        assert await backend.get_actions() == ()

    async def test_query_on_a_non_queryable_node_is_unsupported(self):
        backend = InProcessNwpBackend(NwpNodeDescriptor(name="n", role=NwpNodeRole.ACTION))
        result = await backend.query({})
        assert result.nps_status == "NPS-SERVER-UNSUPPORTED"
        assert result.nwp_error == BridgeErrorCodes.SERVER_TOOL_NOT_FOUND
        assert "is not queryable (role: action)" in result.message

    async def test_query_without_a_dispatcher_is_dispatcher_missing(self):
        backend = InProcessNwpBackend(NwpNodeDescriptor(name="n", role=NwpNodeRole.MEMORY))
        result = await backend.query({})
        assert result.nwp_error == BridgeErrorCodes.SERVER_DISPATCHER_MISSING

    async def test_query_builds_a_query_frame_from_the_filter(self):
        seen: list[QueryFrame] = []

        async def dispatcher(frame: QueryFrame):
            seen.append(frame)
            return CapsFrame(anchor_ref="a", count=0, data=[])

        backend = InProcessNwpBackend(
            NwpNodeDescriptor(name="n", role=NwpNodeRole.MEMORY),
            query_dispatcher=dispatcher)
        result = await backend.query({"limit": 5})
        assert seen[0].filter == {"limit": 5}
        assert result.ok

    async def test_invoke_builds_an_action_frame(self):
        seen: list[ActionFrame] = []

        async def dispatcher(frame: ActionFrame):
            seen.append(frame)
            return CapsFrame(anchor_ref="a", count=0, data=[])

        backend = InProcessNwpBackend(
            NwpNodeDescriptor(name="n", role=NwpNodeRole.ACTION),
            invoke_dispatcher=dispatcher)
        await backend.invoke("do", {"x": 1}, True)
        assert seen[0].action_id == "do"
        assert seen[0].params == {"x": 1}
        assert seen[0].async_ is True

    async def test_a_raising_query_dispatcher_becomes_dispatch_failed(self):
        async def boom(frame):
            raise RuntimeError("nope")

        backend = InProcessNwpBackend(
            NwpNodeDescriptor(name="n", role=NwpNodeRole.MEMORY), query_dispatcher=boom)
        assert (await backend.query({})).nwp_error == BridgeErrorCodes.SERVER_DISPATCH_FAILED

    async def test_an_error_frame_response_keeps_its_status(self):
        async def erroring(frame):
            return ErrorFrame(status="NPS-CLIENT-NOT-FOUND", error="NWP-ACTION-NOT-FOUND",
                              message="gone")

        backend = InProcessNwpBackend(
            NwpNodeDescriptor(name="n", role=NwpNodeRole.ACTION), invoke_dispatcher=erroring)
        result = await backend.invoke("do", None, False)
        assert result.nps_status == "NPS-CLIENT-NOT-FOUND"
        assert result.message == "gone"

    async def test_an_error_frame_without_a_status_defaults_to_internal(self):
        async def erroring(frame):
            return ErrorFrame(status="", error="NWP-X")

        backend = InProcessNwpBackend(
            NwpNodeDescriptor(name="n", role=NwpNodeRole.ACTION), invoke_dispatcher=erroring)
        assert (await backend.invoke("do", None, False)).nps_status == "NPS-SERVER-INTERNAL"

    @pytest.mark.parametrize("returned,expected", [
        (None, {}),
        ({"plain": "dict"}, {"plain": "dict"}),
    ])
    async def test_non_frame_responses_are_passed_through(self, returned, expected):
        async def dispatcher(frame):
            return returned

        backend = InProcessNwpBackend(
            NwpNodeDescriptor(name="n", role=NwpNodeRole.ACTION), invoke_dispatcher=dispatcher)
        assert (await backend.invoke("do", None, False)).payload == expected


class TestHttpBackend:
    def _backend(self, client: httpx.AsyncClient, **kw) -> HttpNwpBackend:
        return HttpNwpBackend(client, NwpUpstream(name="up", base_url=BASE, **kw))

    @respx.mock
    async def test_descriptor_is_fetched_from_the_nwm_and_cached(self):
        route = respx.get(f"{BASE}/.nwm").mock(return_value=httpx.Response(
            200, json={"node_type": "complex", "display_name": "Up",
                       "description": "an upstream"}))
        async with httpx.AsyncClient() as client:
            backend = self._backend(client)
            first = await backend.get_descriptor()
            second = await backend.get_descriptor()

        assert first.role is NwpNodeRole.COMPLEX
        assert first.display_name == "Up" and first.description == "an upstream"
        assert first is second
        assert route.call_count == 1

    @respx.mock
    async def test_an_unreachable_nwm_caches_role_unknown(self):
        # A dead upstream must not take down the Bridge; it is projected onto nothing.
        respx.get(f"{BASE}/.nwm").mock(side_effect=httpx.ConnectError("refused"))
        async with httpx.AsyncClient() as client:
            descriptor = await self._backend(client).get_descriptor()
        assert descriptor.role is NwpNodeRole.UNKNOWN

    @respx.mock
    async def test_actions_are_read_from_the_actions_body_shape(self):
        respx.get(f"{BASE}/actions").mock(return_value=httpx.Response(200, json={
            "actions": {"orders.lookup": {"description": "look up",
                                          "params_schema": {"type": "object"}}}}))
        async with httpx.AsyncClient() as client:
            actions = await self._backend(client).get_actions()
        assert actions[0].action_id == "orders.lookup"
        assert actions[0].description == "look up"
        assert actions[0].input_schema == {"type": "object"}

    @respx.mock
    async def test_a_malformed_actions_body_yields_no_actions(self):
        respx.get(f"{BASE}/actions").mock(return_value=httpx.Response(200, json={"actions": []}))
        async with httpx.AsyncClient() as client:
            assert await self._backend(client).get_actions() == ()

    @respx.mock
    async def test_a_failing_actions_fetch_yields_no_actions(self):
        respx.get(f"{BASE}/actions").mock(return_value=httpx.Response(500, text="boom"))
        async with httpx.AsyncClient() as client:
            assert await self._backend(client).get_actions() == ()

    @respx.mock
    async def test_invoke_posts_the_frame_body_and_forwards_headers(self):
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"ok": True})

        respx.post(f"{BASE}/invoke").mock(side_effect=handler)
        async with httpx.AsyncClient() as client:
            backend = self._backend(client, agent_nid="urn:nps:agent:x:a",
                                    auth_header="Bearer t")
            result = await backend.invoke("do", {"a": 1}, False)

        assert result.ok and result.payload == {"ok": True}
        request = captured[0]
        assert request.headers["Content-Type"] == "application/nwp-frame"
        assert request.headers["X-NWP-Agent"] == "urn:nps:agent:x:a"
        assert request.headers["Authorization"] == "Bearer t"
        assert json.loads(request.content) == {
            "action_id": "do", "params": {"a": 1}, "async": False}

    @respx.mock
    async def test_query_short_circuits_for_a_non_queryable_upstream(self):
        respx.get(f"{BASE}/.nwm").mock(return_value=httpx.Response(
            200, json={"node_type": "action"}))
        async with httpx.AsyncClient() as client:
            result = await self._backend(client).query({})
        assert result.nps_status == "NPS-SERVER-UNSUPPORTED"
        assert result.nwp_error == BridgeErrorCodes.SERVER_TOOL_NOT_FOUND

    @respx.mock
    async def test_query_posts_when_the_upstream_is_queryable(self):
        respx.get(f"{BASE}/.nwm").mock(return_value=httpx.Response(
            200, json={"node_type": "memory"}))
        respx.post(f"{BASE}/query").mock(return_value=httpx.Response(200, json={"count": 0}))
        async with httpx.AsyncClient() as client:
            result = await self._backend(client).query({"limit": 3})
        assert result.ok and result.payload == {"count": 0}

    @respx.mock
    async def test_a_non_2xx_response_maps_through_from_http_status(self):
        respx.post(f"{BASE}/invoke").mock(return_value=httpx.Response(
            403, json={"status": "NPS-AUTH-FORBIDDEN", "error": "NWP-AUTH-NID-SCOPE-VIOLATION",
                       "message": "denied"}))
        async with httpx.AsyncClient() as client:
            result = await self._backend(client).invoke("do", None, False)
        assert result.nps_status == "NPS-AUTH-FORBIDDEN"
        assert result.nwp_error == "NWP-AUTH-NID-SCOPE-VIOLATION"
        assert result.message == "denied"

    @respx.mock
    async def test_a_non_json_error_body_still_maps(self):
        respx.post(f"{BASE}/invoke").mock(return_value=httpx.Response(429, text="slow down"))
        async with httpx.AsyncClient() as client:
            result = await self._backend(client).invoke("do", None, False)
        assert result.nps_status == "NPS-LIMIT-RATE"
        assert result.message == "slow down"

    @respx.mock
    async def test_a_non_json_2xx_body_is_an_upstream_failure(self):
        respx.post(f"{BASE}/invoke").mock(return_value=httpx.Response(200, text="<html/>"))
        async with httpx.AsyncClient() as client:
            result = await self._backend(client).invoke("do", None, False)
        assert result.nps_status == "NPS-DOWNSTREAM-UNAVAILABLE"
        assert result.nwp_error == BridgeErrorCodes.UPSTREAM_FAILED

    @respx.mock
    async def test_a_timeout_and_a_connection_error_are_distinct_nps_classes(self):
        respx.post(f"{BASE}/invoke").mock(side_effect=httpx.ReadTimeout("slow"))
        async with httpx.AsyncClient() as client:
            timed_out = await self._backend(client).invoke("do", None, False)
        assert timed_out.nps_status == "NPS-SERVER-TIMEOUT"

        respx.post(f"{BASE}/query").mock(side_effect=httpx.ConnectError("refused"))
        respx.get(f"{BASE}/.nwm").mock(return_value=httpx.Response(
            200, json={"node_type": "memory"}))
        async with httpx.AsyncClient() as client:
            refused = await self._backend(client).query({})
        assert refused.nps_status == "NPS-DOWNSTREAM-UNAVAILABLE"

    @respx.mock
    async def test_an_http_backend_serves_the_mcp_surface_unchanged(self):
        # The protocol servers are unaware of which backend shape they serve.
        respx.get(f"{BASE}/.nwm").mock(return_value=httpx.Response(
            200, json={"node_type": "action"}))
        respx.get(f"{BASE}/actions").mock(return_value=httpx.Response(
            200, json={"actions": {"orders.lookup": {"description": "look up"}}}))
        respx.post(f"{BASE}/invoke").mock(return_value=httpx.Response(200, json={"ok": 1}))

        async with httpx.AsyncClient() as client:
            options = BridgeInboundOptions(node_id="bridge")
            options.upstreams.append(NwpUpstream(name="up", base_url=BASE))
            mcp = McpInboundServer(options, create_backends(options, client))

            from nps_sdk.nwp.inbound import BridgeJsonRpcRequest

            tools = (await mcp.dispatch(BridgeJsonRpcRequest(
                method="tools/list", id=1))).result["tools"]
            assert [t["name"] for t in tools] == ["up__orders_lookup"]

            called = await mcp.dispatch(BridgeJsonRpcRequest(
                method="tools/call", id=2, params={"name": "up__orders_lookup"}))
            assert called.result["isError"] is False


class TestCreateBackends:
    def test_no_declarations_yields_no_backends(self):
        assert create_backends(BridgeInboundOptions()) == []

    def test_actions_alone_still_materialise_a_backend(self):
        # A deployment that declares actions but forgets the dispatcher must fail
        # loudly, not look like it exposes nothing.
        options = BridgeInboundOptions().add_action("a")
        assert len(create_backends(options)) == 1

    def test_a_query_dispatcher_alone_materialises_a_backend(self):
        async def q(frame):
            return None

        assert len(create_backends(BridgeInboundOptions(query_dispatcher=q))) == 1

    def test_upstreams_require_an_http_client(self):
        options = BridgeInboundOptions()
        options.upstreams.append(NwpUpstream(name="up", base_url=BASE))
        with pytest.raises(ValueError, match="no HTTP client was supplied"):
            create_backends(options)

    def test_both_shapes_may_coexist(self):
        options = BridgeInboundOptions().add_action("a")
        options.upstreams.append(NwpUpstream(name="up", base_url=BASE))
        backends = create_backends(options, http_client=object())
        assert len(backends) == 2
        assert isinstance(backends[0], InProcessNwpBackend)
        assert isinstance(backends[1], HttpNwpBackend)


class TestBridgeServerAction:
    def test_effective_display_name_falls_back_to_the_action_id(self):
        options = BridgeInboundOptions().add_action("a.b", display_name="  ")
        assert options.actions[0].effective_display_name == "a.b"

    def test_effective_display_name_uses_the_declared_name(self):
        options = BridgeInboundOptions().add_action("a.b", display_name="Nice")
        assert options.actions[0].effective_display_name == "Nice"

    def test_add_action_chains(self):
        options = BridgeInboundOptions().add_action("a").add_action("b")
        assert [a.action_id for a in options.actions] == ["a", "b"]
