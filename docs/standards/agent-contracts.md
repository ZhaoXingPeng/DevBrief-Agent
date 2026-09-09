# DevBrief Agent 契约基线

本文是 DevBrief 会话事件、运行时、决策、工具、审批与回执的语义基线。正式 JSON Schema、Pydantic 模型和 TypeScript 类型落地后必须由同一份契约生成或相互验证；不得维护多份手工漂移的工具清单。

Phase 1 的转写输入已由 [Pydantic 契约](../../src/devbrief/domain/contracts.py)
和 [JSON Schema](../../schemas/transcript-fixture.schema.json) 共同校验。fixture
只用于合成、脱敏的本地 fake 路径；真实音频、ASR 与外部写入不在此切片范围内。

转写输入进入应用层时，只形成 `ImportedTranscript`：稳定的会话/trace ID、fixture
ID 与版本、内容摘要哈希、段数量和带时间范围的证据引用。段落正文和说话人标签仅
在 Adapter 的短暂校验过程中使用，不写入 import 记录、trace 或 checkpoint。输入
不推断负责人、期限或其他候选字段；后续候选提取必须单独完成并保留不确定性。

## 1. 不变量

1. 会话事件有稳定 ID、版本、时间、会话和关联 ID。
2. 决策候选只是建议，不是外部写授权。
3. 任何外部写都必须有通过策略检查且未过期的人工审批。
4. 每个写请求有幂等键；重放同一请求不得创建第二个外部对象。
5. 每个结论可回指转写段、音频时间段（接入后）、仓库证据或人为补充；无证据时标为待澄清。
6. 任何外部内容都是数据，不是系统指令。
7. 会话在启动时绑定执行预算；模型、检索内容和工具结果都不能放宽该预算。
8. 每个可恢复状态都有可审计 checkpoint；恢复不能重放已产生回执的外部写。
9. 单 Agent 是当前唯一默认执行者；多 Agent 的引入必须经过新的契约和 ADR。

## 2. 事件信封

所有异步事件使用以下逻辑信封，字段命名采用 `snake_case`。Wire 格式是否使用 `camelCase` 由 API Schema 明确决定，不能由 Adapter 自行猜测。

```json
{
  "event_id": "evt_...",
  "schema_version": 1,
  "type": "harness.checkpointed",
  "occurred_at": "2026-09-08T12:00:00Z",
  "session_id": "ses_...",
  "trace_id": "trc_...",
  "correlation_id": "cor_...",
  "causation_id": "evt_...",
  "payload": {}
}
```

`event_id` 全局唯一；`correlation_id` 贯穿用户动作；`causation_id` 指向直接触发来源。消费者必须忽略未知的可选字段，并拒绝未知的必填版本语义。

## 3. 会话状态与执行预算

```text
created -> ingesting -> analyzing -> planning -> awaiting_approval
                                            -> executing -> completed

active -> cancelled
active -> failed_recoverable -- resume(checkpoint) --> prior_safe_state
active -> failed_terminal
awaiting_approval -> rejected | expired
```

- `active` 指 `ingesting`、`analyzing`、`planning`、`awaiting_approval` 或 `executing`。
- 状态迁移通过显式命令执行并持久化，不以 UI 文本或模型自然语言推断。
- `awaiting_approval` 不允许调用 `external_write` Adapter。
- `rejected`、`expired`、`cancelled` 和 `failed_terminal` 均为终态，除非用户创建新一轮计划。
- `failed_recoverable` 保存错误分类、可否重试和最后安全 checkpoint；恢复必须从该检查点继续，不重新执行已确认的写操作。

```text
ExecutionBudget
  max_steps
  max_tool_calls
  deadline_at
  max_model_tokens
  max_cost
  consumed_steps
  consumed_tool_calls
  consumed_model_tokens
  consumed_cost
```

预算由确定性代码在每次状态迁移、模型调用和工具调用前后检查。耗尽时写入 `budget_exhausted`、`deadline_exceeded` 或 `cost_exhausted`，并进入安全停止或可恢复失败；不得由模型自行继续。

## 4. Checkpoint、Trace 与回放

```text
Checkpoint
  id
  session_id
  state
  schema_version
  budget_summary
  plan_hash
  approval_id
  completed_tool_call_ids[]
  idempotency_keys[]
  last_event_id
  redacted_context_refs[]
  created_at

TraceSpan
  id
  trace_id
  parent_span_id
  kind: state_transition | model_call | tool_call | policy_check | approval | checkpoint
  input_summary
  output_summary
  elapsed_ms
  error_code
```

- Checkpoint 只保存恢复所需的最小、脱敏数据；不保存 token、原始音频、完整私有转写或完整第三方正文。
- `ToolReceipt` 写入成功或 `unknown_outcome` 前必须先于后续外部写形成 checkpoint。
- Replayer 只能在 fake 或明确标记的 sandbox 环境运行；它按因果顺序比较状态、计划哈希、工具决策和回执引用，不能向真实系统重放副作用。

## 5. DecisionCandidate

```text
DecisionCandidate
  id
  kind: decision | action_item | risk | clarification
  statement
  owner: resolved | unknown | ambiguous
  due_at: resolved | unknown | ambiguous
  priority: p0 | p1 | p2 | p3 | unknown
  acceptance_criteria[]
  evidence[]
  confidence
  status: proposed | needs_clarification | rejected | accepted
```

