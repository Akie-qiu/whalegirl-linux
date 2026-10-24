"""Record client lifetime and resource failures without reading chat content."""
import datetime
from collections import deque
import json
import os
from pathlib import Path
import pwd
import subprocess
import time
import urllib.request

ROOT = Path('/var/lib/whalegirl-linux/stability')
NAME = 'whalegirl-wechat-candidate-4118'
PUBLIC = Path('/var/lib/whalegirl-linux/core/stability')
LOCAL = datetime.timezone(datetime.timedelta(hours=8))


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def publish(path, payload):
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chown(pending, 0, pwd.getpwnam('whalegirl-ai').pw_gid)
    pending.chmod(0o640)
    pending.replace(path)


def disposition(sample):
    if sample.get('running') is False or sample.get('wechat') == []:
        return 'offline'
    if sample.get('auth_status') == 'logged_in' and sample.get('account_present'):
        return 'online'
    if sample.get('auth_status') == 'logged_out':
        return 'offline'
    return 'unknown'


def record(result):
    ROOT.mkdir(mode=0o700, exist_ok=True)
    PUBLIC.mkdir(mode=0o750, exist_ok=True)
    os.chown(PUBLIC, 0, pwd.getpwnam('whalegirl-ai').pw_gid)
    PUBLIC.chmod(0o750)
    previous = read_json(ROOT / 'latest.json')
    tracking = read_json(ROOT / 'tracking.json')
    now = result['at']
    events = []

    def event(code, message, level='info'):
        events.append({'at': now, 'time': result['time'], 'code': code, 'level': level, 'message': message})

    if not tracking:
        # Carry forward only the contiguous samples actually recorded before
        # this logger was installed, rather than inventing earlier uptime.
        since, next_at = None, now
        history = ROOT / 'observations.jsonl'
        if history.exists():
            with history.open() as stream:
                rows = list(deque(stream, maxlen=1500))
            for line in reversed(rows):
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if disposition(row) != 'online' or next_at - row['at'] > 180:
                    break
                since, next_at = row['at'], row['at']
        tracking = {'monitor_started_at': now, 'online_since': since,
                    'disconnect_count': 0, 'longest_observed_online_seconds': 0}
        event('logging_started', '服务器持久日志已启用，每分钟采样；本机电脑关闭不影响记录')
    status = disposition(result)
    old_status = tracking.get('status', disposition(previous))
    gap = bool(previous.get('at') and now - previous['at'] > 180)
    changed_process = bool(previous.get('wechat') and result.get('wechat') and
                           (previous['wechat'] != result['wechat'] or previous.get('container_id') != result.get('container_id')))
    if gap:
        event('sampling_gap', '采样中断超过三分钟，连续在线时长从本次有效采样重新计算', 'warning')
    if changed_process:
        event('wechat_process_changed', '检测到微信主进程或容器发生变化，重新计算连续在线时长', 'warning')

    same_container = previous.get('container_id') == result.get('container_id')
    oom_delta = max(0, result.get('memory.events', {}).get('oom_kill', 0) - previous.get('memory.events', {}).get('oom_kill', 0)) if same_container else 0
    pids_delta = max(0, result.get('pids.events', {}).get('max', 0) - previous.get('pids.events', {}).get('max', 0)) if same_container else 0
    if oom_delta:
        event('oom_kill', f'容器新增 {oom_delta} 次内存不足进程击杀；需结合内核日志确认被杀进程', 'danger')
    if pids_delta:
        event('task_limit', f'容器新增 {pids_delta} 次任务上限拒绝创建', 'warning')
    if status == 'online':
        if old_status != 'online' or gap or changed_process or not tracking.get('online_since'):
            tracking['online_since'] = now
            tracking['last_reconnect_at'] = now
            event('wechat_online', '观测到微信已登录，开始新的连续在线记录')
    else:
        tracking['online_since'] = None
        if status != old_status:
            if status == 'offline':
                reason = ('微信容器已停止' if result.get('running') is False else
                          '微信主进程不存在' if result.get('wechat') == [] else
                          '微信客户端显示未登录；具体原因待核对')
                tracking['disconnect_count'] += 1
                tracking['last_disconnect_at'] = now
                event('wechat_offline', reason + '（时间为首次发现时间，采样间隔约一分钟）', 'danger')
            else:
                event('monitor_unavailable', '暂时无法确认微信登录状态，详见采样错误；不将其直接认定为微信掉线', 'warning')
    if previous.get('core_available') is True and result.get('core_available') is False:
        event('bot_core_unavailable', '机器人管理服务暂不可用，微信客户端状态单独记录', 'warning')
    elif previous.get('core_available') is False and result.get('core_available') is True:
        event('bot_core_recovered', '机器人管理服务恢复')
    guard = result.get('restart_guard', {})
    old_guard = previous.get('restart_guard', {})
    guard_delta = max(0, guard.get('blocked_count', 0) - old_guard.get('blocked_count', 0))
    if guard_delta:
        event('ui_timeout_restart_prevented', f'界面读取超时触发了 {guard_delta} 次自动强杀请求，已保留仍运行的微信；需核对界面读取状态', 'warning')
    reader = result.get('a11y_reader', {})
    old_reader = previous.get('a11y_reader', {})
    if reader.get('mounted') and not old_reader.get('mounted'):
        event('a11y_bulk_reader_enabled', '界面读取修复已生效：批量读取稳定子项引用，界面读取串行执行')
    elif old_reader.get('mounted') and not reader.get('mounted'):
        event('a11y_reader_mount_missing', '界面读取修复挂载暂不可确认，需检查容器启动流程', 'warning')
    duration = max(0, now - tracking['online_since']) if tracking.get('online_since') else 0
    tracking['longest_observed_online_seconds'] = max(tracking.get('longest_observed_online_seconds', 0), duration)
    if duration >= 86400 and tracking.get('milestone_session') != tracking['online_since']:
        tracking['milestone_session'] = tracking['online_since']
        event('online_24h', '已完成连续二十四小时在线采样，最终验收还需核对资源事件和采样覆盖范围')
    tracking.update(status=status, last_at=now)
    (ROOT / 'tracking.json').write_text(json.dumps(tracking, ensure_ascii=False, indent=2), encoding='utf-8')

    day = datetime.datetime.fromtimestamp(now, LOCAL).strftime('%Y-%m-%d')
    with (ROOT / 'observations.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + '\n')
    sample_path = PUBLIC / ('samples-' + day + '.jsonl')
    with sample_path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(result, ensure_ascii=False) + '\n')
    os.chown(sample_path, 0, pwd.getpwnam('whalegirl-ai').pw_gid)
    sample_path.chmod(0o640)
    for row in events:
        for path in [ROOT / 'events.jsonl', PUBLIC / 'events.jsonl']:
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        with (ROOT / ('runtime-' + day + '.log')).open('a', encoding='utf-8') as stream:
            stream.write(f"{row['time']} [{row['level']}] {row['message']}\n")
    os.chown(PUBLIC / 'events.jsonl', 0, pwd.getpwnam('whalegirl-ai').pw_gid)
    (PUBLIC / 'events.jsonl').chmod(0o640)
    pending = ROOT / 'latest.pending'
    pending.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(ROOT / 'latest.json')
    summary = {**tracking, 'latest_at': now, 'observed_online_seconds': duration,
               'sampling_seconds': 60, 'goal_seconds': 86400, 'sample': result}
    publish(PUBLIC / 'summary.json', summary)
    print(json.dumps({'status': status, 'observed_online_seconds': duration, 'new_events': events}, ensure_ascii=False))


