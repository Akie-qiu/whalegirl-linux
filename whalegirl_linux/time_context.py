"""A small server clock input, refreshed on each model request."""
from datetime import datetime, timedelta, timezone
import time


BEIJING = timezone(timedelta(hours=8))
CLOCK_RULE = '\n时间以本轮服务器时间为准；各条消息标注的是原消息时间，不能当成现在。当前时间无需联网或猜测。'
CLOCK_MARKER = '\n【服务器当前时间】'
REPLY_MARKER = '\n【本次需要回复的消息】\n'
REPLY_MARKERS = (REPLY_MARKER, '\n【当前需要回复的消息｜本轮主题优先】\n', '\n【本轮新发生的交流】\n')


def before_reply(text, block):
    """Support both saved context headings; preserve all original text."""
    for marker in REPLY_MARKERS:
        if marker in text:
            before, after = text.split(marker, 1)
            return before + block + marker + after
    return text + block


def user_context_block(messages, block):
    """Move changing request data out of system, even without a known heading."""
    result=list(messages)
    for index,message in enumerate(messages):
        if message.get('role')!='user':continue
        content=message.get('content')
        if isinstance(content,str):
            content=before_reply(content,block)
        elif isinstance(content,list):
            content=list(content)
            for i,item in enumerate(content):
                if isinstance(item,dict) and item.get('type')=='text':
                    content[i]={**item,'text':before_reply(str(item.get('text') or ''),block)}
                    break
            else:content.insert(0,{'type':'text','text':block})
        else:continue
        result[index]={**message,'content':content}
        return result,True
    return result,False


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
            return before_reply(text,clock)

        if isinstance(content, str):
            content = insert(content)
        elif isinstance(content, list):
            content = [dict(block) for block in content]
            if any(block.get('type')=='image_url' for block in content):
                # A tool follow-up can reuse the exact same image. Its changing
                # clock must come after image input, not invalidate image tokens.
                content.append({'type':'text','text':clock})
            else:
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
