# DevBrief Agent 开发与贡献规范

DevBrief 采用“先明确需求和契约，再小步实现，再用证据审阅”的协作方式。规范目标不是制造流程负担，而是保证语音、Agent、检索和外部写操作在演进时仍可解释、可测试、可回退。

提交 Issue、PR 和实验评论前，请先按 [GitHub Issue / PR 证据 SOP](docs/standards/github-workflow-sop.md) 选择对象类型和模板；该 SOP 是本文件中标题、验收、RED/GREEN、实验记录与回退要求的可复用入口。

完整工程约束见 [`docs/standards/`](docs/standards/README.md)。在仓库工作的编码 Agent 还必须遵守 [`AGENTS.md`](AGENTS.md)。

## 1. 工作对象与真相来源

| 对象 | 回答的问题 | 完成标志 |
| --- | --- | --- |
| Proposal Issue | 是否值得做，服务谁，做与不做什么 | 问题、成功标准和非目标明确 |
| Design Issue | 边界、契约、数据、替代方案和风险怎么处理 | 可以指导一个或多个 Coding Issue |
| Coding Issue | 当前可交付切片如何验收 | Given/When/Then 或等价验收可执行 |
| ADR | 为什么长期采用某项架构决定 | 背景、替代方案、后果和迁移策略齐全 |
| PR | 当前实现是否完成了关联验收 | 测试、实验、风险、回退和 Review 证据齐全 |
| 契约与测试 | 具体行为是什么 | 与实现同步，且能在 CI/本地复现 |

需求文档说明“为什么”和“约束”；功能文档说明“交付什么”和“如何验收”；代码、Schema、测试是已合并行为的实现真相。发生冲突时，先修正文档或契约，再继续实现。

## 2. Issue

先建立 Issue，再开始行为变化。紧急安全修复可以先开 Draft PR，但必须在合并前补齐 Issue 和风险记录。

- 标题格式：`<emoji> <type>(<scope>): <中文动词短语>`，例如 `✨ feat(approval): 建立外部写操作审批状态机`。
- 一个 Issue 只包含一个可独立验收的结果；“顺便重构”“统一优化”和“研究一下”不是合格标题。
- Proposal 要写用户问题、范围、非目标、成功标准和风险。
- Design 要写候选方案、选择依据、契约/数据影响、安全影响和迁移方式。
- Coding Issue 要写 Given/When/Then、测试边界和外部依赖。
- Bug 要写最小复现、期望/实际、受影响版本、日志脱敏方式和回归范围。

仓库提供 Proposal、Design、Feature、Bug 四种 Issue Form。安全问题不要公开提交，见 [`SECURITY.md`](SECURITY.md)。

## 3. 分支、提交与合并

从最新 `main` 建立短生命周期分支：

```text
feat/<issue>-<topic>
fix/<issue>-<topic>
refactor/<issue>-<topic>
test/<issue>-<topic>
docs/<issue>-<topic>
ci/<issue>-<topic>
chore/<issue>-<topic>
```

一个分支和 PR 只服务一个 Issue。合并后删除分支；不维护长期共享的 `develop` 或 `dev` 分支。

提交格式：

```text
<emoji> <type>(<scope>): <中文动词短语>

背景：<为什么改>
变更：<关键方案与取舍>
验证：<命令、结果和剩余风险>
Refs #<issue>
```

主题不超过 72 个字符，使用一个具体动词，不加句号。允许的类型和 emoji：

| Emoji | Type | 使用场景 |
| --- | --- | --- |
| ✨ | `feat` | 新增可观察能力 |
| 🐛 | `fix` | 修复错误、回归或错误降级 |
| 🔒 | `security` | 安全、隐私或授权修复 |
| 📝 | `docs` | 文档、示例和 ADR |
| ♻️ | `refactor` | 保持外部行为的结构调整 |
| ⚡ | `perf` | 有可复现实验的性能改进 |
| ✅ | `test` | 测试或 fixture 变更 |
| 👷 | `ci` | CI 和质量门禁 |
| 🔧 | `build` | 依赖、构建或开发工具 |
| 🧹 | `chore` | 无用户行为的维护 |
| ⏪ | `revert` | 回退已有变更 |

每笔提交应可构建、可测试、可审阅和可回退。功能与无关格式化、依赖升级、机械移动或大重构必须拆开。默认 Squash Merge；当多个提交各自具备独立审查与回退价值时，PR 可明确请求保留提交历史。

