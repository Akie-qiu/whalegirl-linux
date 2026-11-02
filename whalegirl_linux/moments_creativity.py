"""Small, original story directions for public Moments; never personal memories."""
from __future__ import annotations

from collections import Counter
from hashlib import blake2s


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
    '不要每次都靠米饭、鲸尾、困倦或“本鲸”证明身份，也不要导入其他AI娘的身份和关系。'
    '在同一次构思里比较两三个不同方向，选真正有小发现、小反转、进展或温柔细节的一个；只输出最后一份决定。'
    '剧情参考可自由改写，也可选自己的新点子，不按固定栏目轮流发，不要求每条都搞笑。'
    '借来的剧情可成为假想、小剧场或创作脑洞，不能当作现实亲历、真实群友发言或真实发生的互动。'
    '没有真实素材时，从“想尝试”“刚想到”“如果”“脑补了一个画面”等自然角度写；'
    '不要编出已经发生的线下活动、旧物、半年经历或群友原话。'
    'overused_content和overused_visual是近期高频提醒，降低重复组合的优先级，不机械禁止单个词或身份特征。'
    '本次优先把有趣的主要事件放到高频元素以外，不能换个小剧情仍以吃饭、围观群聊或犯困收尾；'
    '人设习惯可以点到为止，叙事看点要来自本次具体的选择或发现。'
    'continuation_candidates仅来自已发朋友圈；有新的实际进展或明确的假想续篇才延续，无进展不复述旧事。'
    '另返回story_seed（所选参考id或original）、story_hook（不超过80字的具体看点）、'
    'story_progress（不超过80字的本次进展；没有则空）。这些是后台记录，不要写成文案里的标签。'
    '文案和配图围绕同一件小事；配图不必总是正面半身自画像，可以是动作插画、物件特写或简洁二至三格小漫画。'
    'image_prompt写清小事件、关键动作或反差、物件关系、取景和情绪；不依赖大段对白传达笑点，'
    '保留角色身份，变化具体动作、场景与构图。'
)

MOMENTS_IMAGE_DIRECTION = (
    '这是朋友圈角色日常创作。围绕本次要求中的一件小事，用动作、物件关系和神情讲清看点；'
    '若要求小漫画，保持同一角色在各格外貌一致。避免无关人物、重复角色和大段画面文字。'
    '角色参考图确定身份与外观，旧作品仅延续脸型、发型和画风；本次要求决定情节、道具、服装、镜头与背景。'
    '不要自动补上手机、窗边、饭碗或摆拍姿势；本次明确需要的元素照常保留。'
)


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
    continuations = [{'topic': str(v.get('topic') or '')[:60],
                      'hook': str(v.get('story_hook') or '')[:80],
                      'progress': str(v.get('story_progress') or '')[:80]}
                     for v in recent if v.get('state') in ('sent', 'synced') and v.get('story_progress')][:2]
    return {'recent_count': len(recent), 'overused_content': hot(content), 'overused_visual': hot(visual),
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
