# Personal Agent Harness

更新时间：2026-10-12。当前版本：0.1。Python 3.9 及以上，使用标准库，无需安装 Python 依赖。统一命令、可选来源接入、微信凭据处理、Wiki、消化回执、dot 交接、导出与返回合并已经实现。

这个项目帮助用户逐步配置自己的个人 Agent：选择愿意提供的资料，完成必要的导出或连接，把材料转成有出处的个人理解，再让日常 Agent 持续使用和校准这些理解。首个入口采用 dot；个人上下文和维护流程独立保存，后续可以适配其他 Agent。

建设起点是已运行的个人上下文库：迁入 Codex、ChatGPT、微信与账单解析，复用 Wiki 导航、增量阅读、语义消化与反馈回写的方法。dot 带用户完成配置，本地命令承接操作。文件导入可跨平台使用；微信实时读取首先适配已验证的 macOS wechat-use 工具。

## 开始使用

在本目录运行交互引导：

```bash
python3 personal-agent --workspace ~/PersonalContext setup
```

用户只选择目标、资料、处理方式和提供范围。也可以直接把下面的话交给已经连接电脑的 dot：

> 请读取这个 Harness 项目的 README.md、AGENTS.md 和 docs/onboarding.md，带我逐步配置个人 Agent。先了解我的目标、工作区和愿意提供的来源，用 init 和 sources 保存选择；需要我导出或登录时说明实际步骤。然后 collect，按 digest 生成的说明实际阅读资料、更新 Wiki 并提交回执；用 dot 命令生成我的日常入口。未选的来源不收取，时间范围和归属依据实际记录。

完成选择后：

```bash
python3 personal-agent --workspace ~/PersonalContext collect
python3 personal-agent --workspace ~/PersonalContext digest
python3 personal-agent --workspace ~/PersonalContext dot
python3 personal-agent --workspace ~/PersonalContext status
```

digest 生成实际材料路径、版本和回执说明，由 dot 或当前 Agent 读取后提交。脚本不把导入或生成任务当成语义阅读完成。已配置外部 Agent 命令时，可以使用 `digest --run-agent` 或 `maintain --run-agent`。

## 按工作进入

| 要解决的问题 | 文档 |
| --- | --- |
| 核心机制、数据流、模块关系与开发接口 | [架构](docs/architecture.md) |
| 用户怎样一步步配置并逐渐增加来源 | [配置引导](docs/onboarding.md) |
| 所有命令、回执格式和外部运行接入协议 | [命令与协议](docs/commands.md) |
| 哪些脚本可复用、哪些要改、哪些尚需建设 | [来源与代码复用](docs/sources-and-reuse.md) |
| 密钥密码筛除、可供 Agent 阅读的副本和双向同步 | [脱敏与同步](docs/privacy-and-sync.md) |
| 先对微信记录做一遍基础凭据筛除 | [脚本使用](docs/wechat-redaction.md) · [实际代码](scripts/redact_wechat.py) |
| dot 的接入方法、上传边界与定时维护 | [dot 接入](docs/dot-integration.md) |
| 开发顺序与每阶段应形成的实际结果 | [实施顺序](docs/roadmap.md) |
| 工作区配置 | [配置示例](config/example.json) |
| 个人 Wiki 与读取规则 | [上下文库模板](templates/context/README.md) |
| dot 怎样引导配置、开始使用与持续维护 | [配置引导](prompts/setup-guide.md) · [初始化](prompts/dot-onboarding.md) · [消化](prompts/daily-digest.md) · [校准](prompts/calibration.md) |

## 首版用户路径

在 dot 开始配置 → 选择目标与数据源 → 按来源完成导出或连接 → 本地保留原件并生成脱敏阅读副本 → Agent 形成第一批 Wiki → dot 读取这份库 → 把日常新表达和纠正写回 → 按用户选择保存定期任务。

Codex、ChatGPT、微信、健康与消费资料分别启用。只提供本人填写的背景或一份文件，也可以开始使用；其他来源可以以后补充。

## 开发与个人数据的分离

本目录保存通用代码、规则、模板和示例。每个用户自己的原件、来源视图、Wiki、运行状态和上传记录放在单独的个人工作区。项目仓库不包含用于建设原型的个人聊天、账单、健康资料、Cookie、密钥或真实账号配置。

实现时先读 [AGENTS.md](AGENTS.md)。

## 运行与交付边界

文件接入、基础脱敏、状态与包生成在本地运行。语义消化由实际 Agent 执行；dot 的登录、电脑连接、原生文件工具与 schedule 保存由其运行环境承接。上传支持命令适配器和原生工具回执，不绑定内部认证接口。

健康支持自述、文字、JSON/CSV 文件；具体设备专有格式通过实际样本适配。未知微信 JSON 会生成凭据处理后的适配样本；不能解析所选日期时只提供结构。图片、音视频和设备接口尚不由本版识别。

```bash
python3 -m unittest discover -s tests -v
```
