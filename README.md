# DevBrief Agent

DevBrief 是面向研发决策的证据驱动 Agent Harness。它将版本化、脱敏的 Bug
Triage 转写转换为可审阅、可恢复的任务计划，并提供显式预算、工具控制、审批、
checkpoint、trace 和回放。

## 当前状态

本仓库正在初始化 Phase 1，目标是无凭据的本地 Harness 原型。它不是会议纪要
应用、自动编码 Agent、生产服务或 GitHub 自动化。音频/ASR、真实 LLM、GitHub
API、MCP、数据库、Web UI、CI、容器、多 Agent、代码执行和 PR 合并均不在本次
交付范围内。

## Phase 1 切片

1. 版本化、脱敏转写 fixture 与正式契约。
2. 单 Agent 状态机、执行预算、取消与 checkpoint。
3. 脱敏 trace 与无副作用 fake 回放。
4. 具备 read/draft/external 分级和 Policy Gate 的 canonical fake Tool Registry。

## 开发

需要 Python 3.11+ 与 `src` layout。安装依赖后，质量门禁为：

```bash
pytest
ruff format --check
ruff check
pyright
```

PR 只记录实际执行过的命令及结果。交付契约和安全边界见 `docs/requirements/`、
`docs/architecture/`、`docs/standards/`、`CONTRIBUTING.md` 与 `AGENTS.md`。
