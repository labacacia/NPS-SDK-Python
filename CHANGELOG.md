English | [中文版](./CHANGELOG.cn.md)

# Changelog — Python SDK (`nps-lib`)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until NPS reaches v1.0 stable, every repository in the suite is synchronized to the same pre-release version tag.

---

## [1.0.0-alpha.18] — Unreleased

### Added

- Added official stateful LLM context DTOs, a thread-safe process-local store, and an ASGI Action Server coordinator with owner scoping, CAS reservations, lifecycle actions, true asynchronous execution, cancellation, and all 19 shared conformance vectors.

### Changed

- Aligned unary request correlation, LLM usage accounting, strict stateful request validation, task ownership, and cancellation-safe reservation aborts across SDK families.

## [1.0.0-alpha.17] — 2026-08-02

### Added

- Port the reference server surface into the Python SDK: NCP native transport, NWP action/complex/memory nodes and bidirectional bridges, NIP CA services and full verification, NOP orchestration, daemon observability, and telemetry.
- Implement the shared NCP 0.11, NWP 0.20, NIP 0.13, NDP 0.12, and NOP 0.9
  portable profiles and language-neutral conformance fixtures.

### Fixed

- Emit UTC reputation timestamps without relying on deprecated naive-datetime APIs.

## [1.0.0-alpha.16] — 2026-07-23

### Changed

- Suite-wide alpha.16 source synchronization and protocol compatibility update.

## [1.0.0-alpha.15] — 2026-06-28

### Changed

- Suite-wide alpha.15 sync: aligned package metadata, current README/version banners, distribution source trees, and release-prep notes with NPS-Dev.
- Carries the NCP Tier-3 BinaryVector, inbound NWP Bridge server hardening, NIP canonical trust/revoke, and NDP discovery canonical-form alignment delivered by the source-of-truth tree.

## [1.0.0-alpha.14] — 2026-06-26

### Added
- `nps_sdk.nip.NipCaClient`: typed remote NIP CA client for discovery, CRL, agent/node registration, X.509 registration, renewal, revocation, and verification.
- `nps_sdk.nwp.NwpNativeNodeServer`: native-mode NWP serving helper for dispatching QueryFrame/ActionFrame over an already established NCP stream.
- `nps_sdk.conformance`: TC-N1/TC-N2 conformance catalog, manifest builder, and validator for CI/self-certification flows.

---

## [1.0.0-alpha.11] — 2026-05-28

### Added
- NOP saga compensation: `DagNode.compensate_action` / `compensate_params_mapping`, `TaskFrame.compensation_policy`, `TaskState.COMPENSATING` / `COMPENSATED`, `CompensationPolicy` constants (NPS-5 §3.1.6, alpha.9 parity)
- NOP aggregation: `AggregateStrategy.WEIGHTED_FIRST_K` and `MERGE_ALL` (NPS-5 §3.3, alpha.11)
- NOP `DelegateFrame.target_cluster_anchor` for cross-cluster delegation (NPS-5 §3.1, alpha.11)
- NOP `AlignStreamFrame.ack_seq` / `nak_seq` for window-based acknowledgment protocol (NPS-5 §3.4.2, alpha.11)
- NDP security profiles: `SecurityProfile` constants + `InMemoryNdpRegistry.security_profile` enforcement (NPS-4 §7.2, alpha.9 parity)
- NDP ephemeral TTL cap (60 s) in `InMemoryNdpRegistry` (NPS-4 §3.1, alpha.9 parity)
- NDP `AnnounceFrame` alpha.9 fields: `node_roles`, `cluster_anchor`, `spawn_spec_ref`, `bridge_protocols`, `activation_mode`, `activation_endpoint`
- NDP `GraphFrame` redesigned to NPS-4 §5 topology snapshot format: `graph_id`, `nodes` (`NdpGraphNode`: nid/cluster_anchor/node_roles), `edges` (`NdpGraphEdge`: from_nid/to_nid/latency_ms/protocol), `ttl`, `metadata`
- NIP `IdentReputationPolicyHint` and `IdentMetadata.reputation_policy` (RFC-0005 §4.2, alpha.10 parity)
- NIP `IdentFrame.ocsp_staple` (NIP v0.9 §5.1, alpha.11)
- NWP `SubscribeFrame` dataclass and NWM `trust_anchors` field (NWP v0.13, alpha.11)

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

[1.0.0-alpha.2]: https://github.com/LabAcacia/nps/releases/tag/v1.0.0-alpha.2
[1.0.0-alpha.1]: https://github.com/LabAcacia/nps/releases/tag/v1.0.0-alpha.1
