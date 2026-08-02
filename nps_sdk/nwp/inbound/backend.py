# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
The inbound Bridge backend abstraction (NWP §2.1 inbound profile, NPS-CR-0010 §1).

The consolidation of the SDK's in-process Bridge and ``compat/*-ingress`` is a backend
*abstraction*, not a deletion. Two deployment shapes, one interface::

    NwpBackend ──┬── InProcessNwpBackend   (delegate dispatch — the SDK's shape)
                 └── HttpNwpBackend        (HTTP to a remote node — the gateway shape)
                        ▲
        one McpInboundServer / A2aInboundServer / GrpcInboundService
        serving the full method set over either backend

The protocol servers are written against the interface alone and are unaware of which
shape they are serving. Both may coexist in one Bridge.
"""

from __future__ import annotations

import dataclasses
import enum
import json
from typing import Any, Awaitable, Callable, Iterable, Protocol, Sequence

from nps_sdk.core.status_codes import (
    NPS_DOWNSTREAM_UNAVAILABLE,
    NPS_SERVER_INTERNAL,
    NPS_SERVER_TIMEOUT,
    NPS_SERVER_UNSUPPORTED,
)
from nps_sdk.ncp.frames import ErrorFrame
from nps_sdk.nwp.frames import ActionFrame, QueryFrame
from nps_sdk.nwp.inbound.error_map import BridgeErrorCodes, BridgeErrorMap

__all__ = [
    "NwpNodeRole",
    "NwpNodeDescriptor",
    "NwpActionDescriptor",
    "NwpResult",
    "NwpBackend",
    "InProcessNwpBackend",
    "NwpUpstream",
    "HttpNwpBackend",
    "OPEN_OBJECT_SCHEMA",
]

#: Advertised for an action that declares no input schema.
OPEN_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": True}


class NwpNodeRole(enum.Enum):
    """Role an NWP node carries, as reported by its NWM ``node_type`` (NWP §4.1)."""

    UNKNOWN = "unknown"
    MEMORY = "memory"
    ACTION = "action"
    COMPLEX = "complex"
    ANCHOR = "anchor"
    BRIDGE = "bridge"

    @classmethod
    def parse(cls, node_type: str | None) -> "NwpNodeRole":
        """Parse an NWM ``node_type``; anything unrecognised is :attr:`UNKNOWN`."""
        try:
            return cls((node_type or "").lower())
        except ValueError:
            return cls.UNKNOWN


@dataclasses.dataclass(frozen=True)
class NwpNodeDescriptor:
    """Identity and role of one NWP node fronted by an inbound Bridge server."""

    #: Logical name. Namespaces MCP resource URIs and tool names, so it MUST be
    #: unique per Bridge.
    name: str
    role: NwpNodeRole
    display_name: str | None = None
    description: str | None = None

    @property
    def is_queryable(self) -> bool:
        """Memory / Complex — projected onto a foreign protocol's read surface."""
        return self.role in (NwpNodeRole.MEMORY, NwpNodeRole.COMPLEX)

    @property
    def is_invokable(self) -> bool:
        """Action / Complex — projected onto a foreign protocol's call surface."""
        return self.role in (NwpNodeRole.ACTION, NwpNodeRole.COMPLEX)


@dataclasses.dataclass(frozen=True)
class NwpActionDescriptor:
    """One action exposed by an NWP node, projected onto a foreign call surface."""

    action_id: str
    description: str | None = None
    #: JSON Schema for the arguments. ``None`` ⇒ :data:`OPEN_OBJECT_SCHEMA` is advertised.
    input_schema: dict[str, Any] | None = None
    async_: bool = False
    tags: tuple[str, ...] | None = None


@dataclasses.dataclass(frozen=True)
class NwpResult:
    """Outcome of one NWP operation performed on behalf of a foreign client.

    Carries either a payload or an NPS status, so inbound servers can map failures onto
    their protocol's error space per NWP §16.3 instead of forwarding an opaque body.
    **This type is why the mapping works.**
    """

    ok: bool
    payload: Any = None
    nps_status: str | None = None
    nwp_error: str | None = None
    message: str | None = None

    @staticmethod
    def success(payload: Any) -> "NwpResult":
        return NwpResult(ok=True, payload=payload)

    @staticmethod
    def failure(nps_status: str, nwp_error: str | None = None,
                message: str | None = None) -> "NwpResult":
        return NwpResult(ok=False, nps_status=nps_status,
                         nwp_error=nwp_error, message=message)

    @staticmethod
    def dispatch_failed(message: str) -> "NwpResult":
        return NwpResult.failure(
            NPS_SERVER_INTERNAL, BridgeErrorCodes.SERVER_DISPATCH_FAILED, message)

    def raw_payload_json(self) -> str:
        """The payload as compact JSON text — what MCP content blocks carry."""
        if self.payload is None:
            return "{}"
        return json.dumps(self.payload, separators=(",", ":"), ensure_ascii=False)

    def failure_payload(self) -> dict[str, Any]:
        """The ``{status, error, message}`` body a domain failure is projected onto."""
        return {"status": self.nps_status, "error": self.nwp_error, "message": self.message}


class NwpBackend(Protocol):
    """One NWP node reachable by an inbound Bridge server."""

    async def get_descriptor(self) -> NwpNodeDescriptor: ...

    async def get_manifest(self) -> NwpResult: ...

    async def get_actions(self) -> Sequence[NwpActionDescriptor]: ...

    async def query(self, query: Any) -> NwpResult: ...

    async def invoke(self, action_id: str, arguments: Any, async_: bool) -> NwpResult: ...


#: Dispatches an :class:`ActionFrame` to a co-hosted NPS node.
NwpActionDispatcher = Callable[[ActionFrame], Awaitable[Any]]
#: Dispatches a :class:`QueryFrame` to a co-hosted NPS Memory / Complex Node.
NwpQueryDispatcher = Callable[[QueryFrame], Awaitable[Any]]


class InProcessNwpBackend:
    """A backend that dispatches in-process, without a network hop.

    This is the deployment the pre-CR-0010 SDK Bridge server supported, preserved
    verbatim in capability while gaining a queryable surface.
    """

    def __init__(
        self,
        descriptor: NwpNodeDescriptor,
        actions: Iterable[NwpActionDescriptor] | None = None,
        invoke_dispatcher: NwpActionDispatcher | None = None,
        query_dispatcher: NwpQueryDispatcher | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._actions = tuple(actions or ())
        self._invoke = invoke_dispatcher
        self._query = query_dispatcher

    async def get_descriptor(self) -> NwpNodeDescriptor:
        return self._descriptor

    async def get_manifest(self) -> NwpResult:
        return NwpResult.success({
            "node_type": self._descriptor.role.value,
            "display_name": self._descriptor.display_name or self._descriptor.name,
            "description": self._descriptor.description,
        })

    async def get_actions(self) -> Sequence[NwpActionDescriptor]:
        return self._actions if self._descriptor.is_invokable else ()

    async def query(self, query: Any) -> NwpResult:
        if not self._descriptor.is_queryable:
            return NwpResult.failure(
                NPS_SERVER_UNSUPPORTED, BridgeErrorCodes.SERVER_TOOL_NOT_FOUND,
                f"Node '{self._descriptor.name}' is not queryable "
                f"(role: {self._descriptor.role.value}).")
        if self._query is None:
            return NwpResult.failure(
                NPS_SERVER_INTERNAL, BridgeErrorCodes.SERVER_DISPATCHER_MISSING,
                f"Node '{self._descriptor.name}' declares a queryable role but no "
                "query dispatcher was configured.")
        try:
            return _to_result(await self._query(QueryFrame(filter=query)))
        except Exception as exc:  # noqa: BLE001 — any dispatch fault is the same fault
            return NwpResult.dispatch_failed(str(exc))

    async def invoke(self, action_id: str, arguments: Any, async_: bool) -> NwpResult:
        # Deliberately NOT gated on is_invokable: a deployment that declares actions but
        # forgets the dispatcher must fail loudly, not look like it exposes nothing.
        if self._invoke is None:
            return NwpResult.failure(
                NPS_SERVER_INTERNAL, BridgeErrorCodes.SERVER_DISPATCHER_MISSING,
                f"Node '{self._descriptor.name}' has no action dispatcher configured.")
        frame = ActionFrame(action_id=action_id, params=arguments, async_=async_)
        try:
            return _to_result(await self._invoke(frame))
        except Exception as exc:  # noqa: BLE001
            return NwpResult.dispatch_failed(str(exc))


def _to_result(frame: Any) -> NwpResult:
    """Project a co-hosted node's frame response onto :class:`NwpResult`.

    An ErrorFrame keeps its NPS status so the inbound server can map it per §16.3
    rather than forwarding an opaque body.
    """
    if isinstance(frame, ErrorFrame):
        return NwpResult.failure(frame.status or NPS_SERVER_INTERNAL,
                                 frame.error, frame.message)
    if frame is None:
        return NwpResult.success({})
    if hasattr(frame, "to_dict"):
        return NwpResult.success(frame.to_dict())
    return NwpResult.success(frame)


# ── HTTP-fronted backend ──────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class NwpUpstream:
    """A remote NWP node this Bridge fronts over HTTP — the standalone-gateway shape."""

    name: str
    base_url: str
    agent_nid: str | None = None
    auth_header: str | None = None
    read_limit: int = 100


class HttpNwpBackend:
    """A backend that reaches a remote NWP node over its HTTP binding.

    The descriptor is cached after the first fetch. An unreachable ``/.nwm`` caches
    :attr:`NwpNodeRole.UNKNOWN`: a dead upstream must not take down the whole Bridge,
    it is simply projected onto nothing.
    """

    def __init__(self, http_client: Any, upstream: NwpUpstream) -> None:
        self._http = http_client
        self._up = upstream
        self._descriptor: NwpNodeDescriptor | None = None

    # ── descriptor / manifest ────────────────────────────────────────────────

    async def get_descriptor(self) -> NwpNodeDescriptor:
        if self._descriptor is None:
            manifest = await self._get_json("/.nwm")
            role = NwpNodeRole.UNKNOWN
            display = description = None
            if manifest.ok and isinstance(manifest.payload, dict):
                role = NwpNodeRole.parse(manifest.payload.get("node_type"))
                display = manifest.payload.get("display_name")
                description = manifest.payload.get("description")
            self._descriptor = NwpNodeDescriptor(
                name=self._up.name, role=role,
                display_name=display, description=description)
        return self._descriptor

    async def get_manifest(self) -> NwpResult:
        return await self._get_json("/.nwm")

    async def get_actions(self) -> Sequence[NwpActionDescriptor]:
        result = await self._get_json("/actions")
        if not result.ok or not isinstance(result.payload, dict):
            return ()
        actions = result.payload.get("actions")
        if not isinstance(actions, dict):
            return ()
        return tuple(
            NwpActionDescriptor(
                action_id=action_id,
                description=(spec or {}).get("description"),
                input_schema=(spec or {}).get("params_schema"),
            )
            for action_id, spec in actions.items()
        )

    # ── query / invoke ───────────────────────────────────────────────────────

    async def query(self, query: Any) -> NwpResult:
        descriptor = await self.get_descriptor()
        if not descriptor.is_queryable:
            return NwpResult.failure(
                NPS_SERVER_UNSUPPORTED, BridgeErrorCodes.SERVER_TOOL_NOT_FOUND,
                f"Node '{descriptor.name}' is not queryable "
                f"(role: {descriptor.role.value}).")
        return await self._post_json("/query", query if query is not None else {})

    async def invoke(self, action_id: str, arguments: Any, async_: bool) -> NwpResult:
        return await self._post_json(
            "/invoke", {"action_id": action_id, "params": arguments, "async": async_})

    # ── HTTP plumbing (the §16.3 inverse direction) ──────────────────────────

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type
        if self._up.agent_nid:
            headers["X-NWP-Agent"] = self._up.agent_nid
        if self._up.auth_header:
            headers["Authorization"] = self._up.auth_header
        return headers

    async def _get_json(self, path: str) -> NwpResult:
        return await self._request("GET", path, None)

    async def _post_json(self, path: str, body: Any) -> NwpResult:
        return await self._request("POST", path, body)

    async def _request(self, method: str, path: str, body: Any) -> NwpResult:
        url = self._up.base_url.rstrip("/") + path
        try:
            if method == "GET":
                resp = await self._http.get(url, headers=self._headers())
            else:
                resp = await self._http.post(
                    url, json=body,
                    headers=self._headers("application/nwp-frame"))
        except Exception as exc:  # noqa: BLE001
            # Timeouts and connection faults are distinct NPS classes; §16.3 forbids
            # collapsing them onto one status.
            status = (NPS_SERVER_TIMEOUT if "timeout" in type(exc).__name__.lower()
                      else NPS_DOWNSTREAM_UNAVAILABLE)
            return NwpResult.failure(status, BridgeErrorCodes.UPSTREAM_FAILED, str(exc))

        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001 — a non-JSON body from an NWP node is a fault
            payload = None

        if not (200 <= resp.status_code < 300):
            error = payload.get("error") if isinstance(payload, dict) else None
            message = payload.get("message") if isinstance(payload, dict) else None
            return NwpResult.failure(
                BridgeErrorMap.from_http_status(resp.status_code),
                error, message if message is not None else resp.text)

        if payload is None:
            return NwpResult.failure(
                NPS_DOWNSTREAM_UNAVAILABLE, BridgeErrorCodes.UPSTREAM_FAILED,
                f"Upstream '{self._up.name}' returned a non-JSON body for {path}.")
        return NwpResult.success(payload)
