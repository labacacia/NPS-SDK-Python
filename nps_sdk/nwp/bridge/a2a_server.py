# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Inbound A2A adapter exposing local NPS actions as A2A skills
(port of .NET ``A2aServerBridge``)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nps_sdk.ncp.frames import ErrorFrame
from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.bridge import frame_json, json_rpc
from nps_sdk.nwp.bridge.errors import BridgeErrorCodes
from nps_sdk.nwp.bridge.json_rpc import (
    BridgeJsonRpcErrorCodes,
    BridgeJsonRpcRequest,
    BridgeJsonRpcResponse,
)
from nps_sdk.nwp.bridge.server_options import (
    BridgeServerAction,
    BridgeServerOptions,
    IBridgeServerActionInvoker,
)
from nps_sdk.nwp.bridge.server_types import A2aTaskState

_SKILL_KEYS = ("action_id", "actionId", "skill_id", "skillId", "skill")
_PARAM_KEYS = ("params", "arguments")


class A2aServerBridge:
    """Inbound A2A adapter that exposes local NPS actions as A2A skills."""

    def __init__(self, options: BridgeServerOptions, invoker: IBridgeServerActionInvoker) -> None:
        self._options = options
        self._invoker = invoker

    def build_agent_card(self, endpoint_url: str) -> dict[str, Any]:
        """Build the A2A AgentCard for the hosted Bridge server."""
        card: dict[str, Any] = {
            "name": self._options.server_name,
            "description": self._options.description,
            "url": endpoint_url,
            "provider": {
                "organization": "LabAcacia / INNO LOTUS PTY LTD",
                "url": "https://github.com/labacacia/nps",
            },
            "version": self._options.server_version,
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "stateTransitionHistory": False,
            },
            "defaultInputModes": ["text", "data"],
            "defaultOutputModes": ["text", "data"],
            "skills": [
                {
                    "id": action.action_id,
                    "name": action.effective_display_name,
                    "description": action.description,
                    "tags": list(action.tags) if action.tags else None,
                    "inputModes": ["text", "data"],
                    "outputModes": ["data"],
                }
                for action in self._options.actions
            ],
        }
        if self._options.require_auth:
            card["authentication"] = {"schemes": ["apikey"], "credentials": "X-NWP-Agent"}
        else:
            card["authentication"] = None
        return card

    async def dispatch(self, request: BridgeJsonRpcRequest) -> BridgeJsonRpcResponse:
        if request is None:
            raise ValueError("request must not be None")

        if request.method == "tasks/send":
            return await self._send_task(request)
        return json_rpc.error(
            request,
            BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND,
            f"A2A method '{request.method}' is not supported by NWP Bridge server.",
        )

    async def _send_task(self, request: BridgeJsonRpcRequest) -> BridgeJsonRpcResponse:
        task = request.params
        if not isinstance(task, dict):
            return json_rpc.error(
                request, BridgeJsonRpcErrorCodes.INVALID_PARAMS, "A2A tasks/send requires params."
            )

        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            return json_rpc.error(
                request,
                BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                "A2A tasks/send params.id is required.",
            )

        action = self._resolve_action(task)
        if action is None:
            return json_rpc.error(
                request,
                BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                "A2A task metadata must identify an exposed NPS action when multiple actions exist.",
                data={"error": BridgeErrorCodes.SERVER_TOOL_NOT_FOUND},
            )

        frame = ActionFrame(
            action_id=action.action_id,
            params=_extract_action_params(task),
            async_=action.async_,
            idempotency_key=None,
        )
        # Mirror .NET RequestId = task.Id semantics via idempotency_key fallback is not
        # used here; ActionFrame has no request_id field, so the A2A task id round-trips
        # through the response envelope instead.

        try:
            result = await self._invoker.invoke(frame)
            return json_rpc.success(request, _to_task(task, result))
        except Exception as exc:  # noqa: BLE001 - report as failed A2A task
            return json_rpc.success(
                request,
                _to_task(
                    task,
                    ErrorFrame(
                        status="NPS-SERVER-ERROR",
                        error=BridgeErrorCodes.SERVER_DISPATCH_FAILED,
                        message=str(exc),
                    ),
                ),
            )

    def _resolve_action(self, task: dict[str, Any]) -> BridgeServerAction | None:
        message = task.get("message") if isinstance(task.get("message"), dict) else {}
        requested = _first_non_empty(
            _try_get_string(task.get("metadata"), _SKILL_KEYS),
            _try_get_string(message.get("metadata"), _SKILL_KEYS),
        )

        if not requested:
            for part in _parts(message):
                requested = _first_non_empty(
                    _try_get_string(part.get("metadata"), _SKILL_KEYS),
                    _try_get_string(part.get("data"), _SKILL_KEYS),
                )
                if requested:
                    break

        if not requested and len(self._options.actions) == 1:
            return self._options.actions[0]

        if requested is None:
            return None

        lowered = requested.lower()
        for action in self._options.actions:
            if action.action_id.lower() == lowered or action.effective_tool_name.lower() == lowered:
                return action
        return None


def _extract_action_params(task: dict[str, Any]) -> Any:
    message = task.get("message") if isinstance(task.get("message"), dict) else {}
    from_metadata = _try_get_element(task.get("metadata"), _PARAM_KEYS)
    if from_metadata is None:
        from_metadata = _try_get_element(message.get("metadata"), _PARAM_KEYS)
    if from_metadata is not None:
        return from_metadata

    for part in _parts(message):
        nested = _try_get_element(part.get("data"), _PARAM_KEYS)
        if nested is not None:
            return nested

        part_type = str(part.get("type", ""))
        if part_type.lower() == "data" and part.get("data") is not None:
            return part["data"]

        if part_type.lower() == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return {"text": text}

    return None


def _to_task(request: dict[str, Any], frame: Any) -> dict[str, Any]:
    is_error = isinstance(frame, ErrorFrame)
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = frame_json.to_element(frame)

    status: dict[str, Any] = {
        "state": A2aTaskState.FAILED if is_error else A2aTaskState.COMPLETED,
        "timestamp": timestamp,
    }
    if is_error:
        text = frame.message or frame.error if isinstance(frame, ErrorFrame) else "NPS action failed."
        status["message"] = {
            "role": "agent",
            "parts": [{"type": "text", "text": text}],
        }

    task: dict[str, Any] = {
        "id": request["id"],
        "sessionId": request.get("sessionId"),
        "status": status,
        "artifacts": [
            {
                "name": "nps-error" if is_error else "nps-result",
                "parts": [{"type": "data", "data": payload}],
                "index": 0,
            }
        ],
        "history": [request.get("message")],
    }
    return task


def _parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    parts = message.get("parts")
    if isinstance(parts, list):
        return [p for p in parts if isinstance(p, dict)]
    return []


def _try_get_string(source: Any, names: tuple[str, ...]) -> str | None:
    value = _try_get_element(source, names)
    return value if isinstance(value, str) else None


def _try_get_element(source: Any, names: tuple[str, ...]) -> Any:
    if not isinstance(source, dict):
        return None
    for name in names:
        if name in source:
            return source[name]
    return None


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value.strip():
            return value
    return None
