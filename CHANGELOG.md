# Changelog

所有可感知的变化记录在此。本仓库从不含真实外部集成的 Phase 1 Harness 原型开始。

## Unreleased

- 修复内存审批仓库的可变引用泄漏，调用方不能通过修改返回对象改变后续授权状态、有效期或工具 scope。
- 增加 SHA-256 审计 trace 因果链、checkpoint anchor、SQLite/CLI 验证与损坏恢复拒绝；不将其表述为带密钥的不可篡改日志。
- 增加无凭据 deterministic Bug Triage benchmark，输出脱敏的 p50/p95、状态/错误和预算聚合，并支持可选的本机 p95 门禁。

## 0.1.1 - 2026-09-12

- 修复发行包静态资源声明，确保 favicon 和产品图标随 wheel/sdist 交付。
- 补充当前 main 的 Agent 全链路测试记录，并校正 GitHub sandbox E3 证据状态。

## 0.1.0 - 2026-09-11

- 支持将 Web Demo 挂载在 `/devbrief/` 子路径，并提供浏览器网站图标。
- 建立仓库治理和产品契约基线。
- 增加确定性、无凭据的 Bug Triage fake analyzer，输出带转写引用的候选与澄清项。
- 增加本地任务草稿生成、稳定 plan hash 和相似 Issue 人工复核标记。
- 增加停在审批边界的 Bug Triage Application 编排结果。
- 增加受 Policy、精确审批、写前 checkpoint 和幂等回执保护的 Fake Issue 写入路径。
