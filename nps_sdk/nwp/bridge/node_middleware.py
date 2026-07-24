# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Pure-ASGI middleware hosting an outbound Bridge Node
(port of .NET ``BridgeNodeMiddleware`` / ``BridgeNodeOptions``).

Endpoints (under ``options.path_prefix``):

  GET  /.nwm     — Neural Web Manifest (node_type=bridge)
  GET  /actions  — declared bridge dispatch action
  POST /invoke   — ActionFrame → BridgeNode.dispatch → CapsFrame
"""
from __future__ import annotations

import json
from typing import Any, Callable

from nps_sdk.nwp import http_headers
from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.bridge.dispatchers import BridgeDispatcherRegistry, BridgeNode
from nps_sdk.nwp.bridge.errors import BridgeDispatchException, BridgeErrorCodes
from nps_sdk.nwp.bridge.types import NODE_TYPE_BRIDGE


class BridgeNodeOptions:
    """ASGI-hosted Bridge Node options."""

    def __init__(
        self,
        *,
        node_id: str = "nps-bridge",
        path_prefix: str = "",
        action_id: str = "bridge.dispatch",
        require_auth: bool = False,
    ) -> None:
        self.node_id = node_id
        self.path_prefix = path_prefix
        self.action_id = action_id
        self.require_auth = require_auth


class BridgeNodeMiddleware:
    """Pure-ASGI Bridge Node app exposing ``/.nwm``, ``/actions``, ``/invoke``."""

    def __init__(self, bridge: BridgeNode, registry: BridgeDispatcherRegistry, options: BridgeNodeOptions) -> None:
        self._bridge = bridge
        self._registry = registry
        self._options = options
        self._prefix = options.path_prefix.rstrip("/")

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            return

        path = scope.get("path", "")
        if not path.startswith(self._prefix):
            await self._send_json(send, 404, {"status": "NPS-CLIENT-NOT-FOUND", "error": BridgeErrorCodes.TARGET_INVALID, "message": "no bridge node at this path."})
            return

        sub = path[len(self._prefix):]
        method = scope.get("method", "GET")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

        if sub in ("/.nwm", "/.nwm/"):
            await self._send_json(send, 200, self._build_manifest(), http_headers.MIME_MANIFEST)
        elif sub in ("/actions", "/actions/"):
            await self._send_json(send, 200, self._build_actions())
        elif sub in ("/invoke", "/invoke/"):
            if method != "POST":
                await self._send_json(send, 405, {}, )
                return
            await self._handle_invoke(receive, send, headers)
        else:
            await self._send_json(send, 404, {"status": "NPS-CLIENT-NOT-FOUND", "error": BridgeErrorCodes.TARGET_INVALID, "message": "no bridge node at this path."})

    async def _handle_invoke(self, receive: Callable, send: Callable, headers: dict[str, str]) -> None:
        if self._options.require_auth and http_headers.AGENT.lower() not in headers:
            await self._send_error(send, 401, "NPS-CLIENT-UNAUTHORIZED", "NWP-BRIDGE-AUTH-REQUIRED", "X-NWP-Agent header is required.")
            return

        try:
            raw = await _read_body(receive)
            frame = ActionFrame.from_dict(json.loads(raw or b"{}"))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            await self._send_error(send, 400, "NPS-CLIENT-BAD-REQUEST", BridgeErrorCodes.TARGET_INVALID, str(exc))
            return

        if frame.action_id != self._options.action_id:
            await self._send_error(send, 404, "NPS-CLIENT-NOT-FOUND", "NWP-BRIDGE-ACTION-NOT-FOUND", f"Unknown bridge action '{frame.action_id}'.")
            return

        try:
            caps = await self._bridge.dispatch(frame)
            await self._send_json(send, 200, caps.to_dict())
        except BridgeDispatchException as exc:
            status = 502 if exc.error_code == BridgeErrorCodes.UPSTREAM_FAILED else 400
            nps_status = "NPS-SERVER-UPSTREAM-FAILED" if status == 502 else "NPS-CLIENT-BAD-REQUEST"
            await self._send_error(send, status, nps_status, exc.error_code, exc.message)
        except Exception as exc:  # noqa: BLE001
            await self._send_error(send, 500, "NPS-SERVER-ERROR", BridgeErrorCodes.UPSTREAM_FAILED, str(exc))

    def _build_manifest(self) -> dict[str, Any]:
        return {
            "node_type": NODE_TYPE_BRIDGE,
            "node_id": self._options.node_id,
            "bridge_protocols": sorted(self._registry.protocols, key=str.lower),
            "actions": [self._options.action_id],
        }

    def _build_actions(self) -> list[dict[str, Any]]:
        return [
            {
                "action_id": self._options.action_id,
                "description": "Dispatch an NWP ActionFrame to an external Bridge target.",
                "bridge_protocols": sorted(self._registry.protocols, key=str.lower),
            }
        ]

    async def _send_json(self, send: Callable, status: int, body: Any, content_type: str = "application/json") -> None:
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", content_type.encode("latin-1"))]})
        await send({"type": "http.response.body", "body": payload})

    async def _send_error(self, send: Callable, status: int, nps_status: str, error: str, message: str) -> None:
        await self._send_json(send, status, {"status": nps_status, "error": error, "message": message})


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
