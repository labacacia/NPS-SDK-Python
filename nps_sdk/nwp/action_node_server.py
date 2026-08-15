# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS NWP — Action Node server (pure ASGI, zero third-party deps).

Port of the .NET reference ``NPS.NWP.ActionNode.ActionNodeMiddleware`` (NPS-2 §3.2, §7)
in the same pure-ASGI shape as :mod:`nps_sdk.nwp.anchor_server`. The app is a plain
ASGI 3.0 callable — run it under any ASGI server or drive it in-process with
``httpx.ASGITransport``.

Endpoints (under ``options.path_prefix``):

  GET  /.nwm             — Neural Web Manifest (node_type=action)
  GET  /.schema, /actions — declared action registry
  POST /invoke           — ActionFrame → gates → provider; sync + async execution

Reserved actions ``system.task.status`` and ``system.task.cancel`` (NPS-2 §7.3) are
handled by the server itself and MUST NOT appear in ``ActionNodeOptions.actions``.
"""

from __future__ import annotations

import abc
import asyncio
import dataclasses
import datetime as _dt
import hashlib
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from ipaddress import AddressValueError, IPv4Address, IPv6Address
from typing import Any
from urllib.parse import urlsplit

from nps_sdk.nwp import error_codes, http_headers
from nps_sdk.ncp.frames import StreamFrame
from nps_sdk.nwp.frames import ActionFrame

# Reserved action ids (NPS-2 §7.3).
SYSTEM_TASK_STATUS = "system.task.status"
SYSTEM_TASK_CANCEL = "system.task.cancel"

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})

_KNOWN_ROUTES = frozenset({
    "/.nwm", "/.nwm/", "/.schema", "/.schema/",
    "/actions", "/actions/", "/invoke", "/invoke/",
})


# ── Action spec / options ───────────────────────────────────────────────────────

@dataclasses.dataclass
class ActionSpec:
    """NWM action registry entry (NPS-2 §4.6). Describes one invocable operation."""

    async_: bool = False
    description: str | None = None
    params_anchor: str | None = None
    result_anchor: str | None = None
    idempotent: bool | None = None
    timeout_ms_default: int | None = None
    timeout_ms_max: int | None = None
    required_capability: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"async": self.async_}
        if self.description is not None:         d["description"] = self.description
        if self.params_anchor is not None:       d["params_anchor"] = self.params_anchor
        if self.result_anchor is not None:       d["result_anchor"] = self.result_anchor
        if self.idempotent is not None:          d["idempotent"] = self.idempotent
        if self.timeout_ms_default is not None:  d["timeout_ms_default"] = self.timeout_ms_default
        if self.timeout_ms_max is not None:      d["timeout_ms_max"] = self.timeout_ms_max
        if self.required_capability is not None: d["required_capability"] = self.required_capability
        return d


@dataclasses.dataclass
class ActionNodeOptions:
    """Configuration for a single Action Node (port of .NET ``ActionNodeOptions``)."""

    node_id: str
    path_prefix: str
    actions: dict[str, ActionSpec] = dataclasses.field(default_factory=dict)
    display_name: str | None = None
    require_auth: bool = False
    default_timeout_ms: int = 5_000
    max_timeout_ms: int = 300_000
    idempotency_ttl_seconds: float = 24 * 3600
    task_retention_seconds: float = 3600
    reject_private_callback_urls: bool = True
    default_token_budget: int = 0
    profiles: dict[str, Any] | None = None


# ── Execution context / result / provider ──────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ActionContext:
    """Execution context passed to :class:`IActionNodeProvider.execute`."""

    agent_nid: str | None
    request_id: str | None
    spec: ActionSpec
    timeout_ms: int
    priority: str = "normal"
    task_id: str | None = None
    wire_input_bytes: int = 0


@dataclasses.dataclass(frozen=True)
class ActionExecutionResult:
    """Result of a single action execution."""

    result: Any = None
    stream_frames: AsyncIterator[StreamFrame] | None = None
    anchor_ref: str | None = None
    token_est: int = 0


class ActionExecutionError(Exception):
    """Raised by a provider to return a specific NWP error envelope."""

    def __init__(
        self,
        http_status: int,
        nps_status: str,
        error_code: str,
        message: str,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.nps_status = nps_status
        self.error_code = error_code
        self.message = message
        self.details = details


class IActionNodeProvider(abc.ABC):
    """Host-supplied executor. All declared ``action_id`` values MUST be handled."""

    async def authorize(self, frame: ActionFrame, context: ActionContext) -> None:
        """Authorize before idempotency lookup so cached replay cannot bypass policy."""

    @abc.abstractmethod
    async def execute(self, frame: ActionFrame, context: ActionContext) -> ActionExecutionResult: ...


# ── Task store (NPS-2 §7.2) ─────────────────────────────────────────────────────

@dataclasses.dataclass
class ActionTaskRecord:
    task_id: str
    action_id: str
    status: str  # pending | running | completed | failed | cancelled
    created_at: _dt.datetime
    updated_at: _dt.datetime
    request_id: str | None = None
    agent_nid: str | None = None
    progress: float | None = None
    result: Any = None
    error: Any = None


class IActionTaskStore(abc.ABC):
    @abc.abstractmethod
    def create(self, task_id: str, action_id: str, request_id: str | None,
               agent_nid: str | None) -> ActionTaskRecord: ...

    @abc.abstractmethod
    def get(self, task_id: str) -> ActionTaskRecord | None: ...

    @abc.abstractmethod
    def try_transition(self, task_id: str, expected_status: str, new_status: str) -> bool: ...

    @abc.abstractmethod
    def complete(self, task_id: str, result: Any) -> bool: ...

    @abc.abstractmethod
    def fail(self, task_id: str, error: Any) -> bool: ...

    @abc.abstractmethod
    def cancel(self, task_id: str) -> bool: ...

    @abc.abstractmethod
    def update_progress(self, task_id: str, progress: float) -> bool: ...

    @abc.abstractmethod
    def purge_expired(self, retention_seconds: float, now: _dt.datetime) -> int: ...


class InMemoryActionTaskStore(IActionTaskStore):
    """Thread-safe-enough in-process task store for single-instance nodes and tests."""

    def __init__(self, clock: Callable[[], _dt.datetime] | None = None) -> None:
        self._tasks: dict[str, ActionTaskRecord] = {}
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))

    def create(self, task_id, action_id, request_id, agent_nid) -> ActionTaskRecord:
        now = self._clock()
        if task_id in self._tasks:
            raise ValueError(f"Task id collision: {task_id}")
        rec = ActionTaskRecord(
            task_id=task_id, action_id=action_id, status="pending",
            created_at=now, updated_at=now, request_id=request_id, agent_nid=agent_nid,
        )
        self._tasks[task_id] = rec
        return rec

    def get(self, task_id: str) -> ActionTaskRecord | None:
        return self._tasks.get(task_id)

    def try_transition(self, task_id, expected_status, new_status) -> bool:
        rec = self._tasks.get(task_id)
        if rec is None or rec.status != expected_status:
            return False
        rec.status = new_status
        rec.updated_at = self._clock()
        return True

    def complete(self, task_id, result) -> bool:
        rec = self._tasks.get(task_id)
        if rec is None or rec.status in _TERMINAL_STATES:
            return False
        rec.status = "completed"
        rec.result = result
        rec.progress = 1.0
        rec.updated_at = self._clock()
        return True

    def fail(self, task_id, error) -> bool:
        rec = self._tasks.get(task_id)
        if rec is None or rec.status in _TERMINAL_STATES:
            return False
        rec.status = "failed"
        rec.error = error
        rec.updated_at = self._clock()
        return True

    def cancel(self, task_id) -> bool:
        rec = self._tasks.get(task_id)
        if rec is None or rec.status in _TERMINAL_STATES:
            return False
        rec.status = "cancelled"
        rec.updated_at = self._clock()
        return True

    def update_progress(self, task_id, progress) -> bool:
        rec = self._tasks.get(task_id)
        if rec is None:
            return False
        rec.progress = max(0.0, min(1.0, progress))
        rec.updated_at = self._clock()
        return True

    def purge_expired(self, retention_seconds, now) -> int:
        cutoff = now - _dt.timedelta(seconds=retention_seconds)
        expired = [
            k for k, r in self._tasks.items()
            if r.status in _TERMINAL_STATES and r.updated_at < cutoff
        ]
        for k in expired:
            self._tasks.pop(k, None)
        return len(expired)


# ── Idempotency cache (NPS-2 §7.1) ──────────────────────────────────────────────

@dataclasses.dataclass
class IdempotentEntry:
    action_id: str
    params_hash: str
    expires_at: _dt.datetime
    result: Any = None
    stream_frames: tuple[StreamFrame, ...] | None = None
    anchor_ref: str | None = None
    task_id: str | None = None


class IIdempotencyCache(abc.ABC):
    @abc.abstractmethod
    def get(self, action_id: str, idempotency_key: str) -> IdempotentEntry | None: ...

    @abc.abstractmethod
    def try_store(self, action_id: str, idempotency_key: str, entry: IdempotentEntry) -> bool: ...

    @abc.abstractmethod
    def purge_expired(self, now: _dt.datetime) -> int: ...


class InMemoryIdempotencyCache(IIdempotencyCache):
    """In-process idempotency cache with TTL, for single-instance nodes and tests."""

    def __init__(self, clock: Callable[[], _dt.datetime] | None = None) -> None:
        self._entries: dict[str, IdempotentEntry] = {}
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))

    @staticmethod
    def _key(action_id: str, idempotency_key: str) -> str:
        return f"{action_id}{idempotency_key}"

    def get(self, action_id, idempotency_key) -> IdempotentEntry | None:
        key = self._key(action_id, idempotency_key)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._entries.pop(key, None)
            return None
        return entry

    def try_store(self, action_id, idempotency_key, entry) -> bool:
        key = self._key(action_id, idempotency_key)
        now = self._clock()
        existing = self._entries.get(key)
        if existing is not None:
            if existing.expires_at <= now:
                self._entries[key] = entry
                return True
            return existing.params_hash == entry.params_hash
        self._entries[key] = entry
        return True

    def purge_expired(self, now) -> int:
        expired = [k for k, e in self._entries.items() if e.expires_at <= now]
        for k in expired:
            self._entries.pop(k, None)
        return len(expired)


# ── Callback URL SSRF guard (NPS-2 §7.1) ────────────────────────────────────────

class ActionCallbackValidator:
    """Validates ``callback_url``: MUST be https; SHOULD NOT target a private host."""

    @staticmethod
    def validate(callback_url: str, reject_private: bool = True) -> str | None:
        if not callback_url or not callback_url.strip():
            return "callback_url must not be empty."
        parts = urlsplit(callback_url)
        if not parts.scheme or not parts.hostname:
            return f"callback_url '{callback_url}' is not a valid absolute URI."
        if parts.scheme.lower() != "https":
            return f"callback_url MUST use the https:// scheme (got '{parts.scheme}://')."
        if reject_private and is_private_host(parts.hostname):
            return (f"callback_url host '{parts.hostname}' resolves to a private or "
                    "loopback address (SSRF guard).")
        return None


def is_private_host(host: str) -> bool:
    """Detect loopback / link-local / RFC1918 literals. DNS resolution is avoided."""
    if not host:
        return True
    if host.lower() == "localhost":
        return True
    stripped = host.strip("[]")
    try:
        ip: IPv4Address | IPv6Address = IPv4Address(stripped)
    except AddressValueError:
        try:
            ip6 = IPv6Address(stripped)
        except AddressValueError:
            return False  # a resolvable hostname, not an IP literal
        if ip6.ipv4_mapped is not None:
            ip = ip6.ipv4_mapped
        else:
            return ip6.is_loopback or ip6.is_link_local or ip6.is_site_local or ip6.is_private
    b = ip.packed
    return (
        b[0] == 127 or b[0] == 10 or b[0] == 0
        or (b[0] == 172 and 16 <= b[1] <= 31)
        or (b[0] == 192 and b[1] == 168)
        or (b[0] == 169 and b[1] == 254)
    )


# ── Serialization helpers ───────────────────────────────────────────────────────

def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).isoformat()


def _hash_params(params: Any) -> str:
    canon = "null" if params is None else json.dumps(params, separators=(",", ":"),
                                                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest().upper()


async def _read_body(receive: Callable) -> bytes:
    chunks: list[bytes] = []
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            break
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body", False):
            break
    return b"".join(chunks)


# ── The ASGI app ─────────────────────────────────────────────────────────────────

class ActionNodeApp:
    """Pure-ASGI Action Node server."""

    def __init__(
        self,
        options: ActionNodeOptions,
        provider: IActionNodeProvider,
        *,
        task_store: IActionTaskStore | None = None,
        idempotency_cache: IIdempotencyCache | None = None,
        clock: Callable[[], _dt.datetime] | None = None,
    ) -> None:
        if SYSTEM_TASK_STATUS in options.actions or SYSTEM_TASK_CANCEL in options.actions:
            raise ValueError(
                f"Reserved action ids '{SYSTEM_TASK_STATUS}' / '{SYSTEM_TASK_CANCEL}' are "
                "provided by the server and MUST NOT be registered in ActionNodeOptions.actions.")
        self._opt = options
        self._provider = provider
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self._tasks = task_store or InMemoryActionTaskStore(self._clock)
        self._idem = idempotency_cache or InMemoryIdempotencyCache(self._clock)
        self._background_tasks: dict[str, asyncio.Task[None]] = {}
        self._prefix = options.path_prefix.rstrip("/")
        self._nwm_json = _json_bytes(self._build_manifest())
        self._actions_json = _json_bytes(
            {"actions": {aid: spec.to_dict() for aid, spec in options.actions.items()}})

    # ── ASGI entrypoint ──────────────────────────────────────────────────────────

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            return
        path = scope.get("path", "")
        if not path.startswith(self._prefix):
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND",
                                   error_codes.ACTION_NOT_FOUND, "no NWP node at this path.")
            return
        sub = path[len(self._prefix):]
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        method = scope.get("method", "GET")

        if sub not in _KNOWN_ROUTES:
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND",
                                   error_codes.ACTION_NOT_FOUND, "unknown action sub-path.")
            return

        if self._opt.require_auth and http_headers.AGENT.lower() not in headers:
            await self._send_error(send, 401, "NPS-CLIENT-UNAUTHORIZED",
                                   error_codes.AUTH_NID_SCOPE_VIOLATION,
                                   "X-NWP-Agent header is required.")
            return

        if sub in ("/.nwm", "/.nwm/"):
            await self._send(send, 200, self._nwm_json, http_headers.MIME_MANIFEST,
                             {http_headers.NODE_TYPE: "action"})
        elif sub in ("/.schema", "/.schema/", "/actions", "/actions/"):
            await self._send(send, 200, self._actions_json, "application/json")
        elif sub in ("/invoke", "/invoke/"):
            if method != "POST":
                await self._send(send, 405, b"", "application/json")
                return
            await self._handle_invoke(receive, send, headers)

    # ── /invoke ──────────────────────────────────────────────────────────────────

    async def _handle_invoke(self, receive: Callable, send: Callable, headers: dict[str, str]) -> None:
        try:
            raw_body = await _read_body(receive)
            raw = json.loads(raw_body or b"{}")
            frame = ActionFrame.from_dict(raw)
        except (json.JSONDecodeError, ValueError, KeyError) as ex:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID, str(ex))
            return

        # Fields not carried by the Python ActionFrame dataclass — read from the raw body.
        callback_url = raw.get("callback_url")
        priority = raw.get("priority")
        request_id = raw.get("request_id")

        if frame.action_id == SYSTEM_TASK_STATUS:
            await self._handle_task_status(send, frame, request_id, headers)
            return
        if frame.action_id == SYSTEM_TASK_CANCEL:
            await self._handle_task_cancel(send, frame, request_id, headers)
            return

        spec = self._opt.actions.get(frame.action_id)
        if spec is None:
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND",
                                   error_codes.ACTION_NOT_FOUND,
                                   f"Unknown action_id '{frame.action_id}'.")
            return

        if priority is not None and priority not in ("low", "normal", "high"):
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID,
                                   f"priority '{priority}' is invalid (allowed: low/normal/high).")
            return

        if frame.async_ and not spec.async_:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID,
                                   f"action '{frame.action_id}' does not support async execution.")
            return

        effective_timeout = self._clamp_timeout(frame.timeout_ms, spec)

        if callback_url is not None:
            err = ActionCallbackValidator.validate(
                callback_url, self._opt.reject_private_callback_urls)
            if err is not None:
                await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                       error_codes.ACTION_PARAMS_INVALID, err)
                return

        agent_nid = headers.get(http_headers.AGENT.lower())
        eff_priority = priority or "normal"
        ctx = ActionContext(
            agent_nid=agent_nid, request_id=request_id, spec=spec,
            timeout_ms=effective_timeout, priority=eff_priority, task_id=None,
            wire_input_bytes=len(raw_body),
        )
        try:
            await self._provider.authorize(frame, ctx)
        except ActionExecutionError as ex:
            await self._send_error(send, ex.http_status, ex.nps_status,
                                   ex.error_code, ex.message, details=ex.details)
            return
        except Exception:  # noqa: BLE001
            await self._send_error(send, 500, "NPS-SERVER-INTERNAL",
                                   error_codes.NODE_UNAVAILABLE,
                                   "action authorization failed.")
            return

        cache_action_id = self._scoped_cache_action_id(frame.action_id, agent_nid)
        params_hash = _hash_params(frame.params)
        if frame.idempotency_key is not None:
            cached = self._idem.get(cache_action_id, frame.idempotency_key)
            if cached is not None:
                if cached.params_hash != params_hash:
                    await self._send_error(send, 409, "NPS-CLIENT-CONFLICT",
                                           error_codes.ACTION_IDEMPOTENCY_CONFLICT,
                                           "idempotency_key reuse with different params.")
                    return
                if cached.task_id is not None:
                    rec = self._tasks.get(cached.task_id)
                    status = rec.status if rec is not None else "pending"
                    await self._send_async(send, cached.task_id, status, request_id, None)
                    return
                if cached.stream_frames is not None:
                    await self._send_stream(
                        send, _as_async_frames(cached.stream_frames), request_id
                    )
                    return
                await self._send_caps(send, cached.result, cached.anchor_ref, request_id)
                return

        if frame.async_:
            task_id = secrets.token_hex(16)
            self._tasks.create(task_id, frame.action_id, request_id, agent_nid)
            if frame.idempotency_key is not None:
                self._idem.try_store(cache_action_id, frame.idempotency_key, IdempotentEntry(
                    action_id=frame.action_id, params_hash=params_hash, task_id=task_id,
                    expires_at=self._clock() + _dt.timedelta(seconds=self._opt.idempotency_ttl_seconds),
                ))
            ctx = ActionContext(
                agent_nid=agent_nid, request_id=request_id, spec=spec,
                timeout_ms=effective_timeout, priority=eff_priority, task_id=task_id,
                wire_input_bytes=len(raw_body),
            )
            task = asyncio.create_task(self._run_async_task(frame, ctx, effective_timeout))
            self._background_tasks[task_id] = task
            await self._send_async(send, task_id, "pending", request_id, spec.timeout_ms_default)
            return

        # Synchronous path.
        try:
            result = await asyncio.wait_for(
                self._provider.execute(frame, ctx), timeout=effective_timeout / 1000.0)
        except asyncio.TimeoutError:
            await self._send_error(send, 504, "NPS-SERVER-TIMEOUT",
                                   error_codes.NODE_UNAVAILABLE, "action execution timed out.")
            return
        except ActionExecutionError as ex:
            await self._send_error(send, ex.http_status, ex.nps_status,
                                   ex.error_code, ex.message, details=ex.details)
            return
        except Exception:  # noqa: BLE001
            await self._send_error(send, 500, "NPS-SERVER-INTERNAL",
                                   error_codes.NODE_UNAVAILABLE, "action execution failed.")
            return

        anchor_ref = result.anchor_ref or spec.result_anchor
        if result.stream_frames is not None:
            completed = await self._send_stream(send, result.stream_frames, request_id)
            if completed is not None and frame.idempotency_key is not None:
                self._idem.try_store(cache_action_id, frame.idempotency_key, IdempotentEntry(
                    action_id=frame.action_id, params_hash=params_hash,
                    stream_frames=completed, anchor_ref=anchor_ref,
                    expires_at=self._clock() + _dt.timedelta(
                        seconds=self._opt.idempotency_ttl_seconds),
                ))
            return
        if frame.idempotency_key is not None:
            self._idem.try_store(cache_action_id, frame.idempotency_key, IdempotentEntry(
                action_id=frame.action_id, params_hash=params_hash,
                result=result.result, anchor_ref=anchor_ref,
                expires_at=self._clock() + _dt.timedelta(seconds=self._opt.idempotency_ttl_seconds),
            ))
        await self._send_caps(send, result.result, anchor_ref, request_id, result.token_est)

    async def _run_async_task(self, frame: ActionFrame, ctx: ActionContext, timeout_ms: int) -> None:
        assert ctx.task_id is not None
        self._tasks.try_transition(ctx.task_id, "pending", "running")
        try:
            res = await asyncio.wait_for(
                self._provider.execute(frame, ctx), timeout=timeout_ms / 1000.0)
            self._tasks.complete(ctx.task_id, res.result)
        except asyncio.TimeoutError:
            self._tasks.fail(ctx.task_id, {"code": error_codes.NODE_UNAVAILABLE,
                                           "message": "task timed out"})
        except asyncio.CancelledError:
            if self._tasks.get(ctx.task_id).status != "cancelled":
                self._tasks.fail(ctx.task_id, {"code": error_codes.NODE_UNAVAILABLE,
                                               "message": "task cancelled"})
        except ActionExecutionError as ex:
            self._tasks.fail(ctx.task_id, {"code": ex.error_code, "message": ex.message})
        except Exception as ex:  # noqa: BLE001
            self._tasks.fail(ctx.task_id, {"code": error_codes.NODE_UNAVAILABLE,
                                           "message": str(ex)})
        finally:
            self._background_tasks.pop(ctx.task_id, None)

    # ── system.task.status / system.task.cancel ──────────────────────────────────

    async def _handle_task_status(self, send: Callable, frame: ActionFrame,
                                  request_id: str | None,
                                  headers: dict[str, str]) -> None:
        task_id = self._read_string_param(frame.params, "task_id")
        if not task_id:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID, "params.task_id is required.")
            return
        rec = self._tasks.get(task_id)
        if rec is None:
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND",
                                   error_codes.TASK_NOT_FOUND, f"Unknown task_id '{task_id}'.")
            return
        if not self._owns_task(headers, rec):
            await self._send_error(send, 403, "NPS-AUTH-FORBIDDEN",
                                   error_codes.AUTH_NID_SCOPE_VIOLATION,
                                   "The caller does not own this task.")
            return
        status: dict[str, Any] = {
            "task_id": rec.task_id,
            "status": rec.status,
            "created_at": _iso(rec.created_at),
            "updated_at": _iso(rec.updated_at),
        }
        if rec.progress is not None:  status["progress"] = rec.progress
        if rec.request_id is not None: status["request_id"] = rec.request_id
        if rec.result is not None:    status["result"] = rec.result
        if rec.error is not None:     status["error"] = rec.error
        await self._send_caps(send, status, None, request_id)

    async def _handle_task_cancel(self, send: Callable, frame: ActionFrame,
                                  request_id: str | None,
                                  headers: dict[str, str]) -> None:
        task_id = self._read_string_param(frame.params, "task_id")
        if not task_id:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID, "params.task_id is required.")
            return
        rec = self._tasks.get(task_id)
        if rec is None:
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND",
                                   error_codes.TASK_NOT_FOUND, f"Unknown task_id '{task_id}'.")
            return
        if not self._owns_task(headers, rec):
            await self._send_error(send, 403, "NPS-AUTH-FORBIDDEN",
                                   error_codes.AUTH_NID_SCOPE_VIOLATION,
                                   "The caller does not own this task.")
            return
        if rec.status in _TERMINAL_STATES:
            await self._send_error(send, 409, "NPS-CLIENT-CONFLICT",
                                   error_codes.TASK_ALREADY_CANCELLED,
                                   f"Task '{task_id}' is already in a terminal state ('{rec.status}').")
            return
        self._tasks.cancel(task_id)
        task = self._background_tasks.get(task_id)
        if task is not None:
            task.cancel()
        await self._send_caps(send, {"task_id": task_id, "status": "cancelled"}, None, request_id)

    # ── Helpers ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _read_string_param(params: Any, name: str) -> str | None:
        if not isinstance(params, dict):
            return None
        v = params.get(name)
        return v if isinstance(v, str) else None

    def _clamp_timeout(self, requested: int, spec: ActionSpec) -> int:
        spec_max = spec.timeout_ms_max or self._opt.max_timeout_ms
        hard_max = min(spec_max, self._opt.max_timeout_ms)
        if requested <= 0:
            return spec.timeout_ms_default or self._opt.default_timeout_ms
        return min(requested, hard_max)

    def _scoped_cache_action_id(self, action_id: str, agent_nid: str | None) -> str:
        return f"{self._opt.node_id}\x1f{action_id}\x1f{agent_nid or ''}"

    @staticmethod
    def _owns_task(headers: dict[str, str], task: ActionTaskRecord) -> bool:
        return task.agent_nid == headers.get(http_headers.AGENT.lower())

    # ── Response writers ─────────────────────────────────────────────────────────

    async def _send(self, send: Callable, status: int, body: bytes, content_type: str,
                    extra_headers: dict[str, Any] | None = None) -> None:
        hdrs = [(b"content-type", content_type.encode("latin-1"))]
        for k, v in (extra_headers or {}).items():
            hdrs.append((k.lower().encode("latin-1"), str(v).encode("latin-1")))
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})

    async def _send_error(self, send: Callable, http_status: int, nps_status: str,
                          error_code: str, message: str, details: Any | None = None) -> None:
        env: dict[str, Any] = {"status": nps_status, "error": error_code, "message": message}
        if details is not None:
            env["details"] = details
        await self._send(send, http_status, _json_bytes(env), "application/json")

    async def _send_caps(self, send: Callable, payload: Any, anchor_ref: str | None,
                         request_id: str | None, token_est: int = 0) -> None:
        data = [] if payload is None else [payload]
        caps: dict[str, Any] = {
            "anchor_ref": anchor_ref or "",
            "count": len(data),
            "data": data,
        }
        if token_est > 0:
            caps["token_est"] = token_est
        extra: dict[str, Any] = {http_headers.NODE_TYPE: "action"}
        if anchor_ref is not None:
            extra[http_headers.SCHEMA] = anchor_ref
        if token_est > 0:
            extra[http_headers.TOKENS] = token_est
        if request_id is not None:
            extra[http_headers.REQUEST_ID] = request_id
        await self._send(send, 200, _json_bytes(caps), http_headers.MIME_CAPSULE, extra)

    async def _send_stream(
        self,
        send: Callable,
        source: AsyncIterator[StreamFrame],
        request_id: str | None,
    ) -> tuple[StreamFrame, ...] | None:
        headers = [(b"content-type", b"application/x-ndjson"),
                   (http_headers.NODE_TYPE.lower().encode("latin-1"), b"action")]
        if request_id is not None:
            headers.append((http_headers.REQUEST_ID.lower().encode("latin-1"),
                            request_id.encode("latin-1")))
        await send({"type": "http.response.start", "status": 200, "headers": headers})

        stream_id = secrets.token_hex(16)
        emitted: list[StreamFrame] = []
        next_seq = 0
        terminal = False
        try:
            async for supplied in source:
                if terminal:
                    raise ActionExecutionError(
                        500, "NPS-SERVER-INTERNAL", error_codes.NODE_UNAVAILABLE,
                        "action stream emitted frames after its terminal frame")
                if supplied.seq != next_seq:
                    raise ActionExecutionError(
                        500, "NPS-SERVER-INTERNAL", error_codes.NODE_UNAVAILABLE,
                        f"action stream sequence expected {next_seq}, got {supplied.seq}")
                if supplied.error_code is not None and not supplied.is_last:
                    raise ActionExecutionError(
                        500, "NPS-SERVER-INTERNAL", error_codes.NODE_UNAVAILABLE,
                        "action stream error_code is terminal-only")
                frame = dataclasses.replace(supplied, stream_id=stream_id)
                emitted.append(frame)
                await send({"type": "http.response.body",
                            "body": _json_bytes(frame.to_dict()) + b"\n",
                            "more_body": True})
                next_seq += 1
                terminal = frame.is_last
            if not terminal:
                raise ActionExecutionError(
                    500, "NPS-SERVER-INTERNAL", error_codes.NODE_UNAVAILABLE,
                    "action stream ended without a terminal frame")
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return tuple(emitted) if emitted[-1].error_code is None else None
        except asyncio.CancelledError:
            raise
        except Exception as ex:  # noqa: BLE001
            error_code = (ex.error_code if isinstance(ex, ActionExecutionError)
                          else error_codes.NODE_UNAVAILABLE)
            if not terminal:
                failure = StreamFrame(
                    stream_id=stream_id, seq=next_seq, is_last=True,
                    error_code=error_code, data=(),
                )
                await send({"type": "http.response.body",
                            "body": _json_bytes(failure.to_dict()) + b"\n",
                            "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return None
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()

    async def _send_async(self, send: Callable, task_id: str, status: str,
                          request_id: str | None, estimated_ms: int | None) -> None:
        body: dict[str, Any] = {
            "task_id": task_id,
            "status": status,
            "poll_url": f"{self._prefix}/invoke",
        }
        if estimated_ms is not None:
            body["estimated_ms"] = estimated_ms
        if request_id is not None:
            body["request_id"] = request_id
        await self._send(send, 202, _json_bytes(body), "application/json",
                         {http_headers.NODE_TYPE: "action"})

    # ── Manifest ─────────────────────────────────────────────────────────────────

    def _build_manifest(self) -> dict[str, Any]:
        opt = self._opt
        base = self._prefix
        m: dict[str, Any] = {
            "nwp": "0.4",
            "node_id": opt.node_id,
            "node_type": "action",
        }
        if opt.display_name is not None:
            m["display_name"] = opt.display_name
        m["wire_formats"] = ["ncp-capsule", "json"]
        m["preferred_format"] = "json"
        supports_stream = bool(
            (opt.profiles or {}).get("llm", {}).get("supports_stream", False)
        )
        m["capabilities"] = {
            "query": False, "stream": supports_stream, "token_budget_hint": True
        }
        m["auth"] = {
            "required": opt.require_auth,
            "identity_type": "nip-cert" if opt.require_auth else "none",
        }
        m["endpoints"] = {"invoke": f"{base}/invoke", "schema": f"{base}/.schema"}
        if opt.profiles is not None:
            m["profiles"] = opt.profiles
        return m


async def _as_async_frames(frames: tuple[StreamFrame, ...]) -> AsyncIterator[StreamFrame]:
    for frame in frames:
        yield frame
