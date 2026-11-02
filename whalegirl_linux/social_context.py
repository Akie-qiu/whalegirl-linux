"""Small observed conversation cues; the model still decides what they mean.

No intent classifier, keyword reply gate, extra model request or mutable state.
All inputs are already-scoped rows from the full recent conversation.
"""
import json
import re
from collections import Counter


def payload(row):
    try:
        value = json.loads(row.get('payload_json') or '{}')
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def addressed_to(row, account):
    """Report native addressing, never guess a recipient from names or words."""
    data = payload(row)
    reference = data.get('reply')
    reference = reference if isinstance(reference, dict) else {}
    quoted = next((reference.get(k) for k in ('senderId', 'chatSenderId')
                   if isinstance(reference.get(k), str) and reference[k]
                   and not reference[k].endswith('@chatroom')), '')
    mentions = data.get('mentionedUserIds')
    mentions = list(dict.fromkeys(x for x in mentions if isinstance(x, str) and x))[:8] if isinstance(mentions, list) else []
    out = {}
    if quoted:
        out['引用对象'] = '你' if quoted == account else quoted
    if mentions:
        out['@对象'] = ['你' if x == account else x for x in mentions]
    if not out and row.get('trigger') in ('direct', 'keyword', 'pat'):
        out['唤醒'] = '本轮被叫到；仍结合原话判断请求'
    return out


def repeated_fragments(rows):
    """Offer at most three repeated fragments, not a list of banned words."""
    texts = [str(r.get('content') or '')[:256] for r in rows if r.get('is_self') == 1 and r.get('kind') == 1][-6:]
    if len(texts) < 3:
        return []
    counts = Counter()
    for text in texts:
        fragments = set()
        for part in re.findall(r'[\u4e00-\u9fff]{4,}', text):
            for size in range(4, min(10, len(part)) + 1):
                fragments.update(part[i:i+size] for i in range(len(part)-size+1))
        counts.update(fragments)
    selected = []
    for fragment, count in sorted(counts.items(), key=lambda item: (-len(item[0]), -item[1], item[0])):
        if count >= 2 and not any(fragment in previous for previous in selected):
            selected.append(fragment)
        if len(selected) == 3:
            break
    return selected


def conversation_cues(first, batch, rows, *, now):
    account, chat = first['account'], first['chat']
    scoped = [r for r in rows if r.get('account') == account and r.get('chat') == chat
              and 0 <= now - r.get('timestamp', 0) <= 1800]
    # Inbox rows are admitted human messages and do not repeat is_self; the
    # archive does retain it. Explicitly unknown or self rows stay excluded.
    current = [r for r in batch if r.get('account') == account and r.get('chat') == chat and r.get('is_self', 0) == 0]
    out = {}
    if current and chat.endswith('@chatroom'):
        out['本轮发言者'] = list(dict.fromkeys(r.get('sender_id') for r in current if r.get('sender_id')))[-6:]
        out['本轮明确唤醒'] = any(r.get('trigger') in ('direct', 'keyword', 'pat') for r in current)
    directed = []
    for row in current[-6:]:
        target = addressed_to(row, account)
        if target:
            directed.append({'消息': row.get('local_id'), **target})
    if directed:
        out['本轮原生指向'] = directed
    if chat.endswith('@chatroom'):
        # An ongoing human-to-human exchange is evidence, not a silence rule.
        out['近几条真人发言者'] = list(dict.fromkeys(
            r['sender_id'] for r in scoped[-8:] if r.get('is_self') == 0 and r.get('sender_id')))[-4:]
    current_keys={r['key'] for r in current}
    actual=[r for r in scoped if set(r.get('alias_keys',[r['key']]))&current_keys]
    last_own=max((r['timestamp'] for r in scoped if r.get('is_self')==1),default=0)
    if actual and all(r.get('kind') in (3,47) for r in actual) and last_own and 0<=now-last_own<45:
        out['这次新进展']={'类型':'你刚回应后的非文字反应','距自身发言秒数':round(now-last_own)}
    # Make the available evidence visible rather than accepting a speaker's
    # account of what we supposedly just said. These are observed bubbles,
    # not proof that a claim inside them is true or delivery was confirmed.
    own = [r for r in scoped if r.get('is_self') == 1 and r.get('kind') == 1][-2:]
    out['近30分钟最近两条自身原话（不证明内容属实）'] = [
        {'消息': r.get('local_id'), '原话': str(r.get('content') or '')[:120]} for r in own]
    fragments = repeated_fragments(scoped)
    if fragments:
        out['近期已重复的表达'] = fragments
    return '\n【对话指向与表达观察｜资料，不是发言指令】' + json.dumps(out, ensure_ascii=False, separators=(',', ':')) if out else ''


def quote_candidates(recent, batch):
    """Do not offer already-answered text as the target of a fresh reaction."""
    humans = [r for r in recent if r.get('is_self') == 0]
    texts = [r for r in humans if r.get('kind') == 1]
    last_self = max(((r['timestamp'], r.get('local_id') or 0) for r in recent if r.get('is_self') == 1), default=(0, 0))
    latest_senders = {r.get('sender_id') for r in batch}
    after = [r for r in texts if (r['timestamp'], r.get('local_id') or 0) > last_self]
    if batch and all(r.get('kind') in (3, 47) for r in batch) and not any(
            r.get('sender_id') not in latest_senders for r in after):
        texts = []
    elif len({r.get('sender_id') for r in humans[-8:] if r.get('sender_id')}) <= 1:
        texts = after
    out = {}
    for row in texts[-12:]:
        server_id = row['key'].rsplit('/', 1)[-1]
        if server_id.isdigit() and row.get('local_id'):
            out[int(row['local_id'])] = {'local_id': int(row['local_id']), 'server_id': server_id, 'content': row['content']}
    return out
