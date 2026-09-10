# Agent 项目架构审查（面试视角）

**审查日期：** 2026-09-10
**审查对象：** `main`（`ae993b4`）
**审查角色：** Agent 平台/后端高级架构师

## 结论

项目已经从“模型调用 Demo”进入可审计的单 Agent Runtime：输入、候选、证据、计划、策略、审批、外部写入、回执、checkpoint、trace 和 replay 都有类型化边界，并有 91 项离线测试。它最适合应聘 Agent Harness、Agent 后端、AI Testing/研发效能方向，不应表述为已完成生产级多租户平台或 Agentic RL 基础设施。

## 面试官会认可的证据

| 能力 | 代码证据 | 验证方式 |
| --- | --- | --- |
| 可停止的 Agent Runtime | `application/harness.py` 状态机、step/deadline/model/tool budget | `tests/test_harness.py` |
| 不可信上下文隔离 | `application/context.py` trust level、token budget、redaction | `tests/test_context.py` |
| 结构化计划与精确批准 | `domain/approval.py` plan hash、scope、expiry、一次性消费 | `tests/test_approval.py` |
| 外部副作用保护 | `application/external_write.py` policy -> approval -> checkpoint -> receipt | `tests/test_external_write.py`、`tests/test_web_flow.py` |
| 重复/未知结果治理 | `application/receipts.py` idempotency conflict、query-first recovery | `tests/test_receipts.py` |
| 可观测与回放 | `domain/replay.py`、trace/checkpoint schema、SQLite artifact | `tests/test_replay.py`、`tests/test_integrations.py` |
| Provider 可替换 | GitHub、百炼、仓库证据、任务 draft ports/adapters | `tests/test_integrations.py`、`tests/test_repository_and_task_system.py` |
| 用户闭环 | Vue 3/Vite 前端、multipart 音频、审批、TTS、运行历史 | `tests/test_web_flow.py`、`npm run build` |

## 已修正的高风险问题

1. GitHub client 增加严格 `owner/name` 解析和可配置仓库 allowlist，避免计划参数越界。
2. Web 审批拒绝字符串布尔值，避免 JSON 中的 `"false"` 被 Python 当成真值。
3. Web TTS 正确关闭 `mkstemp` 文件描述符，避免长时间服务的 FD 泄漏。
4. CLI 统一捕获 provider/输入错误并返回退出码 `2`，支持 `python -m devbrief.cli`。
5. setuptools 显式包含 Vue 静态资源，确保构建安装后 `devbrief serve` 仍可提供本地前端。
6. 仓库证据读取限定工作区、文件大小、文本编码和脱敏摘要；任务系统 draft 默认 dry-run。

## 尚存风险与面试回答

| 风险 | 当前边界 | 下一步 |
| --- | --- | --- |
| 重启后的审批执行 | SQLite 可恢复结果/回执/摘要；内存 Harness 不自动重建可执行会话 | 持久化 session/approval repository，并以 checkpoint 恢复执行上下文 |
| 真实 GitHub unknown outcome | live adapter 返回成功或错误；通用 query 需要 provider 侧幂等查询 | 使用 provider request id 或 issue search 建立安全查询协议，禁止盲重试 |
| Web 鉴权 | 适合本机单用户；无生产身份认证 | 引入 OIDC/session、CSRF、审计主体和租户隔离 |
| 任务系统 live draft | JSON endpoint adapter，默认 dry-run | 为具体系统建立 schema/version、超时、重试和回执契约 |
| Agent 效果 | 当前 deterministic fake analyzer/planner，用于 Harness 基线 | 接入真实模型前先固定评测集、指标、成本和回归阈值 |
| 并发与部署 | `ThreadingHTTPServer` 本地运行 | 迁移 ASGI/队列/分布式锁，并保持领域层不变 |

## 推荐面试讲法

先讲“不让 Agent 直接拥有副作用”：模型输出是不可信数据，必须经过 schema、policy、精确 plan hash、人工批准、写前 checkpoint 和 idempotency receipt。再用一次故障演示说明 timeout/unknown outcome 如何 query-first 恢复。最后说明为什么先做单 Agent 基线和 Eval，再决定是否引入多 Agent、Memory 或 RL；这样能把取舍、风险和可验证性讲清楚。
