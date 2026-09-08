# DevBrief 技术栈与引入顺序

**状态：** 目标架构，尚未安装依赖或实现服务。

本文件不等于依赖清单。每项技术只有在对应 Phase 的 Design/Coding Issue 明确接口、许可、运行成本、测试和回退方案后，才能进入项目。选择依据见 [岗位能力映射](../research/AI-Agent岗位JD调研与能力映射.md) 与 [ADR 0001](../adr/0001-single-agent-harness-first.md)。

## 1. 目标栈

| 层 | 首选 | 角色 | 引入时间 | 边界 |
| --- | --- | --- | --- | --- |
| Runtime | Python 3.11+ | 领域、应用编排、Adapter、Eval | Phase 1 | Python 3.12 作为后续 CI 目标；不因本地环境缺失而伪造兼容性结论 |
| API | FastAPI | HTTP/SSE 适配层 | Phase 1 后段 | 不承载领域策略或直接外部写入 |
| Schema | Pydantic v2 + JSON Schema | 边界校验、契约生成 | Phase 1 | 领域语义仍以契约文档为真相来源 |
| 编排 | LangGraph | Application 层状态图、checkpoint、Human-in-the-loop | Phase 1 后段 | Domain 不导入框架类型；保留替换 Port |
| 本地存储 | SQLite | 无凭据 Demo、开发和确定性测试 | Phase 1 | 不作为生产并发/租户方案 |
| 服务端存储 | PostgreSQL | 会话、审批、回执、trace、checkpoint | Phase 3 | 通过 Repository Port 访问；不向领域泄露 ORM |
| 检索 | GitHub API + 本地文本/元数据索引 | 代码、ADR、Issue 证据 | Phase 2 | 先报告召回质量；不默认引入独立向量数据库 |
| 向量检索 | PostgreSQL `pgvector` | 仅在检索基线证明需要时使用 | Phase 2+ | 不与业务实体 schema 强绑定 |
| 可观测性 | OpenTelemetry + 结构化 JSON 日志 | trace、指标、错误关联 | Phase 4 | 不记录秘密、原始音频或完整私有转写 |
| Eval | pytest + 版本化 fixture + 自定义 Runner | 领域/契约/回归与报告 | Phase 1 起 | fake 默认；真实模型实验独立标记 |
| Web | Vue 3 + TypeScript | 证据审阅、审批、trace 回放 | Phase 3 | 只展示后端签发的计划与批准状态 |
| 部署 | Docker Compose + GitHub Actions | 本地一致性、CI、服务编排 | Phase 4 | 没有镜像、工作流和日志证据前不能声明已部署 |

## 2. 为什么选 Python + LangGraph，但不把它当系统本体

Python 覆盖当前岗位中反复出现的 Agent 工程、评测、RAG 和后端需求，并便于以一门语言完成 Runtime、Eval 与 Provider Adapter。LangGraph 适合承载 Application 层的显式状态图、持久化检查点和人工审批暂停点；这些能力与 DevBrief 的 Harness 目标匹配。

但框架不应定义业务边界。`DecisionCandidate`、`Plan`、`Approval`、`ToolReceipt`、错误码和事件必须保持框架无关；Adapter 也不能以 LangGraph 内部状态替代持久化契约。这样才能对 Harness 自身进行契约测试，并在需要时替换编排实现。

## 3. 明确后置或不引入的技术

| 技术/方向 | 决定 | 原因 |
| --- | --- | --- |
| 多 Agent 框架 | 后置 | 先得到单 Agent 的质量、成本和恢复基线，再以对照实验决定。 |
| Kafka、消息队列、微服务 | 后置 | 早期单进程/单仓库足以验证状态语义；分布式复杂度不能替代产品证据。 |
| 独立向量数据库 | 后置 | 先用可解释的关键词/元数据检索建立基线，确认召回瓶颈后再引入。 |
| 通用代码执行沙箱 | 不在 MVP | 当前不执行代码、命令或 PR 合并；提前引入会扩大权限和隔离风险。 |
| Agentic RL/SFT 训练平台 | 独立实验 | 是算法岗差异化能力，不是研发决策 Harness 的交付前提。 |
| 商业化观测 SaaS | 可选 | 先以 OpenTelemetry 和自有脱敏 trace 保障可移植、可审计的基础能力。 |

## 4. 实现准入检查

新增框架、SDK、数据库或云服务前，Design Issue 必须回答：

1. 它服务哪条已验收的功能和哪个稳定 Port？
2. 不引入它时的最小替代方案是什么？
3. 许可、凭据、数据流、网络访问和删除路径是什么？
4. 如何在无外网、无真实凭据环境运行 fake 测试？
5. 失败、超时、降级和移除依赖时，状态/审批/回执如何保持安全？

任何不能回答以上问题的“为了简历关键词”依赖都不应合并。
