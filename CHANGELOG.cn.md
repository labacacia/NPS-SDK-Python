[English Version](./CHANGELOG.md) | 中文版

# 变更日志 —— Python SDK (`nps-lib`)

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

在 NPS 达到 v1.0 稳定版之前，套件内所有仓库同步使用同一个预发布版本号。

---

## [1.0.0-alpha.5] —— 2026-05-01

### 新增

- **NWP 错误码常量** —— 新增 `nps_sdk.nwp.error_codes` 模块，包含全部 30 个 NWP wire 错误码（auth、query、action、task、subscribe、infrastructure、manifest、topology、reserved-type）。此前版本均未提供。
- **`nps_sdk.ndp.resolve_via_dns` —— DNS TXT 回退解析** —— 新增异步 `InMemoryNdpRegistry.resolve_via_dns(target, dns_lookup?)`，当内存注册表无匹配时回退查询 `_nps-node.{host}` TXT 记录（NPS-4 §5）。`DnsTxtLookup` protocol + `SystemDnsTxtLookup`（dnspython）；`parse_nps_txt_record` + `extract_host_from_target` 位于 `nps_sdk.ndp.dns_txt`。测试数：211 → 221。

### 变更

- **`AssuranceLevel.from_wire("")` 返回 `ANONYMOUS`** —— `if wire is None:` 改为 `if not wire:`，使 `None` 和 `""` 均返回 `ANONYMOUS`，而非抛出 `ValueError`（spec §5.1.1 向后兼容修复）。
- **版本升至 `1.0.0-alpha.5`** —— 与 NPS 套件 alpha.5 同步。

### 修复

- **`NIP-REPUTATION-GOSSIP-FORK` / `NIP-REPUTATION-GOSSIP-SIG-INVALID`** —— 向 `nps_sdk.nip.error_codes` 新增两个 NIP 声誉 gossip 错误码（RFC-0004 Phase 3，`REPUTATION_GOSSIP_FORK` / `REPUTATION_GOSSIP_SIG_INVALID`）。

---

## [1.0.0-alpha.4] —— 2026-04-30

### 新增

- **NPS-RFC-0001 Phase 2 —— NCP 连接前导（Python helper 跟进）。**
  `nps_sdk.ncp.preamble` 暴露 `write_preamble()` / `read_preamble()`，
  往返字面量 `b"NPS/1.0\n"` 哨兵；测试在
  `tests/test_ncp_preamble.py`。让 Python SDK 与 .NET / Go /
  TypeScript / Java 在 alpha.4 的 preamble helper 持平。
- **NPS-RFC-0002 Phase A/B —— X.509 NID 证书 + ACME `agent-01`
  （Python 端口）。** 新增 `nps_sdk.nip` 子模块：
  - `nps_sdk.nip.x509` —— X.509 NID 证书 builder + verifier
    （基于 `cryptography.x509`）。
  - `nps_sdk.nip.acme` —— ACME `agent-01` 客户端 + 服务端参考实现
    （挑战签发、key authorization、按 NPS-RFC-0002 Phase B 的 JWS
    签名 wire 包络）。
  - `nps_sdk.nip.assurance_level` —— Agent 身份保证等级
    （`anonymous` / `attested` / `verified`），承接 NPS-RFC-0003。
  - `nps_sdk.nip.cert_format` —— IdentFrame 的 `cert_format`
    判别器（`v1` Ed25519 vs. `x509`）。
  - `nps_sdk.nip.error_codes` —— NIP 错误码命名空间。
  - `nps_sdk.nip.verifier` —— dual-trust IdentFrame 验证器
    （v1 + X.509）。
- 新增测试：`test_ncp_preamble.py`、`test_nip_x509.py`、
  `test_nip_acme_agent01.py`。总数：211 tests 全绿（alpha.3 时 162）。

### 变更

- 分发版本升至 `1.0.0-alpha.4`（PyPI 规范化为 `1.0.0a4`）。
- `nps_sdk.nip.frames.IdentFrame` 扩展：在原有 v1 Ed25519 字段旁新增
  可选 `cert_format` 判别器 + `x509_chain` 字段。alpha.3 写出的 v1
  IdentFrame 仍可被 alpha.4 验签。

### 套件级 alpha.4 要点

- **NPS-RFC-0002 X.509 + ACME** —— 完整跨 SDK 端口波（.NET / Java /
  Python / TypeScript / Go / Rust）。服务端可签发 dual-trust IdentFrame
  （v1 Ed25519 + X.509 leaf 链回自签 root），NID 可通过 ACME
  `agent-01` 自助上线。
