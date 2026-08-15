[English Version](./CHANGELOG.md) | 中文版

# 变更日志 —— Python SDK (`nps-lib`)

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

在 NPS 达到 v1.0 稳定版之前，套件内所有仓库同步使用同一个预发布版本号。

---

## [1.0.0-alpha.18] —— 2026-08-15

### 新增

- 新增官方有状态 LLM context DTO、线程安全进程内 store 与 ASGI Action Server coordinator，覆盖 owner 隔离、CAS reservation、生命周期 action、真异步执行、取消和 19 个共享一致性向量。

### 变更

- 跨 SDK 统一 unary request correlation、LLM usage 记账、严格有状态请求校验、任务所有权与取消时 reservation 安全 abort。

## [1.0.0-alpha.17] —— 2026-08-02

### 新增

- 将参考服务端能力移植到 Python SDK：NCP 原生传输、NWP action/complex/memory node 与双向 Bridge、NIP CA 服务及完整验证、NOP 编排、daemon 可观测性和 telemetry。
- 实现共享的 NCP 0.11、NWP 0.20、NIP 0.13、NDP 0.12 与 NOP 0.9
  可移植 Profile，并执行语言无关一致性 fixture。

### 修复

- 生成 UTC reputation 时间戳时不再依赖已弃用的 naive-datetime API。

## [1.0.0-alpha.16] —— 2026-07-23

### 变更

- 完成套件级 alpha.16 源码同步与协议兼容性更新。

## [1.0.0-alpha.15] —— 2026-06-28

### 变更

- 套件级 alpha.15 同步：对齐包元数据、当前 README / 版本 banner、分发源树以及 release-prep 说明到 NPS-Dev。
- 承载源事实树中的 NCP Tier-3 BinaryVector、入站 NWP Bridge server 加固、NIP canonical trust/revoke，以及 NDP discovery canonical-form 对齐。

## [1.0.0-alpha.14] —— 2026-06-26

### Added

- `nps_sdk.nip.NipCaClient`：远程 NIP CA 的类型化客户端，覆盖 discovery、CRL、agent/node 注册、X.509 注册、续签、撤销和校验。
- `nps_sdk.nwp.NwpNativeNodeServer`：native-mode NWP 服务端 helper，用于在已建立的 NCP stream 上分发 QueryFrame/ActionFrame。
- `nps_sdk.conformance`：TC-N1/TC-N2 一致性用例目录、manifest 构造器和校验器，用于 CI/自认证流程。

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

[1.0.0-alpha.2]: https://github.com/LabAcacia/nps/releases/tag/v1.0.0-alpha.2
[1.0.0-alpha.1]: https://github.com/LabAcacia/nps/releases/tag/v1.0.0-alpha.1
