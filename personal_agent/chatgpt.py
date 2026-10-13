"""Durable discovery and paging for the agent's actual app tools."""
from __future__ import annotations

from .core import load, safe_id, write


def enabled(ws):
    source = ws.config['sources']['chatgpt']
    return source['enabled'] and source.get('use_allowed', True) and source.get('mode') == 'app'


def selected(ws, ident):
    scope = ws.config['sources']['chatgpt'].get('scope', {})
    return (not scope.get('include_ids') or ident in scope['include_ids']) and ident not in scope.get('exclude_ids', [])


def state_path(ws):
    return ws.root / 'state/chatgpt-capture.json'


def snapshot_path(ws, ident):
    return ws.root / 'inbox/chatgpt/app' / (safe_id(ident) + '.json')


def capture_status(ws):
    if not enabled(ws):
        return {'status': 'app_capture_disabled'}
    state = load(state_path(ws), {'threads': {}, 'discovery': {}})
    items = [r for ident, r in state['threads'].items() if selected(ws, ident)]
    return {'status': 'available_tool_pages', 'known_threads': len(items),
            'history_pending': sum(not r.get('history_complete') or bool(r.get('head_cursor')) for r in items),
            'truncated_threads': sum(bool(r.get('truncated')) for r in items),
            'discovery': state.get('discovery', {})}


def plan(ws):
    if not enabled(ws):
        return {'status': 'disabled', 'requests': []}
    state = load(state_path(ws), {'threads': {}, 'discovery': {}})
    scope = ws.config['sources']['chatgpt'].get('scope', {})
    imported = [r['id'] for r in ws.catalog()['records'].values() if r['provider'] == 'chatgpt']
    ids = list(dict.fromkeys([*scope.get('include_ids', []), *state['threads'], *imported]))
    requests = []
    for ident in ids:
        if not selected(ws, ident):
            continue
        item = state['threads'].get(ident, {})
        # Poll the head on every maintenance run; resume the saved historical
        # cursor separately so a new message never discards unfinished history.
        requests.append({'tool': 'read_thread', 'args': {'threadId': ident, 'includeOutputs': True}, 'purpose': 'latest'})
        if item.get('head_cursor'):
            requests.append({'tool': 'read_thread', 'args': {'threadId': ident, 'cursor': item['head_cursor'], 'includeOutputs': True}, 'purpose': 'recent_gap'})
        if item.get('next_cursor'):
            requests.append({'tool': 'read_thread', 'args': {'threadId': ident, 'cursor': item['next_cursor'], 'includeOutputs': True}, 'purpose': 'history'})
    return {
        'status': 'awaiting_app_tools', 'scope': scope, 'requests': requests,
        'discovery': [
            {'tool': 'list_threads', 'args': {}, 'lane': 'recent', 'coverage': '工具返回的最近和置顶会话；不是全量历史清单'},
            {'tool': 'list_archived_threads', 'args': {'source': 'chatgpt', **({'cursor': state.get('discovery', {}).get('archived', {}).get('next_cursor')} if state.get('discovery', {}).get('archived', {}).get('next_cursor') else {})}, 'lane': 'archived'},
        ],
        'state': str(state_path(ws)),
        'next_action': '当前 Agent 调用实际 App 工具，将原始结构化结果用 chatgpt discover/record 保存；按返回游标继续。工具只返回摘要或截断时保留缺口。',
    }


def discover(ws, response, lane='recent'):
    if not enabled(ws):
        return {'status': 'disabled', 'requests': []}
    state = load(state_path(ws), {'threads': {}, 'discovery': {}})
    rows = [*response.get('pinnedThreads', []), *response.get('threads', [])]
    found = []
    for row in rows:
        if row.get('kind') != 'chatgpt':
            continue
        ident = row.get('id') or row.get('threadId')
        if not ident or not selected(ws, ident):
            continue
        item = state['threads'].setdefault(ident, {})
        item.update(title=row.get('title', ident), discovered_at=ws.now())
        found.append(ident)
    state.setdefault('discovery', {})[lane] = {
        'at': ws.now(), 'next_cursor': response.get('nextCursor') or response.get('page', {}).get('nextCursor'),
        'coverage': 'returned_items_only',
    }
    write(state_path(ws), state)
    return {**plan(ws), 'discovered': found}


def record(ws, page, cursor=None):
    if not enabled(ws):
        return {'status': 'disabled', 'requests': []}
    thread = page.get('thread', {})
    if thread.get('kind') != 'chatgpt' or not thread.get('id'):
        raise ValueError('需要 read_thread 的 ChatGPT 原始结构化返回，不能把摘要列表作为正文。')
    ident = thread['id']
    if not selected(ws, ident):
        return {'status': 'outside_selection', 'thread_id': ident}
    path = snapshot_path(ws, ident)
    snapshot = load(path, {'provider': 'codex_app.read_thread', 'pages': [], 'page_cursors': []})
    pages, cursors = snapshot['pages'], snapshot['page_cursors']
    state = load(state_path(ws), {'threads': {}, 'discovery': {}})
    item = state['threads'].setdefault(ident, {})
    def message_ids(values):
        return {i['id'] for p in values for t in p.get('turns', []) for i in t.get('items', []) if i.get('id')}
    known = message_ids(pages)
    incoming_ids = message_ids([page])
    overlap = bool(known.intersection(incoming_ids))
    recent_gap = cursor is not None and cursor == item.get('head_cursor')
    # Retain an older head when there is a gap. It remains useful evidence even
    # if native historical cursors change after new messages arrive.
    if cursor is None and None in cursors:
        old_head = cursors.index(None)
        if message_ids([pages[old_head]]) - incoming_ids:
            cursors[old_head] = 'saved-head-' + str(len(pages))
    if cursor in cursors:
        pages[cursors.index(cursor)] = page
    elif cursor is None:
        pages.insert(0, page)
        cursors.insert(0, None)
    elif recent_gap:
        pages.insert(1, page)
        cursors.insert(1, cursor)
    else:
        pages.append(page)
        cursors.append(cursor)
    snapshot['captured_at'] = ws.now()
    paging = page.get('page', {})
    next_cursor = paging.get('nextCursor')
    # A head refresh does not rewind an established history traversal.
    if (cursor is not None and not recent_gap) or 'history_complete' not in item:
        item.update(next_cursor=next_cursor, history_complete=paging.get('hasMore') is False)
    if recent_gap or (cursor is None and known and not overlap):
        item['head_cursor'] = None if overlap or paging.get('hasMore') is False else next_cursor
    if cursor is None and item.get('history_complete') and item.get('updated_at') != thread.get('updatedAt'):
        # A changed conversation can include edits outside the latest page.
        # Revisit its history using the provider's cursor, without discarding
        # already captured pages or their reading receipts in the meantime.
        item.update(next_cursor=next_cursor, history_complete=paging.get('hasMore') is False)
    if cursor is None:
        item['updated_at'] = thread.get('updatedAt')
    item.update(title=thread.get('title', ident), captured_at=ws.now(), snapshot=str(path))
    item['coverage'] = 'available_tool_pages'
    snapshot['history_complete'] = item.get('history_complete', False) and not item.get('head_cursor')
    from .parsers import parse_chatgpt_app
    metadata, _ = parse_chatgpt_app(snapshot)
    item['truncated'] = metadata['truncated_messages'] > 0
    write(path, snapshot)
    write(state_path(ws), state)
    return {'status': 'captured', 'thread_id': ident, **item,
            'next_action': '继续 next_cursor 的历史分页；collect 会把当前已取得文字放入阅读队列。没有返回的内容仍未取得。'}