- **NPS-CR-0002 —— Anchor Node topology 查询** ——
  `topology.snapshot` / `topology.stream` 查询类型（.NET 参考 + L2
  conformance）。Python 消费侧 helper 后续版本跟进；本仓暂无 Python
  NWP server 实现。
- **`nps-registry` SQLite 实仓** + **`nps-ledger` Phase 2**（RFC 9162
  Merkle + STH + inclusion proof）已在 daemon 仓库交付。

---

## [1.0.0-alpha.3] —— 2026-04-25

### Changed

- 版本升级至 `1.0.0-alpha.3`，与 NPS `v1.0.0-alpha.3` 套件同步。本次 Python SDK 无功能变更。
- 162 tests, 97% coverage 仍全绿。

### 套件级 alpha.3 要点（各语言 helper 在 alpha.4 跟进）

- **NPS-RFC-0001 —— NCP 连接前导**（Accepted）。原生模式连接现以字面量 `b"NPS/1.0\n"`（8 字节）开头，便于接收侧把 NPS 帧与随机字节 / TLS / HTTP 区分开。.NET SDK 已落地参考实现；Python helper 在 alpha.4 跟进。
- **NPS-RFC-0003 —— Agent 身份保证等级**（Accepted）。NIP IdentFrame 与 NWM 新增三态 `assurance_level`（`anonymous`/`attested`/`verified`）。.NET 参考类型已落地；Python 同步在 alpha.4。
- **NPS-RFC-0004 —— NID 声誉日志（CT 风格）**（Accepted）。append-only Merkle 日志条目结构发布；.NET 参考签名器已落地（并以 `nps-ledger` daemon Phase 1 形态发布）。Python helper 在 alpha.4 跟进。
- **NPS-CR-0001 —— Anchor / Bridge 节点拆分。** 旧的 "Gateway Node" 角色更名为 **Anchor Node**（集群控制面）；"NPS↔外部协议翻译" 单独成为 **Bridge Node** 类型。AnnounceFrame 新增 `node_kind` / `cluster_anchor` / `bridge_protocols`。源代码层面变更落在 `spec/` + .NET 参考实现；Python NWP 节点类型枚举本仓库尚无 NWP 服务端，按文档保留现状。
- **6 个 NPS 常驻 daemon。** NPS-Dev 新建 `daemons/` 目录，定义 `npsd` / `nps-runner` / `nps-gateway` / `nps-registry` / `nps-cloud-ca` / `nps-ledger`；其中 `npsd` 提供 L1 功能性参考实现，其余为 Phase 1 骨架。详见 [`docs/daemons/architecture.cn.md`](https://github.com/LabAcacia/NPS-Dev/blob/v1.0.0-alpha.3/docs/daemons/architecture.cn.md)。

### 涵盖模块

- nps_sdk.core / ncp / nwp / nip / ndp / nop

---

## [1.0.0-alpha.2] —— 2026-04-19

### Changed

- **PyPI 分发名从 `nps-sdk` 改为 `nps-lib`。** PyPI 上的 `nps-sdk` 名字被无关第三方（Ingenico）占用，LabAcacia 改用 `nps-lib` 发布。导入模块 `nps_sdk` 不变，`import nps_sdk` 代码无需修改——仅需更新 `pip install` 和 `pyproject.toml` 依赖声明。
- 版本升级至 `1.0.0-alpha.2`，与套件同步。除版本对齐外无功能变更。
- 162 tests, 97% coverage 全绿。

### 涵盖模块

- nps_sdk.core / ncp / nwp / nip / ndp / nop

---

## [1.0.0-alpha.1] —— 2026-04-10

作为 NPS 套件 `v1.0.0-alpha.1` 的一部分首次公开 alpha。

[1.0.0-alpha.5]: https://github.com/labacacia/NPS-sdk-py/releases/tag/v1.0.0-alpha.5
[1.0.0-alpha.4]: https://gitee.com/labacacia/NPS-sdk-py/releases/tag/v1.0.0-alpha.4
[1.0.0-alpha.3]: https://github.com/LabAcacia/NPS-Dev/releases/tag/v1.0.0-alpha.3
[1.0.0-alpha.2]: https://github.com/LabAcacia/NPS-Dev/releases/tag/v1.0.0-alpha.2
[1.0.0-alpha.1]: https://github.com/LabAcacia/NPS-Dev/releases/tag/v1.0.0-alpha.1
