from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from zoneinfo import ZoneInfo
from .adapters import Collector, date
from .bootstrap import bootstrap, workspace_root
from .core import LABELS, PROVIDERS, Workspace, load, write
from .delivery import delivery_receipt, export_bundle, sync_incoming
from .runtime import dot_guide, execute_agent, job, schedule
from .privacy import change_selection, record_withdrawal, withdraw


def sources(ws, args):
    config = ws.config
    if args.source:
        options = config['sources'][args.source]
        before = deepcopy(options)
        if args.enable or args.disable:
            options['enabled'] = args.enable
        if args.enable:
            options['use_allowed'] = True
        if args.clear_paths:
            options['paths'] = []
        if args.path:
            options['paths'] = list(dict.fromkeys([*options.get('paths', []), *(str(Path(p).expanduser().resolve()) for p in args.path)]))
        for name in ('mode', 'processing', 'cli'):
            if getattr(args, name) is not None:
                options[name] = getattr(args, name)
        if args.share is not None:
            options['dot_share'] = args.share
        scope = options.setdefault('scope', {})
        for key, value in (('from', args.from_date), ('until', args.until_date)):
            if value is not None:
                date(value, ZoneInfo(config['owner']['timezone']))
                scope[key] = value
        if args.clear_dates:
            scope.pop('from', None)
            scope.pop('until', None)
        for key, items, clear in (('include_ids', args.include, args.clear_includes), ('exclude_ids', args.exclude, args.clear_excludes)):
            if clear:
                scope[key] = []
            if items:
                scope[key] = list(dict.fromkeys([*scope.get(key, []), *items]))
        write(ws.root / 'config.json', config)
        changed = change_selection(ws, args.source, before, options)
        catalog = ws.catalog()
        if args.source in catalog['sources']:
            catalog['sources'][args.source]['selection'] = deepcopy(ws.config['sources'][args.source])
            ws.save_catalog(catalog)
        if changed and changed['kind'] == 'scope_changed' and options['enabled']:
            Collector(ws).collect(args.source)
        else:
            ws.refresh_published()
        ws.refresh()
    if args.discover:
        if not args.source:
            raise ValueError('会话发现需要指定来源')
        return Collector(ws).discover(args.source)
    return ws.config['sources']


def setup(ws):
    def ask(title, default=''):
        suffix = f' [{default}]' if default else ''
        return input(f'{title}{suffix}：').strip() or default
    name, timezone, question = ask('怎样称呼你', '用户'), ask('时区', 'Asia/Shanghai'), ask('当前最希望个人 Agent 帮你解决什么问题')
    ws.create(name, timezone, question)
    config = ws.config
    for provider in PROVIDERS:
        options = config['sources'][provider]
        options['enabled'] = ask(f'启用{LABELS[provider]}资料吗 y/n', 'y' if provider == 'manual' else 'n').lower() in ('y', 'yes', '是')
        if not options['enabled']:
            continue
        path = ask('资料路径，可留空后投放到 inbox')
        if path:
            options['paths'] = [str(Path(path).expanduser().resolve())]
        if provider == 'codex' and not path:
            options['mode'] = 'local'
        if provider == 'wechat':
            options['mode'] = {'1': 'import', '2': 'cli'}[ask('微信资料：1 已导出的文件 / 2 已安装并登录的本机读取工具', '1')]
        if provider in ('codex', 'chatgpt', 'wechat', 'health', 'payments'):
            scope = options.setdefault('scope', {})
            for field, title in (('from', '开始日期 YYYY-MM-DD，留空不限'), ('until', '结束日期 YYYY-MM-DD，留空不限')):
                value = ask(title)
                if value:
                    date(value, ZoneInfo(config['owner']['timezone']))
                    scope[field] = value
            if provider in ('codex', 'chatgpt', 'wechat'):
                print('如需选择部分会话，让 dot 按标题展示并保存你的选择。')
        options['processing'] = {'1': 'digest', '2': 'archive'}[ask('这些资料：1 交给 Agent 阅读并整理认识 / 2 只在本地归档', '1')]
        options['dot_share'] = {'1': 'wiki', '2': 'sources', '3': 'none'}[ask('日常 dot 可用：1 整理出的认识 / 2 认识和所选原文副本 / 3 暂不提供', '1')]
    write(ws.root / 'config.json', config)
    at = ask('自动同步续读的每日时间 HH:MM，输入 暂停 可关闭', ws.config['maintenance'].get('time', '21:00'))
    if at == '暂停':
        schedule(ws, disable=True)
        schedule(ws, task='calibration', disable=True)
    elif at:
        schedule(ws, at)
    from .onboarding import onboard
    return onboard(ws, {'stage': 'configured', 'question': question})


