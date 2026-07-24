# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""End-to-end tests for the pure-ASGI Complex Node server, driven over httpx.ASGITransport."""

from __future__ import annotations

import httpx
import pytest

from nps_sdk.nwp import error_codes
from nps_sdk.nwp.action_node_server import (
    ActionContext,
    ActionExecutionResult,
    ActionSpec,
)
from nps_sdk.nwp.complex_node_server import (
    ABSOLUTE_MAX_DEPTH,
    ComplexChildUrlValidator,
    ComplexGraphRef,
    ComplexNodeApp,
    ComplexNodeOptions,
    IComplexNodeProvider,
    NullComplexNodeProvider,
)
from nps_sdk.nwp.frames import ActionFrame, QueryFrame
from nps_sdk.nwp.memory_node_server import MemoryNodeQueryResult, MemoryNodeSchema, MemoryNodeField

PREFIX = "/orders"
NODE_ID = "urn:nps:node:api.example.com:orders"
CHILD_ID = "urn:nps:node:api.example.com:users"

_SCHEMA = MemoryNodeSchema(fields=[
    MemoryNodeField(name="id", type="string"),
    MemoryNodeField(name="total", type="number"),
])


class _Provider(IComplexNodeProvider):
    def __init__(self, rows=None):
        self._rows = rows if rows is not None else [{"id": "o1", "total": 5}]

    async def query(self, frame, options) -> MemoryNodeQueryResult:
        return MemoryNodeQueryResult(rows=self._rows)

    async def execute(self, frame, ctx) -> ActionExecutionResult:
        return ActionExecutionResult(result={"invoked": frame.action_id}, token_est=3)


def _opts(**kw) -> ComplexNodeOptions:
    base = dict(
        node_id=NODE_ID,
        path_prefix=PREFIX,
        schema=_SCHEMA,
        actions={"orders.cancel": ActionSpec(async_=False, result_anchor="nps:x")},
    )
    base.update(kw)
    return ComplexNodeOptions(**base)


def _app(provider=None, **kw) -> ComplexNodeApp:
    return ComplexNodeApp(_opts(**kw), provider or _Provider())


def _client(app, agent="urn:nps:agent:tester", child_client=None) -> httpx.AsyncClient:
    headers = {"X-NWP-Agent": agent} if agent else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://complex", headers=headers)


# ── Manifest ─────────────────────────────────────────────────────────────────────

class TestManifest:
    async def test_nwm_advertises_graph(self) -> None:
        app = _app(graph=[ComplexGraphRef("user", "https://api.example.com/users")],
                   graph_max_depth=3)
        async with _client(app) as http:
            resp = await http.get(f"{PREFIX}/.nwm")
        assert resp.status_code == 200
        assert resp.headers["x-nwp-node-type"] == "complex"
        m = resp.json()
        assert m["node_type"] == "complex"
        assert m["graph"]["max_depth"] == 3
        assert m["graph"]["refs"][0] == {"rel": "user", "node_url": "https://api.example.com/users"}
        assert m["endpoints"]["invoke"] == f"{PREFIX}/invoke"
        assert m["capabilities"]["query"] is True

    async def test_schema_endpoint(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.get(f"{PREFIX}/.schema")
        assert resp.status_code == 200
        assert resp.headers["x-nwp-schema"].startswith("sha256:")
        assert "fields" in resp.json()

    def test_reserved_action_rejected(self) -> None:
        with pytest.raises(ValueError):
            ComplexNodeApp(_opts(actions={"system.task.cancel": ActionSpec()}), _Provider())

    def test_depth_over_absolute_cap_rejected(self) -> None:
        with pytest.raises(ValueError):
            ComplexNodeApp(_opts(graph_max_depth=ABSOLUTE_MAX_DEPTH + 1), _Provider())


# ── Local query (no expansion) ───────────────────────────────────────────────────

class TestLocalQuery:
    async def test_local_rows(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/query", json={"limit": 10})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/nwp-capsule"
        caps = resp.json()
        assert caps["count"] == 1
        assert caps["data"][0]["id"] == "o1"
        assert "graph" not in caps

    async def test_bad_json_400(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/query", content=b"xx")
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.QUERY_FILTER_INVALID


# ── Cycle detection & depth ──────────────────────────────────────────────────────

class TestCycleAndDepth:
    async def test_cycle_detected(self) -> None:
        app = _app(graph=[ComplexGraphRef("user", "https://api.example.com/users")])
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/query", json={"limit": 5},
                                   headers={"X-NWP-Trace": f"other,{NODE_ID}"})
        assert resp.status_code == 422
        assert resp.json()["error"] == error_codes.GRAPH_CYCLE

    async def test_depth_over_node_max_400(self) -> None:
        app = _app(graph_max_depth=2)
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/query", json={"limit": 5},
                                   headers={"X-NWP-Depth": "3"})
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.DEPTH_EXCEEDED

    async def test_depth_not_integer_400(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/query", json={"limit": 5},
                                   headers={"X-NWP-Depth": "abc"})
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.DEPTH_EXCEEDED

    async def test_depth_zero_no_expansion(self) -> None:
        app = _app(graph=[ComplexGraphRef("user", "https://api.example.com/users")])
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/query", json={"limit": 5},
                                   headers={"X-NWP-Depth": "0"})
        # depth 0 → local only, no graph field
        assert resp.status_code == 200
        assert "graph" not in resp.json()


