# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""
NPS NOP — Neural Orchestration Protocol frame dataclasses.

  TaskFrame        0x40 — DAG task definition submitted to an orchestrator/gateway.
  DelegateFrame    0x41 — subtask delegation sent by orchestrator to a worker agent.
  SyncFrame        0x42 — synchronization barrier (K-of-N wait).
  AlignStreamFrame 0x43 — directed result stream from worker back to orchestrator.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from nps_sdk.core.codec import NpsFrame
from nps_sdk.core.frames import EncodingTier, FrameType
from nps_sdk.nop.models import TaskContext, TaskDag, TaskPriority


# ── TaskFrame (0x40) ─────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class TaskFrame(NpsFrame):
    """
    DAG task definition frame (NPS-5 §4.1).

    Submitted to a Gateway Node or directly to an orchestrator to start a
    multi-agent DAG task. DelegateDepth=0 for root tasks; the orchestrator
    rejects frames with DelegateDepth >= 3 (NPS-5 §8.2).
    """

    task_id:        str
    dag:            TaskDag
    timeout_ms:     int     = 30_000      # max 3_600_000
    max_retries:    int     = 2
    priority:       str     = TaskPriority.NORMAL
    callback_url:   str | None = None     # must be https:// (SSRF protection)
    preflight:      bool    = False
    context:        TaskContext | None = None
    request_id:     str | None = None
    delegate_depth: int     = 0

    @property
    def frame_type(self) -> FrameType:
        return FrameType.TASK

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_id":        self.task_id,
            "dag":            self.dag.to_dict(),
            "timeout_ms":     self.timeout_ms,
            "max_retries":    self.max_retries,
            "priority":       self.priority,
            "preflight":      self.preflight,
            "delegate_depth": self.delegate_depth,
        }
        if self.callback_url is not None:
            d["callback_url"] = self.callback_url
        if self.context is not None:
            d["context"] = self.context.to_dict()
        if self.request_id is not None:
            d["request_id"] = self.request_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskFrame":
        ctx_raw = data.get("context")
        return cls(
            task_id=data["task_id"],
            dag=TaskDag.from_dict(data["dag"]),
            timeout_ms=int(data.get("timeout_ms", 30_000)),
            max_retries=int(data.get("max_retries", 2)),
            priority=data.get("priority", TaskPriority.NORMAL),
            callback_url=data.get("callback_url"),
            preflight=bool(data.get("preflight", False)),
            context=TaskContext.from_dict(ctx_raw) if ctx_raw else None,
            request_id=data.get("request_id"),
            delegate_depth=int(data.get("delegate_depth", 0)),
        )


# ── DelegateFrame (0x41) ─────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class DelegateFrame(NpsFrame):
    """
    Subtask delegation frame sent by the orchestrator to a Worker Agent (NPS-5 §4.2).

    ``action`` is a nwp:// URL, "preflight", or "cancel".
    ``delegate_depth`` = parent task's delegate_depth + 1.
    """

    parent_task_id:  str
    subtask_id:      str
    node_id:         str
    target_agent_nid: str
    action:          str          # nwp:// URL | "preflight" | "cancel"
    delegated_scope: Any
    deadline_at:     str          # ISO 8601 UTC
    params:          Any = None
    idempotency_key: str | None = None
    priority:        str | None = None
    context:         TaskContext | None = None
    delegate_depth:  int = 1

    @property
    def frame_type(self) -> FrameType:
        return FrameType.DELEGATE

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "parent_task_id":   self.parent_task_id,
            "subtask_id":       self.subtask_id,
            "node_id":          self.node_id,
            "target_agent_nid": self.target_agent_nid,
            "action":           self.action,
            "delegated_scope":  self.delegated_scope,
            "deadline_at":      self.deadline_at,
            "delegate_depth":   self.delegate_depth,
        }
        if self.params          is not None: d["params"]           = self.params
        if self.idempotency_key is not None: d["idempotency_key"]  = self.idempotency_key
        if self.priority        is not None: d["priority"]         = self.priority
        if self.context         is not None: d["context"]          = self.context.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DelegateFrame":
        ctx_raw = data.get("context")
        return cls(
            parent_task_id=data["parent_task_id"],
            subtask_id=data["subtask_id"],
            node_id=data["node_id"],
            target_agent_nid=data["target_agent_nid"],
            action=data["action"],
            delegated_scope=data["delegated_scope"],
            deadline_at=data["deadline_at"],
            params=data.get("params"),
            idempotency_key=data.get("idempotency_key"),
            priority=data.get("priority"),
            context=TaskContext.from_dict(ctx_raw) if ctx_raw else None,
            delegate_depth=int(data.get("delegate_depth", 1)),
        )


