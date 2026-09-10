# 工程实践来源

本文记录 DevBrief 采用的工程原则来源。链接用于解释取舍，不表示项目已经具备对应产品的全部能力。

| 来源 | 参考主题 | 在本项目中的落点 |
| --- | --- | --- |
| [Kubernetes Documentation](https://kubernetes.io/docs/concepts/overview/working-with-objects/) | 声明式对象、状态和控制循环 | 类型化状态、checkpoint 与可恢复运行 |
| [OpenTelemetry Documentation](https://opentelemetry.io/docs/concepts/signals/traces/) | trace/span、关联 ID 和可观测性 | `trace_id`、脱敏 span、回放 |
| [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/) | Prompt Injection、工具滥用和敏感信息泄露 | 不可信上下文、Policy、审批、脱敏 |
| [GitHub REST API](https://docs.github.com/en/rest/issues/issues#create-an-issue) | Issue 创建参数和响应 | GitHub adapter、dry-run、receipt |
| [12-Factor App](https://12factor.net/config) | 配置与凭据通过环境注入 | 百炼/GitHub/task-system 环境变量 |

## 使用边界

公开来源只用于设计原则和接口语义；具体实现以仓库中的契约、测试和运行结果为准。真实 provider 实验必须记录脱敏摘要，不能提交 token、原始音频、完整转写或外部正文。
