# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Outbound Bridge dispatchers (NPS → external protocol).

Ports .NET ``IBridgeDispatcher``, ``BridgeDispatcherRegistry``, ``BridgeNode``,
``HttpBridgeDispatcher``, ``GrpcBridgeDispatcher``, ``JsonRpcBridgeDispatcher``,
``McpBridgeDispatcher``, ``A2aBridgeDispatcher``.

CRITICAL: the gRPC/MCP/A2A dispatchers are all JSON / JSON-RPC over HTTP — the
gRPC dispatcher speaks the ``application/grpc+json`` codec over HTTP. There is
no native gRPC/protobuf dependency; every dispatcher uses httpx.
"""
from __future__ import annotations

import json
import struct
import uuid
from typing import Any, Protocol, runtime_checkable

import httpx

from nps_sdk.ncp.frames import CapsFrame
from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.bridge import endpoint_validator, target_parser
from nps_sdk.nwp.bridge.errors import BridgeDispatchException, BridgeErrorCodes
from nps_sdk.nwp.bridge.types import BridgeProtocols, BridgeTarget


def _estimate_token_cost(length: int) -> int:
    if length <= 0:
        return 0
    return max(1, length // 4)


def _apply_string_headers(target: BridgeTarget) -> dict[str, str]:
    found, headers = target_parser.try_get_json(target, "headers")
    if not found or not isinstance(headers, dict):
        return {}
    out: dict[str, str] = {}
    for name, value in headers.items():
        if isinstance(value, str) and value:
            out[name] = value
    return out


def _timeout_seconds(frame: ActionFrame) -> float | None:
    return frame.timeout_ms / 1000.0 if frame.timeout_ms and frame.timeout_ms > 0 else None


@runtime_checkable
class IBridgeDispatcher(Protocol):
    """Translates one NWP action invocation into a concrete non-NPS protocol call."""

    @property
    def protocol(self) -> str:
        """Bridge protocol identifier served by this dispatcher."""
        ...

    async def dispatch(self, frame: ActionFrame, target: BridgeTarget) -> CapsFrame:
        """Dispatch an action frame to the requested external target."""
        ...


# ── HTTP dispatcher ────────────────────────────────────────────────────────────

class HttpBridgeDispatcher:
    """Built-in Bridge dispatcher for HTTP and HTTPS endpoints."""

    RESPONSE_ANCHOR_REF = "nps://bridge/http-response/v1"

    def __init__(self, client: httpx.AsyncClient) -> None:
        if client is None:
            raise ValueError("client must not be None")
        self._client = client

    @property
    def protocol(self) -> str:
        return BridgeProtocols.HTTP

    async def dispatch(self, frame: ActionFrame, target: BridgeTarget) -> CapsFrame:
        parts = endpoint_validator.parse_http_endpoint(target)
        url = parts.geturl()
        method = (target_parser.get_string(target, "method", "POST") or "POST").strip().upper()
        headers = _apply_string_headers(target)
        content = self._build_body(frame, target, method, headers)

        try:
            response = await self._client.request(
                method, url, headers=headers, content=content,
                timeout=_timeout_seconds(frame),
            )
        except httpx.TimeoutException as exc:
            raise BridgeDispatchException(
                BridgeErrorCodes.UPSTREAM_FAILED, "HTTP bridge request timed out.", exc
            )
        except httpx.HTTPError as exc:
            raise BridgeDispatchException(
                BridgeErrorCodes.UPSTREAM_FAILED, "HTTP bridge request failed.", exc
            )

        body_text = response.text
        record = _build_http_record(response, body_text)
        return CapsFrame(
            anchor_ref=self.RESPONSE_ANCHOR_REF,
            count=1,
            data=(record,),
            token_est=_estimate_token_cost(len(body_text)),
        )

    @staticmethod
    def _build_body(
        frame: ActionFrame, target: BridgeTarget, method: str, headers: dict[str, str]
    ) -> bytes | None:
        if method in ("GET", "HEAD"):
            return None

        params = frame.params
        if isinstance(params, dict) and "body" in params:
            body = params["body"]
        else:
            found, target_body = target_parser.try_get_json(target, "body")
            if not found:
                return None
            body = target_body

        media_type = target_parser.get_string(target, "content_type", "application/json")
        if not any(k.lower() == "content-type" for k in headers):
            headers["content-type"] = media_type or "application/json"
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _build_http_record(response: httpx.Response, body_text: str) -> dict[str, Any]:
    content_type = response.headers.get("content-type")
    record: dict[str, Any] = {
        "status_code": response.status_code,
        "reason_phrase": response.reason_phrase,
        "success": 200 <= response.status_code < 300,
        "content_type": content_type,
        "headers": {k: v for k, v in response.headers.items()},
    }
    _write_body(record, body_text, content_type)
    return record


def _write_body(record: dict[str, Any], body_text: str, content_type: str | None) -> None:
    if body_text and content_type and "json" in content_type.lower():
        try:
            record["body"] = json.loads(body_text)
            return
        except json.JSONDecodeError:
            pass
    record["body_text"] = body_text


# ── gRPC (JSON codec over HTTP) dispatcher ──────────────────────────────────────

class GrpcBridgeDispatcher:
    """Built-in Bridge dispatcher for unary gRPC calls using the JSON gRPC codec
    (``application/grpc+json``). The endpoint path identifies the service and
    method, e.g. ``https://host/Package.Service/Method``."""

    RESPONSE_ANCHOR_REF = "nps://bridge/grpc-json-response/v1"

    def __init__(self, client: httpx.AsyncClient) -> None:
        if client is None:
            raise ValueError("client must not be None")
        self._client = client

    @property
    def protocol(self) -> str:
        return BridgeProtocols.GRPC

    async def dispatch(self, frame: ActionFrame, target: BridgeTarget) -> CapsFrame:
        parts = endpoint_validator.parse_http_endpoint(target)
        url = parts.geturl()
        headers = {"content-type": "application/grpc+json", "te": "trailers"}
        headers.update(_apply_string_headers(target))

        try:
            response = await self._client.post(
                url, headers=headers, content=self._build_grpc_message(frame, target),
                timeout=_timeout_seconds(frame),
            )
        except httpx.TimeoutException as exc:
            raise BridgeDispatchException(
                BridgeErrorCodes.UPSTREAM_FAILED, "gRPC bridge request timed out.", exc
            )
        except httpx.HTTPError as exc:
            raise BridgeDispatchException(
                BridgeErrorCodes.UPSTREAM_FAILED, "gRPC bridge request failed.", exc
            )

        body = response.content
        record = _build_grpc_record(response, body)
        return CapsFrame(
            anchor_ref=self.RESPONSE_ANCHOR_REF,
            count=1,
            data=(record,),
            token_est=_estimate_token_cost(len(body)),
        )

    @staticmethod
    def _build_grpc_message(frame: ActionFrame, target: BridgeTarget) -> bytes:
        payload: Any = None
        found = False
        for name in ("grpc_message", "message", "body"):
            found, payload = target_parser.try_get_json(target, name)
            if found:
                break
        if not found:
            params = frame.params
            if isinstance(params, dict) and "grpc_message" in params:
                payload = params["grpc_message"]
            elif params is not None:
                payload = params
            else:
                payload = {}

        json_bytes = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return b"\x00" + struct.pack(">I", len(json_bytes)) + json_bytes


