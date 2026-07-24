# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS NWP — Complex Node server (pure ASGI, zero third-party deps).

Port of the .NET reference ``NPS.NWP.ComplexNode.ComplexNodeMiddleware`` (NPS-2 §2.1, §11)
in the same pure-ASGI shape as :mod:`nps_sdk.nwp.action_node_server`.

Endpoints (under ``options.path_prefix``):

  GET  /.nwm             — Neural Web Manifest (node_type=complex; advertises graph refs)
  GET  /.schema, /actions — schema + declared action registry
  POST /query            — local rows + optional graph expansion of declared child nodes
  POST /invoke           — synchronous action execution (no async task machinery)

Graph expansion (NPS-2 §11 / §13.2):
  * ``X-NWP-Depth`` controls recursion; clamped to ``AbsoluteMaxDepth`` (5).
  * ``X-NWP-Trace`` (comma-separated node_ids) is used for cycle detection.
  * Child URLs are validated with an https + allowlist + SSRF guard before fetch.

Child fetches use ``httpx`` (already a first-party client dependency in this SDK).
"""

from __future__ import annotations

import abc
import asyncio
import dataclasses
import hashlib
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from nps_sdk.nwp import error_codes, http_headers
from nps_sdk.nwp.action_node_server import (
    ActionContext,
    ActionExecutionError,
    ActionExecutionResult,
    ActionSpec,
    is_private_host,
)
from nps_sdk.nwp.frames import ActionFrame, QueryFrame
from nps_sdk.nwp.memory_node_server import (
    MemoryNodeQueryResult,
    MemoryNodeSchema,
)

TRACE_HEADER = "X-NWP-Trace"
ABSOLUTE_MAX_DEPTH = 5

_KNOWN_ROUTES = frozenset({
    "/.nwm", "/.nwm/", "/.schema", "/.schema/", "/actions", "/actions/",
    "/query", "/query/", "/invoke", "/invoke/",
})


# ── Options ──────────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ComplexGraphRef:
    """Child node reference declared in the NWM (NPS-2 §11)."""

    rel: str
    node_url: str


@dataclasses.dataclass
class ComplexNodeOptions:
    """Configuration for a single Complex Node (port of .NET ``ComplexNodeOptions``)."""

    node_id: str
    path_prefix: str
    schema: MemoryNodeSchema | None = None
    actions: dict[str, ActionSpec] = dataclasses.field(default_factory=dict)
    graph: list[ComplexGraphRef] = dataclasses.field(default_factory=list)
    graph_max_depth: int = 2
    allowed_child_url_prefixes: list[str] = dataclasses.field(default_factory=list)
    reject_private_child_urls: bool = True
    allow_http_child_urls: bool = False
    display_name: str | None = None
    require_auth: bool = False
    default_limit: int = 20
    max_limit: int = 1000
    default_timeout_ms: int = 5_000
    max_timeout_ms: int = 300_000
    child_fetch_timeout_seconds: float = 10.0


# ── Child-URL SSRF/allowlist validator (NPS-2 §13.2) ─────────────────────────────

class ComplexChildUrlValidator:
    """Validates child-node URLs before dereference: https + allowlist + SSRF guard."""

    @staticmethod
    def validate(
        child_url: str,
        allowed_prefixes: list[str],
        reject_private: bool = True,
        allow_http: bool = False,
    ) -> str | None:
        if not child_url or not child_url.strip():
            return "child node URL must not be empty."
        parts = urlsplit(child_url)
        if not parts.scheme or not parts.hostname:
            return f"child node URL '{child_url}' is not a valid absolute URI."
        scheme = parts.scheme.lower()
        is_https = scheme == "https"
        is_http = scheme == "http"
        if not is_https and not (allow_http and is_http):
            return f"child node URL MUST use the https:// scheme (got '{parts.scheme}://')."
        if allowed_prefixes:
            if not any(child_url.lower().startswith(p.lower()) for p in allowed_prefixes):
                return f"child node URL '{child_url}' is not in the allowed prefix list."
        if reject_private and is_private_host(parts.hostname):
            return (f"child node host '{parts.hostname}' resolves to a private or "
                    "loopback address (SSRF guard).")
        return None


# ── Provider ─────────────────────────────────────────────────────────────────────

class IComplexNodeProvider(abc.ABC):
    """Local behaviour: mirrors the Memory + Action Node surfaces for /query + /invoke."""

    @abc.abstractmethod
    async def query(self, frame: QueryFrame, options: ComplexNodeOptions) -> MemoryNodeQueryResult: ...

    @abc.abstractmethod
    async def execute(self, frame: ActionFrame, context: ActionContext) -> ActionExecutionResult: ...


class NullComplexNodeProvider(IComplexNodeProvider):
    """Convenience provider for Complex Nodes that only aggregate child nodes."""

    async def query(self, frame, options) -> MemoryNodeQueryResult:
        return MemoryNodeQueryResult(rows=[])

    async def execute(self, frame, context) -> ActionExecutionResult:
        raise RuntimeError(
            "NullComplexNodeProvider does not handle actions. "
            "Register actions only if your Complex Node has a real provider.")


# ── Helpers ──────────────────────────────────────────────────────────────────────

def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


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


def _schema_anchor(schema: MemoryNodeSchema) -> tuple[bytes, str]:
    schema_json = _json_bytes(schema.to_dict())
    anchor_id = "sha256:" + hashlib.sha256(schema_json).hexdigest()
    return schema_json, anchor_id


# ── The ASGI app ──────────────────────────────────────────────────────────────────

class ComplexNodeApp:
    """Pure-ASGI Complex Node server."""

    def __init__(
        self,
        options: ComplexNodeOptions,
        provider: IComplexNodeProvider,
        *,
        http_client: Any | None = None,
    ) -> None:
        from nps_sdk.nwp.action_node_server import SYSTEM_TASK_CANCEL, SYSTEM_TASK_STATUS
        if SYSTEM_TASK_STATUS in options.actions or SYSTEM_TASK_CANCEL in options.actions:
            raise ValueError(
                f"Reserved action ids '{SYSTEM_TASK_STATUS}' / '{SYSTEM_TASK_CANCEL}' "
                "MUST NOT be registered on a Complex Node.")
        if options.graph_max_depth > ABSOLUTE_MAX_DEPTH:
            raise ValueError(
                f"graph_max_depth {options.graph_max_depth} exceeds NPS-2 §11 absolute cap "
                f"{ABSOLUTE_MAX_DEPTH}.")
        self._opt = options
        self._provider = provider
        self._http_client = http_client  # injectable for tests; else lazily created per fetch
        self._prefix = options.path_prefix.rstrip("/")

        if options.schema is not None:
            self._schema_json, self._anchor_id = _schema_anchor(options.schema)
        else:
            self._schema_json, self._anchor_id = b"{}", ""
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
                                   error_codes.ACTION_NOT_FOUND, "unknown complex sub-path.")
            return

        if self._opt.require_auth and http_headers.AGENT.lower() not in headers:
            await self._send_error(send, 401, "NPS-CLIENT-UNAUTHORIZED",
                                   error_codes.AUTH_NID_SCOPE_VIOLATION,
                                   "X-NWP-Agent header is required.")
            return

        if sub in ("/.nwm", "/.nwm/"):
            await self._send(send, 200, self._nwm_json, http_headers.MIME_MANIFEST,
                             {http_headers.NODE_TYPE: "complex"})
        elif sub in ("/.schema", "/.schema/"):
            extra = {http_headers.SCHEMA: self._anchor_id} if self._anchor_id else None
            await self._send(send, 200, self._schema_json, "application/json", extra)
        elif sub in ("/actions", "/actions/"):
            await self._send(send, 200, self._actions_json, "application/json")
        elif sub in ("/query", "/query/"):
            if method != "POST":
                await self._send(send, 405, b"", "application/json")
                return
            await self._handle_query(receive, send, headers)
        elif sub in ("/invoke", "/invoke/"):
            if method != "POST":
                await self._send(send, 405, b"", "application/json")
                return
            await self._handle_invoke(receive, send, headers)

    # ── /query ───────────────────────────────────────────────────────────────────

    async def _handle_query(self, receive: Callable, send: Callable, headers: dict[str, str]) -> None:
        try:
            frame = QueryFrame.from_dict(json.loads(await _read_body(receive) or b"{}"))
        except (json.JSONDecodeError, ValueError, KeyError) as ex:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.QUERY_FILTER_INVALID, str(ex))
            return

        ok, requested_depth, depth_err = self._parse_depth(headers)
        if not ok:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.DEPTH_EXCEEDED, depth_err)
            return

        trace = self._parse_trace(headers)
        if self._opt.node_id in trace:
            await self._send_error(send, 422, "NPS-CLIENT-UNPROCESSABLE",
                                   error_codes.GRAPH_CYCLE,
                                   f"graph cycle detected at '{self._opt.node_id}'.")
            return

        try:
            local = await self._provider.query(frame, self._opt)
        except Exception:  # noqa: BLE001
            await self._send_error(send, 500, "NPS-SERVER-INTERNAL",
                                   error_codes.NODE_UNAVAILABLE, "local query failed.")
            return

        graph_element: list[Any] | None = None
        if requested_depth > 0 and self._opt.graph:
            next_trace = [*trace, self._opt.node_id]
            child_depth = requested_depth - 1
            results = await asyncio.gather(*[
                self._fetch_child(gref, frame, child_depth, next_trace, headers)
                for gref in self._opt.graph
            ])
            graph_element = list(results)

        caps: dict[str, Any] = {
            "anchor_ref": self._anchor_id or None,
            "count": len(local.rows),
            "data": local.rows,
        }
        if local.next_cursor is not None:
            caps["next_cursor"] = local.next_cursor
        if graph_element is not None:
            caps["graph"] = graph_element

        extra: dict[str, Any] = {http_headers.NODE_TYPE: "complex"}
        if self._anchor_id:
            extra[http_headers.SCHEMA] = self._anchor_id
        await self._send(send, 200, _json_bytes(caps), http_headers.MIME_CAPSULE, extra)

    async def _fetch_child(
        self,
        gref: ComplexGraphRef,
        frame: QueryFrame,
        child_depth: int,
        next_trace: list[str],
        parent_headers: dict[str, str],
    ) -> dict[str, Any]:
        ssrf_err = ComplexChildUrlValidator.validate(
            gref.node_url, self._opt.allowed_child_url_prefixes,
            self._opt.reject_private_child_urls, self._opt.allow_http_child_urls)
        if ssrf_err is not None:
            return {"rel": gref.rel, "node": gref.node_url,
                    "error": {"code": error_codes.AUTH_NID_SCOPE_VIOLATION, "message": ssrf_err}}

        url = gref.node_url.rstrip("/") + "/query"
        req_headers = {
            "content-type": http_headers.MIME_FRAME,
            http_headers.DEPTH: str(child_depth),
            TRACE_HEADER: ",".join(next_trace),
        }
        agent = parent_headers.get(http_headers.AGENT.lower())
        if agent is not None:
            req_headers[http_headers.AGENT] = agent
        budget = parent_headers.get(http_headers.BUDGET.lower())
        if budget is not None:
            req_headers[http_headers.BUDGET] = budget

        client = self._http_client
        owns_client = client is None
        try:
            if owns_client:
                import httpx
                client = httpx.AsyncClient(timeout=self._opt.child_fetch_timeout_seconds)
            resp = await client.post(url, content=_json_bytes(frame.to_dict()),
                                     headers=req_headers,
                                     timeout=self._opt.child_fetch_timeout_seconds)
            body = resp.text
            if resp.status_code >= 400:
                return {"rel": gref.rel, "node": gref.node_url,
                        "error": {"code": error_codes.NODE_UNAVAILABLE,
                                  "message": f"child '{gref.rel}' returned {resp.status_code}: "
                                             f"{_truncate(body)}"}}
            try:
                capsule = json.loads(body)
            except json.JSONDecodeError as jx:
                return {"rel": gref.rel, "node": gref.node_url,
                        "error": {"code": error_codes.NODE_UNAVAILABLE,
                                  "message": f"child '{gref.rel}' returned non-JSON body: {jx}"}}
            return {"rel": gref.rel, "node": gref.node_url, "data": capsule}
        except Exception as ex:  # noqa: BLE001 — network/timeout failures degrade to an error entry
            return {"rel": gref.rel, "node": gref.node_url,
                    "error": {"code": error_codes.NODE_UNAVAILABLE, "message": str(ex)}}
        finally:
            if owns_client and client is not None:
                await client.aclose()

    # ── /invoke ──────────────────────────────────────────────────────────────────

    async def _handle_invoke(self, receive: Callable, send: Callable, headers: dict[str, str]) -> None:
        try:
            raw = json.loads(await _read_body(receive) or b"{}")
            frame = ActionFrame.from_dict(raw)
        except (json.JSONDecodeError, ValueError, KeyError) as ex:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID, str(ex))
            return

        priority = raw.get("priority")
        request_id = raw.get("request_id")

        spec = self._opt.actions.get(frame.action_id)
        if spec is None:
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND",
                                   error_codes.ACTION_NOT_FOUND,
                                   f"Unknown action_id '{frame.action_id}'.")
            return

        if frame.async_:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID,
                                   "Complex Node does not support async actions; "
                                   "invoke a downstream Action Node instead.")
            return

        if priority is not None and priority not in ("low", "normal", "high"):
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID,
                                   f"priority '{priority}' is invalid (allowed: low/normal/high).")
            return

        timeout_ms = self._clamp_timeout(frame.timeout_ms, spec)
        agent_nid = headers.get(http_headers.AGENT.lower())
        ctx = ActionContext(
            agent_nid=agent_nid, request_id=request_id, spec=spec,
            timeout_ms=timeout_ms, priority=priority or "normal", task_id=None,
        )
        try:
            result = await asyncio.wait_for(
                self._provider.execute(frame, ctx), timeout=timeout_ms / 1000.0)
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

        anchor_ref = result.anchor_ref or spec.result_anchor or ""
        data = [] if result.result is None else [result.result]
        caps: dict[str, Any] = {"anchor_ref": anchor_ref, "count": len(data), "data": data}
        if result.token_est > 0:
            caps["token_est"] = result.token_est
        extra: dict[str, Any] = {http_headers.NODE_TYPE: "complex"}
        if anchor_ref:
            extra[http_headers.SCHEMA] = anchor_ref
        if result.token_est > 0:
            extra[http_headers.TOKENS] = result.token_est
        if request_id is not None:
            extra[http_headers.REQUEST_ID] = request_id
        await self._send(send, 200, _json_bytes(caps), http_headers.MIME_CAPSULE, extra)

    # ── Helpers ──────────────────────────────────────────────────────────────────

    def _parse_depth(self, headers: dict[str, str]) -> tuple[bool, int, str]:
        raw = headers.get(http_headers.DEPTH.lower())
        if raw is None:
            return True, 0, ""
        try:
            depth = int(raw)
            if depth < 0:
                raise ValueError
        except ValueError:
            return False, 0, f"X-NWP-Depth '{raw}' is not a non-negative integer."
        if depth > self._opt.graph_max_depth:
            return False, 0, f"X-NWP-Depth {depth} exceeds node max_depth {self._opt.graph_max_depth}."
        if depth > ABSOLUTE_MAX_DEPTH:
            return False, 0, f"X-NWP-Depth {depth} exceeds NPS-2 §11 absolute cap {ABSOLUTE_MAX_DEPTH}."
        return True, depth, ""

    def _parse_trace(self, headers: dict[str, str]) -> list[str]:
        raw = headers.get(TRACE_HEADER.lower())
        if not raw:
            return []
        return [p.strip() for p in raw.split(",") if p.strip()]

    def _clamp_timeout(self, requested: int, spec: ActionSpec) -> int:
        spec_max = spec.timeout_ms_max or self._opt.max_timeout_ms
        hard_max = min(spec_max, self._opt.max_timeout_ms)
        if requested <= 0:
            return spec.timeout_ms_default or self._opt.default_timeout_ms
        return min(requested, hard_max)

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

    # ── Manifest ─────────────────────────────────────────────────────────────────

    def _build_manifest(self) -> dict[str, Any]:
        opt = self._opt
        base = self._prefix
        m: dict[str, Any] = {
            "nwp": "0.4",
            "node_id": opt.node_id,
            "node_type": "complex",
        }
        if opt.display_name is not None:
            m["display_name"] = opt.display_name
        m["wire_formats"] = ["ncp-capsule", "json"]
        m["preferred_format"] = "json"
        if self._anchor_id:
            m["schema_anchors"] = {"default": self._anchor_id}
        m["capabilities"] = {
            "query": opt.schema is not None or len(opt.graph) > 0,
            "stream": False,
            "token_budget_hint": True,
        }
        m["auth"] = {
            "required": opt.require_auth,
            "identity_type": "nip-cert" if opt.require_auth else "none",
        }
        endpoints: dict[str, Any] = {"query": f"{base}/query", "schema": f"{base}/.schema"}
        if opt.actions:
            endpoints["invoke"] = f"{base}/invoke"
        m["endpoints"] = endpoints
        if opt.graph:
            m["graph"] = {
                "refs": [{"rel": g.rel, "node_url": g.node_url} for g in opt.graph],
                "max_depth": opt.graph_max_depth,
            }
        return m


def _truncate(s: str) -> str:
    return s if len(s) <= 256 else s[:256] + "…"
