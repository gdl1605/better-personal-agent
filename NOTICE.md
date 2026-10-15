# 许可、来源与项目归属

Better Personal Agent 由 [gdl1605](https://github.com/gdl1605) 维护，项目地址为 [gdl1605/better-personal-agent](https://github.com/gdl1605/better-personal-agent)。

## 许可范围

本仓库的项目代码、通用文档、提示词、模板及虚构示例采用 [MIT License](LICENSE)。复制或分发软件及其重要部分时，保留 LICENSE 中的版权声明和许可全文。本文记录来源与适用范围，不修改 MIT 条款。

用户导入的聊天、账单、健康记录、账号资料，以及由这些资料形成的个人 Wiki，不会因使用本软件而自动适用 MIT 或成为公开内容。用户授权 Agent 阅读、向云端提供材料，与向公众发布材料是不同的决定。

`export` 生成的是按用户选择交付的个人上下文包，其中 `tools/` 携带本项目源码、LICENSE 和本说明；`workspace/` 可能含有获准提供的个人内容，不是面向公众的源码发布包。再次从包内工具导出时继续保留这些项目文件。

## 代码与文档来源

| 内容 | 来源与当前落点 |
| --- | --- |
| 聊天、账单和表格解析 | 从维护者原个人上下文库迁入通用解析方法，见 [parsers.py](personal_agent/parsers.py)；来源映射由[数据源与复用记录](docs/sources-and-reuse.md)维护。原库个人资料不随源码分发。 |
| 来源选择、阅读回执、Wiki 维护和双向交付 | 在本仓库的 [personal_agent/](personal_agent/) 实现；通用模板位于 [templates/context/](templates/context/)，流程说明位于 [prompts/](prompts/)。 |
| 微信凭据初筛 | 从本地原型提取并整理为 [credentials.py](personal_agent/credentials.py)，源码仓库另提供独立入口 [redact_wechat.py](https://github.com/gdl1605/better-personal-agent/blob/main/scripts/redact_wechat.py)；处理范围见[微信初筛说明](docs/wechat-redaction.md)。 |
| 开源协作文件 | 结构参考同一维护者的 [Codex-teammode Workflow](https://github.com/gdl1605/codex-teammode-workflow)，沿用 MIT 许可与公开维护邮箱，并按本项目运行方式改写。 |
| 社区行为规范 | [行为准则](CODE_OF_CONDUCT.md)引用 Contributor Covenant v2.1；外部规范全文的许可由其原始发布方说明。 |

## 外部运行依赖

当前 Python 运行代码和测试使用标准库，没有随仓库捆绑第三方 Python 包。Python 解释器及其标准库遵循自身许可。

Codex、ChatGPT、dot、OpenAI 的模型、文件和调度服务，以及用户单独安装的 `wechat-use` 或其他接入工具，由各自提供方维护并适用各自条款。本仓库提供调用或交接逻辑，不分发这些服务、账号权限或工具本体。CI 引用的 GitHub Actions 也由其各自仓库分发。

后续贡献如引入第三方代码、素材或依赖，应在贡献中注明出处、许可和修改情况，并随分发保留其要求的声明；具体提交方式见[贡献指南](CONTRIBUTING.md)。

## 非官方说明

本项目是独立社区项目，不是 OpenAI 官方产品，未获得 OpenAI 的认可、赞助或背书。OpenAI、ChatGPT、Codex、dot、微信、支付宝等名称仅用于说明相关运行环境或可接入来源；相关名称与商标属于各自权利人。
