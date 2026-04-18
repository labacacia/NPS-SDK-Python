[English Version](./nps_sdk.ndp.md) | 中文版

# `nps_sdk.ndp` — 类与方法参考

> 根模块：`nps_sdk.ndp`
> 规范：[NPS-4 NDP v0.2](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-4-NDP.md)

NDP 是发现层 —— NPS 对应 DNS 的组件。本模块提供三个 NDP
帧类型、一个带惰性 TTL 过期的线程安全内存注册表，以及
由 `NipIdentity` 支持的 announce 签名校验器。

---

## 目录

- [辅助 dataclass](#辅助-dataclass)
  - [`NdpAddress`](#ndpaddress)
  - [`NdpResolveResult`](#ndpresolveresult)
  - [`NdpGraphNode`](#ndpgraphnode)
- [帧](#帧)
  - [`AnnounceFrame` (0x30)](#announceframe-0x30)
  - [`ResolveFrame` (0x31)](#resolveframe-0x31)
  - [`GraphFrame` (0x32)](#graphframe-0x32)
- [`InMemoryNdpRegistry`](#inmemoryndpregistry)
- [校验器](#校验器)
  - [`NdpAnnounceValidator`](#ndpannouncevalidator)
  - [`NdpAnnounceResult`](#ndpannounceresult)
- [端到端示例](#端到端示例)

---

## 辅助 dataclass

### `NdpAddress`

```python
@dataclass(frozen=True)
class NdpAddress:
    host:     str
    port:     int
    protocol: str      # "nwp" | "nwp+tls"

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpAddress"
```

### `NdpResolveResult`

```python
@dataclass(frozen=True)
class NdpResolveResult:
    host:             str
    port:             int
    ttl:              int                  # 秒
    cert_fingerprint: str | None = None    # "sha256:{hex}"

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpResolveResult"
```

### `NdpGraphNode`

```python
@dataclass(frozen=True)
class NdpGraphNode:
    nid:          str
    addresses:    tuple[NdpAddress, ...]
    capabilities: tuple[str, ...]
    node_type:    str | None = None         # "memory" | "action" | ...

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpGraphNode"
```

---

## 帧

### `AnnounceFrame` (0x30)

发布节点的物理可达性与 TTL（NPS-4 §3.1）。

```python
@dataclass(frozen=True)
class AnnounceFrame(NpsFrame):
    nid:          str
    addresses:    tuple[NdpAddress, ...]
    capabilities: tuple[str, ...]
    ttl:          int                         # 0 = 有序下线
    timestamp:    str                         # ISO 8601 UTC
    signature:    str                         # "ed25519:{base64url}"
    node_type:    str | None = None

    def unsigned_dict(self) -> dict[str, Any]
```

签名流程（NPS-4 §3.1）：

1. 调用 `frame.unsigned_dict()` —— 此方法剥离 `signature`。
2. 用 `NipIdentity.sign(dict)` 以该 NID 自己的私钥签名（与其
   `IdentFrame` 所用相同密钥）。
3. `ttl = 0` **必须**在有序下线前签名并发布，以便订阅者清除条目。

### `ResolveFrame` (0x31)

解析 `nwp://` URL 的请求/响应信封。

```python
@dataclass(frozen=True)
class ResolveFrame(NpsFrame):
    target:        str                        # "nwp://api.example.com/products"
    requester_nid: str | None = None
    resolved:      NdpResolveResult | None = None   # 响应时填充
```

Resolve 流量首选 JSON tier —— 量小且需要人类调试。

### `GraphFrame` (0x32)

注册表之间的拓扑同步。

```python
@dataclass(frozen=True)
class GraphFrame(NpsFrame):
    seq:          int                          # 每个发布者严格单调
    initial_sync: bool
    nodes:        tuple[NdpGraphNode, ...] | None = None   # 全量快照
    patch:        Any                         = None       # RFC 6902 JSON Patch
```

`seq` 跳号**必须**触发重新同步请求，信号为 `NDP-GRAPH-SEQ-GAP`。

---

## `InMemoryNdpRegistry`

线程安全、按 TTL 过期的注册表。过期是在每次读取时**惰性**
评估的 —— 没有后台定时器。

```python
class InMemoryNdpRegistry:
    def __init__(self) -> None

    def announce(self, frame: AnnounceFrame) -> None
    def resolve(self, target: str) -> NdpResolveResult | None
    def get_all(self) -> list[AnnounceFrame]
    def get_by_nid(self, nid: str) -> AnnounceFrame | None

    @staticmethod
    def nwp_target_matches_nid(nid: str, target: str) -> bool

    # 用于确定性单元测试
    clock: Callable[[], float]
```

### 行为

- **`announce(frame)`** —— `frame.ttl == 0` 立即清除该 NID；
  否则以绝对过期 `clock() + ttl` 插入（或刷新）条目。
- **`resolve(target)`** —— 扫描当前活跃条目，找到第一个 NID
  "覆盖" `target` 的项（见下），返回该 announcement 中第一个
  广告地址，包装为 `NdpResolveResult`。扫描期间清除已过期条目。
- **`get_all()`** —— 当前所有活跃 announcement 的快照。
- **`get_by_nid(nid)`** —— 精确查询，按需清理。
- **`clock`** —— 在测试中替换为单调 stub：
  `registry.clock = lambda: 1000.0`。

### `nwp_target_matches_nid(nid, target)` *(staticmethod)*

NID ↔ target 覆盖规则：

```
NID:    urn:nps:node:{authority}:{name}
Target: nwp://{authority}/{name}[/subpath]
```

节点 NID 覆盖某 target 的条件：

1. Target scheme 为 `nwp://`。
2. NID authority 等于 target authority（不区分大小写）。
3. Target path 以 `/{name}` 开头，且在此结束或以 `/…` 继续。

输入格式错误时返回 `False` 而非抛异常。

---

## 校验器

### `NdpAnnounceValidator`

使用已注册的 Ed25519 公钥校验 `AnnounceFrame` 的签名。

```python
class NdpAnnounceValidator:
    def __init__(self) -> None

    def register_public_key(self, nid: str, encoded_pub_key: str) -> None
    def remove_public_key(self, nid: str) -> None

    @property
    def known_public_keys(self) -> dict[str, str]    # 只读快照

    def validate(self, frame: AnnounceFrame) -> NdpAnnounceResult
```

`validate`（NPS-4 §7.1）：

1. 在已注册密钥中查找 `frame.nid`。缺失 →
   `NdpAnnounceResult.fail("NDP-ANNOUNCE-NID-MISMATCH", …)`。
   期望的工作流程：先校验广告方的 `IdentFrame`，然后将其
   `pub_key` 注册到此处。
2. 通过 `frame.unsigned_dict()` 构建签名 payload。
3. 调用 `NipIdentity.verify_signature(pub_key, payload, frame.signature)`。
4. 成功返回 `NdpAnnounceResult.ok()`；失败返回
   `NdpAnnounceResult.fail("NDP-ANNOUNCE-SIGNATURE-INVALID", …)`。

编码后的密钥**必须**使用 `NipIdentity.pub_key_string` 产生的
`ed25519:{base64url}` 形式。

### `NdpAnnounceResult`

```python
@dataclass(frozen=True)
class NdpAnnounceResult:
    is_valid:    bool
    error_code:  str | None = None
    message:     str | None = None

    @classmethod
    def ok(cls) -> "NdpAnnounceResult"
    @classmethod
    def fail(cls, error_code: str, message: str) -> "NdpAnnounceResult"
```

---

## 端到端示例

```python
import dataclasses, datetime
from nps_sdk.nip import NipIdentity
from nps_sdk.ndp import (
    AnnounceFrame, NdpAddress,
    InMemoryNdpRegistry, NdpAnnounceValidator,
)

# 1) 发布方生成身份
identity = NipIdentity.generate("/secure/products.key", passphrase="…")
nid      = "urn:nps:node:api.example.com:products"

# 2) 构造并签名 announce
unsigned = AnnounceFrame(
    nid          = nid,
    node_type    = "memory",
    addresses    = (NdpAddress(host="10.0.0.5", port=17433, protocol="nwp+tls"),),
    capabilities = ("nwp:query", "nwp:stream"),
    ttl          = 300,
    timestamp    = datetime.datetime.now(datetime.timezone.utc).isoformat(),
    signature    = "placeholder",
)
signed = dataclasses.replace(unsigned, signature=identity.sign(unsigned.unsigned_dict()))

# 3) 校验并广告
validator = NdpAnnounceValidator()
validator.register_public_key(nid, identity.pub_key_string)
assert validator.validate(signed).is_valid

registry = InMemoryNdpRegistry()
registry.announce(signed)

# 4) 消费方稍后解析
resolved = registry.resolve("nwp://api.example.com/products/items/42")
# → NdpResolveResult(host="10.0.0.5", port=17433, ttl=300)
```
