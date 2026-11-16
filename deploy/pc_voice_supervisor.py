"""Owner-started Windows voice worker, independent of Codex runtimes.

Uses the existing GPT-SoVITS environment. All child windows are hidden. Only the
three explicitly owned components are started, never arbitrary services stopped.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pc_voice_common import ModelStartupGuard, read_control, write_json
from pc_voice_engine import load_engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', default='D:/appData/qqbot/voise/runtime')
    args = parser.parse_args()
    root = Path(args.runtime).resolve()
    scripts = Path(__file__).resolve().parent
    python = Path('D:/anconda/envs/GPTSoVits/python.exe')
    tts = Path('D:/yachiyo/voice')
    engine = load_engine(root)
    ssh = Path(os.environ['WINDIR']) / 'System32/OpenSSH/ssh.exe'
    assert all(p.exists() for p in (root / 'bridge.json', root / 'cyrene.yaml',
                                   root / 'tunnel_ed25519', python, ssh))
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    mutex = kernel.CreateMutexW(None, True, 'Local\\WhalegirlPcVoice20261111')
    if not mutex:
        raise OSError('Cannot create worker mutex')
    if kernel.GetLastError() == 183:
        kernel.CloseHandle(mutex)
        return

    def log(event):
        path = root / 'worker-events.jsonl'
        # Retain bounded logs; no automatic deletion of prior records.
        try:
            if not path.exists() or path.stat().st_size < 2 * 1024**2:
                with path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'at': time.time(), **event}) + '\n')
        except OSError:
            pass

    def signature(process, name):
        try:
            values = [v.replace('\\', '/').lower() for v in process.cmdline()]
            if name == 'model':
                return all(v.replace('\\', '/').lower() in values for v in engine['signature'])
            if name == 'bridge':
                return str(scripts / 'pc_voice_bridge.py').replace('\\', '/').lower() in values and str(root / 'bridge.json').replace('\\', '/').lower() in values
            return ('whalegirl-voice@120.46.186.50' in values and '127.0.0.1:19881:127.0.0.1:9882' in values
                    and str(root / 'tunnel_ed25519').replace('\\', '/').lower() in values)
        except (psutil.Error, OSError):
            return False

    def existing(name):
        if name == 'tunnel':
            for process in psutil.process_iter(['name']):
                if (process.info.get('name') or '').lower() == 'ssh.exe' and signature(process, name):
                    return process
            return None
        port = 9881 if name == 'model' else 9882
        for connection in psutil.net_connections(kind='tcp'):
            if connection.status != psutil.CONN_LISTEN or connection.laddr.port != port:
                continue
            process = psutil.Process(connection.pid)
            if not signature(process, name):
                raise RuntimeError('A different service owns voice port ' + str(port) + '; left untouched')
            return process
        return None

    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
               OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               CUDA_VISIBLE_DEVICES='0', GPT_SOVITS_API_BATCH_SIZE='1', GPT_SOVITS_PARALLEL_AUDIO_SYNTHESIS='0',
               no_proxy='127.0.0.1,localhost')
    env['PATH'] = (str(root / 'tools') + ';'
                   + str(python.parent) + ';' + str(python.parent / 'Library/bin') + ';' + env['PATH'])
    env.update(engine['env'])
    commands = {
        'model': engine['command'],
        'bridge': [str(python), '-I', str(scripts / 'pc_voice_bridge.py'), '--config', str(root / 'bridge.json')],
        'tunnel': [str(ssh), '-n', '-N', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=12', '-o', 'ExitOnForwardFailure=yes',
                   '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-i', str(root / 'tunnel_ed25519'),
                   '-R', '127.0.0.1:19881:127.0.0.1:9882', 'whalegirl-voice@120.46.186.50'],
    }
    owned = {name: existing(name) for name in commands}
    config = json.loads((root / 'bridge.json').read_text(encoding='utf-8'))

    def local_health():
        try:
            request = urllib.request.Request('http://127.0.0.1:9882/health',
                headers={'Authorization': 'Bearer ' + config['token']})
            with urllib.request.urlopen(request, timeout=3) as response:
                return json.load(response)
        except (OSError, ValueError):
            return {}

    def state(phase, **extra):
        value = {'pid': os.getpid(), 'updated': time.time(), 'phase': phase,
                 'engine': engine['backend'],
                 'components': {n: p.pid if p and p.is_running() else None for n, p in owned.items()}, **extra}
        try:
            write_json(root / 'worker-status.json', value)
        except OSError as exc:
            log({'event': 'state_write_deferred', 'error_type': type(exc).__name__})

    def stop_owned(name):
        process = owned[name]
        if process and process.is_running():
            if not signature(process, name):
                raise RuntimeError('Owned voice process identity changed; left untouched')
            children = process.children(recursive=True)
            system_console = Path(os.environ['WINDIR']) / 'System32/conhost.exe'
            consoles = {child.pid for child in children
                        if Path(child.exe()).resolve() == system_console.resolve()}
            if any(not signature(child, name) and child.pid not in consoles for child in children):
                raise RuntimeError('Unexpected child in voice worker; left untouched')
            for child in [*reversed(children), process]:
                if child.pid in consoles:
                    continue
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs([process, *children], timeout=8)
            for child in alive:
                if child.pid not in consoles and signature(child, name):
                    child.kill()
            _, alive = psutil.wait_procs(alive, timeout=5)
            if alive:
                raise RuntimeError('Owned voice process tree did not stop')
            log({'event': 'component_stopped', 'name': name, 'pid': process.pid})
        owned[name] = None

    def drain_and_stop(control):
        started = time.monotonic()
        requested_at = control.get('requested_at', time.time())
        while True:
            if read_control(root)['desired']:
                return False
            status = local_health()
            busy = status.get('busy') is True
            acknowledged = (status.get('cloud_ack_at', 0) >= requested_at
                            and status.get('cloud_accepted') is False)
            state('stopping', busy=busy, cloud_stop_acknowledged=acknowledged)
            if not busy:
                stop_owned('model')
            bridge_alive = owned['bridge'] and owned['bridge'].is_running()
            if (not busy and (acknowledged or not bridge_alive or time.monotonic() - started > 25)
                    or time.monotonic() - started > 210):
                stop_owned('model')
                stop_owned('tunnel')
                stop_owned('bridge')
                state('stopped', cloud_stop_acknowledged=acknowledged)
                log({'event': 'owner_stopped', 'cloud_acknowledged': acknowledged})
                return True
            time.sleep(1)
    next_start = dict.fromkeys(commands, 0.0)
    startup_guard = ModelStartupGuard(read_control(root).get('request_id'))
    log({'event': 'worker_started', 'pid': os.getpid(), 'adopted': {n: p.pid for n, p in owned.items() if p}})
    try:
        while True:
            control = read_control(root)
            startup_guard.control(control.get('request_id'))
            if not control['desired']:
                if drain_and_stop(control):
                    return
                continue
            for name in commands:
                process = owned[name]
                if process and process.is_running():
                    continue
                if process is not None:
                    if name == 'model':
                        try:
                            exit_code = process.wait(timeout=0)
                        except (psutil.Error, OSError):
                            exit_code = None
                        startup_guard.exited(exit_code)
                        log({'event': 'model_exited', 'exit_code': exit_code,
                             'consecutive_failures': startup_guard.failures})
                    owned[name] = None
                if name == 'model' and startup_guard.blocked:
                    continue
                if time.monotonic() < next_start[name]:
                    continue
                if name == 'model' and psutil.virtual_memory().available < 700 * 1024**2:
                    next_start[name] = time.monotonic() + 30
                    log({'event': 'model_deferred', 'reason': 'low_available_RAM'})
                    continue
                running = existing(name)
                if not running:
                    child = subprocess.Popen(commands[name], cwd=engine['cwd'] if name == 'model' else str(scripts), env=env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                    running = psutil.Process(child.pid)
                    log({'event': 'component_started', 'name': name, 'pid': child.pid})
                owned[name] = running
                next_start[name] = time.monotonic() + 15
            health = local_health()
            if health.get('ready') is True:
                startup_guard.ready()
            state('failed' if startup_guard.blocked else 'running' if health.get('ready') is True else 'loading',
                  model_ready=health.get('ready') is True, error=startup_guard.error,
                  model_failures=startup_guard.failures, model_exit_code=startup_guard.exit_code)
            time.sleep(3)
    except BaseException as exc:
        log({'event': 'worker_failed', 'error_type': type(exc).__name__})
        raise
    finally:
        kernel.CloseHandle(mutex)


if __name__ == '__main__':
    main()
