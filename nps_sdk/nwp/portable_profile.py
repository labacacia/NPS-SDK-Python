# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""NWP v0.20 transport-independent Node and Bridge server decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from nps_sdk.core.status_codes import (
    NPS_CLIENT_BAD_FRAME,
    NPS_CLIENT_BAD_PARAM,
    NPS_CLIENT_NOT_FOUND,
    NPS_CLIENT_UNPROCESSABLE,
    NPS_LIMIT_PAYLOAD,
    NPS_OK,
    NPS_OK_ACCEPTED,
    NPS_SERVER_TIMEOUT,
    NPS_SERVER_UNSUPPORTED,
)
from nps_sdk.nwp import error_codes, http_headers
from nps_sdk.nwp.bridge import BridgeTarget
from nps_sdk.nwp.complex_node_server import ComplexChildUrlValidator
from nps_sdk.nwp.inbound.error_map import BridgeErrorCodes

Transport = Literal["http", "native"]
NodeRole = Literal["memory", "action", "complex"]
TelemetryOutcome = Literal["success", "rejected", "cancelled", "timeout"]


@dataclass(frozen=True)
class PortableNodeRequest:
    """Input to the portable Node admission policy."""

    transport: Transport
    node_role: NodeRole
    method: str | None = None
    path: str | None = None
    content_type: str | None = None
    accept: str | None = None
    body_bytes: int = 0
    max_body_bytes: int = 1024 * 1024
    frame_kind: str | None = None
    body_valid: bool = True
    cancelled: bool = False
    correlation_id: str | None = None


@dataclass(frozen=True)
class PortableNodeDecision:
    """Terminal portable Node admission result."""

    decision: str
    http_status: int | None = None
    content_type: str | None = None
    status: str | None = None
    error: str | None = None
    allow: str | None = None
    response_frame: str | None = None
    correlation_id: str | None = None
    telemetry_outcome: TelemetryOutcome = "success"
    legacy_media_type_accepted: bool = False


def evaluate_portable_node(request: PortableNodeRequest) -> PortableNodeDecision:
    """Evaluate admission without reading a stream or invoking a provider."""
    if request.cancelled:
        return _node_result(request, "abort", telemetry_outcome="cancelled")
    if request.transport == "native":
        return _evaluate_native_node(request)
    if request.transport != "http":
        raise ValueError(f"unknown NWP transport: {request.transport}")
    return _evaluate_http_node(request)


def _evaluate_http_node(request: PortableNodeRequest) -> PortableNodeDecision:
    path = (request.path or "").lower()
    method = (request.method or "").upper()
    if path == "/.nwm":
        if method != "GET":
            return _method_not_allowed(request, "GET")
        return _node_result(
            request,
            "serve_manifest",
            http_status=200,
            content_type=http_headers.MIME_MANIFEST,
        )

    if path not in ("/query", "/invoke"):
        return _node_reject(
            request, 404, NPS_CLIENT_NOT_FOUND, error_codes.HTTP_FRAME_BODY_MALFORMED
        )
    if method != "POST":
        return _method_not_allowed(request, "POST")

    media_type = _base_media_type(request.content_type)
    legacy = media_type == http_headers.MIME_LEGACY_FRAME
    if media_type != http_headers.MIME_FRAME and not legacy:
        return _node_reject(
            request,
            400,
            NPS_CLIENT_BAD_FRAME,
            error_codes.HTTP_CONTENT_TYPE_UNSUPPORTED,
        )
    if not _accepts(request.accept, http_headers.MIME_CAPSULE):
        return _node_reject(
            request,
            400,
            NPS_CLIENT_BAD_PARAM,
            error_codes.HTTP_ACCEPT_UNSATISFIABLE,
        )
    if request.max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be positive")
    if request.body_bytes > request.max_body_bytes:
        return _node_reject(
            request, 413, NPS_LIMIT_PAYLOAD, error_codes.HTTP_BODY_TOO_LARGE
        )
    if not request.body_valid:
        return _node_reject(
            request, 400, NPS_CLIENT_BAD_FRAME, error_codes.HTTP_FRAME_BODY_MALFORMED
        )

    query = (
        path == "/query"
        and request.node_role in ("memory", "complex")
        and (request.frame_kind or "").lower() == "query"
    )
    action = (
        path == "/invoke"
        and request.node_role in ("action", "complex")
        and (request.frame_kind or "").lower() == "action"
    )
    if not query and not action:
        return _node_reject(
            request, 400, NPS_CLIENT_BAD_FRAME, error_codes.HTTP_FRAME_BODY_MALFORMED
        )
    return _node_result(
        request,
        "dispatch_query" if query else "dispatch_action",
        http_status=200,
        content_type=http_headers.MIME_CAPSULE,
        legacy_media_type_accepted=legacy,
    )


def _evaluate_native_node(request: PortableNodeRequest) -> PortableNodeDecision:
    frame_kind = (request.frame_kind or "").lower()
    query = frame_kind == "query" and request.node_role in ("memory", "complex")
    action = frame_kind == "action" and request.node_role in ("action", "complex")
    if request.body_valid and (query or action):
        return _node_result(
            request,
            "dispatch_query" if query else "dispatch_action",
            response_frame="caps",
        )
    return _node_result(
        request,
        "error_frame",
        status=NPS_CLIENT_BAD_FRAME,
        error="NWP-NATIVE-FRAME-UNSUPPORTED",
        response_frame="error",
        telemetry_outcome="rejected",
    )


