"""Local control fixture, with background work and real messages disabled."""
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
from whalegirl_linux.settings import Settings


def main():
    out=ROOT/'artifacts/sticker-initiative';out.mkdir(parents=True,exist_ok=True)
    data=Path(tempfile.mkdtemp(prefix='ui-',dir=out))
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    port=sock.getsockname()[1];os.environ['WHALEGIRL_PUBLIC_ORIGINS']='http://127.0.0.1:'+str(port)
    app=create_app(data,gateway=Gateway(),run_background=False);c=app.state.config;c.admin_token='ui-test-only'
    c.save(Settings(enabled=True,account_id='preview',allowed_chat_ids=['preview@chatroom']),'ui-fixture-only')
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.05)
        script=ROOT/sys.argv[1] if len(sys.argv)>1 else ROOT/'deploy/verify_sticker_initiative_ui.cjs'
        run=subprocess.run(['C:/Program Files/nodejs/node.exe',str(script),
            'http://127.0.0.1:'+str(port)],capture_output=True,text=True,encoding='utf-8',timeout=60)
        print(run.stdout)
        if run.returncode:raise RuntimeError(run.stderr[-2000:])
    finally:server.should_exit=True;thread.join(5)


if __name__=='__main__':main()
