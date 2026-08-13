# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""End-to-end tests for the pure-ASGI Action Node server, driven over httpx.ASGITransport."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from nps_sdk.nwp import error_codes
from nps_sdk.nwp.action_node_server import (
    ActionCallbackValidator,
    ActionContext,
    ActionExecutionError,
    ActionExecutionResult,
    ActionNodeApp,
    ActionNodeOptions,
    ActionSpec,
    IActionNodeProvider,
    InMemoryActionTaskStore,
    InMemoryIdempotencyCache,
    is_private_host,
)
from nps_sdk.nwp.frames import ActionFrame

PREFIX = "/orders"
NODE_ID = "urn:nps:node:api.example.com:orders"


class _EchoProvider(IActionNodeProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, frame: ActionFrame, ctx: ActionContext) -> ActionExecutionResult:
        self.calls += 1
        return ActionExecutionResult(
            result={"ok": True, "params": frame.params, "n": self.calls},
            token_est=7,
        )


class _SlowProvider(IActionNodeProvider):
    async def execute(self, frame, ctx) -> ActionExecutionResult:
        await asyncio.sleep(5.0)
        return ActionExecutionResult(result={"never": True})


class _RaisingProvider(IActionNodeProvider):
    async def execute(self, frame, ctx) -> ActionExecutionResult:
        raise ActionExecutionError(422, "NPS-CLIENT-UNPROCESSABLE",
                                   error_codes.ACTION_PARAMS_INVALID, "bad params from provider")


def _opts(**kw) -> ActionNodeOptions:
    base = dict(
        node_id=NODE_ID,
        path_prefix=PREFIX,
        actions={
            "orders.create": ActionSpec(async_=False, result_anchor="nps:orders:result",
                                        timeout_ms_default=100, timeout_ms_max=200),
            "orders.batch": ActionSpec(async_=True, timeout_ms_default=100),
        },
    )
    base.update(kw)
    return ActionNodeOptions(**base)


def _app(provider=None, **kw) -> ActionNodeApp:
    return ActionNodeApp(_opts(**kw), provider or _EchoProvider())


def _client(app: ActionNodeApp, agent: str | None = "urn:nps:agent:tester") -> httpx.AsyncClient:
    headers = {"X-NWP-Agent": agent} if agent else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://action", headers=headers)


# ── Manifest / schema ────────────────────────────────────────────────────────────

