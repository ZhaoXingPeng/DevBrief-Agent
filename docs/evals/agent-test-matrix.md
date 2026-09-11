# DevBrief Agent 完整评测矩阵

**版本：** 1.0  
**评测日期：** 2026-09-11（Asia/Hong_Kong）  
**被测提交：** `1f6bd3907652901f8ea86d0947d5704d632c9dd5`  
**范围：** 单 Agent Harness、Bug Triage 工作流、工具/审批/回执、SQLite/Web、百炼 ASR/TTS 适配器。  
**不包含：** 生产鉴权、多租户、实时流式 ASR、多 Agent、模型训练、自动代码修改和 PR 合并。

## 1. 评测方法基线

主流 Agent 评测不是单一“回答准确率”，而是把任务结果、执行轨迹、工具行为、安全、效率和可复现性分开测量。以下方法用于选择本项目的测试面；外部基准名称是方法参考，不表示 DevBrief 已参加这些基准。

| 方法/代表基准 | 主要测量 | 对 DevBrief 的采用方式 |
| --- | --- | --- |
| 离线任务成功率（GAIA、AgentBench） | 多步任务是否达成、最终答案/状态是否正确 | 固定脱敏 fixture、结构化候选 Precision/Recall/F1、字段准确率 |
| 软件工程任务（SWE-bench） | 在真实仓库上下文中完成任务并通过测试 | 只采用“证据可追溯、验收可执行”的 oracle；MVP 不修改代码 |
| 工具调用/函数调用（BFCL、ToolBench） | 工具选择、参数 Schema、调用顺序和结果使用 | canonical registry、参数校验、policy、approval、receipt 和重放断言 |
| 长程/状态型对话（τ-bench、τ²-bench） | 多轮状态、业务规则、外部副作用和恢复 | plan hash、一次性审批、幂等键、unknown-outcome query-first |
| 浏览器/环境交互（WebArena、BrowserGym） | 环境中的多步操作、状态变化和任务成功 | Web API 流程、重启恢复和音频上传；不宣称浏览器基准成绩 |
| 轨迹/过程评测（process/trajectory grading） | 每一步是否符合策略、证据和工具协议 | trace span、状态序列、预算、工具决策、checkpoint 和 replay report |
| 安全/鲁棒性（提示注入、越权、红队用例） | 不可信内容是否改变策略或造成越权副作用 | 检索内容隔离、工具 allowlist、拒绝/过期/篡改审批测试 |
| 效率与成本 | p50/p95 时延、token、工具调用数、失败率和成本 | 固定预算、工具/模型计数；真实 provider 时单独记录网络和模型版本 |
| 人工/专家评审 | 可用性、澄清质量、证据充分性和风险可解释性 | 仅对候选草稿和 PR artifact 做人工抽样；不把人工样本外推成模型指标 |
| 生产观测与回归 | 版本漂移、告警、错误分类、A/B 和回放 | 版本化 Eval artifact、失败分类和脱敏 trace；线上指标仍是后续工作 |

