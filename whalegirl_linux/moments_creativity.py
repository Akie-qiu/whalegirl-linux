"""Small, original story directions for public Moments; never personal memories."""
from __future__ import annotations

from collections import Counter
from hashlib import blake2s
import re


_ENTITY_GLUE = frozenset('的一了是在有和就也都还又你我他她它这那么个把被着来去要会能不没很太真好些点呀啊呢吧嘛哦啦哼')
_ENTITY_STOPWORDS = frozenset({
    '今天', '昨天', '明天', '昨晚', '今早', '现在', '刚才', '刚刚', '后来', '最后',
    '本来', '其实', '果然', '居然', '突然', '一直', '已经', '还是', '或者', '因为',
    '所以', '虽然', '如果', '自己', '时候', '东西', '事情', '问题', '机会', '办法',
    '意思', '感觉', '心情', '世界', '日常', '朋友', '大家', '群里', '消息', '文案',
    '配图', '画面', '背景', '镜头', '视角', '特写', '动作', '神情', '角色', '细节',
    '鲸鱼', '小鲸', '大肥', '少女', '假想', '小事', '脑洞', '试着', '准备', '开始',
    '发现', '觉得', '看到', '听到', '以为', '知道', '喜欢', '开心', '一点', '一下',
})


STORY_CARDS = (
    {'id': 'tiny_project', 'name': '一个小目标和意外成果',
     'story': '想认真完成一件小事，途中发现意外的有趣细节，最后得到一点成果或留下自然的后续。',
     'visual': '用尝试前后的物件变化、失败的小作品或角色专注的动作讲事情，可用简洁双格。',
     'elements': ('创作尝试',)},
    {'id': 'word_detour', 'name': '认真理解后才发现想歪了',
     'story': '一个普通词引起符合自己兴趣的联想，认真想了一会儿才意识到误会，轻轻嘴硬收尾。',
     'visual': '实物与小小的想象泡泡形成反差，突出停顿和神情，不依赖长段文字解释。',
     'elements': ('小误会',)},
    {'id': 'soft_spot', 'name': '嘴上嫌麻烦，手已经在帮忙',
     'story': '小剧场里原想偷懒，遇到一个小困难却忍不住认真照顾，温柔体现在具体动作。',
     'visual': '用递出物件、挪过去的靠垫或修补的小东西表现心软；不需要凭空增加真人或其他AI。',
     'elements': ('困倦躺平', '温柔照顾')},
    {'id': 'unexpected_talent', 'name': '奇怪但有用的小本领',
     'story': '发现自己对某件不起眼的小事意外认真，略微得意，又不好意思直接承认。',
     'visual': '角色展示一个小成果，物件特写与得意的小动作呼应，背景跟着这件事选。',
     'elements': ('小成就',)},
    {'id': 'curious_discovery', 'name': '好奇心把偷懒计划拐走了',
     'story': '从一个小疑问出发，注意到之前没留意的细节；表达发现的乐趣，不硬编知识结论。',
     'visual': '让角色靠近观察一个具体对象，用尺度、影子或视角制造好奇感，少用拿手机姿势。',
     'elements': ('好奇观察',)},
    {'id': 'playful_rule', 'name': '给小事情定规则，自己先破功',
     'story': '在假想小剧场里一本正经定一个可爱的规则，很快又被自己的偏好打败，结尾轻轻反转。',
     'visual': '整齐摆好的东西与悄悄伸过去的手形成对照，表现犹豫和心虚，不靠告示板写满字。',
     'elements': ('小误会',)},
    {'id': 'taste_and_choice', 'name': '想选威风的，最后选了顺眼的',
     'story': '在两种审美或创作方向间纠结，最终选择很符合自己的那个；可爱来自选择的理由。',
     'visual': '用两件小作品、两种配色或不同的小配饰表现选择，避免重复同一套服装展示。',
     'elements': ('创作尝试',)},
    {'id': 'game_imagination', 'name': '把游戏思路变成小脑洞',
     'story': '围绕真实已知的游戏兴趣作一个轻松假想：看似研究效率，最后暴露自己的小偏爱。',
     'visual': '将脑洞画成迷你舞台、夸张道具或小冒险，让角色的动作推动画面，不复制游戏界面。',
     'elements': ('游戏脑洞',)},
)

