# `nps_sdk.nop` — Class and Method Reference

> Root module: `nps_sdk.nop`
> Spec: [NPS-5 NOP v0.3](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-5-NOP.md)

NOP is the SMTP/MQ of NPS — how multi-Agent workloads get planned, delegated
and joined. This module ships the four NOP frames (`TaskFrame`,
`DelegateFrame`, `SyncFrame`, `AlignStreamFrame`), the full DAG model
(`TaskDag`, `DagNode`, `DagEdge`), retry and aggregation policy enums, and the
async `NopClient` that talks to Gateway Nodes.

---

## Table of contents

- [Constants and enums](#constants-and-enums)
  - [`TaskState`](#taskstate)
  - [`TaskPriority` / `AggregateStrategy` / `BackoffStrategy`](#string-constant-classes)
- [Policy and context types](#policy-and-context-types)
  - [`RetryPolicy`](#retrypolicy)
  - [`TaskContext`](#taskcontext)
- [DAG model](#dag-model)
  - [`DagEdge`](#dagedge)
  - [`DagNode`](#dagnode)
  - [`TaskDag`](#taskdag)
- [Frames](#frames)
  - [`TaskFrame` (0x40)](#taskframe-0x40)
  - [`DelegateFrame` (0x41)](#delegateframe-0x41)
  - [`SyncFrame` (0x42)](#syncframe-0x42)
  - [`AlignStreamFrame` (0x43)](#alignstreamframe-0x43)
  - [`StreamError`](#streamerror)
- [`NopClient`](#nopclient)
  - [`NopTaskStatus`](#noptaskstatus)
- [End-to-end example](#end-to-end-example)

---

## Constants and enums

### `TaskState`

`str Enum` — lifecycle state of a NOP task/subtask.

| Member | Value |
|--------|-------|
| `PENDING` | `"pending"` |
| `PREFLIGHT` | `"preflight"` |
| `RUNNING` | `"running"` |
| `WAITING_SYNC` | `"waiting_sync"` |
| `COMPLETED` | `"completed"` |
| `FAILED` | `"failed"` |
| `CANCELLED` | `"cancelled"` |
| `SKIPPED` | `"skipped"` |

### String-constant classes

Not real enums — plain classes with string class-attributes so they round-trip
through JSON/MsgPack without special handling.

```python
class TaskPriority:
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"

class AggregateStrategy:
    MERGE      = "merge"          # deep-merge dicts
    FIRST      = "first"          # first completed result
    ALL        = "all"            # list of all results
    FASTEST_K  = "fastest_k"      # first K to complete

class BackoffStrategy:
    FIXED       = "fixed"
    LINEAR      = "linear"
    EXPONENTIAL = "exponential"
```

---

## Policy and context types

### `RetryPolicy`

Per-node retry policy with backoff.

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_retries:      int = 2
    backoff:          str = BackoffStrategy.EXPONENTIAL
    initial_delay_ms: int = 1_000
    max_delay_ms:     int = 30_000
    retry_on:         tuple[str, ...] = ()

    def compute_delay_ms(self, attempt: int) -> int
    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetryPolicy"
```

- `retry_on` — tuple of error codes (e.g. `"NOP-NODE-TIMEOUT"`) that are
  considered retryable; empty means "retry on any error".
- `compute_delay_ms(attempt)` — `attempt` is 1-based:
  - `FIXED` → `initial_delay_ms`
  - `LINEAR` → `initial_delay_ms * attempt`
  - `EXPONENTIAL` → `initial_delay_ms * 2**(attempt-1)`
  - Result is clamped to `max_delay_ms`.

### `TaskContext`

Distributed tracing context forwarded through the delegation chain
(OpenTelemetry-shaped).

```python
@dataclass(frozen=True)
class TaskContext:
    session_id:   str | None = None
    trace_id:     str | None = None
    span_id:      str | None = None
    trace_flags:  int | None = None
    baggage:      dict[str, str] | None = None
    custom:       Any = None

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskContext"
```

---

## DAG model

### `DagEdge`

```python
@dataclass(frozen=True)
class DagEdge:
    from_: str       # serialised as "from" on the wire
    to:    str

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DagEdge"
```

`from_` (trailing underscore) sidesteps the Python keyword; the wire form is
`"from"`.

### `DagNode`

```python
@dataclass(frozen=True)
class DagNode:
    id:             str
    action:         str                    # "nwp://…" URL OR "preflight" / "cancel"
    agent:          str                    # worker NID, urn:nps:agent:...
    input_from:     tuple[str, ...] = ()
    input_mapping:  dict[str, str] | None = None    # {param_name → JSONPath}
    timeout_ms:     int | None = None
    retry_policy:   RetryPolicy | None = None
    condition:      str | None = None               # CEL subset
    min_required:   int = 0                         # K-of-N fan-in
```

Notes:

- `condition` is evaluated against the aggregate upstream result; `min_required`
  controls fan-in semantics at a sync barrier.
- `action` supports both nwp:// URLs and two control verbs (`"preflight"`,
  `"cancel"`) that the orchestrator short-circuits.

### `TaskDag`

```python
@dataclass(frozen=True)
class TaskDag:
    nodes: tuple[DagNode, ...]
    edges: tuple[DagEdge, ...]
```

Spec limits enforced server-side (NPS-5 §4.1): up to 32 nodes; delegate chain
depth ≤ 3; max timeout 3 600 000 ms.

---

## Frames

### `TaskFrame` (0x40)

DAG task definition, submitted to a Gateway Node.

```python
@dataclass(frozen=True)
class TaskFrame(NpsFrame):
    task_id:         str
    dag:             TaskDag
    timeout_ms:      int  = 30_000
    max_retries:     int  = 2
    priority:        str  = TaskPriority.NORMAL
    callback_url:    str | None = None              # HTTPS only
    preflight:       bool = False
    context:         TaskContext | None = None
    request_id:      str | None = None
    delegate_depth:  int  = 0                       # 0 for root tasks
```

### `DelegateFrame` (0x41)

Subtask delegation from orchestrator to a worker Agent.

```python
@dataclass(frozen=True)
class DelegateFrame(NpsFrame):
    parent_task_id:    str
    subtask_id:        str
    node_id:           str
    target_agent_nid:  str
    action:            str
    delegated_scope:   Any
    deadline_at:       str                          # ISO 8601 UTC
    params:            Any = None
    idempotency_key:   str | None = None
    priority:          str | None = None
    context:           TaskContext | None = None
    delegate_depth:    int = 1                      # parent + 1
```

### `SyncFrame` (0x42)

Synchronisation barrier.

```python
@dataclass(frozen=True)
class SyncFrame(NpsFrame):
    task_id:      str
    sync_id:      str
    wait_for:     tuple[str, ...]                   # subtask IDs
    min_required: int = 0                           # 0 = all
    aggregate:    str = AggregateStrategy.MERGE
    timeout_ms:   int | None = None
```

`min_required = K, len(wait_for) = N` → classic K-of-N fan-in.

### `AlignStreamFrame` (0x43)

Directed result stream flowing from worker back to orchestrator.

```python
@dataclass(frozen=True)
class AlignStreamFrame(NpsFrame):
    stream_id:    str
    task_id:      str
    subtask_id:   str
    seq:          int
    is_final:     bool
    sender_nid:   str
    data:         Any          = None
    payload_ref:  str | None   = None
    window_size:  int | None   = None
    error:        StreamError | None = None
```

- `seq` is strictly monotonic within a stream.
- `error` non-null marks a terminal failure; `is_final` MUST be `True` in that
  case.

### `StreamError`

```python
@dataclass(frozen=True)
class StreamError:
    error_code: str
    message:    str

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StreamError"
```

---

## `NopClient`

Async HTTP client for talking to a NOP Gateway Node.

```python
class NopClient:
    def __init__(
        self,
        base_url:     str,
        default_tier: EncodingTier          = EncodingTier.MSGPACK,
        timeout:      float                 = 30.0,
        http_client:  httpx.AsyncClient | None = None,
        registry:     FrameRegistry       | None = None,
    ) -> None

    async def __aenter__(self) -> "NopClient"
    async def __aexit__(self, *exc_info) -> None

    async def submit(self, frame: TaskFrame) -> str
    async def get_status(self, task_id: str) -> "NopTaskStatus"
    async def cancel(self, task_id: str) -> None
    async def wait(
        self,
        task_id:       str,
        poll_interval: float = 1.0,
        timeout:       float = 30.0,
    ) -> "NopTaskStatus"

    async def close(self) -> None
```

### `submit(frame) -> str`

POST the `TaskFrame` to `{base_url}/task`. Returns the server-side `task_id`.

### `get_status(task_id) -> NopTaskStatus`

GET `{base_url}/task/{task_id}`; decodes into `NopTaskStatus`.

### `cancel(task_id)`

POST to `{base_url}/task/{task_id}/cancel`.

### `wait(task_id, poll_interval, timeout) -> NopTaskStatus`

Poll `get_status` every `poll_interval` seconds until the task reaches a
terminal state (`COMPLETED`, `FAILED`, `CANCELLED`) or `timeout` elapses —
the latter raises `asyncio.TimeoutError`.

### Ownership

Same rule as `NwpClient`: if you pass your own `http_client`, you own it; if
the constructor creates one, the client owns and closes it in `__aexit__` /
`close()`.

### `NopTaskStatus`

Parsed status response. All properties are read-only views over the raw dict.

```python
class NopTaskStatus:
    def __init__(self, raw: dict[str, Any]) -> None

    @property
    def task_id(self)           -> str
    @property
    def state(self)             -> TaskState
    @property
    def is_terminal(self)       -> bool
    @property
    def aggregated_result(self) -> Any
    @property
    def error_code(self)        -> str | None
    @property
    def error_message(self)     -> str | None
    @property
    def node_results(self)      -> dict[str, Any]
    @property
    def raw(self)               -> dict[str, Any]
```

---

## End-to-end example

```python
import asyncio
from nps_sdk.nop import (
    NopClient, TaskFrame, TaskDag, DagNode, DagEdge,
    TaskPriority, TaskState, TaskContext,
    RetryPolicy, BackoffStrategy,
)

dag = TaskDag(
    nodes=(
        DagNode(
            id="fetch",
            action="nwp://products.example.com/query",
            agent="urn:nps:agent:example.com:fetcher",
            retry_policy=RetryPolicy(
                max_retries=3,
                backoff=BackoffStrategy.EXPONENTIAL,
                initial_delay_ms=500,
                max_delay_ms=5_000,
            ),
        ),
        DagNode(
            id="classify",
            action="nwp://ai.example.com/classify",
            agent="urn:nps:agent:example.com:classifier",
            input_from=("fetch",),
            input_mapping={"text": "$.fetch.body"},
        ),
        DagNode(
            id="route",
            action="nwp://router.example.com/route",
            agent="urn:nps:agent:example.com:router",
            input_from=("classify",),
            condition="$.classify.score > 0.7",
        ),
    ),
    edges=(
        DagEdge(from_="fetch",    to="classify"),
        DagEdge(from_="classify", to="route"),
    ),
)

async def main() -> None:
    async with NopClient("https://gateway.example.com") as nop:
        task_id = await nop.submit(TaskFrame(
            task_id="t-001",
            dag=dag,
            priority=TaskPriority.HIGH,
            timeout_ms=60_000,
            context=TaskContext(session_id="sess-42"),
        ))
        status = await nop.wait(task_id, poll_interval=0.5, timeout=120.0)
        if status.state is TaskState.FAILED:
            raise RuntimeError(f"{status.error_code}: {status.error_message}")
        print(status.aggregated_result)

asyncio.run(main())
```
