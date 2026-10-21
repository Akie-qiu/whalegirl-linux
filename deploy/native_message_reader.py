"""Read authorized native messages, including committed WAL rows, over a private socket."""
import ctypes
import base64
import fcntl
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import queue
import socketserver
import signal
import subprocess
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit
import urllib.request
import xml.etree.ElementTree as ET
from message_payload import parse_app_message


ROOT = Path('/data/whalegirl-jobs')
CONFIG = json.loads(Path('/data/native-tools/native-reader-config.json').read_text())
ACCOUNT = CONFIG['account']
ALLOWED = set(CONFIG['allowed_chats'])
credentials = json.loads(Path('/data/acceptance-keys.json').read_text())
if (not re.fullmatch(re.escape(credentials['account']) + r'(?:_[A-Za-z0-9]{4})?', ACCOUNT)
        or not re.fullmatch(r'[A-Za-z0-9_]+', ACCOUNT)):
    raise RuntimeError('Native reader account mismatch')
KEYS = credentials['keys']
for key in KEYS.values():
    if not re.fullmatch(r'[0-9a-fA-F]{64}', key):
        raise RuntimeError('Invalid native database credential')
BASES = [Path('/home/wechat/xwechat_files') / ACCOUNT / 'db_storage',
         Path('/home/wechat/Documents/xwechat_files') / ACCOUNT / 'db_storage']
BASE = next(p for p in BASES if p.is_dir())
TOKEN = Path('/data/auth-token').read_text().strip()
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ZSTD = ctypes.CDLL('libzstd.so.1')
ZSTD.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
ZSTD.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
ZSTD.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
ZSTD.ZSTD_decompress.restype = ctypes.c_size_t
ZSTD.ZSTD_isError.argtypes = [ctypes.c_size_t]
ZSTD.ZSTD_isError.restype = ctypes.c_uint
NAMES = {}
DATABASES = {}
DATABASE_LOCK = threading.RLock()
TABLE_DATABASES = {}
TABLE_SCANNED = {}
AUTH_LOCK = threading.RLock()
AUTH_UNTIL = 0


