English | [中文版](./CHANGELOG.cn.md)

# Changelog — Python SDK (`nps-lib`)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until NPS reaches v1.0 stable, every repository in the suite is synchronized to the same pre-release version tag.

---

## [1.0.0-alpha.5] — 2026-05-01

### Added

- **NWP error code constants** — new `nps_sdk.nwp.error_codes` module with all 30 NWP wire error codes (auth, query, action, task, subscribe, infrastructure, manifest, topology, reserved-type). Missing from previous releases.
- **`nps_sdk.ndp.resolve_via_dns` — DNS TXT fallback resolution** — new async `InMemoryNdpRegistry.resolve_via_dns(target, dns_lookup?)` falls back to `_nps-node.{host}` TXT record lookup (NPS-4 §5) when no in-memory entry matches. `DnsTxtLookup` protocol + `SystemDnsTxtLookup` (dnspython); `parse_nps_txt_record` + `extract_host_from_target` in `nps_sdk.ndp.dns_txt`. Tests: 211 → 221.

### Changed

- **`AssuranceLevel.from_wire("")` returns `ANONYMOUS`** — `if wire is None:` changed to `if not wire:` so both `None` and `""` return `ANONYMOUS` instead of raising `ValueError` (spec §5.1.1 backward-compat fix).
- **Version bump to `1.0.0-alpha.5`** — synchronized with NPS suite alpha.5 release.

### Fixed

- **`NIP-REPUTATION-GOSSIP-FORK` / `NIP-REPUTATION-GOSSIP-SIG-INVALID`** — two new NIP reputation gossip error codes added to `nps_sdk.nip.error_codes` (RFC-0004 Phase 3, `REPUTATION_GOSSIP_FORK` / `REPUTATION_GOSSIP_SIG_INVALID`).

---

## [1.0.0-alpha.4] — 2026-04-30

### Added

- **NPS-RFC-0001 Phase 2 — NCP connection preamble (Python helper
  parity).** `nps_sdk.ncp.preamble` exposes `write_preamble()` and
  `read_preamble()` round-tripping the literal `b"NPS/1.0\n"`
  sentinel; matched by `tests/test_ncp_preamble.py`. Brings Python in
  line with the .NET / Go / TypeScript / Java preamble helpers shipped
  at alpha.4.
- **NPS-RFC-0002 Phase A/B — X.509 NID certificates + ACME `agent-01`
  (Python port).** New surface under `nps_sdk.nip`:
  - `nps_sdk.nip.x509` — X.509 NID certificate builder + verifier
    (built on `cryptography.x509`).
  - `nps_sdk.nip.acme` — ACME `agent-01` client + server reference
    (challenge issuance, key authorisation, JWS-signed wire envelope
    per NPS-RFC-0002 Phase B).
  - `nps_sdk.nip.assurance_level` — agent identity assurance levels
    (`anonymous` / `attested` / `verified`) per NPS-RFC-0003.
  - `nps_sdk.nip.cert_format` — IdentFrame `cert_format` discriminator
    (`v1` Ed25519 vs. `x509`).
  - `nps_sdk.nip.error_codes` — NIP error code namespace.
  - `nps_sdk.nip.verifier` — dual-trust IdentFrame verifier
    (v1 + X.509).
- New tests: `test_ncp_preamble.py`, `test_nip_x509.py`,
  `test_nip_acme_agent01.py`. Total: 211 tests green
  (was 162 at alpha.3).

### Changed

- Distribution version bumped to `1.0.0-alpha.4` (PyPI
  normalised: `1.0.0a4`).
- `nps_sdk.nip.frames.IdentFrame` extended with optional
  `cert_format` discriminator + `x509_chain` field alongside the
  existing v1 Ed25519 fields. v1 IdentFrames written by alpha.3
  consumers continue to verify unchanged.

### Suite-wide highlights at alpha.4

- **NPS-RFC-0002 X.509 + ACME** — full cross-SDK port wave (.NET /
  Java / Python / TypeScript / Go / Rust). Servers can now issue
  dual-trust IdentFrames (v1 Ed25519 + X.509 leaf cert chained to a
  self-signed root) and self-onboard NIDs over ACME's `agent-01`
  challenge type.
