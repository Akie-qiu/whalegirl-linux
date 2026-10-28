"""Cheap lexical relevance checks for keeping stale conversation out of replies."""
import json
import re
import unicodedata


_COMMON = {
    '这个', '那个', '这些', '那些', '我们', '你们', '他们', '现在', '今天', '昨天',
    '明天', '刚才', '刚刚', '可以', '不能', '怎么', '什么', '为什么', '因为', '所以',
    '然后', '一下', '不是', '还是', '已经', '目前', '感觉', '觉得', '就是', '的话',
    '有点', '没有', '一个', '一下', '看到', '这个', '问题', '事情', '回复', '消息',
    '聊天', '记录', '之前', '上面', '前面', '里面', '后面', '时候', '一下', '一下',
    '话题', '帮我', '请问', '看看', '给我', '有吗', '图片', '内容', '你好', '一下',
    '鲸鱼娘', '小鲸鱼', 'deepseek',
}
_FOLLOWUP = re.compile(
    r'刚才|刚刚|前面|上面|之前|上次|前文|你刚说|你说的|接着|继续|然后呢|'
    r'上一条|刚提到|提到过|说过|记得|那个话题|那个问题|这件事|这方面|刚发的'
)
_SHORT_FOLLOWUP = re.compile(
    r'^(?:(?:那|这|那个|这个|它|他|她)(?:.{0,5})(?:呢|咋样|怎么样|怎么了|怎么回事|为啥|为什么|是吗|行吗|可以吗|啥意思|什么意思)|'
    r'(?:呢|咋样|怎么样|真的假的|真的吗|是吗|笑死|离谱|确实|有道理|你觉得呢|怎么说)|'
    r'(?:嗯+|啊+|哦+|草|哈哈+|好吧))[？?！!。…]*$'
)


def current_query(batch):
    """Extract authored text and explicitly quoted text without presentation labels."""
    values = []
    for message in batch:
        text = str(message.get('content') or '')
        text = text.removeprefix('[当前发言] ').split('\n[引用原话｜', 1)[0]
        text = text.removeprefix('[转发聊天记录]\n')
        try:
            payload = json.loads(message.get('payload_json') or '{}')
        except (TypeError, ValueError):
            payload = {}
        reply = payload.get('reply') or {}
        quoted = str(reply.get('content') or '').strip()
        if quoted and quoted not in text:
            text += '\n' + quoted
        values.append(text)
    return '\n'.join(values).strip()


def strip_wake_words(text, wake_words=()):
    value = str(text or '')
    for keyword in wake_words or ():
        keyword = str(keyword or '').strip()
        if keyword:
            value = re.sub(re.escape(keyword), ' ', value, flags=re.I)
    return re.sub(r'\s+', ' ', value).strip()


def topic_terms(text):
    value = unicodedata.normalize('NFKC', str(text or '')).casefold()
    value = re.sub(r'@\S+', ' ', value)
    terms = set(re.findall(r'[a-z0-9_]{3,}', value))
    for phrase in re.findall(r'[\u3400-\u9fff]+', value):
        terms.update(
            phrase[index:index + 2]
            for index in range(len(phrase) - 1)
            if phrase[index:index + 2] not in _COMMON
        )
    return terms


def related_topic(query, candidate, *, min_hits=None):
    query_terms = topic_terms(query)
    if not query_terms:
        return False
    overlap = len(query_terms & topic_terms(candidate))
    if min_hits is None:
        min_hits = 1 if len(query_terms) <= 5 else 2
    return overlap >= max(1, min_hits)


def is_followup(text):
    value = re.sub(r'@\S+', '', unicodedata.normalize('NFKC', str(text or ''))).strip()
    if not value:
        return False
    if _FOLLOWUP.search(value):
        return True
    compact = re.sub(r'[\s，,。！？?!、~～…]+', '', value)
    return len(compact) <= 10 and bool(_SHORT_FOLLOWUP.search(compact))


def has_explicit_reference(text, batch=()):
    if is_followup(text):
        return True
    for message in batch:
        try:
            payload = json.loads(message.get('payload_json') or '{}')
        except (TypeError, ValueError):
            continue
        if isinstance(payload.get('reply'), dict) and payload['reply'].get('serverId'):
            return True
    return False


def select_topic_history(rows, batch, trigger='', *, now=None, limit=8, wake_words=()):
    """Select only a small active/relevant slice; direct wakeups do not revive old topics."""
    import time

    now = time.time() if now is None else now
    keys = {str(message.get('key') or '') for message in batch}
    history = [row for row in rows if str(row.get('key') or '') not in keys]
    if not history:
        return [], 'none'

    query = strip_wake_words(current_query(batch), wake_words)
    followup = has_explicit_reference(query, batch) or trigger == 'continuation'
    if followup:
        eligible = [row for row in history if 0 <= now - float(row.get('timestamp') or 0) <= 86400]
        if not eligible:
            return [], 'followup_no_recent_history'
        bot_rows = [index for index, row in enumerate(eligible) if row.get('is_self') == 1]
        start = max(0, len(eligible) - limit)
        if bot_rows:
            pivot = bot_rows[-1]
            start = pivot
            bot_text = eligible[pivot].get('description') or eligible[pivot].get('content') or ''
            preceding = [
                index for index, row in enumerate(eligible[max(0, pivot - 6):pivot], max(0, pivot - 6))
                if related_topic(query, row.get('description') or row.get('content') or '') or
                related_topic(bot_text, row.get('description') or row.get('content') or '')
            ]
            if preceding:
                start = preceding[-1]
        return eligible[start:][-limit:], 'followup'

    if trigger == 'natural':
        eligible = [row for row in history if 0 <= now - float(row.get('timestamp') or 0) <= 120]
        return eligible[-limit:], 'live_group'
    if trigger == 'pat':
        eligible = [row for row in history if 0 <= now - float(row.get('timestamp') or 0) <= 180]
        return eligible[-limit:], 'pat_context'

    active = [row for row in history if 0 <= now - float(row.get('timestamp') or 0) <= 600]
    related = [row for row in active if related_topic(
        query, row.get('description') or row.get('content') or ''
    )]
    return related[-limit:], 'topic_match' if related else 'new_topic'