class TestManifest:
    async def test_nwm_shape(self) -> None:
        app = _app(display_name="Orders")
        async with _client(app) as http:
            resp = await http.get(f"{PREFIX}/.nwm")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/nwp-manifest+json"
        assert resp.headers["x-nwp-node-type"] == "action"
        m = resp.json()
        assert m["nwp"] == "0.4"
        assert m["node_id"] == NODE_ID
        assert m["node_type"] == "action"
        assert m["endpoints"] == {"invoke": f"{PREFIX}/invoke", "schema": f"{PREFIX}/.schema"}

    async def test_actions_registry(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.get(f"{PREFIX}/actions")
            schema = await http.get(f"{PREFIX}/.schema")
        assert resp.status_code == 200
        actions = resp.json()["actions"]
        assert actions["orders.create"]["async"] is False
        assert actions["orders.batch"]["async"] is True
        assert schema.json() == resp.json()

    def test_reserved_action_registration_rejected(self) -> None:
        with pytest.raises(ValueError):
            ActionNodeApp(
                ActionNodeOptions(node_id=NODE_ID, path_prefix=PREFIX,
                                  actions={"system.task.status": ActionSpec()}),
                _EchoProvider())


# ── Routing / auth ───────────────────────────────────────────────────────────────

class TestRouting:
    async def test_unknown_subpath_404(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.get(f"{PREFIX}/nope")
        assert resp.status_code == 404
        assert resp.json()["error"] == error_codes.ACTION_NOT_FOUND

    async def test_invoke_get_405(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.get(f"{PREFIX}/invoke")
        assert resp.status_code == 405

    async def test_auth_required_rejects(self) -> None:
        app = _app(require_auth=True)
        async with _client(app, agent=None) as http:
            resp = await http.get(f"{PREFIX}/.nwm")
        assert resp.status_code == 401
        assert resp.json()["error"] == error_codes.AUTH_NID_SCOPE_VIOLATION


# ── Sync invoke ──────────────────────────────────────────────────────────────────

class TestSyncInvoke:
    async def test_happy_path(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create", "params": {"sku": "x"},
                                         "request_id": "req-1"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/nwp-capsule"
        assert resp.headers["x-nwp-node-type"] == "action"
        assert resp.headers["x-nwp-schema"] == "nps:orders:result"
        assert resp.headers["x-nwp-request-id"] == "req-1"
        assert resp.headers["x-nwp-tokens"] == "7"
        caps = resp.json()
        assert caps["count"] == 1
        assert caps["anchor_ref"] == "nps:orders:result"
        assert caps["data"][0]["ok"] is True

    async def test_unknown_action_404(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke", json={"action_id": "orders.nope"})
        assert resp.status_code == 404
        assert resp.json()["error"] == error_codes.ACTION_NOT_FOUND

    async def test_bad_json_400(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke", content=b"not json")
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.ACTION_PARAMS_INVALID

    async def test_async_on_sync_only_action_400(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create", "async": True})
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.ACTION_PARAMS_INVALID

    async def test_bad_priority_400(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create", "priority": "urgent"})
        assert resp.status_code == 400

    async def test_provider_error_maps(self) -> None:
        app = _app(_RaisingProvider())
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke", json={"action_id": "orders.create"})
        assert resp.status_code == 422
        assert resp.json()["error"] == error_codes.ACTION_PARAMS_INVALID

    async def test_timeout_504(self) -> None:
        app = _app(_SlowProvider())
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create", "timeout_ms": 20})
        assert resp.status_code == 504
        assert resp.json()["error"] == error_codes.NODE_UNAVAILABLE

    async def test_timeout_clamped_to_spec_max(self) -> None:
        # spec.timeout_ms_max=200; request 999_999 clamps to 200 → still faster than
        # the 5s slow provider, so we still time out at ~200ms (not the requested value).
        app = _app(_SlowProvider())
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create", "timeout_ms": 999999})
        assert resp.status_code == 504


# ── Async invoke ─────────────────────────────────────────────────────────────────

class TestAsyncInvoke:
    async def test_async_accepted_and_completes(self) -> None:
        provider = _EchoProvider()
        app = _app(provider)
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.batch", "async": True,
                                         "request_id": "r2"})
            assert resp.status_code == 202
            assert resp.headers["x-nwp-node-type"] == "action"
            body = resp.json()
            task_id = body["task_id"]
            assert body["status"] == "pending"
            assert body["poll_url"] == f"{PREFIX}/invoke"
            assert body["request_id"] == "r2"

            # Let the background task run.
            await asyncio.sleep(0.05)

            status = await http.post(f"{PREFIX}/invoke",
                                     json={"action_id": "system.task.status",
                                           "params": {"task_id": task_id}})
        assert status.status_code == 200
        rec = status.json()["data"][0]
        assert rec["task_id"] == task_id
        assert rec["status"] == "completed"
        assert rec["progress"] == 1.0
        assert rec["result"]["ok"] is True


# ── Reserved task actions ────────────────────────────────────────────────────────

class TestReservedTaskActions:
    async def test_status_missing_task_id_400(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "system.task.status", "params": {}})
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.ACTION_PARAMS_INVALID

    async def test_status_unknown_task_404(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "system.task.status",
                                         "params": {"task_id": "nope"}})
        assert resp.status_code == 404
        assert resp.json()["error"] == error_codes.TASK_NOT_FOUND

    async def test_cancel_pending_task(self) -> None:
        store = InMemoryActionTaskStore()
        store.create("t1", "orders.batch", None, "urn:nps:agent:tester")
        app = ActionNodeApp(_opts(), _EchoProvider(), task_store=store)
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "system.task.cancel",
                                         "params": {"task_id": "t1"}})
        assert resp.status_code == 200
        assert resp.json()["data"][0]["status"] == "cancelled"
        assert store.get("t1").status == "cancelled"

    async def test_cancel_terminal_task_409(self) -> None:
        store = InMemoryActionTaskStore()
        store.create("t2", "orders.batch", None, "urn:nps:agent:tester")
        store.complete("t2", {"done": True})
        app = ActionNodeApp(_opts(), _EchoProvider(), task_store=store)
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "system.task.cancel",
                                         "params": {"task_id": "t2"}})
        assert resp.status_code == 409
        assert resp.json()["error"] == error_codes.TASK_ALREADY_CANCELLED


# ── Idempotency ──────────────────────────────────────────────────────────────────

