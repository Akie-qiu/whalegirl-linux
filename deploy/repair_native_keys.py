"""Initialize text-message access for the owner's logged-in 4.1.1.8 account.

Run on the Linux host. Credentials stay inside the gateway container and its
encrypted agent database; stdout contains counts only. No messages are sent.
"""
import subprocess

CODE = r'''
import contextlib,importlib.util,io,json,os,re,subprocess,sys,urllib.request,uuid
from pathlib import Path
root=Path('/data')
token=(root/'auth-token').read_text().strip()
if not re.fullmatch('[0-9a-f]{64}',token):raise RuntimeError('Unexpected credential format')
def auth():
    request=urllib.request.Request('http://127.0.0.1:6174/api/status/auth',headers={'Authorization':'Bearer '+token})
    with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)
before=auth();account=before.get('loggedInUser')
if before.get('status')!='logged_in' or not re.fullmatch(r'[A-Za-z0-9_]+',account or ''):
    raise RuntimeError('A real logged-in account is required')
os.environ['HOME']='/home/wechat'
module_spec=importlib.util.spec_from_file_location('native_keys','/opt/tools/extract-keys.py')
module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
pid=subprocess.check_output(['pgrep','-x','wechat'],text=True).split()[0]
if module.find_active_account(pid)!=account:raise RuntimeError('Native and registered accounts differ')
# SQLCipher page authentication verifies each candidate before the native DB
# read. This path is for text messages; image key setup is deliberately absent.
module.get_build_profile=lambda pid:{'db_page_hmac':True}
module.extract_image_keys=lambda pid,profile:{}
output=root/'text-access-keys.json'
sys.argv=['extract-keys.py','--pid',pid,'--output',str(output)]
with contextlib.redirect_stdout(io.StringIO()):module.main()
output.chmod(0o600)
keys=json.loads(output.read_text())['keys']
if not {'session.db','contact.db','message_0.db'}.issubset(keys):raise RuntimeError('Required keys missing')
if auth().get('loggedInUser')!=account:raise RuntimeError('Account changed during initialization')
commands=['.bail on','.timeout 5000',"PRAGMA key = '"+token+"';",'PRAGMA foreign_keys = ON;','BEGIN IMMEDIATE;']
for name,key in keys.items():
    if not re.fullmatch(r'[A-Za-z0-9_]+\.db',name) or not re.fullmatch('[0-9a-f]{64}',key):
        raise RuntimeError('Unexpected database metadata')
    commands.append(f"INSERT INTO wechat_keys (id,session_id,account_dir,db_name,hex_key,verified_at) SELECT '{uuid.uuid4()}',id,logged_in_user,'{name}','{key}',datetime('now') FROM sessions WHERE name='default' AND logged_in_user='{account}' ON CONFLICT(session_id,account_dir,db_name) DO UPDATE SET hex_key=excluded.hex_key,verified_at=excluded.verified_at;")
commands+=['COMMIT;',f"SELECT count(*) FROM wechat_keys WHERE account_dir='{account}';"]
result=subprocess.run(['/usr/local/bin/sqlcipher','/data/agent.db'],input='\n'.join(commands)+'\n',capture_output=True,text=True,timeout=20)
if result.returncode:raise RuntimeError('Encrypted gateway registration failed')
print(json.dumps({'registered_database_count':int(result.stdout.strip().splitlines()[-1]),'text_access_initialized':True}))
'''

if __name__ == "__main__":
    result = subprocess.run(["docker", "exec", "--interactive", "whalegirl-wechat-candidate-4118", "python3", "-u", "-"],
                            input=CODE, capture_output=True, text=True, timeout=150)
    if result.returncode:
        # Do not expose scanner output, key material, or SQL command text.
        raise SystemExit("Native text access initialization failed; inspect privately on the host")
    print(result.stdout.strip())
