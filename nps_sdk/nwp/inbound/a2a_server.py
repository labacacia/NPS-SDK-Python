# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Inbound A2A server surface of a Bridge Node (NWP §2.1 inbound profile, §16.1.2).

Projects the Action / Complex Nodes behind one or more backends onto A2A skills.
Exactly one JSON-RPC method is served — ``tasks/send`` — plus the AgentCard at
``/.well-known/agent.json``.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Sequence

from nps_sdk.core.status_codes import NPS_SERVER_UNSUPPORTED
from nps_sdk.nwp.inbound.backend import (
    NwpActionDescriptor,
    NwpBackend,
    NwpNodeDescriptor,
    NwpResult,
)
from nps_sdk.nwp.inbound.error_map import (
    BridgeErrorCodes,
    BridgeErrorMap,
    BridgeJsonRpcErrorCodes,
)
from nps_sdk.nwp.inbound.jsonrpc import (
    BridgeJsonRpcRequest,
    BridgeJsonRpcResponse,
    jsonrpc_error,
    jsonrpc_success,
)
from nps_sdk.nwp.inbound.mcp_server import McpToolName
from nps_sdk.nwp.inbound.options import BridgeInboundOptions

__all__ = ["A2aInboundServer", "A2A_AGENT_CARD_PATH"]

#: Where the AgentCard is served.
A2A_AGENT_CARD_PATH = "/.well-known/agent.json"

#: Metadata keys, in priority order, that may name the requested skill.
_SKILL_KEYS = ("action_id", "actionId", "skill_id", "skillId", "skill")
#: Metadata keys, in priority order, that may carry the action arguments.
_PARAM_KEYS = ("params", "arguments")


class A2aInboundServer:
    """Serves the A2A inbound surface over any set of NWP backends."""

    def __init__(self, options: BridgeInboundOptions, backends: Sequence[NwpBackend]) -> None:
        self._options = options
        self._backends = list(backends)

    # ── AgentCard ────────────────────────────────────────────────────────────

    async def build_agent_card(self, endpoint_url: str) -> dict[str, Any]:
        """Build the AgentCard advertising every skill this Bridge Node fronts."""
        skills: list[dict[str, Any]] = []
        for backend, descriptor in await self._invokable_backends():
            for action in await backend.get_actions():
                skills.append({
                    # Qualified on output, always — see NPS-CR-0010 §5.1.
                    "id": McpToolName.encode(descriptor.name, action.action_id),
                    "name": action.description or action.action_id,
                    "description": action.description,
                    "tags": list(action.tags) if action.tags else None,
                    "inputModes": ["text", "data"],
                    "outputModes": ["data"],
                })

        return {
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
            "authentication": (
                {"schemes": ["apikey"], "credentials": "X-NWP-Agent"}
                if self._options.require_auth else None
            ),
            "skills": skills,
        }

    # ── dispatch ─────────────────────────────────────────────────────────────

    async def dispatch(self, request: BridgeJsonRpcRequest) -> BridgeJsonRpcResponse:
        """Dispatch one A2A JSON-RPC request."""
        if request is None:
            raise ValueError("request is required")

        # §16.1.2 MUST-5, checked first thing.
        if not self._options.serves_inbound("a2a"):
            return jsonrpc_error(
                request,
                BridgeErrorMap.to_json_rpc(NPS_SERVER_UNSUPPORTED),
                'This Bridge Node does not declare "a2a" in bridge_inbound_protocols.',
                {"error": BridgeErrorCodes.DIRECTION_UNSUPPORTED,
                 "hint": self._options.declared_protocols_hint()})

        if request.method == "tasks/send":
            return await self._send_task(request)
        return jsonrpc_error(
            request, BridgeJsonRpcErrorCodes.METHOD_NOT_FOUND,
            f"A2A method '{request.method}' is not supported by this Bridge Node.",
            {"error": BridgeErrorCodes.DIRECTION_UNSUPPORTED})

    async def _send_task(self, request: BridgeJsonRpcRequest) -> BridgeJsonRpcResponse:
        task = request.params
        if not isinstance(task, dict):
            return jsonrpc_error(request, BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                                 "A2A tasks/send requires a params object.")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            return jsonrpc_error(request, BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                                 "A2A tasks/send params.id is required.")

        resolved = await self._resolve_action(task)
        if resolved is None:
            return jsonrpc_error(
                request, BridgeJsonRpcErrorCodes.INVALID_PARAMS,
                "A2A task metadata must identify an exposed NPS action when more "
                "than one is available.",
                {"error": BridgeErrorCodes.SERVER_TOOL_NOT_FOUND,
                 "candidates": await self._qualified_skill_ids()})

        backend, action = resolved
        result = await backend.invoke(
            action.action_id, _extract_action_params(task), action.async_)

        # §16.3: an auth / limit / unsupported failure MUST surface as a protocol-level
        # error. Reporting it as a *task* — even a failed one — hands the peer a task
        # object where it should have received a transport error, and A2A peers retry
        # failed tasks. A Bridge MUST NOT silently downgrade an error to a completed task.
        if not result.ok and BridgeErrorMap.must_be_protocol_error(result.nps_status):
            return jsonrpc_error(
                request,
                BridgeErrorMap.to_json_rpc(result.nps_status),
                result.message or result.nps_status or "NWP dispatch failed.",
                {"status": result.nps_status, "error": result.nwp_error})

        return jsonrpc_success(request, _to_task(task, result))

    # ── resolution ───────────────────────────────────────────────────────────

    async def _resolve_action(
        self, task: dict[str, Any],
    ) -> tuple[NwpBackend, NwpActionDescriptor] | None:
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

        candidates: list[tuple[NwpBackend, NwpActionDescriptor]] = []
        for backend, descriptor in await self._invokable_backends():
            for action in await backend.get_actions():
                if not requested:
                    candidates.append((backend, action))
                    continue
                encoded = McpToolName.encode(descriptor.name, action.action_id)
                if (encoded.lower() == requested.lower()
                        or action.action_id.lower() == requested.lower()):
                    return backend, action

        # No skill named: unambiguous only when exactly one action is exposed in total.
        return candidates[0] if len(candidates) == 1 else None

    async def _invokable_backends(
        self,
    ) -> list[tuple[NwpBackend, NwpNodeDescriptor]]:
        out: list[tuple[NwpBackend, NwpNodeDescriptor]] = []
        for backend in self._backends:
            descriptor = await backend.get_descriptor()
            if descriptor.is_invokable:
                out.append((backend, descriptor))
        return out

    async def _qualified_skill_ids(self) -> list[str]:
        ids: list[str] = []
        for backend, descriptor in await self._invokable_backends():
            for action in await backend.get_actions():
                ids.append(McpToolName.encode(descriptor.name, action.action_id))
        return sorted(ids)


