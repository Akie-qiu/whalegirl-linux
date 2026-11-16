"""Lightweight adaptation of fishtalk 0.3.6 fishrules/sim/distill (MIT).

Upstream: Nozzi2455/fishtalk, pinned source and license in vendor/fishtalk.
Roleplay, affectionate address and self references are intentionally not faults.
No Node process, network or model is needed on the reply path.
"""
from collections import Counter, defaultdict
import re
import unicodedata

PARTICLES = tuple('啊吧呢嘛吗哈呀啦哦噢诶欸咯嘞捏惹了呗')
HARD_PHRASES = ('这条我认', '我记下了', '这是本鱼最喜欢的一句话', '收到主人', '本鱼记下了')
PATTERNS = {
    'contrast': re.compile(r'不是.{1,20}[，,]\s*(?:而)?是|与其.{0,20}不如|看似.{0,20}实则|不仅.{0,20}而且'),
    'summary': re.compile(r'^(?:总之|所以说|由此可见|总的来说)'),
    'report': re.compile(r'值得一提的是|希望(?:这|以上).*帮助|让我们一起'),
    'abstract': re.compile(r'这本身就是|本质上|归根结底|说到了?根上'),
}
TAIL = re.compile(r'^(?:希望(?:这|以上)(?:些|的内容|的回答|的说明|个回答|个建议|段话)?'
    r'(?:对你|能)(?:有|有所|提供)?帮助[哦呀哈呢～~]*|'
    r'(?:如果|若)(?:你)?(?:还有|有)(?:其他|任何)?(?:问题|疑问|需求)[，,]?'
    r'(?:可以|请|欢迎)?随时(?:向我)?(?:提问|咨询|告诉我|联系我))$')
NAGGING_RE = re.compile(
    r'(?:别熬|少熬|早点睡|快去睡|赶紧睡|熬到天亮|别太晚|'
    r'几点了.{0,8}(?:睡|熬|歇)|还(?:没|不)睡.{0,6}(?:呀|嘛|吗)|熬夜.{0,6}(?:伤|不好|累))'
)

# Addresses and grammatical glue must not become alleged character faults.
# The detector discovers repeated wording, rather than banning affective words.
ADDRESSES = re.compile(r'deepseek酱|本鲸鱼|鲸鱼娘|妈妈|飘渺|本肥鱼|本鱼|亲爱的|老婆|老公|姐姐|妹妹|宝宝|宝贝|主人', re.I)
GLUE = frozenset('的一了是在有和就也都还又你我他她它这那么个把被着来去要会能不没很太真好些点呀啊呢吧嘛哦啦哼一')


def norm(text):
    text = re.sub(r'\[[^\]]{1,8}\]', '', str(text or ''))
    return ''.join(c.lower() for c in text if not c.isspace() and unicodedata.category(c)[0] not in 'PS')


def grams(text):
    value = norm(text)
    return {value[i:i+2] for i in range(len(value)-1)} if len(value) > 1 else set(value)


def similarity(a, b):
    a, b = grams(a), grams(b)
    return len(a & b) / len(a | b) if a and b else 0.0


def common_length(a, b):
    """Upstream longest common contiguous substring, bounded by caller inputs."""
    x, y = norm(a), norm(b)
    best, previous = 0, [0] * (len(y)+1)
    for c in x:
        diagonal = 0
        for j, other in enumerate(y, 1):
            old = previous[j]
            previous[j] = diagonal + 1 if c == other else 0
            best = max(best, previous[j])
            diagonal = old
    return best


def mask_quoted(text):
    value = str(text or '')
    for pattern in (r'```[\s\S]*?```', r'`[^`\n]+`', r'「[^」]*」', r'『[^』]*』',
                    r'“[^”]*”', r'‘[^’]*’', r'"[^"\n]*"', r"'[^'\n]*'", r'(?m)^\s*>[^\n]*'):
        value = re.sub(pattern, lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), value)
    return value


