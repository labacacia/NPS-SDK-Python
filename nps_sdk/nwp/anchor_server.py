# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS NWP — Anchor Node server (pure ASGI, zero third-party deps).

Server-side counterpart of :mod:`nps_sdk.nwp.anchor_client`, porting the .NET
``NPS.NWP.Anchor.AnchorNodeMiddleware`` wire contract (NPS-AaaS §2, NPS-2 §12).
The app is a plain ASGI 3.0 callable — run it under any ASGI server (uvicorn,
hypercorn) or drive it in-process with ``httpx.ASGITransport``.

Endpoints (under ``options.path_prefix``):

  GET  /.nwm        — Neural Web Manifest (splices cgn_limit / reputation_policy / trust_anchors)
  GET  /.schema, /actions — declared action specs
  POST /invoke      — ActionFrame → gates (auth / rate-limit / reputation / CGN) → handler
  POST /query       — reserved type ``topology.snapshot`` → CapsFrame
  POST /subscribe   — reserved type ``topology.stream`` → NDJSON event stream

The actual business execution behind ``/invoke`` is delegated to an injected
:class:`AnchorInvokeHandler` — mirroring how the .NET middleware requires an
``IAnchorRouter`` + ``INopOrchestrator`` from DI (NOP orchestration is host-supplied).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any, Protocol

from nps_sdk.nip.assurance_level import AssuranceLevel
from nps_sdk.nwp import error_codes, http_headers
from nps_sdk.nwp.anchor_client import (
    AnchorState,
    MemberInfo,
    MemberJoined,
    MemberLeft,
    MemberUpdated,
    ResyncRequired,
    TopologyEvent,
    TopologyFilter,
    TopologySnapshot,
)
from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.reputation import (
    IReputationEvaluator,
    RepOutcome,
    ReputationPolicy,
)


# ── Wire constants (NPS-2 §12) ─────────────────────────────────────────────────

# Anchor sub-paths the node serves; an unknown sub-path is a 404, not a 401.
_KNOWN_ROUTES = frozenset({
    "/.nwm", "/.nwm/", "/.schema", "/.schema/", "/actions", "/actions/",
    "/invoke", "/invoke/", "/query", "/query/", "/subscribe", "/subscribe/",
})


class TopologyWire:
    TYPE_SNAPSHOT = "topology.snapshot"
    TYPE_STREAM = "topology.stream"
    SCOPE_CLUSTER = "cluster"
    SCOPE_MEMBER = "member"
    SNAPSHOT_ANCHOR_REF = "nps:system:topology:snapshot"
    INCLUDE_MEMBERS = "members"
    INCLUDE_CAPABILITIES = "capabilities"
    INCLUDE_TAGS = "tags"
    INCLUDE_METRICS = "metrics"
    EVENT_MEMBER_JOINED = "member_joined"
    EVENT_MEMBER_LEFT = "member_left"
    EVENT_MEMBER_UPDATED = "member_updated"
    EVENT_ANCHOR_STATE = "anchor_state"
    EVENT_RESYNC_REQUIRED = "resync_required"


# ── Errors ─────────────────────────────────────────────────────────────────────

class TopologyProtocolError(Exception):
    """Protocol-level error raised while handling a topology request."""

    def __init__(self, nwp_error_code: str, nps_status: str, message: str) -> None:
        super().__init__(message)
        self.nwp_error_code = nwp_error_code
        self.nps_status = nps_status
        self.message = message


class AnchorRole:
    """Ownership role of an Anchor within its cluster (NPS-CR-0009 §3.2)."""

    ACTIVE = "active"
    STANDBY = "standby"


