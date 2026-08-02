# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Inbound gRPC service surface of a Bridge Node (NWP §2.1 inbound profile, §16.1.2).

Implements the ``NwpIngress`` service logic — the four unary RPCs, backend resolution
and the §16.3 status mapping — of ``Protos/nwp_ingress.proto``, package
``labacacia.grpc_ingress.v1``, carried over unchanged from the published
``LabAcacia.GrpcIngress`` (clients hold generated stubs, so the ``.proto`` is public API).

**Transport binding is deliberately absent.** This SDK depends only on msgpack, httpx
and cryptography; adding grpcio + protobuf to bind a transport would be a heavy new
runtime dependency for every consumer. The request/response types below mirror the
proto messages field-for-field, so a host that *does* have grpcio can adapt generated
stubs onto :class:`GrpcInboundService` with a thin shim. All payloads are JSON-encoded
NWP frame bodies carried as bytes — schemas are runtime-declared via AnchorFrame, so a
typed proto is impossible.

What did change versus the old ingress is the error mapping: it collapsed 401 and 403
both onto ``PERMISSION_DENIED`` and every 5xx onto ``UNAVAILABLE``. §16.3 forbids
collapsing distinct NPS status classes, so this maps through
:class:`~nps_sdk.nwp.inbound.error_map.BridgeErrorMap` — the same table the MCP and A2A
surfaces use, in both directions.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Sequence

from nps_sdk.core.status_codes import NPS_CLIENT_NOT_FOUND, NPS_SERVER_UNSUPPORTED
from nps_sdk.nwp.inbound.backend import NwpBackend, NwpNodeRole, NwpResult
from nps_sdk.nwp.inbound.error_map import BridgeErrorCodes, BridgeErrorMap
from nps_sdk.nwp.inbound.options import BridgeInboundOptions

__all__ = [
    "UpstreamContext",
    "ManifestResponse",
    "InvokeResponse",
    "QueryResponse",
    "ActionsResponse",
    "GrpcRpcError",
    "GrpcInboundService",
]


@dataclasses.dataclass(frozen=True)
class UpstreamContext:
    """``UpstreamContext { upstream = 1; agent_nid = 2; idempotency_key = 3; traceparent = 4; }``"""

    upstream: str = ""
    agent_nid: str = ""
    idempotency_key: str = ""
    traceparent: str = ""


@dataclasses.dataclass(frozen=True)
class ManifestResponse:
    nwm_json: bytes
    node_type: str


@dataclasses.dataclass(frozen=True)
class InvokeResponse:
    http_status: int
    body_json: bytes
    task_id: str


@dataclasses.dataclass(frozen=True)
class QueryResponse:
    http_status: int
    body_json: bytes


@dataclasses.dataclass(frozen=True)
class ActionsResponse:
    actions_json: bytes


class GrpcRpcError(Exception):
    """Equivalent of ``RpcException``: a canonical gRPC status name plus a detail string.

    The NPS status and NWP error code travel in the detail, so a caller can recover the
    exact NPS fault rather than only the coarse gRPC class.
    """

    def __init__(self, status_code: str, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class GrpcInboundService:
    """The four ``NwpIngress`` unary RPCs over any set of NWP backends."""

    def __init__(self, options: BridgeInboundOptions, backends: Sequence[NwpBackend]) -> None:
        self._options = options
        self._backends = list(backends)

    # ── RPCs ─────────────────────────────────────────────────────────────────

    async def get_manifest(self, ctx: UpstreamContext | None = None) -> ManifestResponse:
        backend = await self._resolve(ctx)
        descriptor = await backend.get_descriptor()
        manifest = await backend.get_manifest()
        _ensure(manifest)
        return ManifestResponse(
            nwm_json=manifest.raw_payload_json().encode("utf-8"),
            node_type=("" if descriptor.role is NwpNodeRole.UNKNOWN
                       else descriptor.role.value),
        )

    async def list_actions(self, ctx: UpstreamContext | None = None) -> ActionsResponse:
        backend = await self._resolve(ctx)
        actions = await backend.get_actions()
        body = {"actions": {a.action_id: {"description": a.description} for a in actions}}
        return ActionsResponse(
            actions_json=json.dumps(body, separators=(",", ":")).encode("utf-8"))

    async def invoke(
        self,
        action_id: str,
        params_json: bytes = b"",
        ctx: UpstreamContext | None = None,
    ) -> InvokeResponse:
        if not action_id:
            raise GrpcRpcError("INVALID_ARGUMENT", "action_id is required")

        backend = await self._resolve(ctx)
        args = json.loads(params_json.decode("utf-8")) if params_json else None
        result = await backend.invoke(action_id, args, False)   # always async: false
        _ensure(result)

        return InvokeResponse(
            http_status=200,
            body_json=result.raw_payload_json().encode("utf-8"),
            task_id=_try_read_string(result.payload, "task_id") or "",
        )

    async def query(
        self,
        query_json: bytes = b"",
        ctx: UpstreamContext | None = None,
    ) -> QueryResponse:
        backend = await self._resolve(ctx)
        query = json.loads(query_json.decode("utf-8")) if query_json else {}
        result = await backend.query(query)
        _ensure(result)
        return QueryResponse(http_status=200,
                             body_json=result.raw_payload_json().encode("utf-8"))

    # ── plumbing ─────────────────────────────────────────────────────────────

    async def _resolve(self, ctx: UpstreamContext | None) -> NwpBackend:
        # §16.1.2 MUST-5. Note gRPC is NOT in the default inbound set, so this refuses
        # until "grpc" is explicitly declared.
        if not self._options.serves_inbound("grpc"):
            raise GrpcRpcError(
                BridgeErrorMap.to_grpc_status(NPS_SERVER_UNSUPPORTED),
                f"{NPS_SERVER_UNSUPPORTED} {BridgeErrorCodes.DIRECTION_UNSUPPORTED}: "
                'this Bridge Node does not declare "grpc" in bridge_inbound_protocols.')

        name = ctx.upstream if ctx is not None else ""
        for backend in self._backends:
            descriptor = await backend.get_descriptor()
            if not name and len(self._backends) == 1:
                return backend
            if descriptor.name.lower() == name.lower():
                return backend

        raise GrpcRpcError(
            BridgeErrorMap.to_grpc_status(NPS_CLIENT_NOT_FOUND),
            f"{NPS_CLIENT_NOT_FOUND} {BridgeErrorCodes.SERVER_TOOL_NOT_FOUND}: "
            f"no NWP node named '{name}' is fronted by this Bridge Node.")


def _ensure(result: NwpResult) -> None:
    """Turn a failed NWP result into a :class:`GrpcRpcError` carrying the §16.3 status."""
    if result.ok:
        return
    detail = result.nps_status or "NPS-SERVER-INTERNAL"
    if result.nwp_error:
        detail += f" {result.nwp_error}"
    if result.message:
        detail += f": {result.message}"
    raise GrpcRpcError(BridgeErrorMap.to_grpc_status(result.nps_status), detail)


def _try_read_string(payload: Any, name: str) -> str | None:
    if isinstance(payload, dict):
        value = payload.get(name)
        if isinstance(value, str):
            return value
    return None
