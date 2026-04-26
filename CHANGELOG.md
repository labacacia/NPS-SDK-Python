English | [中文版](./CHANGELOG.cn.md)

# Changelog — Python SDK (`nps-lib`)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until NPS reaches v1.0 stable, every repository in the suite is synchronized to the same pre-release version tag.

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

[1.0.0-alpha.3]: https://github.com/LabAcacia/NPS-Dev/releases/tag/v1.0.0-alpha.3
[1.0.0-alpha.2]: https://github.com/LabAcacia/NPS-Dev/releases/tag/v1.0.0-alpha.2
[1.0.0-alpha.1]: https://github.com/LabAcacia/NPS-Dev/releases/tag/v1.0.0-alpha.1
