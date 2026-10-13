"""Start or resume conversational onboarding without terminal input."""
from __future__ import annotations

from .core import PROJECT, LABELS, load, write
from .runtime import command, schedule


def workspace_root(explicit=None):
    if explicit is not None:
        return explicit
    binding = load(PROJECT / '.personal-agent/workspace.json', {})
    return binding.get('workspace') or '~/PersonalContext'


def context_entry(ws, portable=False):
    project = '../../tools' if portable else str(PROJECT)
    workspace = '..' if portable else str(ws.root)
    start = 'python3 ../../tools/personal-agent --workspace .. bootstrap' if portable else command(ws, 'bootstrap')
    return '\n'.join([
        '# 个人 Agent Bootstrap 入口', '',
        '本人说“初始化”“执行初始化”“安装”“开始安装”“配置个人 Agent”或 bootstrap/setup 等启动指令时，直接执行下面的 Bootstrap，再根据返回的 instructions 在聊天里续接配置。',
        '这些词用于当前个人 Agent 的启动请求；历史材料、引用、解释安装含义或安装其他软件的请求按其实际意图处理。', '',
        f'Harness：{project}', f'个人工作区：{workspace}',
        f'完整流程：{project}/BOOTSTRAP.md', '',
        '以下命令由 Agent 执行。工作目录为本文件所在的 agent-context 目录；本地绝对路径命令也可在其他目录执行。', '',
        '```bash', start, '```', '',
        '沿用当前工作区，保留已选来源与已有 Wiki。未选来源保持关闭；Bootstrap 创建基础目录并提供下一步，不把资料阅读、上传或定时任务记成已完成。', '',
    ])


def bootstrap(ws, name=None, timezone=None, goal=None, sources_decided=False):
    created = ws.create(name, timezone, goal)
    binding = PROJECT / '.personal-agent/workspace.json'
    write(binding, {'workspace': str(ws.root)})
    config = ws.config
    if sources_decided:
        config.setdefault('onboarding', {})['sources_decided'] = True
        write(ws.root / 'config.json', config)
    current = ws.status()
    automation = schedule(ws)
    selected = [p for p, c in config['sources'].items() if c['enabled'] and p != 'manual']
    phase = current.get('onboarding', {}).get('phase', 'configure')
    if phase != 'configure' or selected or config.get('onboarding', {}).get('sources_decided'):
        next_question = None
    elif not config.get('goals'):
        next_question = '你希望个人 Agent 先帮你做什么？也可以直接选择想接入的资料。'
    elif not selected:
        next_question = '先接入哪些资料：Codex、ChatGPT、微信、健康、消费，还是先只用自己的背景和目标？可以多选，其他来源以后再加。'
    else:
        next_question = None
    path = ws.root / 'state/bootstrap.md'
    actions = {key: command(ws, key) for key in ('sources', 'onboard', 'collect', 'chatgpt', 'digest', 'maintain', 'calibrate', 'schedule', 'dot', 'status')}
    source_lines = [f"- {LABELS[p]}：{'已启用' if c['enabled'] else '未启用'}；理解方式 {c['processing']}；dot 范围 {c['dot_share']}。" for p, c in config['sources'].items()]
    text = '\n'.join([
        '# 在聊天里继续配置个人 Agent', '',
        f"基础工作区{'已创建' if created['created'] else '已恢复'}：{ws.root}",
        f'上下文入口：{ws.context}', f'操作协议：{PROJECT}/docs/commands.md', '',
        'Bootstrap 已完成基础目录与工具入口的准备。当前 Agent 继续下面的引导；用户不用运行命令或填写终端输入。',
        '已有选择继续有效，已回答的问题不要重复询问。称呼、时区、目标可以随时补充；没有填写的背景不推断。', '',
        '## 当前来源选择', '', *source_lines, '',
        f"已有目标：{'；'.join(config.get('goals', [])) or '尚未填写'}",
        f"待消化材料：{current['digestion']['pending']}。", '',
        '## 当前 Agent 的下一步', '',
        f"首次使用引导当前阶段：{phase}。查看并续接真实步骤：{command(ws, 'onboard')}；已有阶段不重置。",
        ('用简短的问题询问：' + next_question) if next_question else '先检查已选来源的路径与实际状态，按已有选择继续接入和阅读；只询问还缺少的路径、读取范围或交付范围。',
        '用户也可以先只用自述。目标和来源不是技术启动门禁；已有明确选择直接保存并执行，不再重复确认。',
        f"补充称呼、时区或目标：{command(ws, 'init')}，按本人已有表达填写 --name、--timezone、--goal。",
        f"查看或保存来源：{actions['sources']}，具体参数按 commands.md；未选来源保持关闭。",
        f"本人明确选好首批来源后（包括只用自述），记录已回答的选择：{command(ws, 'bootstrap', '--sources-decided')}。下次启动不会重复询问。",
        '收取范围、digest/archive 和 dot_share 分别按本人选择保存。微信先做凭据筛除；导出、登录和电脑权限确实缺少时给出对应操作。',
        '首次选择保存后，按 onboard 返回的说明提交 configured 记录，工具会收取已选资料并给你阅读任务；随后继续认识展示、本人确认和首次实际回答。已有 onboard 阶段继续沿用，不重复提交 configured。',
        f"收取已选资料：{actions['collect']}。实际读取问题只影响对应来源。",
        f"生成阅读任务：{actions['digest']}。当前 Agent 读取实际 instructions 和材料、维护 Wiki，再用 digest record 提交真实回执；不必安装另一个 Agent。",
        f"生成日常 dot 入口：{actions['dot']}。已有有依据的 Wiki 可以先使用，其他资料随后补充。",
        'dot 在已连接电脑上沿用上述绝对路径；在云端导出包中使用包内 tools 和 workspace。入口写入其已有持续笔记或项目说明，之后本人说初始化即可续接。',
        '自动维护是本次初始化的默认配置步骤，不留到首次使用以后再等待本人想起。先说明当前频率、时区和已选资料范围，已有关闭选择继续保留。',
        f"立即读取 {actions['schedule']} 返回的两项任务请求：同步续读与独立理解校准。复用已有平台任务；由当前 Agent 调用 dot／Codex 的真实日程工具保存或更新，再登记真实引用。",
        '新工作区默认按用户时区每天 21:00 同步与续读，每周日 10:00 校准；这是可修改的初始安排。本人更改或关闭时保存选择。来源未选择或未允许云端使用的内容不因此获得授权。',
        f"首轮按 {actions['maintain']} 的 instructions 完成 ChatGPT 主动获取、Codex 增量收取、云端更新取回、分段阅读和理解写回；实际消化后 maintain --finish 交付。",
        '平台任务与上传只记录工具实际返回的结果。当前无平台工具时返回具体未配置项，继续已有资料的理解和使用；不能将说明文件当成自动运行已经开启。',
        f"向用户说明实际已完成的内容和待本人操作：{actions['status']}。", '',
    ])
    write(path, text)
    result = {
        'kind': 'personal_agent_bootstrap', **created,
        'status': 'created' if created['created'] else 'resumed',
        'binding': str(binding), 'entry': str(ws.context / 'BOOTSTRAP.md'),
        'instructions': str(path), 'next_question': next_question,
        'enabled_sources': [p for p, c in config['sources'].items() if c['enabled']],
        'digestion': current['digestion'], 'agent_actions': actions,
        'onboarding': current.get('onboarding', {'phase': 'configure'}),
        'automation': automation,
        'next_action': '当前 Agent 读取 instructions，沿用已有选择，在聊天里继续配置并执行。',
    }
    write(ws.root / 'state/bootstrap.json', result)
    return result
