"""Persistent per-chat quiet; a fresh group mention or wake word resumes chat."""
import math
import re
import time
import unicodedata
import json


STATIC_PROMPT = ('\n【会话安静控制】对方明确叫你暂时别插话、先听他们讲、暂停聊天或恢复聊天时，可在本次JSON中给'
    'conversation_control={"op":"quiet或resume","source_key":"本轮明确对你说话的真实消息key","minutes":30}。'
    '群聊执行quiet后持续静默，直到本群下一次明确@你或使用唤醒词才恢复原规则；不自动到时恢复。私聊quiet未给时长用后台默认值，minutes=0表示等明确恢复。'
    'resume恢复此前的聊天规则，不开启被管理员关闭的功能。'
    '只接受当前会话真实发言人的要求，不把引用、转发、普通讨论或别人互相叫安静当成控制你的命令，不自行决定让全群静默。'
    '执行quiet时不发送确认文字或表情。群聊静默期间不主动开口，拍一拍不解除静默；私聊明确唤醒可只回答本次，随后继续安静。'
    '明确任务正常执行，不因为自己可以沉默就随机忽略要求。')


def number(value):
    if re.fullmatch(r'\d+(?:\.\d+)?',value):return float(value)
    if value=='半':return .5
    digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits.update({'两':2,'〇':0})
    total=0;digit=0
    for c in value:
        if c in digits:digit=digits[c]
        elif c in '十百':total+=(digit or 1)*{'十':10,'百':100}[c];digit=0
        else:raise ValueError('无法识别时长')
    return float(total+digit)


def command(text, aliases=()):
    """Recognize clear commands locally; nuanced phrasing stays in the main turn."""
    text=unicodedata.normalize('NFKC',str(text or '')).casefold()
    text=re.sub(r'@[^\s\u2005]+[\s\u2005]*','',text)
    text=re.sub(r'[\s，。！？!?、,：:；;~～…“”‘’"\'（）()]','',text)
    aliases=sorted({str(v).casefold() for v in (*aliases,'机器人','鲸鱼娘','小鲸鱼','人工智能','AI','你') if v},key=len,reverse=True)
    name='(?:'+'|'.join(re.escape(v) for v in aliases)+')'
    prefix=r'(?:(?:请|麻烦)?(?:(?:让|叫)?'+name+r')(?:你)?(?:能不能|可不可以)?(?:给我)?)?(?:请|麻烦)?(?:先|现在|暂时|马上)?'
    ending=r'(?:了|吧|我|好吗|好不好|行不行)?'
    if re.fullmatch(prefix+r'(?:可以(?:继续)?说话了?|恢复(?:聊天|回复|说话|自主聊天)?|回来(?:聊天|说话)|继续聊|解除静默|不用安静了)'+ending,text):
        return {'op':'resume'}
    duration=r'(?:半(?:个)?小时|一刻钟|(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百半]+)(?:个)?(?:分钟|小时|秒钟|秒|分))'
    action=r'(?:安静(?:点|一下)?|闭嘴|闭麦|闭口|(?:别|不要)(?:再)?(?:说话|说了|回复|回消息|回话|插嘴|插话|吭声|发言)|消停(?:点|一下)?|停止(?:回复|发言|说话)|暂停(?:回复|发言|聊天)|不许说话|先听我们(?:说|讲))'
    indefinite=r'(?:直到我(?:叫你|让你说话)|等我(?:叫你|让你说话))'
    match=re.fullmatch(prefix+r'(?:一直)?(?P<before>'+duration+r')?'+action+
        r'(?P<after>'+duration+r'|一会儿?|一阵子|一下)?(?P<until>'+indefinite+r')?'+ending,text)
    if not match:return None
    if match['until'] or '一直' in text:return {'op':'quiet','minutes':0}
    token=match['before'] or match['after']
    if not token or token in ('一会','一会儿','一阵子','一下'):return {'op':'quiet'}
    if token=='一刻钟':minutes=15
    elif token in ('半小时','半个小时'):minutes=30
    else:
        n=re.match(r'[\d.零〇一二两三四五六七八九十百半]+',token)[0]
        minutes=number(n)*(60 if '小时' in token else 1/60 if '秒' in token else 1)
    return {'op':'quiet','minutes':max(1/60,min(10080,minutes))}


