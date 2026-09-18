# ADR 0002：以哈希链验证本地审计轨迹完整性

**状态：** Accepted
**日期：** 2026-09-18
**关联 Issue：** #82

## 背景

DevBrief 的审批、外部写和恢复结论依赖 trace 与 checkpoint。此前 trace 能够脱敏并回放
状态、计划 hash、工具决策和回执引用，但 SQLite 内保存的 span 不含因果内容锚点。局部
修改、删除、插入或重排 span 可能保留表面上连续的状态序列，从而误导 Reviewer 或让活动
会话在重启后继续。

岗位调研中的 Agent Harness、AI Testing 与安全岗位都要求 trace、回放、异常恢复和审计
形成可验证闭环，但项目不能把本地 SQLite 锁或普通 hash 误报为生产级不可篡改日志。

## 决定

1. 每个由 Harness 追加的新 `TraceSpan` 使用 `integrity_version=1`、连续 `sequence`、
   `previous_hash` 和 `integrity_hash`。hash 输入是已脱敏 span 的 canonical JSON、sequence
   和前序 hash。
2. 每个 checkpoint 在自己的 checkpoint span 写入后保存 `trace_span_count` 与
   `trace_head_hash`，并以独立 seal 绑定状态、预算、审批、回执键和 anchor；checkpoint
   顺序必须按已锚定 span 数严格递增。
3. Replay、SQLite restart restore 和 `devbrief trace-verify` 共用同一验证器，只输出固定
   mismatch code，不回显 hash 输入、完整摘要、原始转写或秘密。
4. hash/anchor 不匹配的活动会话不得恢复或执行审批后的外部写。缺少完整性字段的历史
   trace 显示为 `legacy_unsealed`，但活动会话必须重新规划，不能静默升级或继续执行。

## 替代方案

| 方案 | 不采用原因 |
| --- | --- |
| HMAC 或 KMS 签名 | 可保护“数据库可写、密钥不可得”的威胁模型，但需要密钥注入、key ID、轮换、灾备与删除策略；本切片没有这些生产前提。 |
| SQLite trigger / 单表校验 | 不能覆盖 fake runtime、导出/导入、replay 或持久化 JSON 的跨层语义。 |
| 远程 append-only 日志 | 增加外部依赖、凭据、成本和故障模式，超过无凭据本地 Demo 的边界。 |
| 不做完整性校验 | 无法在恢复前区分可信 trace 与局部损坏的 trace。 |

## 后果

### 正面

- Reviewer 能通过 API 或 CLI 验证一条持久化 trace，而不是只信任 JSON 可被解析。
- 修改、删除、插入、重排 span，以及 checkpoint 状态、seal 或锚点失配会在 fake/E2 restart 路径中被拒绝。
- 机制与 Provider、Web 框架和 SQLite SQL 表结构解耦，测试默认无凭据。

### 代价与边界

- trace 与 checkpoint payload 增加少量 hash 元数据，历史记录需显式显示 `legacy_unsealed`。
- 普通 SHA-256 链只能检测未同步重写的内容；拥有全部 SQLite 写权限的攻击者可以重算
  span 和 anchor。它不是签名、WORM 存储或远程审计服务。
- HMAC/KMS、密钥轮换、远程不可变日志、多租户授权和生产留存策略仍需独立设计。

## 迁移与验证

新会话自动写入 v1 字段，不做破坏性 SQLite migration；字段在 JSON artifact 中可选以读取
历史记录。验证覆盖正常链、局部修改、删除、重排、anchor 伪造、SQLite restart 拒绝和
CLI 只读报告。回退只需 revert 本 ADR 对应的实现提交；旧 artifact 不删除，仍以
`legacy_unsealed` 被报告。
