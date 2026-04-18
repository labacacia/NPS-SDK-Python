[English Version](./nps_sdk.nwp.md) | 中文版

# `nps_sdk.nwp` — 类与方法参考

> 根模块：`nps_sdk.nwp`
> 规范：[NPS-2 NWP v0.4](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-2-NWP.md)

NWP 是 AI 的 HTTP。本模块提供两个 NWP 帧（`QueryFrame`、
`ActionFrame`）、异步 `NwpClient`，以及用于查询排序、向量
搜索和异步 action 响应的辅助 dataclass。

---

## 目录

- [辅助 dataclass](#辅助-dataclass)
  - [`QueryOrderClause`](#queryorderclause)
  - [`VectorSearchOptions`](#vectorsearchoptions)
  - [`AsyncActionResponse`](#asyncactionresponse)
- [帧](#帧)
  - [`QueryFrame` (0x10)](#queryframe-0x10)
  - [`ActionFrame` (0x11)](#actionframe-0x11)
- [`NwpClient`](#nwpclient)
- [端到端示例](#端到端示例)

---

## 辅助 dataclass

### `QueryOrderClause`

```python
@dataclass(frozen=True)
class QueryOrderClause:
    field: str
    dir:   str              # "asc" | "desc"

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryOrderClause"
```

### `VectorSearchOptions`

```python
@dataclass(frozen=True)
class VectorSearchOptions:
    field:     str
    vector:    tuple[float, ...]
    top_k:     int   = 10
    threshold: float | None = None
    metric:    str   = "cosine"     # "cosine" | "euclidean" | "dot"

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VectorSearchOptions"
```

当目标 Memory Node 通告 `nwp:vector` 时附加到 `QueryFrame.vector_search`。

### `AsyncActionResponse`

```python
@dataclass(frozen=True)
class AsyncActionResponse:
    task_id:  str
    status:   str             # "pending" | "running" | ...
    poll_url: str

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AsyncActionResponse"
```

当 `ActionFrame` 以 `async_=True` 提交时，`NwpClient.invoke` 返回此对象。
轮询 `poll_url` 观察进度。

---

## 帧

### `QueryFrame` (0x10)

针对 Memory Node 的结构化读取。

```python
@dataclass(frozen=True)
class QueryFrame(NpsFrame):
    anchor_ref:    str | None = None
    filter:        Any       = None
    fields:        tuple[str, ...] | None = None
    limit:         int       = 20
    cursor:        str | None = None
    order:         tuple[QueryOrderClause, ...] | None = None
    vector_search: VectorSearchOptions | None = None
```

- `anchor_ref` **可以**指向已缓存的 `AnchorFrame`；节点据此可
  在响应的 `CapsFrame` 中省略 schema。
- `filter` 是自由格式表达式（通常是 dict），由节点的 `.nwm.json`
  manifest 描述。
- `fields` 是投影列表；`None` 表示"schema 中全部字段"。
- `cursor` 是先前 `CapsFrame.next_cursor` 返回的不透明分页游标。

### `ActionFrame` (0x11)

针对 Action / Complex / Gateway Node 的操作调用。

```python
@dataclass(frozen=True)
class ActionFrame(NpsFrame):
    action_id:       str
    params:          Any         = None
    idempotency_key: str | None = None
    timeout_ms:      int         = 5000
    async_:          bool        = False   # 线路上序列化为 "async"
```

- `action_id` 是 Action Node 在 `.nwm.json` → `endpoints.actions[].id`
  下声明的标识。
- `idempotency_key` —— 若存在，节点**必须**在其重放窗口内对调用
  去重。
- `async_=True` 请求节点延迟响应；客户端收到 `AsyncActionResponse`
  而非同步结果。

---

## `NwpClient`

面向 NWP 节点的异步 HTTP 客户端。

```python
class NwpClient:
    def __init__(
        self,
        base_url:     str,
        default_tier: EncodingTier          = EncodingTier.MSGPACK,
        timeout:      float                 = 10.0,
        http_client:  httpx.AsyncClient | None = None,
        registry:     FrameRegistry       | None = None,
    ) -> None

    async def __aenter__(self) -> "NwpClient"
    async def __aexit__(self, *exc_info) -> None
    async def close(self) -> None
```

所有权：若你传入自己的 `http_client`，SDK 在 `__aexit__` /
`close` 时**不会**关闭它；若内部创建，则拥有并负责关闭。

省略 `registry` 时，客户端使用 `FrameRegistry.create_full()`。

### 方法

#### `async send_anchor(frame: AnchorFrame) -> None`

将 `AnchorFrame` POST 到 `{base_url}/anchor`。节点缓存 schema
并以 HTTP 204 确认。失败抛 `httpx.HTTPStatusError`。

#### `async query(frame: QueryFrame) -> CapsFrame`

将 `QueryFrame` POST 到 `{base_url}/query`，将响应解码为 `CapsFrame`。

#### `async stream(frame: QueryFrame) -> AsyncIterator[StreamFrame]`

将 `QueryFrame` POST 到 `{base_url}/stream`，在每个 `StreamFrame`
到达时将其 yield。帧边界：换行分隔的线路消息（NPS-2 §5.4）。
用 `async for` 消费。

#### `async invoke(frame: ActionFrame) -> Any`

将 `ActionFrame` POST 到 `{base_url}/invoke`。

- `frame.async_ == False` → 返回 JSON 解码后的响应体。
- `frame.async_ == True`  → 返回 `AsyncActionResponse`。

非 2xx 响应抛 `httpx.HTTPStatusError`。

---

## 端到端示例

```python
import asyncio
from nps_sdk.core import EncodingTier
from nps_sdk.ncp  import AnchorFrame, FrameSchema, SchemaField
from nps_sdk.nwp  import (
    NwpClient, QueryFrame, QueryOrderClause,
    ActionFrame, AsyncActionResponse,
    VectorSearchOptions,
)

async def main() -> None:
    async with NwpClient("https://products.example.com", default_tier=EncodingTier.MSGPACK) as nwp:
        # 1) 上传锚点一次
        schema = FrameSchema(fields=(
            SchemaField(name="id",    type="uint64"),
            SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
        ))
        anchor = AnchorFrame(anchor_id="sha256:…", schema=schema, ttl=3600)
        await nwp.send_anchor(anchor)

        # 2) 分页查询
        caps = await nwp.query(QueryFrame(
            anchor_ref=anchor.anchor_id,
            filter={"price": {"$lt": "100.00"}},
            order=(QueryOrderClause(field="price", dir="asc"),),
            limit=50,
        ))
        print(caps.count, "rows, cursor:", caps.next_cursor)

        # 3) 流式获取完整集合
        async for chunk in nwp.stream(QueryFrame(anchor_ref=anchor.anchor_id, limit=200)):
            for row in chunk.data:
                ...
            if chunk.is_last:
                break

        # 4) 发起异步 action
        resp = await nwp.invoke(ActionFrame(
            action_id="restock",
            params={"sku": "sku-4242", "qty": 100},
            async_=True,
        ))
        assert isinstance(resp, AsyncActionResponse)
```