def _read_grpc_messages(body: bytes) -> list[Any]:
    messages: list[Any] = []
    offset = 0
    while len(body) - offset >= 5:
        compressed = body[offset] != 0
        length = struct.unpack(">I", body[offset + 1:offset + 5])[0]
        offset += 5
        if compressed or len(body) - offset < length:
            break
        chunk = body[offset:offset + length]
        offset += length
        try:
            messages.append(json.loads(chunk))
        except json.JSONDecodeError:
            import base64
            messages.append(base64.b64encode(chunk).decode("ascii"))
    return messages


def _build_grpc_record(response: httpx.Response, body: bytes) -> dict[str, Any]:
    grpc_status = _read_grpc_header(response, "grpc-status")
    grpc_message = _read_grpc_header(response, "grpc-message")
    success = (200 <= response.status_code < 300) and (grpc_status in ("0", None))
    record: dict[str, Any] = {
        "status_code": response.status_code,
        "success": success,
        "content_type": response.headers.get("content-type"),
        "grpc_status": grpc_status,
        "grpc_message": grpc_message,
        "headers": {k: v for k, v in response.headers.items()},
        "trailers": {},
        "messages": _read_grpc_messages(body),
    }
    return record


def _read_grpc_header(response: httpx.Response, name: str) -> str | None:
    value = response.headers.get(name)
    return value if value is not None else None


