# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0
"""Options + action descriptors for inbound MCP/A2A Bridge server adapters
(port of .NET ``BridgeServerAction`` / ``BridgeServerOptions`` /
``IBridgeServerActionInvoker``)."""
from __future__ import annotations

import dataclasses
from typing import Any, Awaitable, Callable

from nps_sdk.ncp.frames import ErrorFrame
from nps_sdk.nwp.frames import ActionFrame
from nps_sdk.nwp.bridge.errors import BridgeErrorCodes

#: Dispatch delegate: ``(ActionFrame) -> Awaitable[IFrame]``.
BridgeServerActionDispatcher = Callable[[ActionFrame], Awaitable[Any]]

#: Per-request verifier: ``(agent_nid, headers) -> Awaitable[bool]``.
BridgeServerAgentVerifier = Callable[[str, dict[str, str]], Awaitable[bool]]


def to_tool_name(action_id: str) -> str:
    """Return a protocol-safe MCP tool name for an NPS action id."""
    if not action_id or not action_id.strip():
        return "action"
    chars = [ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in action_id.strip()]
    name = "".join(chars).strip("_")
    return name if name else "action"


@dataclasses.dataclass(frozen=True)
class BridgeServerAction:
    """Action exposed by inbound MCP/A2A Bridge server adapters."""

    action_id: str
    tool_name: str | None = None
    display_name: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    async_: bool = False
    tags: tuple[str, ...] | None = None

    @property
    def effective_tool_name(self) -> str:
        return to_tool_name(self.action_id) if not (self.tool_name and self.tool_name.strip()) else self.tool_name

    @property
    def effective_display_name(self) -> str:
        return self.action_id if not (self.display_name and self.display_name.strip()) else self.display_name


class BridgeServerOptions:
    """Options for inbound MCP/A2A Bridge server hosting."""

    def __init__(
        self,
        *,
        node_id: str = "nps-bridge-server",
        path_prefix: str = "",
        mcp_path: str = "/mcp",
        a2a_path: str = "/a2a",
        a2a_agent_card_path: str = "/.well-known/agent.json",
        require_auth: bool = True,
        verify_agent: BridgeServerAgentVerifier | None = None,
        server_name: str = "nps-bridge-server",
        server_version: str = "1.0.0-alpha.15",
        description: str | None = "NPS Bridge server ingress.",
        dispatch: BridgeServerActionDispatcher | None = None,
        max_request_body_bytes: int = 1 * 1024 * 1024,
        dispatch_timeout_ms: int = 30_000,
    ) -> None:
        self.node_id = node_id
        self.path_prefix = path_prefix
        self.mcp_path = mcp_path
        self.a2a_path = a2a_path
        self.a2a_agent_card_path = a2a_agent_card_path
        self.require_auth = require_auth
        self.verify_agent = verify_agent
        self.server_name = server_name
        self.server_version = server_version
        self.description = description
        self.dispatch = dispatch
        self.max_request_body_bytes = max_request_body_bytes
        self.dispatch_timeout_ms = dispatch_timeout_ms
        self.actions: list[BridgeServerAction] = []

    def add_action(
        self,
        action_id: str,
        *,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        tool_name: str | None = None,
        async_: bool = False,
        display_name: str | None = None,
        tags: tuple[str, ...] | None = None,
    ) -> "BridgeServerOptions":
        self.actions.append(
            BridgeServerAction(
                action_id=action_id,
                tool_name=tool_name,
                display_name=display_name,
                description=description,
                input_schema=input_schema,
                async_=async_,
                tags=tags,
            )
        )
        return self


class IBridgeServerActionInvoker:
    """Invokes local NPS actions for inbound Bridge server adapters."""

    async def invoke(self, frame: ActionFrame) -> Any:
        raise NotImplementedError


class BridgeServerActionInvoker(IBridgeServerActionInvoker):
    """Default invoker that delegates to ``options.dispatch``."""

    def __init__(self, options: BridgeServerOptions) -> None:
        self._options = options

    async def invoke(self, frame: ActionFrame) -> Any:
        if self._options.dispatch is None:
            return ErrorFrame(
                status="NPS-SERVER-NOT-IMPLEMENTED",
                error=BridgeErrorCodes.SERVER_DISPATCHER_MISSING,
                message=(
                    "BridgeServerOptions.dispatch must be configured before handling "
                    "inbound Bridge calls."
                ),
            )
        return await self._options.dispatch(frame)
