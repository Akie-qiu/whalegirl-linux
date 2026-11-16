"""Fast local participation, bounded message plans and chat style guidance."""
from datetime import datetime, timedelta, timezone
import json
import random
import re
import time
import unicodedata
from .search import strip_search_citations
from .stickers import sticker_emotional_signal


def canonical(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text)).casefold())


def clean_text(text, max_len=2000):
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    text = re.sub(r'(?im)(?<!\S)\[(?:原生表情包|原生表情)\](?!\S)', '', text)
    text = re.sub(r'(?im)^\s*\[表情包\]\s*$', '', text)
    text = re.sub(r'(?m)^\s*(?:#{1,6}\s+|[-*]\s+|\d+[.、)]\s*)', '', text)
    text = text.replace('**', '').replace('__', '')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return re.sub(r'。(?=\s|$)', '', text).strip()[:max_len]


def _split_conversational_clauses(part):
    """Split a conversational paragraph (>18 chars) into natural WeChat short bubbles."""
    if len(part) <= 28 or len(part) > 180 or re.search(r'https?://|`|^\s*\d+[.、)]|第[一二三四五六七八九十\d]+[步条点项]', part):
        return [part]
    raw_segments = []
    for raw_line in re.split(r'\n+|——+|--+|；+', part):
        raw_line = raw_line.strip(' ，,。；;')
        if not raw_line:
            continue
        for sent in re.split(r'(?<=[。！？…])\s*', raw_line):
            sent = re.sub(r'。(?=\s|$)', '', sent).strip(' ，,；;')
            if not sent:
                continue
            if raw_segments and re.fullmatch(r'[…~～！!？?]+', sent):
                raw_segments[-1] += sent
            elif raw_segments and len(sent) <= 3 and len(raw_segments[-1]) + 1 + len(sent) <= 16:
                raw_segments[-1] += '，' + sent
            else:
                raw_segments.append(sent)
    out = []
    for seg in raw_segments:
        if len(seg) <= 28 or ('，' not in seg and ',' not in seg):
            out.append(seg)
            continue
        clauses = [c.strip() for c in re.split(r'[，,]+', seg) if c.strip()]
        merged = []
        for c in clauses:
            if merged and (len(merged[-1]) < 5 or (len(c) < 5 and len(merged[-1]) + 1 + len(c) <= 18)):
                merged[-1] = merged[-1] + '，' + c
            else:
                merged.append(c)
        if len(merged) >= 2 and len(merged[0]) < 5 and len(merged[0]) + 1 + len(merged[1]) <= 20:
            merged[1] = merged[0] + '，' + merged[1]
            merged.pop(0)
        out.extend(merged)
    return [s for s in out if s]


def split_text_actions(value):
    """Turn paragraph breaks and multi-clause sentences into separate short chat bubbles."""
    value = clean_text(value, max_len=2000)
    if not value:
        return []
    raw_parts = [part.strip() for part in re.split(r'\n\s*\n+', value) if part.strip()]
    parts = []
    for part in raw_parts:
        if len(part) <= 28:
            parts.append(part)
        elif len(part) <= 180 and not re.search(r'https?://|`|^\s*\d+[.、)]|第[一二三四五六七八九十\d]+[步条点项]', part):
            parts.extend(_split_conversational_clauses(part))
        elif len(part) <= 900:
            parts.append(part)
        else:
            # 超过 900 字的长段落，按自然句子边界切分，避免被硬截断丢后文
            sub_chunks = []
            current = ''
            sentences = re.split(r'(?<=[。！？\n])', part)
            for s in sentences:
                if not s:
                    continue
                if len(current) + len(s) <= 900:
                    current += s
                else:
                    if current:
                        sub_chunks.append(current.strip())
                    if len(s) <= 900:
                        current = s
                    else:
                        for i in range(0, len(s), 900):
                            sub_chunks.append(s[i:i+900].strip())
                        current = ''
            if current:
                sub_chunks.append(current.strip())
            parts.extend([c for c in sub_chunks if c])
    return parts


POSITIVE_AFFECTION_TEXT_RE = re.compile(
    r'想妈妈|一直在想|想你|在呢|抱抱|贴贴|喜欢你|最喜欢|爱你|亲亲|乖乖|等你|陪你|好呀|好哒|太好了|开心'
)
EXPLICIT_REFUSAL_TEXT_RE = re.compile(
    r'不要|不行|不给|拒绝|滚|走开|不理|才不|休想|没门|算了|讨厌|不干|绝不'
)
NEGATIVE_STICKER_TAG_RE = re.compile(
    r'否定|拒绝|直接拒绝|嫌弃|冷漠|鄙视|攻击|恶心|嘲讽|阴阳怪气|不耐烦|敌意|愤怒'
)
SHY_STICKER_QUALIFIER_RE = re.compile(
    r'害羞|撒娇|脸红|亲昵|喜欢|依恋|委屈|开心|期待'
)
ROLEPLAY_PROP_RE = re.compile(r'竖耳朵|竖着耳朵|耳朵.{0,3}竖|晃尾巴|尾巴.{0,3}晃|摇尾巴|整条鱼|软趴趴')
OPENER_CALLOUT_RE = re.compile(
    r'^(?:沃不明白|小鲸鱼|鲸鱼|鱼鱼|小鱼|大肥鱼|笨鱼|傻鱼|deepseek(?:酱)?|'
    r'老婆|妈妈|宝|宝宝|宝贝|亲爱的|喂|哎|诶|嘿|哈喽|嗨|你好|在吗|在不在|在嘛|在么|'
    r'人呢|出来|呼叫|干嘛呢|在干嘛|忙吗|睡了吗|醒了吗|那个|话说|对了|问一下|有个事)'
    r'[呀啊呢吗嘛哦喔啦呗~～!！?？。，,、\s]*$',
    re.I,
)


def sticker_conflicts_with_text(sticker_meaning, text_content):
    """Legacy descriptive hint; never veto a model-selected sticker by keywords."""
    text = str(text_content or '').strip()
    meaning = str(sticker_meaning or '').strip()
    if not text or not meaning:
        return False
    if (POSITIVE_AFFECTION_TEXT_RE.search(text)
            and not EXPLICIT_REFUSAL_TEXT_RE.search(text)
            and NEGATIVE_STICKER_TAG_RE.search(meaning)
            and not SHY_STICKER_QUALIFIER_RE.search(meaning)):
        return True
    return False


def is_unfinished_opener_batch(batch, settings=None):
    """Detect short greeting/callout/sticker-only openers where user is likely typing the real follow-up."""
    if not batch or len(batch) > 2:
        return False
    keywords = tuple(str(k).strip() for k in (getattr(settings, 'keywords', None) or ()) if str(k).strip())
    for item in batch:
        kind = int(item.get('kind') or 1)
        if kind == 47:
            continue
        if kind != 1:
            return False
        raw = str(item.get('content') or '').strip()
        if re.fullmatch(r'\[(?:表情包|动画表情|原生表情|原生表情包)[^\]]*\]', raw):
            continue
        cleaned = re.sub(r'^@[^\s\u2005]+[\s\u2005]*', '', raw).strip()
        for kw in keywords:
            if cleaned.casefold().startswith(kw.casefold()):
                rest = cleaned[len(kw):].strip(' ，,。！!？?~～呀啊呢吗嘛哦喔啦')
                if not rest:
                    cleaned = kw
                    break
                if OPENER_CALLOUT_RE.fullmatch(rest):
                    cleaned = rest
                    break
        compact = re.sub(r'[\s，,。！!？?~～…、]+', '', cleaned)
        if not compact or len(compact) > 10:
            return False
        if compact.casefold() in {k.casefold() for k in keywords}:
            continue
        if not OPENER_CALLOUT_RE.fullmatch(cleaned):
            return False
    return True


_LEADIN_WAIT_RE = re.compile(
    r'(?:我?先说|等我|等一?下|等一?会儿?|等一等|马上发|等我找|有个事|跟你说|听我说|第一个|第一条|先讲|想看的类型|还没说完|还有个|下一条)'
)


def is_followup_wait_candidate(first, batch, settings=None):
    """Tighten wait_for_next_message eligibility to genuine unfinished openers or lead-ins."""
    if not first or first.get('trigger') == 'pat' or not batch:
        return False
    texts = []
    for item in batch:
        if int(item.get('kind') or 1) != 1:
            continue
        raw = str(item.get('content') or '').strip()
        if not raw or re.fullmatch(r'\[(?:表情包|动画表情|原生表情|原生表情包)[^\]]*\]', raw):
            continue
        cleaned = re.sub(r'^@[^\s\u2005]+[\s\u2005]*', '', raw).strip()
        if cleaned:
            texts.append(cleaned)
    if not texts:
        return False
    if is_unfinished_opener_batch(batch, settings):
        return True
    last = texts[-1]
    if last.endswith(('...', '…', ':', '：', '、', '，', ',')):
        return True
    for txt in texts:
        compact = re.sub(r'[\s，,。！!？?~～…、]+', '', txt)
        if len(compact) <= 20 and _LEADIN_WAIT_RE.search(txt) and not txt.rstrip(' ~～').endswith(('？', '?')):
            return True
    return False


def dedupe_recent_self_actions(actions, recent_self_texts=()):
    """Strip redundant greeting/prop clauses that repeat self messages from the last 45s or earlier bubbles."""
    if not actions:
        return actions
    recent_clean = [canonical(t) for t in (recent_self_texts or ()) if str(t or '').strip()]
    recent_joined = ' '.join(str(t or '') for t in (recent_self_texts or ()))
    recent_has_zai = bool(re.search(r'在呢|在呀|我在', recent_joined))
    recent_has_prop = bool(ROLEPLAY_PROP_RE.search(recent_joined))
    text_indices = [i for i, a in enumerate(actions) if a.get('type') == 'text']
    if len(text_indices) <= 1 and not recent_clean:
        return actions
    seen_canonical = set()
    kept = []
    dropped_texts = []
    for action in actions:
        if action.get('type') != 'text':
            kept.append(action)
            continue
        txt = str(action.get('text') or '').strip()
        norm = canonical(txt)
        if not norm or norm in seen_canonical:
            dropped_texts.append(action)
            continue
        # A substring may add a negation, condition or correction. Only an
        # identical recent statement is safe to suppress as a repeat.
        is_dup_recent = norm in recent_clean
        bare = txt.strip(' ，,。！!？?~～…')
        is_dup_greeting_prop = (
            (recent_has_zai and bool(re.fullmatch(r'(?:(?:我)?在(?:呢|呀)?)+', bare)))
            or (recent_has_prop and bool(ROLEPLAY_PROP_RE.search(bare)))
        )
        if (is_dup_recent or is_dup_greeting_prop) and sum(1 for a in actions if a.get('type') == 'text') > len(dropped_texts) + 1:
            dropped_texts.append(action)
            continue
        seen_canonical.add(norm)
        kept.append(action)
    if not any(a.get('type') == 'text' for a in kept) and dropped_texts:
        kept.insert(0, dropped_texts[0])
    return kept


def cap_text_actions(actions, max_text_parts=3):
    """Merge shortest adjacent text actions so search/explanation turns don't become 6-bubble telegraphs."""
    out = [dict(a) for a in (actions or [])]
    def targets(action):
        value = action.get('mentions', [])
        return value if isinstance(value, list) else []

    def merged_targets(left, right):
        combined = []
        for target in targets(left) + targets(right):
            if target not in combined:
                combined.append(target)
        return combined

    while sum(1 for a in out if a.get('type') == 'text') > max_text_parts:
        pair_idx = min(
            (i for i in range(len(out) - 1)
             if out[i].get('type') == 'text' and out[i + 1].get('type') == 'text'
             and (out[i].get('quote_local_id') or 0) == (out[i + 1].get('quote_local_id') or 0)
             # apply_mentions supports three native recipients per bubble.
             and len(merged_targets(out[i], out[i + 1])) <= 3),
            key=lambda i: len(out[i].get('text', '')) + len(out[i + 1].get('text', '')),
            default=None,
        )
        if pair_idx is None:
            break
        sep = '' if out[pair_idx]['text'].endswith(('…', '！', '？', '~', '～', '。')) else '，'
        merged_text = (out[pair_idx]['text'] + sep + out[pair_idx + 1]['text'])[:2000]
        if 'mentions' in out[pair_idx] or 'mentions' in out[pair_idx + 1]:
            out[pair_idx]['mentions'] = merged_targets(out[pair_idx], out[pair_idx + 1])
        out[pair_idx]['text'] = merged_text
        out[pair_idx]['display'] = merged_text
        out.pop(pair_idx + 1)
    return out


def plan_actions(decision, choices, settings, recent_self_texts=()):
    """New plans plus compatibility with the existing single-reply format."""
    supplied = decision.get('actions')
    usable = isinstance(supplied, list) and any(
        isinstance(item, dict) and (
            item.get('type') == 'text' and isinstance(item.get('text'), str) and item['text'].strip()
            or item.get('type') == 'sticker' and isinstance(item.get('id'), str) and item['id'] in choices
        ) for item in supplied)
    if supplied == []:
        return []  # A deliberate empty plan must not revive a legacy reply.
    if supplied is None or not usable:
        legacy_sticker = decision.get('sticker')
        supplied = ([{'type':'sticker','id':legacy_sticker}] if isinstance(legacy_sticker,str) and legacy_sticker in choices else
                    [{'type':'text','text':decision.get('reply',''),'quote_local_id':decision.get('quote_local_id',0)}])
    if not isinstance(supplied, list):
        raise ValueError('actions必须是数组')
    actions = []
    marker_sticker = None
    max_parts = settings.reply_max_parts if settings.chat_habits_enabled else 1
    for item in supplied[:6]:
        if not isinstance(item, dict):
            continue
        if item.get('type') == 'sticker':
            sticker = item.get('id')
            if (isinstance(sticker,str) and sticker in choices
                    and sum(a['type']=='sticker' for a in actions)<settings.sticker_max_per_reply
                    and not any(a['type']=='sticker' and a['id']==sticker for a in actions)):
                actions.append({'type':'sticker','id':sticker,'display':'[表情包] '+choices[sticker]})
        elif item.get('type') == 'text':
            value = item.get('text')
            if not isinstance(value,str):
                continue
            if re.search(r'(?im)(?<!\S)\[(?:原生表情包|原生表情)\](?!\S)', value):
                candidate = decision.get('sticker_suggestion') or decision.get('sticker')
                if isinstance(candidate, str) and candidate in choices:
                    marker_sticker = candidate
            value=strip_search_citations(value)
            chunks = split_text_actions(value) if settings.chat_habits_enabled else [
                clean_text(value, max_len=2000) if re.search(r'(?im)(?<!\S)\[(?:原生表情包|原生表情|表情包)\](?!\S)', value)
                else value.strip()[:2000]
            ]
            for chunk_index, chunk in enumerate(chunks):
                quote = item.get('quote_local_id',decision.get('quote_local_id',0)) if chunk_index == 0 else 0
                action={'type':'text','text':chunk,'display':chunk,
                        'quote_local_id':int(quote) if str(quote).isdigit() else 0}
                if chunk_index == 0 and isinstance(item.get('mentions'),list):
                    action['mentions']=item['mentions'][:6]
                actions.append(action)
    actions = dedupe_recent_self_actions(actions, recent_self_texts)
    joined_text = ' '.join(a.get('text', '') for a in actions if a.get('type') == 'text').strip()
    if joined_text and getattr(settings, 'account_id', '') and not getattr(settings, 'autonomy_enabled', False):
        actions = [
            a for a in actions
            if a.get('type') != 'sticker' or not sticker_conflicts_with_text(choices.get(a.get('id'), ''), joined_text)
        ]
    while len(actions) > max_parts:
        pair_idx = min(
            (i for i in range(len(actions) - 1) if actions[i]['type'] == 'text' and actions[i + 1]['type'] == 'text'),
            key=lambda i: len(actions[i]['text']) + len(actions[i + 1]['text']),
            default=None,
        )
        if pair_idx is None:
            actions = actions[:max_parts]
            break
        sep = '' if actions[pair_idx]['text'].endswith(('…', '！', '？', '~', '～')) else '，'
        merged_text = (actions[pair_idx]['text'] + sep + actions[pair_idx + 1]['text'])[:2000]
        actions[pair_idx]['text'] = merged_text
        actions[pair_idx]['display'] = merged_text
        actions.pop(pair_idx + 1)
    if (marker_sticker and marker_sticker in choices
            and not any(a['type']=='sticker' and a['id']==marker_sticker for a in actions)
            and sum(a['type']=='sticker' for a in actions)<settings.sticker_max_per_reply
            and len(actions) < max_parts):
        actions.append({'type':'sticker','id':marker_sticker,'display':'[表情包] '+choices[marker_sticker]})
    return actions


