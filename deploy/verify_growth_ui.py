"""Disposable local UI fixture. No background work, network, account or test messages."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uvicorn

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from preview_learning_ui import Gateway
from whalegirl_linux.app import create_app
from whalegirl_linux.memory_layers import WORLD
from whalegirl_linux.settings import Settings


def main():
    data=Path(tempfile.mkdtemp(prefix='ui-',dir=ROOT/'artifacts/autonomous-growth'))
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    port=sock.getsockname()[1]
    os.environ['WHALEGIRL_PUBLIC_ORIGINS']='http://127.0.0.1:'+str(port)
    app=create_app(data,gateway=Gateway(),run_background=False)
    c,r=app.state.config,app.state.runtime;c.admin_token='ui-test-only'
    c.save(Settings(enabled=True,account_id='preview',allowed_chat_ids=['preview@chatroom']),'ui-fixture-only')
    row=r.memory_book.change('preview',[WORLD],{'chat':WORLD,'subject':'self','kind':'persona',
        'content':'演示：先理解具体处境，再决定怎样回应','source_key':'world:demo-source','venue':'any'})
    with r.store.lock,r.store.db:
        r.store.db.execute("UPDATE memories SET source_class='world_learning',role_id='default',venue='any' WHERE id=?",(row['id'],))
        r.store.db.execute('INSERT INTO growth_sources VALUES(?,?,?,?,?,?,?,?,?)',
            ('demo-source','preview','default','web','https://example.org/demo','演示公开来源','这是一份本地演示正文，不包含真实聊天内容。','demo',time.time()))
        r.store.db.execute('INSERT INTO growth_journals VALUES(?,?,?,?,?,?,?,?)',
            ('demo-journal','demo-run','preview','default','演示学习',json.dumps(['demo-source']),json.dumps({'revision':'尝试先理解具体处境'},ensure_ascii=False),time.time()))
        r.store.db.execute('INSERT INTO growth_insights VALUES(?,?,?,?,?,?,?)',
            (row['id'],'demo-journal','persona','演示学习','对方倾诉时','纯粹分享快乐时不说教',time.time()))
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.05)
        result=subprocess.run(['C:/Program Files/nodejs/node.exe',str(ROOT/'deploy/verify_growth_ui.cjs'),
            'http://127.0.0.1:'+str(port)],capture_output=True,text=True,encoding='utf-8',timeout=60)
        print(result.stdout);print(result.stderr[-2500:] if result.returncode else '')
        if result.returncode:raise RuntimeError('Growth UI check failed')
    finally:server.should_exit=True;thread.join(5)


if __name__=='__main__':main()
