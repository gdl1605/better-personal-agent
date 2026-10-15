# 更新记录

记录对使用者和贡献者有意义的变化。已发布版本对应同名 Git tag 和 GitHub Release，后续改动记在 Unreleased。

## [Unreleased]

- GitHub 仓库更名为 `better-personal-agent`，同步 README 标题、源码获取与开源协作链接。

## [0.1.0] - 2026-10-13

### Wiki 与资料导入修正

- 保留用户维护的根导航；同一版本续读时累计主题与理解页，修正深层 Wiki 页的原文链接。
- 微信长聊天按消息续读，普通文档与表格按全文位置续读，保留发言者、引用背景和出处。
- 合并 ChatGPT 账号导出与工具返回的会话消息，保留新页未返回的历史，并允许较旧导出补齐遗漏消息。
- Codex 默认导入兼容尚无归档目录的新安装；微信读取工具直接使用用户选择的截止日期。
- 补全各来源的接入模式、导出步骤、文件格式与日期筛选说明；已有个人库直接追加资料，无需重新初始化。

### 开源协作与分发

- 采用 MIT License，版权归属为 `Copyright (c) 2026 gdl1605`。
- 增加许可与来源说明、贡献指南、安全报告渠道、社区行为准则、支持说明和本更新记录。
- 增加 GitHub Bug、功能建议和 PR 模板，以及复用现有测试的 Python 3.12 CI。
- README 补充源码获取、运行环境、开源协作入口和非官方说明；忽略本地 `.env` 配置。
- 上下文导出包内的可运行工具携带许可证与项目说明，再次导出时继续保留；个人上下文不因随包交付而成为 MIT 开源内容。

### 首次发布能力

- 提供统一 CLI、自然语言 Bootstrap、独立个人工作区与可续接的首次引导。
- 按用户选择接入 Codex、ChatGPT、微信、消费、健康和自述资料，分别保存来源范围、处理方式与云端提供范围。
- 分离原件和阅读副本，对微信记录执行基础凭据筛除。
- 提供 Wiki 模板、增量与分段阅读、版本回执、本人纠正和依来源维护的理解页面。
- 提供 dot 日常副本、上下文打包、交付回执、共同基线合并与资料撤回。
- 将日常同步续读与独立理解校准交给实际平台调度，并保存真实任务引用。
- 修正 ChatGPT 新消息与历史分页的续接，以及云端修改返回本地的增量合并。

实现历史：[初始实现 de28272](https://github.com/gdl1605/better-personal-agent/commit/de28272)、[引导与维护完善 19da9be](https://github.com/gdl1605/better-personal-agent/commit/19da9be)、[分页与合并修正 687d252](https://github.com/gdl1605/better-personal-agent/commit/687d252)。运行边界与后续方向见[当前实现与后续建设](docs/roadmap.md)。

[Unreleased]: https://github.com/gdl1605/better-personal-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gdl1605/better-personal-agent/releases/tag/v0.1.0
