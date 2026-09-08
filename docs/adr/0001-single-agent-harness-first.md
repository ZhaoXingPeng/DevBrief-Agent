# ADR 0001：先交付可恢复单 Agent Harness

**状态：** Accepted
**日期：** 2026-09-08
**关联 Issue：** 尚未创建；后续实现必须从 Design Issue 开始

## 背景

DevBrief 的初始定位强调会议音频和 ASR，但岗位调研显示，Agent 平台、Harness、AI Testing 和研发效能岗位更重视可证明的运行时能力：状态控制、工具权限、上下文边界、执行预算、中断恢复、评测和回放。

若先实现实时语音、多个模型 Provider 或多 Agent 协作，会扩大输入与基础设施复杂度，却无法优先证明这些关键能力。当前场景的任务链也可以由一个带显式状态图的 Agent 完成；在尚无基线指标时拆分多个 Agent 没有充分依据。

## 决定

1. MVP 的核心是**可恢复的单 Agent Harness**，而不是 ASR 或多 Agent 编排。
2. Phase 1 使用版本化、脱敏或合成的转写 fixture 作为输入；音频上传和 ASR 通过 Input/ASR Port 在 Phase 5 再接入。
3. Harness 必须在首次实现中具备显式状态迁移、执行预算、工具注册与分级、checkpoint、取消/恢复、trace 和回放语义。
4. Domain 层保持独立。LangGraph 可作为 Application 层状态编排实现，但不得成为事件、策略、审批或持久化契约的类型依赖。
5. 多 Agent 只在单 Agent 基线显示任务完成质量、延迟、成本或可维护性存在可量化瓶颈后，以独立 Design Issue 和对照 Eval 引入。
6. DevBrief 不在当前 MVP 执行任意 shell、代码修改或 PR 合并，因此不引入通用代码沙箱；未来若开放此类工具，必须建立新的 ADR、威胁模型和隔离设计。

## 替代方案

| 方案 | 不采用原因 |
| --- | --- |
| 语音优先 | ASR、媒体采集和时延问题会掩盖 Harness、Eval 和安全闭环的验证。 |
| 多 Agent 优先 | 增加消息协议、状态一致性、成本和调试复杂度，当前没有对照数据证明收益。 |
| 直接将 LangGraph 状态作为领域模型 | 将框架细节泄露到契约和策略，降低测试与替换能力。 |
| 先做 Coding Agent 和沙箱 | 代码执行权限和隔离风险超出“研发任务决策”MVP 边界。 |

## 后果

### 正面

- 本地无凭据 Demo 可先验证任务闭环，测试不依赖模型、音频或第三方 API。
- 项目直接覆盖 Agent Harness、质量 Agent 和 Agent 后端岗位的核心工程信号。
- 单 Agent 的 trace、失败恢复、工具权限和 Eval 更容易审阅与定位。

### 代价

- 首个可运行版本不具备实时语音体验，也不声称多 Agent 协同。
- 必须先设计并验证状态/检查点/回放等基础能力，视觉成果出现更晚。
- 算法岗中的 SFT、RL、Agentic RL 不由该主线自动覆盖，需要独立实验项目。

## 迁移与验证

后续实现按 [Harness Runtime](../architecture/harness-runtime.md) 的 Phase 1 到 Phase 5 顺序执行。每个实现 Issue 至少验证：未批准写入被拒绝、预算耗尽停止、取消可审计、恢复不重复外部写、回放不泄露秘密。

## 回退

本决定可在有完整单 Agent 基线和多 Agent 对照实验后被新 ADR 替代。回退时不得删除原有单 Agent 路径或改变外部写审批边界；新路径须与既有契约兼容或提供迁移方案。