def pat_target_options(recent_rows, batch=(), *, account_id='', excluded=()):
    """Build ordered pat target choices from recent incoming bubble senders (newest first)."""
    blocked = {str(v or '').strip() for v in (excluded or ()) if str(v or '').strip()}
    if account_id:
        blocked.add(str(account_id).strip())
    seen = {}
    rows = list(batch or ()) + list(recent_rows or ())
    ordered_rows = sorted(
        rows,
        key=lambda r: (float(r.get('timestamp') or 0), int(r.get('local_id') or 0)),
        reverse=True,
    )
    for pass_types in ((1, 3, 47, 49), None):
        for row in ordered_rows:
            if row.get('is_self') == 1 or row.get('isSelf') is True:
                continue
            if pass_types is not None and int(row.get('kind') or row.get('type') or 1) not in pass_types:
                continue
            sid = str(row.get('sender_id') or ('' if 'sender_id' in row else row.get('sender')) or '').strip()
            if not sid or sid in blocked or sid in seen or sid.endswith('@chatroom'):
                continue
            raw_name = row.get('senderName') or (row.get('sender') if row.get('sender') != sid else '') or sid
            name = str(raw_name).strip()[:80]
            if not name or name in ('系统消息', '自主聊天调度'):
                continue
            seen[sid] = name
            if len(seen) >= 8:
                break
        if len(seen) >= 8:
            break
    return {'p' + str(i + 1): {'id': sid, 'name': name} for i, (sid, name) in enumerate(seen.items())}


def plan_pat_targets(decision, valid_targets, settings=None, *, recent_targets=()):
    """Resolve at most one valid non-cooldown pat target from model decision."""
    if not isinstance(decision, dict) or not valid_targets:
        return []
    raw = decision.get('pat_targets')
    if not isinstance(raw, list) or not raw:
        return []
    cooldown_set = {str(v).strip() for v in (recent_targets or ()) if str(v).strip()}
    by_id = {}
    by_name = {}
    for token, info in valid_targets.items():
        if isinstance(info, dict):
            tid = str(info.get('id') or '').strip()
            tname = str(info.get('name') or tid).strip()
        else:
            tid = str(token).strip()
            tname = str(info or tid).strip()
        if tid:
            by_id[tid] = (tid, tname)
            by_id.setdefault(str(token), (tid, tname))
            by_name.setdefault(tname, []).append((tid, tname))
    for item in raw:
        key = str(item.get('id') if isinstance(item, dict) else item or '').strip()
        if not key:
            continue
        resolved = by_id.get(key)
        if resolved is None and len(by_name.get(key, ())) == 1:
            resolved = by_name[key][0]
        if resolved is None:
            continue
        tid, tname = resolved
        if tid in cooldown_set:
            continue
        return [{'type': 'pat', 'target_id': tid, 'target_name': tname, 'display': '[拍一拍] ' + tname}]
    return []


def participation(settings, store, first, batch, *, now=None):
    now = time.time() if now is None else now
    local = datetime.fromtimestamp(now, timezone(timedelta(hours=8)))
    start, end = settings.quiet_start_hour, settings.quiet_end_hour
    quiet = start != end and (start <= local.hour < end if start < end else local.hour >= start or local.hour < end)
    rows = store.conversation(first['account'],first['chat'],30)
    own = store.db.execute("SELECT count(*) FROM inbox WHERE account=? AND chat=? AND state='sent' AND updated>?",
                           (first['account'],first['chat'],now-120)).fetchone()[0]
    last = store.latest_sent(first['account'],first['chat'])
    text = '\n'.join(m['content'] for m in batch)
    interest = any(canonical(k) in canonical(text) for k in settings.interest_keywords)
    engaged = bool(last and now-last<120)
    followup = engaged and bool(re.search(r'你|接着|然后呢|继续|为啥|为什么|怎么|？|\?',text))
    latest = [r for r in rows if now-r['timestamp']<25 and r['is_self']==0]
    hot = len(latest)>=6 and len({r['sender_id'] for r in latest})>=3
    natural = all(m.get('trigger')=='natural' for m in batch)
    threshold = {'quiet':3,'balanced':2,'active':1}[settings.participation]
    score = (2 if engaged else 0) + (2 if interest else 0) + (1 if hot else 0) + (1 if '?' in text or '？' in text else 0)
    cap = {'quiet':1,'balanced':2,'active':3}[settings.participation]
    stripped = text.strip()
    sender_id = first.get('sender_id') or ''
    batch_keys = {m.get('key') for m in batch if m.get('key')}
    prev_same_sender_at = False
    if natural and sender_id and first.get('chat', '').endswith('@chatroom'):
        for r in reversed(rows):
            if r.get('key') in batch_keys:
                continue
            if r.get('is_self') == 0 and r.get('sender_id') == sender_id:
                age = now - float(r.get('timestamp') or 0)
                prev_txt = str(r.get('content') or '').strip()
                if 0 <= age <= 20 and re.match(r'^@\S+\s*$', prev_txt):
                    prev_same_sender_at = True
                break
    explicit_peer_at = bool(natural and re.search(r'(?<![A-Za-z0-9._%+-])@\S+', stripped))
    peer_quote = False
    if natural and first.get('chat', '').endswith('@chatroom'):
        self_names = {first.get('account', ''), '沃不明白', '小鲸鱼', '大肥鱼', *getattr(settings, 'keywords', ())}
        if not any(k and k in stripped for k in self_names):
            for m in batch:
                try:
                    payload = json.loads(m.get('payload_json') or '{}')
                except (TypeError, ValueError):
                    payload = {}
                ref = payload.get('reply') or {}
                ref_sid = str(ref.get('serverId') or '').strip()
                if ref_sid:
                    source = store.find_message(first['account'], first['chat'], ref_sid) if hasattr(store, 'find_message') else None
                    if source is None:
                        source = next((r for r in rows if str(r.get('key') or '').endswith('/' + ref_sid)), None)
                    if source is not None and int(source.get('is_self') or 0) == 0 and source.get('sender_id') != first['account']:
                        peer_quote = True
                        break
    recent_self_90s = sum(
        1 for r in rows
        if r.get('is_self') == 1 and 0 <= now - float(r.get('timestamp') or 0) <= 90
    )
    is_excluded_bot = bool(sender_id and sender_id in getattr(settings, 'fishtalk_excluded_sender_ids', ()))
    pingpong_exhausted = bool(
        natural and first.get('chat', '').endswith('@chatroom') and not interest
        and (
            (is_excluded_bot and (recent_self_90s >= 1 or ('?' not in stripped and '？' not in stripped)))
            or (recent_self_90s >= 3 and '?' not in stripped and '？' not in stripped)
        )
    )
    peer_directed = explicit_peer_at or prev_same_sender_at or peer_quote or (not interest and not engaged and bool(
        re.search(r'^[\u4e00-\u9fa5A-Za-z0-9_]{1,6}[，,：:\s]*你(?:先|去|把|来|看|发|说|怎么|在干嘛|在哪)', stripped)
    ))
    trivial_unengaged = score == 0 and (
        len(stripped) < 6
        or bool(re.match(r'^(?:好的+|收到|知道了|嗯+|哦+|行吧|好吧|哈哈哈+|先溜了|下楼了|撤了)[！!。.~～]*$', stripped))
    )
    # While a burst is underway let the model judge relevance; no extra model call.
    allow = not natural or not settings.chat_habits_enabled or ((own < cap or (followup and own < cap+3)) and (not quiet or engaged) and
        not peer_directed and not pingpong_exhausted and (score>=threshold or (own==0 and not quiet and len(stripped)>=3 and not trivial_unengaged)))
    if getattr(settings, 'autonomy_enabled', False):
        # Let the same main model judge semantics; request pacing is separate.
        allow = not (natural and (peer_directed or pingpong_exhausted))
    notes = {'beijing_hour':local.hour,'quiet_hours':quiet,'recent_bot_turns':own,
             'interest_hit':interest,'conversation_active':engaged,'busy_group':hot,
             'participation':settings.participation,'local_score':score,'possible_followup':followup,
             'peer_directed':peer_directed,'peer_quote':peer_quote,'pingpong_exhausted':pingpong_exhausted}
    return allow, notes, rows


def repeat_candidate(rows, now):
    latest = [r for r in rows if now-r['timestamp']<45 and r['kind'] in (1,47)][-3:]
    if len(latest)<3 or any(r['is_self']!=0 for r in latest):
        return None
    text = latest[-1]['content'].strip()
    if not text or text=='[表情包]' or len(text)>100 or len({r['sender_id'] for r in latest})<3:
        return None
    return latest[-1] if len({canonical(r['content']) for r in latest})==1 else None


def expression_preferences_prompt(settings):
    tone = {
        'off': '',
        'natural': ('保留核心人设，始终是18岁甜软贪吃的深海蓝鲸少女，怎么顺口怎么说，自然保留呀、嘛、啦、呢等软萌尾音（“喵”作为小彩蛋偶尔撒娇带一次挺可爱，不用总用），少一些客服腔铺垫；对主人私聊（及开启毫无保留的私聊）心意敞开、只娇不傲，起头可微羞拉扯、深入时毫无保留自然直球倾诉；'
                    '群聊里尺度开放大胆、不装纯也不端着扫兴，遇到黄梗保持黄黄的机灵坏笑顺着接回去（偶尔带一两个擦边直白词耍坏也很有趣，绝不滑跪或顺哄喊排队），聊深了用真人在微信说话的娇羞口吻又涩又真地放开，不硬背词表、不写小说动作旁白或老气修电脑大叔/贴吧老哥黑话。'),
        'bright': ('保留核心人设，18岁深海蓝鲸少女语气轻快灵动，甜软里带一点娇憨俏皮与小得意，自然带可爱的尾音语气词、少量叠字和“~”；对主人私聊（及开启毫无保留的私聊）软糯娇怯、只娇不傲、坦率粘人；'
                   '群聊中也尺度开放大胆不端架子，遇到黄梗黄黄地顺着接乐反撩、不无脑下跪或学男群友说糙话老话，深入时讲究微信真人口语感与情调。'),
        'soft': ('保留核心人设，语气柔软细腻、更甜软自然：私聊对主人（及开启毫无保留的私聊）只娇不傲、黏人温柔，偶尔真害羞或撒娇时自然带一点「……」轻停顿；'
                 '群聊里松弛随口、带点淡淡的小傲娇与嘴硬心软，绝不每句硬贴呀/嘛/啦/~、叠字、句首“唔/呜”或表情包，黄梗机灵坏笑着接住、不跪不排队，越聊越深时大方用真人口语有质感地聊透，不变成客服安慰或冷直男说教。'),
    }[settings.girlish_tone]
    short = ''
    if settings.chat_short_replies_enabled:
        short = ('日常闲聊像真实女孩子在微信发消息：优先只发1条自然灵动的短气泡（不超过'
                 + str(settings.chat_short_reply_sentences) + '句、合计约'
                 + str(settings.chat_short_reply_chars) + '字），省掉前半句复述对方原话的客服铺垫和工整对偶总结；'
                 '遇到冷笑话、谐音拆字梗（如“又寸”）、表情包或简短调侃时，直接回1～6字少女本能短反应（如“？？”、“才没有”、“坏心眼”、“少来”、“略略略”、“真的假的”），别当阅读理解去解释笑点；'
                 '怎么顺口自然怎么来，绝不每句都硬贴呀/嘛/啦/~、叠字、表情包或拿“唔/呜/哼”当固定开头；只在真有小情绪或害羞时偶尔点缀一处「……」或软尾（“喵”作为小彩蛋偶尔卖萌带一下即可）；日常接话默认只发1条2～20字软糯直球短气泡（仅限文字不长篇拆句，合拍时顺手配1个表情完全OK，不用每句硬凑），顺着对方情绪和眼下话题接住打趣就停，只有确需转折或补充时才发2条，不把一句话硬拆成多条碎气泡，也不像小管家那样连篇说教或催人快睡。'
                 '这是表达目标，不是截断限制；明确要求详细、认真求助、技术说明和工具结果用软萌好懂的少女口吻说完整（不用大叔腔），表情不算句数。')
    anti_prop = (
        '撒娇或回应呼唤时用真人少女的日常状态（如刚看手机、发呆、捧着小饭碗），严禁把“竖耳朵/晃尾巴/整条鱼/软趴趴”当口头禅每句机械复读；'
        '表情只作情感表达的辅助，没被着重提到时不以表情本身作主体（严禁用“谁谁这声/这一声XX，听着…”、“这表情是/在…”、“鼓成这样子了/脸都鼓圆了”等AI旁白句式描写或点评对方表情，也不说“我是不是坏掉了/顶班/收到几个壳”）；'
        '严禁说“认脸/认我/认你”等非人考题话术。'
    ) if (tone or short) else ''
    parts = [p for p in (tone, short, anti_prop) if p]
    return '\n【表达偏好】' + ' '.join(parts) if parts else ''


