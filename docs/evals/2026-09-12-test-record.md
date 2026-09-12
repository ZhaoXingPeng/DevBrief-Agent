# 2026-09-12 当前 main Agent 全链路测试记录

**阶段：** D2 fake / D4 real / 验证收尾  
**提交：** `7511017e6289931fe0e2b13d4273e5db9539c760`  
**环境：** Windows、Python 3.13.15、pytest 8.4.2、ruff 0.16.6、pyright 1.1.411、Node 22、Vite 6.4.3；百炼兼容 OpenAI endpoint。密钥仅从 `C:\project\百炼.txt` 注入子进程环境。

## 1. 自动化 Agent 门禁

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 全量测试 | `py -3.13 -m pytest` | **124 passed**（10.98s） |
| 质量 | `py -3.13 -m ruff format --check .`；`ruff check .`；`pyright`；`compileall`；Markdown links | 全部通过；Pyright 0 errors；链接通过 |
| 离线 Eval | `py -3.13 -m devbrief.cli eval --check docs/evals/bug-triage-v1-baseline.json` | 2 samples；candidate P/R/F1、字段准确率、证据覆盖均 1.0；失败分类全 0 |
| Web/API | `py -3.13 -m pytest tests/test_web_flow.py -k "run or approve or restart or recover or draft"` | **8 passed**：运行、审批、dry-run 回执、重启、恢复、证据和 draft |
| 集成/安全 | `py -3.13 -m pytest tests/test_integrations.py tests/test_external_write.py tests/test_replay.py` | 相关用例通过；策略、审批、幂等、unknown outcome、回放和脱敏均覆盖 |

## 2. 百炼 TTS -> ASR -> Agent

**假设：** 真实媒体输入转换为同一脱敏 TranscriptFixture 后，可以进入既有 Agent Harness；语音连通性不等同于 Agent 质量。

**命令：**

```text
devbrief speak "认证刷新故障需要跟踪修复，优先级 P0，负责人待确认。" --output <临时目录>/roundtrip.mp3
devbrief transcribe <临时目录>/roundtrip.mp3 --output <临时目录>/transcript.json
devbrief run <临时目录>/transcript.json
```

**结果：** TTS exit `0`，MP3 `257324` bytes；ASR exit `0`，生成 `redacted=true`、1 segment 的 fixture；Harness exit `0`，`state=awaiting_approval`、`plan_hash` 存在、未执行外部写。ASR 将 “P0” 识别为 “P 零”，因此优先级保持 `unknown`，符合“不从不可靠转写补事实”的安全边界。临时文件已清理。

**安全拒绝样本：** “准备提交任务，优先级高，负责人待确认。” 不包含 Bug Triage 触发证据，`run` 返回 `cannot create task draft`，不会创建计划或外部 Issue；这是 R1/R2 的预期失败关闭行为，不是放宽规则的理由。

## 3. 包与发布

从隔离工作目录执行 `py -3.13 -m build` 成功；wheel/sdist 均通过 `twine check`。临时 venv 安装 wheel 后 `devbrief --help` 成功。Web `npm ci` 与 `npm run build` 成功。远端 Release `v0.1.0` 已存在，包含 wheel、sdist、评测矩阵和本记录；Release asset SHA256 由 GitHub 提供。

## 4. 结论与限制

当前 main 满足单 Agent Harness / AI Testing MVP 的契约、工具、审批、恢复、回放、离线 Eval、Web/API、GitHub sandbox（Issue #57）和一次真实媒体入链证据。不能外推真实 LLM 质量、ASR WER、p95 时延/成本、并发稳定性或生产级能力。

仍未执行：真实 LLM 标注集、性能 runner、生产鉴权/OTel/多租户、实时流式 ASR、多 Agent 对照实验。GitHub sandbox 的既有证据为单仓库/单样本/单网络窗口，详见 Issue #57；不外推生产可靠性。`build/` 未跟踪目录会遮蔽本地 `build` 模块，打包验证使用隔离 cwd 绕过，未删除该用户文件。
