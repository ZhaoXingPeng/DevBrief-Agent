# DevBrief Agent

<p align="center">
  <img src="docs/assets/devbrief-mark.svg" width="88" alt="DevBrief 标记">
</p>

<p align="center"><strong>把研发会议中的口语决策，转化为可验证、可审批、可恢复的研发任务。</strong></p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#能力闭环">能力闭环</a> ·
  <a href="#架构">架构</a> ·
  <a href="CONTRIBUTING.md">参与开发</a>
</p>

<p align="center">
  <img src="docs/assets/devbrief-cover.png" alt="DevBrief 从研发决策到证据驱动任务的项目主图" width="96%">
</p>

DevBrief 是一个面向研发决策的证据驱动 Agent Harness。它接收版本化、脱敏的 Bug
Triage fixture，经过分析、证据上下文、任务计划、策略和人工审批，生成可审计、可
回放的本地执行结果。运行时以预算、checkpoint、trace、幂等回执和 query-first
恢复为硬边界。

## 当前状态

Phase 1 本地 Harness 已完成并进入 `main`：82 条 Python 3.13 测试通过，包含从输入
fixture 到审批前编排，以及受控 Fake Issue external-write 闭环。实现不需要凭据，默认
不会访问网络，也不会执行 shell、修改代码或合并 PR。

真实 GitHub/API、真实 LLM、ASR、数据库、Web UI、CI 部署和生产外部写入仍是后续
独立的 Design/Coding Issue，不应将当前 fake provider 当作真实集成。

## 能力闭环

```text
版本化脱敏 fixture
  -> 输入校验与元数据记录
  -> 单 Agent Harness（状态、预算、取消、checkpoint）
  -> 决策候选与可信上下文
  -> 证据绑定的任务计划与稳定 plan hash
  -> canonical Tool Registry + Policy Gate
  -> 精确、可过期的一次性人工审批
  -> Fake Issue external-write（写前意图 checkpoint）
  -> ToolReceipt、幂等重放、unknown outcome query-first 恢复
```

## 交付内容

| 模块 | 交付能力 |
| --- | --- |
| Harness | 状态机、步骤/工具/模型预算、取消、可恢复失败和安全 checkpoint |
| 输入与分析 | 版本化 fixture 校验、确定性 Bug Triage 候选和脱敏证据引用 |
| Context / Planner | 可信区段隔离、上下文预算、任务草稿、相似项人工复核和稳定 plan hash |
| Tools | canonical registry、read/draft/external 分级、参数校验、Policy 和 dispatch |
| Approval | 精确工具/参数/plan hash、过期检查和一次性消费 |
| Receipts | 幂等键冲突检测、成功回执、未知结果保留和 query-first 恢复 |
| Trace / Replay | 脱敏事件、状态与工具审计、回放和漂移报告 |

## 架构

<p align="center">
  <img src="docs/assets/devbrief-architecture.png" alt="DevBrief Agent 的输入、Harness、证据、策略、审批、回执与审计架构图" width="96%">
</p>

运行时的关键不变量是：不可信的转写、仓库证据和工具结果不能改变策略；external-write
必须经过 Policy、匹配的未过期审批、plan hash、幂等键和写前 checkpoint；provider 返回
未知结果时只能先查询，不能盲目重试。

更完整的生命周期、事件字段和恢复语义见 [Harness Runtime](docs/architecture/harness-runtime.md)
与 [Agent 契约](docs/standards/agent-contracts.md)。

## 快速开始

需要 Python 3.11+。在仓库根目录执行：

```bash
python -m pip install -e ".[dev]"
pytest
ruff format --check .
ruff check .
pyright
```

测试只使用固定 fixture 和 fake provider，不需要 API key。若尚未安装 editable package，
可用 `PYTHONPATH=src pytest` 运行测试。

## 文档

- [需求与验收](docs/requirements/需求规格说明.md)
- [系统上下文](docs/architecture/system-context.md)
- [Harness Runtime](docs/architecture/harness-runtime.md)
- [Agent 契约](docs/standards/agent-contracts.md)
- [工程规范](docs/standards/engineering.md)
- [实验记录规范](docs/standards/experiment-records.md)
- [ADR 记录](docs/adr/0001-single-agent-harness-first.md)
- [贡献与 PR/Issue 规范](CONTRIBUTING.md)
- [安全报告](SECURITY.md)

## 路线图

- [x] Phase 1：无凭据 Harness、fixture、分析、计划、策略、审批、回执、回放和 fake external-write。
- [ ] Phase 2：真实仓库证据适配器和可配置的任务系统 draft API。
- [ ] Phase 3：在独立威胁模型和审批设计后接入真实 provider。
- [ ] Phase 4：评测报告、CI、Demo 和可观测性收尾。
- [ ] Phase 5：音频/ASR 与实时体验实验。

## 参与开发

行为变化先建立一个可验收的 Issue，再使用短生命周期分支和对应 PR。PR 必须记录实际
运行的命令、结果、风险和回退方式；安全问题请按 [SECURITY.md](SECURITY.md) 私密报告。
