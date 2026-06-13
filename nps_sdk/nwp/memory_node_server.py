# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Framework-agnostic Memory Node server for NPS-2 §2.1, §4, §5.

The core logic lives in :class:`MemoryNodeServer` which operates on plain
dicts and bytes. Mount it via the built-in WSGI adapter
(:meth:`MemoryNodeServer.wsgi_app`) or integrate with any async framework
using :meth:`MemoryNodeServer.handle`.
"""

from __future__ import annotations

import abc
import asyncio
import dataclasses
import hashlib
import json
import math
import os
import secrets
from typing import Any

from nps_sdk.nwp.frames import QueryFrame
from nps_sdk.nwp.reputation import (
    IReputationEvaluator,
    ReputationPolicy,
    RepOutcome,
    default_reputation_evaluator,
)


# ── Schema types ──────────────────────────────────────────────────────────────

@dataclasses.dataclass
class MemoryNodeField:
    name:        str
    type:        str
    description: str = ""
    nullable:    bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type,
                "description": self.description, "nullable": self.nullable}


@dataclasses.dataclass
class MemoryNodeSchema:
    fields: list[MemoryNodeField]

    def to_dict(self) -> dict[str, Any]:
        return {"fields": [f.to_dict() for f in self.fields]}


# ── Options ───────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class MemoryNodeOptions:
    node_id:       str
    schema:        MemoryNodeSchema
    path_prefix:   str                    = ""
    display_name:  str | None             = None
    default_limit: int                    = 20
    max_limit:     int                    = 1000
    require_auth:  bool                   = False
    default_token_budget: int             = 0
    # Operator-side CGN cap (token-budget.md §7). 0 = unlimited.
    cgn_limit:     int                    = 0
    # RFC-0005 reputation gate. None = disabled.
    reputation_policy: ReputationPolicy | None = None
    # NWP v0.13 alpha.11: trust anchors for federation (NID or DID strings).
    trust_anchors: tuple[str, ...] | None = None


# ── Provider interface ────────────────────────────────────────────────────────

MemoryNodeRow = dict[str, Any]


@dataclasses.dataclass
class MemoryNodeQueryResult:
    rows:        list[MemoryNodeRow]
    next_cursor: str | None = None


class IMemoryNodeProvider(abc.ABC):
    @abc.abstractmethod
    async def query(self, frame: QueryFrame, opts: MemoryNodeOptions) -> MemoryNodeQueryResult: ...


# ── Request / response ────────────────────────────────────────────────────────

@dataclasses.dataclass
class NodeRequest:
    method:  str
    sub_path: str
    headers: dict[str, str]
    body:    bytes


@dataclasses.dataclass
class NodeResponse:
    status:  int
    headers: dict[str, str]
    body:    bytes


# ── Server ────────────────────────────────────────────────────────────────────

class MemoryNodeServer:
    def __init__(
        self,
        provider: IMemoryNodeProvider,
        opts: MemoryNodeOptions,
        evaluator: IReputationEvaluator | None = None,
    ) -> None:
        self._provider  = provider
        self._opts      = opts
        self._prefix    = opts.path_prefix.rstrip("/")
        self._evaluator = evaluator

        schema_json = json.dumps(opts.schema.to_dict(), separators=(",", ":")).encode()
        self._anchor_id  = "sha256:" + hashlib.sha256(schema_json).hexdigest()
        self._schema_json = schema_json
        self._nwm_json   = self._build_nwm()

    # ── Public API ────────────────────────────────────────────────────────────

    async def handle(self, req: NodeRequest) -> NodeResponse:
        """Process a request relative to this node's path prefix."""

        # Auth
        if self._opts.require_auth and not req.headers.get("x-nwp-agent"):
            return _err(401, "NPS-CLIENT-UNAUTHORIZED", "NWP-AUTH-REQUIRED",
                        "X-NWP-Agent header is required.")

        # Reputation gate (RFC-0005 §4.1.4)
        if self._opts.reputation_policy is not None:
            agent_nid = req.headers.get("x-nwp-agent")
            if agent_nid:
                ev = self._evaluator or default_reputation_evaluator()
                decision = await ev.evaluate(agent_nid, "anonymous",
                                             self._opts.reputation_policy)
                if decision.outcome in (RepOutcome.BAN, RepOutcome.REJECT):
                    return _err(403, "NPS-AUTH-FORBIDDEN", "NWP-AUTH-REPUTATION-BLOCKED",
                                "Request rejected by reputation policy.")
                if decision.outcome == RepOutcome.THROTTLE:
                    resp = _err(429, "NPS-LIMIT-RATE", "NWP-AUTH-REPUTATION-BLOCKED",
                                "Request throttled by reputation policy.")
                    resp.headers["Retry-After"] = "60"
                    return resp

        norm = req.sub_path.rstrip("/") or "/"

        if norm == "/.nwm" and req.method == "GET":
            return NodeResponse(200,
                {"Content-Type": "application/json; charset=utf-8",
                 "X-NWP-Node-Type": "memory"},
                self._nwm_json)

        if norm == "/.schema" and req.method == "GET":
            return NodeResponse(200,
                {"Content-Type": "application/json; charset=utf-8",
                 "X-NWP-Schema": self._anchor_id},
                self._schema_json)

        if norm == "/query":
            if req.method != "POST":
                return NodeResponse(405, {}, b"")
            return await self._handle_query(req)

        if norm == "/stream":
            if req.method != "POST":
                return NodeResponse(405, {}, b"")
            return await self._handle_stream(req)

        return NodeResponse(404, {}, b"")

    def wsgi_app(self):
        """Return a WSGI callable that mounts this node at its path_prefix."""
        server = self

        def app(environ, start_response):
            path = environ.get("PATH_INFO", "/")
            if not path.startswith(server._prefix):
                start_response("404 Not Found", [])
                return [b""]
            sub = path[len(server._prefix):] or "/"

            headers: dict[str, str] = {}
            for key, val in environ.items():
                if key.startswith("HTTP_"):
                    h = key[5:].lower().replace("_", "-")
                    headers[h] = val
            body = environ["wsgi.input"].read()
            req = NodeRequest(
                method   = environ.get("REQUEST_METHOD", "GET").upper(),
                sub_path = sub,
                headers  = headers,
                body     = body,
            )
            resp = asyncio.get_event_loop().run_until_complete(server.handle(req))
            status_text = {200: "OK", 201: "Created", 400: "Bad Request",
                           401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
                           405: "Method Not Allowed", 429: "Too Many Requests",
                           500: "Internal Server Error", 501: "Not Implemented",
                           503: "Service Unavailable"}.get(resp.status, "Unknown")
            start_response(f"{resp.status} {status_text}",
                           [(k, v) for k, v in resp.headers.items()])
            return [resp.body]

        return app

    # ── Internal handlers ─────────────────────────────────────────────────────

    async def _handle_query(self, req: NodeRequest) -> NodeResponse:
        try:
            raw = json.loads(req.body)
            frame = QueryFrame.from_dict(raw)
        except Exception as exc:
            return _err(400, "NPS-CLIENT-BAD-REQUEST", "NWP-QUERY-FILTER-INVALID", str(exc))

        # Clamp limit
        limit = min(frame.limit, self._opts.max_limit)
        if limit != frame.limit:
            frame = dataclasses.replace(frame, limit=limit)

        try:
            result = await self._provider.query(frame, self._opts)
        except Exception as exc:
            return _err(500, "NPS-SERVER-INTERNAL", "NWP-NODE-UNAVAILABLE", str(exc))

        rows    = result.rows
        tok_est = _measure_rows(rows)
        budget  = _effective_budget(
            _parse_budget(req.headers.get("x-nwp-budget", "")),
            self._opts.cgn_limit,
        )
        if budget > 0 and tok_est > budget:
            rows, tok_est = _trim_to_budget(rows, budget)

        caps = {
            "frame":       "0x04",
            "anchor_ref":  self._anchor_id,
            "count":       len(rows),
            "data":        rows,
            "next_cursor": result.next_cursor,
            "token_est":   tok_est,
        }
        return NodeResponse(200,
            {"Content-Type":    "application/json; charset=utf-8",
             "X-NWP-Schema":    self._anchor_id,
             "X-NWP-Tokens":    str(tok_est),
             "X-NWP-Node-Type": "memory"},
            json.dumps(caps, separators=(",", ":")).encode())

    async def _handle_stream(self, req: NodeRequest) -> NodeResponse:
        try:
            raw   = json.loads(req.body)
            frame = QueryFrame.from_dict(raw)
        except Exception as exc:
            return _err(400, "NPS-CLIENT-BAD-REQUEST", "NWP-QUERY-FILTER-INVALID", str(exc))

        try:
            result = await self._provider.query(frame, self._opts)
        except Exception as exc:
            return _err(500, "NPS-SERVER-INTERNAL", "NWP-NODE-UNAVAILABLE", str(exc))

        stream_id = secrets.token_hex(8)
        lines = [
            json.dumps({"frame": "0x03", "stream_id": stream_id, "seq": 0, "is_last": False,
                        "anchor_ref": self._anchor_id, "data": result.rows}, separators=(",", ":")),
            json.dumps({"frame": "0x03", "stream_id": stream_id, "seq": 1, "is_last": True,
                        "data": []}, separators=(",", ":")),
        ]
        return NodeResponse(200,
            {"Content-Type":    "application/x-ndjson",
             "X-NWP-Schema":    self._anchor_id,
             "X-NWP-Node-Type": "memory"},
            ("\n".join(lines) + "\n").encode())

    # ── NWM builder ───────────────────────────────────────────────────────────

    def _build_nwm(self) -> bytes:
        nwm: dict[str, Any] = {
            "nwp":              "0.4",
            "node_id":          self._opts.node_id,
            "node_type":        "memory",
            "display_name":     self._opts.display_name,
            "wire_formats":     ["json"],
            "preferred_format": "json",
            "schema_anchors":   {"default": self._anchor_id},
            "capabilities":     {"query": True, "stream": True, "token_budget_hint": True},
            "auth":             {"required": self._opts.require_auth,
                                 "identity_type": "nip-cert" if self._opts.require_auth else "none"},
            "endpoints": {
                "query":  f"{self._prefix}/query",
                "stream": f"{self._prefix}/stream",
                "schema": f"{self._prefix}/.schema",
            },
        }
        if self._opts.cgn_limit > 0:
            nwm["token_budget"] = {"cgn_limit": self._opts.cgn_limit, "profile": "cgn.v1"}
        return json.dumps(nwm, separators=(",", ":")).encode()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _err(status: int, nps_status: str, code: str, message: str) -> NodeResponse:
    body = json.dumps({"frame": "0xFE", "status": nps_status,
                       "error": code, "message": message},
                      separators=(",", ":")).encode()
    return NodeResponse(status, {"Content-Type": "application/json; charset=utf-8"}, body)

def _parse_budget(raw: str) -> int:
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0

def _effective_budget(agent_budget: int, cgn_limit: int) -> int:
    if cgn_limit <= 0:
        return agent_budget
    if agent_budget <= 0:
        return cgn_limit
    return min(cgn_limit, agent_budget)

def _measure_rows(rows: list[MemoryNodeRow]) -> int:
    return math.ceil(len(json.dumps(rows, separators=(",", ":"))) / 4)

def _trim_to_budget(rows: list[MemoryNodeRow], budget: int) -> tuple[list[MemoryNodeRow], int]:
    trimmed: list[MemoryNodeRow] = []
    acc = 0
    for row in rows:
        tok = math.ceil(len(json.dumps(row, separators=(",", ":"))) / 4)
        if acc + tok > budget:
            break
        trimmed.append(row)
        acc += tok
    return trimmed, acc
