English | [中文版](./overview.cn.md)

# NPS Python SDK — Overview

> **PyPI**: [`nps-lib`](https://pypi.org/project/nps-lib/) · **Import namespace**: `nps_sdk` · **Python**: ≥ 3.11
> **Version**: 1.0.0-alpha.1 · **License**: Apache-2.0
> **Spec**: [NPS-0 Overview](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-0-Overview.md)

This SDK is the async Python implementation of the **Neural Protocol Suite** —
a complete internet protocol stack for AI Agents and models. It ships the full
frame codec, all protocol frames (NCP / NWP / NIP / NDP / NOP), async HTTP
clients for NWP and NOP, an in-memory NDP registry, and an Ed25519 identity
toolkit for NIP.

---

## Package layout

```
nps_sdk/
├── core/         # Wire primitives: FrameHeader, codec, cache, registry
├── ncp/          # NCP frames: Anchor / Diff / Stream / Caps / Error
├── nwp/          # NWP frames + async NwpClient
├── nip/          # NIP frames + Ed25519 NipIdentity
├── ndp/          # NDP frames + in-memory registry + signature validator
└── nop/          # NOP frames + DAG models + async NopClient
```

Per-module reference:

| Module | Document | Role |
|--------|----------|------|
| `nps_sdk.core` | [`nps_sdk.core.md`](./nps_sdk.core.md) | Wire-level codec pipeline, header, registry, exceptions |
| `nps_sdk.ncp`  | [`nps_sdk.ncp.md`](./nps_sdk.ncp.md) | Neural Communication Protocol frames |
| `nps_sdk.nwp`  | [`nps_sdk.nwp.md`](./nps_sdk.nwp.md) | Neural Web Protocol — Query/Action + client |
| `nps_sdk.nip`  | [`nps_sdk.nip.md`](./nps_sdk.nip.md) | Neural Identity Protocol — Ident/Revoke + keys |
| `nps_sdk.ndp`  | [`nps_sdk.ndp.md`](./nps_sdk.ndp.md) | Neural Discovery Protocol — Announce/Resolve/Graph |
| `nps_sdk.nop`  | [`nps_sdk.nop.md`](./nps_sdk.nop.md) | Neural Orchestration Protocol — Task/Delegate/Sync |

---

## Install

```bash
pip install nps-lib

# dev / test extras
pip install "nps-lib[dev]"
```

Runtime dependencies: `msgpack>=1.0.8`, `httpx>=0.27.0`, `cryptography>=43.0.0`.

---

## Minimal end-to-end example

```python
import asyncio
from nps_sdk.core import EncodingTier
from nps_sdk.core.codec import NpsFrameCodec
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.ncp import AnchorFrame, FrameSchema, SchemaField
from nps_sdk.nwp import NwpClient, QueryFrame

registry = FrameRegistry.create_full()     # NCP + NWP + NIP + NDP + NOP
codec    = NpsFrameCodec(registry)

# 1) Build and encode an AnchorFrame to test the codec round-trip
schema = FrameSchema(fields=(
    SchemaField(name="id",    type="uint64"),
    SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
))
anchor = AnchorFrame(anchor_id="sha256:…", schema=schema, ttl=3600)

wire  = codec.encode(anchor)               # default Tier-2 MsgPack
back  = codec.decode(wire)                 # → AnchorFrame

# 2) Ask a remote Memory Node for data over NWP
async def main() -> None:
    async with NwpClient("https://products.example.com") as client:
        caps = await client.query(QueryFrame(anchor_ref=anchor.anchor_id, limit=10))
        for row in caps.data:
            print(row)

asyncio.run(main())
```

---

## Encoding tier resolution (NPS-1 §3.2)

1. If `NpsFrameCodec.encode(frame, override_tier=…)` is given, use it.
2. Otherwise use the codec's default tier (defaults to **Tier-2 MsgPack**).
3. Tier-3 is reserved; unknown tier bits raise `NpsCodecError`.

`FrameHeader` stores the tier in flag bits 0–1:

```
┌──────┬──────┬────────────────┬────────────────────┐
│ Byte │ Bits │ Default header │ Extended header    │
├──────┼──────┼────────────────┼────────────────────┤
│  0   │ 0–7  │ FrameType      │ FrameType          │
│  1   │ 0–1  │ Tier (0 or 1)  │ Tier (0 or 1)      │
│      │  2   │ FINAL          │ FINAL              │
│      │  3   │ ENCRYPTED      │ ENCRYPTED          │
│      │  7   │ EXT = 0        │ EXT = 1            │
│ 2–3  │      │ Payload uint16 │ Reserved (MBZ)     │
│ 4–7  │      │ —              │ Payload uint32     │
└──────┴──────┴────────────────┴────────────────────┘
```

Payload > 64 KiB automatically promotes to the 8-byte extended header.

---

## Async conventions

- All I/O clients (`NwpClient`, `NopClient`) are **async-only**; use
  `async with` or call `close()` explicitly.
- If you pass your own `httpx.AsyncClient`, the SDK will **not** close it —
  ownership stays with you.
- Streaming methods return `AsyncIterator[...]`; iterate with `async for`.

---

## Error hierarchy

```
NpsError                       # base
├── NpsFrameError              # malformed / unregistered frame
├── NpsCodecError              # encode / decode failure
├── NpsAnchorNotFoundError     # anchor_id missing in cache (attr: anchor_id)
└── NpsAnchorPoisonError       # same anchor_id, different schema (attr: anchor_id)
```

NOP operations surface protocol errors through `AlignStreamFrame.error`
(a `StreamError`) or `NopTaskStatus.error_code` / `.error_message`. HTTP-layer
failures propagate as `httpx.HTTPStatusError`.

---

## Spec references

| Layer | Spec |
|-------|------|
| Wire framing | [NPS-1 NCP v0.4](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-1-NCP.md) |
| Memory / Action nodes | [NPS-2 NWP v0.4](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-2-NWP.md) |
| Identity + Ed25519 | [NPS-3 NIP v0.2](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-3-NIP.md) |
| Discovery | [NPS-4 NDP v0.2](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-4-NDP.md) |
| Orchestration | [NPS-5 NOP v0.3](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-5-NOP.md) |
| Frame registry | [`frame-registry.yaml`](https://github.com/labacacia/NPS-Release/blob/main/spec/frame-registry.yaml) |
| Error codes | [`error-codes.md`](https://github.com/labacacia/NPS-Release/blob/main/spec/error-codes.md) |