# ── JSON-RPC 2.0 base dispatcher ────────────────────────────────────────────────

class JsonRpcBridgeDispatcher:
    """Base dispatcher for JSON-RPC 2.0 protocols transported over HTTP POST."""

    def __init__(self, client: httpx.AsyncClient, default_method: str, response_anchor_ref: str) -> None:
        if client is None:
            raise ValueError("client must not be None")
        if not default_method or not default_method.strip():
            raise ValueError("Default JSON-RPC method must not be empty.")
        if not response_anchor_ref or not response_anchor_ref.strip():
            raise ValueError("Response anchor reference must not be empty.")
        self._client = client
        self._default_method = default_method
        self._response_anchor_ref = response_anchor_ref

    @property
    def protocol(self) -> str:  # pragma: no cover - overridden by subclasses
        raise NotImplementedError

    async def dispatch(self, frame: ActionFrame, target: BridgeTarget) -> CapsFrame:
        parts = endpoint_validator.parse_http_endpoint(target)
        url = parts.geturl()
        headers = {"content-type": "application/json"}
        headers.update(_apply_string_headers(target))
        content = self._build_request_body(frame, target)

        try:
            response = await self._client.post(
                url, headers=headers, content=content, timeout=_timeout_seconds(frame)
            )
        except httpx.TimeoutException as exc:
            raise BridgeDispatchException(
                BridgeErrorCodes.UPSTREAM_FAILED,
                f"{self.protocol} JSON-RPC bridge request timed out.", exc,
            )
        except httpx.HTTPError as exc:
            raise BridgeDispatchException(
                BridgeErrorCodes.UPSTREAM_FAILED,
                f"{self.protocol} JSON-RPC bridge request failed.", exc,
            )

        body_text = response.text
        record = _build_jsonrpc_record(response, body_text)
        return CapsFrame(
            anchor_ref=self._response_anchor_ref,
            count=1,
            data=(record,),
            token_est=_estimate_token_cost(len(body_text)),
        )

    def _build_request_body(self, frame: ActionFrame, target: BridgeTarget) -> bytes:
        envelope = {
            "jsonrpc": "2.0",
            "id": self._request_id(frame, target),
            "method": self._rpc_method(frame, target),
            "params": self._rpc_params(frame, target),
        }
        return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def _rpc_method(self, frame: ActionFrame, target: BridgeTarget) -> str:
        method = target_parser.get_string(target, "rpc_method") or target_parser.get_string(target, "method")
        if method and method.strip():
            return method

        params = frame.params
        if isinstance(params, dict):
            frame_method = params.get("rpc_method")
            if isinstance(frame_method, str) and frame_method.strip():
                return frame_method

        return self._default_method

    @staticmethod
    def _request_id(frame: ActionFrame, target: BridgeTarget) -> Any:
        found, target_id = target_parser.try_get_json(target, "id")
        if found:
            return target_id

        params = frame.params
        if isinstance(params, dict) and "id" in params:
            return params["id"]

        return frame.idempotency_key or uuid.uuid4().hex

    @staticmethod
    def _rpc_params(frame: ActionFrame, target: BridgeTarget) -> Any:
        for name in ("rpc_params", "params"):
            found, value = target_parser.try_get_json(target, name)
            if found:
                return value

        params = frame.params
        if not isinstance(params, dict):
            return {}

        for name in ("rpc_params", "params", "body"):
            if name in params:
                return params[name]

        excluded = {"bridge_target", "rpc_method", "method", "id"}
        return {k: v for k, v in params.items() if k not in excluded}


def _build_jsonrpc_record(response: httpx.Response, body_text: str) -> dict[str, Any]:
    content_type = response.headers.get("content-type")
    record: dict[str, Any] = {
        "status_code": response.status_code,
        "success": 200 <= response.status_code < 300,
        "content_type": content_type,
        "headers": {k: v for k, v in response.headers.items()},
    }
    _write_jsonrpc_body(record, body_text, content_type)
    return record