# ── SyncFrame (0x42) ─────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class SyncFrame(NpsFrame):
    """
    Synchronization barrier frame (NPS-5 §4.3).

    Waits for ``min_required`` of the listed subtasks to complete before
    the orchestrator proceeds. min_required=0 means all must complete.
    """

    task_id:      str
    sync_id:      str
    wait_for:     tuple[str, ...]     # subtask IDs to wait for
    min_required: int  = 0            # K-of-N; 0 = all
    aggregate:    str  = "merge"
    timeout_ms:   int | None = None

    @property
    def frame_type(self) -> FrameType:
        return FrameType.SYNC

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_id":      self.task_id,
            "sync_id":      self.sync_id,
            "wait_for":     list(self.wait_for),
            "min_required": self.min_required,
            "aggregate":    self.aggregate,
        }
        if self.timeout_ms is not None:
            d["timeout_ms"] = self.timeout_ms
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SyncFrame":
        return cls(
            task_id=data["task_id"],
            sync_id=data["sync_id"],
            wait_for=tuple(data.get("wait_for", [])),
            min_required=int(data.get("min_required", 0)),
            aggregate=data.get("aggregate", "merge"),
            timeout_ms=data.get("timeout_ms"),
        )


# ── StreamError ───────────────────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class StreamError:
    """Error payload inside a final AlignStreamFrame (NPS-5 §4.4)."""

    error_code: str
    message:    str

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": self.error_code, "message": self.message}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StreamError":
        return cls(error_code=data["error_code"], message=data["message"])


# ── AlignStreamFrame (0x43) ──────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class AlignStreamFrame(NpsFrame):
    """
    Directed task result stream frame (NPS-5 §4.4).

    Workers return intermediate and final results to the orchestrator via this
    frame. ``is_final=True`` marks the last chunk; if accompanied by ``error``,
    the subtask has failed.
    """

    stream_id:  str
    task_id:    str
    subtask_id: str
    seq:        int             # strictly monotonically increasing, 0-based
    is_final:   bool
    sender_nid: str
    data:       Any = None
    payload_ref: str | None = None
    window_size: int | None = None
    error:       StreamError | None = None   # non-null when is_final=True and failed

    @property
    def frame_type(self) -> FrameType:
        return FrameType.ALIGN_STREAM

    @property
    def preferred_tier(self) -> EncodingTier:
        return EncodingTier.MSGPACK

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "stream_id":  self.stream_id,
            "task_id":    self.task_id,
            "subtask_id": self.subtask_id,
            "seq":        self.seq,
            "is_final":   self.is_final,
            "sender_nid": self.sender_nid,
        }
        if self.data        is not None: d["data"]        = self.data
        if self.payload_ref is not None: d["payload_ref"] = self.payload_ref
        if self.window_size is not None: d["window_size"] = self.window_size
        if self.error       is not None: d["error"]       = self.error.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AlignStreamFrame":
        err_raw = data.get("error")
        return cls(
            stream_id=data["stream_id"],
            task_id=data["task_id"],
            subtask_id=data["subtask_id"],
            seq=int(data["seq"]),
            is_final=bool(data["is_final"]),
            sender_nid=data["sender_nid"],
            data=data.get("data"),
            payload_ref=data.get("payload_ref"),
            window_size=data.get("window_size"),
            error=StreamError.from_dict(err_raw) if err_raw else None,
        )
