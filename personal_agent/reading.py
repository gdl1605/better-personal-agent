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
        if not isinstance(value, dict) or value.get('kind') != 'selected_reading_copy':
            continue
        messages = value['messages']
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
            excerpts.append({**message, 'text': body[offset:end], 'reading_pointer': f'{original}#/messages/{index}', 'text_range': [offset, end]})
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
    return {'path': str(ws.context / record['document']), 'next_read': resume, 'more': None}
