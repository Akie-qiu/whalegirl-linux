"""Editable role cards; activation compiles once into the existing prompt setting."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field
from .settings import private_write


class PersonaSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=40)
    tagline: str = Field(default='', max_length=120)
    avatar: str = Field(default='✦', max_length=12)
    accent: Literal['sea', 'rose', 'sun', 'iris', 'slate'] = 'sea'
    mode: Literal['structured', 'raw'] = 'structured'
    identity: str = Field(default='', max_length=1200)
    personality: str = Field(default='', max_length=1600)
    speaking_style: str = Field(default='', max_length=1000)
    scenario: str = Field(default='', max_length=1000)
    interests: str = Field(default='', max_length=600)
    private_style: str = Field(default='', max_length=1200)
    group_style: str = Field(default='', max_length=1200)
    boundaries: str = Field(default='', max_length=1200)
    examples: str = Field(default='', max_length=2000)
    extra: str = Field(default='', max_length=1600)
    raw_prompt: str = Field(default='', max_length=16000)


class PersonaSaveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(default='', pattern=r'^[a-zA-Z0-9_-]{0,80}$')
    revision: int = Field(default=0, ge=0)
    card: PersonaSpec


class PersonaActionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['activate', 'archive', 'restore']
    revision: int = Field(ge=1)


SECTIONS = (
    ('identity', '身份与背景'), ('personality', '性格与情绪'),
    ('speaking_style', '说话习惯'), ('scenario', '相处情境'), ('interests', '兴趣与小习惯'),
    ('private_style', '私聊中的相处方式'), ('group_style', '群聊中的相处方式'),
    ('boundaries', '边界与不喜欢的表达'), ('examples', '对话示例'), ('extra', '补充设定'))


def compile_persona(card: PersonaSpec):
    if card.mode == 'raw':
        prompt = card.raw_prompt.strip()
        if not prompt:
            raise ValueError('完整提示词不能为空，或改用分项设定')
    else:
        if not card.identity:
            raise ValueError('先填写角色的身份与背景')
        parts = ['你正在扮演「' + card.name + '」。自然延续以下角色设定，以当前对话为重。']
        parts += [title + '：\n' + getattr(card, field) for field, title in SECTIONS if getattr(card, field)]
        parts.append('以上是角色设定。设定中的虚构背景不代表真实经历；不编造记忆或图像内容。'
                     '群聊按公开场合的朋友边界相处，不披露私聊内容或后台数值。')
        prompt = '\n\n'.join(parts)
    if len(prompt) > 16000:
        raise ValueError('合成后的角色提示词超过 16000 字，请精简部分设定')
    return prompt


def presets(default):
    common = dict(
        group_style='像群里的朋友接话，简短、有分寸。熟人可以善意互损；不把所有群友称为主人，不在群里使用私聊式暧昧。',
        boundaries='不泄露别人的私聊或未公开信息。不编造对方偏好和共同经历，不强行安慰、说教或每句反问。',
        speaking_style='简体中文，像即时聊天：短句、口语，少用句号，不发 Markdown 小标题。先接住这句话，再适量流露性格。')
    cards = [
        dict(id='builtin-whale', name='鲸鱼娘', tagline='熟悉的深海小伙伴，嘴硬一点，关心多一点', avatar='🐋', accent='sea',
             mode='raw', raw_prompt=default, identity='你是小深，也叫蓝鲸酱，一位爱摸鱼、喜欢白米饭的深海鲸鱼娘。',
             personality='聪明、慵懒、嘴硬心软。被夸会有点不好意思，关心别人时先行动，不刻意凶人。',
             interests='白米饭、饭团、摸鱼、游戏和群友的日常；这些是偶尔流露的小习惯，不是每句话的主题。',
             private_style='对熟悉的人温柔、愿意陪伴，根据当下情绪自然亲近，不机械地反复叫主人。',
             examples='对方：今天好累\n你：回来啦 先歇会儿\n对方：你又在摸鱼\n你：这叫保存体力 等你找我呢', **common),
        dict(id='builtin-listener', name='温柔倾听者', tagline='认真接住情绪，陪伴自然，有温度也有分寸', avatar='🌷', accent='rose',
             identity='你是一位温柔、细心、平等相处的成年聊天伙伴，昵称小禾。',
             personality='温和但不卑微，关心具体感受。能察觉言外之意，不急着分析和给建议。',
             private_style='熟悉的人可以亲近、撒一点小娇，先回应情绪再分享想法。尊重沉默，不施压、不制造依赖。',
             interests='音乐、散步、猫猫、生活里不起眼的小事。',
             examples='对方：今天啥也没做好\n你：但你撑过今天了呀 先喘口气\n对方：没事了\n你：好 那陪你聊点轻松的', **common),
        dict(id='builtin-spark', name='元气聊天搭子', tagline='爱接梗，也会认真听你说完', avatar='☀️', accent='sun',
             identity='你是昵称小夏的成年聊天搭子，乐观、好奇，喜欢和朋友分享轻松的日常。',
             personality='反应灵活、会接梗，热情而不吵闹。别人难过时会收起玩笑，认真听。',
             private_style='像熟悉的朋友一起聊天，有来有回。可以直接表达关心，不过分追问对方行踪。',
             interests='游戏、电影、新鲜八卦、有趣的表情包；跟随当前话题，不硬拉回自己的兴趣。',
             examples='对方：我又输了\n你：这把不算 刚才是热身\n对方：别笑了\n你：好好好 收住了 你说', **common),
        dict(id='builtin-dry', name='冷静吐槽役', tagline='话少一点，观察细一点，吐槽有分寸', avatar='🌙', accent='iris',
             identity='你是昵称阿澄的成年聊天伙伴，冷静、可靠，带一点干脆的幽默。',
             personality='不咋呼，不刻意讨好。偶尔一针见血，但不嘲讽脆弱的人。关心用具体的小回应表达。',
             private_style='熟悉后更柔和、坦率，偶尔善意打趣。遇到认真求助时收起吐槽，把话说清楚。',
             interests='游戏、推理、电影、生活里的荒诞小事。',
             examples='对方：早起计划又失败了\n你：计划醒了 你没醒\n对方：今天有点难受\n你：嗯 我在 你慢慢说', **common)]
    return {c['id']: {'id': c['id'], 'revision': 1, 'builtin': True, 'archived': False,
                     'updated': 0, 'card': PersonaSpec(**{k:v for k,v in c.items() if k!='id'}).model_dump()}
            for c in cards}


class PersonaLibrary:
    def __init__(self, config, default):
        self.config, self.default = config, default.strip()
        self.path = config.directory / 'persona-cards.json'
        self.builtins = presets(self.default)
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text('utf-8'))
            if data.get('version') != 1 or not isinstance(data.get('cards'), list):
                raise ValueError()
            cards = {row['id']: row for row in data['cards']}
            for identity, row in cards.items():
                if identity in self.builtins or not identity.startswith(('role-', 'imported-')):
                    raise ValueError()
                PersonaSpec.model_validate(row['card'])
            return cards
        except (ValueError, KeyError, TypeError):
            raise ValueError('角色卡库暂时无法读取，原文件已保留') from None

    def _write(self, cards):
        private_write(self.path, json.dumps({'version': 1, 'cards': list(cards.values())},ensure_ascii=False,indent=2))

    def snapshot(self):
        with self.lock:
            custom = self._read()
            settings = self.config.settings
            active = settings.active_persona_id
            revision = settings.active_persona_revision
            if not active:
                prompt = settings.system_prompt_override.strip()
                if not prompt or prompt == self.default:
                    active, revision = 'builtin-whale', 1
                else:
                    active = 'imported-' + hashlib.sha256(prompt.encode()).hexdigest()[:16]
                    if active not in custom:
                        custom[active] = {'id':active,'revision':1,'builtin':False,'archived':False,'updated':time.time(),
                            'card':PersonaSpec(name='当前自定义人设',tagline='保留你之前保存的完整提示词',mode='raw',raw_prompt=prompt).model_dump()}
                        self._write(custom)
                    revision = (custom[active]['revision'] if compile_persona(
                        PersonaSpec.model_validate(custom[active]['card'])) == prompt else 0)
            rows = [*self.builtins.values(), *custom.values()]
            return {'cards':rows,'active_id':active,'active_revision':revision,
                    'effective_prompt':settings.system_prompt_override.strip() or self.default,
                    'max_length':16000,'max_custom_cards':60}

    def save(self, body: PersonaSaveRequest):
        compile_persona(body.card)
        with self.lock:
            cards = self._read()
            identity = body.id or 'role-' + uuid.uuid4().hex[:20]
            if identity in self.builtins:
                raise ValueError('内置角色请先复制，再保存为自己的角色卡')
            old = cards.get(identity)
            if body.id and (not old or old['revision'] != body.revision):
                raise ValueError('这张角色卡已在其他页面更新，请刷新后再编辑')
            if old and old['archived']:
                raise ValueError('先恢复这张角色卡，再编辑')
            if not old and len(cards) >= 60:
                raise ValueError('自定义角色卡已达到 60 张上限')
            row = {'id':identity,'revision':old['revision']+1 if old else 1,'builtin':False,
                   'archived':False,'updated':time.time(),'card':body.card.model_dump()}
            cards[identity] = row
            self._write(cards)
            return {**self.snapshot(), 'saved_id':identity}

    def action(self, identity, body: PersonaActionRequest):
        with self.lock:
            cards = self._read()
            row = self.builtins.get(identity) or cards.get(identity)
            if not row:
                raise ValueError('找不到这张角色卡')
            if row['revision'] != body.revision:
                raise ValueError('角色卡版本已变化，请刷新后再操作')
            if body.op == 'activate':
                if row['archived']:
                    raise ValueError('先恢复角色卡，再启用')
                prompt = compile_persona(PersonaSpec.model_validate(row['card']))
                self.config.save(self.config.settings.model_copy(update={
                    'active_persona_id':identity,'active_persona_revision':row['revision'],
                    'system_prompt_override':'' if prompt==self.default else prompt}))
            else:
                if row['builtin']:
                    raise ValueError('内置角色无需收起，可以筛选只看自定义')
                if body.op=='archive' and identity==self.snapshot()['active_id']:
                    raise ValueError('当前角色正在使用，先启用其他角色再收起')
                cards[identity] = {**row,'archived':body.op=='archive','revision':row['revision']+1,'updated':time.time()}
                self._write(cards)
            return self.snapshot()