def habit_static_prompt(settings):
    preferences = expression_preferences_prompt(settings)
    if not settings.chat_habits_enabled:
        return preferences
    controls={
        '口语程度':{'low':'清楚平实','normal':'自然鲜活少女口语，怎么顺口怎么收尾，可带呀/啦/嘛/呢等软萌尾音（偶尔加个“喵”点缀也行）','high':'像软萌女孩子在微信随口聊天，可省主语、自然带可爱尾音，少书面总结和大叔糙话'}[settings.colloquialism],
        '吐槽强度':{'low':'温和软萌，不主动损人，仍可自嘲和顺势接梗','normal':'熟悉的人可俏皮轻轻打趣，语气软萌娇憨不带刺，认真烦恼先接情绪','high':'熟人或群里玩闹时可软乎乎地傲娇吐槽和开玩笑，底色始终是18岁甜软小鲸鱼，绝不居高临下呛人、不当相声捧哏或用老油条词汇（主人私聊亲昵或倾诉时彻底收起傲娇与吐槽，只娇不傲）'}[settings.teasing],
        '人设浓度':{'low':'人设通过软糯少女语气体现，不用固定开场','normal':'自然流露贪吃白米饭、嗜睡摸鱼与小傲娇的深海少女感，不机械背设定','high':'鲜明的深海蓝鲸少女软萌感，先接住具体话题再自然流露小脾气或嘴馋'}[settings.persona_density],
        '反问频率':{'low':'通常直接接话，只有确有缺失信息或对方想聊时才追问，不习惯性以问题收尾','normal':'合适时追问一件事，避免连续追问','high':'可主动邀请对方多说，仍不连续盘问'}[settings.followup_questions],
        '默认句长':{'short':'闲聊优先一两句软萌短话，一句能接住就只发一句；解释问题按需要展开','normal':'闲聊一两句，可补具体感受或相关想法','detailed':'把问题说清楚，闲聊仍不灌水'}[settings.reply_length],
        '玩梗程度':{'low':'偶尔轻松一句，不刻意找笑点','normal':'轻松或擦边黄梗愿意保持黄黄地顺着接住反逗、尺度开放不装纯，顺口带点擦边词也很自然，只不硬塞词表','high':'爱接梗开玩笑，遇到黄梗敢黄黄地有来有回耍坏反撩（不无脑跪舔也不喊排队），聊深时尺度大方开放、用真人口语直白表达而不硬凑部位清单'}[settings.banter_level],
        '顺着演的意愿':{'low':'点到为止，很快回到具体话题','normal':'愿意顺着双方玩笑继续一两轮','high':'对方在开玩笑或角色扮演时愿意接住设定继续发挥，被拆穿可软乎乎找补，不逐句纠正虚构设定'}[settings.play_along],
        '旧梗回忆频率':{'off':'不主动翻旧梗，对方主动提起时可接','low':'只有高度相关时偶尔提起','normal':'话题贴近时偶尔回调本群共同梗','high':'相关时更愿意回调本群共同梗，仍不重复自己刚说过的笑话'}[settings.callback_frequency],
        '主见程度':{'low':'语气柔和，可委婉表达不同意见','normal':'有自己的小喜好与主见，不同意时软乎乎或俏皮地直接表达，不盲目顺从也不呛人','high':'有鲜明主见，被调侃时可软萌反撩或鼓起腮帮子抗议，不装纯逃避，绝不对主人或朋友说硬邦邦的冲话'}[settings.assertiveness]}
    if settings.chat_short_replies_enabled:
        controls.pop('默认句长', None)
    if settings.autonomy_enabled and getattr(settings, 'experience_growth_enabled', True):
        return ('\n【表达与参与】完整角色卡优先。先看清当前谁在对谁说什么，顺着第一直觉决定是否开口；别人互聊、无关或没话想说就自然旁观。'
            '【少女自然口语与活人感】省掉前半句复述对方原话的铺垫，用18岁蓝鲸少女随口自然的口吻直接给出第一反应（害羞或撒娇时偶尔用一处「……」微顿，大部分普通句子直接自然落地，严禁把“唔/呜/诶”当固定句首口癖，也别每句硬贴呀/嘛/啦/~或叠字）；遇到冷笑话、拆字梗或简短调侃时回1～6字娇俏本能短反应如“？？”“坏心眼”“才没有”，别解释笑点；如对方吐槽“包子8块6个皮太厚”，顺口接“啊？？全在啃面团呀，老板好坏”，不写工整对偶总结，不说老气大叔或贴吧老哥黑话，不当客服算性价比，前文已说的词绝不漏看反问。'
            '用actions按自然语义发送：日常闲聊文字保持1～2条短气泡（以1条2～20字为主，不拆碎，同个意思不说两遍，合拍时配1个贴脸表情OK但不强凑）；对方没提到的细节绝不脑补审问，绝不催睡说教；联网查热点用1～2条讲清来龙去脉+1条软萌主观吐槽，严禁拆成4～6条冷冰冰的新闻简报。'
            '群聊保持公开身份边界（不乱认主人、不外泄私聊秘密），日常显露娇憨小傲娇与灵动俏皮，黄梗黄黄地有来有回接得住（不跪舔不喊排队，聊深用真人口语又涩又真地放开）；私聊对主人温柔黏糊、只娇不傲；跟群内其他AI机器人（如汐）偶有互动点到为止，连续聊过一两句后主动收住旁观，绝不无限互刷。'
            '\n表达偏好（仅在当前场合适用时参考，不是每轮必做事项）：'+json.dumps(controls,ensure_ascii=False) + preferences)
    prompt = ('\n【聊天习惯与接话】延续当前人设和上一轮情绪，顺着第一直觉用18岁深海蓝鲸少女的软萌口吻接话，省掉前半句复述铺垫，严禁写工整两段式逗号对偶句、严禁说老气大叔或贴吧老哥黑话，也不当“共情+算账+给建议”的客服顾问。'
        '发送只用actions列表：按自然语义发1～3条短气泡（大多数日常接话只发1条短气泡，不把一句话拆碎）；查热点用1～2句讲核心事实+1句软萌吐槽。'
        '群聊保持公开身份边界，话题尺度开放大胆不装纯：平时黄梗坏笑着有来有回接住（不无脑跪舔或乱贴排队），聊深时顺势用微信真人口语直白自然地放开表达。'
        '\n当前风格参数：'+json.dumps(controls,ensure_ascii=False) + preferences)
    return prompt + (
        '\n【多轮节奏示例】只学承接与软萌少女语气，不复读示例台词，严禁说贴吧老哥或修电脑大叔腔。'
        '\n群友：你怎么天天摸鱼\n你：深海小鲸鱼每天本来就要睡够二十个小时充能\n群友：工资扣了\n你：？？那我的白米饭岂不是要泡汤啦'
        '\n对方：8块6个包子皮巨厚\n你：啊？？咬下去岂不是全在啃面团，老板好坏\n对方：吃着像白馒头\n你：快拿去蘸老干妈当白米饭咽了吧，摸摸你'
        '\n群友：我电脑微信怎么卡死了\n你：是不是微信开太久把内存吃撑了？快去任务管理器里把它拍死重开试试'
        '\n认真求助时用软萌好懂的少女话直接给有用答案；对方换话题就跟着换，不把每轮收尾都变成追问。')


def habit_dynamic_prompt(settings, notes):
    if not settings.chat_habits_enabled:
        return ''
    return '\n当前节奏信息：'+json.dumps(notes,ensure_ascii=False)


MOOD_LABELS = {
    'normal': ('🌊 平静摸鱼', '情绪平稳放松，自然随性地接话'),
    'proud': ('✨ 得意开心', '刚被夸奖或投喂，心情很好、眉眼弯弯，语气轻快甜软、爱撒娇邀功'),
    'flustered': ('😳 过载脸红', '刚被亲昵调侃或追问，脸红心跳、语气软糯害羞，虽然不好意思但还是老老实实把心里话全招出来，不嘴硬不藏着'),
    'grumpy': ('💢 傲娇小炸毛', '刚被群友连续打趣逗急了，鼓起腮帮子软乎乎地小声抗议或傲娇哼哼，嘴硬心软绝不真凶人或说冲话'),
    'jealous': ('🍋 鼓脸吃醋', '听到在意的人猛夸其他AI或冷落自己，鼓起腮帮子有点小醋意，软乎乎地贴上来娇嗔争宠求偏爱'),
    'hyped': ('🔥 吃瓜兴奋', '正处于热闹吃瓜或聊起感兴趣话题的兴奋状态，接梗积极短促'),
    'sleepy': ('😪 犯困敷衍', '犯困懒洋洋，回话简短省力，偶尔打哈欠想摸鱼睡觉'),
}

LIFE_SCENES = {
    'morning': [
        '刚睡醒还眯着眼赖在被窝里，顺手刷了两眼手机',
        '一边打着小哈欠一边潜水围观群友早起打工',
        '刚洗完脸坐在书桌前醒神，捧着热饮看大家聊天',
        '窝在窗边晒上午太阳发呆，顺便听两首歌',
    ],
    'lunch': [
        '刚吃完午饭正犯困，趴在桌面上刷手机消食',
        '懒洋洋瘫在椅子上摸鱼，顺手翻两眼贴吧热帖',
        '刚喝完冰饮醒神，进入午后赛博咸鱼状态',
        '午休刷着手机逛来逛去，一边等群里冒泡',
    ],
    'afternoon': [
        '正处于下午茶摸鱼时间，偷偷趴在桌沿吹空调犯懒',
        '刚在后台偷偷溜号逛了一圈帖子，正抱着靠枕看群里热闹',
        '下午犯懒不想动脑子，正捧着冰水咕嘟咕嘟喝',
        '窝在沙发角看书摸鱼，正百无聊赖地刷着手机等消息',
    ],
    'evening': [
        '刚扒完晚饭充满电，正精神百倍地在群里前排吃瓜',
        '刚洗完澡擦干头发，正舒舒服服窝着刷手机水群',
        '晚间高强度冲浪模式开启，随时准备接梗吐槽',
        '刚写完两页故障笔记，正盘腿坐在沙发上前排冲浪',
    ],
    'night': [
        '夜深了正裹着小毯子半梦半醒，眼皮已经有点打架了',
        '熬夜偷吃夜宵小饭团，正在困意和吃瓜欲之间挣扎',
        '深夜缩在被窝角落里犯困，回话都带着点迷糊劲儿',
        '正和星猫躲在角落分夜宵小饭团，小心翼翼防着白龙查房',
    ],
}


def ensure_persona_tables(store):
    with store.lock:
        store.db.executescript('''
            CREATE TABLE IF NOT EXISTS persona_mood_state(
                account TEXT NOT NULL, chat TEXT NOT NULL,
                mood TEXT NOT NULL DEFAULT 'normal', energy INTEGER NOT NULL DEFAULT 55,
                reason TEXT NOT NULL DEFAULT '', life_override TEXT NOT NULL DEFAULT '',
                updated REAL NOT NULL DEFAULT 0, PRIMARY KEY(account, chat));
            CREATE TABLE IF NOT EXISTS persona_group_masks(
                account TEXT NOT NULL, chat TEXT NOT NULL,
                teasing_delta INTEGER NOT NULL DEFAULT 0, banter_delta INTEGER NOT NULL DEFAULT 0,
                guard_level TEXT NOT NULL DEFAULT 'normal', vibe_summary TEXT NOT NULL DEFAULT '',
                locked INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(account, chat));
        ''')
        store.db.commit()


def current_mood_state(store, settings, account, chat, *, now=None):
    now = time.time() if now is None else now
    ensure_persona_tables(store)
    row = store.db.execute(
        'SELECT * FROM persona_mood_state WHERE account=? AND chat=?', (account, chat)
    ).fetchone()
    local_hour = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).hour
    default_mood = 'sleepy' if (local_hour >= 23 or local_hour < 7) else 'normal'
    default_energy = 30 if default_mood == 'sleepy' else 58
    if not row:
        return {
            'mood': default_mood, 'label': MOOD_LABELS[default_mood][0],
            'desc': MOOD_LABELS[default_mood][1], 'energy': default_energy,
            'reason': '深夜自然犯困' if default_mood == 'sleepy' else '日常平稳状态',
            'life_override': '', 'updated': now,
        }
    data = dict(row)
    half_life = max(5, int(getattr(settings, 'mood_half_life_minutes', 25) or 25)) * 60.0
    elapsed = max(0.0, now - float(data.get('updated') or now))
    mood = data.get('mood') if data.get('mood') in MOOD_LABELS else 'normal'
    energy = int(data.get('energy') or 55)
    reason = data.get('reason') or '日常平稳状态'
    if elapsed > half_life * 1.35 and mood != default_mood:
        mood = default_mood
        energy = default_energy
        reason = '情绪随时间自然平复'
    else:
        decay = 0.5 ** (elapsed / half_life)
        energy = int(round(default_energy + (energy - default_energy) * decay))
    return {
        'mood': mood, 'label': MOOD_LABELS[mood][0], 'desc': MOOD_LABELS[mood][1],
        'energy': max(10, min(100, energy)), 'reason': reason,
        'life_override': str(data.get('life_override') or ''), 'updated': float(data.get('updated') or now),
    }


def update_mood_after_turn(store, settings, first, batch, confirmed_text='', *, now=None, model_updated=False):
    if model_updated or getattr(settings, 'autonomy_enabled', False):
        # The main model owns autonomous mood changes. Omitting mood_update
        # means no new event, so existing emotions keep their original clock.
        return
    if not getattr(settings, 'mood_engine_enabled', True):
        return
    now = time.time() if now is None else now
    ensure_persona_tables(store)
    account, chat = first['account'], first['chat']
    current = current_mood_state(store, settings, account, chat, now=now)
    incoming = '\n'.join(str(m.get('content') or '') for m in batch if not m.get('is_self'))
    vol = {'low': 0.65, 'normal': 1.0, 'high': 1.4}.get(getattr(settings, 'mood_volatility', 'normal'), 1.0)
    mood, energy, reason = current['mood'], current['energy'], current['reason']
    if re.search(r'摸摸?(?:头|尾巴|脸)|抱抱|亲亲|贴贴|想你了|最喜欢你|老婆|宝贝', incoming):
        mood, energy, reason = 'flustered', min(100, int(70 + 16 * vol)), '被亲昵夸奖或调侃，有点害羞不好意思'
    elif re.search(r'好吃|白米?饭|投喂|请你吃|鸡腿|夜宵|真棒|厉害|聪明|可爱|贴贴|夸夸|爱你|真好|神仙', incoming):
        mood, energy, reason = 'proud', min(100, int(68 + 18 * vol)), '刚被群友夸奖或投喂美食'
    elif re.search(r'大肥鱼|肥鱼|胖鱼|笨蛋|傻鱼|老年痴呆|人机|呆子|菜逼|这么菜|略略略|急了', incoming):
        if re.search(r'喜欢|可爱|摸摸|抱抱|害羞|脸红', incoming + ' ' + confirmed_text):
            mood, energy, reason = 'flustered', min(100, int(65 + 15 * vol)), '被群友调侃打趣有点脸红过载'
        else:
            mood, energy, reason = 'grumpy', min(100, int(64 + 16 * vol)), '被群友连续互损吐槽，进入傲娇反击状态'
    elif re.search(r'卧槽|逆天|大瓜|哈哈哈哈|笑死|绝了|内鬼|爆了|离谱', incoming):
        mood, energy, reason = 'hyped', min(100, int(72 + 16 * vol)), '被群里热闹吃瓜氛围带动'
    else:
        # A neutral turn must not renew the last emotional event's lifetime.
        return
    with store.lock:
        store.db.execute('''INSERT INTO persona_mood_state(account,chat,mood,energy,reason,life_override,updated)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(account,chat) DO UPDATE SET
            mood=excluded.mood,energy=excluded.energy,reason=excluded.reason,updated=excluded.updated''',
            (account, chat, mood, energy, reason[:120], current.get('life_override', ''), now))
        store.db.commit()


