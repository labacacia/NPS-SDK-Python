# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Tests — NPS-CR-0010 inbound Bridge servers (brief B Part 2 §8).

Covers the six TC-N2-BridgeIn conformance cases plus the .NET tests to mirror beyond
them: the required MCP method set, stdio, resources over a queryable node, resources
served with no Memory Node behind, unqualified tool names, the auth-error-is-a-
protocol-error rule, the loud missing-dispatcher failure, the A2A round trip and
AgentCard, and the gRPC service logic.
"""

from __future__ import annotations

import io
import json

import pytest

from nps_sdk.ncp.frames import CapsFrame, ErrorFrame
from nps_sdk.nwp.frames import ActionFrame, QueryFrame
from nps_sdk.nwp.inbound import (
    A2aInboundServer,
    BridgeErrorCodes,
    BridgeInboundOptions,
    BridgeJsonRpcRequest,
    GrpcInboundService,
    GrpcRpcError,
    InProcessNwpBackend,
    McpInboundServer,
    McpToolName,
    NwpActionDescriptor,
    NwpNodeDescriptor,
    NwpNodeRole,
    NwpResult,
    UpstreamContext,
    create_backends,
)

NODE = "bridge-inbound-test"


# ── Fixtures ──────────────────────────────────────────────────────────────────

async def _caps(frame: ActionFrame | QueryFrame):
    return CapsFrame(anchor_ref="sha256:test", count=1,
                     data=[{"order_id": "o-1", "action": getattr(frame, "action_id", "query")}])


def _options(**kw) -> BridgeInboundOptions:
    opts = BridgeInboundOptions(
        node_id=NODE,
        server_name=NODE,
        node_role=kw.pop("node_role", NwpNodeRole.ACTION),
        action_dispatcher=kw.pop("action_dispatcher", _caps),
        **kw,
    )
    opts.add_action("orders.lookup", description="Look an order up",
                    input_schema={"type": "object", "properties": {"id": {"type": "string"}}})
    return opts


def _servers(options: BridgeInboundOptions):
    backends = create_backends(options)
    return McpInboundServer(options, backends), A2aInboundServer(options, backends), backends


def _req(method: str, params=None, id_: int = 1) -> BridgeJsonRpcRequest:
    return BridgeJsonRpcRequest(method=method, id=id_, params=params)


class _FakeBackend:
    """A backend whose invoke/query outcome is scripted."""

    def __init__(self, descriptor: NwpNodeDescriptor,
                 actions=(), invoke_result=None, query_result=None):
        self._d = descriptor
        self._a = tuple(actions)
        self._i = invoke_result
        self._q = query_result

    async def get_descriptor(self):
        return self._d

    async def get_manifest(self):
        return NwpResult.success({"node_type": self._d.role.value})

    async def get_actions(self):
        return self._a if self._d.is_invokable else ()

    async def query(self, query):
        return self._q or NwpResult.success({"rows": [], "query": query})

    async def invoke(self, action_id, arguments, async_):
        return self._i or NwpResult.success({"action_id": action_id, "args": arguments})


# ── McpToolName ───────────────────────────────────────────────────────────────

class TestMcpToolName:
    def test_reference_example(self):
        assert McpToolName.encode(NODE, "orders.lookup") == "bridge-inbound-test__orders_lookup"

    def test_action_segment_folds_dots(self):
        assert McpToolName.encode_action_segment("orders.lookup") == "orders_lookup"

    def test_illegal_characters_become_underscores(self):
        assert McpToolName.encode("my node!", "a b") == "my_node__a_b"

    def test_blank_names_fall_back_to_node(self):
        assert McpToolName.encode("   ", "___") == "node__node"

    def test_leading_and_trailing_underscores_are_trimmed(self):
        assert McpToolName.encode("__x__", "y") == "x__y"


# ── BridgeIn-01: the full required MCP method set ─────────────────────────────

class TestMcpRequiredMethodSet:
    def test_the_required_set_is_exported_as_data(self):
        assert McpInboundServer.REQUIRED_METHODS == (
            "initialize", "ping", "tools/list", "tools/call",
            "resources/list", "resources/read")

    async def test_all_six_methods_return_a_successful_result(self):
        options = _options(node_role=NwpNodeRole.COMPLEX, query_dispatcher=_caps)
        mcp, _, _ = _servers(options)

        for method, params in (
            ("initialize", None),
            ("ping", None),
            ("tools/list", None),
            ("tools/call", {"name": f"{NODE}__orders_lookup", "arguments": {"id": "o-1"}}),
            ("resources/list", None),
            ("resources/read", {"uri": f"nwp://{NODE}/"}),
        ):
            resp = await mcp.dispatch(_req(method, params))
            assert resp.error is None, (method, resp.error)
            assert resp.result is not None

    async def test_initialize_always_advertises_both_capabilities(self):
        # Action-only node: no Memory Node behind it, resources still advertised.
        mcp, _, _ = _servers(_options())
        result = (await mcp.dispatch(_req("initialize"))).result
        assert result["capabilities"] == {"tools": {}, "resources": {}}
        assert result["serverInfo"]["name"] == NODE

    async def test_resources_methods_are_served_even_with_no_memory_node(self):
        mcp, _, _ = _servers(_options())
        listed = (await mcp.dispatch(_req("resources/list"))).result
        assert listed == {"resources": []}          # empty set IS conformant

    async def test_tools_list_surfaces_qualified_names_and_the_declared_schema(self):
        mcp, _, _ = _servers(_options())
        tools = (await mcp.dispatch(_req("tools/list"))).result["tools"]
        assert [t["name"] for t in tools] == [f"{NODE}__orders_lookup"]
        assert tools[0]["description"] == "Look an order up"
        assert tools[0]["inputSchema"]["properties"] == {"id": {"type": "string"}}

    async def test_absent_schema_advertises_an_open_object_schema(self):
        options = BridgeInboundOptions(node_id=NODE, action_dispatcher=_caps)
        options.add_action("bare")
        mcp = McpInboundServer(options, create_backends(options))
        tools = (await mcp.dispatch(_req("tools/list"))).result["tools"]
        assert tools[0]["inputSchema"] == {"type": "object", "additionalProperties": True}

    async def test_tools_call_returns_the_node_result_with_is_error_false(self):
        mcp, _, _ = _servers(_options())
        resp = await mcp.dispatch(_req(
            "tools/call", {"name": f"{NODE}__orders_lookup", "arguments": {"id": "o-1"}}))
        assert resp.result["isError"] is False
        body = json.loads(resp.result["content"][0]["text"])
        assert body["data"][0]["order_id"] == "o-1"

    async def test_unknown_method_is_method_not_found(self):
        mcp, _, _ = _servers(_options())
        resp = await mcp.dispatch(_req("completion/complete"))
        assert resp.error.code == -32601
        assert resp.error.data["error"] == BridgeErrorCodes.DIRECTION_UNSUPPORTED

    async def test_tools_call_without_a_name_is_invalid_params(self):
        mcp, _, _ = _servers(_options())
        assert (await mcp.dispatch(_req("tools/call", {}))).error.code == -32602
        assert (await mcp.dispatch(_req("tools/call", {"name": "  "}))).error.code == -32602

    async def test_dispatch_requires_a_request(self):
        mcp, _, _ = _servers(_options())
        with pytest.raises(ValueError):
            await mcp.dispatch(None)


# ── resources/* over a queryable node ─────────────────────────────────────────

class TestMcpResources:
    def _complex(self):
        return _options(node_role=NwpNodeRole.COMPLEX, query_dispatcher=_caps)

    async def test_a_queryable_node_becomes_a_resource(self):
        mcp, _, _ = _servers(self._complex())
        resources = (await mcp.dispatch(_req("resources/list"))).result["resources"]
        assert resources[0]["uri"] == f"nwp://{NODE}/"
        assert resources[0]["mimeType"] == "application/json"

    async def test_default_description_names_the_role(self):
        options = BridgeInboundOptions(node_id=NODE, description=None,
                                       node_role=NwpNodeRole.MEMORY,
                                       query_dispatcher=_caps)
        mcp = McpInboundServer(options, create_backends(options))
        resources = (await mcp.dispatch(_req("resources/list"))).result["resources"]
        assert resources[0]["description"] == f"NWP memory Node '{NODE}' — read to query."

    async def test_resources_read_issues_a_query_with_the_read_limit(self):
        seen: list[QueryFrame] = []

        async def capture(frame: QueryFrame):
            seen.append(frame)
            return CapsFrame(anchor_ref="a", count=0, data=[])

        options = _options(node_role=NwpNodeRole.COMPLEX, query_dispatcher=capture)
        options.resource_read_limit = 42
        mcp = McpInboundServer(options, create_backends(options))

        resp = await mcp.dispatch(_req("resources/read", {"uri": f"nwp://{NODE}/"}))
        assert seen[0].filter == {"limit": 42}
        content = resp.result["contents"][0]
        assert content["uri"] == f"nwp://{NODE}/"
        assert content["mimeType"] == "application/json"
        assert json.loads(content["text"])["count"] == 0

    async def test_host_is_matched_case_insensitively(self):
        mcp, _, _ = _servers(self._complex())
        resp = await mcp.dispatch(_req("resources/read", {"uri": f"nwp://{NODE.upper()}/"}))
        assert resp.error is None

    async def test_missing_uri_is_invalid_params(self):
        mcp, _, _ = _servers(self._complex())
        assert (await mcp.dispatch(_req("resources/read", {}))).error.code == -32602

    async def test_a_non_nwp_scheme_is_invalid_params(self):
        mcp, _, _ = _servers(self._complex())
        resp = await mcp.dispatch(_req("resources/read", {"uri": "https://example.com/x"}))
        assert resp.error.code == -32602
        assert "must be of the form nwp://<node>/" in resp.error.message

    async def test_an_unknown_host_is_invalid_params_not_method_not_found(self):
        mcp, _, _ = _servers(self._complex())
        resp = await mcp.dispatch(_req("resources/read", {"uri": "nwp://nope/"}))
        assert resp.error.code == -32602            # NOT -32601
        assert resp.error.data["error"] == BridgeErrorCodes.SERVER_TOOL_NOT_FOUND
        assert resp.error.data["uri"] == "nwp://nope/"

    async def test_a_failing_query_maps_through_the_resource_read_column(self):
        backend = _FakeBackend(
            NwpNodeDescriptor(name=NODE, role=NwpNodeRole.MEMORY),
            query_result=NwpResult.failure("NPS-CLIENT-NOT-FOUND", "NWP-QUERY-FILTER-INVALID",
                                           "no such view"))
        mcp = McpInboundServer(BridgeInboundOptions(node_id=NODE), [backend])
        resp = await mcp.dispatch(_req("resources/read", {"uri": f"nwp://{NODE}/"}))
        assert resp.error.code == -32602            # resource_read column
        assert resp.error.data["status"] == "NPS-CLIENT-NOT-FOUND"


# ── BridgeIn-04: bare id resolves, ambiguity is rejected ──────────────────────

class TestToolResolution:
    def _two_nodes(self):
        a = _FakeBackend(
            NwpNodeDescriptor(name="node-a", role=NwpNodeRole.ACTION),
            actions=[NwpActionDescriptor("orders_lookup"), NwpActionDescriptor("status")])
        b = _FakeBackend(
            NwpNodeDescriptor(name="node-b", role=NwpNodeRole.ACTION),
            actions=[NwpActionDescriptor("status")])
        return McpInboundServer(BridgeInboundOptions(node_id="bridge"), [a, b])

    async def test_a_bare_unique_action_id_resolves(self):
        resp = await self._two_nodes().dispatch(
            _req("tools/call", {"name": "orders_lookup"}))
        assert resp.error is None
        assert json.loads(resp.result["content"][0]["text"])["action_id"] == "orders_lookup"

    async def test_an_ambiguous_bare_id_is_rejected_naming_both_candidates(self):
        resp = await self._two_nodes().dispatch(_req("tools/call", {"name": "status"}))
        assert resp.error.code == -32601
        assert resp.error.data["error"] == BridgeErrorCodes.SERVER_TOOL_NOT_FOUND
        # MUST NOT silently pick one; the rejection names both qualified candidates.
        assert resp.error.data["candidates"] == ["node-a__status", "node-b__status"]

    async def test_a_qualified_name_disambiguates(self):
        resp = await self._two_nodes().dispatch(_req("tools/call", {"name": "node-b__status"}))
        assert resp.error is None

    async def test_a_qualified_match_wins_over_a_bare_candidate(self):
        mcp, _, _ = _servers(_options())
        resp = await mcp.dispatch(_req("tools/call", {"name": f"{NODE}__ORDERS_LOOKUP"}))
        assert resp.error is None                   # ignore-case

    async def test_a_bare_encoded_action_segment_resolves(self):
        # pre-CR-0010 clients saw the dot-folded bare form.
        mcp, _, _ = _servers(_options())
        resp = await mcp.dispatch(_req("tools/call", {"name": "orders_lookup"}))
        assert resp.error is None

    async def test_a_completely_unknown_tool_is_method_not_found(self):
        mcp, _, _ = _servers(_options())
        resp = await mcp.dispatch(_req("tools/call", {"name": "nope"}))
        assert resp.error.code == -32601
        assert "candidates" not in resp.error.data

    async def test_non_invokable_backends_are_skipped(self):
        memory = _FakeBackend(NwpNodeDescriptor(name="mem", role=NwpNodeRole.MEMORY),
                              actions=[NwpActionDescriptor("hidden")])
        mcp = McpInboundServer(BridgeInboundOptions(node_id="b"), [memory])
        assert (await mcp.dispatch(_req("tools/list"))).result == {"tools": []}
        assert (await mcp.dispatch(_req("tools/call", {"name": "hidden"}))).error.code == -32601


# ── BridgeIn-05: §16.3 error mapping on the tools/call path ───────────────────

class TestMcpErrorMapping:
    def _with_failure(self, result: NwpResult) -> McpInboundServer:
        backend = _FakeBackend(
            NwpNodeDescriptor(name=NODE, role=NwpNodeRole.ACTION),
            actions=[NwpActionDescriptor("do")], invoke_result=result)
        return McpInboundServer(BridgeInboundOptions(node_id=NODE), [backend])

    async def test_auth_failure_is_a_protocol_error_not_an_is_error_result(self):
        mcp = self._with_failure(NwpResult.failure(
            "NPS-AUTH-FORBIDDEN", "NWP-AUTH-NID-SCOPE-VIOLATION", "scope violation"))
        resp = await mcp.dispatch(_req("tools/call", {"name": "do"}))
        assert resp.result is None
        assert resp.error.code == -32003
        assert resp.error.data == {"status": "NPS-AUTH-FORBIDDEN",
                                   "error": "NWP-AUTH-NID-SCOPE-VIOLATION"}

    async def test_unauthenticated_is_distinct_from_forbidden(self):
        mcp = self._with_failure(NwpResult.failure("NPS-AUTH-UNAUTHENTICATED"))
        assert (await mcp.dispatch(_req("tools/call", {"name": "do"}))).error.code == -32001

    async def test_a_timeout_is_a_protocol_error(self):
        mcp = self._with_failure(NwpResult.failure(
            "NPS-SERVER-TIMEOUT", "NWP-NODE-UNAVAILABLE", "upstream timed out"))
        assert (await mcp.dispatch(_req("tools/call", {"name": "do"}))).error.code == -32603

    async def test_a_domain_failure_stays_an_is_error_result(self):
        mcp = self._with_failure(NwpResult.failure(
            "NPS-CLIENT-UNPROCESSABLE", "NWP-ACTION-PARAMS-INVALID", "bad id"))
        resp = await mcp.dispatch(_req("tools/call", {"name": "do"}))
        assert resp.error is None
        assert resp.result["isError"] is True
        assert json.loads(resp.result["content"][0]["text"]) == {
            "status": "NPS-CLIENT-UNPROCESSABLE",
            "error": "NWP-ACTION-PARAMS-INVALID",
            "message": "bad id",
        }

    async def test_missing_dispatcher_fails_loudly_with_a_registered_code(self):
        options = BridgeInboundOptions(node_id=NODE)   # actions but no dispatcher
        options.add_action("orders.lookup")
        mcp = McpInboundServer(options, create_backends(options))

        # The tool still appears, so the misconfiguration is visible...
        assert (await mcp.dispatch(_req("tools/list"))).result["tools"]
        resp = await mcp.dispatch(_req("tools/call", {"name": "orders_lookup"}))
        assert resp.error.code == -32603
        assert resp.error.data["error"] == BridgeErrorCodes.SERVER_DISPATCHER_MISSING
        assert resp.error.data["status"] != "NPS-SERVER-NOT-IMPLEMENTED"

    async def test_a_raising_dispatcher_becomes_dispatch_failed(self):
        async def boom(frame):
            raise RuntimeError("kaboom")

        options = _options(action_dispatcher=boom)
        mcp = McpInboundServer(options, create_backends(options))
        resp = await mcp.dispatch(_req("tools/call", {"name": "orders_lookup"}))
        assert resp.error.code == -32603
        assert resp.error.data["error"] == BridgeErrorCodes.SERVER_DISPATCH_FAILED

    async def test_an_error_frame_keeps_its_nps_status(self):
        async def erroring(frame):
            return ErrorFrame(status="NPS-LIMIT-RATE", error="NWP-RATE-LIMIT-EXCEEDED",
                              message="slow down")

        options = _options(action_dispatcher=erroring)
        mcp = McpInboundServer(options, create_backends(options))
        resp = await mcp.dispatch(_req("tools/call", {"name": "orders_lookup"}))
        assert resp.error.code == -32005


# ── BridgeIn-06: undeclared protocol / direction ──────────────────────────────

class TestDirectionGate:
    async def test_mcp_is_refused_when_undeclared(self):
        options = _options()
        options.inbound_protocols = ["a2a"]
        mcp, _, _ = _servers(options)

        resp = await mcp.dispatch(_req("tools/list"))
        assert resp.error.code == -32601
        assert resp.error.message == (
            'This Bridge Node does not declare "mcp" in bridge_inbound_protocols.')
        assert resp.error.data["error"] == BridgeErrorCodes.DIRECTION_UNSUPPORTED
        # §16.1.2 MUST-5 SHOULD-clause: the hint carries both declared arrays.
        assert resp.error.data["hint"] == {"bridge_inbound_protocols": ["a2a"],
                                           "bridge_protocols": []}

    async def test_a2a_is_refused_when_undeclared(self):
        options = _options()
        options.inbound_protocols = ["mcp"]
        _, a2a, _ = _servers(options)

        resp = await a2a.dispatch(_req("tasks/send", {"id": "t1"}))
        assert resp.error.code == -32601
        assert resp.error.data["error"] == BridgeErrorCodes.DIRECTION_UNSUPPORTED
        assert resp.error.data["hint"]["bridge_inbound_protocols"] == ["mcp"]

    async def test_grpc_is_not_in_the_default_inbound_set(self):
        options = _options()
        assert options.inbound_protocols == ["mcp", "a2a"]
        grpc = GrpcInboundService(options, create_backends(options))
        with pytest.raises(GrpcRpcError) as exc:
            await grpc.get_manifest()
        assert exc.value.status_code == "UNIMPLEMENTED"
        assert BridgeErrorCodes.DIRECTION_UNSUPPORTED in exc.value.detail

    def test_serves_inbound_is_case_insensitive(self):
        assert BridgeInboundOptions().serves_inbound("MCP")
        assert not BridgeInboundOptions().serves_inbound("grpc")


# ── stdio transport ───────────────────────────────────────────────────────────

class TestMcpStdio:
    async def _run(self, lines: str, options: BridgeInboundOptions | None = None) -> list[dict]:
        options = options or _options()
        mcp = McpInboundServer(options, create_backends(options))
        out = io.StringIO()
        await mcp.run_stdio(io.StringIO(lines), out)
        return [json.loads(line) for line in out.getvalue().splitlines() if line]

    async def test_line_delimited_json_rpc_round_trip(self):
        responses = await self._run(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
            + "\n"                                    # blank lines are skipped
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        assert [r["id"] for r in responses] == [1, 2]
        assert responses[1]["result"]["tools"][0]["name"] == f"{NODE}__orders_lookup"

    async def test_a_parse_error_becomes_minus_32700_with_a_null_id(self):
        responses = await self._run("{not json}\n")
        assert responses[0]["error"]["code"] == -32700
        assert responses[0]["id"] is None

    async def test_a_non_object_request_is_invalid_request(self):
        responses = await self._run("[1,2,3]\n")
        assert responses[0]["error"]["code"] == -32600
        assert responses[0]["error"]["message"] == "JSON-RPC request is required."

    async def test_a_request_without_a_method_is_invalid_request(self):
        responses = await self._run(json.dumps({"jsonrpc": "2.0", "id": 1}) + "\n")
        assert responses[0]["error"]["code"] == -32600


# ── BridgeIn-03: A2A ──────────────────────────────────────────────────────────

class TestA2aInbound:
    async def test_agent_card_lists_fronted_actions_as_qualified_skills(self):
        _, a2a, _ = _servers(_options())
        card = await a2a.build_agent_card("https://bridge.test/a2a")

        assert card["name"] == NODE
        assert card["url"] == "https://bridge.test/a2a"
        assert card["provider"] == {"organization": "LabAcacia / INNO LOTUS PTY LTD",
                                    "url": "https://github.com/labacacia/nps"}
        assert card["capabilities"] == {"streaming": False, "pushNotifications": False,
                                        "stateTransitionHistory": False}
        assert card["authentication"] == {"schemes": ["apikey"], "credentials": "X-NWP-Agent"}
        skill = card["skills"][0]
        assert skill["id"] == f"{NODE}__orders_lookup"
        assert skill["name"] == "Look an order up"
        assert skill["inputModes"] == ["text", "data"]
        assert skill["outputModes"] == ["data"]

    async def test_agent_card_omits_authentication_when_auth_is_not_required(self):
        options = _options()
        options.require_auth = False
        _, a2a, _ = _servers(options)
        assert (await a2a.build_agent_card("u"))["authentication"] is None

    async def test_tasks_send_dispatches_an_action_and_returns_an_artifact(self):
        _, a2a, _ = _servers(_options())
        resp = await a2a.dispatch(_req("tasks/send", {
            "id": "task-1",
            "sessionId": "s-1",
            "message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]},
            "metadata": {"skill_id": f"{NODE}__orders_lookup"},
        }))

        task = resp.result
        assert task["id"] == "task-1" and task["sessionId"] == "s-1"
        assert task["status"]["state"] == "completed"
        assert task["status"]["message"] is None
        artifact = task["artifacts"][0]
        assert artifact["name"] == "nps-result" and artifact["index"] == 0
        assert artifact["parts"][0]["data"]["anchor_ref"] == "sha256:test"
        assert task["history"][0]["role"] == "user"

    async def test_only_tasks_send_is_served(self):
        _, a2a, _ = _servers(_options())
        resp = await a2a.dispatch(_req("tasks/get", {"id": "t"}))
        assert resp.error.code == -32601
        assert resp.error.data["error"] == BridgeErrorCodes.DIRECTION_UNSUPPORTED

    async def test_missing_id_is_invalid_params(self):
        _, a2a, _ = _servers(_options())
        assert (await a2a.dispatch(_req("tasks/send", {}))).error.code == -32602
        assert (await a2a.dispatch(_req("tasks/send", "nope"))).error.code == -32602

    async def test_a_single_exposed_action_resolves_without_metadata(self):
        _, a2a, _ = _servers(_options())
        resp = await a2a.dispatch(_req("tasks/send", {
            "id": "t", "message": {"role": "user", "parts": []}}))
        assert resp.result["status"]["state"] == "completed"

    async def test_ambiguity_without_metadata_is_rejected(self):
        options = _options()
        options.add_action("orders.cancel")
        _, a2a, _ = _servers(options)
        resp = await a2a.dispatch(_req("tasks/send", {
            "id": "t", "message": {"role": "user", "parts": []}}))
        assert resp.error.code == -32602
        assert resp.error.data["error"] == BridgeErrorCodes.SERVER_TOOL_NOT_FOUND
        assert resp.error.data["candidates"] == [
            f"{NODE}__orders_cancel", f"{NODE}__orders_lookup"]

    @pytest.mark.parametrize("key", ["action_id", "actionId", "skill_id", "skillId", "skill"])
    async def test_every_skill_metadata_key_is_honoured(self, key):
        options = _options()
        options.add_action("orders.cancel")
        _, a2a, _ = _servers(options)
        resp = await a2a.dispatch(_req("tasks/send", {
            "id": "t", "message": {"role": "user", "parts": []},
            "metadata": {key: "orders.lookup"}}))
        assert resp.result["status"]["state"] == "completed"

    async def test_the_skill_may_be_named_on_the_message_or_a_part(self):
        options = _options()
        options.add_action("orders.cancel")
        _, a2a, _ = _servers(options)

        on_message = await a2a.dispatch(_req("tasks/send", {
            "id": "t", "message": {"role": "user", "parts": [],
                                   "metadata": {"skill": "orders.lookup"}}}))
        assert on_message.result["status"]["state"] == "completed"

        on_part = await a2a.dispatch(_req("tasks/send", {
            "id": "t", "message": {"role": "user", "parts": [
                {"type": "data", "data": {"action_id": "orders.lookup"}}]}}))
        assert on_part.result["status"]["state"] == "completed"

    @pytest.mark.parametrize("task,expected", [
        ({"metadata": {"params": {"a": 1}}}, {"a": 1}),
        ({"metadata": {"arguments": {"b": 2}}}, {"b": 2}),
        ({"message": {"metadata": {"params": {"c": 3}}}}, {"c": 3}),
        ({"message": {"parts": [{"type": "data", "data": {"params": {"d": 4}}}]}}, {"d": 4}),
        ({"message": {"parts": [{"type": "data", "data": {"e": 5}}]}}, {"e": 5}),
        ({"message": {"parts": [{"type": "text", "text": "hi"}]}}, {"text": "hi"}),
        ({"message": {"parts": []}}, None),
    ])
    async def test_argument_extraction_order(self, task, expected):
        seen: list = []

        async def capture(frame: ActionFrame):
            seen.append(frame.params)
            return CapsFrame(anchor_ref="a", count=0, data=[])

        options = _options(action_dispatcher=capture)
        _, a2a, _ = _servers(options)
        await a2a.dispatch(_req("tasks/send", {"id": "t", "message": {}, **task}))
        assert seen[0] == expected

    async def test_a_domain_failure_becomes_a_failed_task_not_a_json_rpc_error(self):
        backend = _FakeBackend(
            NwpNodeDescriptor(name=NODE, role=NwpNodeRole.ACTION),
            actions=[NwpActionDescriptor("do")],
            invoke_result=NwpResult.failure("NPS-CLIENT-CONFLICT",
                                            "NWP-ACTION-IDEMPOTENCY-CONFLICT", "dup"))
        a2a = A2aInboundServer(BridgeInboundOptions(node_id=NODE), [backend])
        resp = await a2a.dispatch(_req("tasks/send", {"id": "t", "message": {}}))

        task = resp.result
        assert task["status"]["state"] == "failed"
        # The NPS code is preserved verbatim in the failure detail.
        assert task["status"]["message"]["parts"][0]["text"] == "dup"
        assert task["artifacts"][0]["name"] == "nps-error"
        assert task["artifacts"][0]["parts"][0]["data"]["error"] == \
            "NWP-ACTION-IDEMPOTENCY-CONFLICT"

    async def test_an_infrastructure_failure_becomes_a_json_rpc_error(self):
        # MUST NOT hand the peer a task object where a transport error belongs —
        # A2A peers retry failed tasks.
        backend = _FakeBackend(
            NwpNodeDescriptor(name=NODE, role=NwpNodeRole.ACTION),
            actions=[NwpActionDescriptor("do")],
            invoke_result=NwpResult.failure("NPS-AUTH-FORBIDDEN", "NWP-AUTH-NID-SCOPE-VIOLATION"))
        a2a = A2aInboundServer(BridgeInboundOptions(node_id=NODE), [backend])
        resp = await a2a.dispatch(_req("tasks/send", {"id": "t", "message": {}}))
        assert resp.result is None
        assert resp.error.code == -32003

    async def test_dispatch_requires_a_request(self):
        _, a2a, _ = _servers(_options())
        with pytest.raises(ValueError):
            await a2a.dispatch(None)


# ── BridgeIn-02: gRPC service logic ───────────────────────────────────────────

class TestGrpcInboundService:
    def _service(self, **kw) -> GrpcInboundService:
        options = _options(**kw)
        options.inbound_protocols = ["grpc"]
        return GrpcInboundService(options, create_backends(options))

    async def test_get_manifest_returns_the_nwm_and_node_type(self):
        resp = await self._service().get_manifest()
        assert json.loads(resp.nwm_json)["node_type"] == "action"
        assert resp.node_type == "action"

    async def test_node_type_is_empty_when_the_role_is_unknown(self):
        backend = _FakeBackend(NwpNodeDescriptor(name="u", role=NwpNodeRole.UNKNOWN))
        options = BridgeInboundOptions(node_id="u", inbound_protocols=["grpc"])
        assert (await GrpcInboundService(options, [backend]).get_manifest()).node_type == ""

    async def test_list_actions_returns_the_action_map(self):
        resp = await self._service().list_actions()
        assert json.loads(resp.actions_json) == {
            "actions": {"orders.lookup": {"description": "Look an order up"}}}

    async def test_invoke_round_trip(self):
        resp = await self._service().invoke(
            "orders.lookup", json.dumps({"id": "o-1"}).encode())
        assert resp.http_status == 200
        assert json.loads(resp.body_json)["data"][0]["order_id"] == "o-1"
        assert resp.task_id == ""

    async def test_invoke_lifts_the_task_id_from_the_payload(self):
        backend = _FakeBackend(
            NwpNodeDescriptor(name=NODE, role=NwpNodeRole.ACTION),
            actions=[NwpActionDescriptor("do")],
            invoke_result=NwpResult.success({"task_id": "t-9"}))
        options = BridgeInboundOptions(node_id=NODE, inbound_protocols=["grpc"])
        assert (await GrpcInboundService(options, [backend]).invoke("do")).task_id == "t-9"

    async def test_empty_action_id_is_invalid_argument(self):
        with pytest.raises(GrpcRpcError) as exc:
            await self._service().invoke("")
        assert exc.value.status_code == "INVALID_ARGUMENT"
        assert exc.value.detail == "action_id is required"

    async def test_query_defaults_an_empty_body_to_an_empty_object(self):
        seen: list = []

        async def capture(frame: QueryFrame):
            seen.append(frame.filter)
            return CapsFrame(anchor_ref="a", count=0, data=[])

        options = _options(node_role=NwpNodeRole.COMPLEX, query_dispatcher=capture)
        options.inbound_protocols = ["grpc"]
        resp = await GrpcInboundService(options, create_backends(options)).query()
        assert seen[0] == {}
        assert resp.http_status == 200

    async def test_a_single_backend_resolves_without_a_named_upstream(self):
        assert (await self._service().get_manifest()).node_type == "action"

    async def test_a_named_upstream_resolves_case_insensitively(self):
        resp = await self._service().get_manifest(UpstreamContext(upstream=NODE.upper()))
        assert resp.node_type == "action"

    async def test_an_unknown_upstream_is_not_found(self):
        with pytest.raises(GrpcRpcError) as exc:
            await self._service().get_manifest(UpstreamContext(upstream="nope"))
        assert exc.value.status_code == "NOT_FOUND"
        assert BridgeErrorCodes.SERVER_TOOL_NOT_FOUND in exc.value.detail

    async def test_an_unnamed_upstream_with_several_backends_is_not_found(self):
        a = _FakeBackend(NwpNodeDescriptor(name="a", role=NwpNodeRole.ACTION))
        b = _FakeBackend(NwpNodeDescriptor(name="b", role=NwpNodeRole.ACTION))
        options = BridgeInboundOptions(node_id="x", inbound_protocols=["grpc"])
        with pytest.raises(GrpcRpcError):
            await GrpcInboundService(options, [a, b]).get_manifest()

    async def test_a_failure_carries_the_exact_nps_fault_in_the_detail(self):
        backend = _FakeBackend(
            NwpNodeDescriptor(name=NODE, role=NwpNodeRole.ACTION),
            actions=[NwpActionDescriptor("do")],
            invoke_result=NwpResult.failure("NPS-AUTH-FORBIDDEN",
                                            "NWP-AUTH-NID-SCOPE-VIOLATION", "denied"))
        options = BridgeInboundOptions(node_id=NODE, inbound_protocols=["grpc"])
        with pytest.raises(GrpcRpcError) as exc:
            await GrpcInboundService(options, [backend]).invoke("do")
        # Not merely the coarse gRPC class — the caller can recover the NPS fault.
        assert exc.value.status_code == "PERMISSION_DENIED"
        assert exc.value.detail == "NPS-AUTH-FORBIDDEN NWP-AUTH-NID-SCOPE-VIOLATION: denied"

    async def test_a_bare_failure_still_produces_a_detail(self):
        backend = _FakeBackend(
            NwpNodeDescriptor(name=NODE, role=NwpNodeRole.ACTION),
            actions=[NwpActionDescriptor("do")],
            invoke_result=NwpResult(ok=False))
        options = BridgeInboundOptions(node_id=NODE, inbound_protocols=["grpc"])
        with pytest.raises(GrpcRpcError) as exc:
            await GrpcInboundService(options, [backend]).invoke("do")
        assert exc.value.detail == "NPS-SERVER-INTERNAL"
