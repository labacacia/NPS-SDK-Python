# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NOP DAG model dataclasses: TaskDag, DagNode, DagEdge, RetryPolicy, TaskContext.
Also exports TaskState, TaskPriority, AggregateStrategy constants.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from typing import Any


# ── Enums / constants ─────────────────────────────────────────────────────────

class TaskState(str, Enum):
    """Lifecycle state of a NOP task or subtask (NPS-5 §3.3)."""
    PENDING       = "pending"
    PREFLIGHT     = "preflight"
    RUNNING       = "running"
    WAITING_SYNC  = "waiting_sync"
    COMPLETED     = "completed"
    FAILED        = "failed"
    CANCELLED     = "cancelled"
    SKIPPED       = "skipped"


class TaskPriority:
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"


class AggregateStrategy:
    MERGE     = "merge"
    FIRST     = "first"
    ALL       = "all"
    FASTEST_K = "fastest_k"


class BackoffStrategy:
    FIXED       = "fixed"
    LINEAR      = "linear"
    EXPONENTIAL = "exponential"


# ── RetryPolicy ───────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class RetryPolicy:
    """Per-node retry policy (NPS-5 §3.2)."""

    max_retries:     int  = 2
    backoff:         str  = BackoffStrategy.EXPONENTIAL
    initial_delay_ms: int = 1000
    max_delay_ms:    int  = 30_000
    retry_on:        tuple[str, ...] = ()

    def compute_delay_ms(self, attempt: int) -> int:
        """Return the delay in ms for the given 1-based attempt number."""
        if self.backoff == BackoffStrategy.FIXED:
            delay = self.initial_delay_ms
        elif self.backoff == BackoffStrategy.LINEAR:
            delay = self.initial_delay_ms * attempt
        else:  # exponential
            delay = int(self.initial_delay_ms * (2 ** (attempt - 1)))
        return min(delay, self.max_delay_ms)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "max_retries":      self.max_retries,
            "backoff":          self.backoff,
            "initial_delay_ms": self.initial_delay_ms,
            "max_delay_ms":     self.max_delay_ms,
        }
        if self.retry_on:
            d["retry_on"] = list(self.retry_on)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetryPolicy":
        return cls(
            max_retries=int(data.get("max_retries", 2)),
            backoff=data.get("backoff", BackoffStrategy.EXPONENTIAL),
            initial_delay_ms=int(data.get("initial_delay_ms", 1000)),
            max_delay_ms=int(data.get("max_delay_ms", 30_000)),
            retry_on=tuple(data.get("retry_on", [])),
        )


# ── TaskContext ───────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class TaskContext:
    """Distributed tracing context forwarded through the delegation chain (NPS-5 §3.4)."""

    session_id:  str | None = None
    trace_id:    str | None = None
    span_id:     str | None = None
    trace_flags: int | None = None
    baggage:     dict[str, str] | None = None
    custom:      Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.session_id  is not None: d["session_id"]  = self.session_id
        if self.trace_id    is not None: d["trace_id"]    = self.trace_id
        if self.span_id     is not None: d["span_id"]     = self.span_id
        if self.trace_flags is not None: d["trace_flags"] = self.trace_flags
        if self.baggage     is not None: d["baggage"]     = self.baggage
        if self.custom      is not None: d["custom"]      = self.custom
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskContext":
        return cls(
            session_id=data.get("session_id"),
            trace_id=data.get("trace_id"),
            span_id=data.get("span_id"),
            trace_flags=data.get("trace_flags"),
            baggage=data.get("baggage"),
            custom=data.get("custom"),
        )


# ── DagEdge ───────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class DagEdge:
    """A directed edge From → To in the task DAG (NPS-5 §3.1)."""

    from_: str
    to:    str

    def to_dict(self) -> dict[str, Any]:
        return {"from": self.from_, "to": self.to}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DagEdge":
        return cls(from_=data["from"], to=data["to"])


# ── DagNode ───────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class DagNode:
    """
    A single vertex in the task DAG (NPS-5 §3.1).

    ``action`` is a nwp:// URL identifying the target operation.
    ``agent`` is the Worker NID that will execute the action.
    ``input_from`` lists upstream node IDs whose results are available as inputs.
    ``input_mapping`` maps parameter names to JSONPath expressions over upstream results.
    ``condition`` is a CEL-subset expression; the node is skipped when it evaluates to false.
    ``min_required`` is the K in K-of-N: minimum upstream nodes that must succeed (0 = all).
    """

    id:            str
    action:        str           # nwp:// URL
    agent:         str           # Worker NID
    input_from:    tuple[str, ...] = ()
    input_mapping: dict[str, str] | None = None   # param → JSONPath
    timeout_ms:    int | None = None
    retry_policy:  RetryPolicy | None = None
    condition:     str | None = None
    min_required:  int = 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id":     self.id,
            "action": self.action,
            "agent":  self.agent,
        }
        if self.input_from:
            d["input_from"] = list(self.input_from)
        if self.input_mapping is not None:
            d["input_mapping"] = self.input_mapping
        if self.timeout_ms is not None:
            d["timeout_ms"] = self.timeout_ms
        if self.retry_policy is not None:
            d["retry_policy"] = self.retry_policy.to_dict()
        if self.condition is not None:
            d["condition"] = self.condition
        if self.min_required:
            d["min_required"] = self.min_required
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DagNode":
        rp_raw = data.get("retry_policy")
        return cls(
            id=data["id"],
            action=data["action"],
            agent=data["agent"],
            input_from=tuple(data.get("input_from", [])),
            input_mapping=data.get("input_mapping"),
            timeout_ms=data.get("timeout_ms"),
            retry_policy=RetryPolicy.from_dict(rp_raw) if rp_raw else None,
            condition=data.get("condition"),
            min_required=int(data.get("min_required", 0)),
        )


# ── TaskDag ───────────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class TaskDag:
    """Complete DAG definition for a NOP task (NPS-5 §3.1)."""

    nodes: tuple[DagNode, ...]
    edges: tuple[DagEdge, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskDag":
        return cls(
            nodes=tuple(DagNode.from_dict(n) for n in data.get("nodes", [])),
            edges=tuple(DagEdge.from_dict(e) for e in data.get("edges", [])),
        )