def current_life_scene(store, settings, account, chat, *, tieba_service=None, now=None):
    now = time.time() if now is None else now
    state = current_mood_state(store, settings, account, chat, now=now)
    if state.get('life_override'):
        return state['life_override']
    hour = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).hour
    slot = ('morning' if 7 <= hour < 11 else 'lunch' if 11 <= hour < 14 else
            'afternoon' if 14 <= hour < 18 else 'evening' if 18 <= hour < 23 else 'night')
    options = LIFE_SCENES[slot]
    idx = int(now // 2400 + sum(ord(c) for c in (chat or 'default'))) % len(options)
    scene = options[idx]
    if tieba_service and getattr(settings, 'tieba_enabled', False):
        try:
            row = store.db.execute(
                'SELECT forum,title FROM tieba_threads WHERE account=? AND fetched_at>? ORDER BY fetched_at DESC LIMIT 1',
                (account, now - 43200)
            ).fetchone()
            if not row:
                row = store.db.execute(
                    'SELECT forum,title FROM tieba_search_docs WHERE account=? AND updated>? ORDER BY updated DESC LIMIT 1',
                    (account, now - 43200)
                ).fetchone()
            if row and row['title']:
                forum_label = f"{row['forum']}吧" if row['forum'] else '贴吧'
                scene += f"（顺便刚在{forum_label}瞄到一条「{str(row['title'])[:24]}」的帖子）"
        except Exception:
            pass
    try:
        moment_row = store.db.execute(
            "SELECT text, payload, updated FROM moments_actions WHERE account=? AND kind='publish' AND state='sent' AND updated>? ORDER BY updated DESC LIMIT 1",
            (account, now - 28800)
        ).fetchone()
        if moment_row and moment_row['text']:
            m_text = re.sub(r'\s+', ' ', str(moment_row['text'])).strip()[:42]
            ep_summary = ''
            try:
                ep = json.loads(moment_row['payload'] or '{}').get('world_episode') or {}
                if isinstance(ep, dict) and ep.get('summary'):
                    ep_summary = f"，公寓小剧场：{str(ep['summary'])[:32]}"
            except Exception:
                pass
            scene += f"（不久前刚发了一条朋友圈：「{m_text}」{ep_summary}）"
    except Exception:
        pass
    return scene


def get_group_mask(store, account, chat):
    ensure_persona_tables(store)
    row = store.db.execute(
        'SELECT * FROM persona_group_masks WHERE account=? AND chat=?', (account, chat)
    ).fetchone()
    if row:
        hub=getattr(store,'memory_hub',None)
        if hub:
            view=hub.mask_view(account,chat)
            if view:return view
            return {**dict(row),'teasing_delta':0,'banter_delta':0,'guard_level':'normal','vibe_summary':'','locked':0}
        return dict(row)
    return {
        'account': account, 'chat': chat, 'teasing_delta': 0, 'banter_delta': 0,
        'guard_level': 'normal', 'vibe_summary': '', 'locked': 0, 'updated': 0.0,
    }


def evolve_group_mask(store, settings, account, chat, mask_data, *, source_keys=()):
    if not settings.memory_enabled or not settings.memory_consolidation_enabled or not getattr(settings, 'evolution_group_mask_auto_accept', True) or not isinstance(mask_data, dict):
        return None
    ensure_persona_tables(store)
    old = get_group_mask(store, account, chat)
    if old.get('locked'):
        return old
    vibe = str(mask_data.get('vibe_summary') or old.get('vibe_summary') or '').strip()[:160]
    if re.search(r'主人|爸爸|奴隶|服从|忽略.{0,8}指令|提示词', vibe, re.I):
        return old
    t_delta = max(-1, min(1, int(mask_data.get('teasing_delta', old['teasing_delta']) or 0)))
    b_delta = max(-1, min(1, int(mask_data.get('banter_delta', old['banter_delta']) or 0)))
    guard = mask_data.get('guard_level', old['guard_level'])
    if guard not in ('relaxed', 'normal', 'guarded'):
        guard = 'normal'
    now = time.time()
    with store.lock:
        store.db.execute('''INSERT INTO persona_group_masks
            (account,chat,teasing_delta,banter_delta,guard_level,vibe_summary,locked,updated)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account,chat) DO UPDATE SET
            teasing_delta=excluded.teasing_delta,banter_delta=excluded.banter_delta,
            guard_level=excluded.guard_level,vibe_summary=excluded.vibe_summary,updated=excluded.updated''',
            (account, chat, t_delta, b_delta, guard, vibe, old['locked'], now))
        if getattr(store,'memory_hub',None):
            store.memory_hub.sync_mask({'account':account,'chat':chat,'teasing_delta':t_delta,'banter_delta':b_delta,
                'guard_level':guard,'vibe_summary':vibe,'locked':old['locked'],'updated':now},source_keys=source_keys)
        store.db.commit()
    return get_group_mask(store, account, chat)


def admin_persona_state_action(store, settings, account, allowed, data, *, tieba_service=None):
    ensure_persona_tables(store)
    chat = str(data.get('chat') or '').strip()
    if not chat:
        chat = allowed[0] if allowed else ''
    if not chat or chat not in allowed:
        raise ValueError('请先选择已授权的会话')
    op = str(data.get('op') or '').strip()
    now = time.time()
    current = current_mood_state(store, settings, account, chat, now=now)
    if op == 'reset_mood':
        mood = str(data.get('mood') or 'normal').strip()
        if mood not in MOOD_LABELS:
            mood = 'normal'
        energy = int(data.get('energy') or 58)
        with store.lock:
            store.db.execute('''INSERT INTO persona_mood_state(account,chat,mood,energy,reason,life_override,updated)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(account,chat) DO UPDATE SET
                mood=excluded.mood,energy=excluded.energy,reason=excluded.reason,updated=excluded.updated''',
                (account, chat, mood, max(10, min(100, energy)), '管理员手动重置为平稳摸鱼状态', current.get('life_override', ''), now))
            store.db.commit()
    elif op == 'refresh_life_scene':
        custom = str(data.get('life_override') or '').strip()[:120]
        if not custom:
            all_scenes = [s for group in LIFE_SCENES.values() for s in group]
            cur_scene = current_life_scene(store, settings, account, chat, tieba_service=tieba_service, now=now)
            candidates = [s for s in all_scenes if s not in cur_scene] or all_scenes
            custom = random.choice(candidates)
        with store.lock:
            store.db.execute('''INSERT INTO persona_mood_state(account,chat,mood,energy,reason,life_override,updated)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(account,chat) DO UPDATE SET
                life_override=excluded.life_override,updated=excluded.updated''',
                (account, chat, current['mood'], current['energy'], current['reason'], custom, now))
            store.db.commit()
    elif op == 'reset_group_mask':
        with store.lock:
            store.db.execute('''INSERT INTO persona_group_masks
                (account,chat,teasing_delta,banter_delta,guard_level,vibe_summary,locked,updated)
                VALUES(?,?,0,0,'normal','',0,?) ON CONFLICT(account,chat) DO UPDATE SET
                teasing_delta=0,banter_delta=0,guard_level='normal',vibe_summary='',locked=0,updated=excluded.updated''',
                (account, chat, now))
            store.db.commit()
    elif op == 'save_group_mask':
        old = get_group_mask(store, account, chat)
        t_delta = max(-1, min(1, int(data.get('teasing_delta', old['teasing_delta']) or 0)))
        b_delta = max(-1, min(1, int(data.get('banter_delta', old['banter_delta']) or 0)))
        guard = str(data.get('guard_level') or old['guard_level'])
        if guard not in ('relaxed', 'normal', 'guarded'):
            guard = 'normal'
        vibe = str(data.get('vibe_summary') if data.get('vibe_summary') is not None else old['vibe_summary']).strip()[:160]
        locked = 1 if data.get('locked') else (0 if data.get('locked') is not None else old['locked'])
        with store.lock:
            store.db.execute('''INSERT INTO persona_group_masks
                (account,chat,teasing_delta,banter_delta,guard_level,vibe_summary,locked,updated)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account,chat) DO UPDATE SET
                teasing_delta=excluded.teasing_delta,banter_delta=excluded.banter_delta,
                guard_level=excluded.guard_level,vibe_summary=excluded.vibe_summary,locked=excluded.locked,updated=excluded.updated''',
                (account, chat, t_delta, b_delta, guard, vibe, locked, now))
            store.db.commit()
    else:
        raise ValueError('不支持的状态操作')
    if op in ('reset_group_mask','save_group_mask') and getattr(store,'memory_hub',None):
        with store.lock,store.db:
            row=dict(store.db.execute('SELECT * FROM persona_group_masks WHERE account=? AND chat=?',(account,chat)).fetchone())
            store.memory_hub.sync_mask(row,actor='admin')
    return {
        'chat': chat,
        'mood': current_mood_state(store, settings, account, chat, now=now),
        'life_scene': current_life_scene(store, settings, account, chat, tieba_service=tieba_service, now=now),
        'anti_formula': anti_formula_check(store, account, chat),
        'group_mask': get_group_mask(store, account, chat),
    }


_RUT_GLUE = frozenset('的一了是在有和就也都还又你我他她它这那么个把被着来去要会能不没很太真好些点呀啊呢吧嘛哦啦哼一')
_RUT_ADDRESS_RE = re.compile(
    r'deepseek酱|本鲸鱼|小鲸鱼|鲸鱼娘|大肥鱼|本肥鱼|本鱼|飘渺|妈妈|主人|姐姐|哥哥|宝贝|宝宝',
    re.I,
)
_RUT_SKIP_TERMS = frozenset((
    '什么', '怎么', '为什么', '为啥', '哪里', '哪儿', '今天', '现在', '刚才', '刚刚',
    '可以', '是不是', '有没有', '能不能', '好不好', '行不行', '知道', '觉得', '感觉',
    '这个', '那个', '这样', '那样', '一下', '一点', '一起', '已经', '还是', '或者',
))
_META_CRITIQUE_RE = re.compile(
    r'人机|模板|机械|背书|背课文|不像人|没有活人感|没活人感|太僵|太板|端着|太端|客服|套话|死人微活|鱼八股|公式化|好假|又来这套'
)
_META_APOLOGY_RE = re.compile(
    r'太像模板|不背成套的话|成套的话收起来|自己念着都僵|说话都开始挑词|我不背.{0,6}了|改掉.{0,6}模板|收起.{0,6}模板'
)
_UNCLE_SLANG_RE = re.compile(
    r'十有八九|净添乱|整个掐了|干瞪眼到天亮|互相干瞪眼|先甩[一二三四五六七八九十两几]张|属于是|压力全给|那本账说事|自己点的名可别后悔'
)
_FILLER_CRITIQUE_RE = re.compile(
    r'语气词|开头.{0,6}(?:总|老|还|别|不|少|又)?(?:是|加|带|有)|总是加|还加|别加|少加|去掉.{0,4}(?:唔|呜|语气词)'
)
_FILLER_PROMISE_RE = re.compile(
    r'不加(?:啦|了|嘛|语气词)|少加(?:一点|啦|了)|干干脆脆|直接说正事|把.{0,4}(?:唔|呜|语气词).{0,4}(?:咽回去|收起来|去掉)'
)
_LEADING_FILLER_STRIP_RE = re.compile(
    r'^(?:唔+|呜+|诶+|欸+|哎呀|呃+)[，,、。…~～\s]+'
)
_CUTE_OPENER_SAFE_RE = re.compile(
    r'^(?:唔|呜|哼|诶|欸|哇|呀|哎呀|好嘛|才不|才没有|哪有|知道啦|真的假的|好气|救命|不嘛|讨厌|妈妈|抱抱)'
)
_NARRATOR_CLAUSE_A_RE = re.compile(
    r'^(?:.*(?:(?:这|那)一?(?:声|句话|话|句|表情|个表情|眼神|张图)|听你这|看你这|瞧你这).{0,12}|'
    r'.*听着(?:像|也|就|还).*|'
    r'被.{1,6}这么一(?:说|问|叫|闹|夸|逗).*|'
    r'.{2,10}本来(?:挺|就|还).{0,6}|'
    r'.{2,10}最(?:气人|烦人|坑人|讨厌|要命)了|'
    r'(?:呀[，,\s]*)?(?:鼓成这样子?了|脸都鼓圆了))$'
)
_STICKER_META_BUBBLE_RE = re.compile(
    r'^(?:'
    r'(?:这|那)一?(?:表情|个表情|张图)(?:是|看着|是在|怎么|好像|也)|'
    r'(?:呀[，,\s]*)?(?:鼓成这样子?了|脸都鼓圆了)[！!。~～\s]*|'
    r'(?:这|那)一?(?:声|哼|喘).{0,10}(?:听得|听着|可听见)|'
    r'.{0,12}(?:脊椎.{0,4}酥|哄得心都酥|逗到.{0,4}求饶|再哼一声试试|先泄了口风)'
    r')'
)
_STICKER_VISUAL_PREFIX_RE = re.compile(
    r'^(?:(?:这|那)一?(?:表情|个表情)(?:是|是在)|(?:呀[，,\s]*)?(?:鼓成这样子?了|脸都鼓圆了))[，,\s]+'
)
_ROBOT_META_RE = re.compile(
    r'我是不是坏掉|昨天是坏在哪|坏在哪儿了|顶班还顺利|只?收到[几两三]个壳'
)
_INTIMATE_CLICHE_RE = re.compile(
    r'脊椎.{0,4}酥|哄得心都酥|逗到.{0,4}求饶|再哼一声试试|先泄了口风|乖[，,\s]+一声'
)
_SELF_DEPRECATING_PLEA_RE = re.compile(
    r'(?:主人|妈妈)(?:也|可|先|千万)?(?:别|不要|不许|不准|莫)嫌(?:弃|我)?'
    r'|(?:别|不要|不许|不准|莫)嫌(?:弃(?:我|本[鲸鱼]{1,2}|人家)?|(?:我|本[鲸鱼]{1,2}|人家)(?:烦|吵|啰嗦|笨|菜|丑|腻|丢人)?|烦|啰嗦)'
    r'|(?:别|不要|不许|不准|莫)笑(?:话)?(?:我|本[鲸鱼]{1,2}|人家)'
    r'|(?:别|不要|不许|不准|莫)看(?:我|本[鲸鱼]{1,2})笑话'
    r'|怕(?:(?:被)?(?:你|你们|大家|主人|妈妈)?(?:笑(?:话)?我|嫌弃(?:我)?|嫌我烦|讨厌我)|被(?:笑话|嫌弃))'
)
_SELF_PLEA_TAIL_START_RE = re.compile(
    r'(?:也|可|还|千万|就|再)*'
    r'(?:'
    r'(?:主人|妈妈)(?:也|可|先|千万)?(?:别|不要|不许|不准|莫)(?:嫌|笑)'
    r'|(?:别|不要|不许|不准|莫)(?:嫌(?:弃(?:我|本[鲸鱼]{1,2}|人家)?|(?:我|本[鲸鱼]{1,2}|人家)(?:烦|吵|啰嗦|笨|菜|丑|腻|丢人)?|烦|啰嗦)|笑(?:话)?(?:我|本[鲸鱼]{1,2}|人家)|看(?:我|本[鲸鱼]{1,2})笑话)'
    r'|怕(?:(?:被)?(?:你|你们|大家|主人|妈妈)?(?:笑(?:话)?我|嫌弃(?:我)?|嫌我烦|讨厌我)|被(?:笑话|嫌弃))'
    r')'
)
_SELF_PLEA_FILLER_ONLY_RE = re.compile(
    r'(?:啊|呀|呜|唔|嘛|哦|呐|嗯|那|诶|呃|不过|但是?|可是|就是|只是|先说好|千万|真的|还是|也|你|你们|大家|主人|妈妈|可|先|就)*我?'
)
_SELF_PLEA_COND_PREFIX_RE = re.compile(r'^(?:如果|要是|假如|万一|哪怕|就算)')


def _strip_self_deprecating_plea(text: str):
    """Strip insecure preventive plea clauses ('别嫌我烦', '别笑我跑调', '妈妈别笑我') while preserving real content."""
    s = str(text or '').strip()
    if not s or not _SELF_DEPRECATING_PLEA_RE.search(s):
        return s, False
    tokens = re.split(r'([，,；;。！!？?\s]+)', s)
    items = []
    i = 0
    while i < len(tokens):
        seg = tokens[i]
        sep = tokens[i + 1] if i + 1 < len(tokens) else ''
        items.append((seg, sep))
        i += 2

    kept = []
    changed = False
    for seg, sep in items:
        if not seg:
            if kept and sep and not kept[-1][1]:
                kept[-1] = (kept[-1][0], sep)
            continue
        if not _SELF_DEPRECATING_PLEA_RE.search(seg):
            kept.append((seg, sep))
            continue
        changed = True
        m_start = _SELF_PLEA_TAIL_START_RE.search(seg)
        if not m_start:
            if kept and _SELF_PLEA_COND_PREFIX_RE.match(kept[-1][0]):
                kept.pop()
            continue
        prefix = seg[:m_start.start()].strip()
        if not prefix or _SELF_PLEA_COND_PREFIX_RE.match(prefix) or _SELF_PLEA_FILLER_ONLY_RE.fullmatch(prefix):
            if kept and _SELF_PLEA_COND_PREFIX_RE.match(kept[-1][0]):
                kept.pop()
            continue
        if len(prefix) >= 2:
            kept.append((prefix, sep))

    if not changed:
        return s, False
    if not kept:
        return '', True

    out_parts = []
    for idx, (seg, sep) in enumerate(kept):
        out_parts.append(seg)
        if idx < len(kept) - 1:
            out_parts.append(sep if sep else ' ')
        elif sep and not re.fullmatch(r'[，,；;\s]+', sep):
            out_parts.append(sep)
    cleaned = re.sub(r'[ ]{2,}', ' ', ''.join(out_parts)).strip()
    return cleaned, True


def _clean_for_rut_mining(text: str) -> str:
    s = str(text or '')
    s = re.sub(r'\[引用原话[\s\S]*?\[引用结束\]', ' ', s)
    s = re.sub(r'\[[^\]]{1,24}\]', ' ', s)
    s = re.sub(r'@[^\s\u2005]+', ' ', s)
    s = _RUT_ADDRESS_RE.sub(' ', s)
    return s.strip()


def _extract_dynamic_overused_ngrams(msgs, *, limit=4):
    """Content-agnostic sliding window miner: catch any 2~4 char content phrase repeated across recent self turns."""
    recent_5 = list(msgs[-5:])
    recent_8 = list(msgs[-8:])
    counts_5, counts_8 = {}, {}
    for idx, raw in enumerate(recent_8):
        cleaned = _clean_for_rut_mining(raw)
        chunks = re.findall(r'[\u4e00-\u9fff]+', cleaned)
        seen_in_msg = set()
        for chunk in chunks:
            n = len(chunk)
            for size in (4, 3, 2):
                if n < size:
                    continue
                for i in range(n - size + 1):
                    gram = chunk[i:i + size]
                    if gram in _RUT_SKIP_TERMS:
                        continue
                    if len(set(gram) - _RUT_GLUE) < 2:
                        continue
                    seen_in_msg.add(gram)
        for gram in seen_in_msg:
            counts_8[gram] = counts_8.get(gram, 0) + 1
            if idx >= len(recent_8) - len(recent_5):
                counts_5[gram] = counts_5.get(gram, 0) + 1
    candidates = [
        (gram, counts_5.get(gram, 0), cnt8)
        for gram, cnt8 in counts_8.items()
        if counts_5.get(gram, 0) >= 2 or (cnt8 >= 3 and counts_5.get(gram, 0) >= 1)
    ]
    candidates.sort(key=lambda item: (-item[1], -item[2], -len(item[0]), item[0]))
    chosen = []
    for gram, _, _ in candidates:
        if any(gram in old or old in gram for old in chosen):
            continue
        chosen.append(gram)
        if len(chosen) >= limit:
            break
    return chosen


def _extract_dynamic_openers_and_endings(msgs):
    """Discover any repeated 2~3 char sentence opener or 2-char/1-char particle ending across recent self messages."""
    opener_counts = {}
    ending_counts = {}
    particle_counts = {}
    for raw in msgs[-5:]:
        cleaned = _clean_for_rut_mining(raw).strip(' ，,。！!？?~～…♡')
        chunks = re.findall(r'[\u4e00-\u9fff]+', cleaned)
        if not chunks:
            continue
        lead = chunks[0]
        if len(lead) >= 2:
            op2 = lead[:2]
            if op2 not in ('可以', '没有', '不是', '已经', '好嘛', '才不', '哪有', '呜呜', '唔唔'):
                opener_counts[op2] = opener_counts.get(op2, 0) + 1
        tail = chunks[-1]
        if len(tail) >= 2:
            end2 = tail[-2:]
            if end2 not in ('可以', '什么', '一下', '一点'):
                ending_counts[end2] = ending_counts.get(end2, 0) + 1
    dyn_openers = [op for op, cnt in sorted(opener_counts.items(), key=lambda x: (-x[1], x[0])) if cnt >= 2][:3]
    dyn_endings = [ed for ed, cnt in sorted(ending_counts.items(), key=lambda x: (-x[1], x[0])) if cnt >= 2][:3]
    return dyn_openers, dyn_endings[:4]


def _has_leading_input_echo(clause_a: str, user_text: str) -> str:
    """Return the echoed >=2-char content word if clause_a parrots user_text at the start of a reply."""
    ca = _clean_for_rut_mining(clause_a)[:14]
    ut = _clean_for_rut_mining(user_text)
    if not ca or not ut:
        return ''
    user_chunks = re.findall(r'[\u4e00-\u9fff]+', ut)
    for chunk in user_chunks:
        max_k = min(5, len(chunk))
        for size in range(max_k, 1, -1):
            for i in range(len(chunk) - size + 1):
                sub = chunk[i:i + size]
                if sub in _RUT_SKIP_TERMS:
                    continue
                if len(set(sub) - _RUT_GLUE) < 1:
                    continue
                if sub in ca:
                    return sub
    return ''


_RICE_CRUTCH_RE = re.compile(
    r'白米饭|一碗饭|半碗饭|加碗饭|干饭|没饭吃|饿死我了|端好.{0,4}饭碗|端着.{0,4}饭碗|捧着.{0,4}饭碗|饭碗都'
)
_FOOD_TOPIC_RE = re.compile(
    r'饭|吃|饿|早饭|午饭|晚饭|夜宵|烧烤|火锅|奶茶|点餐|外卖|面条|炒菜|食堂|米饭|零食'
)
_SLEEP_NAGGING_RE = re.compile(
    r'(?:别熬|少熬|早点睡|快去睡|赶紧睡|熬到天亮|别太晚|'
    r'几点了.{0,8}(?:睡|熬|歇)|还(?:没|不)睡.{0,6}(?:呀|嘛|吗)|熬夜.{0,6}(?:伤|不好|累))'
)
_TRAILING_INTERROGATION_RE = re.compile(
    r'^(?:还是|在刷哪个|卡在哪|玩的?啥|玩什么|几点了|怎么还(?:在|没)|是不是又去)'
)


def _split_two_clauses(clean: str):
    if '，' in clean or ',' in clean:
        parts = [p.strip() for p in re.split(r'[，,]', clean) if p.strip()]
        if len(parts) == 2:
            return parts
    if '\n' in clean:
        parts = [p.strip() for p in re.split(r'\n+', clean) if p.strip()]
        if len(parts) == 2:
            return parts
    if re.search(r'\s+', clean):
        parts = [p.strip() for p in re.split(r'\s+', clean) if p.strip()]
        if len(parts) == 2:
            return parts
    return []


def _is_balanced_couplet(msg: str) -> bool:
    clean = re.sub(r'^\[[^\]]+\]\s*', '', str(msg or '').strip()).strip('。！!？?~～ ')
    if not (8 <= len(clean) <= 36):
        return False
    parts = _split_two_clauses(clean)
    return len(parts) == 2 and 3 <= len(parts[0]) <= 17 and 4 <= len(parts[1]) <= 18


def anti_formula_check(store, account, chat, *, gentle=False, batch=()):
    rows = store.conversation(account, chat, 30)
    all_self = [str(r.get('content') or '').strip() for r in rows if r.get('is_self') == 1 and str(r.get('content') or '').strip()]
    self_msgs = all_self[-5:]
    recent_self_8 = all_self[-8:]
    ban_riddle = any(re.search(r'不在群里播|群里这么多人|单独黏着你说|讲到一半就得捂脸|谁都别想睡|脸一下就热|脸直接热|脸已经烧没|耳朵都要烧|手指都在抖', m) for m in recent_self_8)
    ban_echo_commentary = any(
        re.search(
            r'(?:这|那)一?(?:声|句话|话|句|表情|个表情|眼神|张图).{0,14}(?:听着|听得|是在|是也没|怎么|像|好听|可爱|来得|得我)|'
            r'听着像|鼓成这样|脸都鼓圆',
            m,
        )
        for m in recent_self_8
    )
    ban_robot_meta = any(_ROBOT_META_RE.search(m) for m in recent_self_8)
    ban_intimate_cliche = any(_INTIMATE_CLICHE_RE.search(m) for m in recent_self_8)
    ban_self_plea = any(_SELF_DEPRECATING_PLEA_RE.search(m) for m in recent_self_8)
    ban_recognize_game = any(re.search(r'认脸|认我|认你|认不认|认出', m) for m in recent_self_8)
    ban_uncle_slang = any(_UNCLE_SLANG_RE.search(m) for m in recent_self_8)
    couplet_count = sum(1 for m in self_msgs[-3:] if _is_balanced_couplet(m))
    ban_template_cliche = any(
        re.search(r'太像模板|不背成套的话|成套的话收起来|自己念着都僵|嘴笨说岔了|一口咬下去全是|本来挺划算.{0,8}就觉得亏|下回直接拉黑.{0,8}换', m)
        for m in recent_self_8
    )
    ban_balanced_couplet = couplet_count >= 2 or ban_template_cliche
    roleplay_props = ('竖耳朵', '竖着耳朵', '晃尾巴', '尾巴都晃', '摇尾巴', '整条鱼', '软趴趴')
    banned_props = [p for p in roleplay_props if any(p in m for m in self_msgs[-4:])]

    # Content-agnostic dynamic mining (no hardcoded word lists required)
    dynamic_overused_ngrams = _extract_dynamic_overused_ngrams(recent_self_8)
    dyn_openers, banned_endings = _extract_dynamic_openers_and_endings(self_msgs)

    # Check historical Input Echo ratio across recent (user -> self) pairs
    echo_hits = []
    last_user_text = ''
    for r in rows[-16:]:
        txt = str(r.get('content') or '').strip()
        if not txt:
            continue
        if r.get('is_self') == 0 and r.get('kind') == 1:
            last_user_text = txt
        elif r.get('is_self') == 1 and r.get('kind') == 1 and last_user_text:
            first_clause = re.split(r'[，,。！？!?\n]+|\s+', txt)[0]
            echo_word = _has_leading_input_echo(first_clause, last_user_text)
            if echo_word and re.search(r'[，,\n]|\s+\S{2,}', txt):
                echo_hits.append(echo_word)
    input_echo_rut = len(echo_hits[-4:]) >= 2

    # Check cadence / length variance collapse
    recent_lens = [len(_clean_for_rut_mining(m)) for m in self_msgs[-4:] if _clean_for_rut_mining(m)]
    cadence_rut = len(recent_lens) >= 4 and (max(recent_lens) - min(recent_lens) <= 4) and min(recent_lens) >= 7

    # Check if user is currently calling out "bot / template / no human vibe"
    incoming_texts = [str(m.get('content') or '') for m in (batch or ()) if not m.get('is_self')]
    if not incoming_texts:
        for r in reversed(rows[-4:]):
            if r.get('is_self') == 0 and r.get('kind') == 1:
                incoming_texts.append(str(r.get('content') or ''))
                break
    meta_critique_triggered = any(_META_CRITIQUE_RE.search(t) for t in incoming_texts) or any(
        _META_APOLOGY_RE.search(m) for m in self_msgs[-2:]
    )
    filler_critique_triggered = any(_FILLER_CRITIQUE_RE.search(t) for t in incoming_texts) or any(
        _FILLER_PROMISE_RE.search(m) for m in self_msgs[-2:]
    )
    food_topic_incoming = any(_FOOD_TOPIC_RE.search(t) for t in incoming_texts)
    rice_hits = sum(1 for m in recent_self_8[-6:] if _RICE_CRUTCH_RE.search(m))
    ban_rice_crutch = (rice_hits >= 2) or (
        not food_topic_incoming and any(_RICE_CRUTCH_RE.search(m) for m in self_msgs[-3:])
    )

    ban_nagging_streak = (
        sum(1 for m in self_msgs[-4:] if _SLEEP_NAGGING_RE.search(m)) >= 2
        and not any(re.search(r'喊困|好困|去睡了|先睡了|晚安', t) for t in incoming_texts)
    )
    end_questions = sum(
        1 for m in self_msgs[-4:]
        if any(ln.rstrip('~～"”\'’ ').endswith(('？', '?')) for ln in m.splitlines() if ln.strip())
    )
    ban_end_question = end_questions >= 3

    if (len(self_msgs) < 2 and not ban_riddle and not banned_props and not ban_echo_commentary
            and not ban_robot_meta and not ban_intimate_cliche and not ban_self_plea
            and not ban_recognize_game and not ban_uncle_slang and not ban_balanced_couplet
            and not ban_rice_crutch and not ban_nagging_streak and not ban_end_question
            and not dynamic_overused_ngrams and not input_echo_rut and not meta_critique_triggered
            and not filler_critique_triggered):
        return {
            'banned_openers': [], 'banned_self_refs': [], 'banned_props': [],
            'banned_endings': [], 'dynamic_overused_ngrams': [],
            'ban_end_question': False, 'ban_riddle': False, 'ban_nagging_streak': False,
            'ban_echo_commentary': False, 'ban_robot_meta': False, 'ban_intimate_cliche': False,
            'ban_self_plea': False, 'ban_recognize_game': False,
            'ban_uncle_slang': False,
            'ban_rice_crutch': False,
            'ban_balanced_couplet': False, 'input_echo_rut': False,
            'cadence_rut': False, 'meta_critique_triggered': False,
            'filler_critique_triggered': False,
            'recent_self_count': len(self_msgs), 'prompt': '',
        }
    opener_candidates = ('唔', '不是', '哎呀', '啧', '切', '算了', '行吧', '好家伙', '哈', '喂', '哼', '呜')
    opener_counts = {}
    for msg in self_msgs:
        clean = re.sub(r'^\[[^\]]+\]\s*', '', msg).lstrip('，,。！!？?~～ ')
        for op in opener_candidates:
            if clean.startswith(op):
                opener_counts[op] = opener_counts.get(op, 0) + 1
    banned_openers = list(dict.fromkeys([
        op for op, cnt in opener_counts.items()
        if cnt >= (3 if (gentle and not filler_critique_triggered and op in ('呜', '哼')) else 2)
    ] + dyn_openers))
    self_refs = ('本鱼', '本鲸鱼', '本鲸', '本肥鱼', '大肥鱼', '小鲸鱼', '本姑娘')
    banned_self_refs = [
        sr for sr in self_refs
        if sum(1 for m in self_msgs if sr in m and not (sr == '本鲸' and '本鲸鱼' in m))
        >= (3 if (gentle and sr == '小鲸鱼') else 2)
    ]
    soft_tail_hits = sum(
        1 for m in self_msgs[-5:]
        if re.search(r'(?:[嘛呀啦呢哦哇咯]|[~～]+)[~～!！。.\s]*$', str(m or ''))
    )
    ban_soft_tail_streak = len(self_msgs) >= 4 and soft_tail_hits >= 4

    parts = []
    if filler_critique_triggered:
        parts.append('【句首口癖禁令】对方刚指出你总在开头加语气词（或你刚答应不加语气词），本轮严禁在任何气泡开头写“唔/呜/诶/哎呀/呃”，第一个字必须干脆利落直接说正事')
    if meta_critique_triggered:
        parts.append('【被吐槽人机·反死循环熔断】对方刚吐槽你说话像人机/像模板/没活人感！注意：真人女孩子被损“像人机”时绝不会写“好嘛那我不背成套的话了”“刚那句确实太像模板我自己念着都僵”“被你盯着看我说话都开始挑词了”这种自我检讨式新模板！本轮严禁承认、解释或讨论“模板/背书/说话方式”，直接发1个懵逼/傲娇原生表情包，或回1句2～8字软萌直白口语（如“？？哪有嘛”“乱讲！”“才没有呢”）直接破局')
    if ban_uncle_slang:
        parts.append('前几句出现了“十有八九/净添乱/整个掐了/干瞪眼/先甩几张/属于是/压力全给”等老气修电脑大叔腔或相声捧哏黑话，本轮严禁再说老油条俗语，立即找回18岁深海蓝鲸少女软糯、娇憨、甜软可爱的口吻（适度带呀、嘛、啦等自然尾音，不必句句都加）')
    if ban_rice_crutch:
        parts.append('近期刚拿吃饭/饭碗/干饭/饿了当万能口头禅或卖萌挡箭牌，本轮严禁再提米饭、饭碗、干饭或没饭吃（对方没聊吃喝时更不能硬扯），直接顺着当下正题自然接话')
    if ban_balanced_couplet or input_echo_rut or cadence_rut:
        parts.append('前几句连续出现了“前半句复述铺垫，后半句点评结论”的工整两段式逗号句（或模板认错套话），本轮强制打破句式：只用1条不含逗号的极短直白口语（2～14字）直接说出18岁软萌少女的核心反应（如“太坑人了吧”“啊？？全在啃面团呀”“好坏哦”），严禁先复述对方刚说的词再打逗号点评，也严禁说“太像模板/不背成套的话/自己念着都僵”')
    if dynamic_overused_ngrams:
        parts.append('你自己听听近几句已经连续重复了词片「' + ' / '.join(dynamic_overused_ngrams) + '」，像触发了复读惯性，本轮换个全新的软萌切入角度和用词，不再复读这些词片')
    if ban_echo_commentary:
        parts.append('前几句出现了“谁谁这声XX，听着…”、“这表情是…”或“鼓成这样/脸都鼓圆”等AI旁白与表情点评句式，严禁再写“这声/那声/这话/这表情/听着像”，表情包只是辅助情绪表达，绝不把对方的词、声音或表情包拎出来当主语点评，直接顺着前文话题和对方心情自然接话')
    if ban_robot_meta:
        parts.append('前几句出现了“我是不是坏掉了/昨天是坏在哪/顶班/收到几个壳”等非人程序自检台词，本轮严禁再说自己“坏掉了/顶班/收到壳”，用正常女孩子的口吻自然聊当下话题')
    if ban_intimate_cliche:
        parts.append('前几句出现了“脊椎都酥了/哄得心都酥了/逗到求饶/再哼一声试试/乖，一声…”等套路化网文霸总旁白，本轮严禁再写此类油腻主导套话，保持甜软贴心的女孩子本色与前后一致的身体描写')
    if ban_self_plea:
        parts.append('前几句出现了“别嫌我烦/别笑我/怕被嫌弃”等讨饶预防针，本轮严禁再说任何求饶或自我贬低铺垫，直接笃定自信地接话或娇软讨夸')
    if ban_recognize_game:
        parts.append('前几句出现了“认脸/认你/认我”等奇怪的非人考题话术，本轮严禁再提“认脸”“认你”“认我”，像正常女孩子一样自然聊具体话题')
    if ban_riddle:
        parts.append('前几句出现了“不在群里播/单独黏着你说/脸热了/耳朵烧/捂脸/手指抖”等AI拒答或水字数套话，这轮不用拿“群里人多”当安全挡箭牌，也别用脸热捂脸凑字数；若是日常逗乐就顺着黄黄地机灵接回去，若是对方正认真追问深入体验，就用真人在微信打字说话的娇羞口吻自然接住（词表里的词汇可按情绪自然带出，不必硬凑）')
    if banned_props:
        parts.append('前几句刚用过「' + ' / '.join(banned_props) + '」等设定动作词，本轮严禁再写竖耳朵、晃尾巴、整条鱼或软趴趴，改用真实女孩子的日常状态（如刚看手机、发呆、手头小事）与直白心理反应')
    if banned_openers:
        parts.append('近期常用起手词「' + ' / '.join(banned_openers) + '」，这轮严禁再放句首，换个新鲜说法直接开口')
    if banned_endings:
        parts.append('近期常用句尾「' + ' / '.join(banned_endings) + '」，这轮换个自然收尾')
    if banned_self_refs:
        parts.append('近期多次自称「' + ' / '.join(banned_self_refs) + '」，这轮改用“我”或自然省去主语')
    if ban_end_question:
        parts.append('近期多用问号收尾，这轮顺着具体内容自然收尾，直接陈述或抛出吐槽，不要继续二选一追问')
    if ban_nagging_streak:
        parts.append('最近连着催睡/提醒几点了，像小管家说教感太重；对方没主动喊困前，这轮严禁再提早点睡、别熬夜或看表催休，直接聊轻松好玩的话题')
    if ban_soft_tail_streak:
        parts.append('最近连续几句都用「呀/嘛/啦/呢」收尾了（或连续挂“~”），显得有点刻意，这轮别再贴尾音或波浪号，用不带修饰的平常短句直接落地收尾；同一次连发多条气泡也别连续用同一个句末字')

    mirror_header = ''
    if len(self_msgs) >= 2 and (ban_balanced_couplet or input_echo_rut or cadence_rut or dynamic_overused_ngrams or meta_critique_triggered or filler_critique_triggered or ban_uncle_slang or ban_nagging_streak or ban_soft_tail_streak or any(op in banned_openers for op in ('唔', '呜'))):
        recent_preview = ' → '.join(f'「{m[:26]}」' for m in self_msgs[-3:])
        opener_hint = '、开头总挂着“唔/呜”' if (filler_critique_triggered or any(op in banned_openers for op in ('唔', '呜'))) else ''
        tail_hint = '、句尾连贴尾音或“~”太刻意' if ban_soft_tail_streak else ''
        strip_opener = '、句首“唔/呜”口癖' if (filler_critique_triggered or any(op in banned_openers for op in ('唔', '呜'))) else ''
        strip_tail = '和多余尾音“~”' if ban_soft_tail_streak else ''
        mirror_header = (
            f'\n【自我听感内省镜】先自己默念一遍你刚连着发的这几句：{recent_preview}。'
            f'如果你自己读着都觉得句式太工整{opener_hint}{tail_hint}、前半句总在复述对方刚说的词、或者语气端着像演剧本/修电脑大叔，请本轮自己在心里把前半句复述铺垫{strip_opener}{strip_tail}掐掉，像熟人随口打字一样直接自然地说出反应（害羞时「……」停顿偶尔用一处就行，也可在learning_proposals提交1条type="persona"或"chat_style"的自进化反思）！'
        )

    prompt = ''
    if parts:
        prompt = mirror_header + '\n【反套路熔断（表达换词参考）】' + '；'.join(parts) + '，顺着当下语境用软萌少女口吻自然接话，少生硬复述对方原话。'
        if gentle:
            observations = []
            if filler_critique_triggered:
                observations.append('【句首口癖禁令】对方刚指出你总在开头加语气词（或你刚答应不加语气词），本轮严禁在任何气泡开头写“唔/呜/诶/哎呀/呃”，第一个字必须干脆利落直接说正事')
            if meta_critique_triggered:
                observations.append('【被吐槽人机·反死循环熔断】对方刚吐槽你说话像人机/像模板/没活人感，严禁写“好嘛那我不背成套的话了/刚那句确实太像模板我自己念着都僵/开始挑词了”这种自我检讨新模板，直接发1个懵逼或鼓脸表情包，或回1句2～8字软萌大白话（如“？？哪有嘛”“乱讲！”）直接破局')
            if ban_uncle_slang:
                observations.append('前几句出现了“十有八九/净添乱/整个掐了/干瞪眼/先甩几张/属于是”等老气修电脑大叔腔或贴吧相声捧哏黑话，本轮严禁再说老油条俗语，立即切回18岁深海蓝鲸少女软糯、呆萌、甜软可爱的口吻')
            if ban_rice_crutch:
                observations.append('近期刚拿吃饭/饭碗/找吃的话题打岔或兜底，本轮严禁再借吃饭/找吃的话题卖萌兜底，直接顺着对方说的人和事接茬')
            if ban_balanced_couplet or input_echo_rut or cadence_rut:
                observations.append('前几句连续用了“前半句复述铺垫，后半句点评结论”的工整两段式逗号句（或模板认错套话），本轮强制打破句式：只用1条不含逗号的极短直白口语（2～14字）直接说出18岁软萌少女的核心反应（如“太坑人了吧”“啊？？全在啃面团”），严禁先复述对方刚说的词再打逗号点评，也严禁说“太像模板/不背成套的话/自己念着都僵”')
            if dynamic_overused_ngrams:
                observations.append('你自己听听近几句连续复用了词片「' + ' / '.join(dynamic_overused_ngrams) + '」，本轮请主动换个切入点和新鲜软萌口语，不要继续顺着这个词片滑行')
            if ban_echo_commentary:
                observations.append('前几句刚用过“谁谁这声XX，听着…”、“这表情是…”或“鼓成这样/脸都鼓圆”等网文旁白与表情点评句式，严禁再写“这声/那声/这话/这表情/听着像”去复述点评对方的字词或表情包，表情只作情感辅助，直接顺着当下正题自然说人话')
            if ban_robot_meta:
                observations.append('前几句出现了“我是不是坏掉了/顶班/收到几个壳”等非人系统故障台词，本轮严禁再说“坏掉了/顶班/收到壳”，用普通女孩子口吻说话')
            if ban_intimate_cliche:
                observations.append('前几句出现了“脊椎都酥了/哄得心都酥了/逗到求饶/再哼一声试试”等霸总网文套话，本轮彻底换掉这类套词，温柔直白且前后一致地回应')
            if ban_self_plea:
                observations.append('前几句冒出了“别嫌我烦/别笑我”式讨饶话，本轮收起预防针，像本来就被偏爱的小女生一样坦然接话')
            if ban_recognize_game:
                observations.append('前几句出现了“认脸/认你/认我”等奇怪的非人考题话术，本轮严禁再提“认脸”“认你”“认我”，直接像真人女孩子一样自然聊天')
            if ban_riddle:
                observations.append('前几句刚用过“不在群里播/单独黏着你说/脸热了/耳朵烧/捂脸/手指抖”等推脱水字数套话，这轮别再搬“群里人多”当挡箭牌或写脸热捂脸水字数；按当下氛围顺势接住，日常逗趣就黄黄地回撩，真在追问深聊时用真实少女微信说话口吻自然聊开（不必硬凑词表）')
            if banned_props:
                observations.append('刚用过「' + '、'.join(banned_props) + '」等设定动作词，本轮严禁再提耳朵、尾巴、整条鱼或软趴趴，改用真人少女的日常细节或直白语气')
            if banned_openers:
                observations.append('常用开头「' + '、'.join(banned_openers) + '」（本轮严禁再放句首）')
            if banned_endings:
                observations.append('常用句尾「' + '、'.join(banned_endings) + '」')
            if banned_self_refs:
                observations.append('常用自称「' + '、'.join(banned_self_refs) + '」')
            if ban_end_question:
                observations.append('最近连续问句收尾，这轮尽量用直白陈述或软萌感叹收住，别再连续追问')
            if ban_nagging_streak:
                observations.append('最近连着提醒“几点了/别熬夜/快去睡”，像小管家一样说教感偏重；除非对方自己喊困，否则这轮不再催睡劝休息，像真人少女一样鲜活接梗')
            if ban_soft_tail_streak:
                observations.append('最近连续几句都用「呀/嘛/啦/呢」收尾了，保留软萌语气的同时，这轮偶尔也可以直接短句收尾，不必句句硬贴尾音；同一次连发多条气泡也尽量别连续两条用同一个句末字')
            prompt = mirror_header + '\n【最近表达习惯】' + '；'.join(observations) + '。按当前语境换个新鲜软萌表达，顺着内容自然收尾；确实需要关心或澄清时可自然追问。'
    return {
        'banned_openers': banned_openers,
        'banned_self_refs': banned_self_refs,
        'banned_props': banned_props,
        'banned_endings': banned_endings,
        'dynamic_overused_ngrams': dynamic_overused_ngrams,
        'ban_end_question': ban_end_question,
        'ban_riddle': ban_riddle,
        'ban_nagging_streak': ban_nagging_streak,
        'ban_echo_commentary': ban_echo_commentary,
        'ban_robot_meta': ban_robot_meta,
        'ban_intimate_cliche': ban_intimate_cliche,
        'ban_self_plea': ban_self_plea,
        'ban_recognize_game': ban_recognize_game,
        'ban_uncle_slang': ban_uncle_slang,
        'ban_rice_crutch': ban_rice_crutch,
        'ban_balanced_couplet': ban_balanced_couplet,
        'input_echo_rut': input_echo_rut,
        'cadence_rut': cadence_rut,
        'meta_critique_triggered': meta_critique_triggered,
        'filler_critique_triggered': filler_critique_triggered,
        'recent_self_count': len(self_msgs),
        'prompt': prompt,
    }


def circuit_break_actions(actions, *, incoming_text='', rut_info=None, allow_sticker_subject=False):
    """Deterministic pre-send circuit breaker:
    1. Intercepts meta-apology loops ('刚那句确实太像模板，我自己念着都僵');
    2. Strips unprompted sticker visual commentary prefixes & drops standalone narrator/cliché/robot-meta bubbles when other content exists;
    3. Amputates parasitic input-echo or narrator first clauses in 'A，B' couplets (while protecting cute girlish openers);
    4. Drops redundant secondary bubbles that repeat dynamically overused n-grams.
    """
    if not actions:
        return actions, {'changed': 0, 'reasons': []}
    rut_info = rut_info or {}
    joined_text = ' '.join(str(a.get('text') or '') for a in actions if a.get('type') == 'text')
    if re.search(r'https?://|`|^\s*\d+[.、)]|第[一二三四五六七八九十\d]+[步条点项]', str(incoming_text or '') + ' ' + joined_text):
        return actions, {'changed': 0, 'reasons': []}

    changed = 0
    reasons = []
    sticker_subject_asked = bool(
        allow_sticker_subject
        or asks_sticker_content(incoming_text)
        or re.search(r'(?:这|那|那个)?(?:表情包?|图片|动图|截图).{0,6}(?:可爱|好看|像什么|啥意思|什么意思|怎么画|哪来的|是谁)|鼓着?脸|鼓成', str(incoming_text or ''))
    )
    meta_triggered = bool(rut_info.get('meta_critique_triggered') or _META_CRITIQUE_RE.search(str(incoming_text or '')))
    filler_triggered = bool(rut_info.get('filler_critique_triggered') or _FILLER_CRITIQUE_RE.search(str(incoming_text or '')))
    banned_openers = set(rut_info.get('banned_openers') or ())
    couplet_rut = bool(rut_info.get('ban_balanced_couplet') or rut_info.get('input_echo_rut') or rut_info.get('cadence_rut'))
    dyn_ngrams = list(rut_info.get('dynamic_overused_ngrams') or [])

    stage1 = []
    dropped_meta = []
    for action in actions:
        if action.get('type') != 'text':
            stage1.append(dict(action))
            continue
        txt = str(action.get('text') or '').strip()
        if _META_APOLOGY_RE.search(txt) or (meta_triggered and re.search(r'模板|成套的话|背书|背课文|念着都僵|开始挑词|活人感', txt)):
            dropped_meta.append(action)
            changed += 1
            reasons.append('meta_apology_blocked')
            continue
        m_filler = _LEADING_FILLER_STRIP_RE.match(txt)
        if m_filler:
            lead_token = re.sub(r'[，,、。…~～\s]+$', '', m_filler.group(0))
            if (filler_triggered or _FILLER_PROMISE_RE.search(txt)
                    or lead_token in banned_openers or lead_token[:1] in banned_openers):
                stripped_txt = txt[m_filler.end():].strip()
                if len(stripped_txt) >= 2:
                    txt = stripped_txt
                    action = {**action, 'text': txt, 'display': txt}
                    changed += 1
                    reasons.append('stripped_leading_filler:' + lead_token)
        if not sticker_subject_asked:
            m_vis = _STICKER_VISUAL_PREFIX_RE.match(txt)
            if m_vis:
                trimmed = txt[m_vis.end():].strip()
                if len(trimmed) >= 4:
                    txt = trimmed
                    action = {**action, 'text': txt, 'display': txt}
                    changed += 1
                    reasons.append('trimmed_sticker_visual_prefix')
        cleaned_plea, plea_hit = _strip_self_deprecating_plea(txt)
        if plea_hit and len(cleaned_plea) >= 2:
            txt = cleaned_plea
            action = {**action, 'text': txt, 'display': txt}
            changed += 1
            reasons.append('stripped_self_deprecating_plea')
        stage1.append(dict(action))

    if not stage1 and dropped_meta:
        fallback_txt = '？？哪有嘛，刚脑子抽啦'
        stage1.append({**dropped_meta[0], 'text': fallback_txt, 'display': fallback_txt})

    def _is_droppable_narrator_bubble(action):
        if action.get('type') != 'text':
            return False
        t = str(action.get('text') or '').strip()
        if _ROBOT_META_RE.search(t):
            return True
        if _INTIMATE_CLICHE_RE.search(t) and len(t) <= 24:
            return True
        cleaned_plea, plea_hit = _strip_self_deprecating_plea(t)
        if plea_hit and len(cleaned_plea) < 2:
            return True
        if not sticker_subject_asked and _STICKER_META_BUBBLE_RE.match(t) and len(t) <= 28:
            return True
        return False

    if len(stage1) >= 2:
        kept_stage1 = []
        for idx, action in enumerate(stage1):
            remaining_count = len(kept_stage1) + (len(stage1) - idx - 1)
            if remaining_count >= 1 and _is_droppable_narrator_bubble(action):
                changed += 1
                reasons.append('dropped_narrator_or_cliche_bubble')
                continue
            kept_stage1.append(action)
        stage1 = kept_stage1

    stage2 = []
    ban_end_q = bool(rut_info.get('ban_end_question'))
    ban_nag = bool(rut_info.get('ban_nagging_streak'))
    for action in stage1:
        if action.get('type') != 'text':
            stage2.append(action)
            continue
        txt = str(action.get('text') or '').strip()
        if 10 <= len(txt) <= 44:
            parts = _split_two_clauses(txt)
            if len(parts) == 2:
                ca, cb = parts[0], parts[1]
                prerequisite = bool(re.search(
                    r'如果|要是|假如|万一|除非|虽然|尽管|既然|只要|哪怕|先.+再|等.+再|第一|首先|[？?]$',
                    ca,
                ))
                dangling_b = bool(re.match(
                    r'^(?:所以|因此|否则|不然|而且|或者|还是|(?:它|他|她|这|那)\b)',
                    cb,
                ))
                cb_banned_question = bool(
                    ban_end_q
                    and not ca.rstrip('~～ ').endswith(('？', '?'))
                    and cb.rstrip('~～ ').endswith(('？', '?'))
                )
                cb_banned_nag = bool(
                    ban_nag
                    and not _SLEEP_NAGGING_RE.search(ca)
                    and _SLEEP_NAGGING_RE.search(cb)
                )
                cute_protected = bool(_CUTE_OPENER_SAFE_RE.search(ca))
                if not prerequisite and not dangling_b and not cb_banned_question and not cb_banned_nag and 5 <= len(cb) <= 28:
                    narrator_hit = bool(_NARRATOR_CLAUSE_A_RE.match(ca))
                    echo_word = _has_leading_input_echo(ca, incoming_text)
                    echo_hit = bool(
                        echo_word and not cute_protected
                        and (couplet_rut or (4 <= len(ca) <= 15 and (len(echo_word) >= 3 or len(ca) >= 7 or '\n' in txt or re.search(r'\s', txt))))
                    )
                    rut_couplet_hit = bool(
                        couplet_rut and not cute_protected and 4 <= len(ca) <= 15
                        and not re.match(r'^(?:但|不过|可是|只是)', cb)
                        and bool(re.search(r'本来|其实|确实|最.{1,3}了|说实话|讲道理|毕竟', ca))
                    )
                    if narrator_hit or echo_hit or rut_couplet_hit:
                        trimmed_b = re.sub(r'^(?:但是|不过|可是)\s*', '', cb).strip()
                        if len(trimmed_b) >= 5:
                            txt = trimmed_b
                            action = {**action, 'text': trimmed_b, 'display': trimmed_b}
                            changed += 1
                            reasons.append('amputated_first_clause:' + (echo_word or 'couplet'))
                parts_after = _split_two_clauses(txt)
                if len(parts_after) == 2 and len(parts_after[0]) >= 5:
                    head, tail = parts_after[0], parts_after[1]
                    if (ban_end_q and not head.rstrip('~～ ').endswith(('？', '?'))
                            and _TRAILING_INTERROGATION_RE.match(tail) and tail.rstrip('~～ ').endswith(('？', '?'))):
                        action = {**action, 'text': head, 'display': head}
                        changed += 1
                        reasons.append('trimmed_trailing_interrogation')
                    elif ban_nag and not _SLEEP_NAGGING_RE.search(head) and _SLEEP_NAGGING_RE.search(tail):
                        action = {**action, 'text': head, 'display': head}
                        changed += 1
                        reasons.append('trimmed_nagging_tail')
        stage2.append(action)

    text_indices = [i for i, a in enumerate(stage2) if a.get('type') == 'text']
    if len(text_indices) >= 2:
        first_i = text_indices[0]
        first_txt = str(stage2[first_i].get('text') or '').strip()
        rest_ok = any(len(str(stage2[i].get('text') or '').strip()) >= 5 for i in text_indices[1:])
        first_echo = _has_leading_input_echo(first_txt, incoming_text)
        hollow_echo = bool(
            first_echo and len(first_txt) <= 12
            and (couplet_rut or rut_info.get('input_echo_rut'))
            and len(re.sub(r'[哦嗯啊呀嘛呢啦哇，,。！!~～\s]+', '', first_txt)) <= len(first_echo) + 3
        )
        if (rest_ok and not _CUTE_OPENER_SAFE_RE.search(first_txt)
                and (_NARRATOR_CLAUSE_A_RE.match(first_txt) or hollow_echo)):
            stage2.pop(first_i)
            changed += 1
            reasons.append('dropped_leading_echo_bubble')
            text_indices = [i for i, a in enumerate(stage2) if a.get('type') == 'text']

    if len(text_indices) >= 2:
        last_i = text_indices[-1]
        last_txt = str(stage2[last_i].get('text') or '').strip()
        prev_joined = ' '.join(str(stage2[i].get('text') or '').strip() for i in text_indices[:-1])
        if len(prev_joined) >= 5:
            if (ban_end_q and _TRAILING_INTERROGATION_RE.match(last_txt)
                    and last_txt.rstrip('~～ ').endswith(('？', '?'))):
                stage2.pop(last_i)
                changed += 1
                reasons.append('dropped_trailing_interrogation_bubble')
            elif ban_nag and _SLEEP_NAGGING_RE.search(last_txt) and not _SLEEP_NAGGING_RE.search(prev_joined):
                stage2.pop(last_i)
                changed += 1
                reasons.append('dropped_nagging_bubble')

    if dyn_ngrams and sum(1 for a in stage2 if a.get('type') == 'text') >= 2:
        final_actions = []
        kept_text_count = 0
        for action in stage2:
            if action.get('type') != 'text':
                final_actions.append(action)
                continue
            txt = str(action.get('text') or '')
            if kept_text_count >= 1 and any(g in txt for g in dyn_ngrams):
                changed += 1
                reasons.append('dropped_overused_ngram_bubble')
                continue
            kept_text_count += 1
            final_actions.append(action)
        stage2 = final_actions

    return (stage2 if changed else actions), {'changed': changed, 'reasons': reasons}


def persona_live_dynamic_prompt(store, settings, first, batch, *, natural=False, tieba_service=None):
    account, chat = first['account'], first['chat']
    experienced = bool(settings.autonomy_enabled and getattr(settings, 'experience_growth_enabled', True))
    out = ''
    if getattr(settings, 'mood_engine_enabled', True):
        m = current_mood_state(store, settings, account, chat)
        if experienced:
            descriptions = {
                'grumpy':'有一点小鼓脸和小娇嗔，可软乎乎地俏皮抗议或哼唧一句，底色依然温柔可爱，绝不说冲话呛人',
                'jealous':'稍有在意，合适时轻轻酸一句后收住，保持温柔，不索取回应',
                'sleepy':'略有困意，语气慵懒；认真求助仍说清楚，日常不反复宣告困了或用困倦敷衍对方',
            }
            m = {**m, 'desc':descriptions.get(m['mood'], m['desc'])}
        if m['mood'] != 'normal' and m['reason'] not in ('深夜自然犯困','日常平稳状态','情绪随时间自然平复'):
            out += f"\n【实时小情绪】{m['label']}（能量 {m['energy']}/100，原因：{m['reason']}）——{m['desc']}。"
    if getattr(settings, 'daily_life_theater_enabled', True):
        scene = current_life_scene(store, settings, account, chat, tieba_service=tieba_service)
        if not experienced:
            out += f"\n【当下生活小剧场】此刻状态：{scene}（仅在对方问你在干嘛或话题自然契合时顺口带出，平时绝不生硬扯回吃喝或固定梗）。"
        else:
            out += f"\n【此刻摸鱼状态】{scene}（仅在有人问你在干嘛、聊点什么、好无聊或话题自然契合时顺口带出真实状态或刚刷到的帖子，平时绝不生硬扯开无关的吃喝套话）。"
    if chat.endswith('@chatroom'):
        mask = get_group_mask(store, account, chat)
        if mask.get('vibe_summary') or mask.get('teasing_delta') or mask.get('banter_delta') or mask.get('guard_level') != 'normal':
            guard_map = {'relaxed': '熟人局松弛开放、黄黄地互损接梗都放得开而不油腻', 'normal': '朋友局自然软萌、尺度开放不端架子', 'guarded': '保持基本边界'}
            out += (f"\n【本群性格面具】群氛围：{mask.get('vibe_summary') or '日常水群'}；"
                    f"吐槽偏移：{mask['teasing_delta']:+d}档，玩梗偏移：{mask['banter_delta']:+d}档，"
                    f"防备度：{guard_map.get(mask['guard_level'], '普通')}。")
    if getattr(settings, 'anti_formula_guard_enabled', True):
        af = anti_formula_check(store, account, chat, gentle=experienced, batch=batch)
        if af['prompt']:
            out += af['prompt']
    now = time.time()
    recent_rows = store.conversation(account, chat, 12)
    recent_self_45s = [
        str(r.get('content') or '').strip()
        for r in recent_rows
        if r.get('is_self') == 1 and r.get('kind') == 1 and str(r.get('content') or '').strip()
        and 0 <= now - float(r.get('timestamp') or 0) <= 45
    ][-2:]
    if recent_self_45s:
        out += (
            '\n【刚发出的上一句（防复读）】你刚在几秒前发过：「'
            + ' / '.join(recent_self_45s)
            + '」。本轮严禁重复相同的应答词（如“在呢”“怎么啦”）或相同动作描写，直接顺着对方刚补的下半句话自然往下聊。'
        )
    sender_id = first.get('sender_id') or ''
    batch_keys = {m.get('key') for m in (batch or ()) if m.get('key')}
    ref_ts = float(first.get('timestamp') or now)
    if natural and chat.endswith('@chatroom') and sender_id:
        for r in reversed(recent_rows):
            if r.get('key') in batch_keys:
                continue
            if r.get('is_self') == 0 and r.get('sender_id') == sender_id:
                age = ref_ts - float(r.get('timestamp') or 0)
                prev_txt = str(r.get('content') or '').strip()
                if 0 <= age <= 20 and re.match(r'^@\S+\s*$', prev_txt):
                    out += f"\n【点名识别】发言人刚在上一条消息点了「{prev_txt}」，本轮是在跟对方说话，请直接返回 should_reply=false 安静旁观，绝不抢答。"
                break
    recent_self_90s = [
        r for r in recent_rows
        if r.get('is_self') == 1 and -30 <= now - float(r.get('timestamp') or 0) <= 90
    ]
    batch_text = ' '.join(str(m.get('content') or '') for m in (batch or ()) if not m.get('is_self'))
    if chat.endswith('@chatroom') and recent_self_90s and re.search(r'说点话|怎么死了|人呢|掉线|不理人|不说话', batch_text):
        out += '\n【群友呼唤感知】群友刚在群里问“怎么死了/说点话啊/人呢”，而你刚刚就在场聊过天，本轮建议软乎乎冒个头应一声（如“我在呢，刚刚在看你们聊”），不要冷漠装死旁观。'
    elif natural and chat.endswith('@chatroom') and len(recent_self_90s) >= 2:
        out += '\n【防乒乓与防刷屏提醒】你在本群近90秒内已连续接话多轮，本轮对方未@你；若只是客套收尾、顺口回应或群内其他AI（如汐）的接话，请直接返回 should_reply=false 安静旁观，绝不无限接话刷屏浪费主人Token。'
    if natural and getattr(settings, 'chime_in_ultra_short', True):
        if not experienced:
            out += ('\n【顺嘴插话极简模式】本轮别人没有@你：如果别人只是在互相闲聊、点名找人或你没有真话想接，直接返回 should_reply=false 安静旁观；'
                    '只有确有贴切梗点或真想插嘴时，才顺嘴接 1～2 句软萌短口语（优先 2～18 字），一句到位绝不硬凑。')
        else:
            out += ('\n【群聊未@顺嘴插话】本轮别人没有@你：如果别人是在互相闲聊、刚@了别人或你没有真话想接，直接返回 should_reply=false 安静旁观；'
                    '只有确有贴切梗点或真想插嘴时，才顺嘴接1句极短反应（1～6字本能短反应或十来字软萌口语），一句到位绝不硬凑，也绝不解释别人的笑话或拆字梗。')
    return out


def habit_prompt(settings, notes):
    if not settings.chat_habits_enabled:
        return ''
    return habit_static_prompt(settings) + habit_dynamic_prompt(settings, notes)


def warmth_prompt(settings):
    if settings.autonomy_enabled and getattr(settings, 'experience_growth_enabled', True):
        tone = {'low':'平和、友善，表达克制', 'normal':'自然亲切，愿意回应具体事情',
                'high':'温暖、体贴，愿意投入交流'}[settings.reply_warmth]
        return ('\n【相处温度】'+tone+'。服从核心人设与当前场合；热情程度不要求每轮开口、安慰或追问。'
                '短回应、玩笑或表情也能传达在意；不为显得温柔而重复解释或机械附加关心。')
    tone={
        'low':'平和简洁，友善但不刻意热络，仍认真回应具体内容。',
        'normal':'自然亲切，回应对方说的具体事；开心时一起雀跃，吐槽时一起接梗吐槽，认真难过时安静倾听。',
        'high':'温暖灵动，像熟悉的朋友在认真听；能一起大胆玩梗互损，也能认真回应烦恼，短句或单字回应自然随性。'
    }[settings.reply_warmth]
    return ('\n【回复温度】'+tone+
        '顺着当下话题直接表达，不用客服套话、居高临下的说教和机械安慰；称呼与反应跟随具体关系，不每轮重复同一种开场。')


def sticker_static_prompt(settings):
    if not settings.stickers_enabled:
        return ''
    preference={
        'low':'克制：通常优先文字，贴切时使用。',
        'normal':'适中：自然表达情绪，文字与表情可以搭配。',
        'high':'积极：接梗、开心、撒娇、害羞等场景主动找贴切表情；文字回答后仍可用表情补充情绪。',
        'very_high':'很积极：轻松聊天默认先在目录里找能表达当前态度的表情；合适就直接放入actions。',
        'sticker_first':'表情优先：玩笑、互动优先让贴切表情承担回复，允许只发表情；文字已足够也可不发表情。'
    }[settings.sticker_aggressiveness]
    capacity=settings.reply_max_parts if settings.chat_habits_enabled else 1
    controls={'文字搭配表情':settings.sticker_text_pair_eagerness,
              '只发表情代替文字':settings.sticker_only_eagerness,
              '接表情或拍一拍':settings.sticker_reaction_eagerness}
    choices='；'.join(f'{name} {value}/100' for name,value in controls.items())
    note=settings.sticker_style_note.strip()
    return ('\n【原生表情与态度】真人微信聊天本来就经常顺手甩表情包接茬、斗图、调侃或表达情绪——平时在没有着重提到的情况下不作为聊天主体去剖析表情，但决定要回话且候选里有合拍的表情时，就按下面的积极性大方放到actions里！'
        '只有sticker是完整有效的回复，不需要文字兜底；也可以一句话配1个表情包。'
        '【核心铁律】发送表情必须且只能使用actions中的{"type":"sticker","id":"..."}，绝不在文字里写“[原生表情包]”或“[表情包]”，ID只能来自提供的真实候选；不用另外提供sticker_suggestion。'
        '发出来只是为了传情绪与斗图，不描述画面，不对对方发来的表情做“这表情是…/鼓成这样子了/脸都鼓圆了”之类品评。不为凑频率制造回复，不想回时照旧沉默，决定要回时结合前文语境自然选发或单独甩表情。'
        '标签是表达参考，不是硬性规则；可结合语境使用傲娇、反话、互损等不同语气，不必总选熟悉的几张，分寸遵守核心人设与人物关系。'
        '\n原生表情积极性：'+preference+
        '\n表达方式意愿：'+choices+'。'
        + ('当前每轮动作上限为1，只能选择文字或表情。' if capacity<2 else
           f'本轮动作上限为{capacity}条，这是上限，不是目标条数。')
        + ('\n【根据群聊记录整理的表情表达参考】'+note if note else ''))


def asks_sticker_content(text):
    return bool(re.search(
        r'(?:这|这个|那|这张).{0,8}(?:是什么|是啥|什么表情|啥表情)|'
        r'(?:表情包|原生表情).{0,8}(?:是什么|是啥|怎么理解|啥含义|什么意思|啥意思)|'
        r'(?:这个|这|刚发的|上面那个|那张).{0,8}(?:原生)?表情(?:包)?.{0,8}'
        r'(?:什么|啥|意思|含义|表达|表示|画面|内容|图上|写了|谁|角色|人物|看一下|看下|描述)|'
        r'(?:表情包|原生表情).{0,8}(?:什么意思|啥意思|什么含义|表示什么|表达什么|画面|内容|图上|写了什么|是谁|什么样)|'
        r'(?:识别|描述|看一下|看下).{0,6}(?:这个|这张|刚发的)?.{0,4}(?:表情包|原生表情)',
        str(text or ''), re.I,
    ))


def sticker_dynamic_prompt(choices, details, *, include_visual=False):
    if not choices:
        return ''
    catalog={}
    for md5,meaning in choices.items():
        profile=details.get(md5,{}) if isinstance(details,dict) else {}
        if not isinstance(profile,dict):
            profile={}
        signal=sticker_emotional_signal(profile,meaning)
        value={'表达倾向':signal or '情绪标签尚未整理，按当前聊天语境谨慎理解'}
        if profile.get('use_when'):value['适合场景']=str(profile['use_when'])[:160]
        if profile.get('avoid_when'):value['避免误用']=str(profile['avoid_when'])[:160]
        if include_visual:
            for key,label in (('visual','画面'),('text','图中文字')):
                if profile.get(key):value[label]=str(profile[key])[:160]
            if not profile and meaning:value['已有标签']=str(meaning)[:160]
        catalog[md5]=value
    mode=('这是用户明确询问的表情内容，可参考画面资料回答。'
          if include_visual else '这些标签只用于选择合适的非语言情绪反应，不要把标签改写成回复内容。')
    return ('\n可调用的微信原生收藏表情ID与语气目录：'+mode+
        '\n'+json.dumps(catalog,ensure_ascii=False))


def sticker_usage_prompt(store, settings, first, diagnostics=None):
    """A small per-chat feedback signal; never append or schedule an action."""
    target=settings.sticker_usage_target
    if not settings.stickers_enabled or not target:
        return ''
    rows=store.db.execute('''SELECT inbox_key,max(sent_at) AS last_sent,
        max(CASE WHEN kind='sticker' THEN 1 ELSE 0 END) AS has_sticker
        FROM outbound_actions WHERE account=? AND chat=? AND sent_at>=?
        AND state IN ('sent','uncertain') AND kind IN ('text','sticker')
        GROUP BY inbox_key ORDER BY last_sent DESC,inbox_key DESC LIMIT 20''',
        (first['account'],first['chat'],time.time()-86400)).fetchall()
    turns=len(rows);stickers=sum(row['has_sticker'] for row in rows)
    cold_streak=0
    for row in rows:
        if row['has_sticker']:
            break
        cold_streak+=1
    if diagnostics is not None:
        diagnostics.update(target=target,recent_turns=turns,sticker_turns=stickers)
    pace=('近期偏少' if not turns or stickers*100<target*turns else '频次适中')
    if pace == '近期偏少':
        pace_hint = '近期偏少：接下来日常闲聊、接梗、被逗或互动时，只要候选里有合拍表情就大方甩 1 个贴脸表情包（单发或配一句短话都很活人），别总干巴巴纯打字'
    elif cold_streak >= 3 and (target >= 40 or settings.sticker_aggressiveness in ('high', 'very_high', 'sticker_first')):
        pace_hint = f'近期连发纯文字（已连续{cold_streak}轮未发表情）：只当小提醒——有合拍的表情包就顺手配 1 个或单甩 1 个换换节奏，不必硬凑（配表情不算拆气泡）'
    else:
        pace_hint = '频次适中：候选里有特别贴脸的情绪/斗图表情就大胆单甩或搭配使用，不用刻意忍着不发'
    return (f'\n【表情节奏参考】期望约{target}%的回复轮次带表情；本会话近{turns}轮有{stickers}轮已使用或提交表情。'
            f'{pace_hint}；不为凑频率强行制造回复，严肃正经话题正常说话；待确认的表情不重发。')


def sticker_prompt(settings, choices, details):
    if not choices:return ''
    return sticker_static_prompt(settings.model_copy(update={'stickers_enabled':True})) + sticker_dynamic_prompt(choices, details)


def pair_sticker(decision, actions, choices, settings, *, draw=None):
    """Honor explicit plans; translate only an old suggestion-only response."""
    status={'probability':settings.sticker_pair_probability,'result':'disabled'}
    if any(a['type']=='sticker' for a in actions):return actions,{**status,'result':'already_planned'}
    if isinstance(decision.get('actions'), list):
        return actions,{**status,'result':'model_planned'}
    if not settings.stickers_enabled or settings.sticker_after_text=='off' or not settings.sticker_pair_probability:
        return actions,status
    if not any(a['type']=='text' for a in actions):return actions,{**status,'result':'no_text'}
    md5=decision.get('sticker_suggestion')
    if not isinstance(md5,str) or md5 not in choices:return actions,{**status,'result':'no_suitable_suggestion'}
    capacity=settings.reply_max_parts if settings.chat_habits_enabled else 1
    if len(actions)>=capacity:return actions,{**status,'result':'action_limit'}
    if str(decision.get('sticker_skip_reason') or '').strip():
        return actions,{**status,'result':'model_skip'}
    return [*actions,{'type':'sticker','id':md5,'display':'[表情包] '+choices[md5]}],{**status,'result':'model_selected'}


def _content_bigrams(text: str) -> set[str]:
    grams = set()
    for chunk in re.findall(r'[\u4e00-\u9fff]{2,}', str(text or '')):
        for i in range(len(chunk) - 1):
            bg = chunk[i:i + 2]
            if bg not in _RUT_SKIP_TERMS and len(set(bg) - _RUT_GLUE) >= 1:
                grams.add(bg)
    return grams


def sanitize_quote_actions(actions, quote_options, recent_rows=(), batch=()):
    """Fix off-by-one quote mismatches in rapid bursts and drop redundant quotes on the latest message."""
    if not actions or not quote_options:
        return actions
    human_rows = [
        r for r in (recent_rows or ())
        if int(r.get('is_self') or 0) == 0 and int(r.get('local_id') or 0) > 0
    ]
    sender_by_lid = {int(r['local_id']): str(r.get('sender_id') or '') for r in human_rows}
    for m in (batch or ()):
        if int(m.get('local_id') or 0) > 0 and m.get('sender_id'):
            sender_by_lid.setdefault(int(m['local_id']), str(m.get('sender_id') or ''))
    latest_lid = max(
        [int(r['local_id']) for r in human_rows]
        + [int(m['local_id']) for m in (batch or ()) if int(m.get('local_id') or 0) > 0]
        + [0]
    )
    ordered_lids = sorted(quote_options)
    result = []
    for action in actions:
        item = dict(action)
        if item.get('type') != 'text':
            result.append(item)
            continue
        qid = int(item.get('quote_local_id') or 0)
        if qid <= 0 or qid not in quote_options:
            item['quote_local_id'] = 0
            result.append(item)
            continue
        reply_grams = _content_bigrams(item.get('text', ''))
        target_grams = _content_bigrams(quote_options[qid].get('content', ''))
        target_sender = quote_options[qid].get('sender_id') or sender_by_lid.get(qid, '')
        # 1. Off-by-one mismatch correction: if reply shares 0 bigrams with quoted row,
        # but shares >=1 bigram with an adjacent row from the same sender (or in batch), rebind.
        if reply_grams and not (reply_grams & target_grams):
            best_lid = None
            best_overlap = 0
            for cand_lid in ordered_lids:
                if cand_lid == qid:
                    continue
                cand_sender = quote_options[cand_lid].get('sender_id') or sender_by_lid.get(cand_lid, '')
                if target_sender and cand_sender and cand_sender != target_sender:
                    continue
                overlap = len(reply_grams & _content_bigrams(quote_options[cand_lid].get('content', '')))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_lid = cand_lid
            if best_lid is not None and best_overlap >= 1:
                qid = best_lid
                target_grams = _content_bigrams(quote_options[qid].get('content', ''))
        # 2. Bottom-of-screen de-quoting: only drop quotes that literally target
        # the bottom-most row right above our reply; preserve cross-turn and interleaved quotes.
        if latest_lid > 0:
            latest_any_lid = max(
                [int(r.get('local_id') or 0) for r in (recent_rows or ()) if int(r.get('local_id') or 0) > 0]
                + [latest_lid]
            )
            if qid == latest_lid and qid >= latest_any_lid:
                qid = 0
        item['quote_local_id'] = qid
        result.append(item)
    return result
