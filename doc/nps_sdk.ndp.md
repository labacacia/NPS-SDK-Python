# `nps_sdk.ndp` — Class and Method Reference

> Root module: `nps_sdk.ndp`
> Spec: [NPS-4 NDP v0.2](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-4-NDP.md)

NDP is the discovery layer — the NPS analogue of DNS. This module provides the
three NDP frame types, a thread-safe in-memory registry with lazy TTL eviction,
and an announce signature validator backed by `NipIdentity`.

---

## Table of contents

- [Supporting dataclasses](#supporting-dataclasses)
  - [`NdpAddress`](#ndpaddress)
  - [`NdpResolveResult`](#ndpresolveresult)
  - [`NdpGraphNode`](#ndpgraphnode)
- [Frames](#frames)
  - [`AnnounceFrame` (0x30)](#announceframe-0x30)
  - [`ResolveFrame` (0x31)](#resolveframe-0x31)
  - [`GraphFrame` (0x32)](#graphframe-0x32)
- [`InMemoryNdpRegistry`](#inmemoryndpregistry)
- [Validator](#validator)
  - [`NdpAnnounceValidator`](#ndpannouncevalidator)
  - [`NdpAnnounceResult`](#ndpannounceresult)
- [End-to-end example](#end-to-end-example)

---

## Supporting dataclasses

### `NdpAddress`

```python
@dataclass(frozen=True)
class NdpAddress:
    host:     str
    port:     int
    protocol: str      # "nwp" | "nwp+tls"

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpAddress"
```

### `NdpResolveResult`

```python
@dataclass(frozen=True)
class NdpResolveResult:
    host:             str
    port:             int
    ttl:              int                  # seconds
    cert_fingerprint: str | None = None    # "sha256:{hex}"

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpResolveResult"
```

### `NdpGraphNode`

```python
@dataclass(frozen=True)
class NdpGraphNode:
    nid:          str
    addresses:    tuple[NdpAddress, ...]
    capabilities: tuple[str, ...]
    node_type:    str | None = None         # "memory" | "action" | ...

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NdpGraphNode"
```

---

## Frames

### `AnnounceFrame` (0x30)

Publishes a node's physical reachability and TTL (NPS-4 §3.1).

```python
@dataclass(frozen=True)
class AnnounceFrame(NpsFrame):
    nid:          str
    addresses:    tuple[NdpAddress, ...]
    capabilities: tuple[str, ...]
    ttl:          int                         # 0 = orderly shutdown
    timestamp:    str                         # ISO 8601 UTC
    signature:    str                         # "ed25519:{base64url}"
    node_type:    str | None = None

    def unsigned_dict(self) -> dict[str, Any]
```

Signing (NPS-4 §3.1):

1. Call `frame.unsigned_dict()` — this strips `signature`.
2. Sign with `NipIdentity.sign(dict)` using the NID's own private key (the same
   key that backs its `IdentFrame`).
3. `ttl = 0` MUST be signed and published before orderly shutdown so
   subscribers evict the entry.

### `ResolveFrame` (0x31)

Request/response envelope for resolving an `nwp://` URL.

```python
@dataclass(frozen=True)
class ResolveFrame(NpsFrame):
    target:        str                        # "nwp://api.example.com/products"
    requester_nid: str | None = None
    resolved:      NdpResolveResult | None = None   # populated on response
```

JSON is the preferred tier for resolve traffic — it's low-volume and
human-debugged.

### `GraphFrame` (0x32)

Topology sync between registries.

```python
@dataclass(frozen=True)
class GraphFrame(NpsFrame):
    seq:          int                          # strictly monotonic per publisher
    initial_sync: bool
    nodes:        tuple[NdpGraphNode, ...] | None = None   # full snapshot
    patch:        Any                         = None       # RFC 6902 JSON Patch
```

Gaps in `seq` MUST trigger a re-sync request signalled with
`NDP-GRAPH-SEQ-GAP`.

---

## `InMemoryNdpRegistry`

Thread-safe, TTL-evicting registry. Expiry is evaluated **lazily** on every
read — no background timer.

```python
class InMemoryNdpRegistry:
    def __init__(self) -> None

    def announce(self, frame: AnnounceFrame) -> None
    def resolve(self, target: str) -> NdpResolveResult | None
    def get_all(self) -> list[AnnounceFrame]
    def get_by_nid(self, nid: str) -> AnnounceFrame | None

    @staticmethod
    def nwp_target_matches_nid(nid: str, target: str) -> bool

    # For deterministic unit tests
    clock: Callable[[], float]
```

### Behaviour

- **`announce(frame)`** — `frame.ttl == 0` immediately evicts the NID;
  otherwise the entry is inserted (or refreshed) with an absolute expiry
  of `clock() + ttl`.
- **`resolve(target)`** — scans currently-live entries for the first whose NID
  "covers" `target` (see below) and returns the first advertised address in
  that announcement, wrapped in `NdpResolveResult`. Expired entries are
  purged during the scan.
- **`get_all()`** — snapshot of all currently-live announcements.
- **`get_by_nid(nid)`** — exact lookup, with on-demand purge.
- **`clock`** — replace with a monotonic stub in tests:
  `registry.clock = lambda: 1000.0`.

### `nwp_target_matches_nid(nid, target)` *(staticmethod)*

The NID ↔ target covering rule:

```
NID:    urn:nps:node:{authority}:{name}
Target: nwp://{authority}/{name}[/subpath]
```

A node NID covers a target when:

1. The target scheme is `nwp://`.
2. The NID authority equals the target authority (case-insensitive).
3. The target path starts with `/{name}` and either ends there or continues
   with `/…`.

Returns `False` for malformed inputs rather than raising.

---

## Validator

### `NdpAnnounceValidator`

Verifies an `AnnounceFrame` signature using a registered Ed25519 public key.

```python
class NdpAnnounceValidator:
    def __init__(self) -> None

    def register_public_key(self, nid: str, encoded_pub_key: str) -> None
    def remove_public_key(self, nid: str) -> None

    @property
    def known_public_keys(self) -> dict[str, str]    # read-only snapshot

    def validate(self, frame: AnnounceFrame) -> NdpAnnounceResult
```

`validate` (NPS-4 §7.1):

1. Looks up `frame.nid` in the registered keys. Missing →
   `NdpAnnounceResult.fail("NDP-ANNOUNCE-NID-MISMATCH", …)`. The expected
   workflow is: verify the announcer's `IdentFrame` first, then register its
   `pub_key` here.
2. Builds the signing payload via `frame.unsigned_dict()`.
3. Calls `NipIdentity.verify_signature(pub_key, payload, frame.signature)`.
4. Returns `NdpAnnounceResult.ok()` on success, or
   `NdpAnnounceResult.fail("NDP-ANNOUNCE-SIGNATURE-INVALID", …)` on failure.

The encoded key MUST use the `ed25519:{base64url}` form produced by
`NipIdentity.pub_key_string`.

### `NdpAnnounceResult`

```python
@dataclass(frozen=True)
class NdpAnnounceResult:
    is_valid:    bool
    error_code:  str | None = None
    message:     str | None = None

    @classmethod
    def ok(cls) -> "NdpAnnounceResult"
    @classmethod
    def fail(cls, error_code: str, message: str) -> "NdpAnnounceResult"
```

---

## End-to-end example

```python
import dataclasses, datetime
from nps_sdk.nip import NipIdentity
from nps_sdk.ndp import (
    AnnounceFrame, NdpAddress,
    InMemoryNdpRegistry, NdpAnnounceValidator,
)

# 1) Publisher generates identity
identity = NipIdentity.generate("/secure/products.key", passphrase="…")
nid      = "urn:nps:node:api.example.com:products"

# 2) Build and sign the announce
unsigned = AnnounceFrame(
    nid          = nid,
    node_type    = "memory",
    addresses    = (NdpAddress(host="10.0.0.5", port=17433, protocol="nwp+tls"),),
    capabilities = ("nwp:query", "nwp:stream"),
    ttl          = 300,
    timestamp    = datetime.datetime.now(datetime.timezone.utc).isoformat(),
    signature    = "placeholder",
)
signed = dataclasses.replace(unsigned, signature=identity.sign(unsigned.unsigned_dict()))

# 3) Validate and announce
validator = NdpAnnounceValidator()
validator.register_public_key(nid, identity.pub_key_string)
assert validator.validate(signed).is_valid

registry = InMemoryNdpRegistry()
registry.announce(signed)

# 4) Consumer resolves later
resolved = registry.resolve("nwp://api.example.com/products/items/42")
# → NdpResolveResult(host="10.0.0.5", port=17433, ttl=300)
```
