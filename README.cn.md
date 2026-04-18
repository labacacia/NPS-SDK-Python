[English Version](./README.md) | 中文版

# NPS Python SDK (`nps-lib`)

[![PyPI](https://img.shields.io/pypi/v/nps-lib)](https://pypi.org/project/nps-lib/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/)

**Neural Protocol Suite (NPS)** 的异步 Python SDK —— 专为 AI Agent 与神经模型设计的完整互联网协议栈。

PyPI 包：**`nps-lib`** · 导入命名空间：`nps_sdk`

---

## NPS 仓库导航

| 仓库 | 职责 | 语言 |
|------|------|------|
| [NPS-Release](https://github.com/labacacia/NPS-Release) | 协议规范（权威来源） | Markdown / YAML |
| [NPS-sdk-dotnet](https://github.com/labacacia/NPS-sdk-dotnet) | 参考实现 | C# / .NET 10 |
| **[NPS-sdk-py](https://github.com/labacacia/NPS-sdk-py)**（本仓库） | 异步 Python SDK | Python 3.11+ |
| [NPS-sdk-ts](https://github.com/labacacia/NPS-sdk-ts) | Node/浏览器 SDK | TypeScript |
| [NPS-sdk-java](https://github.com/labacacia/NPS-sdk-java) | JVM SDK | Java 21+ |
| [NPS-sdk-rust](https://github.com/labacacia/NPS-sdk-rust) | 异步 SDK | Rust stable |
| [NPS-sdk-go](https://github.com/labacacia/NPS-sdk-go) | Go SDK | Go 1.23+ |

---

## 状态

**v1.0.0-alpha.1 — Phase 1 发布**

覆盖 NPS 全部五个协议：NCP + NWP + NIP + NDP + NOP。162 个测试，**97% 覆盖率**。

## 运行要求

- Python 3.11+
- 运行时依赖：`msgpack`、`httpx`、`cryptography`

## 安装

```bash
pip install nps-lib
# 含测试 / 开发 extras
pip install "nps-lib[dev]"
```

## API 参考

完整的类与方法参考见 [`doc/`](./doc/)：

| 模块 | 说明 | 参考文档 |
|------|------|----------|
| — | 包总览、安装、快速开始 | [`doc/overview.cn.md`](./doc/overview.cn.md) |
| `nps_sdk.core` | 帧头、编解码（Tier-1 JSON / Tier-2 MsgPack）、AnchorFrame 缓存、异常 | [`doc/nps_sdk.core.cn.md`](./doc/nps_sdk.core.cn.md) |
| `nps_sdk.ncp`  | NCP 帧：`AnchorFrame`、`DiffFrame`、`StreamFrame`、`CapsFrame`、`ErrorFrame` | [`doc/nps_sdk.ncp.cn.md`](./doc/nps_sdk.ncp.cn.md) |
| `nps_sdk.nwp`  | NWP 帧：`QueryFrame`、`ActionFrame`；异步 `NwpClient` | [`doc/nps_sdk.nwp.cn.md`](./doc/nps_sdk.nwp.cn.md) |
| `nps_sdk.nip`  | NIP 帧：`IdentFrame`、`RevokeFrame`；`NipIdentity`（Ed25519） | [`doc/nps_sdk.nip.cn.md`](./doc/nps_sdk.nip.cn.md) |
| `nps_sdk.ndp`  | NDP 帧 + 内存注册表 + Announce 验证器 | [`doc/nps_sdk.ndp.cn.md`](./doc/nps_sdk.ndp.cn.md) |
| `nps_sdk.nop`  | NOP 帧、DAG 模型、异步编排客户端 | [`doc/nps_sdk.nop.cn.md`](./doc/nps_sdk.nop.cn.md) |

## 快速开始

### 编解码 NCP 帧

```python
from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp.frames import AnchorFrame, FrameSchema, SchemaField

codec = NpsFrameCodec(FrameRegistry.create_default())

schema = FrameSchema(fields=(
    SchemaField(name="id",    type="uint64"),
    SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
))
frame = AnchorFrame(anchor_id="sha256:...", schema=schema)

wire = codec.encode(frame)                  # 默认 Tier-2 MsgPack
back = codec.decode(wire)                   # → AnchorFrame
```

### AnchorFrame 缓存

```python
from nps_sdk.core.cache import AnchorFrameCache

cache = AnchorFrameCache()
anchor_id = cache.set(frame)                # 返回规范化 sha256 id
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
from nps_sdk.nwp import ActionFrame

async with NwpClient("https://node.example.com") as client:
    result = await client.invoke(
        ActionFrame(action_id="orders.create", params={"sku": "X-101", "qty": 1})
    )
```

### NIP 身份

```python
from nps_sdk.nip.identity import NipIdentity

# 生成并加密（AES-256-GCM + PBKDF2）
identity = NipIdentity.generate("ca.key", passphrase="my-secret")

# 从文件加载
identity = NipIdentity()
identity.load("ca.key", passphrase="my-secret")

# 签名 / 验签
sig = identity.sign(ident_frame.unsigned_dict())
ok  = NipIdentity.verify_signature(identity.pub_key_string, payload, sig)
```

### NDP —— 公告与解析

```python
from nps_sdk.ndp import InMemoryNdpRegistry, NdpAnnounceValidator

registry  = InMemoryNdpRegistry()
validator = NdpAnnounceValidator()
validator.register_public_key(nid, identity.pub_key_string)

await registry.announce(frame)
resolved = await registry.resolve("nwp://example.com/data")
```

### NOP —— 提交与等待

```python
from nps_sdk.nop import NopClient, TaskFrame

async with NopClient("http://orchestrator.example.com") as client:
    task_id = await client.submit(TaskFrame(task_id="job-1", dag=dag))
    status  = await client.wait(task_id, timeout=30.0)
```

## 架构

```
nps_sdk/
├── core/     # 线缆原语（FrameHeader、编解码、AnchorFrame 缓存、异常）
├── ncp/      # NCP 帧（0x01–0x0F）
├── nwp/      # NWP 帧（0x10–0x1F）+ 异步 HTTP 客户端
├── nip/      # NIP 帧（0x20–0x2F）+ Ed25519 身份
├── ndp/      # NDP 帧（0x30–0x3F）+ 注册表 + 验证器
└── nop/      # NOP 帧（0x40–0x4F）+ DAG 模型 + 编排客户端
```

## 编码分层

| Tier | 值 | 说明 |
|------|----|------|
| Tier-1 JSON    | `0x00` | UTF-8 JSON。开发 / 兼容 |
| Tier-2 MsgPack | `0x01` | MessagePack 二进制。约小 60%。**生产默认** |

## NWP HTTP Overlay

`NwpClient` 通过 HTTP 通信，`Content-Type: application/x-nps-frame`。

| 操作 | 路径 | 请求 | 响应 |
|------|------|------|------|
| Schema 锚点 | `POST /anchor` | `AnchorFrame` | `204 No Content` |
| 结构化查询 | `POST /query` | `QueryFrame` | `CapsFrame` |
| 流式查询 | `POST /stream` | `QueryFrame` | `StreamFrame` 分片 |
| Action 调用 | `POST /invoke` | `ActionFrame` | 原始结果或 `AsyncActionResponse` |

## NIP CA Server

`nip-ca-server/` 目录提供一个独立 NIP 证书颁发机构服务 —— 基于 FastAPI，SQLite 存储，开箱即用的 Docker 部署。

## 运行测试

```bash
pytest                 # 运行所有测试并生成覆盖率报告（低于 90% 会失败）
pytest -k test_nip     # 只跑 NIP 测试
```

## 许可证

Apache 2.0 —— 详见 [LICENSE](./LICENSE) 与 [NOTICE](./NOTICE)。

Copyright 2026 INNO LOTUS PTY LTD
