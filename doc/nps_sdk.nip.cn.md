[English Version](./nps_sdk.nip.md) | 中文版

# `nps_sdk.nip` — 类与方法参考

> 根模块：`nps_sdk.nip`
> 规范：[NPS-3 NIP v0.2](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-3-NIP.md)

NIP 是 NPS 的 TLS/PKI。本模块暴露身份帧（`IdentFrame`、
`RevokeFrame`）、它们的元数据模型（`IdentMetadata`），
以及拥有 Ed25519 密钥对的 `NipIdentity` 辅助类
（静态加密采用 AES-256-GCM + PBKDF2-SHA256）。

---

## 目录

- [`IdentMetadata`](#identmetadata)
- [帧](#帧)
  - [`IdentFrame` (0x20)](#identframe-0x20)
  - [`RevokeFrame` (0x22)](#revokeframe-0x22)
- [`NipIdentity`](#nipidentity)
- [规范化 JSON + 签名格式](#规范化-json--签名格式)
- [端到端示例](#端到端示例)

---

## `IdentMetadata`

```python
@dataclass(frozen=True)
class IdentMetadata:
    model_family: str | None = None
    tokenizer:    str | None = None
    runtime:      str | None = None

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IdentMetadata"
```

附加到 `IdentFrame.metadata` 的可选元数据。排除在签名计算之外
—— 它是运行时填充的提示，不是身份的一部分。

---

## 帧

### `IdentFrame` (0x20)

Agent 身份证书（NPS-3 §3）。作为任何已认证 session 的开场帧发送。

```python
@dataclass(frozen=True)
class IdentFrame(NpsFrame):
    nid:          str                       # urn:nps:agent:{authority}:{name}
    pub_key:      str                       # "ed25519:{base64url(DER)}"
    capabilities: tuple[str, ...]
    scope:        Any
    issued_by:    str                       # 颁发者 NID
    issued_at:    str                       # ISO 8601 UTC
    expires_at:   str
    serial:       str                       # 每个颁发者单调递增
    signature:    str                       # "ed25519:{base64url}"
    metadata:     IdentMetadata | None = None

    def unsigned_dict(self) -> dict[str, Any]
```

`unsigned_dict()` 返回用作签名输入的 dict：内容与 `to_dict()`
相同，但剥离了 `signature` 和 `metadata` 字段。

### `RevokeFrame` (0x22)

证书吊销（NPS-3 §9）。

```python
@dataclass(frozen=True)
class RevokeFrame(NpsFrame):
    target_nid: str
    serial:     str
    reason:     str           # 如 "key_compromise"、"superseded"
    revoked_at: str           # ISO 8601 UTC
    signature:  str           # "ed25519:{base64url}" —— 由 CA 签名

    def unsigned_dict(self) -> dict[str, Any]
```

由颁发 CA 签名。校验者**必须**拒绝使用任何被有效 `RevokeFrame`
覆盖的 `IdentFrame`（按 `nid` + `serial` 匹配）。

> **说明：** `TrustFrame`（类型 0x21）在规范的帧注册表中存在，
> 但本 SDK 未将其实现为 dataclass —— 信任锚分发目前不在 Agent
> 侧库的范围内。

---

## `NipIdentity`

由加密 keyfile 支持的 Ed25519 密钥对管理器。

磁盘文件格式：

```
[ version (1 B) = 0x01 ]
[ salt    (16 B) ]
[ nonce   (12 B) ]
[ ciphertext (enc{private_key(32 B) || public_key(32 B)}) ]
[ auth_tag (16 B, GCM) ]
```

密钥派生：**PBKDF2-SHA256**，600 000 轮；加密算法：**AES-256-GCM**。

```python
class NipIdentity:
    def __init__(self) -> None

    @classmethod
    def generate(cls, key_file_path: str, passphrase: str) -> "NipIdentity"

    def load(self, key_file_path: str, passphrase: str) -> None
    @property
    def is_loaded(self) -> bool

    @property
    def public_key(self) -> Ed25519PublicKey
    @property
    def pub_key_string(self) -> str

    def sign(self, payload: dict[str, Any]) -> str

    @staticmethod
    def verify_signature(
        pub_key_str: str,
        payload:     dict[str, Any],
        signature_str: str,
    ) -> bool
```

### `generate(key_file_path, passphrase) -> NipIdentity` *(classmethod)*

生成全新的密钥对、写入加密 keyfile、返回已加载的 `NipIdentity`。
若目标路径已存在则会被覆盖 —— 请先备份。

### `load(key_file_path, passphrase)`

就地解密已有 keyfile。抛出：

- 路径不存在时 `FileNotFoundError`。
- 口令错误或文件损坏（GCM 认证失败）时 `ValueError`。

### `public_key` / `pub_key_string`

`public_key` 返回原生 `cryptography` 对象；填充 `IdentFrame.pub_key`
时使用 `pub_key_string` —— 它产生 NPS 其他位置使用的
`ed25519:{base64url(DER)}` 形式。

### `sign(payload) -> str`

按下面规则规范化 `payload`、用已加载私钥签名，返回
`"ed25519:{base64url(signature)}"`。`is_loaded is False` 时抛 `RuntimeError`。

### `verify_signature(pub_key_str, payload, signature_str) -> bool` *(staticmethod)*

无需加载密钥对即可校验签名。签名错误时返回 `False`（不抛异常）
—— 这是故意宽松的，以便调用方生成人类可读的错误消息。

---

## 规范化 JSON + 签名格式

`sign` 和 `verify_signature` 在触及 Ed25519 原语之前都会规范化 payload：

1. 丢弃任何值为 `None` 的键。
2. 对剩余键在每一级按字典序排序。
3. 用 `separators=(",", ":")` 和 `ensure_ascii=False` 序列化。

得到的 UTF-8 字节就是实际被签名的数据。对于 `IdentFrame` 和
`AnnounceFrame`，将帧的 `unsigned_dict()` 作为 payload ——
它已经剥离了 `signature`（和 `metadata`）字段。

线路格式为 `"ed25519:"` + 64 字节 Ed25519 签名的
base64url（无 padding）。

---

## 端到端示例

```python
import asyncio, datetime
from nps_sdk.nip import IdentFrame, IdentMetadata, NipIdentity

# 1) 一次性：创建密钥对
identity = NipIdentity.generate("/secure/agent.key", passphrase="correct horse battery")

# 2) 构造并签名一个 IdentFrame
nid      = "urn:nps:agent:example.com:agent-001"
unsigned = IdentFrame(
    nid          = nid,
    pub_key      = identity.pub_key_string,
    capabilities = ("nwp:query", "nop:delegate"),
    scope        = {"read": ["products:*"], "write": []},
    issued_by    = "urn:nps:ca:example.com:root",
    issued_at    = datetime.datetime.now(datetime.timezone.utc).isoformat(),
    expires_at   = (datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(days=30)).isoformat(),
    serial       = "000001",
    signature    = "placeholder",
    metadata     = IdentMetadata(model_family="sonnet-4.6"),
)
signed = dataclass_replace(unsigned, signature=identity.sign(unsigned.unsigned_dict()))
# （实际代码使用 dataclasses.replace —— 此处为简洁记作 dataclass_replace）

# 3) 任何持有 pub_key 的一方都可以校验
ok = NipIdentity.verify_signature(
    identity.pub_key_string,
    signed.unsigned_dict(),
    signed.signature,
)
assert ok
```
