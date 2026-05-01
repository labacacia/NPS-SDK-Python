English | [中文版](./README.cn.md)

# NPS Python SDK (`nps-lib`)

Python client library for the **Neural Protocol Suite (NPS)** — a complete internet protocol stack designed for AI agents and models.

PyPI package: `nps-lib` | Python namespace: `nps_sdk`

## Status

**v1.0.0-alpha.5 — NWP error codes + NIP gossip error codes**

Covers all five protocols — NCP + NWP + NIP + NDP + NOP — frame definitions, async client, Ed25519 identity management, **plus full NPS-RFC-0002 X.509 + ACME `agent-01` NID certificate primitives** (`nps_sdk.nip.x509` + `nps_sdk.nip.acme`).

**alpha.5 additions:**

- `nps_sdk.nwp.error_codes` — 30 NWP wire error code constants (`NWP-AUTH-*`, `NWP-QUERY-*`, `NWP-TOPOLOGY-*`, `NWP-RESERVED-TYPE-UNSUPPORTED`, …).
- `nps_sdk.nip.error_codes` — new `REPUTATION_GOSSIP_FORK` / `REPUTATION_GOSSIP_SIG_INVALID` constants (RFC-0004 Phase 3).
- `AssuranceLevel.from_wire("")` returns `ANONYMOUS` instead of raising `ValueError` (spec §5.1.1 fix).
- `nps_sdk.ndp.dns_txt` — DNS TXT fallback resolution: `resolve_via_dns(target, dns_lookup=None)` looks up `_nps-node.<host>` TXT records (`v=nps1 nid=... port=... fp=...`) when a target is not in the in-memory registry.

Tests: 221 across the SDK, all passing.

## Requirements

- Python 3.11+
- Dependencies: `msgpack`, `httpx`, `cryptography`

## Installation

```bash
pip install nps-lib
```

For development:

```bash
pip install "nps-lib[dev]"
```

## Modules

| Module | Description |
|--------|-------------|
| `nps_sdk.core` | Frame header, codec (Tier-1 JSON / Tier-2 MsgPack), anchor cache, exceptions |
| `nps_sdk.ncp`  | NCP frames: AnchorFrame, DiffFrame, StreamFrame, CapsFrame, HelloFrame, ErrorFrame |
| `nps_sdk.nwp`  | NWP frames: QueryFrame, ActionFrame; async `NwpClient` |
| `nps_sdk.nwp.error_codes` | NWP wire error code constants (30 codes: auth, query, action, task, subscribe, infrastructure, manifest, topology, reserved-type) |
| `nps_sdk.nip`        | NIP frames: IdentFrame (v2 dual-trust), TrustFrame, RevokeFrame; `NipIdentity` (Ed25519); `NipIdentVerifier` + `NipVerifierOptions` (RFC-0002 §8.1 dual-trust); `AssuranceLevel` (RFC-0003) |
| `nps_sdk.nip.x509`   | RFC-0002 X.509 NID certs: `NipX509Builder` / `NipX509Verifier` / `NpsX509Oids` |
| `nps_sdk.nip.acme`   | RFC-0002 ACME `agent-01`: `AcmeClient` / `AcmeServer` (in-process) / JWS helpers / messages |
| `nps_sdk.ndp`  | NDP frames: AnnounceFrame, ResolveFrame, GraphFrame; in-memory registry + validator; DNS TXT fallback resolution (`resolve_via_dns`, `nps_sdk.ndp.dns_txt`) |
| `nps_sdk.nop`  | NOP frames: TaskFrame, DelegateFrame, SyncFrame, AlignStreamFrame; async `NopClient` |

## Quick Start

### Encoding / Decoding NCP Frames

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

wire   = codec.encode(frame)           # bytes — Tier-2 MsgPack by default
result = codec.decode(wire)            # → AnchorFrame
```

### Anchor Cache (Schema Deduplication)

```python
from nps_sdk.core.cache import AnchorFrameCache

cache     = AnchorFrameCache()
anchor_id = cache.set(frame)           # stores; returns canonical sha256 anchor_id
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
from nps_sdk.nwp import NwpClient, ActionFrame

async with NwpClient("https://node.example.com") as client:
    result = await client.invoke(
        ActionFrame(action_id="orders.create", params={"sku": "X-101", "qty": 1})
    )
```

### NIP Identity Management

```python
from nps_sdk.nip.identity import NipIdentity

# Generate and save an encrypted Ed25519 keypair
identity = NipIdentity.generate("ca.key", passphrase="my-secret")

# Load from file
identity = NipIdentity()
identity.load("ca.key", passphrase="my-secret")

# Sign a NIP frame payload (canonical JSON, no 'signature' field)
sig = identity.sign(ident_frame.unsigned_dict())

# Verify
ok = NipIdentity.verify_signature(identity.pub_key_string, payload, sig)
```

## Architecture

```
nps_sdk/
├── core/          # Wire primitives (FrameHeader, codec, cache, exceptions)
├── ncp/           # NCP frames (0x01–0x0F)
├── nwp/           # NWP frames (0x10–0x1F) + async HTTP client
└── nip/           # NIP frames (0x20–0x2F) + Ed25519 identity
```

### Frame Encoding Tiers

| Tier | Value | Description |
|------|-------|-------------|
| Tier-1 JSON    | `0x00` | UTF-8 JSON. Development / compatibility. |
| Tier-2 MsgPack | `0x01` | MessagePack binary. ~60% smaller. **Production default.** |

### NWP HTTP Overlay Mode

`NwpClient` communicates via HTTP with `Content-Type: application/x-nps-frame`.
Sub-paths per operation:

| Operation | Path | Request Frame | Response Frame |
|-----------|------|---------------|----------------|
| Schema anchor | `POST /anchor` | AnchorFrame | 204 |
| Structured query | `POST /query` | QueryFrame | CapsFrame |
| Streaming query | `POST /stream` | QueryFrame | StreamFrame chunks |
| Action invocation | `POST /invoke` | ActionFrame | raw result or AsyncActionResponse |

## Running Tests

```bash
pytest                 # all tests + coverage report
pytest -k test_nip     # NIP tests only
```

Coverage target: ≥ 90 %.

## License

Apache 2.0 — see [LICENSE](../../LICENSE).

Copyright 2026 INNO LOTUS PTY LTD