class AnchorEpochGuard:
    """Epoch fence + leader check for a multi-Anchor cluster (NPS-CR-0009 §3.2).

    Intra-cluster consensus (Raft/Paxos/a lease store) is explicitly out of scope and
    implementation-defined; only the observable wire contract below is normative.

    Two deliberately asymmetric rules:

    * **Epoch fence** — an inbound frame carrying a *strictly greater* ``cluster_epoch``
      means this Anchor is a superseded leader. It self-fences: demotes to standby, emits
      a terminal ``anchor_failover`` and raises ``NWP-ANCHOR-EPOCH-FENCED``. An equal or
      lower inbound epoch is **not** an error.
    * **Leader check** — a topology *write* is rejected with ``NWP-ANCHOR-NOT-LEADER``
      when this Anchor is a standby, or is the active owner but quorum-lost (read-only
      degraded). Reads are always allowed; a standby MAY serve stale reads stamped with
      its last-known epoch.

    Both errors map to ``NPS-CLIENT-CONFLICT`` (HTTP 409).
    """

    def __init__(
        self,
        anchor_nid: str,
        own_epoch: int = 1,
        role: str = AnchorRole.ACTIVE,
        on_event: "Callable[[TopologyEvent], None] | None" = None,
        close_streams: "Callable[[], None] | None" = None,
    ) -> None:
        if own_epoch < 1:
            raise ValueError("own_epoch must be >= 1")
        self.anchor_nid = anchor_nid
        self.own_epoch = int(own_epoch)
        self.role = role
        self.degraded = False
        self.fenced = False
        #: Every event this guard has emitted, newest last — the wire feed for subscribers.
        self.events: list[TopologyEvent] = []
        self._on_event = on_event
        self._close_streams = close_streams

    # ── (a) epoch fence + (b) leader check ────────────────────────────────────

    def check_inbound(
        self,
        cluster_epoch: int | None,
        *,
        is_topology_write: bool = False,
        sender_anchor_nid: str | None = None,
    ) -> None:
        """Apply the fence to any inbound frame, then the leader check to writes.

        :param cluster_epoch: the inbound frame's ``cluster_epoch``; ``None`` means 1.
        :raises TopologyProtocolError: ``NWP-ANCHOR-EPOCH-FENCED`` or
            ``NWP-ANCHOR-NOT-LEADER``.
        """
        inbound = 1 if cluster_epoch is None else int(cluster_epoch)

        # (a) EPOCH FENCE — first, and for ANY inbound frame, read or write.
        if inbound > self.own_epoch:
            self.role = AnchorRole.STANDBY
            self.fenced = True
            self._emit(
                AnchorState.failover(
                    successor_nid=sender_anchor_nid or "",
                    cluster_epoch=inbound,
                    reason=AnchorState.REASON_ACTIVE_LOST,
                )
            )
            if self._close_streams is not None:
                self._close_streams()
            raise TopologyProtocolError(
                error_codes.ANCHOR_EPOCH_FENCED,
                "NPS-CLIENT-CONFLICT",
                f"Inbound cluster_epoch {inbound} exceeds this Anchor's epoch "
                f"{self.own_epoch}; this Anchor is a superseded leader and is fenced.",
            )
        # inbound <= own_epoch is NOT an error.

        # (b) LEADER CHECK — writes only.
        if is_topology_write and (self.role != AnchorRole.ACTIVE or self.degraded):
            reason = (
                "the Anchor lost quorum and is read-only degraded"
                if self.degraded and self.role == AnchorRole.ACTIVE
                else "this Anchor is a standby"
            )
            raise TopologyProtocolError(
                error_codes.ANCHOR_NOT_LEADER,
                "NPS-CLIENT-CONFLICT",
                f"Topology writes are only accepted by the active cluster owner; {reason}.",
            )

        # (c) reads always proceed.

    # ── response stamping ─────────────────────────────────────────────────────

    def stamp(self, snapshot: TopologySnapshot) -> TopologySnapshot:
        """Stamp *snapshot* with this Anchor's current epoch (NPS-2 §12.2)."""
        snapshot.cluster_epoch = self.own_epoch
        return snapshot

    # ── state transitions ─────────────────────────────────────────────────────

    def on_quorum_lost(self, quorum_size: int, available: int) -> AnchorState:
        """Enter read-only-degraded and emit ``anchor_quorum_lost``.

        Callers SHOULD additionally set their NDP self-announcement ``health`` to
        ``"degraded"``.
        """
        self.degraded = True
        event = AnchorState.quorum_lost(quorum_size, available)
        self._emit(event)
        return event

    def on_quorum_restored(self) -> None:
        """Leave read-only-degraded once quorum returns."""
        self.degraded = False

    def on_take_ownership(
        self,
        new_epoch: int,
        reason: str = AnchorState.REASON_PLANNED,
    ) -> AnchorState:
        """Become the active owner at *new_epoch* and emit ``anchor_failover``.

        *new_epoch* MUST be strictly greater than every epoch observed so far — the
        epoch is a fencing token, so a re-used value would let a stale leader win.
        The caller MUST re-sign and re-publish its AnnounceFrame with the new epoch:
        ``cluster_epoch`` is inside the signed canonical form (NPS-CR-0009 §1.1).
        """
        if new_epoch <= self.own_epoch:
            raise ValueError(
                f"cluster_epoch must strictly increase: {new_epoch} <= {self.own_epoch}"
            )
        self.own_epoch = int(new_epoch)
        self.role = AnchorRole.ACTIVE
        self.degraded = False
        self.fenced = False
        event = AnchorState.failover(self.anchor_nid, self.own_epoch, reason)
        self._emit(event)
        return event

    def _emit(self, event: TopologyEvent) -> None:
        self.events.append(event)
        if self._on_event is not None:
            self._on_event(event)


class AnchorActionError(Exception):
    """Raised by an :class:`AnchorInvokeHandler` to return an NWP error envelope."""

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


# ── Topology request types (server-side) ───────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class AnchorSnapshotRequest:
    scope: str = TopologyWire.SCOPE_CLUSTER
    include: frozenset[str] = frozenset({TopologyWire.INCLUDE_MEMBERS})
    depth: int = 1
    target_nid: str | None = None


@dataclasses.dataclass(frozen=True)
class AnchorStreamRequest:
    scope: str = TopologyWire.SCOPE_CLUSTER
    filter: TopologyFilter | None = None
    since_version: int | None = None


# ── Topology service ───────────────────────────────────────────────────────────

class AnchorTopologyService(Protocol):
    """Supplies cluster topology for an Anchor Node's reserved query types."""

    anchor_nid: str

    async def get_snapshot(self, request: AnchorSnapshotRequest) -> TopologySnapshot: ...

    def subscribe(self, request: AnchorStreamRequest) -> AsyncIterator[TopologyEvent]: ...


