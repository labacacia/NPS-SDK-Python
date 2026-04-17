# NPS Python SDK (`nps-lib`)

[![PyPI](https://img.shields.io/pypi/v/nps-lib)](https://pypi.org/project/nps-lib/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB)](https://www.python.org/)

Async Python SDK for the **Neural Protocol Suite (NPS)** — a complete internet protocol stack purpose-built for AI Agents and models.

PyPI package: **`nps-lib`** · import namespace: `nps_sdk`

---

## NPS Repositories

| Repo | Role | Language |
|------|------|----------|
| [NPS-Release](https://github.com/labacacia/NPS-Release) | Protocol specifications (authoritative) | Markdown / YAML |
| [NPS-sdk-dotnet](https://github.com/labacacia/NPS-sdk-dotnet) | Reference implementation | C# / .NET 10 |
| **[NPS-sdk-py](https://github.com/labacacia/NPS-sdk-py)** (this repo) | Async Python SDK | Python 3.11+ |
| [NPS-sdk-ts](https://github.com/labacacia/NPS-sdk-ts) | Node/browser SDK | TypeScript |
| [NPS-sdk-java](https://github.com/labacacia/NPS-sdk-java) | JVM SDK | Java 21+ |
| [NPS-sdk-rust](https://github.com/labacacia/NPS-sdk-rust) | Async SDK | Rust stable |
| [NPS-sdk-go](https://github.com/labacacia/NPS-sdk-go) | Go SDK | Go 1.23+ |

---

## Status

**v1.0.0-alpha.1 — Phase 1 release**

Covers all five NPS protocols: NCP + NWP + NIP + NDP + NOP. 162 tests, **97 % coverage**.

## Requirements

- Python 3.11+
- Runtime dependencies: `msgpack`, `httpx`, `cryptography`

## Installation

```bash
pip install nps-lib
# with test/dev extras
pip install "nps-lib[dev]"
```

## Modules

| Module | Description |
|--------|-------------|
| `nps_sdk.core` | Frame header, codec (Tier-1 JSON / Tier-2 MsgPack), anchor cache, exceptions |
| `nps_sdk.ncp`  | NCP frames: `AnchorFrame`, `DiffFrame`, `StreamFrame`, `CapsFrame`, `ErrorFrame` |
| `nps_sdk.nwp`  | NWP frames: `QueryFrame`, `ActionFrame`; async `NwpClient` |
| `nps_sdk.nip`  | NIP frames: `IdentFrame`, `RevokeFrame`; `NipIdentity` (Ed25519) |
| `nps_sdk.ndp`  | NDP frames + in-memory registry + announce validator |
| `nps_sdk.nop`  | NOP frames, DAG models, async orchestration client |

## Quick Start

### Encoding / Decoding NCP Frames

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

wire = codec.encode(frame)                  # Tier-2 MsgPack by default
back = codec.decode(wire)                   # → AnchorFrame
```

### Anchor Cache

```python
from nps_sdk.core.cache import AnchorFrameCache

cache = AnchorFrameCache()
anchor_id = cache.set(frame)                # returns canonical sha256 id
frame     = cache.get_required(anchor_id)
```

### Querying a Memory Node (async)

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

### Invoking an Action Node (async)

```python
from nps_sdk.nwp import ActionFrame

async with NwpClient("https://node.example.com") as client:
    result = await client.invoke(
        ActionFrame(action_id="orders.create", params={"sku": "X-101", "qty": 1})
    )
```

### NIP Identity

```python
from nps_sdk.nip.identity import NipIdentity

# Generate & encrypt (AES-256-GCM + PBKDF2)
identity = NipIdentity.generate("ca.key", passphrase="my-secret")

# Load from file
identity = NipIdentity()
identity.load("ca.key", passphrase="my-secret")

# Sign / verify
sig = identity.sign(ident_frame.unsigned_dict())
ok  = NipIdentity.verify_signature(identity.pub_key_string, payload, sig)
```

### NDP — Announce & Resolve

```python
from nps_sdk.ndp import InMemoryNdpRegistry, NdpAnnounceValidator

registry  = InMemoryNdpRegistry()
validator = NdpAnnounceValidator()
validator.register_public_key(nid, identity.pub_key_string)

await registry.announce(frame)
resolved = await registry.resolve("nwp://example.com/data")
```

### NOP — Submit & Wait

```python
from nps_sdk.nop import NopClient, TaskFrame

async with NopClient("http://orchestrator.example.com") as client:
    task_id = await client.submit(TaskFrame(task_id="job-1", dag=dag))
    status  = await client.wait(task_id, timeout=30.0)
```

## Architecture

```
nps_sdk/
├── core/     # Wire primitives (FrameHeader, codec, cache, exceptions)
├── ncp/      # NCP frames (0x01–0x0F)
├── nwp/      # NWP frames (0x10–0x1F) + async HTTP client
├── nip/      # NIP frames (0x20–0x2F) + Ed25519 identity
├── ndp/      # NDP frames (0x30–0x3F) + registry + validator
└── nop/      # NOP frames (0x40–0x4F) + DAG models + orchestration client
```

## Encoding Tiers

| Tier | Value | Description |
|------|-------|-------------|
| Tier-1 JSON    | `0x00` | UTF-8 JSON. Development / compatibility |
| Tier-2 MsgPack | `0x01` | MessagePack binary. ~60% smaller. **Production default** |

## NWP HTTP Overlay

`NwpClient` communicates via HTTP with `Content-Type: application/x-nps-frame`.

| Operation | Path | Request | Response |
|-----------|------|---------|----------|
| Schema anchor | `POST /anchor` | `AnchorFrame` | `204 No Content` |
| Structured query | `POST /query` | `QueryFrame` | `CapsFrame` |
| Streaming query | `POST /stream` | `QueryFrame` | `StreamFrame` chunks |
| Action invocation | `POST /invoke` | `ActionFrame` | raw result or `AsyncActionResponse` |

## NIP CA Server

A standalone NIP Certificate Authority server is bundled under [`nip-ca-server/`](./nip-ca-server/) — FastAPI, SQLite-backed, Docker-ready.

## Running Tests

```bash
pytest                 # all tests + coverage report (fail under 90%)
pytest -k test_nip     # NIP tests only
```

## License

Apache 2.0 — see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).

Copyright 2026 INNO LOTUS PTY LTD