class ChatControl:
    def __init__(self,runtime):
        self.runtime=runtime;self.store=runtime.store
        with self.store.lock,self.store.db:
            self.store.db.execute('''CREATE TABLE IF NOT EXISTS chat_quiet_state(
                account TEXT,chat TEXT,until REAL NOT NULL DEFAULT 0,
                cutoff_time REAL NOT NULL DEFAULT 0,cutoff_id INTEGER NOT NULL DEFAULT 0,
                source_key TEXT NOT NULL DEFAULT '',updated REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(account,chat))''')
        self.states={(r['account'],r['chat']):dict(r) for r in self.store.db.execute('SELECT * FROM chat_quiet_state')}

    def state(self,account,chat,now=None):
        now=time.time() if now is None else now
        row=self.states.get((account,chat),{})
        until=row.get('until',0)
        return {**row,'active':until<0 or until>now,'until':until,
                'remaining_seconds':None if until<0 else max(0,round(until-now))}

    def active(self,account,chat):return self.state(account,chat)['active']

    @staticmethod
    def after_cutoff(event,state):
        return not state.get('cutoff_time') or (float(event['timestamp']),int(event.get('local_id') or 0)) > (
            state['cutoff_time'],state.get('cutoff_id',0))

    def allowed(self,event):
        state=self.state(event['account'],event['chat'])
        if not self.after_cutoff(event,state):
            return False  # Never replay a reply cancelled by an earlier quiet command.
        if not state['active']:return True
        if event.get('trigger')=='pat':return not event['chat'].endswith('@chatroom')
        payload=json.loads(event.get('payload_json') or '{}')
        return event.get('trigger')=='direct' and bool(payload.get('explicit_wake'))

    def apply(self,chat,op,minutes=None,source=None,*,defer_keys=()):
        s=self.runtime.config.settings
        if chat not in s.allowed_chat_ids:raise ValueError('会话未授权')
        if op not in ('quiet','resume'):raise ValueError('未知静默操作')
        now=time.time();account=s.account_id
        old=self.states.get((account,chat),{})
        row={'account':account,'chat':chat,'until':0,'cutoff_time':old.get('cutoff_time',0),
             'cutoff_id':old.get('cutoff_id',0),'source_key':source['key'] if source else '', 'updated':now}
        if op=='quiet':
            if chat.endswith('@chatroom') and (source is not None or minutes is None):minutes=0
            minutes=float(s.quiet_default_minutes if minutes is None else minutes)
            if not math.isfinite(minutes) or not 0<=minutes<=10080:raise ValueError('静默时长需为0～10080分钟')
            row['until']=now+minutes*60 if minutes else -1
            row['cutoff_time']=float(source['timestamp']) if source else now
            row['cutoff_id']=int(source.get('local_id') or 0) if source else 0
        with self.store.lock,self.store.db:
            self.store.db.execute('''INSERT OR REPLACE INTO chat_quiet_state
                (account,chat,until,cutoff_time,cutoff_id,source_key,updated)
                VALUES(:account,:chat,:until,:cutoff_time,:cutoff_id,:source_key,:updated)''',row)
        self.states[(account,chat)]=row
        if op=='quiet':
            for queued in self.store.queued_chat(account,chat):
                if queued['key'] not in defer_keys and not self.allowed(queued):
                    self.store.update(queued['key'],'ignored',error='临时静默，旧回复已取消，不补发')
        return self.state(account,chat)

    def observe(self,event,raw,*,aliases=(),reply_to_bot=False,defer_keys=()):
        if raw.get('isSelf') is not False or raw.get('forwarded') or raw.get('type') not in (1,49):return None
        state=self.state(event['account'],event['chat'])
        if not self.after_cutoff(event,state):return None
        group=event['chat'].endswith('@chatroom')
        text=str(raw.get('content') or '')
        addressed=(not group or raw.get('isMentioned') is True or reply_to_bot
                   or any(str(v).strip() and str(v).casefold() in text.casefold()
                          for v in (*aliases,'鲸鱼娘','小鲸鱼','机器人','人工智能','AI')))
        op=command(text,aliases)
        if op is None and self.runtime.silence_requested(raw,event,self.runtime.config.settings,
                extra_keywords=aliases,reply_to_bot=reply_to_bot):op={'op':'quiet'}
        explicit_wake=bool(json.loads(event.get('payload_json') or '{}').get('explicit_wake'))
        if group and op and op['op']=='quiet' and raw.get('reply') and not addressed:return None
        if op and op['op']=='resume' and not addressed:return None
        if op is None and group and state['active'] and explicit_wake:
            self.apply(event['chat'],'resume',source=event)
            return None  # Keep the wakeup queued so it gets a normal reply.
        if not group and not addressed:return None
        if op:
            self.apply(event['chat'],op['op'],op.get('minutes'),event,defer_keys=defer_keys)
            self.store.update(event['key'],'ignored',error=('群聊已静默，等待下次@或唤醒词恢复' if group else '要求机器人安静，已进入临时静默') if op['op']=='quiet' else '已恢复此会话原来的聊天规则')
        return op

    def model_control(self,first,batch,decision):
        if first['account']!=self.runtime.config.settings.account_id:return None
        op=decision.get('conversation_control')
        if not isinstance(op,dict) or op.get('op') not in ('quiet','resume'):return None
        sources={r['key']:r for r in batch if r.get('trigger')=='direct' and r.get('is_self',0)==0
                 and r['account']==first['account'] and r['chat']==first['chat']
                 and not json.loads(r.get('payload_json') or '{}').get('forwarded')}
        source=sources.get(op.get('source_key'))
        if not source:return None
        self.apply(first['chat'],op['op'],op.get('minutes'),source)
        return op['op']

    def dynamic_prompt(self,first):
        if not self.active(first['account'],first['chat']):return ''
        if first['chat'].endswith('@chatroom'):
            return '\n本群已静默，等待下一条明确@你或唤醒词恢复，不发送文字或表情。'
        return '\n本会话目前临时静默；这次是明确唤醒，只回答本轮任务，随后继续安静。除非对方明确要求恢复，不改变静默状态。'

    def snapshot(self):
        s=self.runtime.config.settings
        return {chat:self.state(s.account_id,chat) for chat in s.allowed_chat_ids}
