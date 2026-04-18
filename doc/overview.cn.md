[English Version](./overview.md) | 中文版

# NPS Python SDK — 总览

> **PyPI**：[`nps-lib`](https://pypi.org/project/nps-lib/) · **导入命名空间**：`nps_sdk` · **Python**：≥ 3.11
> **版本**：1.0.0-alpha.1 · **许可证**：Apache-2.0
> **规范**：[NPS-0 Overview](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-0-Overview.cn.md)

本 SDK 是 **Neural Protocol Suite** 的异步 Python 实现 ——
面向 AI Agent 与神经模型的完整互联网协议栈。它提供完整的
帧编解码、全部协议帧（NCP / NWP / NIP / NDP / NOP）、NWP 和 NOP
的异步 HTTP 客户端、内存 NDP 注册表以及 Ed25519 身份工具。

---

## 包结构

```
nps_sdk/
├── core/         # 线缆原语：FrameHeader、编解码、AnchorFrame 缓存、注册表
├── ncp/          # NCP 帧：Anchor / Diff / Stream / Caps / Error
├── nwp/          # NWP 帧 + 异步 NwpClient
├── nip/          # NIP 帧 + Ed25519 NipIdentity
├── ndp/          # NDP 帧 + 内存注册表 + 签名验证器
└── nop/          # NOP 帧 + DAG 模型 + 异步 NopClient
```

按模块参考：

| 模块 | 文档 | 职责 |
|------|------|------|
| `nps_sdk.core` | [`nps_sdk.core.cn.md`](./nps_sdk.core.cn.md) | 线缆级编解码管线、帧头、注册表、异常 |
| `nps_sdk.ncp`  | [`nps_sdk.ncp.cn.md`](./nps_sdk.ncp.cn.md) | Neural Communication Protocol 帧 |
| `nps_sdk.nwp`  | [`nps_sdk.nwp.cn.md`](./nps_sdk.nwp.cn.md) | Neural Web Protocol —— Query/Action + 客户端 |
| `nps_sdk.nip`  | [`nps_sdk.nip.cn.md`](./nps_sdk.nip.cn.md) | Neural Identity Protocol —— Ident/Revoke + 密钥 |
| `nps_sdk.ndp`  | [`nps_sdk.ndp.cn.md`](./nps_sdk.ndp.cn.md) | Neural Discovery Protocol —— Announce/Resolve/Graph |
| `nps_sdk.nop`  | [`nps_sdk.nop.cn.md`](./nps_sdk.nop.cn.md) | Neural Orchestration Protocol —— Task/Delegate/Sync |

---

## 安装

```bash
pip install nps-lib

# 开发 / 测试 extras
pip install "nps-lib[dev]"
```

运行时依赖：`msgpack>=1.0.8`、`httpx>=0.27.0`、`cryptography>=43.0.0`。

---

## 最小端到端示例

```python
import asyncio
from nps_sdk.core import EncodingTier
from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp import AnchorFrame, FrameSchema, SchemaField
from nps_sdk.nwp import NwpClient, QueryFrame

registry = FrameRegistry.create_full()     # NCP + NWP + NIP + NDP + NOP
codec    = NpsFrameCodec(registry)

# 1) 构造并编解码 AnchorFrame 以测试往返
schema = FrameSchema(fields=(
    SchemaField(name="id",    type="uint64"),
    SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
))
anchor = AnchorFrame(anchor_id="sha256:…", schema=schema, ttl=3600)

wire  = codec.encode(anchor)               # 默认 Tier-2 MsgPack
back  = codec.decode(wire)                 # → AnchorFrame

# 2) 通过 NWP 向远端 Memory Node 请求数据
async def main() -> None:
    async with NwpClient("https://products.example.com") as client:
        caps = await client.query(QueryFrame(anchor_ref=anchor.anchor_id, limit=10))
        for row in caps.data:
            print(row)

asyncio.run(main())
```

---

## 编码分层决议（NPS-1 §3.2）

1. 如果传入 `NpsFrameCodec.encode(frame, override_tier=…)`，使用它。
2. 否则使用编解码器的默认 Tier（默认为 **Tier-2 MsgPack**）。
3. Tier-3 为保留；未知 Tier 位会抛出 `NpsCodecError`。

`FrameHeader` 将 Tier 存在 flag 位 0–1：

```
┌──────┬──────┬────────────────┬────────────────────┐
│ Byte │ Bits │ 默认帧头        │ 扩展帧头            │
├──────┼──────┼────────────────┼────────────────────┤
│  0   │ 0–7  │ FrameType      │ FrameType          │
│  1   │ 0–1  │ Tier（0 或 1） │ Tier（0 或 1）      │
│      │  2   │ FINAL          │ FINAL              │
│      │  3   │ ENCRYPTED      │ ENCRYPTED          │
│      │  7   │ EXT = 0        │ EXT = 1            │
│ 2–3  │      │ Payload uint16 │ 保留（MBZ）         │
│ 4–7  │      │ —              │ Payload uint32     │
└──────┴──────┴────────────────┴────────────────────┘
```

负载 > 64 KiB 会自动升级为 8 字节扩展帧头。

---

## 异步约定

- 所有 I/O 客户端（`NwpClient`、`NopClient`）都是 **异步** 的；使用
  `async with` 或显式调用 `close()`。
- 如果你自带 `httpx.AsyncClient`，SDK **不会** 关闭它 ——
  所有权仍归你。
- 流式方法返回 `AsyncIterator[...]`；用 `async for` 迭代。

---

## 异常层级

```
NpsError                       # 基类
├── NpsFrameError              # 帧格式错误 / 未注册帧
├── NpsCodecError              # 编解码失败
├── NpsAnchorNotFoundError     # 缓存中缺 anchor_id（属性：anchor_id）
└── NpsAnchorPoisonError       # 相同 anchor_id 但 schema 不同（属性：anchor_id）
```

NOP 操作通过 `AlignStreamFrame.error`（一个 `StreamError`）
或 `NopTaskStatus.error_code` / `.error_message` 暴露协议错误。HTTP 层
失败以 `httpx.HTTPStatusError` 向上传播。

---

## 规范参考

| 层 | 规范 |
|----|------|
| 线缆封帧 | [NPS-1 NCP v0.4](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-1-NCP.cn.md) |
| Memory / Action 节点 | [NPS-2 NWP v0.4](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-2-NWP.cn.md) |
| 身份 + Ed25519 | [NPS-3 NIP v0.2](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-3-NIP.cn.md) |
| 发现 | [NPS-4 NDP v0.2](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-4-NDP.cn.md) |
| 编排 | [NPS-5 NOP v0.3](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-5-NOP.cn.md) |
| 帧注册表 | [`frame-registry.yaml`](https://github.com/labacacia/NPS-Release/blob/main/spec/frame-registry.yaml) |
| 错误码 | [`error-codes.cn.md`](https://github.com/labacacia/NPS-Release/blob/main/spec/error-codes.cn.md) |