def collect():
    result = {'at': time.time(), 'time': datetime.datetime.now(LOCAL).isoformat()}
    errors = {}
    try:
        info = json.loads(subprocess.check_output(['docker', 'inspect', NAME], text=True, timeout=8))[0]
        result.update(container_id=info['Id'][:12], running=info['State']['Running'],
                      restarts=info['RestartCount'], init=info['HostConfig'].get('Init', False))
        if result['running']:
            pid = info['State']['Pid']
            rel = next(s.split(':', 2)[2] for s in (Path('/proc') / str(pid) / 'cgroup').read_text().splitlines() if s.startswith('0::'))
            cg = Path('/sys/fs/cgroup') / rel.lstrip('/')
            for name in ['memory.current', 'memory.swap.current', 'pids.current', 'pids.max']:
                result[name] = (cg / name).read_text().strip()
            for name in ['memory.events', 'pids.events']:
                result[name] = dict((k, int(v)) for k, v in (s.split() for s in (cg / name).read_text().splitlines()))
            native, zombies, readers = [], 0, 0
            for process in Path('/proc').iterdir():
                if not process.name.isdigit():
                    continue
                try:
                    if info['Id'] not in (process / 'cgroup').read_text():
                        continue
                    fields = dict(s.split(':', 1) for s in (process / 'status').read_text().splitlines() if ':' in s)
                    zombies += fields['State'].strip().startswith('Z')
                    args = (process / 'cmdline').read_bytes().split(b'\0')
                    readers += args[:3] == [b'python3', b'-u', b'/data/native-tools/native_message_reader.py']
                    if fields['Name'].strip() == 'wechat':
                        native.append({'pid': int(process.name), 'started_ticks': (process / 'stat').read_text().rsplit(') ', 1)[1].split()[19]})
                except (FileNotFoundError, ProcessLookupError):
                    pass
            result.update(wechat=native, zombies=zombies, native_readers=readers)
            helper = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools/a11y_safe.py')
            if helper.is_file():
                try:
                    mounted = helper.read_bytes() == (Path('/proc') / str(pid) / 'root/opt/tools/a11y-dump').read_bytes()
                    samples = list((helper.parent / 'a11y-state').glob('latest-*.json'))
                    newest = max(samples, key=lambda p: p.stat().st_mtime) if samples else None
                    result['a11y_reader'] = {'version': 'bulk-references-v1', 'mounted': mounted,
                                             'latest': read_json(newest) if newest else {}}
                except OSError:
                    result['a11y_reader'] = {'version': 'bulk-references-v1', 'mounted': False}
            tools = Path('/var/lib/whalegirl-linux/candidate-4.1.1.8/gateway/native-tools')
            guard = read_json(tools / 'health-state/restart-guard.json')
            try:
                installed = (tools/'health_restart_guard.py').read_bytes() == (Path('/proc')/str(pid)/'root/usr/local/sbin/kill').read_bytes()
            except OSError:
                installed = False
            result['restart_guard'] = {**guard, 'installed': installed}
    except Exception as exc:
        errors['resources'] = type(exc).__name__
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        token = Path('/var/lib/whalegirl-linux/core/gateway.token').read_text().strip()
        request = urllib.request.Request('http://127.0.0.1:6175/api/status/auth', headers={'Authorization': 'Bearer ' + token})
        with opener.open(request, timeout=6) as response:
            auth = json.load(response)
        result.update(auth_status=auth.get('status'), account_present=bool(auth.get('loggedInUser')))
    except Exception as exc:
        errors['native_status'] = type(exc).__name__
    try:
        token = Path('/var/lib/whalegirl-linux/core/admin.token').read_text().strip()
        request = urllib.request.Request('http://127.0.0.1:8092/api/state', headers={'Authorization': 'Bearer ' + token})
        with opener.open(request, timeout=6) as response:
            state = json.load(response)
        result.update(core_available=True, enabled=state.get('enabled'),
                      paused=bool(state.get('paused_reason')), error_present=bool(state.get('last_error')))
    except Exception as exc:
        result['core_available'] = False
        errors['core'] = type(exc).__name__
    if errors:
        result['errors'] = errors
    record(result)


if __name__ == '__main__':
    collect()
