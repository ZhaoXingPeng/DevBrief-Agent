# Bug Triage Eval 基线

`bug-triage-v1-baseline.json` 是针对公开、合成、已脱敏 fixture 的确定性 fake
analyzer 基线。它只保留数据集/模型/提示词版本和聚合指标，故意不包含转写正文、候选
正文、音频、trace、凭据或外部对象。

从仓库根目录运行：

```bash
devbrief eval --check docs/evals/bug-triage-v1-baseline.json
```

该命令读取 `fixtures/evals/bug-triage-v1.json`，在运行时执行 fake analyzer 并比较聚合
报告。要审阅当前输出而不写文件：

```bash
devbrief eval
```

要对候选改动建立新的可审计基线，先修改或新增版本化 label set，再在独立 Issue/PR 中运行：

```bash
devbrief eval --output docs/evals/<dataset>-baseline.json
```

报告中的 Precision、Recall、F1、字段准确率、证据覆盖和失败分类只描述这一版本的固定
fake 路径，不能外推为真实 LLM、ASR、GitHub 或生产环境的质量/时延/成本结果。真实实验
必须依照[实验记录规范](../standards/experiment-records.md)单独留痕。

## Runtime benchmark

运行时基准与候选质量 Eval 分开输出。它重复执行公开、合成、`redacted=true` 的
deterministic Bug Triage 路径，默认 30 次测量和 3 次 warmup：

```bash
devbrief benchmark --iterations 30 --warmup 3 --output benchmark.json
```

artifact 只包含 workload/fixture 版本与 digest、测量和 warmup 次数、p50/p95/min/max
wall time、状态/固定错误分类及 steps/tool calls/model tokens/cost 聚合；不包含原始转写、
逐轮 session/trace、候选正文、token、provider 响应或凭据。p50/p95 使用 nearest-rank：
对排序后的 `n` 个值取 `ceil(p * n)` 的位置，warmup 不进入指标。

可选门禁必须由调用者明确提供：

```bash
devbrief benchmark --iterations 30 --warmup 3 --max-p95-ms 1000 --output benchmark.json
```

若门禁超过，CLI 仍先写出 artifact，再返回退出码 `3`。该命令的值只说明当前机器、当前
无凭据 fake 路径的观测，不是实际 LLM/ASR/外部工具、生产容量或 SLO 声明。真实 Provider
或跨机器实验仍须按[实验记录规范](../standards/experiment-records.md)单独记录环境、版本、
样本范围和不可外推限制。