class InMemoryAnchorTopologyService:
    """Reference in-memory topology service.

    ``members`` seeds the snapshot; ``events`` (optional) are replayed by
    :meth:`subscribe` in order, then the stream ends. Override :meth:`subscribe`
    for a live event source.
    """

    def __init__(
        self,
        anchor_nid: str,
        members: Iterable[MemberInfo] | None = None,
        version: int = 1,
        events: Iterable[TopologyEvent] | None = None,
    ) -> None:
        self.anchor_nid = anchor_nid
        self._members = list(members or [])
        self._version = version
        self._events = list(events or [])

    async def get_snapshot(self, request: AnchorSnapshotRequest) -> TopologySnapshot:
        members = (
            self._members
            if TopologyWire.INCLUDE_MEMBERS in request.include
            else []
        )
        return TopologySnapshot(
            version=self._version,
            anchor_nid=self.anchor_nid,
            cluster_size=len(self._members),
            members=members,
        )

    async def subscribe(
        self, request: AnchorStreamRequest
    ) -> AsyncIterator[TopologyEvent]:
        for ev in self._events:
            yield ev


# ── Options ────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class AnchorActionSpec:
    """A declared action advertised in the NWM (NPS-2 §4.6)."""

    description: str | None = None
    params_anchor: str | None = None
    result_anchor: str | None = None
    estimated_cgn: int | None = None
    timeout_ms_default: int | None = None
    timeout_ms_max: int | None = None
    required_capability: str | None = None
    async_: bool = False
    # NPS-CR-0009 §3.2(b): actions that mutate cluster topology are accepted only by the
    # active, non-degraded owner. Reads leave this False.
    topology_write: bool = False


@dataclasses.dataclass
class AnchorNodeOptions:
    """Configuration for a single Anchor Node (port of .NET ``AnchorNodeOptions``)."""

    node_id: str
    path_prefix: str
    actions: dict[str, AnchorActionSpec] = dataclasses.field(default_factory=dict)
    display_name: str | None = None
    require_auth: bool = True
    required_capabilities: list[str] | None = None
    require_topology_capability: bool = False
    default_timeout_ms: int = 30_000
    max_timeout_ms: int = 300_000
    default_token_budget: int = 0
    cgn_limit: int = 0
    assurance_hint_url: str | None = None
    reputation_policy: ReputationPolicy | None = None
    trust_anchors: list[str] | None = None
    rate_limits: dict[str, Any] | None = None
    auto_inject_trace_context: bool = True


# ── Invoke handler / rate limiter ──────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class InvokeContext:
    agent_nid: str | None
    effective_timeout_ms: int
    budget_cgn: int
    priority: str = "normal"
    trace_id: str | None = None
    span_id: str | None = None


# An invoke handler returns the JSON-serializable result payload, or raises AnchorActionError.
AnchorInvokeHandler = Callable[[str, ActionFrame, InvokeContext], Awaitable[Any]]


@dataclasses.dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_seconds: int | None = None
    reason: str | None = None


class AnchorRateLimiter(Protocol):
    def try_acquire(self, consumer_key: str, cost_hint: int) -> RateDecision: ...

    def release(self, consumer_key: str) -> None: ...


class AllowAllRateLimiter:
    """Default no-op limiter; swap for a real implementation in production."""

    def try_acquire(self, consumer_key: str, cost_hint: int) -> RateDecision:
        return RateDecision(allowed=True)

    def release(self, consumer_key: str) -> None:
        return None


# ── Serialization helpers ──────────────────────────────────────────────────────

def _member_to_dict(m: MemberInfo) -> dict[str, Any]:
    d: dict[str, Any] = {
        "nid": m.nid,
        "node_roles": list(m.node_roles),
        "activation_mode": m.activation_mode,
    }
    if m.child_anchor is not None:
        d["child_anchor"] = m.child_anchor
    if m.member_count is not None:
        d["member_count"] = m.member_count
    if m.tags is not None:
        d["tags"] = m.tags
    if m.joined_at is not None:
        d["joined_at"] = m.joined_at
    if m.last_seen is not None:
        d["last_seen"] = m.last_seen
    if m.capabilities is not None:
        d["capabilities"] = m.capabilities
    if m.metrics is not None:
        d["metrics"] = m.metrics
    return d


def _snapshot_to_dict(s: TopologySnapshot) -> dict[str, Any]:
    d: dict[str, Any] = {
        "version": s.version,
        "anchor_nid": s.anchor_nid,
        "cluster_size": s.cluster_size,
        "members": [_member_to_dict(m) for m in s.members],
    }
    if s.truncated is not None:
        d["truncated"] = s.truncated
    if s.cluster_epoch is not None:
        d["cluster_epoch"] = s.cluster_epoch
    return d