- **NPS-CR-0002 — Anchor Node topology queries** —
  `topology.snapshot` / `topology.stream` query types (.NET reference
  + L2 conformance suite). Python consumer-side helpers planned for a
  later release; no Python NWP server lives here yet.
- **`nps-registry` SQLite-backed real registry** + **`nps-ledger`
  Phase 2** (RFC 9162 Merkle + STH + inclusion proofs) shipped in the
  daemon repos.

---

## [1.0.0-alpha.3] — 2026-04-25

### Changed

- Version bump to `1.0.0-alpha.3` for suite-wide synchronization with the NPS `v1.0.0-alpha.3` release. No functional changes in the Python SDK at this milestone.
- 162 tests, 97% coverage still green.

### Suite-wide highlights at alpha.3 (per-language helpers planned for alpha.4)

- **NPS-RFC-0001 — NCP connection preamble** (Accepted). Native-mode connections now begin with the literal `b"NPS/1.0\n"` (8 bytes) so receivers can disambiguate NPS framing from random bytes / TLS / HTTP. Reference helper landed in the .NET SDK; Python helper deferred to alpha.4.
- **NPS-RFC-0003 — Agent identity assurance levels** (Accepted). NIP IdentFrame and NWM gain a tri-state `assurance_level` (`anonymous`/`attested`/`verified`). Reference types landed in .NET; Python parity deferred to alpha.4.
- **NPS-RFC-0004 — NID reputation log (CT-style)** (Accepted). Append-only Merkle log entry shape published; reference signer landed in .NET (and shipped as the `nps-ledger` daemon Phase 1). Python helpers deferred to alpha.4.
- **NPS-CR-0001 — Anchor / Bridge node split.** The legacy "Gateway Node" role is renamed to **Anchor Node** (cluster control plane); the "translate NPS↔external protocol" role is now its own **Bridge Node** type. AnnounceFrame gained `node_kind` / `cluster_anchor` / `bridge_protocols`. Source-of-truth changes are in `spec/` + the .NET reference implementation; Python NWP node-type enum stays as documented today (no Python NWP server lives here yet).
- **6 NPS resident daemons.** New `daemons/` tree in NPS-Dev defines `npsd` / `nps-runner` / `nps-gateway` / `nps-registry` / `nps-cloud-ca` / `nps-ledger`; `npsd` ships an L1-functional reference and the rest ship as Phase 1 skeletons. See [`docs/daemons/architecture.md`](https://github.com/LabAcacia/NPS-Dev/blob/v1.0.0-alpha.3/docs/daemons/architecture.md).

### Covered modules

- nps_sdk.core / ncp / nwp / nip / ndp / nop

---

## [1.0.0-alpha.2] — 2026-04-19

### Changed

- **PyPI distribution renamed from `nps-sdk` to `nps-lib`.** The `nps-sdk` name on PyPI is owned by an unrelated party (Ingenico); LabAcacia ships under `nps-lib` instead. Import module `nps_sdk` is unchanged, so existing `import nps_sdk` code works without modification — only `pip install` and `pyproject.toml` dependency declarations need updating.
- Version bump to `1.0.0-alpha.2` for suite-wide synchronization. No functional changes beyond version alignment.
- 162 tests, 97% coverage green.

### Covered modules

- nps_sdk.core / ncp / nwp / nip / ndp / nop

---

## [1.0.0-alpha.1] — 2026-04-10

First public alpha as part of the NPS suite `v1.0.0-alpha.1` release.

[1.0.0-alpha.5]: https://github.com/labacacia/NPS-sdk-py/releases/tag/v1.0.0-alpha.5
[1.0.0-alpha.4]: https://github.com/labacacia/NPS-sdk-py/releases/tag/v1.0.0-alpha.4
[1.0.0-alpha.3]: https://github.com/LabAcacia/NPS-Dev/releases/tag/v1.0.0-alpha.3
[1.0.0-alpha.2]: https://github.com/LabAcacia/NPS-Dev/releases/tag/v1.0.0-alpha.2
[1.0.0-alpha.1]: https://github.com/LabAcacia/NPS-Dev/releases/tag/v1.0.0-alpha.1
