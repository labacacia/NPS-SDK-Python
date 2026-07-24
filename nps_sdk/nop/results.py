# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""Terminal result types returned by the NOP orchestrator (NPS-5 §5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from nps_sdk.nop.error_codes import NOP_TASK_CANCELLED
from nps_sdk.nop.models import TaskState


@dataclass(frozen=True)
class SagaCompensationResult:
    """Summary of a Saga compensation run (NPS-5 §3.5)."""

    attempted: int
    succeeded: int
    failed: int
    failed_node_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NopTaskResult:
    """Final result returned by :meth:`NopOrchestrator.execute` (NPS-5 §5)."""

    task_id: str
    final_state: TaskState
    aggregated_result: Any = None
    error_code: str | None = None
    error_message: str | None = None
    node_results: Mapping[str, Any] = field(default_factory=dict)
    compensation: SagaCompensationResult | None = None

    @classmethod
    def success(
        cls,
        task_id: str,
        aggregated_result: Any,
        node_results: Mapping[str, Any],
        compensation: SagaCompensationResult | None = None,
    ) -> "NopTaskResult":
        return cls(
            task_id=task_id,
            final_state=TaskState.COMPLETED,
            aggregated_result=aggregated_result,
            node_results=dict(node_results),
            compensation=compensation,
        )

    @classmethod
    def failure(
        cls,
        task_id: str,
        error_code: str,
        error_message: str,
        compensation: SagaCompensationResult | None = None,
    ) -> "NopTaskResult":
        return cls(
            task_id=task_id,
            final_state=TaskState.FAILED,
            error_code=error_code,
            error_message=error_message,
            compensation=compensation,
        )

    @classmethod
    def cancelled(cls, task_id: str, reason: str) -> "NopTaskResult":
        return cls(
            task_id=task_id,
            final_state=TaskState.CANCELLED,
            error_code=NOP_TASK_CANCELLED,
            error_message=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a snake_case dict (used as the callback POST body)."""
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "final_state": self.final_state.value,
            "node_results": dict(self.node_results),
        }
        if self.aggregated_result is not None:
            d["aggregated_result"] = self.aggregated_result
        if self.error_code is not None:
            d["error_code"] = self.error_code
        if self.error_message is not None:
            d["error_message"] = self.error_message
        if self.compensation is not None:
            d["compensation"] = {
                "attempted": self.compensation.attempted,
                "succeeded": self.compensation.succeeded,
                "failed": self.compensation.failed,
                "failed_node_ids": list(self.compensation.failed_node_ids),
            }
        return d