# ── Projection ────────────────────────────────────────────────────────────────

def _to_task(request: dict[str, Any], result: NwpResult) -> dict[str, Any]:
    timestamp = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    payload = result.payload if result.ok else result.failure_payload()
    if result.ok and payload is None:
        payload = {}

    return {
        "id": request.get("id"),
        "sessionId": request.get("sessionId"),
        "status": {
            "state": "completed" if result.ok else "failed",
            "timestamp": timestamp,
            "message": None if result.ok else {
                "role": "agent",
                "parts": [{
                    "type": "text",
                    # §16.3: the NPS code is preserved verbatim in the failure detail.
                    "text": (result.message or result.nwp_error
                             or result.nps_status or "NPS action failed."),
                }],
            },
        },
        "artifacts": [{
            "name": "nps-result" if result.ok else "nps-error",
            "parts": [{"type": "data", "data": payload}],
            "index": 0,
        }],
        "history": [request.get("message")],
    }


def _extract_action_params(task: dict[str, Any]) -> Any:
    """Find the action arguments, in the normative order."""
    message = task.get("message") if isinstance(task.get("message"), dict) else {}
    for source in (task.get("metadata"), message.get("metadata")):
        found = _try_get(source, _PARAM_KEYS)
        if found is not None:
            return found

    for part in _parts(message):
        nested = _try_get(part.get("data"), _PARAM_KEYS)
        if nested is not None:
            return nested
        part_type = str(part.get("type") or "").lower()
        if part_type == "data" and part.get("data") is not None:
            return part.get("data")
        if part_type == "text" and (part.get("text") or "").strip():
            return {"text": part.get("text")}
    return None


def _parts(message: Any) -> list[dict[str, Any]]:
    if not isinstance(message, dict):
        return []
    parts = message.get("parts")
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


def _try_get(source: Any, names: Sequence[str]) -> Any:
    if not isinstance(source, dict):
        return None
    for name in names:
        if name in source:
            return source[name]
    return None


def _try_get_string(source: Any, names: Sequence[str]) -> str | None:
    value = _try_get(source, names)
    return value if isinstance(value, str) else None


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value and value.strip():
            return value
    return None
