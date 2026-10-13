"""Make bounded reading copies with resumable positions, not completion claims."""
from __future__ import annotations

from .core import load, within, write


def segment(ws, record, target, max_chars=24000):
    previous = record.get('previous_review') or {}
    resume = previous.get('next_read') if previous.get('revision') == record['revision'] else None
    originals = record.get('readable_originals', [])
    for original in originals:
        path = within(ws.context, original, 'sources')
        if path.suffix != '.json':
            continue
        value = load(path)
        if not isinstance(value, dict):
            continue
        if value.get('kind') == 'selected_reading_copy':
            messages, collection = value['messages'], 'messages'
        elif value.get('provider') == 'wechat-selected-reading-copy':
            fields = ('server_id', 'local_id', 'create_time', 'sender_is_self', 'sender_display_name',
                      'sender_wxid', 'chat_display_name', 'message_kind', 'local_type', '_origin_record', '_origin_pointer')
            messages = [{**{k: row[k] for k in fields if k in row},
                         'text': row.get('display_text_rendered') or row.get('display_text') or row.get('message_content') or ''}
                        for row in value['rows']]
            collection = 'rows'
        else:
            continue
        start = resume if isinstance(resume, dict) and 'message' in resume else {'message': 0, 'offset': 0}
        index, offset = int(start['message']), int(start.get('offset', 0))
        if index < 0 or index > len(messages) or offset < 0:
            index, offset = 0, 0
        start = {'message': index, 'offset': offset}
        excerpts, remaining = [], max_chars
        while index < len(messages) and remaining > 0:
            message = messages[index]
            body = message.get('text', '')
            end = min(len(body), offset + remaining)
            excerpts.append({**message, 'text': body[offset:end], 'reading_pointer': f'{original}#/{collection}/{index}', 'text_range': [offset, end]})
            remaining -= max(1, end - offset)
            if end < len(body):
                offset = end
                break
            index += 1
            offset = 0
        more = index < len(messages)
        value = {'key': record['key'], 'revision': record['revision'], 'start': start,
                 'next_read': {'message': index, 'offset': offset} if more else None,
                 'total_messages': len(messages), 'more': more, 'messages': excerpts}
        write(target, value)
        return {k: v for k, v in {**value, 'path': str(target)}.items() if k != 'messages'}
    # Imported diaries, Markdown chat exports and tables also need bounded
    # reading; their generated view retains the original row/source pointers.
    text = (ws.context / record['document']).read_text(encoding='utf-8')
    offset = int(resume.get('offset', 0)) if isinstance(resume, dict) else 0
    if offset < 0 or offset > len(text):
        offset = 0
    end = min(len(text), offset + max_chars)
    more = end < len(text)
    value = {'key': record['key'], 'revision': record['revision'], 'start': {'offset': offset},
             'next_read': {'offset': end} if more else None, 'total_chars': len(text), 'more': more,
             'reading_pointer': record['document'], 'text_range': [offset, end], 'text': text[offset:end]}
    write(target, value)
    return {k: v for k, v in {**value, 'path': str(target)}.items() if k != 'text'}