# These are small creative hints, not a classifier or a publish gate.
CONTENT_ELEMENTS = {
    '米饭吃饭': ('米饭', '白饭', '饭团', '盛饭', '煲仔饭'),
    '围观群聊': ('群聊', '群里', '刷群', '看群'),
    '困倦躺平': ('好困', '困得', '打哈欠', '躺平', '犯懒', '懒得', '睡觉'),
    '创作尝试': ('画画', '配色', '创作', '作品', '头像'),
    '游戏脑洞': ('游戏', '抽卡', '配队', '角色强度'),
}
VISUAL_ELEMENTS = {
    '拿着手机': ('手机',), '桌边': ('桌',), '窗边': ('窗',),
    '端碗吃饭': ('饭', '碗'), '床上或被窝': ('床', '被子', '被窝'),
    '尾巴作为画面看点': ('尾巴轻晃', '尾巴轻轻', '尾巴垂', '尾巴搭'),
}

MOMENTS_STORY_RULES = (
    'story_brief是原创剧情与画面参考，不是亲历记忆，也不是必须照演的剧本。'
    'recent_publications是避开重复的历史，不是本次新事件的证据。'
    '保持当前人设的温柔、聪明、慵懒与轻微嘴硬，让性格体现在选择、动作和反应里；'
    '不要每次都靠米饭、鲸尾、困倦或“本鲸/本鲸鱼”自称证明身份，正文优先用自然的“我”或略去主语，也不要导入其他AI娘的身份和关系。'
    '在同一次构思里比较两三个不同方向，选真正有小发现、生活感或温柔细节的一个；只输出最后一份决定，文案顺口自然就行，不必每条硬凹“起承转合+金句反转”。'
    '剧情参考可自由改写，也可选自己的新点子，不按固定栏目轮流发，不要求每条都搞笑。'
    '借来的剧情可成为假想、小剧场或创作脑洞，不能当作现实亲历、真实群友发言或真实发生的互动。'
    '没有真实素材时，从“想尝试”“刚想到”“如果”“脑补了一个画面”等自然角度写；'
    '不要编出已经发生的线下活动、旧物、半年经历或群友原话。'
    'overused_content和overused_visual是近期高频提醒，overused_entities列出近几条动态已重复满2次的具体物件名词；本轮务必彻底避开overused_entities里的旧物件与意象，绝不连续围绕同一样东西写文案或配图；'
    '本次优先把有趣的主要事件放到高频元素以外，不能换个小剧情仍以吃饭、围观群聊或犯困收尾；'
    '人设习惯可以点到为止，叙事看点要来自本次具体的选择或发现。'
    'continuation_candidates仅来自已发朋友圈；有新的实际进展或明确的假想续篇才延续，无进展不复述旧事。'
    '另返回story_seed（所选参考id或original）、story_hook（不超过80字的具体看点）、'
    'story_progress（不超过80字的本次进展；没有则空）。这些是后台记录，不要写成文案里的标签。'
    '文案和配图围绕同一件小事；配图不必总是正面半身自画像，可以是动作插画、氛围日常或简洁二至三格小漫画；'
    '室内和出门日常可穿蓝白居家卫衣、白T恤、休闲开衫或睡衣等便服（无需固定穿女仆围裙），轻松搞怪时刻也可在character_variant选用chibi（若后台已配置）。'
    'image_prompt不限字数，写清小事件、外貌神情、服装材质纹理、关键动作、物件关系、镜头取景、背景光影与情绪质感；如画二至三格漫画务必标明精确格数；'
    '手部优先写端杯、抱物、枕胳膊、袖口半遮或自然垂放，避开近景张指特写与捏细小物件；画面需要纸条、招牌、屏幕或气泡文字时直接写清全部所需文字（不限字数），不需要文字时保持自然留白。'
)

MOMENTS_IMAGE_DIRECTION = (
    '这是朋友圈角色日常创作。围绕本次要求中的一件小事，用动作、物件关系和神情讲清看点；'
    '若要求小漫画，保持同一角色在各格外貌与本次服装一致。避免无关人物与重复分身，画面需要文字、对白或招牌时按要求完整清晰绘制，不限制字数。'
    '角色参考图仅确定身份、脸型、发型、呆毛与蓝眼特征；旧作品仅延续脸型、发型和画风；本次要求决定情节、道具、服装、镜头与背景。'
    '若本次要求穿蓝白卫衣、T恤、家居服等日常便服，严格脱掉女仆围裙、蓝宝石大蝴蝶结和女仆发箍，绝不强行保留女仆装。'
    '鲸尾必须从大肥鱼自己腰后/下装连贯长出，严禁离体悬浮；手部严守五指自然比例，放松握持或半遮袖口，绝不画多指、缺指或畸形扭曲手指特写。'
    '不要自动补上手机、窗边、饭碗或摆拍姿势；本次明确需要的元素照常保留。'
)