def _changes_to_dict(c: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fld in (
        "nid", "node_roles", "activation_mode", "child_anchor",
        "member_count", "tags", "joined_at", "last_seen", "capabilities", "metrics",
    ):
        v = getattr(c, fld, None)
        if v is not None:
            out[fld] = v
    return out


def _event_payload(ev: TopologyEvent) -> tuple[str, int | None, dict[str, Any]]:
    """Return ``(event_type, seq, payload)`` for a topology event."""
    if isinstance(ev, MemberJoined):
        return TopologyWire.EVENT_MEMBER_JOINED, ev.version, _member_to_dict(ev.member)
    if isinstance(ev, MemberLeft):
        return TopologyWire.EVENT_MEMBER_LEFT, ev.version, {"nid": ev.nid}
    if isinstance(ev, MemberUpdated):
        return (
            TopologyWire.EVENT_MEMBER_UPDATED,
            ev.version,
            {"nid": ev.nid, "changes": _changes_to_dict(ev.changes)},
        )
    if isinstance(ev, AnchorState):
        return TopologyWire.EVENT_ANCHOR_STATE, ev.version, {"field": ev.field, "details": ev.details}
    if isinstance(ev, ResyncRequired):
        return TopologyWire.EVENT_RESYNC_REQUIRED, None, {"reason": ev.reason}
    raise ValueError(f"unknown TopologyEvent subtype: {type(ev).__name__}")


def _event_to_envelope(stream_id: str, ev: TopologyEvent) -> dict[str, Any]:
    event_type, seq, payload = _event_payload(ev)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    cgn_est = max(1, len(raw.encode("utf-8")) // 4)  # token-budget §7.2 UTF-8/4
    env: dict[str, Any] = {
        "stream_id": stream_id,
        "event_type": event_type,
        "timestamp": _iso_now(),
        "payload": payload,
        "cgn_est": cgn_est,
    }
    if seq is not None:
        env["seq"] = seq
    return env


def _iso_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _topology_http_status(nps_status: str) -> int:
    """HTTP status for a :class:`TopologyProtocolError`'s NPS status."""
    if nps_status == "NPS-AUTH-FORBIDDEN":
        return 403
    if nps_status == "NPS-CLIENT-CONFLICT":   # NWP-ANCHOR-NOT-LEADER / -EPOCH-FENCED
        return 409
    return 400


# ── The ASGI app ───────────────────────────────────────────────────────────────

class AnchorNodeApp:
    """Pure-ASGI Anchor Node server."""

    def __init__(
        self,
        options: AnchorNodeOptions,
        *,
        invoke_handler: AnchorInvokeHandler | None = None,
        topology_service: AnchorTopologyService | None = None,
        reputation_evaluator: IReputationEvaluator | None = None,
        rate_limiter: AnchorRateLimiter | None = None,
        epoch_guard: AnchorEpochGuard | None = None,
    ) -> None:
        self._opt = options
        self._handler = invoke_handler
        self._topology = topology_service
        self._evaluator = reputation_evaluator
        self._limiter = rate_limiter or AllowAllRateLimiter()
        # NPS-CR-0009 §3.2. When absent the node behaves exactly as before the CR:
        # a single-Anchor cluster that never fences and never stamps an epoch.
        self._epoch = epoch_guard
        self._prefix = options.path_prefix.rstrip("/")
        self._nwm_json = _json_bytes(self._build_manifest())
        self._actions_json = _json_bytes({"actions": self._actions_dict()})

    # ── ASGI entrypoint ────────────────────────────────────────────────────────

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

        # Resolve the route BEFORE the auth gate: an unknown sub-path is a 404 regardless of auth,
        # so a missing X-NWP-Agent on a non-existent route does not leak a 401 (auth state) for a
        # path that has no resource. Known routes fall through to the auth check below.
        if sub not in _KNOWN_ROUTES:
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND",
                                   error_codes.ACTION_NOT_FOUND, "unknown anchor sub-path.")
            return

        # Auth gate (applies to known routes, mirroring .NET).
        if self._opt.require_auth and http_headers.AGENT.lower() not in headers:
            await self._send_error(send, 401, "NPS-AUTH-UNAUTHENTICATED",
                                   error_codes.AUTH_NID_SCOPE_VIOLATION,
                                   "X-NWP-Agent header is required.")
            return

        if sub in ("/.nwm", "/.nwm/"):
            await self._send(send, 200, self._nwm_json, http_headers.MIME_MANIFEST,
                             {http_headers.NODE_TYPE: "anchor"})
        elif sub in ("/.schema", "/.schema/", "/actions", "/actions/"):
            await self._send(send, 200, self._actions_json, "application/json")
        elif sub in ("/invoke", "/invoke/"):
            if method != "POST":
                await self._send(send, 405, b"", "application/json")
                return
            await self._handle_invoke(receive, send, headers)
        elif sub in ("/query", "/query/"):
            if method != "POST":
                await self._send(send, 405, b"", "application/json")
                return
            await self._handle_query(receive, send, headers)
        elif sub in ("/subscribe", "/subscribe/"):
            if method != "POST":
                await self._send(send, 405, b"", "application/json")
                return
            await self._handle_subscribe(receive, send, headers)
        else:
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND",
                                   error_codes.ACTION_NOT_FOUND, "unknown anchor sub-path.")

    # ── /query (topology.snapshot) ──────────────────────────────────────────────

    async def _handle_query(self, receive: Callable, send: Callable, headers: dict[str, str]) -> None:
        if not await self._check_topology_capability(headers, send):
            return
        try:
            body = json.loads(await _read_body(receive) or b"{}")
        except (json.JSONDecodeError, ValueError) as ex:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.QUERY_FILTER_INVALID, str(ex))
            return

        if body.get("type") != TopologyWire.TYPE_SNAPSHOT:
            t = body.get("type")
            await self._send_error(send, 501, "NPS-SERVER-UNSUPPORTED",
                                   error_codes.RESERVED_TYPE_UNSUPPORTED,
                                   "Anchor /query requires a reserved type per NPS-2 §12."
                                   if t is None else
                                   f"Reserved query type '{t}' is not implemented by this Anchor Node.")
            return

        if self._topology is None:
            await self._send_error(send, 501, "NPS-SERVER-UNSUPPORTED",
                                   error_codes.NODE_UNAVAILABLE,
                                   "topology.snapshot is not available — no topology service registered.")
            return

        try:
            # NPS-CR-0009 §3.2(a): the fence applies to any inbound frame, read included.
            self._fence(body)
            req = _parse_snapshot_request(body)
            snapshot = await self._topology.get_snapshot(req)
        except TopologyProtocolError as ex:
            await self._send_error(send, _topology_http_status(ex.nps_status),
                                   ex.nps_status, ex.nwp_error_code, ex.message)
            return

        # NPS-2 §12.2: every topology.snapshot response carries the current cluster_epoch.
        if self._epoch is not None:
            snapshot = self._epoch.stamp(snapshot)

        caps = {
            "anchor_ref": TopologyWire.SNAPSHOT_ANCHOR_REF,
            "count": 1,
            "data": [_snapshot_to_dict(snapshot)],
        }
        await self._send(send, 200, _json_bytes(caps), http_headers.MIME_CAPSULE, {
            http_headers.NODE_TYPE: "anchor",
            http_headers.SCHEMA: TopologyWire.SNAPSHOT_ANCHOR_REF,
        })

    # ── /subscribe (topology.stream) ────────────────────────────────────────────

    async def _handle_subscribe(self, receive: Callable, send: Callable, headers: dict[str, str]) -> None:
        if not await self._check_topology_capability(headers, send):
            return
        try:
            body = json.loads(await _read_body(receive) or b"{}")
        except (json.JSONDecodeError, ValueError) as ex:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.QUERY_FILTER_INVALID, str(ex))
            return

        if body.get("type") != TopologyWire.TYPE_STREAM:
            t = body.get("type")
            await self._send_error(send, 501, "NPS-SERVER-UNSUPPORTED",
                                   error_codes.RESERVED_TYPE_UNSUPPORTED,
                                   "Anchor /subscribe requires a reserved type per NPS-2 §12."
                                   if t is None else
                                   f"Reserved subscribe type '{t}' is not implemented by this Anchor Node.")
            return

        if self._topology is None:
            await self._send_error(send, 501, "NPS-SERVER-UNSUPPORTED",
                                   error_codes.NODE_UNAVAILABLE,
                                   "topology.stream is not available — no topology service registered.")
            return

        try:
            self._fence(body)
            req, stream_id = _parse_stream_request(body)
        except TopologyProtocolError as ex:
            await self._send_error(send, _topology_http_status(ex.nps_status),
                                   ex.nps_status, ex.nwp_error_code, ex.message)
            return

        # Start the NDJSON stream.
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", http_headers.MIME_CAPSULE.encode("latin-1")),
                (http_headers.NODE_TYPE.lower().encode("latin-1"), b"anchor"),
            ],
        })

        # First line: subscription ack.
        ack = {
            "kind": "ack",
            "stream_id": stream_id,
            "status": "subscribed",
            "last_seq": 0,
            "resumed": req.since_version is not None,
        }
        # NPS-2 §12.2: the stream response carries the current cluster_epoch too.
        if self._epoch is not None:
            ack["cluster_epoch"] = self._epoch.own_epoch
        await self._write_line(send, ack)

        try:
            async for ev in self._topology.subscribe(req):
                await self._write_line(send, _event_to_envelope(stream_id, ev))
                if isinstance(ev, ResyncRequired):
                    break
        except TopologyProtocolError as ex:
            await self._write_line(send, {
                "status": ex.nps_status, "error": ex.nwp_error_code, "message": ex.message,
            })
        finally:
            await send({"type": "http.response.body", "body": b"", "more_body": False})

    # ── /invoke ──────────────────────────────────────────────────────────────────

    async def _handle_invoke(self, receive: Callable, send: Callable, headers: dict[str, str]) -> None:
        try:
            raw = json.loads(await _read_body(receive) or b"{}")
            frame = ActionFrame.from_dict(raw)
        except (json.JSONDecodeError, ValueError, KeyError) as ex:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID, str(ex))
            return

        spec = self._opt.actions.get(frame.action_id)
        if spec is None:
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND",
                                   error_codes.ACTION_NOT_FOUND,
                                   f"Unknown action_id '{frame.action_id}'.")
            return

        # NPS-CR-0009 §3.2: fence any inbound frame, and reject topology writes that
        # reach a standby or a quorum-lost owner.
        try:
            self._fence(raw, is_topology_write=spec.topology_write)
        except TopologyProtocolError as ex:
            await self._send_error(send, _topology_http_status(ex.nps_status),
                                   ex.nps_status, ex.nwp_error_code, ex.message)
            return

        if frame.async_ and not spec.async_:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST",
                                   error_codes.ACTION_PARAMS_INVALID,
                                   f"action '{frame.action_id}' does not support async execution.")
            return

        agent_nid = headers.get(http_headers.AGENT.lower())
        consumer_key = agent_nid or "anonymous"
        effective_timeout = self._clamp_timeout(frame.timeout_ms, spec)
        budget_cgn = self._read_effective_budget(headers)
        cgn_cost_hint = spec.estimated_cgn or 0

        # Rate limit (per-consumer).
        rate = self._limiter.try_acquire(consumer_key, cgn_cost_hint)
        if not rate.allowed:
            extra = {"Retry-After": rate.retry_after_seconds} if rate.retry_after_seconds else None
            await self._send_error(send, 429, "NPS-LIMIT-RATE",
                                   error_codes.BUDGET_EXCEEDED,
                                   rate.reason or "rate limit exceeded.", extra_headers=extra)
            return

        # Reputation policy gate (RFC-0005 §4.1.4).
        assurance = _extract_ident_assurance(headers)
        policy = self._opt.reputation_policy
        if policy is not None and policy.enabled and self._evaluator is not None:
            try:
                decision = await self._evaluator.evaluate(consumer_key, assurance.wire, policy)
            except Exception:  # noqa: BLE001
                self._limiter.release(consumer_key)
                await self._send_error(send, 500, "NPS-SERVER-INTERNAL",
                                       error_codes.NODE_UNAVAILABLE, "reputation evaluation failed.")
                return

            handled = await self._apply_reputation(send, decision, policy, assurance)
            if handled:
                self._limiter.release(consumer_key)
                return

        # Pre-execution CGN-limit check (token-budget.md §7.2).
        if budget_cgn > 0 and cgn_cost_hint > 0 and cgn_cost_hint > budget_cgn:
            self._limiter.release(consumer_key)
            await self._send_error(send, 400, "NPS-CLIENT-REQUEST-TOO-LARGE",
                                   error_codes.CGN_LIMIT_EXCEEDED,
                                   f"estimated CGN {cgn_cost_hint} exceeds effective budget {budget_cgn}.",
                                   details={"effective_budget": budget_cgn, "estimated_cgn": cgn_cost_hint})
            return

        if self._handler is None:
            self._limiter.release(consumer_key)
            await self._send_error(send, 501, "NPS-SERVER-UNSUPPORTED",
                                   error_codes.NODE_UNAVAILABLE,
                                   "no invoke handler registered on this Anchor Node.")
            return

        trace_id = secrets.token_hex(16) if self._opt.auto_inject_trace_context else None
        span_id = secrets.token_hex(8) if self._opt.auto_inject_trace_context else None
        ctx = InvokeContext(
            agent_nid=agent_nid,
            effective_timeout_ms=effective_timeout,
            budget_cgn=budget_cgn,
            trace_id=trace_id,
            span_id=span_id,
        )

        try:
            if frame.async_:
                task_id = secrets.token_hex(16)
                asyncio.create_task(self._run_async(frame, ctx))
                body = {
                    "task_id": task_id,
                    "status": "pending",
                    "poll_url": f"{self._prefix}/invoke",
                }
                await self._send(send, 202, _json_bytes(body), "application/json",
                                 {http_headers.NODE_TYPE: "anchor"})
                return

            try:
                result = await asyncio.wait_for(
                    self._handler(frame.action_id, frame, ctx),
                    timeout=effective_timeout / 1000.0,
                )
            except asyncio.TimeoutError:
                await self._send_error(send, 504, "NPS-SERVER-TIMEOUT",
                                       error_codes.NODE_UNAVAILABLE, "anchor task timed out.")
                return
            except AnchorActionError as ex:
                await self._send_error(send, ex.http_status, ex.nps_status,
                                       ex.error_code, ex.message, details=ex.details)
                return

            caps = {
                "anchor_ref": spec.result_anchor or "",
                "count": 0 if result is None else 1,
                "data": [] if result is None else [result],
            }
            extra = {http_headers.NODE_TYPE: "anchor"}
            if spec.result_anchor:
                extra[http_headers.SCHEMA] = spec.result_anchor
            if spec.estimated_cgn:
                extra[http_headers.TOKENS] = spec.estimated_cgn
            await self._send(send, 200, _json_bytes(caps), http_headers.MIME_CAPSULE, extra)
        finally:
            self._limiter.release(consumer_key)

    async def _run_async(self, frame: ActionFrame, ctx: InvokeContext) -> None:
        assert self._handler is not None
        try:
            await self._handler(frame.action_id, frame, ctx)
        except Exception:  # noqa: BLE001 — fire-and-forget
            pass

    async def _apply_reputation(
        self, send: Callable, decision: Any, policy: ReputationPolicy, assurance: AssuranceLevel
    ) -> bool:
        """Write a response for a non-Accept decision; return True if handled."""
        # The reference evaluator (nwp.reputation.ReputationDecision) carries the matched rule
        # rather than separate incident/severity fields; derive them from matched_rule.
        rule = decision.matched_rule
        incident = rule.incident if rule is not None else None
        severity = rule.severity if rule is not None else None
        outcome = decision.outcome
        if outcome is RepOutcome.ACCEPT:
            return False  # caller proceeds (X-NWP-Reputation-Status set on the success response is optional)

        if outcome is RepOutcome.BAN:
            details = (
                {"matched_incident": incident, "matched_severity": severity}
                if incident is not None else None
            )
            await self._send_error(send, 403, "NPS-AUTH-FORBIDDEN", error_codes.REPUTATION_BANNED,
                                   f"Request rejected: {incident} ({severity}) — NID temporarily banned."
                                   if incident is not None else
                                   "Request rejected: NID temporarily banned.",
                                   details=details)
            return True

        if outcome is RepOutcome.REJECT:
            if decision.error_code == error_codes.AUTH_ASSURANCE_TOO_LOW:
                await self._send_error(send, 403, "NPS-AUTH-FORBIDDEN",
                                       error_codes.AUTH_ASSURANCE_TOO_LOW,
                                       f"Assurance level too low: requires '{policy.min_assurance_level}', "
                                       f"caller declared '{assurance.wire}'.",
                                       details={"matched_incident": None, "hint": self._opt.assurance_hint_url})
            else:
                await self._send_error(send, 403, "NPS-AUTH-FORBIDDEN",
                                       error_codes.REPUTATION_REJECTED,
                                       f"Request rejected: {incident} ({severity})."
                                       if incident is not None else
                                       "Request rejected by reputation policy.",
                                       details={"matched_incident": incident,
                                                "matched_severity": severity}
                                       if incident is not None else None)
            return True

        if outcome is RepOutcome.THROTTLE:
            await self._send_error(send, 429, "NPS-CLIENT-RATE-LIMITED",
                                   error_codes.REPUTATION_THROTTLED,
                                   f"Request rate-limited: {incident} ({severity})."
                                   if incident is not None else
                                   "Request rate-limited by reputation policy.",
                                   details={"matched_incident": incident,
                                            "matched_severity": severity}
                                   if incident is not None else None,
                                   extra_headers={"Retry-After": 60})
            return True

        return False

    # ── Gates / helpers ──────────────────────────────────────────────────────────

    def _fence(self, body: dict[str, Any], *, is_topology_write: bool = False) -> None:
        """Run the NPS-CR-0009 §3.2 epoch fence / leader check over a decoded request body.

        The Python HTTP binding carries the sender's fencing token as a top-level
        ``cluster_epoch`` on the request body (and ``sender_anchor_nid`` alongside it).
        A body with no ``cluster_epoch`` reads as epoch 1 and never fences.
        """
        if self._epoch is None:
            return
        raw = body.get("cluster_epoch")
        epoch = int(raw) if isinstance(raw, int) else None
        sender = body.get("sender_anchor_nid")
        self._epoch.check_inbound(
            epoch,
            is_topology_write=is_topology_write,
            sender_anchor_nid=sender if isinstance(sender, str) else None,
        )

    async def _check_topology_capability(self, headers: dict[str, str], send: Callable) -> bool:
        if not self._opt.require_topology_capability:
            return True
        raw = headers.get(http_headers.CAPABILITIES.lower(), "")
        caps = {c.strip().lower() for c in raw.split(",") if c.strip()}
        if "topology:read" in caps:
            return True
        await self._send_error(
            send, 403, "NPS-AUTH-FORBIDDEN", error_codes.TOPOLOGY_UNAUTHORIZED,
            "Caller must declare 'topology:read' in X-NWP-Capabilities to access topology endpoints.",
        )
        return False

    def _clamp_timeout(self, requested: int, spec: AnchorActionSpec) -> int:
        spec_max = spec.timeout_ms_max or self._opt.max_timeout_ms
        hard_max = min(spec_max, self._opt.max_timeout_ms)
        if requested <= 0:
            return spec.timeout_ms_default or self._opt.default_timeout_ms
        return min(requested, hard_max)

    def _read_effective_budget(self, headers: dict[str, str]) -> int:
        raw = headers.get(http_headers.BUDGET.lower())
        try:
            agent_budget = int(raw) if raw is not None else self._opt.default_token_budget
        except ValueError:
            agent_budget = self._opt.default_token_budget
        cgn_limit = self._opt.cgn_limit
        if cgn_limit == 0:
            return agent_budget
        if agent_budget == 0:
            return cgn_limit
        return min(cgn_limit, agent_budget)

    # ── ASGI response helpers ────────────────────────────────────────────────────

    async def _send(
        self, send: Callable, status: int, body: bytes, content_type: str,
        extra_headers: dict[str, Any] | None = None,
    ) -> None:
        hdrs = [(b"content-type", content_type.encode("latin-1"))]
        for k, v in (extra_headers or {}).items():
            hdrs.append((k.lower().encode("latin-1"), str(v).encode("latin-1")))
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})

    async def _send_error(
        self, send: Callable, http_status: int, nps_status: str, error_code: str,
        message: str, details: Any | None = None, extra_headers: dict[str, Any] | None = None,
    ) -> None:
        env: dict[str, Any] = {"status": nps_status, "error": error_code, "message": message}
        if details is not None:
            env["details"] = details
        await self._send(send, http_status, _json_bytes(env), "application/json", extra_headers)

    async def _write_line(self, send: Callable, obj: Any) -> None:
        await send({
            "type": "http.response.body",
            "body": _json_bytes(obj) + b"\n",
            "more_body": True,
        })

    # ── Manifest construction ────────────────────────────────────────────────────

    def _actions_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for action_id, spec in self._opt.actions.items():
            entry: dict[str, Any] = {"action_id": action_id, "async": spec.async_}
            if spec.description is not None:
                entry["description"] = spec.description
            if spec.params_anchor is not None:
                entry["params_anchor"] = spec.params_anchor
            if spec.result_anchor is not None:
                entry["result_anchor"] = spec.result_anchor
            if spec.estimated_cgn is not None:
                entry["cgn_est"] = spec.estimated_cgn
            if spec.timeout_ms_default is not None:
                entry["timeout_ms_default"] = spec.timeout_ms_default
            if spec.timeout_ms_max is not None:
                entry["timeout_ms_max"] = spec.timeout_ms_max
            if spec.required_capability is not None:
                entry["required_capability"] = spec.required_capability
            out[action_id] = entry
        return out

    def _build_manifest(self) -> dict[str, Any]:
        opt = self._opt
        base = self._prefix
        m: dict[str, Any] = {
            "nwp": "0.4",
            "node_id": opt.node_id,
            "node_type": "anchor",
        }
        if opt.display_name is not None:
            m["display_name"] = opt.display_name
        m["wire_formats"] = ["ncp-capsule", "json"]
        m["preferred_format"] = "json"
        m["capabilities"] = {
            "query": False,
            "stream": False,
            "subscribe": False,
            "vector_search": False,
            "token_budget_hint": True,
            "ext_frame": False,
        }
        auth: dict[str, Any] = {
            "required": opt.require_auth,
            "identity_type": "nip-cert" if opt.require_auth else "none",
        }
        if opt.required_capabilities is not None:
            auth["required_capabilities"] = opt.required_capabilities
        m["auth"] = auth
        m["endpoints"] = {"invoke": f"{base}/invoke", "schema": f"{base}/.schema"}
        if opt.cgn_limit > 0:
            m["token_budget"] = {"cgn_limit": opt.cgn_limit, "profile": "cgn.v1"}
        if opt.rate_limits is not None:
            m["rate_limits"] = opt.rate_limits
        if opt.reputation_policy is not None and opt.reputation_policy.enabled:
            m["reputation_policy"] = dataclasses.asdict(opt.reputation_policy)
        if opt.trust_anchors:
            m["trust_anchors"] = opt.trust_anchors
        m["actions"] = list(self._actions_dict().values())
        return m