def human_status(value):
    lines = [f"个人库：{value['context']}", '']
    for provider, item in value['sources'].items():
        run = item.get('last_run') or {}
        processing = {'digest': '用于整理认识', 'archive': '只归档'}[item['processing']]
        sharing = {'none': '暂不提供', 'wiki': '个人认识', 'sources': '认识与所选原文副本'}[item['dot_share']]
        lines.append(f"{LABELS[provider]}：{'采集启用' if item['enabled'] else '采集关闭'}，已有 {item['records']} 份材料，{processing}；日常 dot 可用：{sharing}。")
        if run.get('next_action'):
            lines.append(f"  下一步：{run['next_action']}")
        if run.get('errors'):
            lines.append(f"  有 {len(run['errors'])} 项实际读取问题，其他已收取材料保留。")
    counts = value['digestion']
    lines += ['', f"当前版本已读 {counts['reviewed_current']}，待阅读或更新 {counts['pending']}。"]
    capture = value.get('chatgpt_capture', {})
    if capture.get('status') == 'available_tool_pages':
        lines.append(f"ChatGPT 工具已发现 {capture['known_threads']} 个所选会话，其中 {capture['history_pending']} 个仍有历史待获取、{capture['truncated_threads']} 个含截断；发现覆盖范围以实际工具返回为准。")
    if value['delivery']:
        lines.append(f"最近交付：{value['delivery'].get('status')}；包 {value['delivery'].get('bundle', '')}。")
    if value.get('sync', {}).get('conflicts'):
        lines.append(f"同步保留了 {len(value['sync']['conflicts'])} 项双方修改，位置见 state/sync.json，按原话合并对应主落点。")
    for key, label, item in [('maintenance', '同步续读', value['maintenance']), ('calibration', '理解校准', value['maintenance'].get('calibration', {}))]:
        if item.get('enabled'):
            cadence = '每天' if key == 'maintenance' else '每周 ' + item.get('day', '待选择')
            lines.append(f"{label}：{cadence} {item.get('time', '待选择')}；{'已登记平台任务 ' + item['saved_schedule_ref'] if item.get('saved_schedule_ref') else '待当前 Agent 保存平台任务'}；运行位置 {item.get('executor', '待平台返回')}。")
        else:
            lines.append(f'{label}：已关闭。')
    if value.get('dot_view'):
        lines.append(f"dot 入口最近更新：{value['dot_view'].get('updated_at', '见发布目录')}。")
    phases = {'configure': '选择目标和资料', 'review': '整理并展示个人认识', 'confirm': '等待本人确认或纠正', 'first_use': '结合认识处理当前问题', 'ready': '已完成首次使用'}
    lines.append('首次引导：' + phases[value.get('onboarding', {}).get('phase', 'configure')] + '。')
    pending = sum(r['status'] != 'removed' for a in value.get('withdrawals', {}).get('actions', []) for r in a['remote'])
    if pending:
        lines.append(f'还有 {pending} 项已登记远端副本待 dot 按撤回请求处理。')
    if value.get('wiki_rebuild', {}).get('pages'):
        lines.append(f"资料范围改变后，有 {len(value['wiki_rebuild']['pages'])} 页认识待依据仍获准的资料重建。")
    return '\n'.join(lines)


