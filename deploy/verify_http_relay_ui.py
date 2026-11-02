"""Local browser check for HTTP model saving and zero pacing; no real sends."""
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
    out=ROOT/'artifacts/http-relay-zero';out.mkdir(parents=True,exist_ok=True)
    data=Path(tempfile.mkdtemp(prefix='ui-',dir=out))
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    port=sock.getsockname()[1];os.environ['WHALEGIRL_PUBLIC_ORIGINS']='http://127.0.0.1:'+str(port)
    app=create_app(data,gateway=Gateway(),run_background=False);c=app.state.config;c.admin_token='ui-test-only'
    c.save(Settings(account_id='preview',allowed_chat_ids=['preview@chatroom']),'ui-fixture-only')
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.05)
        run=subprocess.run(['C:/Program Files/nodejs/node.exe',str(ROOT/'deploy/verify_http_relay_ui.cjs'),
            'http://127.0.0.1:'+str(port)],capture_output=True,text=True,encoding='utf-8',timeout=60)
        print(run.stdout)
        if run.returncode:raise RuntimeError(run.stderr[-2000:])
    finally:server.should_exit=True;thread.join(5)


if __name__=='__main__':main()
