from __future__ import annotations

import argparse
import json
from pathlib import Path

from zoneinfo import ZoneInfo
from .adapters import Collector, date
from .core import LABELS, PROVIDERS, Workspace, load, write
from .delivery import delivery_receipt, export_bundle, sync_incoming
from .runtime import dot_guide, execute_agent, job, schedule


def sources(ws, args):
    config = ws.config
    if args.source:
        options = config['sources'][args.source]
        if args.enable or args.disable:
            options['enabled'] = args.enable
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
        ws.refresh()
    if args.discover:
        if not args.source:
            raise ValueError('会话发现需要指定来源')
        return Collector(ws).discover(args.source)
    return config['sources']


def setup(ws):
    def ask(title, default=''):
        suffix = f' [{default}]' if default else ''
        return input(f'{title}{suffix}：').strip() or default
    ws.create(ask('怎样称呼你', '用户'), ask('时区', 'Asia/Shanghai'), ask('希望个人 Agent 帮你做什么'))
    config = ws.config
    for provider in PROVIDERS:
        options = config['sources'][provider]
        options['enabled'] = ask(f'启用{LABELS[provider]}资料吗 y/n', 'y' if provider == 'manual' else 'n').lower() in ('y', 'yes', '是')
        if not options['enabled']:
            continue
        path = ask('资料路径，可留空后投放到 inbox')
        if path:
            options['paths'] = [str(Path(path).expanduser().resolve())]
        if provider == 'wechat':
            options['mode'] = ask('微信方式 import 已导出文件 / cli 本机工具', 'import')
        if provider in ('codex', 'chatgpt', 'wechat', 'health', 'payments'):
            scope = options.setdefault('scope', {})
            for field, title in (('from', '开始日期 YYYY-MM-DD，留空不限'), ('until', '结束日期 YYYY-MM-DD，留空不限')):
                value = ask(title)
                if value:
                    date(value, ZoneInfo(config['owner']['timezone']))
                    scope[field] = value
            if provider in ('codex', 'chatgpt', 'wechat'):
                print('会话范围可在接入说明中先按标题发现，再用 sources --include/--exclude 选择。')
        options['processing'] = ask('digest 更新理解 / archive 只归档', 'digest')
        options['dot_share'] = ask('dot 使用范围 none/wiki/sources', 'wiki')
        if options['processing'] not in ('digest', 'archive') or options['dot_share'] not in ('none', 'wiki', 'sources'):
            raise ValueError('处理方式或交付范围不在当前支持的选项中')
    write(ws.root / 'config.json', config)
    at = ask('每天整理时间 HH:MM，留空暂不开启')
    if at:
        schedule(ws, at)
    return {'workspace': str(ws.root), 'next': ['collect', 'digest', 'dot'], 'dot': dot_guide(ws)['guide']}


def human_status(value):
    lines = [f"个人库：{value['context']}", '']
    for provider, item in value['sources'].items():
        run = item.get('last_run') or {}
        lines.append(f"{LABELS[provider]}：{'采集启用' if item['enabled'] else '采集关闭'}，已有 {item['records']} 份材料，处理 {item['processing']}，dot 范围 {item['dot_share']}。")
        if run.get('next_action'):
            lines.append(f"  下一步：{run['next_action']}")
        if run.get('errors'):
            lines.append(f"  有 {len(run['errors'])} 项实际读取问题，其他已收取材料保留。")
    counts = value['digestion']
    lines += ['', f"当前版本已读 {counts['reviewed_current']}，待阅读或更新 {counts['pending']}。"]
    if value['delivery']:
        lines.append(f"最近交付：{value['delivery'].get('status')}；包 {value['delivery'].get('bundle', '')}。")
    if value.get('sync', {}).get('conflicts'):
        lines.append(f"同步保留了 {len(value['sync']['conflicts'])} 项双方修改，位置见 state/sync.json，按原话合并对应主落点。")
    if value['maintenance'].get('enabled'):
        item = value['maintenance']
        lines.append(f"持续安排：每天 {item.get('time', '待选择')}；{'已记录平台任务引用' if item.get('saved_schedule_ref') else '待 dot 保存平台任务'}。")
    return '\n'.join(lines)


def parser():
    p = argparse.ArgumentParser(description='Personal Agent Harness：按选择接入资料，维护个人 Wiki，交给 dot 使用。')
    p.add_argument('--workspace', '-w', default='~/PersonalContext')
    p.add_argument('--json', action='store_true')
    commands = p.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='创建工作区与 Wiki')
    for flag in ('name', 'timezone', 'goal'):
        init.add_argument('--' + flag)
    commands.add_parser('setup', help='逐步配置可选来源')
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
    schedules = commands.add_parser('schedule', help='生成周期请求并记录真实任务引用')
    schedules.add_argument('--daily-at')
    schedules.add_argument('--confirm-ref')
    schedules.add_argument('--disable', action='store_true')
    maintain = commands.add_parser('maintain', help='收取、消化与已选交付')
    maintain.add_argument('--run-agent', action='store_true')
    maintain.add_argument('--limit', type=int)
    maintain.add_argument('--export', action='store_true')
    maintain.add_argument('--upload', action='store_true')
    maintain.add_argument('--force', action='store_true')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    ws = Workspace(args.workspace)
    try:
        if args.command == 'init':
            result = ws.create(args.name, args.timezone, args.goal)
        elif args.command == 'setup':
            result = setup(ws)
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
                result = job(ws, args.source, args.limit)
                if args.run_agent:
                    result = execute_agent(ws, result)
        elif args.command == 'feedback':
            result = ws.feedback(args.text if args.text is not None else args.file.read_text(), args.page, args.section, args.source_id)
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
            write(ws.root / 'config.json', config)
            result = config['runtime']
        elif args.command == 'schedule':
            if args.disable:
                config = ws.config
                config['maintenance']['enabled'] = False
                write(ws.root / 'config.json', config)
                result = {'status': 'selection_disabled', 'saved_schedule_ref': config['maintenance'].get('saved_schedule_ref'), 'next_action': '如平台已有任务，让 dot 取消真实任务并读取结果。'}
            else:
                result = schedule(ws, args.daily_at, args.confirm_ref)
        else:
            collected = Collector(ws).collect(force=args.force)
            prepared = job(ws, limit=args.limit)
            processed = execute_agent(ws, prepared) if args.run_agent and prepared['sources'] else {**prepared, 'status': 'awaiting_agent' if prepared['sources'] else 'no_pending_sources'}
            result = {'collection': collected, 'digestion': processed}
            if args.export or args.upload:
                result['delivery'] = export_bundle(ws, upload=args.upload)
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
