# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
AnchorNodeClient — async client for an Anchor Node's reserved query types.

Communicates with the Anchor Node's /query and /subscribe endpoints using
the topology.snapshot and topology.stream wire types (NPS-2 §12).
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

# ── Wire constants ─────────────────────────────────────────────────────────────

_SCOPE_CLUSTER = "cluster"
_SCOPE_MEMBER  = "member"
_TYPE_SNAPSHOT = "topology.snapshot"
_TYPE_STREAM   = "topology.stream"
_EVENT_MEMBER_JOINED   = "member_joined"
_EVENT_MEMBER_LEFT     = "member_left"
_EVENT_MEMBER_UPDATED  = "member_updated"
_EVENT_ANCHOR_STATE    = "anchor_state"
_EVENT_RESYNC_REQUIRED = "resync_required"

# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class MemberInfo:
    nid:             str
    node_roles:      list[str]
    activation_mode: str
    child_anchor:    bool | None           = None
    member_count:    int | None            = None
    tags:            list[str] | None      = None
    joined_at:       str | None            = None
    last_seen:       str | None            = None
    capabilities:    Any | None            = None
    metrics:         Any | None            = None

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> "MemberInfo":
        return cls(
            nid             = d["nid"],
            node_roles      = d.get("node_roles") or [],
            activation_mode = d.get("activation_mode", ""),
            child_anchor    = d.get("child_anchor"),
            member_count    = d.get("member_count"),
            tags            = d.get("tags"),
            joined_at       = d.get("joined_at"),
            last_seen       = d.get("last_seen"),
            capabilities    = d.get("capabilities"),
            metrics         = d.get("metrics"),
        )


@dataclass
class TopologySnapshot:
    version:      int
    anchor_nid:   str
    cluster_size: int
    members:      list[MemberInfo]
    truncated:    bool | None = None
    # NPS-CR-0009 §1.4 / NPS-2 §12.2 — epoch under which the responding Anchor owns the
    # cluster. Wire key ``cluster_epoch``; absent means 1; not signed. Every
    # topology.snapshot / topology.stream response MUST carry the current value.
    cluster_epoch: int | None = None

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> "TopologySnapshot":
        epoch = d.get("cluster_epoch")
        return cls(
            version      = int(d["version"]),
            anchor_nid   = d["anchor_nid"],
            cluster_size = int(d["cluster_size"]),
            members      = [MemberInfo._from_dict(m) for m in d.get("members") or []],
            truncated    = d.get("truncated"),
            cluster_epoch = int(epoch) if epoch is not None else None,
        )


@dataclass
class TopologyFilter:
    tags_any:   list[str] | None = None
    tags_all:   list[str] | None = None
    node_roles: list[str] | None = None

    def _to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.tags_any   is not None: out["tags_any"]   = self.tags_any
        if self.tags_all   is not None: out["tags_all"]   = self.tags_all
        if self.node_roles is not None: out["node_roles"] = self.node_roles
        return out


@dataclass
class MemberChanges:
    nid:             str | None       = None
    node_roles:      list[str] | None = None
    activation_mode: str | None       = None
    child_anchor:    bool | None      = None
    member_count:    int | None       = None
    tags:            list[str] | None = None
    joined_at:       str | None       = None
    last_seen:       str | None       = None
    capabilities:    Any | None       = None
    metrics:         Any | None       = None

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> "MemberChanges":
        return cls(
            nid             = d.get("nid"),
            node_roles      = d.get("node_roles"),
            activation_mode = d.get("activation_mode"),
            child_anchor    = d.get("child_anchor"),
            member_count    = d.get("member_count"),
            tags            = d.get("tags"),
            joined_at       = d.get("joined_at"),
            last_seen       = d.get("last_seen"),
            capabilities    = d.get("capabilities"),
            metrics         = d.get("metrics"),
        )


# ── Topology events ────────────────────────────────────────────────────────────


@dataclass
class TopologyEvent(ABC):
    version: int


@dataclass
class MemberJoined(TopologyEvent):
    member: MemberInfo = field(default_factory=lambda: MemberInfo("", [], ""))


@dataclass
class MemberLeft(TopologyEvent):
    nid: str = ""


@dataclass
class MemberUpdated(TopologyEvent):
    nid:     str           = ""
    changes: MemberChanges = field(default_factory=MemberChanges)


