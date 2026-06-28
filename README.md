English | [中文版](./README.cn.md)

# NPS Python SDK (`nps-lib`)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](../../LICENSE)
[![Release](https://img.shields.io/badge/release-v1.0.0--alpha.15-orange.svg)](../../CHANGELOG.md)
[![NCP](https://img.shields.io/badge/NCP-v0.9-5b8cff.svg)]()
[![NWP](https://img.shields.io/badge/NWP-v0.14-4af0b0.svg)]()
[![NIP](https://img.shields.io/badge/NIP-v0.10-7b61ff.svg)]()
[![NDP](https://img.shields.io/badge/NDP-v0.9-f0a050.svg)]()
[![NOP](https://img.shields.io/badge/NOP-v0.7-ff8c42.svg)]()

Python client library for the **Neural Protocol Suite (NPS)** — a complete internet protocol stack designed for AI agents and models.

PyPI package: `nps-lib` | Python namespace: `nps_sdk`

## Status

**v1.0.0-alpha.15 — RFC-0002 cross-SDK port (second language)**

Covers all five protocols — NCP + NWP + NIP + NDP + NOP — frame definitions, async client, Ed25519 identity management, **plus full NPS-RFC-0002 X.509 + ACME `agent-01` NID certificate primitives** (`nps_sdk.nip.x509` + `nps_sdk.nip.acme`).

Tests: 221 across the SDK + RFC-0002/0003/0004 coverage, all passing.

Alpha.15 additions: typed remote NIP CA client (`nps_sdk.nip.NipCaClient`), native-mode NWP serving helper (`nps_sdk.nwp.NwpNativeNodeServer`), and TC-N1/TC-N2 conformance manifest helpers (`nps_sdk.conformance`).

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
| `nps_sdk.nwp`  | NWP frames: QueryFrame, ActionFrame; async `NwpClient`; native serving via `NwpNativeNodeServer` |
| `nps_sdk.nip`        | NIP frames: IdentFrame (v2 dual-trust), TrustFrame, RevokeFrame; `NipIdentity` (Ed25519); `NipIdentVerifier` + `NipVerifierOptions` (RFC-0002 §8.1 dual-trust); `AssuranceLevel` (RFC-0003); remote CA `NipCaClient` |
| `nps_sdk.nip.x509`   | RFC-0002 X.509 NID certs: `NipX509Builder` / `NipX509Verifier` / `NpsX509Oids` |
| `nps_sdk.nip.acme`   | RFC-0002 ACME `agent-01`: `AcmeClient` / `AcmeServer` (in-process) / JWS helpers / messages |
| `nps_sdk.ndp`  | NDP frames: AnnounceFrame, ResolveFrame, GraphFrame; in-memory registry + validator |
| `nps_sdk.nop`  | NOP frames: TaskFrame, DelegateFrame, SyncFrame, AlignStreamFrame; async `NopClient` |
| `nps_sdk.conformance` | TC-N1/TC-N2 conformance catalog, manifest builder, and validator |

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

### Native NWP Serving

```python
from nps_sdk.nwp import NwpNativeNodeServer

server = NwpNativeNodeServer(
    query_handler=lambda query: [{"id": 42}],
    action_handler=lambda action: {"action": action.action_id},
)

# `reader`/`writer` are already past NCP preamble, TLS, and Hello negotiation.
await server.serve(reader, writer)
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

### NIP Remote CA Client

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

### Conformance Manifest

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

Apache 2.0 — see [LICENSE](https://github.com/labacacia/NPS-Dev/blob/main/LICENSE).

Copyright 2026 INNO LOTUS PTY LTD
