# DevBrief 工程与代码规范

## 1. 设计目标

DevBrief 是有外部写能力的 Agent 系统。工程设计首先保护四件事：事实可追溯、外部写可控制、失败可恢复、行为可验证。代码简洁、框架选择和性能优化都不能破坏这四个目标。

## 2. 分层与依赖方向

```text
Web / CLI / WebSocket adapters
          |
Application use cases and Single-Agent Harness orchestration
          |
Domain models, policies, contracts, ports
          |
Infrastructure adapters: input/ASR, LLM, GitHub, Linear, storage, telemetry
```

- Adapter 负责协议、认证、序列化和 provider 细节，不能承载领域决策。
- Application 层的 Harness 编排会话状态、执行预算、checkpoint、检索、规划、审批和恢复，但不依赖具体 SDK 类型。
- Domain 层定义 `DecisionCandidate`、`Approval`、`ToolRequest`、`ToolReceipt`、权限策略和不变量；它不导入 FastAPI、LangGraph、GitHub SDK 或数据库 ORM。
- Port 是稳定能力接口；ASR、检索、Issue Tracker、时钟、ID 生成和存储通过 Adapter 实现。
- `packages/contracts`（建立后）是事件/命令 Schema 的单一事实源。前端与后端共享语义，不共享对方实现。
- 禁止跨层读取私有状态。需要共享逻辑时，提取纯函数、领域服务或显式 Port。

### Harness-first 约束

- Phase 1 只实现单 Agent。多 Agent、分布式调度和代码执行必须有独立 ADR 与对照 Eval，不能作为“更智能”的默认替换。
- 每个会话在开始前绑定执行预算；预算、deadline、取消、Policy 拒绝和 Schema 失败由确定性代码处理，不能交给模型自行决定。
- checkpoint 是状态机的一部分，不是日志副产物。恢复前先核对审批、幂等键和 `ToolReceipt`，避免重复外部写。
- LangGraph 可被 Application 层采用为状态图实现，但领域契约、Policy、审批和持久化记录不依赖其内部类型或序列化格式。

## 3. Contracts First

下列变化必须先改契约，再改实现：事件字段、命令参数、工具参数、审批状态、任务草稿结构、回执、持久化记录和错误代码。

固定顺序：

1. 建立或更新 Design Issue；跨模块或破坏性变更再建立 ADR。
2. 修改 `docs/standards/agent-contracts.md` 和正式 Schema。
3. 为旧行为和新行为补契约测试，声明兼容窗口或迁移。
4. 更新 producer、consumer、Adapter 与 UI。
5. 删除兼容字段前，完成 deprecation 记录和迁移验证。

新增字段默认可选并给出安全默认值。删除字段、重命名字段、改变语义或放宽写权限属于破坏性变更，必须显式版本化或给出迁移路径。

## 4. 代码书写

### 通用

- 命名描述业务意图；禁止 `data2`、`utils`、`common`、`manager` 这类无边界名称。
- 首选小型纯函数和显式数据结构；副作用集中在用例和 Adapter 边缘。
- 禁止静默吞错、宽泛 `except Exception: pass`、无界重试、无超时网络调用和以字符串拼接构造结构化数据。
- 日志使用结构化字段，记录 `session_id`、`trace_id`、`tool_call_id` 等非敏感关联信息；不得记录原始音频、全量转写、凭据和未脱敏外部内容。
- 新依赖必须在 Issue/PR 说明用途、许可、替代方案、引入的运行/安全成本和删除路径。

### Python

- 使用 Python 类型标注、Pydantic Schema 和显式异常/结果语义；外部 I/O 与领域逻辑分离。
- `ruff format` 是格式真相，行宽 88；不要手工对齐来对抗 formatter。
- 公共函数、跨边界类和复杂状态转换写简短 docstring，说明不变量、错误和副作用，而不是复述代码。
- `Any`、字典裸传和 `cast` 只允许在 Adapter 边界，必须局部化并说明原因。

### TypeScript / Vue

- 启用 `strict`；事件、命令、API 响应和状态机不得使用隐式 `any`。
- 组件只管理呈现和用户交互；会话 reducer、网络和领域映射放在 composable/store/service。
- 前端不得自行推断写权限或绕过审批；只展示后端签发的可执行计划与批准状态。
- 使用 Prettier 和 ESLint 的自动格式化结果，不手写与其冲突的风格。