@dataclass
class AnchorState(TopologyEvent):
    """``anchor_state`` topology event.

    ``field`` is the sub-type tag on the wire and ``details`` its payload object.
    The sub-type tag constants live here rather than on :class:`TopologyWire`,
    mirroring the reference implementation — they are part of this event's own
    contract, not the shared topology vocabulary.
    """

    #: Pre-existing sub-type: the Anchor rebased its topology version counter.
    FIELD_VERSION_REBASED = "version_rebased"
    #: NPS-CR-0009 §1.2: cluster ownership moved to ``successor_nid`` at a new epoch.
    FIELD_ANCHOR_FAILOVER = "anchor_failover"
    #: NPS-CR-0009 §1.3: the Anchor lost quorum and is read-only degraded.
    FIELD_ANCHOR_QUORUM_LOST = "anchor_quorum_lost"

    #: Reason enum for :meth:`failover`.
    REASON_PLANNED = "planned"
    REASON_ACTIVE_LOST = "active_lost"

    field:   str      = ""
    details: Any | None = None

    @staticmethod
    def failover(
        successor_nid: str,
        cluster_epoch: int,
        reason: str = REASON_PLANNED,
        version: int = 0,
    ) -> "AnchorState":
        """Build an ``anchor_failover`` event (NPS-CR-0009 §1.2).

        :param successor_nid: the Anchor that took ownership.
        :param cluster_epoch: the new, strictly-greater epoch.
        :param reason: ``"planned"`` (default) or ``"active_lost"``.
        """
        return AnchorState(
            version=version,
            field=AnchorState.FIELD_ANCHOR_FAILOVER,
            details={
                "successor_nid": successor_nid,
                "cluster_epoch": cluster_epoch,
                "reason": reason,
            },
        )

    @staticmethod
    def quorum_lost(quorum_size: int, available: int, version: int = 0) -> "AnchorState":
        """Build an ``anchor_quorum_lost`` event (NPS-CR-0009 §1.3)."""
        return AnchorState(
            version=version,
            field=AnchorState.FIELD_ANCHOR_QUORUM_LOST,
            details={"quorum_size": quorum_size, "available": available},
        )


@dataclass
class ResyncRequired(TopologyEvent):
    reason:  str = ""
    version: int = 0  # type: ignore[assignment]  # overrides parent default; always 0


# ── Exception ──────────────────────────────────────────────────────────────────


