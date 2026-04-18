[English Version](./nps_sdk.core.md) | 中文版

# `nps_sdk.core` — 类与方法参考

> 根模块：`nps_sdk.core`
> 规范：[NPS-1 NCP v0.4 §3](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-1-NCP.md)

`core` 模块是所有其他 NPS 模块构建所依赖的线路层基础：
帧头解析、Tier-1/Tier-2 编解码管线、锚点缓存、帧类型注册表，
以及异常层级。

---

## 目录

- [枚举](#枚举)
  - [`FrameType`](#frametype)
  - [`EncodingTier`](#encodingtier)
  - [`FrameFlags`](#frameflags)
- [`FrameHeader`](#frameheader)
- [编解码器](#编解码器)
  - [`NpsFrame`](#npsframe)
  - [`Tier1JsonCodec`](#tier1jsoncodec)
  - [`Tier2MsgPackCodec`](#tier2msgpackcodec)
  - [`NpsFrameCodec`](#npsframecodec)
- [`AnchorFrameCache`](#anchorframecache)
- [`FrameRegistry`](#frameregistry)
- [异常](#异常)

---

## 枚举

### `FrameType`

`IntEnum` —— 贯穿整个套件的统一帧字节命名空间（NPS-0 §9）。

| 成员 | 值 | 层 |
|--------|-------|-------|
| `ANCHOR` | `0x01` | NCP |
| `DIFF` | `0x02` | NCP |
| `STREAM` | `0x03` | NCP |
| `CAPS` | `0x04` | NCP |
| `ALIGN` | `0x05` | NCP（**已弃用** —— 请使用 `ALIGN_STREAM`）|
| `QUERY` | `0x10` | NWP |
| `ACTION` | `0x11` | NWP |
| `IDENT` | `0x20` | NIP |
| `TRUST` | `0x21` | NIP |
| `REVOKE` | `0x22` | NIP |
| `ANNOUNCE` | `0x30` | NDP |
| `RESOLVE` | `0x31` | NDP |
| `GRAPH` | `0x32` | NDP |
| `TASK` | `0x40` | NOP |
| `DELEGATE` | `0x41` | NOP |
| `SYNC` | `0x42` | NOP |
| `ALIGN_STREAM` | `0x43` | NOP |
| `ERROR` | `0xFE` | Reserved —— 统一错误帧 |

### `EncodingTier`

`IntEnum` —— 存储在 flag 位 0–1 的线路编码 tier（NPS-1 §3.2）。

| 成员 | 值 | 备注 |
|--------|-------|-------|
| `JSON` | `0x00` | UTF-8 JSON，人类可读，开发/兼容使用 |
| `MSGPACK` | `0x01` | 二进制 MessagePack；体积约小 60%，生产默认 |

`0x02` 和 `0x03` 保留。未知 tier 抛出 `NpsCodecError`。

### `FrameFlags`

`IntFlag` —— 定长帧头中的 flag 字节。

| Flag | 位 | 用途 |
|------|------|---------|
| `NONE` / `TIER1_JSON` | `0x00` | 编码 tier 0 |
| `TIER2_MSGPACK` | `0x01` | 编码 tier 1 |
| `FINAL` | `0x04` | `StreamFrame` 的最后一块；非 stream 帧必置 |
| `ENCRYPTED` | `0x08` | Payload 已加密（生产保留）|
| `EXT` | `0x80` | 扩展 8 字节帧头（4 字节 payload 长度）|

位 4–6 保留 —— 发送端必须写 0，接收端必须忽略。

---

## `FrameHeader`

解析/写入 NPS 帧头（NPS-1 §3.1）。默认 4 字节，扩展 8 字节。

```python
class FrameHeader:
    frame_type:     FrameType
    flags:          FrameFlags
    payload_length: int

    def __init__(self, frame_type: FrameType, flags: FrameFlags, payload_length: int) -> None
```

模块级常量：

| 常量 | 值 | 含义 |
|----------|-------|---------|
| `DEFAULT_HEADER_SIZE` | `4` | 默认模式帧头字节数 |
| `EXTENDED_HEADER_SIZE` | `8` | 扩展模式帧头字节数 |
| `DEFAULT_MAX_PAYLOAD` | `0xFFFF` | 65 535 B —— 不带 `EXT` 时的上限 |
| `EXTENDED_MAX_PAYLOAD` | `0xFFFF_FFFF` | 4 GiB − 1 —— 带 `EXT` 时的上限 |

### 属性

| 属性 | 类型 | 返回 |
|----------|------|---------|
| `is_extended` | `bool` | 设置了 `EXT` flag 时为 `True` |
| `header_size` | `int` | `is_extended` 时为 `8`，否则 `4` |
| `encoding_tier` | `EncodingTier` | 从位 0–1 提取 |
| `is_final` | `bool` | `FINAL` flag |
| `is_encrypted` | `bool` | `ENCRYPTED` flag |

### 方法

#### `FrameHeader.parse(buf) -> FrameHeader` *(classmethod)*

从 `buf: bytes | bytearray | memoryview` 的起始处解析帧头。
自动通过 `EXT` flag 检测扩展模式。缓冲区过短时抛出 `NpsFrameError`。

#### `to_bytes() -> bytes`

序列化为 4 或 8 字节（取决于 `is_extended`）。全程大端。

布局：

```
默认（EXT=0, 4 B）：  [type (1) | flags (1) | length u16 (2)]
扩展（EXT=1, 8 B）：  [type (1) | flags (1) | reserved (2) | length u32 (4)]
```

---

## 编解码器

### `NpsFrame`

所有具体帧 dataclass 继承的 mixin 基类。契约：

```python
class NpsFrame:
    @property
    def frame_type(self) -> FrameType: ...

    @property
    def preferred_tier(self) -> EncodingTier: ...   # 默认：MSGPACK

    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NpsFrame": ...
```

`ncp`、`nwp`、`nip`、`ndp`、`nop` 中所有帧类均实现此契约。

### `Tier1JsonCodec`

UTF-8 JSON 编码/解码器。使用紧凑分隔符（`","`、`":"`）。方法：

- `encode(frame: NpsFrame) -> bytes` —— 失败时抛 `NpsCodecError`。
- `decode(frame_type: FrameType, payload: bytes, registry: FrameRegistry) -> NpsFrame`

### `Tier2MsgPackCodec`

二进制 MessagePack 编解码器。使用 `use_bin_type=True` 以在线路上
区分 `bytes` 和 `str`。与 `Tier1JsonCodec` 方法一致。相较 JSON
产生约小 60% 的 payload —— 生产流量首选。

### `NpsFrameCodec`

顶层编解码器调度器。

```python
class NpsFrameCodec:
    def __init__(self, registry: FrameRegistry, max_payload: int = 0xFFFF) -> None

    def encode(self, frame: NpsFrame, override_tier: EncodingTier | None = None) -> bytes
    def decode(self, wire: bytes) -> NpsFrame
    def peek_header(self, wire: bytes) -> FrameHeader
```

行为：

- **`encode`** 按 `override_tier`、`frame.preferred_tier` 或类默认值
  （MsgPack）选择 tier。当 payload 超过 `DEFAULT_MAX_PAYLOAD` 时
  自动设置 `FrameFlags.EXT`。非 stream 帧始终设置 `FrameFlags.FINAL`。
- **`decode`** 解析帧头、校验 tier、通过注册表查找具体类、委托
  给对应的编解码器。
- **`peek_header`** 仅解析帧头 —— 需要在不反序列化 payload 的
  情况下路由线路数据时有用。

格式错误的线路消息抛 `NpsFrameError`；编码/解码失败抛 `NpsCodecError`。

---

## `AnchorFrameCache`

`AnchorFrame` 实例的进程内缓存，以规范 `sha256:{64-hex}` id 为键。

```python
class AnchorFrameCache:
    def __init__(self) -> None

    def set(self, frame: AnchorFrame) -> str
    def get(self, anchor_id: str) -> AnchorFrame | None
    def get_required(self, anchor_id: str) -> AnchorFrame
    def invalidate(self, anchor_id: str) -> None

    @staticmethod
    def compute_anchor_id(schema: FrameSchema) -> str

    def __len__(self) -> int          # 当前活跃条目
```

行为：

- **`set`** 计算规范 id（按字段排序的 JSON → SHA-256）并存储帧。
  若已缓存相同 id 但不同 schema 的帧，抛 `NpsAnchorPoisonError`
  （NPS-1 §3.3 锚点投毒防护）。
- **`get_required`** 抛 `NpsAnchorNotFoundError`，异常上带 `anchor_id` 属性。
- **`__len__`** 返回前清除过期条目（`ttl != 0` 且按挂钟时间已过期）。
- **`compute_anchor_id`** 是确定性的：相同的 `FrameSchema` 在
  不同进程和机器上产生相同的 id。

---

## `FrameRegistry`

将 `FrameType` 字节映射到具体帧类。编解码器在解码任何帧时都需要它。

```python
class FrameRegistry:
    def __init__(self, mapping: dict[FrameType, type[NpsFrame]]) -> None

    def resolve(self, frame_type: FrameType) -> type[NpsFrame]
    def register(self, frame_type: FrameType, cls: type[NpsFrame]) -> None

    @classmethod
    def create_default(cls) -> "FrameRegistry"
    @classmethod
    def create_full(cls)    -> "FrameRegistry"
```

- `create_default()` —— 仅 NCP（`Anchor`、`Diff`、`Stream`、`Caps`、`Error`）。
- `create_full()` —— NCP + NWP + NIP + NDP + NOP；这是连接客户端时
  通常需要的版本。
- `resolve` 对未注册的帧类型抛 `NpsFrameError`。

---

## 异常

全部继承自 `NpsError`。

| 类 | 何时抛出 |
|-------|-------------|
| `NpsError` | 基类；永不直接抛出 |
| `NpsFrameError` | 线路帧头无效、帧未在注册表中 |
| `NpsCodecError` | Tier-1 / Tier-2 编码或解码失败 |
| `NpsAnchorNotFoundError` | `AnchorFrameCache.get_required` 未命中；带 `anchor_id` |
| `NpsAnchorPoisonError` | 相同锚点 id、不同 schema；带 `anchor_id` |

---

## 端到端示例

```python
from nps_sdk.core.codec   import NpsFrameCodec
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.core.cache   import AnchorFrameCache
from nps_sdk.ncp import AnchorFrame, FrameSchema, SchemaField

# 1) 编解码器
codec = NpsFrameCodec(FrameRegistry.create_default())

# 2) 构造并往返一个锚点
schema  = FrameSchema(fields=(
    SchemaField(name="id",    type="uint64"),
    SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
))
anchor  = AnchorFrame(anchor_id="placeholder", schema=schema, ttl=3600)
wire    = codec.encode(anchor)
decoded = codec.decode(wire)

# 3) 按内容寻址 id 缓存
cache = AnchorFrameCache()
real_id = cache.set(AnchorFrame(
    anchor_id=AnchorFrameCache.compute_anchor_id(schema),
    schema=schema,
    ttl=3600,
))
assert cache.get_required(real_id).schema == schema
```