class TestIdempotency:
    async def test_sync_rehit_returns_cached_no_reexec(self) -> None:
        provider = _EchoProvider()
        app = _app(provider)
        payload = {"action_id": "orders.create", "params": {"a": 1}, "idempotency_key": "k1"}
        async with _client(app) as http:
            r1 = await http.post(f"{PREFIX}/invoke", json=payload)
            r2 = await http.post(f"{PREFIX}/invoke", json=payload)
        assert r1.status_code == 200 and r2.status_code == 200
        assert provider.calls == 1  # second call served from cache
        assert r1.json()["data"][0]["n"] == r2.json()["data"][0]["n"] == 1

    async def test_conflict_on_different_params(self) -> None:
        app = _app()
        async with _client(app) as http:
            await http.post(f"{PREFIX}/invoke",
                            json={"action_id": "orders.create", "params": {"a": 1},
                                  "idempotency_key": "k2"})
            conflict = await http.post(f"{PREFIX}/invoke",
                                       json={"action_id": "orders.create", "params": {"a": 2},
                                             "idempotency_key": "k2"})
        assert conflict.status_code == 409
        assert conflict.json()["error"] == error_codes.ACTION_IDEMPOTENCY_CONFLICT

    async def test_async_rehit_returns_same_task(self) -> None:
        app = _app()
        payload = {"action_id": "orders.batch", "async": True, "idempotency_key": "ak"}
        async with _client(app) as http:
            r1 = await http.post(f"{PREFIX}/invoke", json=payload)
            r2 = await http.post(f"{PREFIX}/invoke", json=payload)
        assert r1.status_code == 202
        # Re-hit returns the same task handle (202).
        assert r2.status_code == 202
        assert r1.json()["task_id"] == r2.json()["task_id"]

    def test_cache_ttl_expiry(self) -> None:
        import datetime as dt
        now = [dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)]
        cache = InMemoryIdempotencyCache(clock=lambda: now[0])
        from nps_sdk.nwp.action_node_server import IdempotentEntry
        cache.try_store("a", "k", IdempotentEntry(
            action_id="a", params_hash="h",
            expires_at=now[0] + dt.timedelta(seconds=10)))
        assert cache.get("a", "k") is not None
        now[0] += dt.timedelta(seconds=20)
        assert cache.get("a", "k") is None


# ── Callback SSRF guard ──────────────────────────────────────────────────────────

class TestCallbackGuard:
    async def test_http_callback_rejected(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create",
                                         "callback_url": "http://example.com/cb"})
        assert resp.status_code == 400
        assert resp.json()["error"] == error_codes.ACTION_PARAMS_INVALID

    async def test_private_callback_rejected(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create",
                                         "callback_url": "https://127.0.0.1/cb"})
        assert resp.status_code == 400
        assert "SSRF" in resp.json()["message"]

    async def test_public_https_callback_ok(self) -> None:
        app = _app()
        async with _client(app) as http:
            resp = await http.post(f"{PREFIX}/invoke",
                                   json={"action_id": "orders.create",
                                         "callback_url": "https://hooks.example.com/cb"})
        assert resp.status_code == 200

    def test_validator_and_is_private_host(self) -> None:
        assert ActionCallbackValidator.validate("") is not None
        assert ActionCallbackValidator.validate("not a url") is not None
        assert ActionCallbackValidator.validate("https://8.8.8.8/x") is None
        assert ActionCallbackValidator.validate("https://10.0.0.1/x") is not None
        assert ActionCallbackValidator.validate("https://10.0.0.1/x", reject_private=False) is None
        assert is_private_host("localhost") is True
        assert is_private_host("::1") is True
        assert is_private_host("172.16.0.5") is True
        assert is_private_host("172.32.0.5") is False


# ── Task store unit coverage ─────────────────────────────────────────────────────

class TestTaskStore:
    def test_transitions_and_purge(self) -> None:
        import datetime as dt
        now = [dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)]
        store = InMemoryActionTaskStore(clock=lambda: now[0])
        store.create("x", "a", "r", "agent")
        assert store.try_transition("x", "pending", "running") is True
        assert store.try_transition("x", "pending", "running") is False
        assert store.update_progress("x", 0.5) is True
        assert store.get("x").progress == 0.5
        assert store.fail("x", {"code": "E"}) is True
        assert store.complete("x", {}) is False  # already terminal
        assert store.cancel("x") is False
        now[0] += dt.timedelta(hours=2)
        assert store.purge_expired(3600, now[0]) == 1
        assert store.get("x") is None

    def test_create_collision(self) -> None:
        store = InMemoryActionTaskStore()
        store.create("d", "a", None, None)
        with pytest.raises(ValueError):
            store.create("d", "a", None, None)
