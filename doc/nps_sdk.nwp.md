# `nps_sdk.nwp` — Class and Method Reference

> Root module: `nps_sdk.nwp`
> Spec: [NPS-2 NWP v0.4](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-2-NWP.md)

NWP is the HTTP-of-AI. This module ships the two NWP frames (`QueryFrame`,
`ActionFrame`), the async `NwpClient`, and supporting dataclasses for query
ordering, vector search, and async action responses.

---

## Table of contents

- [Supporting dataclasses](#supporting-dataclasses)
  - [`QueryOrderClause`](#queryorderclause)
  - [`VectorSearchOptions`](#vectorsearchoptions)
  - [`AsyncActionResponse`](#asyncactionresponse)
- [Frames](#frames)
  - [`QueryFrame` (0x10)](#queryframe-0x10)
  - [`ActionFrame` (0x11)](#actionframe-0x11)
- [`NwpClient`](#nwpclient)
- [End-to-end example](#end-to-end-example)

---

## Supporting dataclasses

### `QueryOrderClause`

```python
@dataclass(frozen=True)
class QueryOrderClause:
    field: str
    dir:   str              # "asc" | "desc"

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueryOrderClause"
```

### `VectorSearchOptions`

```python
@dataclass(frozen=True)
class VectorSearchOptions:
    field:     str
    vector:    tuple[float, ...]
    top_k:     int   = 10
    threshold: float | None = None
    metric:    str   = "cosine"     # "cosine" | "euclidean" | "dot"

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VectorSearchOptions"
```

Attached to a `QueryFrame.vector_search` when the target Memory Node advertises
`nwp:vector`.

### `AsyncActionResponse`

```python
@dataclass(frozen=True)
class AsyncActionResponse:
    task_id:  str
    status:   str             # "pending" | "running" | ...
    poll_url: str

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AsyncActionResponse"
```

Returned by `NwpClient.invoke` when the `ActionFrame` was submitted with
`async_=True`. Poll `poll_url` to observe progress.

---

## Frames

### `QueryFrame` (0x10)

Structured read against a Memory Node.

```python
@dataclass(frozen=True)
class QueryFrame(NpsFrame):
    anchor_ref:    str | None = None
    filter:        Any       = None
    fields:        tuple[str, ...] | None = None
    limit:         int       = 20
    cursor:        str | None = None
    order:         tuple[QueryOrderClause, ...] | None = None
    vector_search: VectorSearchOptions | None = None
```

- `anchor_ref` MAY point at an already-cached `AnchorFrame`; nodes use it to
  omit the schema from the response `CapsFrame`.
- `filter` is a free-form expression (typically a dict) as described by the
  node's `.nwm.json` manifest.
- `fields` is a projection list; `None` means "all fields in the schema".
- `cursor` is the opaque pagination cursor previously returned in
  `CapsFrame.next_cursor`.

### `ActionFrame` (0x11)

Operation invocation against an Action / Complex / Gateway Node.

```python
@dataclass(frozen=True)
class ActionFrame(NpsFrame):
    action_id:       str
    params:          Any         = None
    idempotency_key: str | None = None
    timeout_ms:      int         = 5000
    async_:          bool        = False   # serialised as "async" on the wire
```

- `action_id` is the identifier declared under the Action Node's
  `.nwm.json` → `endpoints.actions[].id`.
- `idempotency_key` — if present, the node MUST deduplicate the call within
  its replay window.
- `async_=True` asks the node for a deferred response; the client receives
  an `AsyncActionResponse` instead of the synchronous result.

---

## `NwpClient`

Async HTTP client for talking to NWP nodes.

```python
class NwpClient:
    def __init__(
        self,
        base_url:     str,
        default_tier: EncodingTier          = EncodingTier.MSGPACK,
        timeout:      float                 = 10.0,
        http_client:  httpx.AsyncClient | None = None,
        registry:     FrameRegistry       | None = None,
    ) -> None

    async def __aenter__(self) -> "NwpClient"
    async def __aexit__(self, *exc_info) -> None
    async def close(self) -> None
```

Ownership: if you pass your own `http_client`, the SDK will **not** close it
on `__aexit__` / `close`; if it creates one internally, it owns and closes it.

If you omit `registry`, the client uses `FrameRegistry.create_full()`.

### Methods

#### `async send_anchor(frame: AnchorFrame) -> None`

POST the `AnchorFrame` to `{base_url}/anchor`. Nodes cache the schema and
acknowledge with HTTP 204. Raises `httpx.HTTPStatusError` on failure.

#### `async query(frame: QueryFrame) -> CapsFrame`

POST `QueryFrame` to `{base_url}/query`, decodes the response as a `CapsFrame`.

#### `async stream(frame: QueryFrame) -> AsyncIterator[StreamFrame]`

POST `QueryFrame` to `{base_url}/stream` and yield each received
`StreamFrame` as it arrives. Framing: newline-delimited wire messages
(NPS-2 §5.4). Consume with `async for`.

#### `async invoke(frame: ActionFrame) -> Any`

POST `ActionFrame` to `{base_url}/invoke`.

- `frame.async_ == False` → returns the JSON-decoded response body.
- `frame.async_ == True`  → returns `AsyncActionResponse`.

Raises `httpx.HTTPStatusError` on non-2xx responses.

---

## End-to-end example

```python
import asyncio
from nps_sdk.core import EncodingTier
from nps_sdk.ncp  import AnchorFrame, FrameSchema, SchemaField
from nps_sdk.nwp  import (
    NwpClient, QueryFrame, QueryOrderClause,
    ActionFrame, AsyncActionResponse,
    VectorSearchOptions,
)

async def main() -> None:
    async with NwpClient("https://products.example.com", default_tier=EncodingTier.MSGPACK) as nwp:
        # 1) Upload an anchor once
        schema = FrameSchema(fields=(
            SchemaField(name="id",    type="uint64"),
            SchemaField(name="price", type="decimal", semantic="commerce.price.usd"),
        ))
        anchor = AnchorFrame(anchor_id="sha256:…", schema=schema, ttl=3600)
        await nwp.send_anchor(anchor)

        # 2) Query a page
        caps = await nwp.query(QueryFrame(
            anchor_ref=anchor.anchor_id,
            filter={"price": {"$lt": "100.00"}},
            order=(QueryOrderClause(field="price", dir="asc"),),
            limit=50,
        ))
        print(caps.count, "rows, cursor:", caps.next_cursor)

        # 3) Stream the full set
        async for chunk in nwp.stream(QueryFrame(anchor_ref=anchor.anchor_id, limit=200)):
            for row in chunk.data:
                ...
            if chunk.is_last:
                break

        # 4) Fire an async action
        resp = await nwp.invoke(ActionFrame(
            action_id="restock",
            params={"sku": "sku-4242", "qty": 100},
            async_=True,
        ))
        assert isinstance(resp, AsyncActionResponse)
```
