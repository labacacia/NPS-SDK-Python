# `nps_sdk.core` — Class and Method Reference

> Root module: `nps_sdk.core`
> Spec: [NPS-1 NCP v0.4 §3](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-1-NCP.md)

The `core` module is the wire-level foundation every other NPS module builds on:
frame header parsing, the Tier-1/Tier-2 codec pipeline, the anchor cache, the
frame type registry, and the exception hierarchy.

---

## Table of contents

- [Enums](#enums)
  - [`FrameType`](#frametype)
  - [`EncodingTier`](#encodingtier)
  - [`FrameFlags`](#frameflags)
- [`FrameHeader`](#frameheader)
- [Codec](#codec)
  - [`NpsFrame`](#npsframe)
  - [`Tier1JsonCodec`](#tier1jsoncodec)
  - [`Tier2MsgPackCodec`](#tier2msgpackcodec)
  - [`NpsFrameCodec`](#npsframecodec)
- [`AnchorFrameCache`](#anchorframecache)
- [`FrameRegistry`](#frameregistry)
- [Exceptions](#exceptions)

---

## Enums

### `FrameType`

`IntEnum` — unified frame byte namespace across the whole suite (NPS-0 §9).

| Member | Value | Layer |
|--------|-------|-------|
| `ANCHOR` | `0x01` | NCP |
| `DIFF` | `0x02` | NCP |
| `STREAM` | `0x03` | NCP |
| `CAPS` | `0x04` | NCP |
| `ALIGN` | `0x05` | NCP (**deprecated** — use `ALIGN_STREAM`) |
| `QUERY` | `0x10` | NWP |
| `ACTION` | `0x11` | NWP |
| `IDENT` | `0x20` | NIP |
| `TRUST` | `0x21` | NIP |
| `REVOKE` | `0x22` | NIP |
| `ANNOUNCE` | `0x30` | NDP |
| `RESOLVE` | `0x31` | NDP |
| `GRAPH` | `0x32` | NDP |
| `TASK` | `0x40` | NOP |
| `DELEGATE` | `0x41` | NOP |
| `SYNC` | `0x42` | NOP |
| `ALIGN_STREAM` | `0x43` | NOP |
| `ERROR` | `0xFE` | Reserved — unified error frame |

### `EncodingTier`

`IntEnum` — wire encoding tier stored in flag bits 0–1 (NPS-1 §3.2).

| Member | Value | Notes |
|--------|-------|-------|
| `JSON` | `0x00` | UTF-8 JSON, human-readable, development / compatibility |
| `MSGPACK` | `0x01` | Binary MessagePack; ~60 % smaller, production default |

`0x02` and `0x03` are reserved. Unknown tiers raise `NpsCodecError`.

### `FrameFlags`

`IntFlag` — flags byte in the fixed frame header.

| Flag | Bits | Purpose |
|------|------|---------|
| `NONE` / `TIER1_JSON` | `0x00` | Encoding tier 0 |
| `TIER2_MSGPACK` | `0x01` | Encoding tier 1 |
| `FINAL` | `0x04` | Last chunk of a `StreamFrame`; required on non-stream frames |
| `ENCRYPTED` | `0x08` | Payload encrypted (reserved for production) |
| `EXT` | `0x80` | Extended 8-byte header (4-byte payload length) |

Bits 4–6 are reserved — senders MUST write 0, receivers MUST ignore.

---

## `FrameHeader`

Parses / writes the NPS frame header (NPS-1 §3.1). 4 bytes default, 8 bytes
extended.

```python
class FrameHeader:
    frame_type:     FrameType
    flags:          FrameFlags
    payload_length: int

    def __init__(self, frame_type: FrameType, flags: FrameFlags, payload_length: int) -> None
```

Module-level constants:

| Constant | Value | Meaning |
|----------|-------|---------|
| `DEFAULT_HEADER_SIZE` | `4` | Bytes of a default-mode header |
| `EXTENDED_HEADER_SIZE` | `8` | Bytes of an extended-mode header |
| `DEFAULT_MAX_PAYLOAD` | `0xFFFF` | 65 535 B — upper bound without `EXT` |
| `EXTENDED_MAX_PAYLOAD` | `0xFFFF_FFFF` | 4 GiB − 1 — upper bound with `EXT` |

### Properties

| Property | Type | Returns |
|----------|------|---------|
| `is_extended` | `bool` | `True` if the `EXT` flag is set |
| `header_size` | `int` | `8` when `is_extended`, else `4` |
| `encoding_tier` | `EncodingTier` | Extracted from bits 0–1 |
| `is_final` | `bool` | `FINAL` flag |
| `is_encrypted` | `bool` | `ENCRYPTED` flag |

### Methods

#### `FrameHeader.parse(buf) -> FrameHeader` *(classmethod)*

Parse a header from the start of `buf: bytes | bytearray | memoryview`.
Automatically detects extended mode via the `EXT` flag. Raises
`NpsFrameError` if the buffer is too short.

#### `to_bytes() -> bytes`

Serialise to 4 or 8 bytes (depending on `is_extended`). Big-endian throughout.

Layout:

```
Default (EXT=0, 4 B):     [type (1) | flags (1) | length u16 (2)]
Extended (EXT=1, 8 B):    [type (1) | flags (1) | reserved (2) | length u32 (4)]
```

---

## Codec

### `NpsFrame`

Mixin base that every concrete frame dataclass inherits from. Contract:

```python
class NpsFrame:
    @property
    def frame_type(self) -> FrameType: ...

    @property
    def preferred_tier(self) -> EncodingTier: ...   # default: MSGPACK

    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NpsFrame": ...
```

All frame classes in `ncp`, `nwp`, `nip`, `ndp`, `nop` implement this.

### `Tier1JsonCodec`

UTF-8 JSON encoder/decoder. Uses compact separators (`","`, `":"`). Methods:

- `encode(frame: NpsFrame) -> bytes` — raises `NpsCodecError` on failure.
- `decode(frame_type: FrameType, payload: bytes, registry: FrameRegistry) -> NpsFrame`

### `Tier2MsgPackCodec`

Binary MessagePack codec. Uses `use_bin_type=True` to distinguish `bytes` vs
`str` on the wire. Same method surface as `Tier1JsonCodec`. Produces ~60 %
smaller payloads vs JSON — preferred for production traffic.

### `NpsFrameCodec`

Top-level codec dispatcher.

```python
class NpsFrameCodec:
    def __init__(self, registry: FrameRegistry, max_payload: int = 0xFFFF) -> None

    def encode(self, frame: NpsFrame, override_tier: EncodingTier | None = None) -> bytes
    def decode(self, wire: bytes) -> NpsFrame
    def peek_header(self, wire: bytes) -> FrameHeader
```

Behaviour:

- **`encode`** picks the tier from `override_tier`, `frame.preferred_tier`, or
  the class default (MsgPack). Automatically sets `FrameFlags.EXT` when the
  payload exceeds `DEFAULT_MAX_PAYLOAD`. Always sets `FrameFlags.FINAL` for
  non-stream frames.
- **`decode`** parses the header, validates the tier, looks up the concrete
  class via the registry, delegates to the matching codec.
- **`peek_header`** parses only the header — useful when routing wire data
  without deserialising the payload.

Raises `NpsFrameError` for malformed wire messages and `NpsCodecError` for
encode/decode failures.

---

## `AnchorFrameCache`

In-process cache for `AnchorFrame` instances, keyed by canonical
`sha256:{64-hex}` id.

```python
class AnchorFrameCache:
    def __init__(self) -> None

    def set(self, frame: AnchorFrame) -> str
    def get(self, anchor_id: str) -> AnchorFrame | None
    def get_required(self, anchor_id: str) -> AnchorFrame
    def invalidate(self, anchor_id: str) -> None

    @staticmethod
    def compute_anchor_id(schema: FrameSchema) -> str

    def __len__(self) -> int          # current live entries
```

Behaviour:

- **`set`** computes the canonical id (sorted-field JSON → SHA-256) and stores
  the frame. If another frame with the same id but different schema is already
  cached, raises `NpsAnchorPoisonError` (NPS-1 §3.3 anchor-poisoning guard).
- **`get_required`** raises `NpsAnchorNotFoundError` with `anchor_id` attribute
  set on the exception.
- **`__len__`** evicts expired entries (where `ttl != 0` and wall-clock expired)
  before returning.
- **`compute_anchor_id`** is deterministic: identical `FrameSchema` produces
  the same id across processes and machines.

---

## `FrameRegistry`

Maps `FrameType` bytes to concrete frame classes. Required by the codec to
decode any frame.

```python
class FrameRegistry:
    def __init__(self, mapping: dict[FrameType, type[NpsFrame]]) -> None

    def resolve(self, frame_type: FrameType) -> type[NpsFrame]
    def register(self, frame_type: FrameType, cls: type[NpsFrame]) -> None

    @classmethod
    def create_default(cls) -> "FrameRegistry"
    @classmethod
    def create_full(cls)    -> "FrameRegistry"
```

- `create_default()` — NCP-only (`Anchor`, `Diff`, `Stream`, `Caps`, `Error`).
- `create_full()` — NCP + NWP + NIP + NDP + NOP; this is what you usually want
  when wiring up a client.
- `resolve` raises `NpsFrameError` for unregistered frame types.

---

## Exceptions

All inherit from `NpsError`.

| Class | Raised when |
|-------|-------------|
| `NpsError` | Base; never raised directly |
| `NpsFrameError` | Wire header invalid, frame missing from registry |
| `NpsCodecError` | Tier-1 / Tier-2 encode or decode failure |
| `NpsAnchorNotFoundError` | `AnchorFrameCache.get_required` misses; carries `anchor_id` |
| `NpsAnchorPoisonError` | Same anchor id, different schema; carries `anchor_id` |

---

## End-to-end example

```python
from nps_sdk.core.codec   import NpsFrameCodec
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.core.cache   import AnchorFrameCache
from nps_sdk.ncp import AnchorFrame, FrameSchema, SchemaField

# 1) Codec
codec = NpsFrameCodec(FrameRegistry.create_default())

# 2) Build and round-trip an anchor
schema  = FrameSchema(fields=(
    SchemaField(name="id",    type="uint64"),
    SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
))
anchor  = AnchorFrame(anchor_id="placeholder", schema=schema, ttl=3600)
wire    = codec.encode(anchor)
decoded = codec.decode(wire)

# 3) Cache by content-addressed id
cache = AnchorFrameCache()
real_id = cache.set(AnchorFrame(
    anchor_id=AnchorFrameCache.compute_anchor_id(schema),
    schema=schema,
    ttl=3600,
))
assert cache.get_required(real_id).schema == schema
```