# ── Graph expansion (real child fetch over injected ASGI client) ─────────────────

def _child_app() -> ComplexNodeApp:
    """A leaf Complex Node used as a graph child."""
    child_opts = ComplexNodeOptions(
        node_id=CHILD_ID, path_prefix="", schema=_SCHEMA,
    )
    return ComplexNodeApp(child_opts, _Provider(rows=[{"id": "u1", "total": 0}]))


class TestGraphExpansion:
    async def test_expands_child(self) -> None:
        child = _child_app()
        child_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=child),
                                       base_url="http://child")
        parent = ComplexNodeApp(
            _opts(graph=[ComplexGraphRef("user", "http://child/")],
                  allow_http_child_urls=True, reject_private_child_urls=False),
            _Provider(), http_client=child_http)
        try:
            async with _client(parent) as http:
                resp = await http.post(f"{PREFIX}/query", json={"limit": 5},
                                       headers={"X-NWP-Depth": "1"})
        finally:
            await child_http.aclose()
        assert resp.status_code == 200
        caps = resp.json()
        assert caps["count"] == 1  # local rows
        graph = caps["graph"]
        assert len(graph) == 1
        assert graph[0]["rel"] == "user"
        assert graph[0]["data"]["data"][0]["id"] == "u1"

    async def test_child_ssrf_rejected_inline(self) -> None:
        # Child URL fails the https guard → error entry in graph, no fetch attempted.
        parent = _app(graph=[ComplexGraphRef("user", "http://internal/users")])
        async with _client(parent) as http:
            resp = await http.post(f"{PREFIX}/query", json={"limit": 5},
                                   headers={"X-NWP-Depth": "1"})
        assert resp.status_code == 200
        entry = resp.json()["graph"][0]
        assert entry["error"]["code"] == error_codes.AUTH_NID_SCOPE_VIOLATION

    async def test_child_http_error_becomes_entry(self) -> None:
        # Point at a child app whose path prefix does not match → child returns 404.
        child = ComplexNodeApp(
            ComplexNodeOptions(node_id=CHILD_ID, path_prefix="/somewhere-else", schema=_SCHEMA),
            _Provider())
        child_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=child),
                                       base_url="http://child")
        parent = ComplexNodeApp(
            _opts(graph=[ComplexGraphRef("user", "http://child/")],
                  allow_http_child_urls=True, reject_private_child_urls=False),
            _Provider(), http_client=child_http)
        try:
            async with _client(parent) as http:
                resp = await http.post(f"{PREFIX}/query", json={"limit": 5},
                                       headers={"X-NWP-Depth": "1"})
        finally:
            await child_http.aclose()
        entry = resp.json()["graph"][0]
        assert entry["error"]["code"] == error_codes.NODE_UNAVAILABLE


# ── Invoke ───────────────────────────────────────────────────────────────────────

class TestInvoke:
    async def test_sync_invoke(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.cancel", "request_id": "r"})
        assert resp.status_code == 200
        assert resp.headers["x-nwp-node-type"] == "complex"
        assert resp.headers["x-nwp-request-id"] == "r"
        assert resp.json()["data"][0]["invoked"] == "orders.cancel"

    async def test_async_rejected(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.cancel", "async": True})
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.ACTION_PARAMS_INVALID

    async def test_unknown_action_404(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke", json={"action_id": "orders.nope"})
        assert resp.status_code == 404

    async def test_null_provider_query_empty(self) -> None:
        app = ComplexNodeApp(
            ComplexNodeOptions(node_id=NODE_ID, path_prefix=PREFIX), NullComplexNodeProvider())
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/query", json={"limit": 5})
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ── Child URL validator unit coverage ────────────────────────────────────────────

class TestChildUrlValidator:
    def test_rules(self) -> None:
        v = ComplexChildUrlValidator.validate
        assert v("", []) is not None
        assert v("not-a-url", []) is not None
        assert v("http://x.com/a", []) is not None            # http rejected
        assert v("http://x.com/a", [], allow_http=True) is None
        assert v("https://8.8.8.8/a", []) is None
        assert v("https://127.0.0.1/a", []) is not None       # SSRF
        assert v("https://ok.com/a", ["https://ok.com"]) is None
        assert v("https://evil.com/a", ["https://ok.com"]) is not None  # allowlist miss