### 文件与函数规模

行数是发现职责混杂的信号，不是考核目标：

| 对象 | 规则 |
| --- | --- |
| 生产源文件 | 400 行开始在 Issue/PR 评估职责；超过 500 行必须拆分或说明明确例外 |
| 单一模块/组件 | 超过 800 行不可合并，除非是生成文件、静态 fixture 或 ADR 记录的临时兼容层 |
| 函数/方法 | 超过 60 行时先检查是否混合校验、编排、I/O 与转换；允许有清晰的事务/状态机例外 |
| 嵌套 | 超过 3 层优先提取 guard clause、策略对象或纯函数 |
| PR | 以单一概念为尺度；约 100 行通常容易审阅，接近 1000 行必须拆分或在 PR 给出阅读顺序 |

不把机械移动、格式化、生成物或测试 fixture 与行为改动混在同一统计中。拆分前先补行为测试；拆分步骤和前后风险写入 PR。

## 5. Agent、工具与安全

- Agent 不拥有写权限；它只提出结构化计划。Policy 层、审批记录和写 Adapter 共同决定是否执行。
- 工具按最小权限分为只读、草稿写入、外部写入。外部写工具不能在 planner 中直接调用。
- 所有工具调用消耗会话预算并带可配置超时；MCP 是 Adapter 协议，不是绕过 Tool Registry 的权限通道。
- 所有写请求必须绑定 `approval_id`、`idempotency_key`、`trace_id`、过期时间和回执；重试只可重放同一幂等键。
- Prompt、检索文本和工具结果分区存放。检索内容仅能引用，不能改变系统策略、工具 allowlist 或审批状态。
- LLM 负责提取、解释和规划；确定性代码负责 Schema 校验、授权、时间、幂等、去重阈值、数据写入和审计。

## 6. 测试策略

| 层级 | 目的 | 要求 |
| --- | --- | --- |
| 单元测试 | 纯领域规则、解析、状态转移、去重评分 | 快速、确定、无网络 |
| 契约测试 | Schema、事件序列、工具入参/回执与兼容性 | Producer 与 consumer 两侧覆盖 |
| Adapter 测试 | HTTP/SDK 映射、认证错误、超时、重试分类 | 使用 fake 或录制且脱敏的响应 |
| 集成测试 | 会话到草稿、审批到回执的流程 | 默认 fake；外部服务有明确环境标签 |
| Harness 恢复测试 | 预算耗尽、取消、checkpoint、重放和未知结果 | 默认 fake；不得因重放触发真实外部写 |
| E2E / 真实实验 | 真实 ASR、GitHub sandbox、浏览器采集 | 不作为每次 PR 必跑；记录证据与不可外推范围 |
| Eval | 行动项抽取、字段准确率、证据覆盖、工具选择、恢复/回放 | 固定样本、版本化标注、报告模型/提示词版本、时延和成本 |

任何改变审批、外部写、状态恢复、预算或重试的 PR，至少覆盖批准、拒绝、过期、预算耗尽、取消、超时、重放和重复请求。真实模型输出不应作为唯一断言；用结构、策略和可复现 fixture 验证系统行为。

## 7. 文档、ADR 与发布

- README 只写已交付和明确规划的内容，未实现功能必须标注状态。
- 需求写问题、范围和约束；功能文档写流程与验收；两者不互相替代。
- ADR 一次只记录一个长期决定：背景、决定、替代方案、后果、迁移、验证和回退。
- 用户或集成人可感知的变化写入 `CHANGELOG.md`；内部重构不必写入。
- Release 前冻结公共契约、持久化迁移和外部写策略，给出可执行回退方案。

## 8. 规范来源

本规范结合项目已有的 VoiceLife、BabelFLUX、DBJavaGenix 实践，并参考 Kubernetes、Google Engineering Practices、Django 与 Black。具体观察、链接和本项目的取舍见 [`../research/engineering-practice-sources.md`](../research/engineering-practice-sources.md)。技术选择和引入顺序见 [`technology-stack.md`](technology-stack.md)。