def parser():
    p = argparse.ArgumentParser(description='Personal Agent Harness：按选择接入资料，维护个人 Wiki，交给 dot 使用。')
    p.add_argument('--workspace', '-w', help='个人库路径；省略时沿用本项目 Bootstrap 绑定的工作区，初次为 ~/PersonalContext')
    p.add_argument('--json', action='store_true')
    commands = p.add_subparsers(dest='command', required=True)
    for name, help_text in (('bootstrap', '自动创建或恢复工作区，由当前 Agent 在聊天里引导'), ('init', '创建工作区与 Wiki')):
        init = commands.add_parser(name, help=help_text)
        for flag in ('name', 'timezone', 'goal'):
            init.add_argument('--' + flag)
        if name == 'bootstrap':
            init.add_argument('--sources-decided', action='store_true', help='记录本人已选好首批来源，包括先只用自述')
    commands.add_parser('setup', help='逐步配置可选来源')
    onboarding = commands.add_parser('onboard', help='dot 承接首次引导、本人确认和首次实际使用')
    onboarding.add_argument('action', nargs='?', choices=('next', 'record'), default='next')
    onboarding.add_argument('response', nargs='?', type=Path)
    withdrawal = commands.add_parser('withdraw', help='按本人请求停止使用某来源，撤回依赖它的认识')
    withdrawal.add_argument('source', nargs='?', choices=PROVIDERS)
    withdrawal.add_argument('--delete-raw', action='store_true', help='同时删除本工作区保存的原件；外部原始文件不变')
    withdrawal.add_argument('--receipt', type=Path)
    source = commands.add_parser('sources', help='查看或修改单个来源')
    source.add_argument('source', nargs='?', choices=PROVIDERS)
    state = source.add_mutually_exclusive_group()
    state.add_argument('--enable', action='store_true')
    state.add_argument('--disable', action='store_true')
    source.add_argument('--path', action='append')
    source.add_argument('--mode')
    source.add_argument('--cli')
    source.add_argument('--discover', action='store_true')
    source.add_argument('--processing', choices=('digest', 'archive'))
    source.add_argument('--share', choices=('none', 'wiki', 'sources'))
    source.add_argument('--from', dest='from_date')
    source.add_argument('--until', dest='until_date')
    source.add_argument('--include', action='append')
    source.add_argument('--exclude', action='append')
    for flag in ('clear-paths', 'clear-dates', 'clear-includes', 'clear-excludes'):
        source.add_argument('--' + flag, action='store_true')
    collect = commands.add_parser('collect', help='收取已启用来源')
    collect.add_argument('--source', choices=PROVIDERS)
    collect.add_argument('--force', action='store_true', help='解析器改动后重新读取选定来源')
    digest = commands.add_parser('digest', help='生成阅读任务或提交实际回执')
    digest.add_argument('action', nargs='?', choices=('pending', 'record'), default='pending')
    digest.add_argument('response', nargs='?', type=Path)
    digest.add_argument('--source', choices=PROVIDERS)
    digest.add_argument('--limit', type=int, help='本轮批次大小，其他材料继续留在队列')
    digest.add_argument('--run-agent', action='store_true')
    digest.add_argument('--max-chars', type=int, default=24000, help='每份长聊天本轮文字分段大小；不是阅读完成条件')
    chatgpt = commands.add_parser('chatgpt', help='为 Agent 生成 App 获取请求，保存发现结果与正文分页')
    chatgpt.add_argument('action', nargs='?', choices=('plan', 'discover', 'record'), default='plan')
    chatgpt.add_argument('--response', type=Path)
    chatgpt.add_argument('--lane', choices=('recent', 'archived'), default='recent')
    chatgpt.add_argument('--cursor', help='本次 read_thread 请求使用的原始 cursor')
    feedback = commands.add_parser('feedback', help='把本人确认写回 Wiki 章节')
    body = feedback.add_mutually_exclusive_group(required=True)
    body.add_argument('--text')
    body.add_argument('--file', type=Path)
    feedback.add_argument('--page', default='wiki/关于用户/当前认识.md')
    feedback.add_argument('--section', default='最新本人确认')
    feedback.add_argument('--source-id')
    export = commands.add_parser('export', help='按选择生成云端上下文包')
    export.add_argument('--output', type=Path)
    export.add_argument('--return', dest='returning', action='store_true')
    export.add_argument('--upload', action='store_true')
    export.add_argument('--receipt', type=Path)
    sync = commands.add_parser('sync', help='合并返回包或准备云端交付')
    sync.add_argument('--incoming', type=Path)
    sync.add_argument('--upload', action='store_true')
    for name in ('dot', 'status'):
        item = commands.add_parser(name)
        item.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
    runtime = commands.add_parser('runtime', help='配置外部 Agent 或上传命令')
    runtime.add_argument('--agent-command-json')
    runtime.add_argument('--uploader-command-json')
    runtime.add_argument('--sync-directory', help='另一端文件工具投递返回包的交换目录，默认在个人工作区 exchange')
    sync_choice = runtime.add_mutually_exclusive_group()
    sync_choice.add_argument('--cloud-sync', action='store_true')
    sync_choice.add_argument('--no-cloud-sync', action='store_true')
    schedules = commands.add_parser('schedule', help='生成周期请求并记录真实任务引用')
    schedules.add_argument('--daily-at')
    schedules.add_argument('--confirm-ref')
    schedules.add_argument('--disable', action='store_true')
    schedules.add_argument('--enable', action='store_true')
    schedules.add_argument('--cancelled', action='store_true', help='平台实际取消后清除该任务引用')
    schedules.add_argument('--task', choices=('maintenance', 'calibration'), default='maintenance')
    schedules.add_argument('--weekly-on', choices=('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'))
    schedules.add_argument('--executor', help='实际保存任务的运行环境，如 codex-local 或 dot-cloud')
    commands.add_parser('calibrate', help='生成独立的个人理解校准任务')
    maintain = commands.add_parser('maintain', help='收取、消化与已选交付')
    maintain.add_argument('--run-agent', action='store_true')
    maintain.add_argument('--limit', type=int)
    maintain.add_argument('--export', action='store_true')
    maintain.add_argument('--upload', action='store_true')
    maintain.add_argument('--force', action='store_true')
    maintain.add_argument('--finish', action='store_true', help='实际消化后刷新入口并执行已选交付')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        ws = Workspace(workspace_root(args.workspace))
        if args.command == 'bootstrap':
            result = bootstrap(ws, args.name, args.timezone, args.goal, args.sources_decided)
        elif args.command == 'init':
            result = ws.create(args.name, args.timezone, args.goal)
            from .onboarding import onboard
            result['onboarding'] = onboard(ws)
        elif args.command == 'setup':
            result = setup(ws)
        elif args.command == 'onboard':
            from .onboarding import onboard
            if args.action == 'record' and not args.response:
                raise ValueError('onboard record 需要实际交互记录')
            result = onboard(ws, load(args.response) if args.action == 'record' else None)
        elif args.command == 'withdraw':
            if args.receipt:
                result = record_withdrawal(ws, load(args.receipt))
            elif args.source:
                result = withdraw(ws, args.source, args.delete_raw)
            else:
                raise ValueError('withdraw 需要来源或实际远端回执')
        elif args.command == 'sources':
            result = sources(ws, args)
        elif args.command == 'collect':
            result = Collector(ws).collect(args.source, args.force)
        elif args.command == 'digest':
            if args.action == 'record':
                if not args.response:
                    raise ValueError('digest record 需要实际回执文件')
                result = ws.record(load(args.response.expanduser()))
            else:
                result = job(ws, args.source, args.limit, args.max_chars)
                if args.run_agent:
                    result = execute_agent(ws, result)
        elif args.command == 'feedback':
            result = ws.feedback(args.text if args.text is not None else args.file.read_text(), args.page, args.section, args.source_id)
        elif args.command == 'chatgpt':
            from .chatgpt import discover, plan, record
            if args.action == 'plan':
                result = plan(ws)
            elif not args.response:
                raise ValueError('保存 App 返回需要 --response 原始结构化结果.json')
            elif args.action == 'discover':
                result = discover(ws, load(args.response), args.lane)
            else:
                result = record(ws, load(args.response), args.cursor)
        elif args.command == 'export':
            result = delivery_receipt(ws, load(args.receipt)) if args.receipt else export_bundle(ws, args.output, args.returning, args.upload)
        elif args.command == 'sync':
            result = sync_incoming(ws, args.incoming) if args.incoming else export_bundle(ws, upload=args.upload)
        elif args.command == 'dot':
            result = dot_guide(ws)
        elif args.command == 'status':
            result = ws.status()
        elif args.command == 'runtime':
            config = ws.config
            for key in ('agent', 'uploader'):
                raw = getattr(args, key + '_command_json')
                if raw is not None:
                    value = json.loads(raw)
                    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                        raise ValueError('运行命令须为 JSON 字符串数组')
                    config['runtime'][key + '_command'] = value
            if args.sync_directory is not None:
                config['runtime']['sync_directory'] = str(Path(args.sync_directory).expanduser().resolve()) if args.sync_directory else ''
            if args.cloud_sync or args.no_cloud_sync:
                config['maintenance']['cloud_sync'] = args.cloud_sync
            write(ws.root / 'config.json', config)
            result = config['runtime']
        elif args.command == 'schedule':
            result = schedule(ws, args.daily_at, args.confirm_ref, args.task, args.weekly_on, args.disable, args.enable, args.executor, args.cancelled)
        elif args.command == 'calibrate':
            from .maintenance import calibrate
            result = calibrate(ws)
        else:
            from .maintenance import finish, maintain
            result = finish(ws, args.upload, args.export) if args.finish else maintain(ws, args.limit, args.force, args.run_agent, args.upload, args.export)
        if args.command == 'status' and not args.json:
            print(human_status(result))
        elif args.command == 'dot' and not args.json:
            print(result['text'])
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        p.exit(1, f'{type(error).__name__}: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