def repeated_expressions(turns, *, limit=3):
    """Discover recurring short wording across complete, distinct sent turns.

    Longer expressions need three uses; short content words need four. Quotes,
    addresses, particles and old-only phrases are not expression habits. The
    result is reference data, never a reason to delete a reply or ban a word.
    """
    if limit <= 0:
        return []
    counts, recent = Counter(), set()
    for index, turn in enumerate(list(turns)[:24]):
        text = ADDRESSES.sub(' ', mask_quoted(str(turn or '')[:1200]))
        segments = re.findall(r'[\u3400-\u9fff]+', text)
        seen = set()
        for segment in segments:
            for size in range(2, min(10, len(segment))+1):
                for start in range(len(segment)-size+1):
                    phrase = segment[start:start+size]
                    if len(set(phrase)-GLUE) >= 2:
                        seen.add(phrase)
        counts.update(seen)
        if index < 6:
            recent.update(seen)
    pool = sorted(((text, count) for text, count in counts.items()
        if text in recent and count >= (4 if len(text) < 4 else 3)),
        key=lambda item: (-item[1], -len(item[0]), item[0]))
    chosen = []
    for text, count in pool:
        if any(text in old or old in text for old, _ in chosen):
            continue
        chosen.append((text, count))
        if len(chosen) >= limit:
            break
    return [{'text': text, 'turns': count} for text, count in chosen]


def _is_question_turn(text):
    lines = [line.strip().rstrip('~～"”\'’ ') for line in str(text or '').splitlines() if line.strip()]
    return any(line.endswith(('？', '?')) for line in lines)


def _is_two_part_turn(text):
    raw = str(text or '').strip()
    # Genuine ellipsis pauses or playful tilde endings are soft voice beats,
    # not mechanical "short reaction + explanation/interrogation" templates.
    if '……' in raw or '...' in raw or '…' in raw or raw.endswith(('~', '～')):
        return False
    clean = raw.strip('。！!？?~～ ')
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if len(lines) == 2 and 2 <= len(lines[0]) <= 16 and len(lines[1]) >= 5:
        return True
    if not lines or len(lines) > 1 or not (8 <= len(clean) <= 38):
        return False
    if '，' in clean or ',' in clean:
        parts = [p.strip() for p in re.split(r'[，,]', clean) if p.strip()]
        return (
            len(parts) == 2 and 2 <= len(parts[0]) <= 12
            and 5 <= len(parts[1]) <= 24
            and (raw.endswith(('？', '?')) or '?' in parts[1] or '？' in parts[1])
        )
    parts = [p.strip() for p in re.split(r'\s+', clean) if p.strip()]
    return len(parts) == 2 and 2 <= len(parts[0]) <= 17 and 5 <= len(parts[1]) <= 24


def _opener_prefix4(text):
    clean = ADDRESSES.sub(' ', str(text or ''))
    clean = re.sub(r'^\[[^\]]{1,12}\]\s*', '', clean).lstrip(' ，,。！!？?~～…')
    m = re.match(r'[\u3400-\u9fff]{4,}', clean)
    return m[0][:4] if m else ''


def check(text, *, previous=(), examples=(), literal=False, recurring=()):
    value = str(text or '')
    masked = mask_quoted(value)
    issues, notes = [], []
    # Upstream hard examples are advisory for this character: a truthful memory
    # acknowledgement or affectionate address must never be globally forbidden.
    for phrase in HARD_PHRASES:
        if phrase in masked:
            notes.append('habitual_acknowledgement')
            break
    for code, pattern in PATTERNS.items():
        if pattern.search(masked):
            notes.append(code)
    if not literal and previous:
        prev_masked = [mask_quoted(old) for old in previous[:6]]
        if _is_question_turn(masked) and sum(1 for old in prev_masked[:3] if _is_question_turn(old)) >= 2:
            notes.append('question_streak')
        if _is_two_part_turn(masked) and sum(1 for old in prev_masked[:4] if _is_two_part_turn(old)) >= 2:
            notes.append('two_part_rut')
        lead = _opener_prefix4(masked)
        if len(norm(masked)) >= 6 and lead and any(_opener_prefix4(old) == lead for old in prev_masked):
            notes.append('fast_opener_repeat')
        if NAGGING_RE.search(masked) and any(NAGGING_RE.search(old) for old in prev_masked[:5]):
            notes.append('nagging_streak')
    if not literal and any(norm(item['text']) in norm(masked) for item in recurring):
        issues.append('repeated_expression')
    if not literal and len(norm(masked)) >= 12:
        if any(similarity(masked, old) >= .82 for old in previous if len(norm(old)) >= 12):
            issues.append('recent_repeat')
        # Do not mistake a short shared noun or ordinary greeting for copying.
        for example in examples:
            clean = mask_quoted(example)[:120]
            if len(re.findall(r'[\u3400-\u9fff]', clean)) >= 12:
                common = common_length(masked[:500], clean)
                if common >= 16 and common >= .65 * len(norm(clean)):
                    issues.append('corpus_copy')
                    break
    return {'issues': issues, 'notes': notes, 'ok': not issues and not notes}


