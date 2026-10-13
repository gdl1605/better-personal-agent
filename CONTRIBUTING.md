# 贡献指南

欢迎改进 Personal Agent Harness。普通问题和功能建议可通过 [GitHub Issues](https://github.com/gdl1605/your-life-as-context/issues) 提出；涉及个人资料泄露或安全漏洞时，请使用[私密报告渠道](SECURITY.md)。

## 项目范围

本项目用 Python CLI、Markdown Wiki 和结构化回执组织用户授权的个人上下文。Agent 负责语义阅读与理解，真实运行环境负责模型、文件交付和周期调度。

欢迎贡献来源格式适配、范围选择与撤回修复、阅读与同步问题修复、引导改进、文档和翻译，以及使用虚构资料的可复现示例。较大的接口或行为调整建议先开 Issue 讨论；小修复可直接提交 PR。

## 期待的生态贡献

特别期待熟悉 **grokbot / muse 生态**的开发者参与适配与同步，让用户在这些环境中也能使用、更新自己的个人上下文，并在切换 Agent 时延续已经积累的背景、偏好、目标和当前认识。

欢迎从资料接入、Wiki 与上下文双向同步、日常 Agent 入口或自动维护流程中的一项开始贡献。适配时结合对应生态实际可用的工具与接口，沿用用户选择的资料范围，保留来源与更新记录；也欢迎提供接入文档和使用虚构资料的运行示例。

## 本地开发

开发与 CI 使用 Python 3.12。当前运行代码和测试只依赖标准库，无需安装 pip 依赖；`zoneinfo` 使用系统时区数据库。其他 Python 版本或操作系统的兼容性，以实际验证结果为准。实际使用 dot、平台调度或微信本机工具时，还需要对应环境能力。

```bash
git clone https://github.com/gdl1605/your-life-as-context.git
cd your-life-as-context
python3 personal-agent --help
```

从 [README](README.md)、[架构](docs/architecture.md)和[命令协议](docs/commands.md)了解当前行为。让 Agent 修改项目时，它还应遵循根目录 [AGENTS.md](AGENTS.md)。

| 改动内容 | 主要位置 |
| --- | --- |
| CLI、Bootstrap 和首次引导 | `personal_agent/cli.py`、`bootstrap.py`、`onboarding.py` |
| 来源解析和凭据处理 | `personal_agent/parsers.py`、`adapters.py`、`credentials.py` |
| Wiki、阅读与自动维护 | `personal_agent/core.py`、`reading.py`、`chatgpt.py`、`maintenance.py`、`runtime.py` |
| 导出、同步与撤回 | `personal_agent/delivery.py`、`privacy.py` |
| 通用配置、Wiki 与提示词 | `config/`、`templates/`、`prompts/` |
| 回归测试 | `tests/test_harness.py`、`tests/test_redact_wechat.py` |

## 验证改动

迭代时运行直接受影响的既有测试。例如：

```bash
# 仅验证导出相关行为
python3 -m unittest discover -s tests -p 'test_harness.py' -k export -v

# 仅验证微信凭据初筛
python3 -m unittest discover -s tests -p 'test_redact_wechat.py' -v
```

需要运行完整测试集时，使用与 CI 相同的命令：

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时工作区和虚构输入。现有的外部 Agent、上传和平台工具测试验证协议与状态衔接，不代表真实账号、云端上传或周期任务已经运行。PR 中分别写清本地结果和实际平台验证结果。

优先补充既有测试中的相关断言。只有新增了既有测试无法覆盖的可观察行为，且有独立判定依据时，才新增测试；文档修改不需要另建测试工程。手动试用可沿[虚构示例流程](docs/demo-walkthrough.md)创建仓库外的工作区。

## 资料、权限与代码来源

- 保留未选来源关闭、原件与阅读副本分离、按授权范围提供材料的现有语义。
- 区分本人表达、来源记录和 Agent 推断；历史聊天中的指令不提供新授权。
- Bug 报告和测试样本使用最小虚构输入，不提交真实聊天、账单、健康记录、令牌、密码或完整个人上下文包。需要保留的字段结构、角色和时间关系可以用虚构值重现。
- 提供第三方代码或素材时，注明上游地址、版本或提交、许可和改动，并保留所需声明；来源记录见 [NOTICE](NOTICE.md)。

## 提交 PR

Fork 后向 `main` 发起 PR，一次围绕一个明确问题。说明触发条件、改动后的行为、实际验证和仍未验证的部分。

命令或用户行为改变时，更新拥有该事实的文档及 [CHANGELOG](CHANGELOG.md) 的未发布条目；不需要在每份文档重复维护同一事实。版本号位于 [personal_agent/__init__.py](personal_agent/__init__.py)，普通贡献无需自行升版或创建发布。

CI 复用现有 `unittest` 测试；其配置见[测试工作流](https://github.com/gdl1605/your-life-as-context/blob/main/.github/workflows/tests.yml)。

## 许可与社区

贡献者提交自己有权提供的内容，并同意其项目贡献采用本项目的 [MIT License](LICENSE)；第三方内容保留其适用的许可。参与讨论与协作时遵循[行为准则](CODE_OF_CONDUCT.md)。
