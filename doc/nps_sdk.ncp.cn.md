[English Version](./nps_sdk.ncp.md) | 中文版

# `nps_sdk.ncp` — 类与方法参考

> 根模块：`nps_sdk.ncp`
> 规范：[NPS-1 NCP v0.4](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-1-NCP.md)

NCP 是 NPS 的线路与 schema 层。本模块暴露在线路上传输的具体
帧 dataclass（`AnchorFrame`、`DiffFrame`、`StreamFrame`、
`CapsFrame`、`ErrorFrame`），以及它们所携带的 schema 模型
（`FrameSchema`、`SchemaField`、`JsonPatchOperation`）。

所有帧类都是**冻结 dataclass**，扩展自 `NpsFrame`
（见 [`nps_sdk.core.cn.md`](./nps_sdk.core.cn.md#npsframe)）。它们都提供：

- `frame_type` —— 本帧映射到的 `FrameType` 枚举成员。
- `preferred_tier` —— 每个 NCP 帧都是 `EncodingTier.MSGPACK`。
- `to_dict()` / `from_dict(data)` —— 通过普通 dict 与编解码器往返。

---

## 目录

- [Schema 类型](#schema-类型)
  - [`SchemaField`](#schemafield)
  - [`FrameSchema`](#frameschema)
- [帧](#帧)
  - [`AnchorFrame` (0x01)](#anchorframe-0x01)
  - [`DiffFrame` (0x02)](#diffframe-0x02)
  - [`StreamFrame` (0x03)](#streamframe-0x03)
  - [`CapsFrame` (0x04)](#capsframe-0x04)
  - [`ErrorFrame` (0xFE)](#errorframe-0xfe)
- [`JsonPatchOperation`](#jsonpatchoperation)
- [端到端示例](#端到端示例)

---

## Schema 类型

### `SchemaField`

```python
@dataclass(frozen=True)
class SchemaField:
    name:     str
    type:     str
    semantic: str | None = None
    nullable: bool = False

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SchemaField"
```

`FrameSchema` 中的一列。

- `type` 是 NPS 逻辑类型（`"uint64"`、`"decimal"`、`"string"` 等）。
- `semantic` 是可选的 NPS 语义注册表中带点号的标签
  （如 `"commerce.price.usd"`）。Agent 用它跨 schema 对齐。

### `FrameSchema`

```python
@dataclass(frozen=True)
class FrameSchema:
    fields: tuple[SchemaField, ...]

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrameSchema"
```

`AnchorFrame` 内部携带的 schema payload。一旦锚定，后续的
`DiffFrame` / `StreamFrame` / `CapsFrame` 消息都可以通过
`anchor_ref` 引用该 schema —— 这是 NPS 节省 token 的核心技巧。

---

## 帧

### `AnchorFrame` (0x01)

Schema 锚点 —— **内容寻址**的 schema 广告。

```python
@dataclass(frozen=True)
class AnchorFrame(NpsFrame):
    anchor_id: str          # "sha256:{64 hex}"
    schema:    FrameSchema
    ttl:       int = 3600   # 秒；0 = 仅本 session
```

- `anchor_id` 必须等于 `AnchorFrameCache.compute_anchor_id(schema)`。
  不匹配会毒化缓存，在接收方产生 `NpsAnchorPoisonError`。
- `ttl = 0` 表示锚点只在当前 session 有效。

### `DiffFrame` (0x02)

引用某锚点的增量更新 —— 仅以 RFC 6902 JSON Patch 操作携带
变化的字段。

```python
@dataclass(frozen=True)
class DiffFrame(NpsFrame):
    anchor_ref: str
    base_seq:   int
    patch:      tuple[JsonPatchOperation, ...]
    entity_id:  str | None = None
```

`base_seq` 是流内 patch 排序的单调递增序号。出现跳号**必须**
触发重新同步（错误码 `NCP-STREAM-SEQ-GAP`）。

### `StreamFrame` (0x03)

流式分块帧。

```python
@dataclass(frozen=True)
class StreamFrame(NpsFrame):
    stream_id:   str
    seq:         int
    is_last:     bool
    data:        tuple[Any, ...]
    anchor_ref:  str | None = None
    window_size: int | None = None   # 背压提示
    error_code:  str | None = None   # 终态错误（令 is_last = True）
```

- `is_last = True` 标记最后一块；接收方**可以**在此后立即
  释放流状态。
- `window_size` 可选携带 NCP 流控提示，允许发送端按此推进
  （接收端可忽略）。

### `CapsFrame` (0x04)

Capsule —— 引用已缓存 schema 的完整分页结果集。

```python
@dataclass(frozen=True)
class CapsFrame(NpsFrame):
    anchor_ref:      str
    count:           int
    data:            tuple[Any, ...]
    next_cursor:     str | None = None
    token_est:       int | None = None
    cached:          bool | None = None
    tokenizer_used:  str | None = None
```

`token_est` + `tokenizer_used` 使消费者可以扣减预算而无需重新
tokenize。`next_cursor` 是不透明的下一页游标。

### `ErrorFrame` (0xFE)

整个套件共享的统一错误帧（NPS-0 §9）。

```python
@dataclass(frozen=True)
class ErrorFrame(NpsFrame):
    status:  str              # NPS 状态码，如 "NPS-404"
    error:   str              # 协议码，如 "NWP-QUERY-NOT-FOUND"
    message: str | None = None
    details: Any         = None
```

用于原生传输模式。在 HTTP overlay 模式下，NPS 状态码通过
`X-NPS-Status` header 暴露，响应体仍携带具体的 `ErrorFrame`。

---

## `JsonPatchOperation`

`DiffFrame.patch` 中单个的 RFC 6902 patch。

```python
@dataclass(frozen=True)
class JsonPatchOperation:
    op:    str                # "add" | "remove" | "replace" | "move" | "copy" | "test"
    path:  str                # JSON Pointer
    value: Any      = None
    from_: str | None = None  # 线路上序列化为 "from"（RFC 6902）

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JsonPatchOperation"
```

`from_`（带尾下划线）避免与 Python 关键字 `from` 冲突；
`to_dict` / `from_dict` 处理到 `"from"` 的线路格式转换。

---

## 端到端示例

```python
from nps_sdk.core.codec   import NpsFrameCodec
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.core.cache   import AnchorFrameCache
from nps_sdk.ncp import (
    AnchorFrame, CapsFrame, DiffFrame, StreamFrame,
    FrameSchema, SchemaField, JsonPatchOperation,
)

schema = FrameSchema(fields=(
    SchemaField(name="id",    type="uint64"),
    SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
))

# 1) 以规范 id 发出 AnchorFrame
cache = AnchorFrameCache()
anchor = AnchorFrame(
    anchor_id=AnchorFrameCache.compute_anchor_id(schema),
    schema=schema,
    ttl=3600,
)
cache.set(anchor)

# 2) 发出引用它的 DiffFrame
diff = DiffFrame(
    anchor_ref=anchor.anchor_id,
    base_seq=1,
    patch=(
        JsonPatchOperation(op="replace", path="/price", value="19.99"),
    ),
    entity_id="sku-4242",
)

# 3) 通过编解码器往返
codec = NpsFrameCodec(FrameRegistry.create_default())
wire  = codec.encode(diff)
back  = codec.decode(wire)
assert isinstance(back, DiffFrame)
```