class ReadOnlyDatabase:
    """Keep the cipher connection open; every SELECT still sees the latest WAL."""
    def __init__(self, name):
        self.name = name
        self.lock = threading.RLock()
        self.process = None

    def start(self):
        path = next(BASE.glob('*/' + self.name))
        self.process = subprocess.Popen(
            ['/usr/local/bin/sqlcipher', '-readonly', 'file:' + str(path) + '?mode=ro'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1)
        self.output = queue.Queue()
        output, process = self.output, self.process
        def consume():
            for line in process.stdout:
                output.put(line)
            output.put(None)
        threading.Thread(target=consume, daemon=True).start()
        self.process.stdin.write('.bail off\n.timeout 200\nPRAGMA key = "x\'' + KEYS[self.name] +
                                 '\'";\nPRAGMA cipher_compatibility=4;\nPRAGMA query_only=ON;\n.mode json\n')
        self.process.stdin.flush()

    def query(self, sql):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.start()
            marker = 'whale_end_' + uuid.uuid4().hex
            try:
                self.process.stdin.write(sql + '\n.print ' + marker + '\n')
                self.process.stdin.flush()
                lines = []
                deadline = time.monotonic() + 3
                while True:
                    line = self.output.get(timeout=max(.01, deadline-time.monotonic()))
                    if line is None:
                        raise RuntimeError('Native database connection ended')
                    if line.strip() == marker:
                        break
                    lines.append(line)
                value = ''.join(lines)
                if re.search(r'(?mi)^(?:Parse error|Runtime error|Error:)', value):
                    raise RuntimeError('Native database rejected query')
                start = value.find('[')
                return json.loads(value[start:]) if start >= 0 else []
            except Exception:
                self.process.terminate()
                self.process.wait(timeout=2)
                self.process = None
                raise RuntimeError('Read-only native query failed') from None

    def close(self):
        with self.lock:
            process, self.process = self.process, None
            if process is None:
                return
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()


def query(name, sql):
    with DATABASE_LOCK:
        database = DATABASES.setdefault(name, ReadOnlyDatabase(name))
    return database.query(sql)


def decode(value, compressed):
    raw = bytes.fromhex(value or '')
    if compressed and raw.startswith(b'\x28\xb5\x2f\xfd'):
        size = ZSTD.ZSTD_getFrameContentSize(raw, len(raw))
        capacity = size if size < 4 * 1024 * 1024 else 4 * 1024 * 1024
        target = ctypes.create_string_buffer(capacity)
        count = ZSTD.ZSTD_decompress(target, capacity, raw, len(raw))
        if ZSTD.ZSTD_isError(count):
            return '[compressed content unavailable]'
        raw = target.raw[:count]
    return raw.decode('utf-8', errors='replace')


def xml_tag(text, name):
    match = re.search('<' + name + r'>(.*?)</' + name + '>', text, re.S)
    if not match:
        return ''
    return re.sub(r'^<!\[CDATA\[|\]\]>$', '', match[1]).strip()


def is_account(wxid):
    return bool(wxid and (wxid == ACCOUNT or wxid == credentials['account']))


def allowed_chats():
    path = ROOT / 'authorized-chats.json'
    if not path.exists():
        return ALLOWED
    value = json.loads(path.read_text())
    if value.get('account_id') != ACCOUNT:
        raise PermissionError('Native reader account authorization changed')
    return set(value.get('allowed_chat_ids') or [])


def authorized(chat):
    global AUTH_UNTIL
    if chat not in allowed_chats():
        raise PermissionError('Conversation is not authorized')
    with AUTH_LOCK:
        if time.monotonic() < AUTH_UNTIL:
            return
        request = urllib.request.Request('http://127.0.0.1:6174/api/status/auth',
                                         headers={'Authorization': 'Bearer ' + TOKEN})
        with OPENER.open(request, timeout=2) as response:
            auth = json.load(response)
        if auth.get('status') != 'logged_in' or auth.get('loggedInUser') != ACCOUNT:
            raise PermissionError('Authorized account is not logged in')
        # Reading fixed authorized databases does not need a screenshot every poll.
        # The core still performs a fresh account check before every real send.
        AUTH_UNTIL = time.monotonic() + 30


def message_database(chat):
    names,table=message_databases(chat)
    return (names[0] if names else None),table


def message_databases(chat):
    table = 'Msg_' + hashlib.md5(chat.encode()).hexdigest()
    if table not in TABLE_DATABASES or time.monotonic()-TABLE_SCANNED.get(table,0)>60:
        names=[]
        for name in sorted(KEYS):
            if re.fullmatch(r'message_\d+\.db', name) and query(name,
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='" + table + "';"):
                names.append(name)
        TABLE_DATABASES[table]=names;TABLE_SCANNED[table]=time.monotonic()
    return TABLE_DATABASES.get(table,[]), table


class StickerRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        sticker_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sticker_url(value):
    url = urlsplit(value)
    if (url.scheme not in ('http', 'https') or url.username or url.password or
            url.hostname not in ('vweixinf.tc.qq.com', 'wxapp.tc.qq.com', 'snsvideo.c2c.wechat.com', 'emoji.qpic.cn') or
            url.port not in (None, 80, 443)):
        raise ValueError('Unsupported native sticker source')
    return value


def sticker_media(chat, local_id):
    authorized(chat)
    name, table = message_database(chat)
    if not name:
        return {}
    rows = query(name, f'SELECT local_type,hex(message_content) AS body,WCDB_CT_message_content AS compressed FROM "{table}" WHERE local_id={int(local_id)};')
    if len(rows) != 1 or (int(rows[0]['local_type']) & 0xffffffff) != 47:
        return {}
    body = decode(rows[0]['body'], rows[0]['compressed'])
    root = ET.fromstring(body[body.index('<'):])
    emoji = root.find('.//emoji')
    if emoji is None:
        return {}
    return fetch_sticker(emoji.get('md5', '').lower(), emoji.get('cdnurl', ''), emoji.get('desc', ''))


def fetch_sticker(md5, url, description=''):
    if not re.fullmatch(r'[0-9a-f]{32}', md5):
        return {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), StickerRedirects())
    with opener.open(sticker_url(url), timeout=4) as response:
        raw = response.read(4*1024*1024+1)
    if len(raw) > 4*1024*1024 or hashlib.md5(raw).hexdigest() != md5:
        raise ValueError('Native sticker integrity mismatch')
    return {'type': 'emoji', 'data': base64.b64encode(raw).decode(),
            'quality': 'native_sticker', 'md5': md5, 'description': description}


def favorites():
    scope = allowed_chats()
    if not scope:
        return []
    authorized(next(iter(scope)))
    return query('emoticon.db','SELECT f.md5,e.caption FROM kFavEmoticonOrderTable f LEFT JOIN kNonStoreEmoticonTable e ON f.md5=e.md5 ORDER BY f.rowid DESC;')


def favorite_media(md5):
    if not re.fullmatch('[0-9a-f]{32}',md5) or md5 not in {r['md5'] for r in favorites()}:
        raise ValueError('Sticker is not a native favorite')
    rows=query('emoticon.db',"SELECT cdn_url,caption FROM kNonStoreEmoticonTable WHERE md5='"+md5+"';")
    return fetch_sticker(md5,rows[0]['cdn_url'],rows[0].get('caption','')) if len(rows)==1 else {}


def messages(chat, limit, offset=0, *, before_time=None, before_id=0, after_time=None, after_id=0, server_id=None):
    authorized(chat)
    databases,table=message_databases(chat)
    rows = []
    for name in databases:
        conditions=[]
        if before_time is not None:
            conditions.append(f'(m.create_time<{int(float(before_time))} OR (m.create_time={int(float(before_time))} AND m.local_id<{int(before_id)}))')
        if after_time is not None:
            # Re-read the boundary second: server IDs and bodies can be enriched later.
            conditions.append(f'(m.create_time>{int(float(after_time))} OR '
                              f'(m.create_time={int(float(after_time))} AND m.local_id>{int(after_id)}))')
        if server_id is not None:conditions.append(f'm.server_id={int(server_id)}')
        where='WHERE '+' AND '.join(conditions) if conditions else ''
        ordering='ASC' if after_time is not None else 'DESC'
        rows.extend(query(name, f'''SELECT m.local_id,m.server_id,m.local_type,m.create_time,
            hex(m.message_content) AS body,m.WCDB_CT_message_content AS compressed,
            hex(m.source) AS source,m.WCDB_CT_source AS source_compressed,n.user_name AS sender
            FROM "{table}" m LEFT JOIN Name2Id n ON m.real_sender_id=n.rowid
            {where} ORDER BY m.create_time {ordering},m.local_id {ordering} LIMIT {int(limit)+int(offset)};'''))
    unique={}
    for r in sorted(rows,key=lambda r:(r['create_time'],r['local_id']),reverse=after_time is None):
        identity=str(r['server_id']) if r.get('server_id') else 'local:'+str(r['local_id'])
        if identity not in unique:unique[identity]=r
    rows=list(unique.values())[int(offset):int(offset)+int(limit)]
    unknown = {r['sender'] for r in rows if r.get('sender') and r['sender'] not in NAMES}
    if unknown and 'contact.db' in KEYS:
        names = ','.join("'" + value.replace("'", "''") + "'" for value in unknown)
        for row in query('contact.db', f'SELECT username,nick_name,remark FROM contact WHERE username IN ({names});'):
            NAMES[row['username']] = row.get('remark') or row.get('nick_name') or row['username']
    output = []
    for row in rows:
        kind = int(row['local_type']) & 0xffffffff
        body = decode(row['body'], row['compressed'])
        source = decode(row['source'], row['source_compressed'])
        sender = row.get('sender') or ''
        if chat.endswith('@chatroom') and ':\n' in body[:80]:
            body = body.split(':\n', 1)[1]
        # Forwarded/quoted messages may contain an old @; only outer message metadata can wake the bot.
        mentioned = any(wxid.strip() and ACCOUNT.startswith(wxid.strip())
                        for wxid in xml_tag(source, 'atuserlist').split(','))
        reply = None
        app = parse_app_message(body) if kind==49 else {}
        pat = None
        if kind in (49, 10000, 10002) and ('<patinfo>' in body or '<pat>' in body):
            try:
                xml = ET.fromstring(body[body.index('<'):])
                info = xml.find('.//patinfo')
                if info is None:
                    info = xml.find('.//pat')
                if info is not None:
                    sender = info.findtext('fromusername') or sender
                    target = info.findtext('pattedusername') or ''
                    pat = {'sender': sender, 'target': target, 'isForMe': is_account(target)}
                    mentioned = pat['isForMe']
            except (ValueError, ET.ParseError):
                pass
        reply=app.get('reply')
        if kind == 3:
            content = ''
        elif kind == 47:
            content = '[emoji]'
        elif kind == 49:
            content = app.get('content') or xml_tag(body,'title') or '[微信卡片内容暂不可解析]'
        else:
            content = body
        value = {'localId': row['local_id'], 'serverId': row['server_id'], 'chatId': chat,
                 'sender': sender, 'senderName': NAMES.get(sender, sender), 'type': kind,
                 'content': content, 'timestamp': datetime.fromtimestamp(row['create_time'], timezone.utc).isoformat(),
                 'isMentioned': mentioned, 'isSelf': is_account(sender)}
        if not sender:
            value.pop('isSelf')
        if reply:
            value['reply'] = reply
        if app.get('forwarded'):
            value['forwarded']=app['forwarded']
        if app.get('appType'):
            value['appType']=app['appType']
        if pat:
            value['event'] = 'pat'
            value['pat'] = pat
            value['content'] = xml_tag(body, 'title') or ('拍了拍机器人' if pat['isForMe'] else '拍了拍其他人')
        if kind == 47:
            try:
                emoji = ET.fromstring(body[body.index('<'):]).find('.//emoji')
                if emoji is not None:
                    value['sticker'] = {'md5': emoji.get('md5', ''), 'description': emoji.get('desc', '')}
            except (ValueError, ET.ParseError):
                pass
        output.append(value)
    return output


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            url = urlsplit(self.path)
            params = parse_qs(url.query)
            if url.path == '/stickers':
                result = {'stickers':favorites()}
            elif url.path == '/favorite-media':
                result = favorite_media(params['md5'][0])
            elif url.path == '/media':
                result = sticker_media(params['chat'][0], int(params['local_id'][0]))
            elif url.path == '/messages':
                cursors={k:params[k][0] for k in ('before_time','before_id','after_time','after_id','server_id') if k in params}
                value = messages(params['chat'][0], max(1, min(200, int(params.get('limit', ['100'])[0]))),
                             max(0, min(20000, int(params.get('offset', ['0'])[0]))),**cursors)
                result = {'account': ACCOUNT, 'messages': value}
            else:
                raise ValueError('Unsupported native read route')
            payload = json.dumps(result, ensure_ascii=False).encode()
            status = 200
        except Exception as exc:
            payload = json.dumps({'error': type(exc).__name__}).encode()
            status = 503
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    # Keep one listener per data directory, including across docker exec restarts.
    singleton = (ROOT / 'native-reader.lock').open('a')
    fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    name = 'native-read-' + str(time.time_ns()) + '.sock'
    endpoint = ROOT / name
    owner = ROOT.stat()
    with socketserver.ThreadingUnixStreamServer(str(endpoint), Handler) as server:
        server.daemon_threads = True
        server.block_on_close = False
        os.chown(endpoint, owner.st_uid, owner.st_gid)
        endpoint.chmod(0o600)
        pointer = ROOT / 'native-read.endpoint'
        pointer.write_text(name)
        os.chown(pointer, owner.st_uid, owner.st_gid)
        pointer.chmod(0o600)
        print(json.dumps({'native_reader_ready': True, 'account': ACCOUNT, 'authorized_chats': len(ALLOWED)}), flush=True)
        try:
            server.serve_forever(poll_interval=.2)
        finally:
            with DATABASE_LOCK:
                for database in DATABASES.values():
                    database.close()
            singleton.close()