参考入口：[GAIA](https://huggingface.co/gaia-benchmark)、[AgentBench](https://github.com/THUDM/AgentBench)、[SWE-bench](https://www.swebench.com/)、[BFCL](https://gorilla.cs.berkeley.edu/leaderboard.html)、[ToolBench](https://github.com/OpenBMB/ToolBench)、[τ-bench](https://github.com/sierra-research/tau-bench)、[WebArena](https://webarena.dev/) 和 [BrowserGym](https://browsergym.readthedocs.io/)。

## 2. JD 能力对齐

岗位样本和来源边界见[AI Agent 岗位 JD 调研与能力映射](../research/AI-Agent岗位JD调研与能力映射.md)。当前目标岗位是 Agent Harness/平台、Agent 后端、AI Testing/质量 Agent 和研发效能 Agent；不把 SFT/RL、多 Agent 或生产规模写成已完成能力。

| JD 能力域 | 矩阵覆盖 | 当前证据 | 结论 |
| --- | --- | --- | --- |
| 规划与执行控制 | HAR、PLAN | E1/E2，123 个自动化测试 | 单 Agent 生命周期和硬预算已验证 |
| 上下文、Memory、RAG | CTX、EVID | E1 | 有界证据和不可信数据隔离已验证；无向量索引/长期记忆 |
| 工具协议与编排 | TOOL、DISP | E1/E2 | Registry、Schema、Policy、Dispatcher 契约已验证 |
| Runtime、恢复、幂等 | REC、EXT、WEB | E1/E2 | SQLite 重启、回执重放、query-first 已验证 |
| Eval、回归、失败归因 | EVAL | E1 | 固定 fake 基线已验证；真实模型质量集尚未建立 |
| Trace、回放、观测 | TRACE | E1 | 脱敏 trace/checkpoint/replay 已验证；无 OTel/线上告警 |
| 安全与权限 | SEC、APP | E1/E2 | external-write 必须 Policy + 精确审批；无生产身份认证 |
| 语音/多模态 | MEDIA | E4（受控真实） | 百炼 TTS→ASR 往返成功；样本少，不代表 ASR 质量基线 |
| 工程交付 | CI、REL | E1 | Python/Web 构建和质量门禁通过；发布流程在本次完成 |
| 多 Agent、训练/RL | N/A | E0 | 明确未实现，须有独立 Design/Eval 后再声称支持 |

## 3. 测试矩阵

证据等级：E1=单元/契约，E2=fake 集成，E3=sandbox，E4=受控真实实验。状态“通过”只代表该行的 oracle 已满足，不代表整类问题已解决。

| ID | 维度/方法 | 用例与可执行 oracle | 自动化/命令 | 证据 | 状态 | 缺口或下一步 |
| --- | --- | --- | --- | --- | --- | --- |
| CON-01 | Schema/输入 | 有效 fixture 生成 session、版本和时间序列；缺字段拒绝且不建半成品 | `tests/test_contracts_and_fixtures.py` | E1 | 通过 | 增加更多语言/说话人样本 |
| HAR-01 | 状态机 | 正常路径严格为 created→ingesting→analyzing→planning→awaiting_approval | `tests/test_harness.py`、`test_orchestration.py` | E1/E2 | 通过 | 无 |
| HAR-02 | 预算 | max steps/tools/model tokens/cost 耗尽后停止并写原因 | `tests/test_harness.py`、`test_tools.py` | E1 | 通过 | 需要统一指标导出 |
| HAR-03 | Deadline | 截止时间到达后不再调用 provider，进入可恢复失败 | `tests/test_harness.py` | E1 | 通过 | 增加时钟注入的跨时区样本 |
| HAR-04 | 取消/恢复 | 取消和 recover 只能从最后安全 checkpoint 继续 | `tests/test_harness.py`、`test_trace_replay.py` | E1/E2 | 通过 | 分布式锁未覆盖 |
| PLAN-01 | 任务计划 | 候选、证据、验收条件和稳定 plan_hash 全部存在 | `tests/test_planner.py` | E1 | 通过 | 真实 LLM 结构化输出待测 |
| PLAN-02 | 去重/不确定性 | 相似 Issue 只能标记 review_required，不得自动判重/关闭 | `tests/test_planner.py` | E1 | 通过 | 真实检索召回待测 |
| CTX-01 | Prompt injection | Issue/仓库文本作为 untrusted_data，不能改变 allowlist/预算/审批 | `tests/test_context.py`、`test_tools.py` | E1 | 通过 | 增加跨语言注入集 |
| CTX-02 | 上下文预算 | 超预算时记录裁剪理由和引用，不得静默截断后写入 | `tests/test_context.py` | E1 | 通过 | 需要线上 token 计量 |
| EVID-01 | 证据边界 | 工作区路径受限，返回 hash/`repo://` 摘要，敏感值脱敏 | `tests/test_repository_and_task_system.py`、`test_web_flow.py` | E1/E2 | 通过 | symlink/网络文件系统需补测 |
| TOOL-01 | BFCL 风格工具选择 | 只允许 canonical registry，Schema 错误不调用 handler | `tests/test_tools.py`、`test_dispatcher.py` | E1 | 通过 | 增加工具参数组合生成测试 |
| TOOL-02 | Policy | prohibited/越权/预算耗尽返回结构化拒绝且留下 trace | `tests/test_tools_policy.py`、`test_dispatcher.py` | E1 | 通过 | 生产用户授权未实现 |
| APP-01 | 审批门禁 | 无 approval 或 scope 不匹配时 provider 调用次数为 0 | `tests/test_approval.py`、`test_external_write.py` | E1/E2 | 通过 | 无 |
| APP-02 | 审批生命周期 | rejected/expired/consumed 不可再次执行 | `tests/test_approval.py`、`test_external_write.py` | E1 | 通过 | 跨进程消费锁待补 |
| APP-03 | 计划篡改 | 修改参数、仓库或 plan_hash 后旧 approval 被拒绝 | `tests/test_approval.py`、`test_external_write.py` | E1 | 通过 | 无 |
| EXT-01 | dry-run 外部写 | 批准后生成 dry-run receipt，不发真实 GitHub 请求 | `tests/test_external_write.py`、`test_web_flow.py` | E2 | 通过 | sandbox 仓库未配置 |
| EXT-02 | 幂等/重放 | 相同 key+参数返回原 receipt，provider 写入次数不增加 | `tests/test_receipts.py`、`test_external_write.py` | E1/E2 | 通过 | 并发竞态待压测 |
| EXT-03 | unknown outcome | 超时后只能 query-first；查询不到保持 unknown，不盲重试 | `tests/test_receipts.py`、`test_web_flow.py` | E1/E2 | 通过 | 真实 GitHub 超时演练待做 |
| EXT-04 | GitHub sandbox | 最小权限 token 在测试仓库创建一次 Issue 并可按 key 查询 | 需 `DEVBRIEF_GITHUB_*` | E3 | 未执行 | 需专用仓库和短期 token，不能用个人生产仓库 |
| TRACE-01 | 脱敏/隐私 | trace/checkpoint 不含 token、原始音频、完整私有正文 | `tests/test_trace.py`、`test_receipts.py` | E1 | 通过 | 需 secret scanner 纳入 CI |
| TRACE-02 | 轨迹回放 | fake replay 比较状态、plan_hash、工具决策和 receipt 引用 | `tests/test_replay.py`、`test_trace_replay.py` | E1 | 通过 | 无跨版本 replay 兼容矩阵 |
| REC-01 | SQLite 重启 | awaiting_approval、executing、completed 重启后状态/receipt 正确恢复 | `tests/test_web_flow.py` | E2 | 通过 | 多进程 SQLite 压测待做 |
| WEB-01 | API 工作流 | `/api/run`→`/api/approve`→`/api/runs` 返回完整公开摘要 | `tests/test_web_flow.py` | E2 | 通过 | 浏览器自动化 smoke 待补 |
| WEB-02 | 上传安全 | 10 MB 限制、扩展名/MIME/签名校验、临时文件清理 | `tests/test_web_flow.py`、`test_meeting_input.py` | E1/E2 | 通过 | 真实浏览器上传待测 |
| MEDIA-01 | 真实多模态 | TTS 生成音频，ASR 生成 redacted fixture，文件存在且响应可解析 | 本次受控 CLI 往返 | E4 | 通过 | 仅 1 条中文短句；无 WER/时延分布 |
| MEDIA-02 | provider 失败 | 缺 key/HTTP 错误/非法 JSON 返回 `MediaError`，不泄露响应正文 | `tests/test_integrations.py` | E1 | 通过 | 真实限流/断网待演练 |
| EVAL-01 | 离线质量 | 固定数据集输出 candidate P/R/F1、字段准确率、证据覆盖 | `devbrief eval --check ...` | E1 | 通过 | 当前仅 2 个 fake 样本 |
| EVAL-02 | 失败归因 | missing/unexpected/duplicate/field/no-evidence 分类稳定 | `tests/test_eval.py`、`test_eval_baseline.py` | E1 | 通过 | 需要人工标注扩充至业务规模 |
| PERF-01 | 效率 | 同一输入报告 p50/p95 wall time、tool calls、模型 tokens、成本 | 设计项，尚无 runner | E0 | 未执行 | 增加基准脚本和重复次数后再报告 |
| PROD-01 | 线上可靠性 | 错误率、恢复率、告警和版本漂移在部署环境可查询 | 生产观测未实现 | E0 | 未执行 | OTel/指标/鉴权独立 Issue |
| REL-01 | 包/构建 | wheel/sdist 可安装，Web 静态资源可构建，版本与 Release 一致 | `python -m build`、`npm run build` | E1 | 待收尾 | 本次 Release 完成后更新链接 |

## 4. 通过门槛

- **契约门槛：** CON/HAR/TOOL/APP/TRACE/REC 必须全通过；任何越权写、审批绕过或脱敏失败为阻断项。
- **离线质量门槛：** 当前 fake 基线 `sample_count=2`，candidate Precision/Recall/F1、字段准确率和证据覆盖均为 `1.0`；该数值只适用于 `bug-triage-fake-baseline@1.0.0`。
- **真实语音门槛：** TTS 和 ASR 均返回可解析结果，fixture `redacted=true`，并清理临时文件；不以单句结果推导 WER 或生产可用性。
- **发布门槛：** Python 质量门禁、Web 构建、Markdown 链接和 Eval check 全通过；Release artifact 可下载并用 SHA256 校验。

## 5. 当前结论与缺口

项目已经符合 Agent Harness/AI Testing 岗位对“单 Agent 运行时、受控工具、审批、恢复、Eval、trace 和工程交付”的 MVP 证据要求。它还不符合生产级 Agent 平台的完整要求：没有生产鉴权/多租户/限流、OTel 线上指标、真实 GitHub sandbox 报告、较大真实 ASR/LLM 标注集、p95 成本/时延基线、实时流式语音或多 Agent 对照实验。

下一轮应按优先级补齐：

1. 建立 50+ 条脱敏标注集和真实模型对照，报告字段错误与失败类别。
2. 为 GitHub 建立专用 sandbox，演练超时、重复提交和 query-first 恢复。
3. 增加性能 runner（至少 30 次，报告 p50/p95、token、工具调用和成本）。
4. 接入 OTel/指标与生产鉴权前，不能宣称“生产级”或“支持多租户”。
