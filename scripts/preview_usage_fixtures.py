"""Clearly synthetic dashboard data, only imported by the isolated UI preview."""
from datetime import datetime, timedelta
import time

from whalegirl_linux.token_usage import TZ
from whalegirl_linux.usage_context import CURRENT_USAGE, set_usage_context


def seed_usage(runtime, chats):
    now=time.time()
    day=datetime.fromtimestamp(now,TZ).replace(hour=0,minute=0,second=0,microsecond=0)
    runtime.chat_labels.update({chat['id']:chat['name'] for chat in chats})
    account=runtime.config.settings.account_id
    index=2000
    for offset in range(7):
        for hour in range(24):
            created=(day-timedelta(days=offset)+timedelta(hours=hour)).timestamp()
            if created>now:continue
            amount=(3,5,7,9,8,4,2)[offset] if 8<=hour<=23 else 1
            for n in range(amount):
                chat=chats[(offset+hour+n)%len(chats)]['id']
                identity=f'usage-preview-{index}';index+=1
                token=CURRENT_USAGE.set({})
                try:
                    set_usage_context(account,chat,identity)
                    incoming=3000+(hour*233+offset*571+n*143)%4000
                    outgoing=60+(hour*13+n*7)%180
                    failed=index%31==0
                    runtime.token_ledger.record(account,dict(created=created,provider='synthetic-relay.example',
                        model='claude-sonnet-5-5' if index%7==0 else 'deepseek-v4.1-flash',
                        operation='growth_learn' if index%13==0 else 'chat',
                        status='error' if failed else 'success',elapsed_ms=1500+(hour*109+n*337)%9000,
                        input_tokens=None if failed else incoming,output_tokens=None if failed else outgoing,
                        total_tokens=None if failed else incoming+outgoing,cached_tokens=None if failed else incoming-700,
                        uncached_tokens=None if failed else 700))
                finally:CURRENT_USAGE.reset(token)
                for part in range(2):
                    runtime.store.observe([dict(key=identity+'-human-'+str(part),account=account,chat=chat,
                        sender='合成示例',sender_id='preview-person-'+str(n%5),kind=1,is_self=0,
                        local_id=index*10+part,content='合成看板测试记录，不是真实聊天',timestamp=created)])
                state='uncertain' if index%37==0 else 'failed' if failed else 'sent'
                kind='audio' if index%29==0 else 'image' if index%17==0 else 'sticker' if index%3==0 else 'text'
                runtime.store.db.execute('INSERT INTO outbound_actions(id,inbox_key,account,chat,kind,reply,state,sent_at) '
                    'VALUES(?,?,?,?,?,?,?,?)',(identity,('proactive:' if index%11==0 else '')+identity,
                     account,chat,kind,'合成示例',state,created))
    runtime.store.db.commit()