# ── Request parsing ────────────────────────────────────────────────────────────

def _parse_snapshot_request(body: dict[str, Any]) -> AnchorSnapshotRequest:
    topo = body.get("topology")
    if not isinstance(topo, dict):
        raise TopologyProtocolError(error_codes.TOPOLOGY_UNSUPPORTED_SCOPE, "NPS-CLIENT-BAD-PARAM",
                                    "topology.snapshot requires a 'topology' object per NPS-2 §12.1.")
    scope = _parse_scope(topo)
    include = _parse_include(topo)
    depth = _parse_depth(topo)
    target_nid = topo.get("target_nid") if isinstance(topo.get("target_nid"), str) else None
    if scope == TopologyWire.SCOPE_MEMBER and not target_nid:
        raise TopologyProtocolError(error_codes.TOPOLOGY_UNSUPPORTED_SCOPE, "NPS-CLIENT-BAD-PARAM",
                                    "topology.target_nid is required when topology.scope = \"member\".")
    return AnchorSnapshotRequest(scope=scope, include=include, depth=depth, target_nid=target_nid)


def _parse_stream_request(body: dict[str, Any]) -> tuple[AnchorStreamRequest, str]:
    topo = body.get("topology")
    if not isinstance(topo, dict):
        raise TopologyProtocolError(error_codes.TOPOLOGY_UNSUPPORTED_SCOPE, "NPS-CLIENT-BAD-PARAM",
                                    "topology.stream requires a 'topology' object per NPS-2 §12.2.")
    scope = _parse_scope(topo)
    filt: TopologyFilter | None = None
    f = topo.get("filter")
    if isinstance(f, dict):
        _validate_filter_keys(f)
        filt = TopologyFilter(
            tags_any=f.get("tags_any"), tags_all=f.get("tags_all"), node_roles=f.get("node_roles"),
        )
    since = topo.get("since_version")
    if since is None:
        since = body.get("resume_from_seq")
    since = int(since) if isinstance(since, int) else None
    stream_id = body.get("stream_id")
    if not isinstance(stream_id, str) or not stream_id:
        stream_id = secrets.token_hex(16)
    return AnchorStreamRequest(scope=scope, filter=filt, since_version=since), stream_id


