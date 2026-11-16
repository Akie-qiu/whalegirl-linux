"""Read-only production preflight; no runtime/config/store instantiation."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import urllib.request

BASE=Path('/opt/whalegirl-linux')
DATA=Path('/var/lib/whalegirl-linux/core')
CURRENT=(BASE/'current').resolve()
FILES=['whalegirl_linux/'+name for name in ('app.py','runtime.py','proactive.py','proactive_tools.py',
    'token_usage.py','studio.js','studio.css','web.html','console.js','console.css','sticker-library.js',
    'personas.css','sticker-library.css','images.css','recovery.css','usage_dashboard.py','usage_tools.py',
    'usage_pricing.py','usage_context.py','usage-dashboard.js','usage-dashboard.css','assets/studio-background.jpg')]


def command(*args):
    return subprocess.check_output(args,text=True,timeout=20).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def api(path):
    token=(DATA/'admin.token').read_text().strip()
    request=urllib.request.Request('http://127.0.0.1:8092'+path,headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)


def main():
    settings=api('/api/config');state=api('/api/state');health=api('/health')
    files={name:dict(sha256=digest(CURRENT/name),size=(CURRENT/name).stat().st_size if (CURRENT/name).is_file() else 0)
           for name in FILES}
    protected={p.name:digest(p) for p in DATA.iterdir() if p.is_file() and
               (p.suffix=='.enc' or p.suffix=='.token' or p.name in ('settings.json','persona-cards.json'))}
    with sqlite3.connect('file:'+str(DATA/'state.sqlite3')+'?mode=ro',uri=True) as db:
        counts={}
        for table in ('outbound_actions','inbox','moments_actions','picture_jobs','audio_jobs','growth_runs'):
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                counts[table]={r[0]:r[1] for r in db.execute('SELECT state,count(*) FROM '+table+
                    " WHERE state IN ('sending','generating','planning','queued','uncertain') GROUP BY state")}
        token_rows=db.execute('SELECT count(*) FROM model_token_usage').fetchone()[0]
    disk=shutil.disk_usage('/')
    services={name:dict(line.split('=',1) for line in command('systemctl','show',name,'-p','ActiveState','-p','SubState',
              '-p','MainPID','-p','WorkingDirectory').splitlines()) for name in
              ('whalegirl-linux.service','whalegirl-native-reader.service')}
    container=command('docker','inspect','--format','{{.Id}} {{.State.Status}} {{.RestartCount}}','whalegirl-wechat-candidate-4118')
    print(json.dumps(dict(current=str(CURRENT),services=services,container=container,
        health=health,logged_in=state.get('gateway',{}).get('status'),paused=bool(state.get('paused_reason')),
        processing=bool(state.get('processing')),config=dict(enabled=settings.get('enabled'),model=settings.get('model'),
        api_style=settings.get('api_style'),authorized_chats=len(settings.get('allowed_chat_ids',[]))),
        disk=dict(total=disk.total,free=disk.free),database_files={p.name:p.stat().st_size for p in DATA.glob('state.sqlite3*')
        if p.is_file()},inflight=counts,token_rows=token_rows,protected_hashes=protected,files=files),ensure_ascii=False))


if __name__=='__main__':main()
