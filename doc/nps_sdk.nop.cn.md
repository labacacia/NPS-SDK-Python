[English Version](./nps_sdk.nop.md) | 中文版

# `nps_sdk.nop` — 类与方法参考

> 根模块：`nps_sdk.nop`
> 规范：[NPS-5 NOP v0.3](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-5-NOP.md)

NOP 是 NPS 的 SMTP/MQ —— 多 Agent 工作负载的规划、委托与
汇合。本模块提供四个 NOP 帧（`TaskFrame`、`DelegateFrame`、
`SyncFrame`、`AlignStreamFrame`）、完整 DAG 模型（`TaskDag`、
`DagNode`、`DagEdge`）、重试与聚合策略枚举，以及与 Gateway
Node 通信的异步 `NopClient`。

---

## 目录

- [常量与枚举](#常量与枚举)
  - [`TaskState`](#taskstate)
  - [`TaskPriority` / `AggregateStrategy` / `BackoffStrategy`](#字符串常量类)
- [策略与上下文类型](#策略与上下文类型)
  - [`RetryPolicy`](#retrypolicy)
  - [`TaskContext`](#taskcontext)
- [DAG 模型](#dag-模型)
  - [`DagEdge`](#dagedge)
  - [`DagNode`](#dagnode)
  - [`TaskDag`](#taskdag)
- [帧](#帧)
  - [`TaskFrame` (0x40)](#taskframe-0x40)
  - [`DelegateFrame` (0x41)](#delegateframe-0x41)
  - [`SyncFrame` (0x42)](#syncframe-0x42)
  - [`AlignStreamFrame` (0x43)](#alignstreamframe-0x43)
  - [`StreamError`](#streamerror)
- [`NopClient`](#nopclient)
  - [`NopTaskStatus`](#noptaskstatus)
- [端到端示例](#端到端示例)

---

## 常量与枚举

### `TaskState`

`str Enum` —— NOP 任务/子任务的生命周期状态。

| 成员 | 值 |
|--------|-------|
| `PENDING` | `"pending"` |
| `PREFLIGHT` | `"preflight"` |
| `RUNNING` | `"running"` |
| `WAITING_SYNC` | `"waiting_sync"` |
| `COMPLETED` | `"completed"` |
| `FAILED` | `"failed"` |
| `CANCELLED` | `"cancelled"` |
| `SKIPPED` | `"skipped"` |

### 字符串常量类

并非真正的枚举 —— 只是持有字符串类属性的普通类，这样
无需特殊处理就能往返 JSON/MsgPack。

```python
class TaskPriority:
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"

class AggregateStrategy:
    MERGE      = "merge"          # 深度合并 dict
    FIRST      = "first"          # 第一个完成的结果
    ALL        = "all"            # 所有结果的列表
    FASTEST_K  = "fastest_k"      # 最先完成的 K 个

class BackoffStrategy:
    FIXED       = "fixed"
    LINEAR      = "linear"
    EXPONENTIAL = "exponential"
```

---

## 策略与上下文类型

### `RetryPolicy`

带退避的单节点重试策略。

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

- `retry_on` —— 被视为可重试的错误码元组（如 `"NOP-NODE-TIMEOUT"`）；
  空表示"任何错误都重试"。
- `compute_delay_ms(attempt)` —— `attempt` 从 1 开始：
  - `FIXED` → `initial_delay_ms`
  - `LINEAR` → `initial_delay_ms * attempt`
  - `EXPONENTIAL` → `initial_delay_ms * 2**(attempt-1)`
  - 结果截断到 `max_delay_ms`。

### `TaskContext`

委托链中转发的分布式追踪上下文（OpenTelemetry 形态）。

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

## DAG 模型

### `DagEdge`

```python
@dataclass(frozen=True)
class DagEdge:
    from_: str       # 线路上序列化为 "from"
    to:    str

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DagEdge"
```

`from_`（带尾下划线）规避 Python 关键字；线路形式为 `"from"`。

### `DagNode`

```python
@dataclass(frozen=True)
class DagNode:
    id:             str
    action:         str                    # "nwp://…" URL 或 "preflight" / "cancel"
    agent:          str                    # worker NID，urn:nps:agent:...
    input_from:     tuple[str, ...] = ()
    input_mapping:  dict[str, str] | None = None    # {param_name → JSONPath}
    timeout_ms:     int | None = None
    retry_policy:   RetryPolicy | None = None
    condition:      str | None = None               # CEL 子集
    min_required:   int = 0                         # K-of-N fan-in
```

说明：

- `condition` 对聚合的上游结果求值；`min_required` 在 sync
  屏障处控制 fan-in 语义。
- `action` 同时支持 nwp:// URL 和两个控制动词（`"preflight"`、
  `"cancel"`），编排器会对其短路处理。

### `TaskDag`

```python
@dataclass(frozen=True)
class TaskDag:
    nodes: tuple[DagNode, ...]
    edges: tuple[DagEdge, ...]
```

服务端强制执行的规范限制（NPS-5 §4.1）：最多 32 个节点；
委托链深度 ≤ 3；最大 timeout 3 600 000 ms。

---

## 帧

### `TaskFrame` (0x40)

提交到 Gateway Node 的 DAG 任务定义。

```python
@dataclass(frozen=True)
class TaskFrame(NpsFrame):
    task_id:         str
    dag:             TaskDag
    timeout_ms:      int  = 30_000
    max_retries:     int  = 2
    priority:        str  = TaskPriority.NORMAL
    callback_url:    str | None = None              # 仅 HTTPS
    preflight:       bool = False
    context:         TaskContext | None = None
    request_id:      str | None = None
    delegate_depth:  int  = 0                       # 根任务为 0
```

### `DelegateFrame` (0x41)

编排器向 worker Agent 的子任务委托。

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

同步屏障。

```python
@dataclass(frozen=True)
class SyncFrame(NpsFrame):
    task_id:      str
    sync_id:      str
    wait_for:     tuple[str, ...]                   # 子任务 ID
    min_required: int = 0                           # 0 = 全部
    aggregate:    str = AggregateStrategy.MERGE
    timeout_ms:   int | None = None
```

`min_required = K, len(wait_for) = N` → 经典的 K-of-N fan-in。

### `AlignStreamFrame` (0x43)

从 worker 回流到编排器的定向结果流。

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

- `seq` 在流内严格单调。
- `error` 非空标记终态失败；此时 `is_final` **必须**为 `True`。

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

面向 NOP Gateway Node 的异步 HTTP 客户端。

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

将 `TaskFrame` POST 到 `{base_url}/task`。返回服务端的 `task_id`。

### `get_status(task_id) -> NopTaskStatus`

GET `{base_url}/task/{task_id}`；解码为 `NopTaskStatus`。

### `cancel(task_id)`

POST 到 `{base_url}/task/{task_id}/cancel`。

### `wait(task_id, poll_interval, timeout) -> NopTaskStatus`

每 `poll_interval` 秒轮询一次 `get_status`，直到任务到达终态
（`COMPLETED`、`FAILED`、`CANCELLED`）或超过 `timeout` ——
后者抛 `asyncio.TimeoutError`。

### 所有权

与 `NwpClient` 相同：若你传入自己的 `http_client`，所有权归你；
若构造函数内部创建，客户端在 `__aexit__` / `close()` 时拥有并关闭。

### `NopTaskStatus`

解析后的状态响应。所有属性都是原始 dict 的只读视图。

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

## 端到端示例

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
