"""A small server clock input, refreshed on each model request."""
from datetime import datetime, timedelta, timezone
import time


BEIJING = timezone(timedelta(hours=8))
CLOCK_RULE = '\n时间以本轮服务器时间为准；各条消息标注的是原消息时间，不能当成现在。当前时间无需联网或猜测。'
CLOCK_MARKER = '\n【服务器当前时间】'
REPLY_MARKER = '\n【本次需要回复的消息】\n'


def server_time(stamp=None):
    now = datetime.fromtimestamp(time.time() if stamp is None else stamp, BEIJING)
    return now.strftime('%Y-%m-%d %H:%M:%S') + ' 周' + '一二三四五六日'[now.weekday()] + ' 北京时间'


def clock_messages(messages):
    """Copy only the first user text; keep images and tool history unchanged."""
    result = list(messages)
    clock = CLOCK_MARKER + server_time() + '\n'
    for index, message in enumerate(messages):
        if message.get('role') != 'user':
            continue
        content = message.get('content')

        def insert(text):
            # Normal chat puts the live clock immediately before the new message.
            if REPLY_MARKER in text:
                before, after = text.split(REPLY_MARKER, 1)
                return before + clock + REPLY_MARKER + after
            return text + clock

        if isinstance(content, str):
            content = insert(content)
        elif isinstance(content, list):
            content = [dict(block) for block in content]
            for block in content:
                if block.get('type') == 'text':
                    block['text'] = insert(str(block.get('text') or ''))
                    break
            else:
                content.insert(0, {'type': 'text', 'text': clock})
        else:
            continue
        result[index] = {**message, 'content': content}
        break
    return result
