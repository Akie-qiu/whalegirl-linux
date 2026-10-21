"""Focused safeguards checks in a retained, isolated database; no WeChat sends."""
import asyncio
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from whalegirl_linux.gateway import Receipt
from whalegirl_linux.safety import SafetyGuard, client_warning
from whalegirl_linux.settings import Settings
from whalegirl_linux.store import Store

folder=Path(tempfile.mkdtemp(prefix='safety-',dir=ROOT/'test-runs'))
store=Store(folder/'state.sqlite3')
guard=SafetyGuard(store)
now=time.time()
settings=Settings(safety_per_minute=2,safety_chat_per_hour=40)

def sent(key,text,when):
    event={'key':key,'account':'test','chat':'test@chatroom','sender':'test',
           'content':'test','timestamp':when,'local_id':int(key),'sender_id':'test'}
    store.insert(event,'sent')
    store.update(key,'sent',reply=text,receipt=key)
    store.db.execute('UPDATE inbox SET updated=? WHERE key=?',(when,key))
    store.db.commit()

sent('1','这是重复回复',now-10)
assert guard.check('test','test@chatroom',settings,now=now)['allowed']
assert guard.check('test','test@chatroom',settings,now=now,reply='这是 重复回复')['code']=='duplicate_reply'
sent('2','第二个回复',now-5)
assert guard.check('test','test@chatroom',settings,now=now)['code']=='minute_limit'
assert guard.check('test','test@chatroom',settings,now=now+61)['allowed']
unlimited=settings.model_copy(update={'safety_per_minute':0,'safety_per_hour':0,'safety_chat_per_hour':0,'safety_per_day':0})
assert guard.check('test','test@chatroom',unlimited,now=now)['allowed']
no_limits=settings.model_copy(update={'safety_send_limits_enabled':False})
assert guard.check('test','test@chatroom',no_limits,now=now)['allowed']
assert guard.check('test','test@chatroom',no_limits,now=now,reply='这是重复回复')['code']=='duplicate_reply'
assert guard.check('test','test@chatroom',settings.model_copy(update={'safety_per_minute':60,'safety_chat_per_hour':2}),now=now)['code']=='chat_hour_limit'
failure=Receipt(False,error='pre-send failure',definite_failure=True)
for _ in range(3):guard.outcome('failure-test',failure,settings)
assert SafetyGuard(store).held('failure-test')
guard.resume('failure-test');assert not guard.held('failure-test')
assert all(guard.login_request('login-test',settings) for _ in range(3))
assert not guard.login_request('login-test',settings)
assert client_warning('操作过于频繁，请稍后再试')
assert client_warning('This account has been banned')
assert not client_warning('网络连接失败，请检查网络设置')
assert guard.check('test','other@chatroom',settings.model_copy(update={'safety_per_minute':60}),reply='这是重复回复',now=now)['allowed']
store.close()
print('Rate limits, duplicate suppression, persistent circuit breaker, login throttle, and native-warning matching passed.')
print('Retained isolated database:',folder)
