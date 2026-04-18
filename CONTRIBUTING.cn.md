[English Version](./CONTRIBUTING.md) | 中文版

# 为 NPS 贡献代码

感谢你有兴趣为 Neural Protocol Suite 做出贡献。

## Issue 前缀

| 前缀 | 用途 |
|------|------|
| `spec:` | 规范问题与设计讨论 |
| `impl:` | 实现 Bug 与修复 |
| `sdk:`  | SDK 相关（Python / TypeScript 等） |
| `docs:` | 文档改进 |

## 工作流

1. 任何非平凡的变更先开 Issue
2. Fork 仓库并创建分支：`feature/your-feature` 或 `fix/your-fix`
3. 提交 Pull Request 并关联对应的 Issue

## 规范变更

对 `spec/` 目录下文件的变更必须先开讨论 Issue 才能接受 PR。
涉及线缆格式或帧结构的规范变更必须同时做版本号升级。

## 代码风格

- **C# / .NET**：遵循 Microsoft 标准 C# 约定，开启 Nullable
- **Python**：PEP 8，必须带类型标注
- **TypeScript**：开启严格模式

## 许可证

一旦提交贡献，你即同意贡献内容按 Apache 2.0 许可证授权。
