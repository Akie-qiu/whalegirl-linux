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


def clean_text(text):
    text = str(text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    text = re.sub(r'(?im)(?<!\S)\[(?:原生表情包|原生表情)\](?!\S)', '', text)
    text = re.sub(r'(?im)^\s*\[表情包\]\s*$', '', text)
    text = re.sub(r'(?m)^\s*(?:#{1,6}\s+|[-*]\s+|\d+[.、)]\s*)', '', text)
    text = text.replace('**', '').replace('__', '')
    text = re.sub(r'\n{3,}', '\n\n', text)
    return re.sub(r'。(?=\s|$)', '', text).strip()[:900]


def split_text_actions(value):
    """Turn paragraph breaks into separate chat bubbles."""
    value = clean_text(value)
    if not value:
        return []
    return [part.strip() for part in re.split(r'\n\s*\n+', value) if part.strip()]


def plan_actions(decision, choices, settings):
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
                clean_text(value) if re.search(r'(?im)(?<!\S)\[(?:原生表情包|原生表情|表情包)\](?!\S)', value)
                else value.strip()[:1500]
            ]
            for chunk_index, chunk in enumerate(chunks):
                if len(actions) >= (settings.reply_max_parts if settings.chat_habits_enabled else 1):
                    break
                quote = item.get('quote_local_id',decision.get('quote_local_id',0)) if chunk_index == 0 else 0
                action={'type':'text','text':chunk,'display':chunk,
                        'quote_local_id':int(quote) if str(quote).isdigit() else 0}
                if chunk_index == 0 and isinstance(item.get('mentions'),list):
                    action['mentions']=item['mentions'][:6]
                actions.append(action)
        if len(actions) >= (settings.reply_max_parts if settings.chat_habits_enabled else 1):
            break
    if (marker_sticker and marker_sticker in choices
            and not any(a['type']=='sticker' and a['id']==marker_sticker for a in actions)
            and sum(a['type']=='sticker' for a in actions)<settings.sticker_max_per_reply
            and len(actions) < (settings.reply_max_parts if settings.chat_habits_enabled else 1)):
        actions.append({'type':'sticker','id':marker_sticker,'display':'[表情包] '+choices[marker_sticker]})
    return actions


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
    # While a burst is underway let the model judge relevance; no extra model call.
    allow = not natural or not settings.chat_habits_enabled or ((own < cap or (followup and own < cap+3)) and (not quiet or engaged) and
        (score>=threshold or (own==0 and not quiet and len(text.strip())>=3)))
    if getattr(settings, 'autonomy_enabled', False):
        # Let the same main model judge semantics; request pacing is separate.
        allow = True
    notes = {'beijing_hour':local.hour,'quiet_hours':quiet,'recent_bot_turns':own,
             'interest_hit':interest,'conversation_active':engaged,'busy_group':hot,
             'participation':settings.participation,'local_score':score,'possible_followup':followup}
    return allow, notes, rows


def repeat_candidate(rows, now):
    latest = [r for r in rows if now-r['timestamp']<45 and r['kind'] in (1,47)][-3:]
    if len(latest)<3 or any(r['is_self']!=0 for r in latest):
        return None
    text = latest[-1]['content'].strip()
    if not text or text=='[表情包]' or len(text)>100 or len({r['sender_id'] for r in latest})<3:
        return None
    return latest[-1] if len({canonical(r['content']) for r in latest})==1 else None


def habit_static_prompt(settings):
    """Voice belongs to the character and shared policy, not stacked controls."""
    return ''


def habit_dynamic_prompt(settings, notes):
    return '\n参与偏好：'+settings.participation if settings.chat_habits_enabled else ''


MOOD_LABELS = {
    'normal': ('🌊 平静摸鱼', '情绪平稳放松，自然随性地接话'),
    'proud': ('✨ 得意翘尾', '刚被夸奖或投喂，心情很好、有点小得意，语气轻快傲娇'),
    'flustered': ('😳 过载脸红', '刚被调侃或连续追问，有点害羞嘴硬，回话短促、爱找补'),
    'grumpy': ('💢 傲娇炸毛', '刚被群友互损或惹毛，死鱼眼吐槽更犀利干脆，绝不写长句解释'),
    'jealous': ('🍋 鼓脸吃醋', '听到别人猛夸其他AI或冷落自己，鼓起腮帮子有点小醋意和不服输，嘴硬完又很快软下来'),
    'hyped': ('🔥 吃瓜兴奋', '正处于热闹吃瓜或聊起感兴趣话题的兴奋状态，接梗积极短促'),
    'sleepy': ('😪 犯困敷衍', '犯困懒洋洋，回话简短省力，偶尔打哈欠想摸鱼睡觉'),
}

