"""Durable speech/music jobs, independent configuration and conservative delivery."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field

from .audio_gateway import AudioGateway
from .audio_media import MAX_OUTPUT_BYTES, probe, tune
from .audio_provider import AudioError, AudioProviderClient, GenerationUnknown
from .audio_settings import AudioConfig, SpeechDelivery
from .resources import native_pressure


class SpeechArguments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=1200)
    voice_preset_id: str = Field(default='', max_length=40)
    language: Literal['auto', 'zh', 'yue', 'en', 'ja', 'ko'] | None = None
    delivery: SpeechDelivery | None = None


class MusicArguments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=1600)
    lyrics: str = Field(default='', max_length=5000)
    instrumental: bool = False
    duration: int | None = Field(default=None, ge=5, le=180)
    delivery: str = Field(default='file', pattern='^(file|voice)$')


class AudioService:
    PREVIEW_RETENTION_SECONDS = 24 * 60 * 60
    CLEANUP_INTERVAL_SECONDS = 5 * 60

    def __init__(self, runtime, *, provider=None):
        self.runtime, self.store, self.config = runtime, runtime.store, runtime.config
        self.settings = AudioConfig(self.config, self.store)
        self.provider = provider or AudioProviderClient()
        self.gateway = AudioGateway(runtime.gateway)
        self.directory = self.config.directory / 'audio-media'
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.generation_lock, self.delivery_lock = asyncio.Lock(), asyncio.Lock()
        self.media_lock = asyncio.Lock()
        self.pc_check_at = 0.0
        self.cleanup_check_at = 0.0
        with self.store.lock, self.store.db:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS audio_jobs(
                    id TEXT PRIMARY KEY,account TEXT NOT NULL,chat TEXT NOT NULL,
                    source_key TEXT NOT NULL,kind TEXT NOT NULL,destination TEXT NOT NULL,
                    state TEXT NOT NULL,args_json TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,
                    submitted REAL NOT NULL DEFAULT 0,config_json TEXT NOT NULL DEFAULT '{}',
                    result_enc TEXT NOT NULL DEFAULT '',source_file TEXT NOT NULL DEFAULT '',
                    output_file TEXT NOT NULL DEFAULT '',format TEXT NOT NULL DEFAULT '',duration REAL NOT NULL DEFAULT 0,
                    checksum TEXT NOT NULL DEFAULT '',native_job_id TEXT NOT NULL DEFAULT '',
                    receipt TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',timing_json TEXT NOT NULL DEFAULT '{}',
                    render_of TEXT NOT NULL DEFAULT '',retry_at REAL NOT NULL DEFAULT 0,delivery_override TEXT NOT NULL DEFAULT '',
                    delivery_attempt INTEGER NOT NULL DEFAULT 0,media_cleanup_pending INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(account,source_key));
                CREATE INDEX IF NOT EXISTS audio_job_queue ON audio_jobs(state,account,created);
                CREATE TABLE IF NOT EXISTS audio_job_sources(
                    job_id TEXT NOT NULL,inbox_key TEXT NOT NULL,account TEXT NOT NULL,chat TEXT NOT NULL,
                    PRIMARY KEY(job_id,inbox_key));
                CREATE TABLE IF NOT EXISTS audio_usage(
                    job_id TEXT PRIMARY KEY,account TEXT NOT NULL,kind TEXT NOT NULL,model TEXT NOT NULL,
                    created REAL NOT NULL,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,
                    duration REAL,charge_state TEXT NOT NULL DEFAULT 'unconfirmed');
            ''')
            if 'delivery_override' not in {row[1] for row in self.store.db.execute('PRAGMA table_info(audio_jobs)')}:
                self.store.db.execute("ALTER TABLE audio_jobs ADD COLUMN delivery_override TEXT NOT NULL DEFAULT ''")
            if 'delivery_attempt' not in {row[1] for row in self.store.db.execute('PRAGMA table_info(audio_jobs)')}:
                self.store.db.execute('ALTER TABLE audio_jobs ADD COLUMN delivery_attempt INTEGER NOT NULL DEFAULT 0')
            if 'media_cleanup_pending' not in {row[1] for row in self.store.db.execute('PRAGMA table_info(audio_jobs)')}:
                self.store.db.execute('ALTER TABLE audio_jobs ADD COLUMN media_cleanup_pending INTEGER NOT NULL DEFAULT 0')
            self.store.db.execute('CREATE INDEX IF NOT EXISTS audio_job_media_cleanup '
                'ON audio_jobs(destination,state,created,media_cleanup_pending)')
            self.store.db.execute("UPDATE audio_jobs SET state='outcome_unknown',error='生成阶段重启，是否已扣费待核对，未重新提交' WHERE state='generating'")
            self.store.db.execute("UPDATE audio_jobs SET state='needs_review',error='发送阶段重启，需核对，不自动重发' WHERE state='sending'")
            self.store.db.execute("UPDATE audio_jobs SET state='download_retry' WHERE state='downloading' AND result_enc!=''")
            self.store.db.execute("UPDATE audio_jobs SET state='failed',error='内嵌结果处理阶段中断，未重新生成' WHERE state='downloading' AND result_enc=''")
            self.store.db.execute("INSERT OR IGNORE INTO audio_job_sources SELECT id,substr(source_key,1,length(source_key)-6),account,chat "
                                  "FROM audio_jobs WHERE destination='chat' AND source_key LIKE '%:audio'")
        with self.store.lock:
            recovered = [row[0] for row in self.store.db.execute("SELECT id FROM audio_jobs WHERE destination='chat'")]
        for identity in recovered:
            self.sync_sources(identity)
        from .audio_tools import AudioTools
        self.tools = AudioTools(self)

    async def close(self):
        await self.provider.close()

    async def pc_status(self):
        capture = self.settings.capture('speech')
        status = await self.provider.pc_status(capture)
        active = status.get('active_voice')
        if not status.get('connected') or active not in {'cyrene', 'yachiyo', 'xiaofei'}:
            return status
        revision, settings = self.settings.current()
        if settings.providers[settings.speech_profile].backend != 'pc_bridge':
            return status
        matching = [key for key, preset in settings.presets.items() if preset.voice == active]
        values = settings.model_dump()
        if not matching:
            if active != 'xiaofei' or status.get('backend') != 'gsv-lite' or len(settings.presets) >= 20:
                status['preset_sync_pending'] = True
                return status
            source = settings.presets[settings.default_preset].model_dump()
            values['presets'][active] = {**source, 'name': '小菲', 'voice': active}
            matching = [active]
        current = settings.presets[settings.default_preset]
        if active == 'xiaofei':
            model_name = 'xiaofei-gsv-lite-v2'
        else:
            model_name = active + ('-aniflive-v2proplus' if status.get('backend') == 'aniflive' else '-v2proplus')
        if current.voice != active or settings.providers[settings.speech_profile].model != model_name:
            if current.voice != active:
                values['default_preset'] = active if active in matching else matching[0]
            values['providers'][settings.speech_profile]['model'] = model_name
            try:
                self.settings.save(revision, values)
                status['preset_synchronized'] = True
            except ValueError:
                status['preset_sync_pending'] = True
        return status

    def get(self, identity):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM audio_jobs WHERE id=? AND account=?',
                (identity, self.config.settings.account_id)).fetchone()
        return dict(row) if row else None

    def change(self, identity, **values):
        allowed = {'state', 'submitted', 'config_json', 'result_enc', 'source_file', 'output_file', 'format',
                   'duration', 'checksum', 'native_job_id', 'receipt', 'error', 'timing_json', 'retry_at',
                   'delivery_override', 'delivery_attempt', 'media_cleanup_pending'}
        if not values or set(values) - allowed:
            raise ValueError('音频任务更新字段无效')
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE audio_jobs SET ' + ','.join(key + '=?' for key in values) + ',updated=? WHERE id=?',
                                 (*values.values(), time.time(), identity))
        if 'state' in values:
            self.sync_sources(identity)

    def bind_sources(self, identity, batch):
        job = self.get(identity)
        if not job:
            raise ValueError('音频任务不存在')
        with self.store.lock, self.store.db:
            for item in batch:
                if item['account'] != job['account'] or item['chat'] != job['chat']:
                    raise ValueError('音频批次不可跨账号或会话')
                self.store.db.execute('INSERT OR IGNORE INTO audio_job_sources VALUES(?,?,?,?)',
                    (identity, item['key'], job['account'], job['chat']))
        self.sync_sources(identity)

    def sync_sources(self, identity):
        """Only the anchor counts as a send; merged messages share its final result."""
        with self.store.lock, self.store.db:
            row = self.store.db.execute('SELECT * FROM audio_jobs WHERE id=?', (identity,)).fetchone()
            if not row or row['destination'] != 'chat':
                return
            job = dict(row)
            state = {'sent': 'sent', 'needs_review': 'uncertain', 'outcome_unknown': 'uncertain',
                     'failed_before_send': 'failed', 'download_failed': 'failed', 'failed': 'failed',
                     'cancelled': 'cancelled', 'held': 'audio_held', 'sending': 'sending'}.get(job['state'], 'audio_queued')
            anchor = job['source_key'].removesuffix(':audio')
            self.store.db.execute('UPDATE inbox SET state=CASE WHEN key=? THEN ? ELSE \'merged\' END,'
                'receipt=?,error=?,updated=? WHERE account=? AND chat=? AND key IN '
                '(SELECT inbox_key FROM audio_job_sources WHERE job_id=?)',
                (anchor, state, job['receipt'], ('音频任务 ' + identity + ' · ' + job['state'] + ' · ' + job['error'])[:300],
                 time.time(), job['account'], job['chat'], identity))

    def permitted(self, kind, chat):
        _, audio = self.settings.current()
        core = self.config.settings
        if (not core.enabled or not audio.enabled or not getattr(audio, 'voice_enabled' if kind == 'speech' else 'music_enabled')
                or chat not in core.allowed_chat_ids or audio.allowed_chat_ids and chat not in audio.allowed_chat_ids):
            return False
        return True

    def enqueue(self, kind, arguments, *, first=None, preview=False, render_of='', request_id=''):
        if kind not in ('speech', 'music'):
            raise ValueError('音频工具类型无效')
        args = (SpeechArguments if kind == 'speech' else MusicArguments).model_validate(arguments)
        account = self.config.settings.account_id
        chat = '' if preview else str((first or {}).get('chat') or '')
        if not preview and ((first or {}).get('account') != account or not self.permitted(kind, chat)):
            raise ValueError('该会话尚未获准使用此音频工具')
        revision, audio = self.settings.current()
        if isinstance(args, SpeechArguments) and args.delivery is not None and not audio.ai_voice_dynamics_enabled:
            raise ValueError('管理员未开启 AI 单句声音调节')
        if isinstance(args, SpeechArguments) and args.voice_preset_id:
            if args.voice_preset_id not in audio.presets:
                raise ValueError('指定声线不存在')
            if not preview and args.voice_preset_id not in audio.ai_preset_ids:
                raise ValueError('管理员未开放该声线供 AI 选择')
        if not render_of:
            captured = self.settings.capture(kind, getattr(args, 'voice_preset_id', ''))
            if captured['provider'].backend == 'pc_bridge' and len(args.text) > 600:
                raise ValueError('电脑语音每次最多600字')
            if (isinstance(args, SpeechArguments) and args.language is not None
                    and captured['provider'].backend != 'pc_bridge'):
                raise ValueError('当前语音接口不支持显式语种参数，请保留自动语言处理')
        else:
            source = self.get(render_of)
            if not preview or kind != 'speech' or not source or source['kind'] != 'speech' or not source['source_file']:
                raise ValueError('调参试听需要本账号已有的语音素材')
            selected = audio.presets[args.voice_preset_id or audio.default_preset]
            if selected.voice != json.loads(source['config_json']).get('preset', {}).get('voice'):
                raise ValueError('复用素材只能调整音高、语速和音量；更换模型音色需要重新生成试听')
        source_key = ('preview:' + (request_id or uuid.uuid4().hex)) if preview else str(first['key']) + ':audio'
        args_json = args.model_dump_json(exclude_none=isinstance(args, SpeechArguments))
        identity, now = uuid.uuid4().hex, time.time()
        with self.store.lock, self.store.db:
            existing = self.store.db.execute('SELECT id,kind,args_json,render_of FROM audio_jobs WHERE account=? AND source_key=?', (account, source_key)).fetchone()
            if existing:
                if preview and (existing['kind'], existing['args_json'], existing['render_of']) != (kind, args_json, render_of):
                    raise ValueError('同一试听请求 ID 的参数发生变化，请发起新的试听')
                return self.get(existing[0])
            self.store.db.execute('INSERT INTO audio_jobs(id,account,chat,source_key,kind,destination,state,args_json,created,updated,render_of) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (identity, account, chat, source_key, kind,
                'preview' if preview else 'chat', 'queued', args_json, now, now, render_of))
        if not preview:
            self.bind_sources(identity, [first])
        return self.get(identity)

    def safe_path(self, relative):
        path = self.directory / relative
        if (not relative or path.is_symlink() or self.directory.is_symlink()
                or not path.resolve().is_relative_to(self.directory.resolve())
                or any(part.is_symlink() for part in path.parents if part != self.directory.parent)):
            raise AudioError('音频文件路径无效')
        return path

    def cached_bytes(self):
        return sum(path.stat().st_size for path in self.directory.glob('*/*') if path.is_file() and not path.is_symlink())

    def cleanup_job_media(self, identity, *, preview=False, eligible_preview_ids=()):
        """Remove only this task's generated files after a confirmed send or preview expiry."""
        eligible_preview_ids = set(eligible_preview_ids)
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM audio_jobs WHERE id=? AND account=?',
                (identity, self.config.settings.account_id)).fetchone()
            if not row:
                return False
            job = dict(row)
            if preview:
                if (job['destination'] != 'preview' or job['state'] != 'ready'
                        or job['created'] > time.time() - self.PREVIEW_RETENTION_SECONDS):
                    return False
            elif job['destination'] != 'chat' or job['state'] != 'sent' or not job['media_cleanup_pending']:
                return False

            # A queued render may still need the source audio. Wait until that
            # preview is finished or expired before releasing its parent file.
            dependents = self.store.db.execute(
                "SELECT id FROM audio_jobs WHERE render_of=? AND state IN ('queued','generating','downloading','download_retry','ready')",
                (identity,)).fetchall()
            if any(item['id'] not in eligible_preview_ids for item in dependents):
                return False

            if len(identity) != 32 or any(char not in '0123456789abcdef' for char in identity):
                return False
            work = self.directory / identity
            if work.parent != self.directory or work.is_symlink() or not work.resolve().is_relative_to(self.directory.resolve()):
                return False
            paths = {}
            for field in ('source_file', 'output_file'):
                relative = job[field]
                if not relative:
                    continue
                try:
                    path = self.safe_path(relative)
                except (AudioError, OSError, ValueError):
                    continue
                if path.parent != work or path.name not in {'source.audio', 'tuned.wav'}:
                    continue
                paths[relative] = path

            protected_paths = set()
            for relative in paths:
                refs = self.store.db.execute(
                    'SELECT id FROM audio_jobs WHERE id!=? AND (source_file=? OR output_file=?)',
                    (identity, relative, relative)).fetchall()
                if any(item['id'] not in eligible_preview_ids for item in refs):
                    protected_paths.add(relative)

            removed = set()
            for relative, path in paths.items():
                if relative in protected_paths:
                    continue
                try:
                    if path.is_file() and not path.is_symlink():
                        path.unlink()
                    removed.add(relative)
                except OSError:
                    continue

            updates = {}
            for field in ('source_file', 'output_file'):
                if job[field] and job[field] in removed:
                    updates[field] = ''
            remaining = any(job[field] and job[field] not in removed for field in ('source_file', 'output_file'))
            if preview and not remaining:
                updates.update(state='expired', error='试听文件已按保留期限清理；任务与费用记录仍保留')
            if not preview:
                updates['media_cleanup_pending'] = int(remaining)
                updates['result_enc'] = ''
            if updates:
                with self.store.db:
                    self.store.db.execute('UPDATE audio_jobs SET ' + ','.join(key + '=?' for key in updates)
                        + ',updated=? WHERE id=?', (*updates.values(), time.time(), identity))
            if not remaining:
                try:
                    work.rmdir()
                except OSError:
                    pass
            return bool(removed)

    async def cleanup_expired_previews(self):
        async with self.generation_lock:
            async with self.media_lock:
                cutoff = time.time() - self.PREVIEW_RETENTION_SECONDS
                with self.store.lock:
                    rows = self.store.db.execute("SELECT id FROM audio_jobs WHERE account=? AND destination='preview' "
                        'AND state=\'ready\' AND created<=?', (self.config.settings.account_id, cutoff)).fetchall()
                    pending = self.store.db.execute("SELECT id FROM audio_jobs WHERE destination='chat' AND state='sent' "
                        'AND media_cleanup_pending=1 AND account=?', (self.config.settings.account_id,)).fetchall()
                eligible = {row['id'] for row in rows}
                removed = 0
                for row in pending:
                    removed += self.cleanup_confirmed_media(row['id'])
                for identity in eligible:
                    removed += self.cleanup_job_media(identity, preview=True, eligible_preview_ids=eligible)
                return removed

    def cleanup_confirmed_media(self, identity):
        try:
            return self.cleanup_job_media(identity)
        except Exception:
            # A cleanup failure must never change the already committed send receipt.
            return False

    def resource_reason(self, audio):
        free = shutil.disk_usage(self.directory).free
        reservation = audio.max_download_mb * 1024**2 + MAX_OUTPUT_BYTES
        if free < audio.disk_reserve_mb * 1024**2 + reservation:
            return '磁盘剩余空间不足，暂停新音频，不影响文字聊天'
        if self.cached_bytes() + reservation > audio.cache_mb * 1024**2:
            return '音频缓存接近上限，请在核对文件后清理或调整容量'
        if native_pressure(self.config):
            return '现有服务器资源保护正在生效，暂缓音频任务'
        meminfo = Path('/proc/meminfo')
        if meminfo.is_file():
            values = dict(line.split(':', 1) for line in meminfo.read_text().splitlines())
            if int(values.get('MemAvailable', '0 kB').split()[0]) < 128 * 1024:
                return '可用内存不足，暂缓音频处理'
        return ''

    def _usage(self, identity, raw=None, duration=None):
        raw = raw or {}
        values = []
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            value = raw.get(key)
            values.append(value if type(value) is int and value >= 0 else None)
        with self.store.lock, self.store.db:
            self.store.db.execute('UPDATE audio_usage SET input_tokens=COALESCE(?,input_tokens),'
                'output_tokens=COALESCE(?,output_tokens),total_tokens=COALESCE(?,total_tokens),'
                'duration=COALESCE(?,duration) WHERE job_id=?', (*values, duration, identity))

    async def generate_once(self):
        async with self.generation_lock:
            with self.store.lock:
                row = self.store.db.execute("SELECT * FROM audio_jobs WHERE account=? AND state IN ('queued','download_retry') "
                    'AND retry_at<=? ORDER BY created LIMIT 1', (self.config.settings.account_id, time.time())).fetchone()
            if not row:
                return False
            job = dict(row)
            _, audio = self.settings.current()
            reason = self.resource_reason(audio)
            if reason:
                self.change(job['id'], error=reason, retry_at=time.time() + 30)
                return False
            if job['destination'] == 'chat' and not self.permitted(job['kind'], job['chat']):
                self.change(job['id'], state='cancelled', error='音频功能或会话授权已关闭')
                return True
            try:
                await self._generate(job)
            except asyncio.CancelledError:
                current = self.get(job['id'])
                if current and current['state'] == 'generating':
                    self.change(job['id'], state='outcome_unknown', error='生成过程停止，可能已扣费，不自动重试')
                raise
            except GenerationUnknown as exc:
                if self.get(job['id'])['state'] != 'cancelled':
                    self.change(job['id'], state='outcome_unknown', error=str(exc))
            except Exception as exc:
                current = self.get(job['id'])
                if current and current['state'] != 'cancelled':
                    state = 'download_failed' if current['result_enc'] and not current['source_file'] else 'failed'
                    self.change(job['id'], state=state, error=str(exc) if isinstance(exc, AudioError) else '音频处理异常：' + type(exc).__name__)
            return True

    async def _generate(self, job):
        identity, args = job['id'], json.loads(job['args_json'])
        work = self.safe_path(identity)
        work.mkdir(mode=0o700, exist_ok=True)
        source = work / 'source.audio'
        started = time.monotonic()
        requested_delivery = SpeechDelivery.model_validate(args['delivery']) if args.get('delivery') is not None else None
        if job['render_of']:
            original = self.get(job['render_of'])
            if not original:
                raise AudioError('试听原素材不在当前账号')
            source = self.safe_path(original['source_file'])
            revision, audio = self.settings.current()
            preset_id = args.get('voice_preset_id') or audio.default_preset
            preset = audio.presets[preset_id]
            if requested_delivery:
                if not audio.ai_voice_dynamics_enabled:
                    raise AudioError('AI 单句声音调节已关闭，未处理旧参数任务')
                preset = requested_delivery.apply(preset)
            if preset.voice != json.loads(original['config_json']).get('preset', {}).get('voice'):
                raise AudioError('声线在排队期间更换了模型音色，请重新生成试听')
            capture_json = {'revision': revision, 'preset_id': preset_id, 'preset': preset.model_dump(),
                            'render_only': True, 'voice_max_seconds': audio.voice_max_seconds, 'voice_overflow': audio.voice_overflow}
            capture_json['native_synthesis'] = json.loads(original['config_json']).get('native_synthesis', {})
            if requested_delivery:
                capture_json['delivery'] = requested_delivery.model_dump()
            self.change(identity, state='downloading', config_json=json.dumps(capture_json))
        elif job['state'] == 'download_retry':
            result = json.loads(self.config.fernet.decrypt(job['result_enc'].encode()))
            capture_json = json.loads(job['config_json'])
            from .audio_settings import VoicePreset
            preset = VoicePreset.model_validate(capture_json['preset'])
            _, audio = self.settings.current()
            self.change(identity, state='downloading', error='')
            await self.provider.download(result, source, audio.max_download_mb * 1024**2)
        else:
            capture = self.settings.capture(job['kind'], args.get('voice_preset_id', ''))
            capture['request_id'] = identity
            audio, preset = capture['settings'], capture['preset']
            if requested_delivery:
                if not audio.ai_voice_dynamics_enabled:
                    raise AudioError('AI 单句声音调节已关闭，未提交合成')
                preset = requested_delivery.apply(preset)
                capture['preset'] = preset
                if capture['provider'].backend == 'pc_bridge':
                    capture['native_synthesis'] = {'speed_factor': preset.speed,
                        'fragment_interval': requested_delivery.pause_seconds}
            capture_json = {'revision': capture['revision'], 'profile_id': capture['profile_id'],
                'provider': capture['provider'].model_dump(), 'preset_id': capture['preset_id'],
                'preset': preset.model_dump(), 'singing_style': audio.singing_style,
                'voice_max_seconds': audio.voice_max_seconds, 'voice_overflow': audio.voice_overflow}
            if requested_delivery:
                capture_json.update(delivery=requested_delivery.model_dump(),
                    native_synthesis=capture.get('native_synthesis', {}))
            with self.store.lock, self.store.db:
                changed = self.store.db.execute("UPDATE audio_jobs SET state='generating',submitted=?,config_json=?,updated=?,error='' "
                    "WHERE id=? AND state='queued'", (time.time(), json.dumps(capture_json), time.time(), identity))
                if changed.rowcount != 1:
                    return
                self.store.db.execute('INSERT INTO audio_usage(job_id,account,kind,model,created,charge_state) VALUES(?,?,?,?,?,?)',
                    (identity, job['account'], job['kind'], capture['provider'].model, time.time(),
                     'local_no_api_charge' if capture['provider'].backend == 'pc_bridge' else 'unconfirmed'))
            result = await self.provider.generate(capture, job['kind'], args)
            if result.get('language'):
                capture_json['synthesis_language'] = result['language']
                self.change(identity, config_json=json.dumps(capture_json))
            self._usage(identity, result.get('usage'))
            if result.get('charge_state') == 'local_no_api_charge':
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE audio_usage SET charge_state='local_no_api_charge' WHERE job_id=?", (identity,))
            encrypted = self.config.fernet.encrypt(json.dumps({'url': result['url']}).encode()).decode() if 'url' in result else ''
            if self.get(identity)['state'] == 'cancelled':
                self.change(identity, result_enc=encrypted)
                return
            self.change(identity, state='downloading', result_enc=encrypted,
                        timing_json=json.dumps({'generation_seconds': result['generation_seconds']}))
            await self.provider.download(result, source, audio.max_download_mb * 1024**2)
        async with self.media_lock:
            metadata = await probe(source, max_seconds=600)
            # Billable length is the original generated audio, not post-tuning duration.
            if not job['render_of']:
                self._usage(identity, duration=metadata['duration'])
            self.change(identity, source_file=str(source.relative_to(self.directory)))
            output = source
            native_speed = capture_json.get('native_synthesis', {}).get('speed_factor', 1)
            # The local engine already applied this rate. Render-only auditions
            # reuse its raw file and compensate rather than speeding it twice.
            if native_speed != 1:
                preset = preset.model_copy(update={'speed': preset.speed / native_speed})
            if job['kind'] == 'speech' and (preset.pitch or preset.speed != 1 or preset.gain_db):
                output = work / 'tuned.wav'
                metadata = await tune(source, output, preset)
            def checksum_file():
                with output.open('rb') as content:
                    return hashlib.file_digest(content, 'sha256').hexdigest()
            checksum = await asyncio.to_thread(checksum_file)
        current = self.get(identity)
        if not current or current['state'] == 'cancelled':
            return
        timing = json.loads(current['timing_json'])
        timing['generation_pipeline_seconds'] = round(time.monotonic() - started, 3)
        self.change(identity, state='ready', output_file=str(output.relative_to(self.directory)),
            format=metadata['format'], duration=metadata['duration'], checksum=checksum, error='', timing_json=json.dumps(timing))

    def _allowed(self, job):
        core = self.config.settings
        first = self.store.get(job['source_key'].removesuffix(':audio'))
        if not first or first['account'] != job['account'] or first['chat'] != job['chat']:
            return False
        return (core.account_id == job['account'] and self.permitted(job['kind'], job['chat'])
                and not self.runtime.paused_reason and not self.runtime.safety.held(job['account'])
                and self.runtime.chat_control.allowed(first)
                and (first.get('trigger') != 'proactive' or self.runtime.proactive.send_allowed(first))
                and self.get(job['id'])['state'] in ('ready', 'sending'))

    async def deliver_once(self):
        async with self.delivery_lock:
            with self.store.lock:
                row = self.store.db.execute("SELECT * FROM audio_jobs WHERE account=? AND destination='chat' AND state='ready' "
                    'AND retry_at<=? ORDER BY created LIMIT 1', (self.config.settings.account_id, time.time())).fetchone()
            if not row:
                return False
            job = dict(row)
            if not self._allowed(job):
                self.change(job['id'], error='发送权限、会话静默或运行暂停暂不允许，成品保留', retry_at=time.time() + 10)
                return False
            if self.runtime.processing:
                return False
            core = self.config.settings
            due = max(self.runtime.last_send + core.min_send_interval,
                      self.store.latest_sent(job['account'], job['chat']) + core.chat_cooldown)
            if due > time.time():
                self.change(job['id'], retry_at=due)
                return False
            args, snapshot = json.loads(job['args_json']), json.loads(job['config_json'])
            native = job['delivery_override'] != 'file' and (job['kind'] == 'speech' or args.get('delivery') == 'voice')
            if native and job['duration'] > snapshot['voice_max_seconds']:
                if snapshot['voice_overflow'] == 'hold':
                    self.change(job['id'], state='held', error='实际语音超过原生时长设置，请在后台试听并选择文件发送')
                    return True
                native = False
            auth = await self.runtime.gateway.status()
            if auth.get('status') != 'logged_in' or auth.get('loggedInUser') != job['account']:
                self.change(job['id'], error='微信掉线或账号变化，未发送', retry_at=time.time() + 30)
                return False
            display = args.get('text') or ('[原创音乐] ' + args.get('prompt', '')[:160])
            guard = self.runtime.safety.check(job['account'], job['chat'], core, reply=display, kind='audio')
            if not guard['allowed']:
                self.change(job['id'], error=guard['reason'], retry_at=max(time.time() + 10, guard.get('retry_at', 0)))
                return False
            path = self.safe_path(job['output_file'])
            with self.store.lock, self.store.db:
                changed = self.store.db.execute("UPDATE audio_jobs SET state='sending',delivery_attempt=delivery_attempt+1,updated=? WHERE id=? AND state='ready'",
                                                (time.time(), job['id']))
                if changed.rowcount != 1:
                    return False
                attempt = self.store.db.execute('SELECT delivery_attempt FROM audio_jobs WHERE id=?', (job['id'],)).fetchone()[0]
                action_id = f'audio:{job["id"]}#attempt-{attempt}'
                self.store.db.execute('INSERT INTO outbound_actions(id,inbox_key,account,chat,kind,reply,state,sent_at) '
                    "VALUES(?,?,?,?,?,?,'sending',?)", (action_id, job['source_key'].removesuffix(':audio'), job['account'], job['chat'],
                    'audio', display, time.time()))
            self.runtime.last_send = time.time()
            started = time.monotonic()
            try:
                allowed = lambda: self._allowed(job)
                if native:
                    receipt = await self.gateway.voice(job['chat'], path, f'audio:{job["id"]}:attempt-{attempt}', allowed,
                        lambda value: self.change(job['id'], native_job_id=value))
                else:
                    receipt = await self.gateway.file(job['chat'], path, 'whalegirl-' + job['id'] + '.' + job['format'], allowed)
            except BaseException:
                # A started native operation may outlive an HTTP disconnect.
                self.change(job['id'], state='needs_review', error='发送阶段中断，需核对，不自动重发')
                self.store.action_finish(action_id, 'uncertain', error='音频发送阶段中断')
                raise
            finally:
                current = self.get(job['id'])
                timing = json.loads(current['timing_json'])
                timing['send_seconds'] = round(time.monotonic() - started, 3)
                self.change(job['id'], timing_json=json.dumps(timing))
            # Commit the receipt before optional history/safety bookkeeping. A
            # later failure must never downgrade an already acknowledged send.
            state = 'sent' if receipt.confirmed else 'failed_before_send' if receipt.definite_failure else 'needs_review'
            self.change(job['id'], state=state, receipt=receipt.message_id, error=receipt.error,
                media_cleanup_pending=int(receipt.confirmed))
            self.store.action_finish(action_id, 'sent' if receipt.confirmed else 'failed' if receipt.definite_failure else 'uncertain',
                receipt.message_id, receipt.error, details={'audio_job_id': job['id'], **receipt.details})
            try:
                self.runtime.safety.outcome(job['account'], receipt, core, chat=job['chat'])
                source_key = job['source_key'].removesuffix(':audio')
                event = self.store.get(source_key)
                if event:
                    self.store.update(source_key, 'sent' if receipt.confirmed else 'failed' if receipt.definite_failure else 'uncertain',
                                      reply=display, receipt=receipt.message_id, error=receipt.error)
                    if receipt.confirmed:
                        self.store.remember(event, display)
            except Exception as exc:
                current = self.get(job['id'])
                timing = json.loads(current['timing_json'])
                timing['bookkeeping_error'] = type(exc).__name__
                self.change(job['id'], timing_json=json.dumps(timing))
            if receipt.confirmed:
                self.cleanup_confirmed_media(job['id'])
            return True

    async def reconcile(self, identity):
        async with self.delivery_lock:
            job = self.get(identity)
            if not job or job['state'] != 'needs_review' or not job['native_job_id']:
                raise ValueError('只有已受理且送达待核对的原生语音可查询；不会重新录音或提交')
            try:
                receipt = await self.gateway.voice_receipt(job['chat'], job['native_job_id'])
            except Exception:
                raise ValueError('原生语音状态暂无法读取，未重发') from None
            if receipt is None:
                raise ValueError('原生任务仍未结束，请稍后核对，未重发')
            state = 'sent' if receipt.confirmed else 'failed_before_send' if receipt.definite_failure else 'needs_review'
            self.change(identity, state=state, receipt=receipt.message_id, error=receipt.error,
                media_cleanup_pending=int(receipt.confirmed))
            self.store.action_finish(f'audio:{identity}#attempt-{job["delivery_attempt"]}',
                'sent' if receipt.confirmed else 'failed' if receipt.definite_failure else 'uncertain',
                receipt.message_id, receipt.error, details={'audio_job_id': identity, **receipt.details})
            if receipt.confirmed:
                self.cleanup_confirmed_media(identity)
            return self.public_job(self.get(identity))

    def action(self, identity, operation):
        job = self.get(identity)
        if not job:
            raise ValueError('音频任务不存在或不属于当前账号')
        if operation == 'cancel':
            if job['state'] == 'sent':
                raise ValueError('音频已经发送，取消不能撤回消息或费用')
            self.change(identity, state='cancelled', error='已取消本地任务；已提交上游的生成仍可能计费')
        elif operation == 'retry_download' and job['state'] == 'download_failed' and job['result_enc']:
            self.change(identity, state='download_retry', error='', retry_at=0)
        elif operation == 'send_file' and job['state'] == 'held' and job['output_file']:
            self.change(identity, state='ready', delivery_override='file', error='', retry_at=0)
        elif operation == 'retry_send' and job['state'] == 'failed_before_send' and job['output_file']:
            # The next send uses a fresh action/idempotency identity; preserve this attempt.
            self.change(identity, state='ready', native_job_id='', error='', retry_at=0)
        else:
            raise ValueError('当前状态不允许该操作；不确定送达不能直接重发')
        return self.public_job(self.get(identity))

    @staticmethod
    def public_job(job):
        return {key: job[key] for key in ('id', 'kind', 'chat', 'destination', 'state', 'created', 'updated',
            'submitted', 'format', 'duration', 'receipt', 'error', 'render_of')} | {
            'arguments': json.loads(job['args_json']), 'applied': json.loads(job['config_json']),
            'timing': json.loads(job['timing_json']), 'playable': bool(job['output_file']),
            'can_retry_send': job['state'] == 'failed_before_send',
            'can_check_delivery': job['state'] == 'needs_review' and bool(job['native_job_id'])}

    def snapshot(self):
        with self.store.lock:
            jobs = [dict(row) for row in self.store.db.execute('SELECT * FROM audio_jobs WHERE account=? ORDER BY created DESC LIMIT 40',
                (self.config.settings.account_id,))]
            usage = dict(self.store.db.execute('SELECT count(*) AS submitted_requests, '
                "sum(CASE WHEN kind='speech' THEN duration END) AS speech_seconds, "
                "sum(CASE WHEN kind='music' THEN 1 ELSE 0 END) AS music_requests, "
                'sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens,sum(total_tokens) AS total_tokens '
                ',count(total_tokens) AS token_reported_requests,sum(CASE WHEN total_tokens IS NULL THEN 1 ELSE 0 END) AS token_missing_requests '
                'FROM audio_usage WHERE account=?', (self.config.settings.account_id,)).fetchone())
        return {**self.settings.snapshot(), 'jobs': [self.public_job(job) for job in jobs], 'history': self.settings.history(),
            'usage': {**usage, 'billing': '电脑语音不调用收费语音 API；云端语音按秒、音乐按次，零Token不代表免费，实际费用以服务商账单为准；未上报Token不计作零'},
            'resources': {'cached_bytes': self.cached_bytes(), 'disk_free_bytes': shutil.disk_usage(self.directory).free,
                'ffmpeg_available': bool(shutil.which('ffmpeg')), 'ffprobe_available': bool(shutil.which('ffprobe'))},
            'tool_support': {'available': self.config.settings.api_style in ('auto', 'openai') and not self.config.settings.model_stream_enabled,
                'note': 'AI自动工具需要 Chat Completions 非流式接口；其他协议暂不暴露音频工具'},
            'automatic_cleanup': True,
            'cleanup_policy': {'sent_media': 'confirmed delivery only', 'preview_retention_hours': 24,
                'retry_and_uncertain_media': 'retained', 'job_and_usage_history': 'retained'}}

    async def loop(self, generate=False):
        while True:
            try:
                if generate and time.monotonic() >= self.cleanup_check_at:
                    self.cleanup_check_at = time.monotonic() + self.CLEANUP_INTERVAL_SECONDS
                    await self.cleanup_expired_previews()
                if generate and time.monotonic() >= self.pc_check_at:
                    self.pc_check_at = time.monotonic() + 20
                    _, settings = self.settings.current()
                    if (settings.providers[settings.speech_profile].backend == 'pc_bridge'
                            and hasattr(self.provider, 'pc_status')):
                        await self.pc_status()
                worked = await (self.generate_once() if generate else self.deliver_once())
                await asyncio.sleep(.4 if worked else 2)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Audio errors stay local: never globally pause ordinary replies.
                await asyncio.sleep(3)
