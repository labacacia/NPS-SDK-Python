# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
DAG validation (Kahn topo-sort + cycle detection + limits) and callback-URL
validation (https + SSRF guard) for the NOP orchestrator (NPS-5 §3.1.1, §8.4).
"""

from __future__ import annotations

import ipaddress
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from nps_sdk.nop.constants import NopConstants
from nps_sdk.nop.error_codes import (
    NOP_CONDITION_EVAL_ERROR,
    NOP_TASK_DAG_CYCLE,
    NOP_TASK_DAG_INVALID,
    NOP_TASK_DAG_TOO_LARGE,
)
from nps_sdk.nop.models import TaskDag


@dataclass(frozen=True)
class DagValidationResult:
    """Result of DAG validation."""

    is_valid: bool
    error_code: str | None = None
    error_message: str | None = None
    topological_order: list[str] | None = None

    @classmethod
    def success(cls, order: list[str]) -> "DagValidationResult":
        return cls(is_valid=True, topological_order=order)

    @classmethod
    def failure(cls, error_code: str, message: str) -> "DagValidationResult":
        return cls(is_valid=False, error_code=error_code, error_message=message)


class DagValidator:
    """Validates a :class:`TaskDag` against NPS-5 §3.1.1 rules."""

    @staticmethod
    def validate(dag: TaskDag) -> DagValidationResult:
        nodes = list(dag.nodes)
        if len(nodes) == 0:
            return DagValidationResult.failure(
                NOP_TASK_DAG_INVALID, "DAG must contain at least one node."
            )

        if len(nodes) > NopConstants.MAX_DAG_NODES:
            return DagValidationResult.failure(
                NOP_TASK_DAG_TOO_LARGE,
                f"DAG contains {len(nodes)} nodes, exceeding the maximum of "
                f"{NopConstants.MAX_DAG_NODES}.",
            )

        node_ids: set[str] = set()
        for node in nodes:
            if node.id in node_ids:
                return DagValidationResult.failure(
                    NOP_TASK_DAG_INVALID, f"Duplicate node ID: '{node.id}'."
                )
            node_ids.add(node.id)

        adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
        in_degree: dict[str, int] = {nid: 0 for nid in node_ids}

        for edge in dag.edges:
            if edge.from_ not in node_ids:
                return DagValidationResult.failure(
                    NOP_TASK_DAG_INVALID,
                    f"Edge references unknown source node: '{edge.from_}'.",
                )
            if edge.to not in node_ids:
                return DagValidationResult.failure(
                    NOP_TASK_DAG_INVALID,
                    f"Edge references unknown target node: '{edge.to}'.",
                )
            adjacency[edge.from_].append(edge.to)
            in_degree[edge.to] += 1

        # input_from references must be known nodes
        for node in nodes:
            for upstream in node.input_from:
                if upstream not in node_ids:
                    return DagValidationResult.failure(
                        NOP_TASK_DAG_INVALID,
                        f"Node '{node.id}' references unknown upstream node "
                        f"'{upstream}' in input_from.",
                    )

        # At least one start node (no incoming edges)
        if not any(d == 0 for d in in_degree.values()):
            return DagValidationResult.failure(
                NOP_TASK_DAG_INVALID,
                "DAG must have at least one start node (no incoming edges).",
            )

        # At least one end node (no outgoing edges)
        if not any(len(lst) == 0 for lst in adjacency.values()):
            return DagValidationResult.failure(
                NOP_TASK_DAG_INVALID,
                "DAG must have at least one end node (no outgoing edges).",
            )

        # Condition expression length
        for node in nodes:
            if node.condition is not None and len(node.condition) > NopConstants.MAX_CONDITION_LENGTH:
                return DagValidationResult.failure(
                    NOP_CONDITION_EVAL_ERROR,
                    f"Node '{node.id}' condition expression exceeds "
                    f"{NopConstants.MAX_CONDITION_LENGTH} characters.",
                )

        # Kahn's algorithm: topological sort + cycle detection
        remaining = dict(in_degree)
        queue: deque[str] = deque(nid for nid, d in in_degree.items() if d == 0)
        sorted_order: list[str] = []
        while queue:
            cur = queue.popleft()
            sorted_order.append(cur)
            for neighbor in adjacency[cur]:
                remaining[neighbor] -= 1
                if remaining[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_order) != len(node_ids):
            return DagValidationResult.failure(
                NOP_TASK_DAG_CYCLE, "DAG contains a cycle."
            )

        return DagValidationResult.success(sorted_order)


class NopCallbackValidator:
    """Validates ``TaskFrame.callback_url`` per NPS-5 §8.4."""

    @staticmethod
    def validate_callback_url(callback_url: str | None) -> str | None:
        """Return ``None`` when valid; otherwise a human-readable error string."""
        if not callback_url or not callback_url.strip():
            return "callback_url must not be empty."

        parts = urlsplit(callback_url)
        if not parts.scheme or not parts.netloc:
            return f"callback_url '{callback_url}' is not a valid absolute URI."

        if parts.scheme.lower() != "https":
            return (
                f"callback_url MUST use the https:// scheme "
                f"(got '{parts.scheme}://')."
            )

        host = parts.hostname or ""
        if NopCallbackValidator.is_private_host(host):
            return (
                f"callback_url host '{host}' resolves to a private or "
                f"loopback address (SSRF guard)."
            )

        return None

    @staticmethod
    def is_private_host(host: str) -> bool:
        """
        True when ``host`` is a well-known private / loopback / link-local
        address or hostname, without performing DNS resolution.
        """
        if not host:
            return True

        if host.lower() == "localhost":
            return True

        stripped = host.strip("[]")  # IPv6 URI form: [::1]
        try:
            ip = ipaddress.ip_address(stripped)
        except ValueError:
            return False

        return NopCallbackValidator._is_private_ip(ip)

    @staticmethod
    def _is_private_ip(ip: "ipaddress._BaseAddress") -> bool:
        # Normalize IPv4-mapped IPv6 (::ffff:10.0.0.1) to IPv4.
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped

        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
        )