class AnchorTopologyException(Exception):
    """
    Raised when the Anchor Node returns a protocol-level error, either as a
    non-2xx HTTP response or as an error envelope inside a topology.stream.
    """

    def __init__(
        self,
        nwp_error_code: str,
        nps_status: str,
        message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.nwp_error_code = nwp_error_code
        self.nps_status     = nps_status


# ── Client ─────────────────────────────────────────────────────────────────────


class AnchorNodeClient:
    """
    Async client for an Anchor Node's reserved topology query types
    (NPS-2 §12 — topology.snapshot and topology.stream).

    Usage::

        async with AnchorNodeClient("https://anchor.example.com") as client:
            snap = await client.get_snapshot()
            async for event in client.subscribe():
                ...

    The client is auth-agnostic; configure ``http_client`` with any required
    headers before passing it in.
    """

    def __init__(
        self,
        base_url: str,
        *,
        path_prefix: str = "",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url    = base_url.rstrip("/")
        self._path_prefix = path_prefix.rstrip("/")
        self._owns_client = http_client is None
        self._http        = http_client or httpx.AsyncClient()

    # ── Context manager ────────────────────────────────────────────────────────

    async def __aenter__(self) -> "AnchorNodeClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def close(self) -> None:
        """Close the underlying HTTP client. Not needed when used as an async context manager."""
        if self._owns_client:
            await self._http.aclose()

    # ── topology.snapshot ──────────────────────────────────────────────────────

    async def get_snapshot(
        self,
        scope:      str                   = _SCOPE_CLUSTER,
        include:    tuple[str, ...] | list[str] = ("members",),
        depth:      int                   = 1,
        target_nid: str | None            = None,
        **kwargs:   Any,
    ) -> TopologySnapshot:
        """
        Fetch the current cluster topology (NPS-2 §12.1).

        Args:
            scope:      ``"cluster"`` (default) or ``"member"``.
            include:    Fields to include in the response; default ``("members",)``.
            depth:      Recursion depth for sub-cluster expansion; default 1.
            target_nid: Required when ``scope="member"``; ignored otherwise.
            **kwargs:   Forwarded to ``httpx.AsyncClient.post`` (e.g. ``timeout``).

        Returns:
            A fully-materialised :class:`TopologySnapshot`.

        Raises:
            AnchorTopologyException: on non-2xx HTTP response.
        """
        topology: dict[str, Any] = {
            "scope":   scope,
            "include": list(include),
            "depth":   depth,
        }
        if target_nid is not None:
            topology["target_nid"] = target_nid

        body = {
            "type":     _TYPE_SNAPSHOT,
            "topology": topology,
        }

        url  = f"{self._base_url}{self._path_prefix}/query"
        resp = await self._http.post(url, json=body, **kwargs)

        if not (200 <= resp.status_code < 300):
            raise _build_error(resp)

        caps = resp.json()
        # CapsFrame: {"data": [...]} — topology snapshot is data[0]
        data = caps.get("data") or []
        if not data:
            raise AnchorTopologyException(
                "UNKNOWN",
                f"HTTP-{resp.status_code}",
                "topology.snapshot returned an empty CapsFrame data array.",
            )
        return TopologySnapshot._from_dict(data[0])

    # ── topology.stream ────────────────────────────────────────────────────────

    async def subscribe(
        self,
        filter:        TopologyFilter | None = None,
        since_version: int | None            = None,
        scope:         str                   = _SCOPE_CLUSTER,
    ) -> AsyncIterator[TopologyEvent]:
        """
        Subscribe to live topology events (NPS-2 §12.2).

        The first NDJSON line (subscription ack) is consumed internally and
        not yielded. Subsequent lines are parsed into :class:`TopologyEvent`
        subclasses. Unknown event types are silently skipped.

        Yields :class:`ResyncRequired` as the final event, then stops. The
        caller MUST issue a fresh :meth:`get_snapshot` before resubscribing.

        Args:
            filter:        Optional subscriber-side filter.
            since_version: Resume from this topology version; ``None`` means
                           "from now".
            scope:         ``"cluster"`` (default) or ``"member"``.

        Raises:
            AnchorTopologyException: on non-2xx HTTP response or a mid-stream
                                     error envelope.
        """
        topology: dict[str, Any] = {"scope": scope}
        if filter is not None:
            fd = filter._to_dict()
            if fd:
                topology["filter"] = fd
        if since_version is not None:
            topology["since_version"] = since_version

        req_body = {
            "type":      _TYPE_STREAM,
            "action":    "subscribe",
            "stream_id": uuid.uuid4().hex,
            "topology":  topology,
        }

        url = f"{self._base_url}{self._path_prefix}/subscribe"
        async with self._http.stream(
            "POST",
            url,
            json=req_body,
            headers={"Accept": "application/x-ndjson"},
        ) as resp:
            if not (200 <= resp.status_code < 300):
                # Consume body to build a useful error message
                body_text = await resp.aread()
                raise _build_error_from_bytes(resp.status_code, body_text)

            line_iter = resp.aiter_lines()

            # Consume and discard the subscription ack (first line)
            try:
                await line_iter.__anext__()
            except StopAsyncIteration:
                return

            async for line in line_iter:
                if not line.strip():
                    continue

                parsed = _try_parse_json(line)
                if parsed is None:
                    continue

                # Error envelope: has "error" + "status", no "event_type"
                if "event_type" not in parsed and "error" in parsed and "status" in parsed:
                    raise AnchorTopologyException(
                        nwp_error_code = parsed.get("error", "UNKNOWN"),
                        nps_status     = parsed.get("status", "UNKNOWN"),
                        message        = parsed.get("message"),
                    )

                event = _parse_event(parsed)
                if event is None:
                    continue

                yield event

                if isinstance(event, ResyncRequired):
                    return


# ── Internal helpers ───────────────────────────────────────────────────────────


def _try_parse_json(line: str) -> dict[str, Any] | None:
    try:
        result = json.loads(line)
        if isinstance(result, dict):
            return result
        return None
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_event(d: dict[str, Any]) -> TopologyEvent | None:
    event_type = d.get("event_type")
    if not isinstance(event_type, str):
        return None

    seq: int = int(d["seq"]) if "seq" in d and d["seq"] is not None else 0
    payload: dict[str, Any] = d.get("payload") or {}

    if event_type == _EVENT_MEMBER_JOINED:
        return MemberJoined(
            version = seq,
            member  = MemberInfo._from_dict(payload),
        )
    if event_type == _EVENT_MEMBER_LEFT:
        return MemberLeft(
            version = seq,
            nid     = payload.get("nid", ""),
        )
    if event_type == _EVENT_MEMBER_UPDATED:
        return MemberUpdated(
            version = seq,
            nid     = payload.get("nid", ""),
            changes = MemberChanges._from_dict(payload.get("changes") or {}),
        )
    if event_type == _EVENT_ANCHOR_STATE:
        return AnchorState(
            version = seq,
            field   = payload.get("field", ""),
            details = payload.get("details"),
        )
    if event_type == _EVENT_RESYNC_REQUIRED:
        return ResyncRequired(
            version = 0,
            reason  = payload.get("reason", "unknown"),
        )

    # Unknown event type — skip
    return None


def _build_error(resp: httpx.Response) -> AnchorTopologyException:
    return _build_error_from_bytes(resp.status_code, resp.content)


def _build_error_from_bytes(status_code: int, body: bytes) -> AnchorTopologyException:
    try:
        d = json.loads(body)
        if isinstance(d, dict) and "error" in d and "status" in d:
            return AnchorTopologyException(
                nwp_error_code = d.get("error", "UNKNOWN"),
                nps_status     = d.get("status", "UNKNOWN"),
                message        = d.get("message") or body.decode(errors="replace"),
            )
    except (json.JSONDecodeError, ValueError):
        pass
    body_str = body.decode(errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
    return AnchorTopologyException(
        nwp_error_code = "UNKNOWN",
        nps_status     = f"HTTP-{status_code}",
        message        = body_str,
    )
