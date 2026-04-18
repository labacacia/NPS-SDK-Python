English | [中文版](./nps_sdk.nip.cn.md)

# `nps_sdk.nip` — Class and Method Reference

> Root module: `nps_sdk.nip`
> Spec: [NPS-3 NIP v0.2](https://github.com/labacacia/NPS-Release/blob/main/spec/NPS-3-NIP.md)

NIP is the TLS/PKI of NPS. This module exposes the identity frames
(`IdentFrame`, `RevokeFrame`), their metadata model (`IdentMetadata`), and the
`NipIdentity` helper that owns an Ed25519 keypair (encrypted at rest with
AES-256-GCM + PBKDF2-SHA256).

---

## Table of contents

- [`IdentMetadata`](#identmetadata)
- [Frames](#frames)
  - [`IdentFrame` (0x20)](#identframe-0x20)
  - [`RevokeFrame` (0x22)](#revokeframe-0x22)
- [`NipIdentity`](#nipidentity)
- [Canonical JSON + signing format](#canonical-json--signing-format)
- [End-to-end example](#end-to-end-example)

---

## `IdentMetadata`

```python
@dataclass(frozen=True)
class IdentMetadata:
    model_family: str | None = None
    tokenizer:    str | None = None
    runtime:      str | None = None

    def to_dict(self) -> dict[str, Any]
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IdentMetadata"
```

Optional metadata attached to `IdentFrame.metadata`. Excluded from the
signature calculation — it's a runtime-populated hint, not part of identity.

---

## Frames

### `IdentFrame` (0x20)

Agent identity certificate (NPS-3 §3). Sent as the opening frame on any
authenticated session.

```python
@dataclass(frozen=True)
class IdentFrame(NpsFrame):
    nid:          str                       # urn:nps:agent:{authority}:{name}
    pub_key:      str                       # "ed25519:{base64url(DER)}"
    capabilities: tuple[str, ...]
    scope:        Any
    issued_by:    str                       # issuer NID
    issued_at:    str                       # ISO 8601 UTC
    expires_at:   str
    serial:       str                       # monotonic per issuer
    signature:    str                       # "ed25519:{base64url}"
    metadata:     IdentMetadata | None = None

    def unsigned_dict(self) -> dict[str, Any]
```

`unsigned_dict()` returns the dict used as the signing input: same content as
`to_dict()` but with the `signature` and `metadata` fields stripped.

### `RevokeFrame` (0x22)

Certificate revocation (NPS-3 §9).

```python
@dataclass(frozen=True)
class RevokeFrame(NpsFrame):
    target_nid: str
    serial:     str
    reason:     str           # e.g. "key_compromise", "superseded"
    revoked_at: str           # ISO 8601 UTC
    signature:  str           # "ed25519:{base64url}" — signed by CA

    def unsigned_dict(self) -> dict[str, Any]
```

Signed by the issuing CA. Verifiers MUST refuse to use any `IdentFrame` whose
`nid` + `serial` is covered by a valid `RevokeFrame`.

> **Note:** `TrustFrame` (type 0x21) exists in the spec's frame registry but is
> not materialised as a dataclass in this SDK — trust anchor distribution is
> currently out of scope for the agent-side library.

---

## `NipIdentity`

An Ed25519 keypair manager backed by an encrypted keyfile.

File format on disk:

```
[ version (1 B) = 0x01 ]
[ salt    (16 B) ]
[ nonce   (12 B) ]
[ ciphertext (enc{private_key(32 B) || public_key(32 B)}) ]
[ auth_tag (16 B, GCM) ]
```

Key derivation: **PBKDF2-SHA256**, 600 000 iterations; cipher: **AES-256-GCM**.

```python
class NipIdentity:
    def __init__(self) -> None

    @classmethod
    def generate(cls, key_file_path: str, passphrase: str) -> "NipIdentity"

    def load(self, key_file_path: str, passphrase: str) -> None
    @property
    def is_loaded(self) -> bool

    @property
    def public_key(self) -> Ed25519PublicKey
    @property
    def pub_key_string(self) -> str

    def sign(self, payload: dict[str, Any]) -> str

    @staticmethod
    def verify_signature(
        pub_key_str: str,
        payload:     dict[str, Any],
        signature_str: str,
    ) -> bool
```

### `generate(key_file_path, passphrase) -> NipIdentity` *(classmethod)*

Produces a brand-new keypair, writes the encrypted keyfile, and returns a
loaded `NipIdentity`. If the target path already exists it is overwritten —
back it up first.

### `load(key_file_path, passphrase)`

Decrypts an existing keyfile in place. Raises:

- `FileNotFoundError` if the path doesn't exist.
- `ValueError` for a wrong passphrase or a corrupt file (GCM auth failure).

### `public_key` / `pub_key_string`

`public_key` returns the raw `cryptography` object; use
`pub_key_string` when populating `IdentFrame.pub_key` — it yields the
`ed25519:{base64url(DER)}` form that the rest of NPS uses.

### `sign(payload) -> str`

Canonicalises `payload` per the rules below, signs with the loaded private
key, and returns `"ed25519:{base64url(signature)}"`. Raises `RuntimeError`
when `is_loaded is False`.

### `verify_signature(pub_key_str, payload, signature_str) -> bool` *(staticmethod)*

Verifies a signature without needing to load a keypair. Returns `False` on
bad signatures (never raises) — this is deliberately lenient so callers can
produce human error messages.

---

## Canonical JSON + signing format

Both `sign` and `verify_signature` canonicalise the payload before touching
the Ed25519 primitive:

1. Drop any keys whose value is `None`.
2. Sort remaining keys lexicographically at every level.
3. Serialise with `separators=(",", ":")` and `ensure_ascii=False`.

The resulting UTF-8 bytes are what actually gets signed. For `IdentFrame`
and `AnnounceFrame`, use the frame's `unsigned_dict()` as the payload —
it already strips the `signature` (and `metadata`) field.

The wire format is `"ed25519:"` + base64url-without-padding of the 64-byte
Ed25519 signature.

---

## End-to-end example

```python
import asyncio, datetime
from nps_sdk.nip import IdentFrame, IdentMetadata, NipIdentity

# 1) One-off: create the keypair
identity = NipIdentity.generate("/secure/agent.key", passphrase="correct horse battery")

# 2) Build and sign an IdentFrame
nid      = "urn:nps:agent:example.com:agent-001"
unsigned = IdentFrame(
    nid          = nid,
    pub_key      = identity.pub_key_string,
    capabilities = ("nwp:query", "nop:delegate"),
    scope        = {"read": ["products:*"], "write": []},
    issued_by    = "urn:nps:ca:example.com:root",
    issued_at    = datetime.datetime.now(datetime.timezone.utc).isoformat(),
    expires_at   = (datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(days=30)).isoformat(),
    serial       = "000001",
    signature    = "placeholder",
    metadata     = IdentMetadata(model_family="sonnet-4.6"),
)
signed = dataclass_replace(unsigned, signature=identity.sign(unsigned.unsigned_dict()))
# (use dataclasses.replace in real code — shown as dataclass_replace for brevity)

# 3) Anyone with the pub_key can verify
ok = NipIdentity.verify_signature(
    identity.pub_key_string,
    signed.unsigned_dict(),
    signed.signature,
)
assert ok
```