def trim_service_tail(text):
    """Remove only a standalone, unquoted generic service closing with substance left."""
    chunks = re.findall(r'[^。！？!?；;\n]+[。！？!?；;\n]*|[。！？!?；;\n]+', text)
    removed = []
    kept = []
    for chunk in chunks:
        body = chunk.strip().rstrip('。！？!?；;').strip()
        if mask_quoted(body) == body and len(body) <= 65 and TAIL.fullmatch(body):
            removed.append(chunk)
        else:
            kept.append(chunk)
    value = ''.join(kept).strip()
    # An explicit answer must not disappear or turn into a fixed fallback line.
    return value if removed and norm(value) else text


def profile(items, *, examples=3):
    """Adapt distill.profile; require different speakers for shared catchphrases."""
    lengths = sorted(len(i['text']) for i in items)
    percentile = lambda p: lengths[min(len(lengths)-1, int(len(lengths)*p))] if lengths else 0
    particles, endings, openings, phrases = Counter(), Counter(), Counter(), Counter()
    phrase_people, opener_people = defaultdict(set), defaultdict(set)
    no_end = 0
    for item in items:
        text, person = item['text'], item['speaker']
        if text[-1:] in '？！…。~～':
            endings[text[-1]] += 1
        else:
            no_end += 1
        tail = re.sub(r'[\s。？！!?，,~～…]+$', '', text)[-1:]
        if tail in PARTICLES:
            particles[tail] += 1
        lead = re.split(r'[\W_]', text, maxsplit=1)[0]
        for size in range(2, min(4, len(lead))+1):
            openings[lead[:size]] += 1
            opener_people[lead[:size]].add(person)
        clean = re.sub(r'\d', '', norm(text))
        for phrase in {clean[i:i+n] for n in (3, 4) for i in range(len(clean)-n+1)}:
            phrases[phrase] += 1
            phrase_people[phrase].add(person)
    leads = []
    for opening, count in sorted(openings.items(), key=lambda p: (-len(p[0]), -p[1], p[0])):
        if count < 2 or len(opener_people[opening]) < 2:
            continue
        if not any(kept.startswith(opening) and amount >= .7*count for kept, amount in leads):
            leads.append((opening, count))
    chosen, speakers = [], Counter()
    pool = sorted(items, key=lambda i: (i.get('label') not in ('good', 'funny'), abs(len(i['text'])-percentile(.5)), i['key']))
    for per_person in (1, 2, 3):
        for item in pool:
            if len(chosen) >= examples:
                break
            if not 4 <= len(item['text']) <= 65 or len(set(norm(item['text']))) < 3:
                continue
            if speakers[item['speaker']] >= per_person or any(similarity(item['text'], i['text']) > .5 for i in chosen):
                continue
            chosen.append({k: item[k] for k in ('key', 'speaker', 'text')})
            speakers[item['speaker']] += 1
    return {'count': len(items), 'people': len({i['speaker'] for i in items}),
        'length': {'p25': percentile(.25), 'median': percentile(.5), 'p75': percentile(.75)},
        'no_end_punctuation_ratio': round(no_end / len(items), 2) if items else 0,
        'particles': particles.most_common(5),
        'openings': sorted(leads, key=lambda p: (-p[1], p[0]))[:5],
        'phrases': [(p, c) for p, c in phrases.most_common(50) if c >= 3 and len(phrase_people[p]) >= 2][:6],
        'examples': chosen}