def _write_jsonrpc_body(record: dict[str, Any], body_text: str, content_type: str | None) -> None:
    if body_text and content_type and "json" in content_type.lower():
        try:
            parsed = json.loads(body_text)
            record["jsonrpc_response"] = parsed
            if isinstance(parsed, dict):
                if "result" in parsed:
                    record["result"] = parsed["result"]
                if "error" in parsed:
                    record["error"] = parsed["error"]
            return
        except json.JSONDecodeError:
            pass
    record["body_text"] = body_text


class McpBridgeDispatcher(JsonRpcBridgeDispatcher):
    """Built-in Bridge dispatcher for MCP JSON-RPC servers over HTTP POST."""

    RESPONSE_ANCHOR_REF = "nps://bridge/mcp-jsonrpc-response/v1"

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(client, "tools/call", self.RESPONSE_ANCHOR_REF)

    @property
    def protocol(self) -> str:
        return BridgeProtocols.MCP


class A2aBridgeDispatcher(JsonRpcBridgeDispatcher):
    """Built-in Bridge dispatcher for A2A JSON-RPC endpoints over HTTP POST."""

    RESPONSE_ANCHOR_REF = "nps://bridge/a2a-jsonrpc-response/v1"

    def __init__(self, client: httpx.AsyncClient) -> None:
        super().__init__(client, "tasks/send", self.RESPONSE_ANCHOR_REF)

    @property
    def protocol(self) -> str:
        return BridgeProtocols.A2A


# ── Registry + facade ───────────────────────────────────────────────────────────

class BridgeDispatcherRegistry:
    """In-memory registry mapping bridge protocol identifiers to dispatchers."""

    def __init__(self, dispatchers: list[IBridgeDispatcher] | None = None) -> None:
        self._dispatchers: dict[str, IBridgeDispatcher] = {}
        for dispatcher in dispatchers or []:
            self.register(dispatcher)

    @staticmethod
    def create_default(client: httpx.AsyncClient) -> "BridgeDispatcherRegistry":
        """Create a registry with all built-in dispatchers: HTTP/HTTPS, gRPC JSON,
        MCP JSON-RPC, and A2A JSON-RPC."""
        return (
            BridgeDispatcherRegistry()
            .register(HttpBridgeDispatcher(client))
            .register(GrpcBridgeDispatcher(client))
            .register(McpBridgeDispatcher(client))
            .register(A2aBridgeDispatcher(client))
        )

    @property
    def protocols(self) -> list[str]:
        return list(self._dispatchers.keys())

    def register(self, dispatcher: IBridgeDispatcher) -> "BridgeDispatcherRegistry":
        if dispatcher is None:
            raise ValueError("dispatcher must not be None")
        if not dispatcher.protocol or not dispatcher.protocol.strip():
            raise ValueError("Bridge dispatcher protocol must not be empty.")
        self._dispatchers[dispatcher.protocol.lower()] = dispatcher
        return self

    def resolve(self, protocol: str) -> IBridgeDispatcher:
        if not protocol or not protocol.strip():
            raise BridgeDispatchException(
                BridgeErrorCodes.TARGET_INVALID, "bridge_target.protocol is required."
            )
        dispatcher = self._dispatchers.get(protocol.lower())
        if dispatcher is None:
            raise BridgeDispatchException(
                BridgeErrorCodes.PROTOCOL_UNSUPPORTED,
                f"Bridge protocol '{protocol}' is not registered.",
            )
        return dispatcher


class BridgeNode:
    """Stateless Bridge Node dispatcher facade. Host transports feed decoded
    :class:`ActionFrame` values here and write the returned :class:`CapsFrame`."""

    def __init__(self, dispatchers: BridgeDispatcherRegistry) -> None:
        if dispatchers is None:
            raise ValueError("dispatchers must not be None")
        self._dispatchers = dispatchers

    async def dispatch(self, frame: ActionFrame) -> CapsFrame:
        """Parse ``bridge_target``, resolve a protocol dispatcher, and invoke it."""
        if frame is None:
            raise ValueError("frame must not be None")
        target = target_parser.from_action_frame(frame)
        dispatcher = self._dispatchers.resolve(target.protocol)
        return await dispatcher.dispatch(frame, target)