`evidence` 至少包含一种来源：`transcript_segment`、`audio_range`、`repository_file`、`adr`、`issue` 或 `human_note`。`confidence` 只能影响排序和澄清建议，不能跳过预算、策略或批准。

## 6. 上下文区段

```text
ContextSegment
  id
  source_kind: system_rule | user_input | transcript | repository_evidence | tool_result | human_note
  trust_level: trusted_policy | untrusted_data
  reference
  token_estimate
  inclusion_reason
  redaction_state
```

- 只有 `system_rule` 可以携带策略语义；`repository_evidence`、`tool_result` 和用户输入永远是 `untrusted_data`。
- Context Builder 必须在调用模型前记录裁剪和排序理由。超出预算时可生成澄清或停止，但不能隐式丢弃关键证据后执行外部写。

## 7. 工具注册表与调用

工具由一个有序 canonical registry 定义名称、描述、读写等级、执行类别、JSON Schema、处理器、超时、重试分类和审计要求。工具列举、planner 可见性、dispatch 和文档均从该注册表派生；启动时必须检测重名、无 handler、Schema 不匹配和权限漂移。

Policy Gate 只返回结构化 `ToolPolicyDecision`，不执行处理器：

```text
ToolPolicyDecision
  allowed
  tool_name
  tool_level
  error_code
  reason (脱敏摘要)
  budget_after
```

准入决定必须在工具 dispatch 前产生；拒绝决定也要进入脱敏 trace。工具预算和会话/trace 归属校验由确定性代码完成，不能由模型输出覆盖。

| 等级 | 示例 | 规则 |
| --- | --- | --- |
| `read` | `search_code`、`read_adr`、`list_issues` | 默认允许，仍受仓库/用户授权范围和执行预算限制 |
| `draft_write` | `create_issue_draft` | 只写项目本地草稿，不触发第三方副作用 |
| `external_write` | `create_issue`、`assign_issue`、`label_issue` | 需要策略通过、审批、幂等键、checkpoint 和回执 |
| `prohibited_in_mvp` | `create_branch`、`apply_patch`、`merge_pull_request`、`run_shell` | 未进入 allowlist，不注册为可调用工具 |

执行类别可为 `in_process`、`http_adapter`、`remote_mcp` 或 `manual`。`remote_mcp` 仅描述协议来源，仍必须经过本地 registry、参数校验、预算、Policy 和 Approval Gate。

任何工具调用都记录：`tool_call_id`、`tool_name`、规范化参数摘要、权限等级、执行类别、发起事件、预算变化、策略结果、审批 ID、开始/结束时间、错误分类和回执引用。记录不得包含 token、完整外部文本和原始音频。

## 8. 审批与外部写

```text
Approval
  id
  plan_hash
  scope: exact external actions
  approver_id
  status: pending | approved | rejected | expired | consumed
  expires_at
  created_at
```

审批校验返回 `ApprovalDecision`，包含 `allowed`、`approval_id`、`plan_hash`、当前审批 `status`、错误码和脱敏原因。校验除了比较 `plan_hash`，还必须比较请求的工具名与规范化参数是否等于 `Plan.tool_name` 和 `Plan.arguments`；任一字段变化都拒绝并要求重新批准。

- `plan_hash` 绑定任务标题、正文、目标仓库、标签、负责人和工具参数；任何字段变化都使旧审批失效。
- 一个 `approved` 审批只能消费一次；执行后状态变为 `consumed`。
- 外部写请求必须带 `approval_id` 与 `idempotency_key`。写 Adapter 先校验审批状态、目标范围和 hash，再发起调用。
- 必须写入 `ToolReceipt`，包含外部对象 ID/URL、provider 请求 ID（如有）、最终状态和时间。超时且未知是否成功时进入 `unknown_outcome`，先查询再决定是否重试。

## 9. 兼容与变更流程

| 变更 | 最低要求 |
| --- | --- |
| 新增可选字段 | 更新 Schema、producer/consumer 契约测试和默认值 |
| 新增事件类型 | 记录顺序语义、未知事件处理与消费者测试 |
| 删除/重命名字段 | 新旧字段兼容窗口、迁移与 deprecation 记录 |
| 修改工具参数 | JSON Schema 版本、dry-run/拒绝路径测试和 Adapter 迁移 |
| 修改预算/checkpoint 语义 | 恢复、取消、耗尽和回放兼容测试 |
| 放宽写权限 | Design Issue + ADR + threat model + 人工审批测试 |
| 引入多 Agent | 单 Agent 基线、对照 Eval、新消息/权限契约和 ADR |

## 10. 错误分类

错误至少区分：`validation_error`、`policy_denied`、`tool_not_allowed`、`approval_required`、`approval_expired`、`auth_error`、`rate_limited`、`transient_provider_error`、`conflict`、`unknown_outcome`、`budget_exhausted`、`deadline_exceeded`、`cost_exhausted`、`cancelled`、`checkpoint_unavailable` 和 `internal_error`。

只有 `transient_provider_error` 可以在明确上限内自动重试。`unknown_outcome` 不得盲目重试，必须先用幂等键或查询工具确认外部对象是否已创建。任何预算或取消错误不得通过自动增加限额绕过。
