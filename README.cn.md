[English Version](./README.md) | 中文版

# NPS Python SDK (`nps-lib`)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](../../LICENSE)
[![Release](https://img.shields.io/badge/release-v1.0.0--alpha.15-orange.svg)](../../CHANGELOG.cn.md)
[![NCP](https://img.shields.io/badge/NCP-v0.9-5b8cff.svg)]()
[![NWP](https://img.shields.io/badge/NWP-v0.14-4af0b0.svg)]()
[![NIP](https://img.shields.io/badge/NIP-v0.10-7b61ff.svg)]()
[![NDP](https://img.shields.io/badge/NDP-v0.9-f0a050.svg)]()
[![NOP](https://img.shields.io/badge/NOP-v0.7-ff8c42.svg)]()

面向 **Neural Protocol Suite (NPS)** 的 Python 客户端库 —— 为 AI Agent 与模型设计的完整互联网协议栈。

PyPI 包名：`nps-lib` | Python 命名空间：`nps_sdk`

## 状态

**v1.0.0-alpha.15 —— RFC-0002 跨 SDK 端口波（第二棒语言）**

包含 NCP + NWP + NIP + NDP + NOP 全部五个协议的帧定义和异步客户端，**加完整 NPS-RFC-0002 X.509 + ACME `agent-01` NID 证书原语**（`nps_sdk.nip.x509` + `nps_sdk.nip.acme`）。

测试数：221 个（覆盖 SDK + RFC-0002/0003/0004），全绿。

Alpha.14 候选新增：远程 NIP CA 类型化客户端（`nps_sdk.nip.NipCaClient`）、native-mode NWP 服务端 helper（`nps_sdk.nwp.NwpNativeNodeServer`）和 TC-N1/TC-N2 一致性 manifest helper（`nps_sdk.conformance`）。

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
| `nps_sdk.nwp`  | NWP 帧：QueryFrame、ActionFrame；异步 `NwpClient`；`NwpNativeNodeServer` native 服务端 |
| `nps_sdk.nip`        | NIP 帧：IdentFrame（v2 双信任）、TrustFrame、RevokeFrame；`NipIdentity`（Ed25519）；`NipIdentVerifier` + `NipVerifierOptions`（RFC-0002 §8.1 双信任）；`AssuranceLevel`（RFC-0003）；远程 CA `NipCaClient` |
| `nps_sdk.nip.x509`   | RFC-0002 X.509 NID 证书：`NipX509Builder` / `NipX509Verifier` / `NpsX509Oids` |
| `nps_sdk.nip.acme`   | RFC-0002 ACME `agent-01`：`AcmeClient` / `AcmeServer`（进程内） / JWS helpers / messages |
| `nps_sdk.ndp`  | NDP 帧：AnnounceFrame、ResolveFrame、GraphFrame；内存注册表 + 校验器 |
| `nps_sdk.nop`  | NOP 帧：TaskFrame、DelegateFrame、SyncFrame、AlignStreamFrame；异步 `NopClient` |
| `nps_sdk.conformance` | TC-N1/TC-N2 一致性用例目录、manifest 构造器和校验器 |

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

### Native NWP 服务端

```python
from nps_sdk.nwp import NwpNativeNodeServer

server = NwpNativeNodeServer(
    query_handler=lambda query: [{"id": 42}],
    action_handler=lambda action: {"action": action.action_id},
)

# `reader`/`writer` 已完成 NCP preamble、TLS 和 Hello negotiation。
await server.serve(reader, writer)
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

### NIP 远程 CA Client

```python
from nps_sdk.nip import NipCaClient, NipCaRegisterRequest

async with NipCaClient("https://ca.example.com", route_prefix="/nip") as ca:
    discovery = await ca.get_discovery()
    ident = await ca.register_agent(
        NipCaRegisterRequest("agent-a", "ed25519:<pub>", ("nwp:query",)),
        bearer_token="token",
    )
    status = await ca.verify_agent(ident.nid)
```

### 一致性 Manifest

```python
from nps_sdk.conformance import (
    NODE_L1,
    NpsConformanceCaseResult,
    NpsConformanceManifest,
    catalog_for_profile,
    validate_manifest,
)

results = [NpsConformanceCaseResult(case.id, "pass") for case in catalog_for_profile(NODE_L1)]
manifest = NpsConformanceManifest.create(
    profile=NODE_L1,
    iut_name="my-node",
    iut_version="1.0.0-alpha.15",
    iut_nid="urn:nps:node:example.com:my-node",
    peer_name="labacacia-fixture",
    peer_version="1.0.0-alpha.15",
    results=results,
)
result = validate_manifest(manifest)
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

Apache 2.0 —— 详见 [LICENSE](https://github.com/labacacia/NPS-Dev/blob/main/LICENSE)。

Copyright 2026 INNO LOTUS PTY LTD
