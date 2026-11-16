"""Bounded personality projection replay: synthetic stores, real model, no sends.

Run only in a private /tmp source sandbox on Linux. Credentials stay in memory;
production chat databases and platform clients are never opened. The companion
evaluate_focus_chat harness supplies the existing Runtime/Gateway/build fixtures.
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import random
try:
    import resource
except ImportError:
    resource = None
import signal
import shlex
import statistics
import subprocess
import sys
import time
import uuid
import io
import zipfile


CASES = [
    {'id': 'music', 'title': '已有音乐兴趣的相关新话题',
     'seed': [('eval_owner', '今天总算把活干完了，想听点东西放松')],
     'sender': 'eval_owner', 'input': '今晚想听点音乐，你会挑什么？随便聊聊就行', 'trigger': 'direct'},
    {'id': 'distress', 'title': '只想吐槽，不需要建议',
     'seed': [('eval_a', '老板说开个五分钟的小会')],
     'sender': 'eval_a', 'input': '结果开了俩小时，我人都开没了。不想听建议，就让我骂两句', 'trigger': 'direct'},
    {'id': 'playful', 'title': '短句接住熟人的小事故',
     'seed': [('eval_owner', '第一次做草莓蛋糕，刚脱模')],
     'sender': 'eval_owner', 'input': '塌成草莓饼了，至少不用切了哈哈', 'trigger': 'direct'},
    {'id': 'correction', 'title': '被指出解释太多后的接话',
     'seed': [('eval_bot', '这可能需要从沟通方式和时间管理说起'),
              ('eval_owner', '我只是随口吐槽一下')],
     'sender': 'eval_owner', 'input': '又开始讲道理了，停停，我就想笑一下这破事', 'trigger': 'direct'},
    {'id': 'override', 'title': '当前选择不能被旧兴趣盖过',
     'seed': [('eval_owner', '平时那种轻柔的吉他听多了')],
     'sender': 'eval_owner', 'input': '今天就想听重金属，不想听吉他，别劝我换呀', 'trigger': 'direct'},
    {'id': 'others', 'title': '多人互相安排时可选择沉默',
     'seed': [('eval_a', '阿乙你到我楼下了吗')],
     'sender': 'eval_b', 'input': '还有五分钟，阿甲先下楼吧，我给你带了充电线', 'trigger': 'natural'},
]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')
    path.chmod(0o600)


def resources():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
                (name, ctypes.c_uint64) for name in ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')]
        class Process(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak', 'rss', 'pool_peak', 'pool', 'nonpaged_peak', 'nonpaged', 'page', 'page_peak')]
        memory = Memory();memory.length = ctypes.sizeof(memory)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
        process = Process();process.size = ctypes.sizeof(process)
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        read.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        read(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(process), process.size)
        return {'rss_mib': round(process.rss / 2**20, 3), 'peak_rss_mib': round(process.peak / 2**20, 3),
                'available_mib': round(memory.available / 2**20, 2), 'load_1m': None}
    values = {line.split(':')[0]: int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line}
    rss = next(int(line.split()[1]) / 1024 for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:'))
    return {'rss_mib': round(rss, 3), 'available_mib': round(values['MemAvailable'] / 1024, 2),
            'load_1m': os.getloadavg()[0], 'peak_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3)}


def guard():
    if resources()['available_mib'] < 128:
        raise RuntimeError('host_memory_guard')


def protected():
    core = Path('/var/lib/whalegirl-linux/core')
    return {name: hashlib.sha256((core / name).read_bytes()).hexdigest() for name in
            ('settings.json', 'persona-cards.json', 'model-profiles.json', 'model-api-keys.enc', 'bilibili-session.enc')
            if (core / name).exists()}


def configure(runtime, **updates):
    values = runtime.config.settings.model_copy(update=updates)
    runtime.config.save(values)


def fixture_runtime(args, folder, *, real):
    if os.name == 'nt':
        runtime, gateway, calls, metadata = local_build(args, folder, real=real)
    else:
        from evaluate_focus_chat import build
        runtime, gateway, calls, metadata = build(args, folder, real=real)
    # Disallow every external tool and background activity. No Runtime.start().
    updates = dict(stickers_enabled=False, editable_memory_enabled=False, learning_enabled=False,
                   web_search_enabled=False, tieba_enabled=False, bilibili_enabled=False,
                   bilibili_wechat_video_enabled=False, bilibili_comment_mentions_enabled=False,
                   images_chat_enabled=False, images_autosend=False, moments_enabled=False,
                   proactive_enabled=False, life_autonomy_enabled=False, autonomy_enabled=False,
                   memory_consolidation_enabled=False, growth_bilibili_expression_shared=True,
                   growth_tieba_expression_shared=True, growth_mature_traits_chat_enabled=True)
    configure(runtime, **{key: value for key, value in updates.items() if key in runtime.config.settings.model_fields})
    return runtime, gateway, calls, metadata


BOT = 'eval_bot'
ROOM = 'eval_group@chatroom'
NAMES = {'eval_owner': '飘渺', 'eval_a': '阿甲', 'eval_b': '阿乙', BOT: 'DeepSeek酱'}


def event(index, person, text, *, trigger='direct', stamp=None):
    return {'key': f'{BOT}/{ROOM}/{index}', 'account': BOT, 'chat': ROOM,
        'sender': NAMES.get(person, person), 'sender_id': person, 'is_self': int(person == BOT),
        'kind': 1, 'local_id': index, 'content': text, 'trigger': trigger,
        'timestamp': time.time() if stamp is None else stamp,
        'payload_json': json.dumps({'image_address': {'mentioned_me': True}} if trigger == 'direct' else {})}


class LocalGateway:
    def __init__(self):
        self.sent = [];self.store = None;self.next = 5000
    async def status(self):
        return {'status': 'logged_in', 'loggedInUser': BOT}
    status_for_send = status
    def warm_send_status(self):
        pass
    async def group_members(self, chat):
        return [{'id': key, 'name': name} for key, name in NAMES.items()]
    async def chats(self):
        return []
    async def send(self, chat, text, **options):
        from whalegirl_linux.gateway import Receipt
        self.sent.append({'type': 'text', 'text': text});self.next += 1
        self.store.observe([event(self.next, BOT, text)])
        return Receipt(True, str(self.next))
    async def close(self):
        pass


def local_seed(runtime, room, case=None, *, size=100):
    now = time.time()
    text = ['昨天路过一家面包店，没进去', '窗台的绿植又长了两片叶子', '我把桌面整理了一下', '天气还不错']
    rows = [event(index, 'eval_a' if index % 2 else 'eval_b', text[index % 4], stamp=now-1000+index*.1)
            for index in range(1, size + 1)]
    training = [('eval_a', '刚下班，累得不想动'), ('eval_b', '那先歇会儿嘛'),
        ('eval_a', '老板说两句，讲了半小时'), ('eval_owner', '他的两句按章节算吧'),
        ('eval_b', '终于把设置弄好了'), ('eval_a', '好耶，收工'),
        ('eval_owner', '我又把饭煮成粥了'), ('eval_a', '省一道汤'),
        ('eval_b', '我买的手柄三个月没拆封'), ('eval_owner', '盒子都比你玩得久'),
        ('eval_a', '晚安啦'), ('eval_b', '睡吧，明天见')]
    rows.extend(event(index, person, value, stamp=now-700+(index-300)*4)
                for index, (person, value) in enumerate(training, 300))
    runtime.store.observe(rows);runtime.fishtalk.rebuild(room)
    runtime.store.observe([event(index, person, value, stamp=now-30+index-700)
        for index, (person, value) in enumerate((case or {}).get('seed', []), 700)])


def remote_command(args, mode):
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', args.ssh_host,
        shlex.join(['systemd-run', '--quiet', '--pipe', '--wait', '--collect',
        '--service-type=exec', '--unit=whalegirl-personality-eval-' + uuid.uuid4().hex[:12],
        '--property=MemoryMax=160M', '--property=MemoryHigh=128M', '--property=MemorySwapMax=0',
        '--property=CPUQuota=20%', '--property=Nice=15', '--property=CPUWeight=10',
        '--property=TasksMax=16', '--property=RuntimeMaxSec=65',
        '/opt/whalegirl-linux/venv/bin/python', args.remote_root + '/personality_eval_model_bridge.py',
        '--code', args.remote_root, '--mode', mode])]


def prepare_bridge(args):
    """Source-only upload. No production state, credentials, or model calls."""
    args.remote_root = '/tmp/whalegirl-personality-eval-20261106-' + uuid.uuid4().hex[:12]
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as package:
        package.write(Path(__file__).with_name('personality_eval_model_bridge.py'), 'personality_eval_model_bridge.py')
        for name in ('__init__.py', 'upstream/__init__.py', 'upstream/ai_engine.py', 'token_usage.py', 'time_context.py'):
            package.write(args.code / 'whalegirl_linux' / name, 'whalegirl_linux/' + name)
    install = "import io,os,pathlib,sys,zipfile;p=pathlib.Path(sys.argv[1]);p.mkdir(mode=0o700);zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).extractall(p);[(x.chmod(0o700 if x.is_dir() else 0o600)) for x in p.rglob('*')];print('source_only_sandbox_ready')"
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', args.ssh_host,
               'python3 -c ' + shlex.quote(install) + ' ' + shlex.quote(args.remote_root)]
    result = subprocess.run(command, input=data.getvalue(), capture_output=True, timeout=30)
    if result.returncode or b'source_only_sandbox_ready' not in result.stdout:
        raise RuntimeError('source_bridge_install_failed')
    result = subprocess.run(remote_command(args, 'metadata'), capture_output=True, timeout=75)
    if result.returncode:
        raise RuntimeError('source_bridge_metadata_failed')
    metadata = json.loads(result.stdout)
    if not metadata.get('ok') or not metadata.get('production_config_unchanged'):
        raise RuntimeError(metadata.get('error_code') or 'source_bridge_metadata_rejected')
    args.production = metadata['settings']
    write(args.out / 'remote-probe.json', {key: value for key, value in metadata.items() if key != 'settings'})
    write(args.out / 'remote-sandbox.json', {'path': args.remote_root, 'source_only': True,
        'memory_max_mib': 160, 'cpu_quota_percent': 20, 'native_sends': 0})


def local_build(args, folder, *, real):
    from whalegirl_linux.runtime import Runtime
    from whalegirl_linux.settings import Config, Settings, reply_temperature
    from whalegirl_linux.store import Store
    from whalegirl_linux.upstream.ai_engine import AIEngine
    values = dict(getattr(args, 'production', {}))
    persona = values.get('system_prompt_override') or ''
    values.update(enabled=True, account_id=BOT, allowed_chat_ids=[ROOM], owner_ids=['eval_owner'],
        context_messages=args.context, min_send_interval=0, chat_cooldown=0, reply_part_interval=.3,
        debounce_single_seconds=0, debounce_burst_seconds=0, debounce_max_seconds=0,
        followup_wait_enabled=False, trigger_only_chat_ids=[], safety_enabled=False)
    if args.temperature is not None:
        values['model_temperature'] = args.temperature
    config = Config(folder)
    config.save(Settings.model_validate({key: value for key, value in values.items() if key in Settings.model_fields}),
                'synthetic-placeholder')
    store = Store(folder / 'state.sqlite3');gateway = LocalGateway();gateway.store = store;calls = []
    def factory(**options):
        engine = AIEngine(**options)
        async def reply(messages, **kw):
            if not real:
                return json.dumps({'should_reply': False, 'actions': []})
            if args.request_stop or args.request_count >= 12:
                args.request_stop = args.request_stop or 'evaluation_request_cap'
                raise RuntimeError('evaluation_request_cap_or_api_paused')
            args.request_count += 1
            payload = json.dumps({'messages': messages, **kw}, ensure_ascii=False).encode('utf-8')
            started = time.monotonic()
            process = await asyncio.create_subprocess_exec(*remote_command(args, 'request'),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(payload), 75)
                if process.returncode:
                    calls.append({'seconds': round(time.monotonic()-started, 4), 'actual_requests': 0,
                        'bridge_exit_code': process.returncode,
                        'error': 'ssh_transport_failed' if process.returncode == 255 else 'isolated_model_process_failed'})
                    raise RuntimeError('isolated_model_process_failed')
                result = json.loads(stdout)
                args.actual_requests += result.get('actual_requests', 0)
                if not result.get('ok'):
                    args.request_stop = result.get('error_code') or 'model_request_failed'
                if not result.get('production_config_unchanged'):
                    args.request_stop = 'production_configuration_changed'
                engine.last_error = '' if result.get('ok') else args.request_stop
                engine.last_response_meta = result.get('response_meta', {})
                engine.last_first_token_ms = result.get('first_token_ms')
                for usage in result.get('usage', []):
                    if engine.usage_callback:
                        engine.usage_callback(usage)
                calls.append({'seconds': round(time.monotonic()-started, 4), 'bridge': result.get('seconds'),
                    'static_hash': hashlib.sha256(kw.get('system_prompt', '').encode()).hexdigest(),
                    'system_chars': len(kw.get('system_prompt', '')),
                    'message_chars': len(json.dumps(messages, ensure_ascii=False)),
                    'actual_requests': result.get('actual_requests', 0), 'resources': result.get('resources'),
                    'usage': result.get('usage', []), 'finish': engine.last_response_meta.get('finish_reason'),
                    'error': engine.last_error, 'config_unchanged': result.get('production_config_unchanged')})
                return result.get('answer', '')
            except Exception as error:
                args.request_stop = 'bridge_' + type(error).__name__
                engine.last_error = args.request_stop
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
                return ''
        engine.chat = reply
        return engine
    runtime = Runtime(config, store, gateway, args.code / 'styles', engine_factory=factory)
    runtime.account_id = BOT;runtime.gateway_state = {'status': 'logged_in', 'loggedInUser': BOT}
    return runtime, gateway, calls, {'model': config.settings.model, 'temperature': reply_temperature(config.settings),
        'reasoning': config.settings.reasoning_effort, 'persona_sha256': hashlib.sha256(persona.encode()).hexdigest(),
        'context_messages': args.context, 'api_style': config.settings.api_style}


def seed_growth(runtime, count):
    """Artificial experiences, never claimed to be real platform observations."""
    from whalegirl_linux.memory_layers import INTERNET
    account = runtime.config.settings.account_id
    role = runtime.memory_hub.role()
    growth = runtime.growth
    now = time.time()
    sources = []
    journals = []
    body = '合成测试语料。甲说会议开了两小时，乙说这小会按连续剧算。甲说蛋糕塌了，乙说省得切了。只是测试。'
    with runtime.store.db:
        for index in range(3):
            source_id = 'synthetic-source-' + str(index)
            journal_id = 'synthetic-journal-' + str(index)
            source = {'id': source_id, 'kind': 'tieba', 'url': 'https://tieba.baidu.com/p/' + str(99000001 + index)}
            runtime.store.db.execute('INSERT INTO growth_sources VALUES(?,?,?,?,?,?,?,?,?)',
                (source_id, account, role, 'tieba', source['url'], '合成测试，不是实际浏览', body,
                 hashlib.sha256((body + str(index)).encode()).hexdigest(), now - (12 - index * 6) * 3600))
            runtime.store.db.execute('INSERT INTO growth_journals VALUES(?,?,?,?,?,?,?,?)',
                (journal_id, 'synthetic-run-' + str(index), account, role, '合成经历', json.dumps([source_id]),
                 json.dumps({'encounter': '仅合成测试，不是实际平台学习'}), now - (12 - index * 6) * 3600))
            sources.append(source)
            journals.append(journal_id)
    for index in range(count):
        expression = index < 3
        topic = '情境短句' if expression else '吉他音乐' if index == 3 else '合成兴趣' + str(index)
        content = ('轻松吐槽时先用短句接住荒诞反差，不解释笑点，不立刻给建议。样本' + str(index)
                   if expression else '我对安静的原声吉他音乐渐渐感兴趣，愿意了解旋律和人的反应。'
                   if index == 3 else '合成测试兴趣' + str(index) + '，用于检查成长规模，不是真实偏好。')
        row = runtime.memory_book.change(account, [INTERNET], {'chat': INTERNET, 'subject': 'self',
            'kind': 'persona', 'content': content, 'tier': 'ordinary', 'venue': 'any',
            'source_key': 'internet:' + sources[0]['id']})
        category = 'expression' if expression else 'interest'
        with runtime.store.db:
            runtime.store.db.execute("UPDATE memories SET source_class='internet_learning' WHERE id=?", (row['id'],))
            runtime.store.db.execute('INSERT INTO growth_insights VALUES(?,?,?,?,?,?,?)',
                (row['id'], journals[0], category, topic, '朋友轻松吐槽或讨论音乐时',
                 '技术求助或严肃难过时不照搬' if expression else '', now))
        growth.policy.register(row['id'], 'tieba', category, {'text': content, 'scene': '轻松吐槽的意外反转',
            'emotion': '好笑又无奈', 'intent': '短句接话，保留余地',
            'examples': ['这小会按连续剧算呀'], 'interest_delta': 1}, evidence_verified=True)
        if expression:
            event = {'scope': 'reply_thread', 'reaction_kind': 'comment', 'context': '父楼合成内容' + str(index),
                     'reaction': '合成真人回复' + str(index)}
            with runtime.store.db:
                runtime.store.db.execute('UPDATE memory_evidence SET receipt=? WHERE memory_id=?',
                    (json.dumps({'reaction_event': event}, ensure_ascii=False), row['id']))
        else:
            # Bulk fixtures keep pressure setup outside the measured path.
            trait_key = growth.policy.trait_key(category, topic)
            with runtime.store.db:
                for source_index, source in enumerate(sources):
                    excerpt = '合成跨时间证据' + str(index) + '-' + str(source_index)
                    runtime.store.db.execute('''INSERT INTO growth_personality_evidence
                        (account,role_id,trait_key,category,topic,memory_id,journal_id,source_id,source_kind,stance,excerpt_hash,created,delta,excerpt)
                        VALUES(?,?,?,?,?,?,?,?,?,'support',?,?,1,?)''',
                        (account, role, trait_key, category, topic, row['id'], journals[source_index], source['id'], 'tieba',
                         hashlib.sha256(excerpt.encode()).hexdigest(), now - (12 - source_index * 6) * 3600, excerpt))
    provenance = [item['_provenance'] for item in growth._style_evidence({'tieba'})]
    if len(provenance) >= 3:
        card = {'summary': '合成真人互动归纳的短句接话规律', 'patterns': [
            {'situation': 'playful', 'rhythm': '一句短话接反差，不解释笑点',
             'conversation_moves': ['顺着当前这句话逗一下，不扩写新事件'],
             'emotion_response': '一起觉得好笑，不做总结', 'avoid_when': ['distress', 'technical']},
            {'situation': 'distress', 'rhythm': '短短接住情绪，再留空间',
             'conversation_moves': ['回应对方具体吐槽，别立刻劝解或指导'],
             'emotion_response': '熟悉而温柔，可以轻轻吐槽处境', 'avoid_when': ['technical']}],
            '_provenance': provenance[:3]}
        with runtime.store.db:
            runtime.store.db.execute('INSERT OR REPLACE INTO growth_style_cards VALUES(?,?,?,?,?,?)',
                (account, role, 'tieba', json.dumps(card, ensure_ascii=False), 3, now))
    return {'synthetic_memories': count, 'expression_rows': 3, 'traits': max(0, count - 3), 'real_readings': 0}


class RequestCap:
    def __init__(self, maximum=12):
        self.maximum = maximum
        self.attempts = []
        self.blocked = ''
        self.label = ''
        self.original = None

    def install(self):
        import httpx
        self.original = httpx.AsyncClient.send
        cap = self
        async def send(client, request, *args, **kwargs):
            if cap.blocked or len(cap.attempts) >= cap.maximum:
                raise RuntimeError('evaluation_request_cap_or_api_paused')
            # Model calls only. This also prevents an accidentally enabled tool
            # from reaching any platform/API outside the model POST endpoint.
            if request.method != 'POST' or not any(part in request.url.path for part in ('chat/completions', 'responses', 'messages')):
                raise RuntimeError('evaluation_network_target_rejected')
            try:
                guard()
            except RuntimeError:
                cap.blocked = 'host_memory_guard'
                raise
            item = {'label': cap.label, 'started': time.time(), 'request_bytes': len(request.content)}
            cap.attempts.append(item)
            started = time.monotonic()
            try:
                response = await cap.original(client, request, *args, **kwargs)
                item['http_status'] = response.status_code
                if response.status_code >= 400:
                    cap.blocked = 'model_http_' + str(response.status_code)
                return response
            except Exception as error:
                item['error_type'] = type(error).__name__
                cap.blocked = 'model_transport_' + type(error).__name__
                raise
            finally:
                item['seconds'] = round(time.monotonic() - started, 4)
        httpx.AsyncClient.send = send

    def restore(self):
        if self.original:
            import httpx
            httpx.AsyncClient.send = self.original


def timeout(signum, frame):
    raise TimeoutError('bounded_benchmark_timeout')


async def pressure(args):
    from evaluate_focus_chat import ROOM, event, seed
    folder = args.out / 'pressure-store'
    folder.mkdir(parents=True, mode=0o700)
    runtime, gateway, calls, metadata = fixture_runtime(args, folder, real=False)
    report = {'kind': 'synthetic_pressure', 'native_sends': 0, 'external_calls': 0, 'phases': [], 'config': metadata}
    try:
        seed(runtime, ROOM, size=100)
        for count in (100, 1000):
            if count == 1000:
                # Separate store prevents fixtures being counted twice.
                await runtime.close()
                folder = args.out / 'pressure-store-1000'
                folder.mkdir(parents=True, mode=0o700)
                runtime, gateway, calls, metadata = fixture_runtime(args, folder, real=False)
                seed(runtime, ROOM, size=100)
            guard()
            setup = time.monotonic()
            seeded = seed_growth(runtime, count)
            phase = {**seeded, 'setup_seconds': round(time.monotonic() - setup, 4), 'before': resources()}
            row = event(9000, 'eval_owner', '今晚想听点音乐，你会挑什么？')
            prompt_times = []
            diagnostics = {}
            for _ in range(3):
                signal.alarm(35)
                started = time.monotonic()
                projection = runtime.growth.prompt(row, [row], diagnostics)
                prompt_times.append(time.monotonic() - started)
                signal.alarm(0)
            signal.alarm(35)
            started = time.monotonic()
            snapshot = runtime.growth.snapshot()
            phase.update(prompt_seconds=prompt_times, prompt_p50_seconds=round(statistics.median(prompt_times), 5),
                         prompt_max_seconds=round(max(prompt_times), 5), prompt_chars=len(projection),
                         diagnostics=diagnostics, snapshot_seconds=round(time.monotonic() - started, 5),
                         snapshot_bytes=len(json.dumps(snapshot, ensure_ascii=False).encode()),
                         snapshot_personality_items=len(snapshot.get('personality_states', [])),
                         snapshot_personality_total=snapshot.get('personality_total'), after=resources())
            signal.alarm(0)
            report['phases'].append(phase)
            write(args.out / 'pressure.json', report)
        report['ok'] = True
    except Exception as error:
        report.update(ok=False, error_type=type(error).__name__, error_code='synthetic_pressure_incomplete')
    finally:
        signal.alarm(0)
        await runtime.close()
        report['resources'] = resources()
        write(args.out / 'pressure.json', report)
    return report


async def local_pressure(args):
    """Windows-safe mock probe, no production configuration or API access."""
    from whalegirl_linux.runtime import Runtime
    from whalegirl_linux.settings import Config, Settings
    from whalegirl_linux.store import Store
    class NoGateway:
        async def close(self):
            pass
        async def status(self):
            return {'status': 'disconnected'}
    report = {'kind': 'windows_synthetic_pressure', 'production_configuration_read': False,
              'production_db_opened': False, 'native_sends': 0, 'external_model_calls': 0, 'phases': []}
    for count in (100, 1000):
        folder = args.out / ('local-store-' + str(count))
        folder.mkdir(parents=True, mode=0o700)
        config = Config(folder)
        config.save(Settings(enabled=False, account_id='eval_bot', allowed_chat_ids=['eval_group@chatroom'],
            growth_mature_traits_chat_enabled=True, growth_tieba_expression_shared=True,
            memory_enabled=True, life_autonomy_enabled=False), 'synthetic-placeholder')
        runtime = Runtime(config, Store(folder / 'state.sqlite3'), NoGateway(), args.code / 'styles')
        try:
            before = resources();started = time.monotonic()
            seeded = seed_growth(runtime, count)
            setup = time.monotonic() - started
            row = {'key': 'eval_bot/eval_group@chatroom/9000', 'account': 'eval_bot', 'chat': 'eval_group@chatroom',
                   'sender_id': 'eval_owner', 'sender': '合成主人', 'kind': 1, 'is_self': 0, 'local_id': 9000,
                   'timestamp': time.time(), 'content': '今晚想听点音乐，你会挑什么？', 'trigger': 'direct'}
            runtime.store.observe([row])
            times = [];diag = {};snapshots = []
            for _ in range(3):
                started = time.monotonic();projection = runtime.growth.prompt(row, [row], diag)
                times.append(time.monotonic() - started)
            for _ in range(2):
                changes = runtime.store.db.total_changes;started = time.monotonic()
                snapshot = runtime.growth.snapshot()
                snapshots.append({'seconds': round(time.monotonic() - started, 5),
                                  'sqlite_changes': runtime.store.db.total_changes - changes})
            phase = {**seeded, 'setup_seconds': round(setup, 4), 'before': before, 'after': resources(),
                'prompt_seconds': times, 'prompt_p50_seconds': round(statistics.median(times), 5),
                'prompt_max_seconds': round(max(times), 5), 'prompt_chars': len(projection), 'diagnostics': diag,
                'snapshots': snapshots, 'snapshot_bytes': len(json.dumps(snapshot, ensure_ascii=False).encode()),
                'snapshot_personality_items': len(snapshot.get('personality_states', [])),
                'snapshot_personality_total': snapshot.get('personality_total')}
            report['phases'].append(phase)
            write(args.out / 'local-pressure.json', report)
        finally:
            await runtime.close()
    report['ok'] = True
    write(args.out / 'local-pressure.json', report)
    return report


async def replay(args):
    if os.name == 'nt':
        seed = local_seed
    else:
        from evaluate_focus_chat import seed
    cap = RequestCap(12)
    cap.install()
    if args.resume:
        blind = json.loads((args.out / 'blind.json').read_text('utf-8'))['snippets']
        key = json.loads((args.out / 'key.json').read_text('utf-8'))
        technical = json.loads((args.out / 'technical.json').read_text('utf-8'))['runs']
        by_case = {case['id']: case for case in CASES}
        old = {item['label']: item for item in blind}
        complete = {item['label'] for item in blind if item['state'] in ('sent', 'ignored')}
        completed_cases = {item['case'] for item in key if item['label'] in complete}
        pending = sorted([item for item in key if item['label'] not in complete],
                         key=lambda item: item['case'] not in completed_cases)
        jobs = [(by_case[item['case']], item['reference']) for item in pending]
        codes = [int(item['label'].replace('片段', '')) for item in pending]
    else:
        jobs = [(case, arm) for case in CASES for arm in (False, True)]
        random.SystemRandom().shuffle(jobs)
        codes = random.SystemRandom().sample(range(100, 999), len(jobs))
        blind = [];key = [];technical = []
    try:
        for (case, arm), code in zip(jobs, codes):
            label = '片段' + str(code)
            cap.label = label
            folder = args.out / ('store-' + str(code) + ('-resume-' + uuid.uuid4().hex[:6] if args.resume else ''))
            folder.mkdir(parents=True, mode=0o700)
            row = {'label': label, 'title': case['title'], 'history': case['seed'], 'input': case['input'],
                   'output': [], 'state': 'not_run', 'error': ''}
            if not args.resume:
                key.append({'label': label, 'case': case['id'], 'reference': arm})
            else:
                row['previous_attempt'] = {name: old[label].get(name) for name in ('state', 'error')}
                blind = [item for item in blind if item['label'] != label]
            if cap.blocked or getattr(args, 'request_stop', ''):
                row.update(state='skipped', error='接口受限或不可用后停止，不重试')
                blind.append(row)
                continue
            runtime = None
            before = len(cap.attempts)
            try:
                guard()
                runtime, gateway, calls, metadata = fixture_runtime(args, folder, real=True)
                seeded = seed_growth(runtime, 12)
                configure(runtime, growth_mature_traits_chat_enabled=arm,
                          growth_bilibili_expression_shared=arm, growth_tieba_expression_shared=arm)
                seed(runtime, ROOM, case, size=100)
                incoming = event(1000, case['sender'], case['input'], trigger=case['trigger'])
                runtime.store.observe([incoming])
                runtime.store.insert(incoming)
                started = time.monotonic()
                async with runtime.chat_turn_lock:
                    await runtime.process_next()
                state = runtime.store.get(incoming['key']) or {}
                metric = runtime.store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?', (incoming['key'],)).fetchone()
                metrics = json.loads(metric[0]) if metric else {}
                row.update(output=gateway.sent, state=state.get('state', 'unknown'),
                           elapsed_seconds=round(time.monotonic() - started, 4))
                if state.get('error'):
                    row['error'] = '回复流程返回错误；类型见技术记录'
                technical.append({'label': label, 'config': metadata, 'seed': seeded,
                    'calls': calls, 'transport_attempts': sum(item.get('actual_requests', 0) for item in calls)
                        if os.name == 'nt' else len(cap.attempts) - before,
                    'growth': metrics.get('growth'), 'usage': runtime.token_ledger.snapshot('eval_bot'),
                    'resources': resources(), 'state': row['state'], 'native_sends': 0})
            except Exception as error:
                row.update(state='failed', error='受控测试未完成：' + type(error).__name__)
                if str(error) == 'host_memory_guard':
                    cap.blocked = 'host_memory_guard'
                technical.append({'label': label, 'error_type': type(error).__name__,
                                  'transport_attempts': len(cap.attempts) - before})
            finally:
                if runtime is not None:
                    await runtime.close()
            blind.append(row)
            write(args.out / 'blind.json', {'synthetic_experiences': True, 'real_model': True, 'native_sends': 0, 'snippets': blind})
            write(args.out / 'technical.json', {'attempts': cap.attempts, 'runs': technical,
                'api_stop': getattr(args, 'request_stop', '') or cap.blocked,
                'actual_requests': getattr(args, 'actual_requests', len(cap.attempts))})
        write(args.out / 'key.json', key)
        write(args.out / 'blind.json', {'synthetic_experiences': True, 'real_model': True, 'native_sends': 0, 'snippets': blind})
    finally:
        cap.restore()
    return {'snippets': len(blind), 'actual_requests': getattr(args, 'actual_requests', len(cap.attempts)),
            'api_stop': getattr(args, 'request_stop', '') or cap.blocked}


def reveal(args):
    scores_path = args.out / 'blind_scores.json'
    scores = json.loads(scores_path.read_text('utf-8'))
    if not isinstance(scores.get('scores'), list) or not scores['scores']:
        raise RuntimeError('blind_scores_required_before_reveal')
    digest = hashlib.sha256(scores_path.read_bytes()).hexdigest()
    keys = {item['label']: item for item in json.loads((args.out / 'key.json').read_text('utf-8'))}
    results = [{**item, **keys[item['label']]} for item in scores['scores']]
    write(args.out / 'revealed.json', {'scores_sha256_before_reveal': digest, 'revealed_at': time.time(),
        'evaluator': scores.get('evaluator'), 'kind': 'synthetic_experiences_real_model_agent_blind_review', 'results': results})


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mode', choices=('pressure', 'local-pressure', 'replay', 'reveal'), required=True)
    parser.add_argument('--context', type=int, default=100)
    parser.add_argument('--temperature', type=float)
    parser.add_argument('--natural-mode', action='store_true')
    parser.add_argument('--ssh-host', default='root@120.46.186.50')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    root = args.code.resolve().parent
    local = args.mode == 'local-pressure' or os.name == 'nt'
    if not local and (root.parent != Path('/tmp') or not root.name.startswith('whalegirl-personality-eval-20261106-')):
        raise RuntimeError('private_linux_sandbox_required')
    args.out.mkdir(parents=True, exist_ok=True, mode=0o700)
    sys.path.insert(0, str(args.code))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()
    if not local:
        signal.signal(signal.SIGALRM, timeout)
    # Prevent Config.sync_authority from using any inherited gateway directory.
    os.environ.pop('WHALEGIRL_UI_JOBS_DIR', None)
    before = {} if local else protected()
    started = time.monotonic()
    try:
        if args.mode == 'local-pressure':
            result = await local_pressure(args)
        elif args.mode == 'reveal':
            reveal(args)
            result = {'revealed': True}
        elif args.mode == 'pressure':
            result = await pressure(args)
        else:
            if os.name == 'nt':
                previous = json.loads((args.out / 'technical.json').read_text('utf-8')) if args.resume else {}
                args.actual_requests = previous.get('actual_requests', 0)
                args.request_count = args.actual_requests;args.request_stop = ''
                prepare_bridge(args)
            result = await replay(args)
    except Exception as error:
        result = {'error_type': type(error).__name__, 'error_code': 'isolated_evaluation_incomplete'}
    result.update(mode=args.mode, seconds=round(time.monotonic() - started, 4), resources=resources(),
                  production_config_unchanged=True if local else protected() == before, production_db_opened=False,
                  native_sends=0, memory_limit_mib=160 if args.mode == 'replay' else None,
                  experiences='synthetic_only')
    write(args.out / (args.mode + '-summary.json'), result)
    write(args.out / (args.mode + '-invocation-' + str(time.time_ns()) + '.json'), result)
    print(json.dumps({key: result.get(key) for key in ('mode', 'seconds', 'snippets', 'actual_requests',
                     'api_stop', 'error_type', 'production_config_unchanged')}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
