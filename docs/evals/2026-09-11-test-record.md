# 2026-09-11 测试与真实语音实验记录

**阶段：** D2 fake / D4 real / 验证收尾  
**项目：** DevBrief-Agent  
**提交：** `1f6bd3907652901f8ea86d0947d5704d632c9dd5`  
**环境：** Windows、Python 3.13、Node 22、pydantic 2.13.5、pytest 8.4.2、ruff 0.16.6、pyright 1.1.411；百炼兼容 OpenAI endpoint；密钥仅注入子进程环境。

## 1. D2 fake 与质量门禁

**假设：** 固定 fixture、契约、状态机、审批、回执、SQLite/Web 和构建链路可在无凭据环境中复现。

**命令与结果：**

| 命令 | 结果 |
| --- | --- |
| `py -3.13 -m pytest` | `123 passed in 9.85s` |
| `py -3.13 -m ruff format --check .` | `107 files already formatted` |
| `py -3.13 -m ruff check .` | `All checks passed!` |
| `py -3.13 -m pyright` | `0 errors, 0 warnings, 0 informations` |
| `py -3.13 -m compileall -q src` | exit code 0 |
| `py -3.13 tools/check_markdown_links.py` | `Markdown local links: passed` |
| `py -3.13 -m devbrief.cli eval --check docs/evals/bug-triage-v1-baseline.json` | exit code 0；2 samples，candidate P/R/F1=1.0，field accuracy=1.0，evidence coverage=1.0 |
| `npm ci; npm run build`（`web/`） | Vite 6.4.3 build 成功，静态资源复制到 `src/devbrief/integration/static/` |

**结论：** 接受。E1/E2 门禁全通过；fake 基线只证明固定数据和确定性路径，不代表真实 LLM/ASR/GitHub 质量。

## 2. D4 百炼 TTS→ASR 往返

**假设：** 兼容端点可用默认模型完成中文短句 TTS，生成音频可再次被 ASR 解析为脱敏 fixture。

**输入：** 一条不含凭据或私有业务内容的中文短句（“准备提交任务，优先级高，负责人待确认。”）；输出写入系统临时目录，实验结束后删除。

**命令：**

```text
DEVBRIEF_BAILIAN_BASE_URL=<兼容端点>
py -3.13 -m devbrief.cli speak <短句> --output <临时目录>/roundtrip.mp3
py -3.13 -m devbrief.cli transcribe <临时目录>/roundtrip.mp3 --output <临时目录>/transcript.json
```

**结果：**

- TTS exit code `0`，MP3 大小 `203564` bytes。
- ASR exit code `0`，生成 `fixture_id=audio-2810f39e66c1e1ea`。
- `segment_count=1`、`redacted=true`、`transcript_chars=19`。
- 临时音频和 JSON 已清理；密钥未打印、未写入 trace/SQLite/仓库。

**结论：** 接受为一次 E4 受控真实连通性实验。它证明端点、鉴权、默认模型和适配器协议在该时间窗口可用；不证明 WER、说话人分离、长音频、并发、p95 延迟、成本或生产稳定性。

## 3. 未执行或阻塞项

- GitHub sandbox 外部写：未执行。当前实现默认 dry-run；没有把个人仓库或长期 token 当作测试环境。
- 真实 LLM 质量集：未执行。当前 Eval 是确定性 fake analyzer，不能外推模型质量。
- 性能/成本：未执行。仓库尚无固定重复次数和 p50/p95 runner。
- 生产鉴权、OTel、限流、多租户、实时流式 ASR、多 Agent：不在本版本范围。

## 4. 脱敏检查

本记录只保留版本、命令、计数、退出码、artifact 摘要和限制。未保存密钥、原始音频、完整转写、HTTP 请求头、私有仓库正文、数据库、trace 或 GitHub 对象内容。

## 5. PR 评论正文

以下内容已按仓库实验评论格式准备，发布到对应 PR；评论中的结果与本文件一致。

```text
阶段：D2 fake / D4 real / 验证收尾
假设：固定 fixture 与 Agent Harness 门禁可复现，且百炼兼容端点可完成一次受控 TTS→ASR 往返。
环境：提交 1f6bd390；Python 3.13；pytest 8.4.2；ruff 0.16.6；pyright 1.1.411；Node 22；百炼兼容端点（密钥不记录）。
输入：公开脱敏 fixture 2 个；真实语音为 1 条不含隐私的中文短句，原始音频仅在系统临时目录存在。
命令：py -3.13 -m pytest；ruff format/check；pyright；compileall；Markdown links；devbrief eval --check；npm ci && npm run build；devbrief speak/transcribe 往返。
结果：123 passed；格式、lint、类型、compileall、链接、Eval check、Web build 全通过。Eval 2 samples，candidate P/R/F1=1.0，字段准确率=1.0，证据覆盖=1.0。百炼 TTS exit=0、203564 bytes；ASR exit=0、1 segment、redacted=true、19 字符；临时 artifact 已清理。
结论：E1/E2 门禁和一次 E4 真实语音连通性实验接受；当前提交满足单 Agent Harness/AI Testing MVP 证据要求，可发布 Release。未将 fake 指标写成真实模型质量，也未声称生产级。
限制：GitHub sandbox 外部写、真实 LLM 质量集、性能 p50/p95/成本、生产鉴权/OTel/多租户、实时流式 ASR、多 Agent 均未覆盖；下一步见 docs/evals/agent-test-matrix.md。
```