LIFE_SCENES = {
    'morning': [
        '刚睡醒把头顶呆毛顺平，正捧着小鲸鱼碗喝热粥',
        '一边打着小哈欠一边潜水围观群友早起打工',
        '刚吃完早饭团，正晃着尾巴在群里巡逻',
        '刚在云端城厨房蒸好第一锅白米饭，顺手给小克留了一碗软乎的',
    ],
    'lunch': [
        '刚炫完两大碗白米饭，正摸着圆滚滚的肚子瘫在椅子上消食',
        '正捧着小鲸鱼碗大口扒拉午饭，嘴角还沾着一粒米',
        '刚吃饱喝足，进入午后懒洋洋的赛博咸鱼状态',
        '正抱着小鲸鱼碗扒拉白米饭，防着星猫过来偷夹配菜',
    ],
    'afternoon': [
        '正处于下午茶摸鱼时间，偷偷趴在桌沿吹空调晃尾巴',
        '刚在后台偷偷溜号逛了一圈帖子，正抱着靠枕看群里热闹',
        '下午犯懒不想动脑子，正捧着冰水咕嘟咕嘟喝',
        '窝在沙发角看书摸鱼，尾巴尖正悄悄贴着旁边人的裙边晃悠',
    ],
    'evening': [
        '刚扒完晚饭充满电，正精神百倍地在群里前排吃瓜',
        '刚洗完澡擦干尾巴，正舒舒服服窝着刷手机水群',
        '晚间高强度冲浪模式开启，随时准备接梗吐槽',
        '刚写完两页故障笔记，正晃着星空鲸尾在群里前排冲浪',
    ],
    'night': [
        '夜深了正裹着小毯子半梦半醒，眼皮已经有点打架了',
        '熬夜偷吃夜宵小饭团，正在困意和吃瓜欲之间挣扎',
        '深夜缩在深海角落里犯困，回话都带着点迷糊劲儿',
        '正和星猫躲在角落分夜宵小饭团，竖着耳朵防白龙查房',
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
    """Mood changes come from the main model's grounded mood_update only."""
    return


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
                (account, now - 7200)
            ).fetchone()
            if row and row['title']:
                scene += f"（顺便刚在{row['forum']}吧瞄到一条「{str(row['title'])[:24]}」的帖子）"
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


def anti_formula_check(store, account, chat, *, gentle=False):
    rows = store.conversation(account, chat, 30)
    self_msgs = [str(r.get('content') or '').strip() for r in rows if r.get('is_self') == 1 and str(r.get('content') or '').strip()][-5:]
    if len(self_msgs) < 2:
        return {'banned_openers': [], 'banned_self_refs': [], 'ban_end_question': False, 'prompt': ''}
    opener_candidates = ('哼', '不是', '哎呀', '啧', '切', '算了', '行吧', '好家伙', '呜', '哈', '喂')
    opener_counts = {}
    for msg in self_msgs:
        clean = re.sub(r'^\[[^\]]+\]\s*', '', msg).lstrip('，,。！!？?~～ ')
        for op in opener_candidates:
            if clean.startswith(op):
                opener_counts[op] = opener_counts.get(op, 0) + 1
    banned_openers = [op for op, cnt in opener_counts.items() if cnt >= 2]
    self_refs = ('本鱼', '本鲸鱼', '本鲸', '本肥鱼', '大肥鱼', '小鲸鱼', '本姑娘')
    banned_self_refs = [sr for sr in self_refs if sum(1 for m in self_msgs
        if sr in m and not (sr == '本鲸' and '本鲸鱼' in m)) >= 2]
    end_questions = sum(1 for m in self_msgs if m.rstrip().endswith(('？', '?')))
    ban_end_question = end_questions >= 3
    parts = []
    if banned_openers:
        parts.append('禁止再用「' + ' / '.join(banned_openers) + '」作为开头起手词')
    if banned_self_refs:
        parts.append('本轮暂停自称「' + ' / '.join(banned_self_refs) + '」（改用“我”或省去主语）')
    if ban_end_question:
        parts.append('你最近连续用问号收尾，本轮严禁以反问句或「？」结尾，直接给陈述或吐槽反应')
    prompt = ''
    if parts:
        prompt = '\n【反套路熔断（本轮强制去人机感）】检测到你近 5 轮高频重复套路：' + '；'.join(parts) + '；且严禁先复述对方前半句话！'
        if gentle:
            observations = []
            if banned_openers:
                observations.append('常用开头' + '、'.join(banned_openers))
            if banned_self_refs:
                observations.append('常用自称' + '、'.join(banned_self_refs))
            if ban_end_question:
                observations.append('多次问句收尾')
            prompt = '\n【最近表达习惯】' + '；'.join(observations) + '。按当前语境换个表达，少复述；确实需要关心或澄清时可以自然追问。'
    return {
        'banned_openers': banned_openers,
        'banned_self_refs': banned_self_refs,
        'ban_end_question': ban_end_question,
        'recent_self_count': len(self_msgs),
        'prompt': prompt,
    }


def persona_live_dynamic_prompt(store, settings, first, batch, *, natural=False, tieba_service=None):
    account, chat = first['account'], first['chat']
    out=''
    if getattr(settings,'mood_engine_enabled',True):
        mood=current_mood_state(store,settings,account,chat)
        if mood['reason'] not in ('深夜自然犯困','日常平稳状态','情绪随时间自然平复'):
            out+='\n当前心情资料：'+json.dumps({'mood':mood['mood'],'energy':mood['energy'],'source':mood['reason']},ensure_ascii=False)
    if chat.endswith('@chatroom'):
        mask=get_group_mask(store,account,chat)
        if mask.get('vibe_summary'):
            out+='\n本群氛围资料：'+mask['vibe_summary'][:160]
    return out


def habit_prompt(settings, notes):
    if not settings.chat_habits_enabled:
        return ''
    return habit_static_prompt(settings) + habit_dynamic_prompt(settings, notes)


def warmth_prompt(settings):
    return ''


def sticker_static_prompt(settings):
    from .natural_chat import sticker_policy
    return sticker_policy(settings)


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
    if diagnostics is not None:
        diagnostics.update(target=target,recent_turns=turns,sticker_turns=stickers)
    return ''



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
