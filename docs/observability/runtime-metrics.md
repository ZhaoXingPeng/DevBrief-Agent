# Runtime Metrics 快照

`devbrief metrics --db <path> [--output <path>]` 是一个无凭据、只读的本地审阅入口。
它使用已有 `triage_runs` 记录中的结果、trace 和 checkpoint，在内存中按会话聚合，
不会初始化 SQLite、迁移 schema、访问网络或调用 Provider。

## 输出契约

输出是 `RuntimeMetricsSnapshot`，`schema_version` 固定为 `1`，字段语义如下：

| 字段 | 口径 |
| --- | --- |
| `session_count` | 运行记录数量；`state_counts` 覆盖同一分母 |
| `state_counts` / `error_counts` | 会话状态，以及 trace 或结果中出现的固定 `ErrorCode` |
| `trace_kind_counts` / `span_count` | 每种脱敏 trace kind 与全部 span 数量 |
| `tool_counts` | `tool_call` span 中的安全工具名计数，不包含参数或摘要 |
| `model_call_count` / `tool_call_count` | 对应 trace kind 的 span 计数 |
| `checkpoint_count` / `recover_count` | checkpoint 数量；从 `failed_recoverable` 恢复到安全状态的迁移次数 |
| `trace_integrity_counts` | 每个会话的 `verified`、`legacy_unsealed` 或 `invalid` 状态 |
| `trace_integrity_mismatch_counts` | 固定格式完整性 mismatch code 的出现次数 |
| `budget_totals` | 每个会话最终预算的 steps、tool calls、model tokens 和 cost 求和 |

字典按稳定顺序输出。工具名和 mismatch code 必须是受限的小写标识符；未知错误码不
会被原样复制到输出。完整性损坏仍可生成聚合快照，并在 integrity 计数中标记；JSON
损坏、契约不合法或数据库结构不兼容则整体失败。

## 脱敏与失败边界

快照不保存原始转写、fixture 正文、trace `input_summary`/`output_summary`、工具参数、
计划正文、审批人、回执 URL/ID、凭据或 SQLite 原始行。`--output` 写文件时不会同时向
stdout 输出内容；未提供 `--output` 才打印 JSON。

缺失数据库、不可读数据库、缺少 `triage_runs` 表或 artifact 校验失败，CLI 返回 `2`，
stderr 只给出稳定的 `devbrief error:` 消息，且不会隐式创建数据库。该功能是本地 fake
路径的聚合审阅，不是 OpenTelemetry、Prometheus/Grafana、生产 SLO 或告警系统。