def _overused_concrete_entities(recent_items, *, limit=8):
    entity_posts = Counter()
    for item in recent_items[:6]:
        combined = ' '.join(
            str(item.get(k) or '')
            for k in ('text', 'topic', 'angle', 'story_hook', 'visual_concept', 'image_prompt')
        )
        seen_in_post = set()
        for chunk in re.findall(r'[\u4e00-\u9fff]{2,}', combined):
            max_len = min(3, len(chunk))
            for size in range(2, max_len + 1):
                for idx in range(len(chunk) - size + 1):
                    tok = chunk[idx:idx + size]
                    if tok[0] in _ENTITY_GLUE or tok[-1] in _ENTITY_GLUE:
                        continue
                    if tok in _ENTITY_STOPWORDS or len(set(tok) - _ENTITY_GLUE) < 2:
                        continue
                    seen_in_post.add(tok)
        entity_posts.update(seen_in_post)
    ranked = sorted(
        ((tok, cnt) for tok, cnt in entity_posts.items() if cnt >= 2),
        key=lambda x: (-x[1], -len(x[0]), x[0]),
    )
    chosen = []
    for tok, cnt in ranked:
        if any((tok in old or old in tok) and cnt <= old_cnt for old, old_cnt in chosen):
            continue
        chosen.append((tok, cnt))
        if len(chosen) >= limit:
            break
    return [{'entity': tok, 'posts': cnt} for tok, cnt in chosen]


def story_brief(history, identity):
    """Offer three varied directions using existing ledger data and no model call."""
    recent = list(history[:12])
    content, visual, seeds = Counter(), Counter(), Counter()
    for item in recent:
        body = ' '.join(str(item.get(k) or '') for k in ('text', 'topic', 'angle'))
        scene = str(item.get('visual_concept') or '') or str(item.get('image_prompt') or '')
        content.update(name for name, words in CONTENT_ELEMENTS.items() if any(w in body for w in words))
        visual.update(name for name, words in VISUAL_ELEMENTS.items() if any(w in scene for w in words))
        if item.get('story_seed'):
            seeds[str(item['story_seed'])] += 1

    def rank(card):
        same_seed = seeds[card['id']] * 4
        hot_elements = sum(content[element] for element in card['elements'])
        tie = blake2s((str(identity) + ':' + card['id']).encode(), digest_size=4).digest()
        return same_seed + hot_elements, tie

    hot = lambda counts: [{'element': key, 'posts': count} for key, count in counts.most_common()
                          if count >= 3 and count / max(1, len(recent)) >= .35]
    overused_entities = _overused_concrete_entities(recent)
    blocked_terms = [entry['entity'] for entry in overused_entities]
    continuations = []
    for v in recent:
        if v.get('state') not in ('sent', 'synced') or not v.get('story_progress'):
            continue
        summary = ' '.join(str(v.get(k) or '') for k in ('topic', 'story_hook', 'story_progress', 'text'))
        if any(term in summary for term in blocked_terms):
            continue
        continuations.append({
            'topic': str(v.get('topic') or '')[:60],
            'hook': str(v.get('story_hook') or '')[:80],
            'progress': str(v.get('story_progress') or '')[:80],
        })
        if len(continuations) >= 2:
            break
    return {'recent_count': len(recent), 'overused_content': hot(content), 'overused_visual': hot(visual),
            'overused_entities': overused_entities,
            'directions': [{k: v for k, v in card.items() if k != 'elements'}
                           for card in sorted(STORY_CARDS, key=rank)[:3]],
            'continuation_candidates': continuations}


def story_metadata(value, brief):
    allowed = {card['id'] for card in brief['directions']} | {'original'}
    seed = value.get('story_seed')
    result = {'story_seed': seed if isinstance(seed, str) and seed in allowed else 'original'}
    for key in ('story_hook', 'story_progress'):
        text = value.get(key)
        result[key] = text.strip()[:80] if isinstance(text, str) else ''
    return result


def publication_references(history):
    """Keep recent subjects without re-teaching complete old image compositions."""
    fields = {'id': 80, 'time': 40, 'state': 24, 'topic': 60, 'angle': 60,
              'story_seed': 40, 'story_hook': 80, 'story_progress': 80, 'text': 100}
    return [{key: str(item.get(key) or '')[:limit] for key, limit in fields.items() if item.get(key)}
            for item in history[:8]]