def _method_not_allowed(
    request: PortableNodeRequest, allowed_method: str
) -> PortableNodeDecision:
    return _node_result(
        request,
        "reject",
        http_status=405,
        allow=allowed_method,
        telemetry_outcome="rejected",
    )


def _node_reject(
    request: PortableNodeRequest, http_status: int, status: str, error: str
) -> PortableNodeDecision:
    return _node_result(
        request,
        "reject",
        http_status=http_status,
        content_type=http_headers.MIME_ERROR,
        status=status,
        error=error,
        telemetry_outcome="rejected",
    )


def _node_result(
    request: PortableNodeRequest,
    decision: str,
    *,
    http_status: int | None = None,
    content_type: str | None = None,
    status: str | None = None,
    error: str | None = None,
    allow: str | None = None,
    response_frame: str | None = None,
    telemetry_outcome: TelemetryOutcome = "success",
    legacy_media_type_accepted: bool = False,
) -> PortableNodeDecision:
    return PortableNodeDecision(
        decision=decision,
        http_status=http_status,
        content_type=content_type,
        status=status,
        error=error,
        allow=allow,
        response_frame=response_frame,
        correlation_id=request.correlation_id,
        telemetry_outcome=telemetry_outcome,
        legacy_media_type_accepted=legacy_media_type_accepted,
    )


@dataclass(frozen=True)
class BridgeLifecycleRequest:
    """Input to portable outbound Bridge preflight."""

    protocol: str
    endpoint: str
    registered_protocols: Sequence[str]
    allow_http: bool = True
    reject_private: bool = True
    allowed_prefixes: Sequence[str] = ()
    timeout_ms: int = 0
    elapsed_ms: int = 0
    cancelled: bool = False
    correlation_id: str | None = None
    task_mode: str = "sync"


@dataclass(frozen=True)
class BridgeLifecycleDecision:
    """Terminal outbound Bridge lifecycle result."""

    decision: str
    http_status: int | None = None
    status: str | None = None
    error: str | None = None
    correlation_id: str | None = None
    task_mode: str | None = None
    telemetry_outcome: TelemetryOutcome = "success"


def evaluate_bridge_lifecycle(request: BridgeLifecycleRequest) -> BridgeLifecycleDecision:
    """Evaluate target, dispatcher, endpoint, cancellation, and deadline."""
    if request.cancelled:
        return _bridge_result(request, "abort", telemetry_outcome="cancelled")
    if not request.protocol.strip() or not request.endpoint.strip():
        return _bridge_result(
            request,
            "reject",
            http_status=422,
            status=NPS_CLIENT_UNPROCESSABLE,
            error=BridgeErrorCodes.TARGET_INVALID,
            telemetry_outcome="rejected",
        )
    if request.protocol.lower() not in {
        protocol.lower() for protocol in request.registered_protocols
    }:
        return _bridge_result(
            request,
            "reject",
            http_status=501,
            status=NPS_SERVER_UNSUPPORTED,
            error=BridgeErrorCodes.PROTOCOL_UNSUPPORTED,
            telemetry_outcome="rejected",
        )

    target = BridgeTarget(
        request.protocol,
        request.endpoint,
        {
            "allow_http": request.allow_http,
            "reject_private": request.reject_private,
            "allowed_prefixes": list(request.allowed_prefixes),
        },
    )
    endpoint_error = ComplexChildUrlValidator.validate(
        target.endpoint,
        list(request.allowed_prefixes),
        reject_private=request.reject_private,
        allow_http=request.allow_http,
    )
    if endpoint_error is not None:
        return _bridge_result(
            request,
            "reject",
            http_status=422,
            status=NPS_CLIENT_UNPROCESSABLE,
            error=BridgeErrorCodes.ENDPOINT_INVALID,
            telemetry_outcome="rejected",
        )

    if request.timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")
    if request.elapsed_ms < 0:
        raise ValueError("elapsed_ms must not be negative")
    if request.elapsed_ms >= request.timeout_ms:
        return _bridge_result(
            request,
            "reject",
            http_status=504,
            status=NPS_SERVER_TIMEOUT,
            error=BridgeErrorCodes.UPSTREAM_FAILED,
            telemetry_outcome="timeout",
        )

    task_mode = "async" if request.task_mode.lower() == "async" else "sync"
    return _bridge_result(
        request,
        "dispatch",
        status=NPS_OK_ACCEPTED if task_mode == "async" else NPS_OK,
        task_mode=task_mode,
    )


def _bridge_result(
    request: BridgeLifecycleRequest,
    decision: str,
    *,
    http_status: int | None = None,
    status: str | None = None,
    error: str | None = None,
    telemetry_outcome: TelemetryOutcome = "success",
    task_mode: str | None = None,
) -> BridgeLifecycleDecision:
    return BridgeLifecycleDecision(
        decision=decision,
        http_status=http_status,
        status=status,
        error=error,
        correlation_id=request.correlation_id,
        task_mode=task_mode,
        telemetry_outcome=telemetry_outcome,
    )


def _base_media_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _accepts(accept: str | None, response_type: str) -> bool:
    if not accept or not accept.strip():
        return True
    values = {_base_media_type(item) for item in accept.split(",")}
    return bool(values & {"*/*", "application/*", response_type})
