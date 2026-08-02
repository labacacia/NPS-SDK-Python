# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
Transport-independent configuration of a Bridge Node's inbound surface
(NWP §2.1 inbound profile, NPS-CR-0010).

This type deliberately knows nothing about HTTP. The inbound protocol servers are
written against it alone, so they never touch a request context and can be driven from
stdio, from a unit test, or from any host binding. Hosting concerns — paths, an
HTTP-context-bound auth verifier, body limits — belong to the hosting layer.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Sequence

from nps_sdk.nwp.inbound.backend import (
    HttpNwpBackend,
    InProcessNwpBackend,
    NwpActionDescriptor,
    NwpActionDispatcher,
    NwpBackend,
    NwpNodeDescriptor,
    NwpNodeRole,
    NwpQueryDispatcher,
    NwpUpstream,
)

__all__ = ["BridgeServerAction", "BridgeInboundOptions", "create_backends"]


@dataclasses.dataclass(frozen=True)
class BridgeServerAction:
    """An action exposed by an inbound Bridge as an MCP tool / A2A skill."""

    action_id: str
    display_name: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    async_: bool = False
    tags: tuple[str, ...] | None = None

    @property
    def effective_display_name(self) -> str:
        return self.display_name.strip() if (self.display_name or "").strip() else self.action_id


@dataclasses.dataclass
class BridgeInboundOptions:
    """Declared inbound surface of a Bridge Node."""

    #: Identifier of the co-hosted node. Namespaces its MCP resource URIs and tool names.
    node_id: str = "nps-bridge-server"
    #: Server name returned by MCP ``initialize`` and the A2A AgentCard.
    server_name: str = "nps-bridge-server"
    server_version: str = "1.0.0-alpha.17"
    description: str | None = "NPS Bridge Node inbound surface."
    #: Role of the co-hosted node projected by the in-process backend. Action is the only
    #: shape the pre-CR-0010 Bridge server supported; set COMPLEX/MEMORY together with
    #: ``query_dispatcher`` to project resources as well as tools.
    node_role: NwpNodeRole = NwpNodeRole.ACTION
    actions: list[BridgeServerAction] = dataclasses.field(default_factory=list)
    #: Local NPS action dispatcher. Required for a co-hosted invokable node.
    action_dispatcher: NwpActionDispatcher | None = None
    #: Local NPS query dispatcher. Leaving it unset is conformant — the resource methods
    #: are still served, over an empty set (NWP §16.1.2).
    query_dispatcher: NwpQueryDispatcher | None = None
    #: Remote NWP nodes fronted over HTTP. May be combined with a co-hosted node.
    upstreams: list[NwpUpstream] = dataclasses.field(default_factory=list)
    #: Max rows a single MCP ``resources/read`` may pull from a Memory Node.
    resource_read_limit: int = 100
    #: The NDP ``bridge_inbound_protocols`` set. Note gRPC is NOT in the default, so the
    #: gRPC service refuses until "grpc" is added (NWP §16.1.2 MUST-5).
    inbound_protocols: list[str] = dataclasses.field(
        default_factory=lambda: ["mcp", "a2a"])
    #: Advertised on the A2A AgentCard, so it is part of the protocol surface rather
    #: than merely host configuration.
    require_auth: bool = True

    def add_action(
        self,
        action_id: str,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        async_: bool = False,
        display_name: str | None = None,
        tags: tuple[str, ...] | None = None,
    ) -> "BridgeInboundOptions":
        """Add an exposed local action; returns ``self`` for chaining."""
        self.actions.append(BridgeServerAction(
            action_id=action_id, display_name=display_name, description=description,
            input_schema=input_schema, async_=async_, tags=tags))
        return self

    def serves_inbound(self, protocol: str) -> bool:
        """Whether this Bridge Node declares *protocol* on its inbound surface."""
        return any(p.lower() == protocol.lower() for p in self.inbound_protocols)

    def declared_protocols_hint(self) -> dict[str, list[str]]:
        """Both declared arrays, for the §16.1.2 MUST-5 SHOULD-clause ``hint``."""
        return {
            "bridge_inbound_protocols": list(self.inbound_protocols),
            "bridge_protocols": [],
        }


def create_backends(
    options: BridgeInboundOptions,
    http_client: Any | None = None,
) -> Sequence[NwpBackend]:
    """Materialise the backends declared by *options*.

    Note the ``actions`` term: a deployment that declares actions but forgets the
    dispatcher still gets an in-process backend, so its tools appear in ``tools/list``
    and a call fails loudly with ``NWP-BRIDGE-SERVER-DISPATCHER-MISSING``. Omitting the
    backend instead would make a misconfiguration look like "this node exposes nothing".
    """
    backends: list[NwpBackend] = []

    if (options.action_dispatcher is not None
            or options.query_dispatcher is not None
            or options.actions):
        backends.append(InProcessNwpBackend(
            NwpNodeDescriptor(
                name=options.node_id,
                role=options.node_role,
                display_name=options.server_name,
                description=options.description,
            ),
            [
                NwpActionDescriptor(
                    action_id=a.action_id,
                    description=a.description,
                    input_schema=a.input_schema,
                    async_=a.async_,
                    tags=a.tags,
                )
                for a in options.actions
            ],
            options.action_dispatcher,
            options.query_dispatcher,
        ))

    if options.upstreams:
        if http_client is None:
            raise ValueError(
                "BridgeInboundOptions.upstreams is non-empty but no HTTP client was "
                "supplied. An HTTP-fronted Bridge Node needs a client to reach its "
                "upstream nodes.")
        for upstream in options.upstreams:
            backends.append(HttpNwpBackend(http_client, upstream))

    return backends
