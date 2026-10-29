"""Local send safeguards. These limits are not WeChat account-safety thresholds."""
from datetime import datetime, timedelta, timezone
import json
import re
import time
import unicodedata


def canonical(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')).casefold())


def client_warning(text):
    """Only pass native system-dialog text here, never ordinary chat messages."""
    patterns = (r'操作(?:过于|太)?频繁', r'发送(?:消息)?(?:过于|太)?频繁',
        r'(?:帐号|账号|账户).{0,12}(?:异常|被封|封禁|停用|冻结|安全风险)',
        r'(?:限制|禁止|暂停).{0,8}(?:登录|登陆|发消息|发送|使用|功能)',
        r'(?:安全|身份|人脸|短信|手机).{0,5}验证', r'涉嫌.{0,8}违规',
        r'账号.{0,8}风险', r'account.{0,25}(?:blocked|restricted|suspended|banned|unusual|abnormal)',
        r'too (?:many|frequent)|security verification|unusual activity|temporarily restricted')
    return bool(text and any(re.search(pattern, text, re.I) for pattern in patterns))


class SafetyGuard:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS safety_state(account TEXT PRIMARY KEY,state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS safety_events(
                    id INTEGER PRIMARY KEY,account TEXT,created REAL,level TEXT,code TEXT,reason TEXT,chat TEXT);
                CREATE INDEX IF NOT EXISTS safety_events_account ON safety_events(account,created);
            ''')
            store.db.commit()
        self.last_check = 0
        self.check_error = ''
        self.last_notice = ''

    def state(self, account):
        row = self.store.db.execute('SELECT state FROM safety_state WHERE account=?', (account,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _save(self, account, state):
        self.store.db.execute('INSERT OR REPLACE INTO safety_state VALUES(?,?)', (account,json.dumps(state)))
        self.store.db.commit()

    def record(self, account, code, reason, *, chat='', level='warning'):
        now = time.time()
        with self.store.lock:
            previous = self.store.db.execute('''SELECT created FROM safety_events
                WHERE account=? AND code=? AND chat=? ORDER BY id DESC LIMIT 1''', (account,code,chat)).fetchone()
            if previous and now-previous[0] < 30 and code != 'login_request':
                return
            self.store.db.execute('INSERT INTO safety_events(account,created,level,code,reason,chat) VALUES(?,?,?,?,?,?)',
                                  (account,now,level,code,reason[:400],chat))
            self.store.db.commit()

    def hold(self, account, code, reason):
        with self.store.lock:
            state = self.state(account)
            if not state.get('hold_reason'):
                state.update(hold_reason=reason,hold_code=code,hold_since=time.time())
                self._save(account,state)
            self.record(account,code,reason,level='danger')

    def held(self, account):
        state = self.state(account)
        return state.get('hold_reason','') or (
            '正在恢复微信界面，完成后继续回复'
            if state.get('recovery_status') in ('pending', 'recovering') else '')

    def resume(self, account):
        with self.store.lock:
            state = self.state(account)
            state.update(consecutive_failures=0, recovery_used=False,
                         recovery_status='idle', hold_reason='', hold_code='', hold_since=None)
            self._save(account,state)
            self.record(account,'manual_resume','已人工确认恢复发送',level='info')

    def activity(self, account, now=None, chat=None):
        now = time.time() if now is None else now
        local = datetime.fromtimestamp(now,timezone(timedelta(hours=8)))
        day_start = local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        since = min(now-3600,day_start)
        rows = self.store.db.execute('''SELECT i.chat,i.reply,
            CASE WHEN json_extract(m.metrics,'$.reply_type')='sticker' THEN 'sticker' ELSE 'text' END AS kind,
            COALESCE(json_extract(m.metrics,'$.send_started_at'),i.updated) AS sent_at
            FROM inbox i LEFT JOIN reply_metrics m ON m.key=i.key
            WHERE i.account=? AND i.state IN ('sent','sending','uncertain')
            AND NOT EXISTS(SELECT 1 FROM outbound_actions a WHERE a.inbox_key=i.key)
            AND COALESCE(json_extract(m.metrics,'$.send_started_at'),i.updated)>=?
            UNION ALL SELECT chat,reply,kind,sent_at FROM outbound_actions
            WHERE account=? AND state IN ('sent','sending','uncertain') AND sent_at>=?''', (account,since,account,since)).fetchall()
        if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='moments_actions'").fetchone():
            rows += self.store.db.execute('''SELECT 'moments:' || kind || ':' ||
                COALESCE(json_extract(payload,'$.post.id'),'timeline') AS chat,text AS reply,
                'text' AS kind,updated AS sent_at FROM moments_actions WHERE account=?
                AND state IN ('sent','sending','uncertain') AND updated>=?''',(account,since)).fetchall()
        counts = {'minute':0,'hour':0,'day':0,'chat_hour':0,'text_hour':0,'text_day':0,'sticker_hour':0,'sticker_day':0,'image_hour':0,'image_day':0}
        for row in rows:
            when = row['sent_at']
            if row['kind'] != 'image':
                counts['minute'] += when > now-60
                counts['hour'] += when > now-3600
                counts['day'] += when >= day_start
                counts['chat_hour'] += row['chat']==chat and when > now-3600
            counts[row['kind']+'_hour'] += when > now-3600
            counts[row['kind']+'_day'] += when >= day_start
        return counts,rows,day_start

    def check(self, account, chat, settings, *, reply=None, now=None, kind=None):
        now = time.time() if now is None else now
        held = self.held(account)
        if held:
            return {'allowed':False,'code':'safety_hold','reason':held,'retry_at':None}
        # Uploaded and previously generated images have no send-count or
        # duplicate-content cap. Login/permission checks and receipts remain.
        if kind == 'image':
            return {'allowed':True}
        if not settings.safety_enabled:
            return {'allowed':True}
        counts,rows,day_start = self.activity(account,now,chat)
        for field,cap,seconds,code,label in (
            ('minute',settings.safety_per_minute,60,'minute_limit','账号一分钟发送上限'),
            ('hour',settings.safety_per_hour,3600,'hour_limit','账号一小时发送上限'),
            ('chat_hour',settings.safety_chat_per_hour,3600,'chat_hour_limit','该会话一小时发送上限'),
            ('day',settings.safety_per_day,86400,'day_limit','账号当日发送上限')):
            if settings.safety_send_limits_enabled and cap>0 and counts[field] >= cap:
                times = [r['sent_at'] for r in rows if
                    r['kind'] != 'image' and (field != 'chat_hour' or r['chat']==chat) and
                    r['sent_at'] >= (day_start if field=='day' else now-seconds)]
                retry_at = day_start+86400 if field=='day' else sorted(times)[len(times)-cap]+seconds
                return {'allowed':False,'code':code,'reason':f'本地保护：已达{label}（{cap}条），本次不发送',
                        'retry_at':retry_at}
        if kind in ('text','sticker') and settings.safety_send_limits_enabled:
            for window,seconds in (('hour',3600),('day',86400)):
                cap=getattr(settings,kind+'_per_'+window)
                if cap>0 and counts[kind+'_'+window]>=cap:
                    times=[r['sent_at'] for r in rows if r['kind']==kind and
                           r['sent_at']>=(day_start if window=='day' else now-seconds)]
                    return {'allowed':False,'code':kind+'_'+window+'_limit',
                            'reason':'本地保护：已达'+('文字' if kind=='text' else '表情')+('当日' if window=='day' else '每小时')+'独立额度',
                            'retry_at':day_start+86400 if window=='day' else min(times)+seconds}
        if reply:
            for row in rows:
                if (row['chat']==chat and row['sent_at']>now-settings.safety_duplicate_seconds
                        and canonical(row['reply'])==canonical(reply)):
                    return {'allowed':False,'code':'duplicate_reply','reason':'本地保护：近期已在该会话发送相同内容，本次不重复发送',
                            'retry_at':row['sent_at']+settings.safety_duplicate_seconds}
        return {'allowed':True}

    def outcome(self, account, receipt, settings, *, chat=''):
        with self.store.lock:
            state = self.state(account)
            if receipt.confirmed:
                if (state.get('recovery_status') == 'awaiting_send'
                        and 0 < getattr(receipt, 'details', {}).get('started_at', 0)
                        < state.get('recovery_completed_at', 0)):
                    return
                if state.get('consecutive_failures') or state.get('recovery_used'):
                    # A different in-flight send can finish while recovery is
                    # pending. It must not cancel that requested UI operation.
                    state['consecutive_failures'] = 0
                    if state.get('recovery_status') not in ('pending', 'recovering'):
                        state['recovery_used'] = False
                        if state.get('recovery_status') == 'awaiting_send':
                            state['recovery_status'] = 'recovered'
                            self.record(account, 'ui_recovery_confirmed', '界面恢复后的新消息已确认发送，失败计数已清零', level='info')
                    self._save(account,state)
                return
            if not receipt.definite_failure or (not settings.safety_enabled
                    and not getattr(settings, 'send_auto_recovery_enabled', True)):
                return
            attempts=max(1,min(2,int(getattr(receipt,'attempts',1) or 1)))
            state['consecutive_failures']=state.get('consecutive_failures',0)+attempts
            if chat:
                state['recovery_chat'] = chat
            state['last_send_error'] = str(receipt.error or '')[:240]
            self._save(account,state)
            self.record(account,'send_failed',
                        f'发送在提交前失败；本次明确失败尝试 {attempts} 次')
            if state['consecutive_failures'] >= 2 or state.get('recovery_status') == 'awaiting_send':
                if (getattr(settings, 'send_auto_recovery_enabled', True)
                        and not state.get('recovery_used') and not state.get('hold_reason')):
                    state.update(recovery_status='pending', recovery_requested_at=time.time(),
                                 recovery_used=True, recovery_error='')
                    self._save(account, state)
                    self.record(account, 'ui_recovery_requested', '连续2次明确发送失败，准备返回微信主页面并刷新界面状态', chat=chat, level='info')
                    callback = getattr(self, 'recovery_callback', None)
                    if callback:
                        callback()
                elif state.get('recovery_status') not in ('pending', 'recovering'):
                    self.hold(account, 'failure_circuit',
                        '微信界面恢复后仍明确发送失败，已暂停；请在顶部“异常与恢复”处理'
                        if state.get('recovery_used') else
                        '连续2次明确发送失败，已暂停；请在顶部“异常与恢复”处理')

    def login_request(self, account, settings):
        if not settings.safety_enabled:
            return True
        count = self.store.db.execute("SELECT count(*) FROM safety_events WHERE account=? AND code='login_request' AND created>?",
                                      (account,time.time()-600)).fetchone()[0]
        if count >= 7:
            self.record(account,'login_rate_limit','本地保护：10分钟内已请求7次登录，暂停继续刷新二维码')
            return False
        self.record(account,'login_request','用户请求重新登录微信',level='info')
        return True

    def snapshot(self, account, settings):
        counts,rows,_ = self.activity(account)
        per_chat = {}
        for row in rows:
            if row['kind'] != 'image' and row['sent_at']>time.time()-3600:
                per_chat[row['chat']]=per_chat.get(row['chat'],0)+1
        limits_enabled = settings.safety_enabled and settings.safety_send_limits_enabled
        limited_chats = [{'chat':chat,'count':n} for chat,n in per_chat.items() if
                         limits_enabled and settings.safety_chat_per_hour>0 and n>=settings.safety_chat_per_hour]
        rate_limited = limits_enabled and bool(limited_chats or
            (settings.safety_per_minute>0 and counts['minute']>=settings.safety_per_minute) or
            (settings.safety_per_hour>0 and counts['hour']>=settings.safety_per_hour) or
            (settings.safety_per_day>0 and counts['day']>=settings.safety_per_day))
        state = self.state(account)
        events = [dict(row) for row in self.store.db.execute('''SELECT created,level,code,reason,chat
            FROM safety_events WHERE account=? ORDER BY id DESC LIMIT 20''', (account,))]
        return {'enabled':settings.safety_enabled,'hold_reason':state.get('hold_reason',''),
                'hold_code':state.get('hold_code',''),'hold_since':state.get('hold_since'),
                'counts':counts,'last_check':self.last_check,'check_error':self.check_error,
                'limited_chats':limited_chats,'rate_limited':rate_limited,
                'limits_enabled':limits_enabled,
                'client_warning':self.last_notice,
                'consecutive_failures':state.get('consecutive_failures',0),'events':events,
                'limits':{'minute':settings.safety_per_minute,'hour':settings.safety_per_hour,
                          'chat_hour':settings.safety_chat_per_hour,'day':settings.safety_per_day,
                          'duplicate_seconds':settings.safety_duplicate_seconds,
                          'text_hour':settings.text_per_hour,'text_day':settings.text_per_day,
                          'sticker_hour':settings.sticker_per_hour,'sticker_day':settings.sticker_per_day}}
