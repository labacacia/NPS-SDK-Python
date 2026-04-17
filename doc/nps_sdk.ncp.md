# `nps_sdk.ncp` — Class and Method Reference

> Root module: `nps_sdk.ncp`
> Spec: [NPS-1 NCP v0.4](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-1-NCP.md)

NCP is the wire-and-schema layer of NPS. This module exposes the concrete frame
dataclasses that travel on the wire (`AnchorFrame`, `DiffFrame`, `StreamFrame`,
`CapsFrame`, `ErrorFrame`) plus the schema model they carry (`FrameSchema`,
`SchemaField`, `JsonPatchOperation`).

All frame classes are **frozen dataclasses** extending `NpsFrame`
(see [`nps_sdk.core.md`](./nps_sdk.core.md#npsframe)). They all provide:

- `frame_type` — the `FrameType` enum member this frame maps to.
- `preferred_tier` — `EncodingTier.MSGPACK` for every NCP frame.
- `to_dict()` / `from_dict(data)` — round-trip through plain dicts for the codec.

---

## Table of contents

- [Schema types](#schema-types)
  - [`SchemaField`](#schemafield)
  - [`FrameSchema`](#frameschema)
- [Frames](#frames)
  - [`AnchorFrame` (0x01)](#anchorframe-0x01)
  - [`DiffFrame` (0x02)](#diffframe-0x02)
  - [`StreamFrame` (0x03)](#streamframe-0x03)
  - [`CapsFrame` (0x04)](#capsframe-0x04)
  - [`ErrorFrame` (0xFE)](#errorframe-0xfe)
- [`JsonPatchOperation`](#jsonpatchoperation)
- [End-to-end example](#end-to-end-example)

---

## Schema types

### `SchemaField`

```python
@dataclass(frozen=True)
class SchemaField:
    name:     str
    type:     str
    semantic: str | None = None
    nullable: bool = False

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SchemaField"
```

One column in a `FrameSchema`.

- `type` is a logical NPS type (`"uint64"`, `"decimal"`, `"string"`, …).
- `semantic` is an optional dotted tag from the NPS semantic registry
  (e.g. `"commerce.price.usd"`). Agents use it for cross-schema alignment.

### `FrameSchema`

```python
@dataclass(frozen=True)
class FrameSchema:
    fields: tuple[SchemaField, ...]

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrameSchema"
```

The schema payload carried inside an `AnchorFrame`. Once anchored, subsequent
`DiffFrame` / `StreamFrame` / `CapsFrame` messages can reference the schema by
`anchor_ref` — the core token-saving trick of NPS.

---

## Frames

### `AnchorFrame` (0x01)

Schema anchor — the **content-addressed** schema advertisement.

```python
@dataclass(frozen=True)
class AnchorFrame(NpsFrame):
    anchor_id: str          # "sha256:{64 hex}"
    schema:    FrameSchema
    ttl:       int = 3600   # seconds; 0 = session-only
```

- `anchor_id` MUST match `AnchorFrameCache.compute_anchor_id(schema)`.
  Mismatches poison the cache and produce `NpsAnchorPoisonError` on the
  receiver.
- `ttl = 0` means the anchor lives only for the current session.

### `DiffFrame` (0x02)

Incremental update referencing an anchor — carries only changed fields as
RFC 6902 JSON Patch operations.

```python
@dataclass(frozen=True)
class DiffFrame(NpsFrame):
    anchor_ref: str
    base_seq:   int
    patch:      tuple[JsonPatchOperation, ...]
    entity_id:  str | None = None
```

`base_seq` is the monotonically increasing sequence number for ordering patches
within a stream. Gaps MUST trigger a resync (error `NCP-STREAM-SEQ-GAP`).

### `StreamFrame` (0x03)

Streaming chunk frame.

```python
@dataclass(frozen=True)
class StreamFrame(NpsFrame):
    stream_id:   str
    seq:         int
    is_last:     bool
    data:        tuple[Any, ...]
    anchor_ref:  str | None = None
    window_size: int | None = None   # back-pressure hint
    error_code:  str | None = None   # terminal error (sets is_last = True)
```

- `is_last = True` marks the final chunk; receivers MAY release stream state
  immediately afterwards.
- `window_size` optionally carries an NCP flow-control hint the sender is
  allowed to advance (receiver may ignore).

### `CapsFrame` (0x04)

Capsule — a complete, paginated result set that references a cached schema.

```python
@dataclass(frozen=True)
class CapsFrame(NpsFrame):
    anchor_ref:      str
    count:           int
    data:            tuple[Any, ...]
    next_cursor:     str | None = None
    token_est:       int | None = None
    cached:          bool | None = None
    tokenizer_used:  str | None = None
```

`token_est` + `tokenizer_used` let the consumer debit budgets without needing
to re-tokenise. `next_cursor` carries the opaque pagination cursor for the next
page.

### `ErrorFrame` (0xFE)

Unified error frame shared across the whole suite (NPS-0 §9).

```python
@dataclass(frozen=True)
class ErrorFrame(NpsFrame):
    status:  str              # NPS status code, e.g. "NPS-404"
    error:   str              # Protocol code, e.g. "NWP-QUERY-NOT-FOUND"
    message: str | None = None
    details: Any         = None
```

Used in native transport mode. In HTTP overlay mode, NPS status codes are
surfaced via the `X-NPS-Status` header while the body still carries the
concrete `ErrorFrame`.

---

## `JsonPatchOperation`

Single RFC 6902 patch used in `DiffFrame.patch`.

```python
@dataclass(frozen=True)
class JsonPatchOperation:
    op:    str                # "add" | "remove" | "replace" | "move" | "copy" | "test"
    path:  str                # JSON Pointer
    value: Any      = None
    from_: str | None = None  # serialised as "from" on the wire (RFC 6902)

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JsonPatchOperation"
```

`from_` (trailing underscore) avoids clashing with the Python keyword `from`;
`to_dict` / `from_dict` handle the wire-format translation to `"from"`.

---

## End-to-end example

```python
from nps_sdk.core.codec   import NpsFrameCodec
from nps_sdk.core.registry import FrameRegistry
from nps_sdk.core.cache   import AnchorFrameCache
from nps_sdk.ncp import (
    AnchorFrame, CapsFrame, DiffFrame, StreamFrame,
    FrameSchema, SchemaField, JsonPatchOperation,
)

schema = FrameSchema(fields=(
    SchemaField(name="id",    type="uint64"),
    SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
))

# 1) Emit AnchorFrame with the canonical id
cache = AnchorFrameCache()
anchor = AnchorFrame(
    anchor_id=AnchorFrameCache.compute_anchor_id(schema),
    schema=schema,
    ttl=3600,
)
cache.set(anchor)

# 2) Emit a DiffFrame that references it
diff = DiffFrame(
    anchor_ref=anchor.anchor_id,
    base_seq=1,
    patch=(
        JsonPatchOperation(op="replace", path="/price", value="19.99"),
    ),
    entity_id="sku-4242",
)

# 3) Round-trip everything via the codec
codec = NpsFrameCodec(FrameRegistry.create_default())
wire  = codec.encode(diff)
back  = codec.decode(wire)
assert isinstance(back, DiffFrame)
```
