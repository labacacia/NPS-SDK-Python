# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Tests for the NWP Bridge dispatcher subsystem (NPS-CR-0001), ported from the
.NET reference. Outbound dispatchers are driven against a stub HTTP endpoint via
httpx.MockTransport; inbound server bridges via httpx.ASGITransport."""
from __future__ import annotations

import json

import httpx
import pytest

from nps_sdk.ncp.frames import CapsFrame, ErrorFrame
from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.bridge import target_parser, endpoint_validator
from nps_sdk.nwp.bridge.dispatchers import (
    A2aBridgeDispatcher,
    BridgeDispatcherRegistry,
    BridgeNode,
    GrpcBridgeDispatcher,
    HttpBridgeDispatcher,
    McpBridgeDispatcher,
)
from nps_sdk.nwp.bridge.errors import BridgeDispatchException, BridgeErrorCodes
from nps_sdk.nwp.bridge.json_rpc import BridgeJsonRpcErrorCodes, BridgeJsonRpcRequest
from nps_sdk.nwp.bridge.a2a_server import A2aServerBridge
from nps_sdk.nwp.bridge.mcp_server import McpServerBridge
from nps_sdk.nwp.bridge.node_middleware import BridgeNodeMiddleware, BridgeNodeOptions
from nps_sdk.nwp.bridge.server_middleware import BridgeServerMiddleware
from nps_sdk.nwp.bridge.server_options import (
    BridgeServerActionInvoker,
    BridgeServerOptions,
    to_tool_name,
)


# ── helpers ─────────────────────────────────────────────────────────────────────

def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _target(**params):
    return ActionFrame(action_id="bridge.dispatch", params=params)


# ── BridgeTargetParser ──────────────────────────────────────────────────────────

def test_target_parser_nested_bridge_target():
    frame = _target(bridge_target={"protocol": "http", "endpoint": "https://x.example/api", "method": "GET"})
    t = target_parser.from_action_frame(frame)
    assert t.protocol == "http"
    assert t.endpoint == "https://x.example/api"
    assert target_parser.get_string(t, "method") == "GET"


def test_target_parser_top_level_params():
    frame = _target(protocol="mcp", endpoint="https://mcp.example/rpc")
    t = target_parser.from_action_frame(frame)
    assert t.protocol == "mcp"


def test_target_parser_extras_folding():
    t = target_parser.from_json({"protocol": "http", "endpoint": "https://x/y", "extras": {"a": 1}, "b": 2})
    assert t.extras["a"] == 1
    assert t.extras["b"] == 2


def test_target_parser_missing_params_raises():
    with pytest.raises(BridgeDispatchException) as e:
        target_parser.from_action_frame(ActionFrame(action_id="a", params=None))
    assert e.value.error_code == BridgeErrorCodes.TARGET_INVALID


def test_target_parser_missing_protocol_raises():
    with pytest.raises(BridgeDispatchException) as e:
        target_parser.from_json({"endpoint": "https://x/y"})
    assert e.value.error_code == BridgeErrorCodes.TARGET_INVALID


def test_target_parser_not_object_raises():
    with pytest.raises(BridgeDispatchException):
        target_parser.from_json("not-an-object")


def test_get_string_coercions():
    t = target_parser.from_json({"protocol": "http", "endpoint": "https://x/y", "n": 7, "flag": True})
    assert target_parser.get_string(t, "n") == "7"
    assert target_parser.get_string(t, "flag") == "True"
    assert target_parser.get_string(t, "missing", "def") == "def"


# ── BridgeEndpointValidator (SSRF) ──────────────────────────────────────────────

def test_endpoint_rejects_private_host():
    t = target_parser.from_json({"protocol": "http", "endpoint": "http://127.0.0.1/x"})
    with pytest.raises(BridgeDispatchException) as e:
        endpoint_validator.parse_http_endpoint(t)
    assert e.value.error_code == BridgeErrorCodes.ENDPOINT_INVALID


def test_endpoint_rejects_non_http_scheme():
    t = target_parser.from_json({"protocol": "http", "endpoint": "ftp://example.com/x"})
    with pytest.raises(BridgeDispatchException):
        endpoint_validator.parse_http_endpoint(t)


def test_endpoint_rejects_http_when_allow_http_false():
    t = target_parser.from_json({"protocol": "http", "endpoint": "http://example.com/x", "allow_http": False})
    with pytest.raises(BridgeDispatchException):
        endpoint_validator.parse_http_endpoint(t)


def test_endpoint_allows_private_when_reject_private_false():
    t = target_parser.from_json({"protocol": "http", "endpoint": "http://127.0.0.1/x", "reject_private": False})
    parts = endpoint_validator.parse_http_endpoint(t)
    assert parts.hostname == "127.0.0.1"


def test_endpoint_allowed_prefixes_reject():
    t = target_parser.from_json({
        "protocol": "http", "endpoint": "https://evil.example/x",
        "allowed_prefixes": ["https://good.example/"],
    })
    with pytest.raises(BridgeDispatchException):
        endpoint_validator.parse_http_endpoint(t)


def test_endpoint_allowed_prefixes_accept():
    t = target_parser.from_json({
        "protocol": "http", "endpoint": "https://good.example/api/v1",
        "allowed_prefixes": ["https://good.example/api"],
    })
    parts = endpoint_validator.parse_http_endpoint(t)
    assert parts.hostname == "good.example"


# ── HTTP dispatcher ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_http_dispatcher_post_json_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content
        seen["ct"] = request.headers.get("content-type")
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    async with _client(handler) as client:
        d = HttpBridgeDispatcher(client)
        frame = ActionFrame(action_id="a", params={"body": {"x": 1}})
        t = target_parser.from_json({"protocol": "http", "endpoint": "https://api.example/do"})
        caps = await d.dispatch(frame, t)

    assert isinstance(caps, CapsFrame)
    assert caps.anchor_ref == HttpBridgeDispatcher.RESPONSE_ANCHOR_REF
    assert caps.count == 1
    rec = caps.data[0]
    assert rec["status_code"] == 200
    assert rec["success"] is True
    assert rec["body"] == {"ok": True}
    assert seen["method"] == "POST"
    assert json.loads(seen["body"]) == {"x": 1}
    assert "json" in seen["ct"]


@pytest.mark.asyncio
async def test_http_dispatcher_get_has_no_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.content == b""
        return httpx.Response(200, text="hi", headers={"content-type": "text/plain"})

    async with _client(handler) as client:
        d = HttpBridgeDispatcher(client)
        t = target_parser.from_json({"protocol": "http", "endpoint": "https://api.example/x", "method": "GET"})
        caps = await d.dispatch(ActionFrame(action_id="a", params={}), t)

    assert caps.data[0]["body_text"] == "hi"


@pytest.mark.asyncio
async def test_http_dispatcher_applies_custom_headers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-custom") == "v"
        return httpx.Response(204, headers={"content-type": "application/json"})

    async with _client(handler) as client:
        d = HttpBridgeDispatcher(client)
        t = target_parser.from_json({
            "protocol": "http", "endpoint": "https://api.example/x",
            "method": "DELETE", "headers": {"X-Custom": "v"},
        })
        caps = await d.dispatch(ActionFrame(action_id="a"), t)
    assert caps.data[0]["status_code"] == 204


@pytest.mark.asyncio
async def test_http_dispatcher_upstream_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(handler) as client:
        d = HttpBridgeDispatcher(client)
        t = target_parser.from_json({"protocol": "http", "endpoint": "https://api.example/x"})
        with pytest.raises(BridgeDispatchException) as e:
            await d.dispatch(ActionFrame(action_id="a"), t)
    assert e.value.error_code == BridgeErrorCodes.UPSTREAM_FAILED


# ── gRPC (JSON codec) dispatcher ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grpc_dispatcher_frames_message():
    import struct

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ct"] = request.headers.get("content-type")
        body = request.content
        length = struct.unpack(">I", body[1:5])[0]
        captured["payload"] = json.loads(body[5:5 + length])
        # echo one framed reply message
        reply = json.dumps({"pong": 1}).encode()
        wire = b"\x00" + struct.pack(">I", len(reply)) + reply
        return httpx.Response(200, content=wire, headers={
            "content-type": "application/grpc+json", "grpc-status": "0",
        })

    async with _client(handler) as client:
        d = GrpcBridgeDispatcher(client)
        frame = ActionFrame(action_id="a", params={"grpc_message": {"ping": 1}})
        t = target_parser.from_json({"protocol": "grpc", "endpoint": "https://host.example/pkg.Svc/M"})
        caps = await d.dispatch(frame, t)

    assert caps.anchor_ref == GrpcBridgeDispatcher.RESPONSE_ANCHOR_REF
    assert captured["ct"] == "application/grpc+json"
    assert captured["payload"] == {"ping": 1}
    rec = caps.data[0]
    assert rec["grpc_status"] == "0"
    assert rec["success"] is True
    assert rec["messages"] == [{"pong": 1}]


# ── JSON-RPC / MCP / A2A dispatchers ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_dispatcher_default_method_and_response_mapping():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": {"content": []}},
                              headers={"content-type": "application/json"})

    async with _client(handler) as client:
        d = McpBridgeDispatcher(client)
        frame = ActionFrame(action_id="a", params={"id": "1", "params": {"name": "t"}})
        t = target_parser.from_json({"protocol": "mcp", "endpoint": "https://mcp.example/rpc"})
        caps = await d.dispatch(frame, t)

    assert captured["req"]["jsonrpc"] == "2.0"
    assert captured["req"]["method"] == "tools/call"
    assert captured["req"]["params"] == {"name": "t"}
    rec = caps.data[0]
    assert rec["result"] == {"content": []}
    assert rec["jsonrpc_response"]["id"] == "1"


@pytest.mark.asyncio
async def test_a2a_dispatcher_default_method():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "x", "error": {"code": -1, "message": "no"}},
                              headers={"content-type": "application/json"})

    async with _client(handler) as client:
        d = A2aBridgeDispatcher(client)
        t = target_parser.from_json({"protocol": "a2a", "endpoint": "https://a2a.example/rpc", "id": "x"})
        caps = await d.dispatch(ActionFrame(action_id="a", params={"foo": "bar"}), t)

    assert captured["req"]["method"] == "tasks/send"
    assert captured["req"]["id"] == "x"
    assert captured["req"]["params"] == {"foo": "bar"}
    assert caps.data[0]["error"] == {"code": -1, "message": "no"}


@pytest.mark.asyncio
async def test_jsonrpc_rpc_method_override():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 1},
                              headers={"content-type": "application/json"})

    async with _client(handler) as client:
        d = McpBridgeDispatcher(client)
        t = target_parser.from_json({
            "protocol": "mcp", "endpoint": "https://mcp.example/rpc",
            "rpc_method": "tools/list", "rpc_params": {"a": 1},
        })
        await d.dispatch(ActionFrame(action_id="a"), t)

    assert captured["req"]["method"] == "tools/list"
    assert captured["req"]["params"] == {"a": 1}


# ── Registry + BridgeNode facade ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registry_create_default_and_protocols():
    async with _client(lambda r: httpx.Response(200)) as client:
        reg = BridgeDispatcherRegistry.create_default(client)
    assert set(reg.protocols) == {"http", "grpc", "mcp", "a2a"}


def test_registry_resolve_unknown_protocol():
    reg = BridgeDispatcherRegistry()
    with pytest.raises(BridgeDispatchException) as e:
        reg.resolve("nope")
    assert e.value.error_code == BridgeErrorCodes.PROTOCOL_UNSUPPORTED


def test_registry_resolve_empty_protocol():
    with pytest.raises(BridgeDispatchException) as e:
        BridgeDispatcherRegistry().resolve("")
    assert e.value.error_code == BridgeErrorCodes.TARGET_INVALID


def test_registry_register_empty_protocol_raises():
    class Bad:
        protocol = ""

        async def dispatch(self, frame, target):  # pragma: no cover
            ...

    with pytest.raises(ValueError):
        BridgeDispatcherRegistry().register(Bad())


@pytest.mark.asyncio
async def test_bridge_node_facade_routes_by_protocol():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": 1}, headers={"content-type": "application/json"})

    async with _client(handler) as client:
        node = BridgeNode(BridgeDispatcherRegistry.create_default(client))
        frame = _target(bridge_target={"protocol": "http", "endpoint": "https://api.example/x"})
        caps = await node.dispatch(frame)
    assert caps.anchor_ref == HttpBridgeDispatcher.RESPONSE_ANCHOR_REF


@pytest.mark.asyncio
async def test_bridge_node_protocol_unsupported():
    async with _client(lambda r: httpx.Response(200)) as client:
        node = BridgeNode(BridgeDispatcherRegistry())  # empty
        frame = _target(bridge_target={"protocol": "http", "endpoint": "https://api.example/x"})
        with pytest.raises(BridgeDispatchException) as e:
            await node.dispatch(frame)
    assert e.value.error_code == BridgeErrorCodes.PROTOCOL_UNSUPPORTED


# ── to_tool_name ────────────────────────────────────────────────────────────────

def test_to_tool_name_sanitizes():
    assert to_tool_name("com.example/do this") == "com_example_do_this"
    assert to_tool_name("keep-me_1") == "keep-me_1"
    assert to_tool_name("") == "action"
    assert to_tool_name("___") == "action"


# ── Inbound MCP server bridge ───────────────────────────────────────────────────

def _invoker(dispatch):
    opts = BridgeServerOptions(dispatch=dispatch, require_auth=False)
    opts.add_action("echo", description="Echo", input_schema={"type": "object"})
    return opts, BridgeServerActionInvoker(opts)


@pytest.mark.asyncio
async def test_mcp_server_initialize_and_list():
    opts, inv = _invoker(None)
    mcp = McpServerBridge(opts, inv)

    init = await mcp.dispatch(BridgeJsonRpcRequest(method="initialize", id=1))
    assert init.result["protocolVersion"] == "2024-11-05"
    assert init.result["serverInfo"]["name"] == "nps-bridge-server"

    listed = await mcp.dispatch(BridgeJsonRpcRequest(method="tools/list", id=2))
    assert listed.result["tools"][0]["name"] == "echo"
    assert listed.result["tools"][0]["inputSchema"] == {"type": "object"}


@pytest.mark.asyncio
async def test_mcp_server_tool_call_dispatches_local_action():
    seen = {}

    async def dispatch(frame: ActionFrame):
        seen["frame"] = frame
        return CapsFrame(anchor_ref="nps://r", count=1, data=({"ok": True},))

    opts, inv = _invoker(dispatch)
    mcp = McpServerBridge(opts, inv)
    resp = await mcp.dispatch(BridgeJsonRpcRequest(
        method="tools/call", id=3, params={"name": "echo", "arguments": {"a": 1}}))

    assert seen["frame"].action_id == "echo"
    assert seen["frame"].params == {"a": 1}
    assert resp.result["isError"] is False
    assert "ok" in resp.result["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_server_tool_not_found():
    opts, inv = _invoker(lambda f: None)
    mcp = McpServerBridge(opts, inv)
    resp = await mcp.dispatch(BridgeJsonRpcRequest(
        method="tools/call", id=4, params={"name": "missing"}))
    assert resp.error.code == BridgeJsonRpcErrorCodes.TOOL_NOT_FOUND
    assert resp.error.data["error"] == BridgeErrorCodes.SERVER_TOOL_NOT_FOUND


@pytest.mark.asyncio
async def test_mcp_server_dispatcher_missing_returns_error_frame():
    opts, inv = _invoker(None)  # no dispatch configured
    mcp = McpServerBridge(opts, inv)
    resp = await mcp.dispatch(BridgeJsonRpcRequest(
        method="tools/call", id=5, params={"name": "echo"}))
    assert resp.result["isError"] is True
    assert BridgeErrorCodes.SERVER_DISPATCHER_MISSING in resp.result["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_server_method_not_found():
    opts, inv = _invoker(None)
    resp = await McpServerBridge(opts, inv).dispatch(BridgeJsonRpcRequest(method="bogus", id=6))
    assert resp.error.code == BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_mcp_server_dispatch_failure_returns_error_frame():
    async def dispatch(frame):
        raise RuntimeError("kaboom")

    opts, inv = _invoker(dispatch)
    resp = await McpServerBridge(opts, inv).dispatch(BridgeJsonRpcRequest(
        method="tools/call", id=7, params={"name": "echo"}))
    assert resp.result["isError"] is True
    assert BridgeErrorCodes.SERVER_DISPATCH_FAILED in resp.result["content"][0]["text"]


# ── Inbound A2A server bridge ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a2a_server_send_task_dispatches_and_builds_task():
    seen = {}

    async def dispatch(frame: ActionFrame):
        seen["frame"] = frame
        return CapsFrame(anchor_ref="nps://r", count=1, data=({"v": 1},))

    opts = BridgeServerOptions(dispatch=dispatch, require_auth=False)
    opts.add_action("echo")
    a2a = A2aServerBridge(opts, BridgeServerActionInvoker(opts))

    req = BridgeJsonRpcRequest(method="tasks/send", id="r1", params={
        "id": "task-1",
        "message": {"role": "user", "parts": [{"type": "data", "data": {"hello": "world"}}]},
    })
    resp = await a2a.dispatch(req)
    assert seen["frame"].action_id == "echo"
    assert seen["frame"].params == {"hello": "world"}
    assert resp.result["id"] == "task-1"
    assert resp.result["status"]["state"] == "completed"
    assert resp.result["artifacts"][0]["name"] == "nps-result"


@pytest.mark.asyncio
async def test_a2a_server_action_resolution_from_metadata():
    async def dispatch(frame):
        return CapsFrame(anchor_ref="nps://r", count=1, data=())

    opts = BridgeServerOptions(dispatch=dispatch, require_auth=False)
    opts.add_action("alpha")
    opts.add_action("beta")
    a2a = A2aServerBridge(opts, BridgeServerActionInvoker(opts))

    req = BridgeJsonRpcRequest(method="tasks/send", id="r", params={
        "id": "t", "metadata": {"skill_id": "beta"},
        "message": {"role": "user", "parts": []},
    })
    resp = await a2a.dispatch(req)
    assert resp.result["status"]["state"] == "completed"


@pytest.mark.asyncio
async def test_a2a_server_tool_not_found_when_ambiguous():
    async def dispatch(frame):  # pragma: no cover - not reached
        return CapsFrame(anchor_ref="nps://r", count=1, data=())

    opts = BridgeServerOptions(dispatch=dispatch, require_auth=False)
    opts.add_action("alpha")
    opts.add_action("beta")
    a2a = A2aServerBridge(opts, BridgeServerActionInvoker(opts))

    req = BridgeJsonRpcRequest(method="tasks/send", id="r", params={
        "id": "t", "message": {"role": "user", "parts": []},
    })
    resp = await a2a.dispatch(req)
    assert resp.error.code == BridgeJsonRpcErrorCodes.INVALID_PARAMS
    assert resp.error.data["error"] == BridgeErrorCodes.SERVER_TOOL_NOT_FOUND


@pytest.mark.asyncio
async def test_a2a_server_missing_id():
    opts = BridgeServerOptions(dispatch=lambda f: None, require_auth=False)
    opts.add_action("echo")
    a2a = A2aServerBridge(opts, BridgeServerActionInvoker(opts))
    resp = await a2a.dispatch(BridgeJsonRpcRequest(method="tasks/send", id="r", params={
        "message": {"role": "user", "parts": []}}))
    assert resp.error.code == BridgeJsonRpcErrorCodes.INVALID_PARAMS


@pytest.mark.asyncio
async def test_a2a_server_method_not_found():
    opts = BridgeServerOptions(require_auth=False)
    a2a = A2aServerBridge(opts, BridgeServerActionInvoker(opts))
    resp = await a2a.dispatch(BridgeJsonRpcRequest(method="tasks/get", id="r"))
    assert resp.error.code == BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND


def test_a2a_agent_card():
    opts = BridgeServerOptions(require_auth=True)
    opts.add_action("echo", description="d", tags=("x",))
    a2a = A2aServerBridge(opts, BridgeServerActionInvoker(opts))
    card = a2a.build_agent_card("https://host/a2a")
    assert card["url"] == "https://host/a2a"
    assert card["skills"][0]["id"] == "echo"
    assert card["authentication"]["schemes"] == ["apikey"]


# ── Inbound BridgeServerMiddleware (ASGI) ───────────────────────────────────────

def _asgi_client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://svc")


@pytest.mark.asyncio
async def test_server_middleware_mcp_tool_call():
    async def dispatch(frame: ActionFrame):
        return CapsFrame(anchor_ref="nps://r", count=1, data=({"echoed": frame.params},))

    opts = BridgeServerOptions(dispatch=dispatch, require_auth=False)
    opts.add_action("echo")
    app = BridgeServerMiddleware(opts)

    async with _asgi_client(app) as client:
        resp = await client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "echo", "arguments": {"a": 1}},
        })
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 1
    assert body["result"]["isError"] is False


@pytest.mark.asyncio
async def test_server_middleware_agent_card_route():
    opts = BridgeServerOptions(require_auth=False)
    opts.add_action("echo")
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    assert resp.json()["skills"][0]["id"] == "echo"


@pytest.mark.asyncio
async def test_server_middleware_auth_required_rejects_missing_agent():
    async def verify(nid, headers):  # pragma: no cover - not reached
        return True

    opts = BridgeServerOptions(require_auth=True, verify_agent=verify)
    opts.add_action("echo")
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "tasks/send"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_server_middleware_auth_accepts_valid_agent():
    async def dispatch(frame):
        return CapsFrame(anchor_ref="nps://r", count=1, data=())

    async def verify(nid, headers):
        return nid == "urn:nps:agent:example.com:alice"

    opts = BridgeServerOptions(require_auth=True, verify_agent=verify, dispatch=dispatch)
    opts.add_action("echo")
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.post("/a2a",
            headers={"X-NWP-Agent": "urn:nps:agent:example.com:alice"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tasks/send",
                  "params": {"id": "t", "message": {"role": "user", "parts": []}}})
    assert resp.status_code == 200
    assert resp.json()["result"]["status"]["state"] == "completed"


@pytest.mark.asyncio
async def test_server_middleware_rejects_invalid_agent_nid():
    async def verify(nid, headers):  # pragma: no cover
        return True

    opts = BridgeServerOptions(require_auth=True, verify_agent=verify)
    opts.add_action("echo")
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.post("/mcp",
            headers={"X-NWP-Agent": "not-a-nid"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_server_middleware_payload_too_large():
    opts = BridgeServerOptions(require_auth=False, max_request_body_bytes=10)
    opts.add_action("echo")
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "echo", "arguments": {"big": "x" * 200}}})
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_server_middleware_method_not_post():
    opts = BridgeServerOptions(require_auth=False)
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.get("/a2a")
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_server_middleware_unknown_route_404():
    opts = BridgeServerOptions(require_auth=False)
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.post("/nope", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_server_middleware_mcp_sse_get():
    opts = BridgeServerOptions(require_auth=False)
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert resp.status_code == 200
    assert "event: endpoint" in resp.text


# ── Outbound BridgeNodeMiddleware (ASGI) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_node_middleware_manifest_and_actions():
    async with _client(lambda r: httpx.Response(200)) as up:
        registry = BridgeDispatcherRegistry.create_default(up)
        app = BridgeNodeMiddleware(BridgeNode(registry), registry, BridgeNodeOptions())
        async with _asgi_client(app) as client:
            manifest = await client.get("/.nwm")
            actions = await client.get("/actions")

    assert manifest.status_code == 200
    m = manifest.json()
    assert m["node_type"] == "bridge"
    assert m["bridge_protocols"] == ["a2a", "grpc", "http", "mcp"]
    assert actions.json()[0]["action_id"] == "bridge.dispatch"


@pytest.mark.asyncio
async def test_node_middleware_invoke_dispatches_http():
    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": 1}, headers={"content-type": "application/json"})

    async with _client(upstream) as up:
        registry = BridgeDispatcherRegistry.create_default(up)
        app = BridgeNodeMiddleware(BridgeNode(registry), registry, BridgeNodeOptions())
        async with _asgi_client(app) as client:
            resp = await client.post("/invoke", json={
                "action_id": "bridge.dispatch",
                "params": {"bridge_target": {"protocol": "http", "endpoint": "https://api.example/x"}},
            })
    assert resp.status_code == 200
    assert resp.json()["anchor_ref"] == HttpBridgeDispatcher.RESPONSE_ANCHOR_REF


@pytest.mark.asyncio
async def test_node_middleware_unknown_action_404():
    async with _client(lambda r: httpx.Response(200)) as up:
        registry = BridgeDispatcherRegistry.create_default(up)
        app = BridgeNodeMiddleware(BridgeNode(registry), registry, BridgeNodeOptions())
        async with _asgi_client(app) as client:
            resp = await client.post("/invoke", json={
                "action_id": "wrong",
                "params": {"bridge_target": {"protocol": "http", "endpoint": "https://x/y"}}})
    assert resp.status_code == 404
    assert resp.json()["error"] == "NWP-BRIDGE-ACTION-NOT-FOUND"


@pytest.mark.asyncio
async def test_node_middleware_endpoint_invalid_maps_400():
    async with _client(lambda r: httpx.Response(200)) as up:
        registry = BridgeDispatcherRegistry.create_default(up)
        app = BridgeNodeMiddleware(BridgeNode(registry), registry, BridgeNodeOptions())
        async with _asgi_client(app) as client:
            resp = await client.post("/invoke", json={
                "action_id": "bridge.dispatch",
                "params": {"bridge_target": {"protocol": "http", "endpoint": "http://127.0.0.1/x"}}})
    assert resp.status_code == 400
    assert resp.json()["error"] == BridgeErrorCodes.ENDPOINT_INVALID


@pytest.mark.asyncio
async def test_node_middleware_upstream_failed_maps_502():
    def upstream(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with _client(upstream) as up:
        registry = BridgeDispatcherRegistry.create_default(up)
        app = BridgeNodeMiddleware(BridgeNode(registry), registry, BridgeNodeOptions())
        async with _asgi_client(app) as client:
            resp = await client.post("/invoke", json={
                "action_id": "bridge.dispatch",
                "params": {"bridge_target": {"protocol": "http", "endpoint": "https://api.example/x"}}})
    assert resp.status_code == 502
    assert resp.json()["error"] == BridgeErrorCodes.UPSTREAM_FAILED


@pytest.mark.asyncio
async def test_node_middleware_bad_json_400():
    async with _client(lambda r: httpx.Response(200)) as up:
        registry = BridgeDispatcherRegistry.create_default(up)
        app = BridgeNodeMiddleware(BridgeNode(registry), registry, BridgeNodeOptions())
        async with _asgi_client(app) as client:
            resp = await client.post("/invoke", content=b"{not json",
                                     headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"] == BridgeErrorCodes.TARGET_INVALID


@pytest.mark.asyncio
async def test_node_middleware_get_invoke_405():
    async with _client(lambda r: httpx.Response(200)) as up:
        registry = BridgeDispatcherRegistry.create_default(up)
        app = BridgeNodeMiddleware(BridgeNode(registry), registry, BridgeNodeOptions())
        async with _asgi_client(app) as client:
            resp = await client.get("/invoke")
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_node_middleware_unknown_path_404():
    async with _client(lambda r: httpx.Response(200)) as up:
        registry = BridgeDispatcherRegistry.create_default(up)
        app = BridgeNodeMiddleware(BridgeNode(registry), registry, BridgeNodeOptions())
        async with _asgi_client(app) as client:
            resp = await client.get("/other")
    assert resp.status_code == 404


# ── extra coverage: dispatchers + validator + a2a text part ─────────────────────

@pytest.mark.asyncio
async def test_jsonrpc_params_from_frame_body_and_default_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = json.loads(request.content)
        return httpx.Response(200, text="not-json", headers={"content-type": "text/plain"})

    async with _client(handler) as client:
        d = McpBridgeDispatcher(client)
        # params dict without rpc_params/params/body -> filtered passthrough; id from idempotency_key
        frame = ActionFrame(action_id="a", idempotency_key="idem-1", params={"x": 1, "bridge_target": {}})
        t = target_parser.from_json({"protocol": "mcp", "endpoint": "https://mcp.example/rpc"})
        caps = await d.dispatch(frame, t)

    assert captured["req"]["id"] == "idem-1"
    assert captured["req"]["params"] == {"x": 1}
    assert caps.data[0]["body_text"] == "not-json"


@pytest.mark.asyncio
async def test_grpc_message_from_target_extra():
    import struct

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        length = struct.unpack(">I", body[1:5])[0]
        captured["payload"] = json.loads(body[5:5 + length])
        return httpx.Response(200, content=b"", headers={"content-type": "application/grpc+json"})

    async with _client(handler) as client:
        d = GrpcBridgeDispatcher(client)
        t = target_parser.from_json({
            "protocol": "grpc", "endpoint": "https://h/pkg.S/M", "message": {"m": 1}})
        caps = await d.dispatch(ActionFrame(action_id="a"), t)

    assert captured["payload"] == {"m": 1}
    assert caps.data[0]["messages"] == []


def test_endpoint_allow_http_true_ok():
    t = target_parser.from_json({"protocol": "http", "endpoint": "http://public.example/x"})
    parts = endpoint_validator.parse_http_endpoint(t)
    assert parts.scheme == "http"


def test_endpoint_allowed_prefix_root_matches_any_path():
    t = target_parser.from_json({
        "protocol": "http", "endpoint": "https://good.example/deep/path",
        "allowed_prefixes": "https://good.example/",
    })
    parts = endpoint_validator.parse_http_endpoint(t)
    assert parts.hostname == "good.example"


def test_endpoint_allowed_prefix_port_mismatch_rejected():
    t = target_parser.from_json({
        "protocol": "https", "endpoint": "https://good.example:8443/x",
        "allowed_prefixes": ["https://good.example/x"],
    })
    with pytest.raises(BridgeDispatchException):
        endpoint_validator.parse_http_endpoint(t)


@pytest.mark.asyncio
async def test_a2a_text_part_becomes_text_params():
    seen = {}

    async def dispatch(frame: ActionFrame):
        seen["frame"] = frame
        return CapsFrame(anchor_ref="nps://r", count=1, data=())

    opts = BridgeServerOptions(dispatch=dispatch, require_auth=False)
    opts.add_action("echo")
    a2a = A2aServerBridge(opts, BridgeServerActionInvoker(opts))
    req = BridgeJsonRpcRequest(method="tasks/send", id="r", params={
        "id": "t", "message": {"role": "user", "parts": [{"type": "text", "text": "hello"}]}})
    resp = await a2a.dispatch(req)
    assert seen["frame"].params == {"text": "hello"}
    assert resp.result["status"]["state"] == "completed"


@pytest.mark.asyncio
async def test_a2a_dispatch_failure_builds_failed_task():
    async def dispatch(frame):
        raise RuntimeError("boom")

    opts = BridgeServerOptions(dispatch=dispatch, require_auth=False)
    opts.add_action("echo")
    a2a = A2aServerBridge(opts, BridgeServerActionInvoker(opts))
    req = BridgeJsonRpcRequest(method="tasks/send", id="r", params={
        "id": "t", "message": {"role": "user", "parts": [{"type": "data", "data": {}}]}})
    resp = await a2a.dispatch(req)
    assert resp.result["status"]["state"] == "failed"
    assert resp.result["artifacts"][0]["name"] == "nps-error"


@pytest.mark.asyncio
async def test_server_middleware_dispatch_timeout_504():
    import asyncio as _asyncio

    async def dispatch(frame):
        await _asyncio.sleep(0.2)
        return CapsFrame(anchor_ref="nps://r", count=1, data=())

    opts = BridgeServerOptions(dispatch=dispatch, require_auth=False, dispatch_timeout_ms=10)
    opts.add_action("echo")
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.post("/a2a", json={
            "jsonrpc": "2.0", "id": 1, "method": "tasks/send",
            "params": {"id": "t", "message": {"role": "user", "parts": []}}})
    assert resp.status_code == 504


@pytest.mark.asyncio
async def test_server_middleware_empty_body_invalid_request():
    opts = BridgeServerOptions(require_auth=False)
    opts.add_action("echo")
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.post("/mcp", content=b"")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == BridgeJsonRpcErrorCodes.INVALID_REQUEST


@pytest.mark.asyncio
async def test_server_middleware_parse_error():
    opts = BridgeServerOptions(require_auth=False)
    opts.add_action("echo")
    app = BridgeServerMiddleware(opts)
    async with _asgi_client(app) as client:
        resp = await client.post("/mcp", content=b"{bad",
                                 headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == BridgeJsonRpcErrorCodes.PARSE_ERROR


@pytest.mark.asyncio
async def test_node_middleware_requires_auth_when_configured():
    async with _client(lambda r: httpx.Response(200)) as up:
        registry = BridgeDispatcherRegistry.create_default(up)
        app = BridgeNodeMiddleware(BridgeNode(registry), registry, BridgeNodeOptions(require_auth=True))
        async with _asgi_client(app) as client:
            resp = await client.post("/invoke", json={"action_id": "bridge.dispatch"})
    assert resp.status_code == 401
