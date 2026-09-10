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
