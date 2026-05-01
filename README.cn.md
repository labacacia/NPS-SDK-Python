[English Version](./README.md) | 中文版

# NPS Python SDK (`nps-lib`)

面向 **Neural Protocol Suite (NPS)** 的 Python 客户端库 —— 为 AI Agent 与模型设计的完整互联网协议栈。

PyPI 包名：`nps-lib` | Python 命名空间：`nps_sdk`

## 状态

**v1.0.0-alpha.5 —— NWP 错误码 + NIP gossip 错误码**

包含 NCP + NWP + NIP + NDP + NOP 全部五个协议的帧定义和异步客户端，**加完整 NPS-RFC-0002 X.509 + ACME `agent-01` NID 证书原语**（`nps_sdk.nip.x509` + `nps_sdk.nip.acme`）。

**alpha.5 新增：**

- `nps_sdk.nwp.error_codes` —— 30 个 NWP wire 错误码常量（`NWP-AUTH-*`、`NWP-QUERY-*`、`NWP-TOPOLOGY-*`、`NWP-RESERVED-TYPE-UNSUPPORTED` 等）。
- `nps_sdk.nip.error_codes` —— 新增 `REPUTATION_GOSSIP_FORK` / `REPUTATION_GOSSIP_SIG_INVALID` 常量（RFC-0004 Phase 3）。
- `AssuranceLevel.from_wire("")` 改为返回 `ANONYMOUS`，不再抛 `ValueError`（spec §5.1.1 修复）。
- `nps_sdk.ndp.dns_txt` —— DNS TXT 回退解析：目标不在内存注册表时，`resolve_via_dns(target, dns_lookup=None)` 查询 `_nps-node.<host>` TXT 记录（`v=nps1 nid=... port=... fp=...`）并返回首条有效匹配。

测试数：221 个，全绿。

## 环境要求

- Python 3.11+
- 依赖：`msgpack`、`httpx`、`cryptography`

## 安装

```bash
pip install nps-lib
```

开发模式：

```bash
pip install "nps-lib[dev]"
```

## 模块

| 模块 | 说明 |
|------|------|
| `nps_sdk.core` | 帧头、编解码器（Tier-1 JSON / Tier-2 MsgPack）、anchor 缓存、异常类型 |
| `nps_sdk.ncp`  | NCP 帧：AnchorFrame、DiffFrame、StreamFrame、CapsFrame、HelloFrame、ErrorFrame |
| `nps_sdk.nwp`  | NWP 帧：QueryFrame、ActionFrame；异步 `NwpClient` |
| `nps_sdk.nwp.error_codes` | NWP wire 错误码常量（30 个：auth、query、action、task、subscribe、infrastructure、manifest、topology、reserved-type） |
| `nps_sdk.nip`        | NIP 帧：IdentFrame（v2 双信任）、TrustFrame、RevokeFrame；`NipIdentity`（Ed25519）；`NipIdentVerifier` + `NipVerifierOptions`（RFC-0002 §8.1 双信任）；`AssuranceLevel`（RFC-0003） |
| `nps_sdk.nip.x509`   | RFC-0002 X.509 NID 证书：`NipX509Builder` / `NipX509Verifier` / `NpsX509Oids` |
| `nps_sdk.nip.acme`   | RFC-0002 ACME `agent-01`：`AcmeClient` / `AcmeServer`（进程内） / JWS helpers / messages |
| `nps_sdk.ndp`  | NDP 帧：AnnounceFrame、ResolveFrame、GraphFrame；内存注册表 + 校验器；DNS TXT 回退解析（`resolve_via_dns`、`nps_sdk.ndp.dns_txt`） |
| `nps_sdk.nop`  | NOP 帧：TaskFrame、DelegateFrame、SyncFrame、AlignStreamFrame；异步 `NopClient` |

## 快速开始

### NCP 帧编解码

```python
from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp.frames import AnchorFrame, FrameSchema, SchemaField

registry = FrameRegistry.create_default()
codec    = NpsFrameCodec(registry)

schema = FrameSchema(fields=(
    SchemaField(name="id",    type="uint64"),
    SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
))
frame  = AnchorFrame(anchor_id="sha256:...", schema=schema)

wire   = codec.encode(frame)           # bytes — 默认 Tier-2 MsgPack
result = codec.decode(wire)            # → AnchorFrame
```

### Anchor 缓存（Schema 去重）

```python
from nps_sdk.core.cache import AnchorFrameCache

cache     = AnchorFrameCache()
anchor_id = cache.set(frame)           # 存入并返回规范 sha256 anchor_id
frame     = cache.get_required(anchor_id)
```

### 查询 Memory Node（异步）

```python
import asyncio
from nps_sdk.nwp import NwpClient, QueryFrame

async def main():
    async with NwpClient("https://node.example.com") as client:
        caps = await client.query(
            QueryFrame(anchor_ref="sha256:...", limit=50)
        )
        print(caps.count, caps.data)

asyncio.run(main())
```

### 调用 Action Node（异步）

```python
from nps_sdk.nwp import NwpClient, ActionFrame

async with NwpClient("https://node.example.com") as client:
    result = await client.invoke(
        ActionFrame(action_id="orders.create", params={"sku": "X-101", "qty": 1})
    )
```

### NIP 身份管理

```python
from nps_sdk.nip.identity import NipIdentity

# 生成并保存加密的 Ed25519 密钥对
identity = NipIdentity.generate("ca.key", passphrase="my-secret")

# 从文件加载
identity = NipIdentity()
identity.load("ca.key", passphrase="my-secret")

# 对 NIP 帧 payload 签名（规范化 JSON，不含 'signature' 字段）
sig = identity.sign(ident_frame.unsigned_dict())

# 验签
ok = NipIdentity.verify_signature(identity.pub_key_string, payload, sig)
```

## 架构

```
nps_sdk/
├── core/          # 线上原语（FrameHeader、codec、cache、exceptions）
├── ncp/           # NCP 帧（0x01–0x0F）
├── nwp/           # NWP 帧（0x10–0x1F）+ 异步 HTTP 客户端
├── nip/           # NIP 帧（0x20–0x2F）+ Ed25519 身份
├── ndp/           # NDP 帧（0x30–0x3F）+ 内存注册表
└── nop/           # NOP 帧（0x40–0x4F）+ 异步 NopClient
```

### 帧编码 Tier

| Tier | 值 | 说明 |
|------|----|------|
| Tier-1 JSON    | `0x00` | UTF-8 JSON，用于开发 / 兼容场景 |
| Tier-2 MsgPack | `0x01` | MessagePack 二进制，体积缩小约 60%。**生产环境默认值。** |

### NWP HTTP Overlay 模式

`NwpClient` 通过 HTTP 以 `Content-Type: application/x-nps-frame` 通信。按操作划分子路径：

| 操作 | 路径 | 请求帧 | 响应帧 |
|------|------|--------|--------|
| Schema anchor | `POST /anchor` | AnchorFrame | 204 |
| 结构化查询 | `POST /query` | QueryFrame | CapsFrame |
| 流式查询 | `POST /stream` | QueryFrame | StreamFrame 分片 |
| Action 调用 | `POST /invoke` | ActionFrame | 原始结果或 AsyncActionResponse |

## 运行测试

```bash
pytest                 # 全部测试 + 覆盖率报告
pytest -k test_nip     # 仅 NIP 测试
```

覆盖率目标：≥ 90 %。

## 许可证

Apache 2.0 —— 详见 [LICENSE](../../LICENSE)。

Copyright 2026 INNO LOTUS PTY LTD
