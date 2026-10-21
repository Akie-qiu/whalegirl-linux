"""Initialize image access from the logged-in owner's native process; print counts only."""
import subprocess

CODE = r'''
import ctypes,ctypes.util,json,re,subprocess,uuid,urllib.request
from pathlib import Path
root=Path('/data');token=(root/'auth-token').read_text().strip()
req=urllib.request.Request('http://127.0.0.1:6174/api/status/auth',headers={'Authorization':'Bearer '+token})
with urllib.request.urlopen(req,timeout=10) as response:auth=json.load(response)
account=auth.get('loggedInUser')
if auth.get('status')!='logged_in' or not re.fullmatch('[A-Za-z0-9_]+',account or ''):raise RuntimeError('Login required')
base=Path('/home/wechat/xwechat_files')/account
if not base.exists():base=Path('/home/wechat/Documents/xwechat_files')/account
targets=[]
for p in sorted(base.rglob('*_t.dat'),key=lambda p:p.stat().st_mtime,reverse=True)[:12]:
 raw=p.read_bytes()
 if raw[:6]==bytes.fromhex('070856320807') and len(raw)>31:targets.append(raw)
if not targets:raise RuntimeError('No native image available')
crypto=ctypes.CDLL(ctypes.util.find_library('crypto'))
class AESKey(ctypes.Structure):
 _fields_=[('rd_key',ctypes.c_uint*60),('rounds',ctypes.c_int)]
crypto.AES_set_decrypt_key.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.POINTER(AESKey)]
crypto.AES_decrypt.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(AESKey)]
seen=set();tested=0;found=None
def check(value):
 global tested
 prefix=value[:16]
 if prefix in seen or len(set(prefix))<6:return None
 seen.add(prefix);tested+=1;schedule=AESKey()
 if crypto.AES_set_decrypt_key(prefix,128,ctypes.byref(schedule)):return None
 for raw in targets:
  out=ctypes.create_string_buffer(16);crypto.AES_decrypt(raw[15:31],out,ctypes.byref(schedule));head=out.raw
  if head.startswith(b'\xff\xd8\xff') and raw[-2]^0xff==raw[-1]^0xd9:
   return value.decode().ljust(32,'0'),raw[-1]^0xd9
 return None
pids=subprocess.check_output(['pgrep','-x','wechat'],text=True).split()
for p in Path('/proc').iterdir():
 if p.name.isdigit():
  try:
   if (p/'comm').read_text().strip() in ['WeChatAppEx','wxutility'] and (p/'status').read_text().find('VmRSS:')>=0:pids.append(p.name)
  except OSError:pass
scanned=0
for pid in pids[:8]:
 try:
  regions=[]
  for line in Path('/proc',pid,'maps').read_text().splitlines():
   parts=line.split()
   if not parts[1].startswith('rw'):continue
   start,end=[int(x,16) for x in parts[0].split('-')]
   if end-start<=128*1024*1024:regions.append((start,end))
  with open('/proc/'+pid+'/mem','rb',buffering=0) as mem:
   for start,end in regions:
    for pos in range(start,end,4*1024*1024):
     try:mem.seek(pos);data=mem.read(min(4*1024*1024+64,end-pos))
     except OSError:continue
     scanned+=len(data)
     for match in re.finditer(rb'[0-9a-fA-F]{16,}',data):
      run=match.group(0)
      for offset in range(0,min(len(run)-15,64)):
       found=check(run[offset:offset+32])
       if found:break
      if found:break
     if found:break
    if found:break
 except (OSError,ValueError):pass
 if found:break
if not found:
 print(json.dumps({'image_key_initialized':False,'candidate_count':tested,'scanned_mib':round(scanned/1048576,1)}));raise SystemExit(2)
key,xor=found
commands=['.bail on','.timeout 5000',"PRAGMA key = '"+token+"';",'BEGIN IMMEDIATE;']
for name,value in [('_image_aes',key),('_image_xor',format(xor,'02x'))]:
 if not re.fullmatch('[0-9a-fA-F]+',value):raise RuntimeError('Invalid image access metadata')
 commands.append(f"INSERT INTO wechat_keys (id,session_id,account_dir,db_name,hex_key,verified_at) SELECT '{uuid.uuid4()}',id,logged_in_user,'{name}','{value}',datetime('now') FROM sessions WHERE name='default' AND logged_in_user='{account}' ON CONFLICT(session_id,account_dir,db_name) DO UPDATE SET hex_key=excluded.hex_key,verified_at=excluded.verified_at;")
commands+=['COMMIT;']
p=subprocess.run(['/usr/local/bin/sqlcipher','/data/agent.db'],input='\n'.join(commands)+'\n',capture_output=True,text=True,timeout=15)
if p.returncode:raise RuntimeError('Encrypted image key registration failed')
print(json.dumps({'image_key_initialized':True,'candidate_count':tested,'scanned_mib':round(scanned/1048576,1)}))
'''

if __name__ == '__main__':
    result = subprocess.run(['docker','exec','-i','whalegirl-wechat-candidate-4118','python3','-u','-'],
                            input=CODE,capture_output=True,text=True,timeout=60)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode:
        raise SystemExit('Image access initialization incomplete; credentials retained on server only')
