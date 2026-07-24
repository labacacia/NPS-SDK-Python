# Copyright 2026 INNO LOTUS PTY LTD
# SPDX-License-Identifier: Apache-2.0

"""NPS.NOP — Neural Orchestration Protocol: DAG tasks, delegation, and result streaming."""

from nps_sdk.nop.models import (
    AggregateStrategy,
    BackoffStrategy,
    CompensationPolicy,
    DagEdge,
    DagNode,
    RetryPolicy,
    TaskContext,
    TaskDag,
    TaskPriority,
    TaskState,
)
from nps_sdk.nop.frames import (
    AlignStreamFrame,
    DelegateFrame,
    StreamError,
    SyncFrame,
    TaskFrame,
)
from nps_sdk.nop.client import NopClient, NopTaskStatus
from nps_sdk.nop import error_codes
from nps_sdk.nop.error_codes import NOP_ERROR_TO_NPS_STATUS

# ── Server-side orchestration engine ──────────────────────────────────────────
from nps_sdk.nop.constants import NopConstants
from nps_sdk.nop.condition import NopConditionError, evaluate as evaluate_condition
from nps_sdk.nop.input_mapper import NopMappingError, build_params, resolve
from nps_sdk.nop import aggregator
from nps_sdk.nop.aggregator import aggregate, aggregate_end_nodes, merge
from nps_sdk.nop.validation import (
    DagValidationResult,
    DagValidator,
    NopCallbackValidator,
)
from nps_sdk.nop.store import (
    INopTaskStore,
    InMemoryNopTaskStore,
    NopSubtaskRecord,
    NopTaskRecord,
)
from nps_sdk.nop.worker import INopWorkerClient, PreflightResult
from nps_sdk.nop.results import NopTaskResult, SagaCompensationResult
from nps_sdk.nop.orchestrator import (
    INopOrchestrator,
    NopOrchestrator,
    NopOrchestratorOptions,
)
from nps_sdk.nop import instrumentation

__all__ = [
    # models
    "AggregateStrategy",
    "BackoffStrategy",
    "CompensationPolicy",
    "DagEdge",
    "DagNode",
    "RetryPolicy",
    "TaskContext",
    "TaskDag",
    "TaskPriority",
    "TaskState",
    # frames
    "AlignStreamFrame",
    "DelegateFrame",
    "StreamError",
    "SyncFrame",
    "TaskFrame",
    # client
    "NopClient",
    "NopTaskStatus",
    # error codes
    "error_codes",
    "NOP_ERROR_TO_NPS_STATUS",
    # constants
    "NopConstants",
    # condition
    "NopConditionError",
    "evaluate_condition",
    # input mapping
    "NopMappingError",
    "build_params",
    "resolve",
    # aggregation
    "aggregator",
    "aggregate",
    "aggregate_end_nodes",
    "merge",
    # validation
    "DagValidationResult",
    "DagValidator",
    "NopCallbackValidator",
    # storage
    "INopTaskStore",
    "InMemoryNopTaskStore",
    "NopSubtaskRecord",
    "NopTaskRecord",
    # worker
    "INopWorkerClient",
    "PreflightResult",
    # results
    "NopTaskResult",
    "SagaCompensationResult",
    # orchestrator
    "INopOrchestrator",
    "NopOrchestrator",
    "NopOrchestratorOptions",
    # telemetry / instrumentation ([I] band)
    "instrumentation",
]