def _parse_scope(topo: dict[str, Any]) -> str:
    s = topo.get("scope")
    if s is None:
        return TopologyWire.SCOPE_CLUSTER
    if s in (TopologyWire.SCOPE_CLUSTER, TopologyWire.SCOPE_MEMBER):
        return s
    raise TopologyProtocolError(error_codes.TOPOLOGY_UNSUPPORTED_SCOPE, "NPS-CLIENT-BAD-PARAM",
                                f"unknown topology.scope '{s}'.")


def _parse_include(topo: dict[str, Any]) -> frozenset[str]:
    inc = topo.get("include")
    if not isinstance(inc, list):
        return frozenset({TopologyWire.INCLUDE_MEMBERS})
    known = {TopologyWire.INCLUDE_MEMBERS, TopologyWire.INCLUDE_CAPABILITIES,
             TopologyWire.INCLUDE_TAGS, TopologyWire.INCLUDE_METRICS}
    flags = {v for v in inc if v in known}
    return frozenset(flags) if flags else frozenset({TopologyWire.INCLUDE_MEMBERS})


def _parse_depth(topo: dict[str, Any]) -> int:
    d = topo.get("depth")
    if isinstance(d, int) and d > 0:
        return d
    return 1


def _validate_filter_keys(filter_obj: dict[str, Any]) -> None:
    for key in filter_obj:
        if key in ("tags_any", "tags_all", "node_roles"):
            continue
        if key == "node_kind":
            raise TopologyProtocolError(error_codes.TOPOLOGY_FILTER_UNSUPPORTED, "NPS-CLIENT-BAD-PARAM",
                                        "topology.filter.node_kind expired after alpha.5; use node_roles.")
        raise TopologyProtocolError(error_codes.TOPOLOGY_FILTER_UNSUPPORTED, "NPS-CLIENT-BAD-PARAM",
                                    f"topology.filter key '{key}' is not recognized.")


def _extract_ident_assurance(headers: dict[str, str]) -> AssuranceLevel:
    raw = headers.get(http_headers.IDENT.lower())
    if not raw:
        return AssuranceLevel.ANONYMOUS
    try:
        doc = json.loads(raw)
        if isinstance(doc, dict):
            lvl = doc.get("assurance_level")
            if isinstance(lvl, str):
                return AssuranceLevel.from_wire(lvl)
    except (json.JSONDecodeError, ValueError):
        pass
    return AssuranceLevel.ANONYMOUS


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