## 4. TDD、实验与 PR 评论

行为开发按 Red -> Green -> Refactor：

1. **RED**：从 Issue 验收写失败测试，确认失败原因是目标行为未实现。
2. **GREEN**：只实现令当前测试通过的最小行为，并运行相关回归。
3. **REFACTOR**：改善命名、重复和边界，保持测试为绿。
4. **FULL CHECK**：运行与变更风险相称的本地质量门禁。

不要求保留不可构建的 RED commit，但 PR 必须记录测试名、失败原因、GREEN 结果和重构范围。

PR 首次描述写结论、关联 Issue、范围/非范围、架构与兼容、验证、风险和回退。随后持续追加事实性评论，而不是在最终提交后补写“全部通过”。至少记录下列节点：

- Design 或契约决定；
- 有结果的实验、失败实验或真实环境预检；
- 验证收尾、CI 变化、审查意见处理或回退决定。

每条实验评论采用：

```text
实验：<假设、输入、环境和命令>
结果：<实际观测、数据、日志或 artifact 位置>
结论：<保留、放弃、阻塞或下一步>
```

性能、稳定性、Agent 质量或缺陷修复追加 STAR：

```text
Situation（情境）：现象、规模、影响
Task（任务）：目标和验收阈值
Action（行动）：实现、数据、工具或实验设计
Result（结果）：实际数据、测试、残余风险和不可外推范围
```

真实音频、完整串口、OAuth token、用户数据和未脱敏 trace 不进入评论。只记录可公开的摘要、命令、结果、哈希和受控本地证据路径。

## 5. Review

Review 先判断行为、边界和风险，再讨论风格：

1. 是否完成关联 Issue，是否扩大范围？
2. 新旧契约是否有兼容策略和双端测试？
3. 外部写工具是否经过权限、审批、幂等和回执控制？
4. 检索内容是否被当作不可信数据处理？
5. 测试是否覆盖拒绝、超时、重放、重复请求和失败路径？
6. 是否保留真实验证与未覆盖范围，而非将 mock 写成真实集成？
7. 是否有明确回退策略？

评论前缀统一为：`blocking:`、`question:`、`suggestion:`、`nit:`、`praise:`。作者不得只回复“已修改”；应说明位置、验证或不采纳理由。关键行为改动后需要重新 Review。

## 6. 质量门禁

代码落地后，计划采用以下最小门禁：

| 范围 | 本地检查 |
| --- | --- |
| Python | `ruff format --check`、`ruff check`、`pyright`、`pytest` |
| Web | `prettier --check`、`eslint`、`vue-tsc --noEmit`、`vitest run`、生产构建 |
| 契约 | Schema 兼容性、事件序列和工具入参/回执契约测试 |
| 集成 | fake 默认必跑；sandbox/real 按 Issue 明确触发且不得泄露凭据 |
| 文档 | 链接、Markdown、示例与实际接口同步检查 |

每次 PR 只报告实际运行的命令和结果。CI 失败不得通过删测试、放松断言或扩大忽略规则“修绿”；如果失败与当前变更无关，在 PR 中说明可复现证据、影响和后续 Issue。

## 7. 安全、隐私与 AI 辅助

- 禁止提交真实 token、Cookie、私钥、`.env`、原始音频、转写、真实 Issue 内容、运行 trace、数据库快照和构建产物。
- 所有第三方代码、生成代码和 AI 建议由提交者负责理解、许可核验、测试和维护；“模型建议如此”不是设计理由。
- 未经审批，Agent 不得创建外部任务、改写任务、创建分支、修改代码或合并 PR。
- 发现漏洞请私密报告，见 [`SECURITY.md`](SECURITY.md)。

## 8. 规范来源与取舍

本仓库吸收了 VoiceLife 的 Issue 分层、短分支、TDD 记录和真实验证留痕，BabelFLUX 的中文 Gitmoji 标题、事件契约和 STAR 实验记录，DBJavaGenix 的原子工具/规范契约；同时参考 Kubernetes 的 PR 分类和文档链接、Google 的小变更与事实优先 Review、Django 的可用提交历史、Black 的 88 列格式基线。详见 [`docs/research/engineering-practice-sources.md`](docs/research/engineering-practice-sources.md)。
