"""Manual start/stop panel. Closing it does not keep a logon task enabled."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pc_voice_common import read_control, read_worker, request_control
from pc_voice_engine import voice_catalog
from pc_voice_selection import request_switch, selection_status

ROOT = Path('D:/appData/qqbot/voise/runtime')
PYTHONW = Path('D:/anconda/envs/GPTSoVits/pythonw.exe')
SUPERVISOR = Path(__file__).with_name('pc_voice_supervisor.py')


def backend_label(backend):
    return {'aniflive': 'AnifLive TensorRT 加速', 'gsv-lite': 'GSV-TTS-Lite CUDA 图加速',
            'gpt-sovits': 'GPT-SoVITS 兼容'}.get(backend, '等待电脑')


def worker_live(root):
    state = read_worker(root)
    try:
        process = psutil.Process(int(state['pid']))
        expected = str(SUPERVISOR).replace('\\', '/').lower()
        return (process.is_running() and state.get('phase') != 'stopped'
                and expected in [arg.replace('\\', '/').lower() for arg in process.cmdline()])
    except (psutil.Error, KeyError, ValueError, TypeError):
        return False


def ensure_worker(root):
    if not worker_live(root):
        subprocess.Popen([str(PYTHONW), '-I', str(SUPERVISOR), '--runtime', str(root)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)


def action(root, operation):
    if operation not in ('start', 'stop'):
        raise ValueError('Unsupported voice control action')
    intent = request_control(root, operation == 'start')
    # A stop can also reclaim an orphaned instance through the same identity-
    # checked worker. It never starts fresh model/tunnel components while off.
    ensure_worker(root)
    return {'requested': operation, 'request_id': intent['request_id']}


def snapshot(root):
    control, state = read_control(root), read_worker(root)
    result = {'desired': control['desired'], 'worker_running': worker_live(root),
              'phase': state.get('phase', 'stopped'), 'model_ready': False,
              'error': state.get('error', '') if state.get('phase') == 'failed' else '',
              'cloud_confirmed': False, 'busy': False, 'voices': voice_catalog(root),
              'voice_switch': selection_status(root)}
    try:
        result['active_voice'] = json.loads((root / 'engine.json').read_text()).get('voice_id', 'cyrene')
    except (OSError, ValueError, TypeError):
        result['active_voice'] = 'cyrene'
    catalog = {row['id']: row for row in result['voices']}
    result['engine'] = catalog.get(result['active_voice'], {}).get('engine', {}).get('backend', state.get('engine'))
    try:
        config = json.loads((root / 'bridge.json').read_text(encoding='utf-8'))
        request = urllib.request.Request('http://127.0.0.1:9882/health',
            headers={'Authorization': 'Bearer ' + config['token']})
        with urllib.request.urlopen(request, timeout=3) as response:
            health = json.load(response)
        result.update(model_ready=health.get('ready') is True, busy=health.get('busy') is True,
            engine=health.get('backend', 'gpt-sovits'),
            active_voice=health.get('active_voice', 'cyrene'),
            cloud_confirmed=bool(control['desired'] and not health.get('stopping')
                and health.get('cloud_accepted') and time.time() - health.get('cloud_ack_at', 0) < 45),
            cloud_ack_at=health.get('cloud_ack_at', 0))
    except (OSError, ValueError, KeyError):
        pass
    return result


def run_panel(root, initial_action=''):
    import tkinter as tk
    from tkinter import messagebox

    window = tk.Tk()
    window.title('鲸鱼娘 · 电脑语音')
    window.geometry('600x430')
    window.resizable(False, False)
    window.configure(bg='#F4F1ED')
    frame = tk.Frame(window, bg='#F4F1ED', padx=30, pady=24)
    frame.pack(fill='both', expand=True)
    tk.Label(frame, text='电脑语音', bg='#F4F1ED', fg='#34414D', font=('Microsoft YaHei UI', 22, 'bold')).pack(anchor='w')
    tk.Label(frame, text='想用的时候再启动，服务器会自动接通', bg='#F4F1ED', fg='#66727B', font=('Microsoft YaHei UI', 10)).pack(anchor='w', pady=(3, 20))
    main = tk.StringVar(value='正在读取状态…')
    model = tk.StringVar(value='模型　—')
    cloud = tk.StringVar(value='云端　—')
    tk.Label(frame, textvariable=main, bg='#F4F1ED', fg='#34414D', font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
    for value in (model, cloud):
        tk.Label(frame, textvariable=value, bg='#F4F1ED', fg='#66727B', font=('Microsoft YaHei UI', 10)).pack(anchor='w', pady=(7, 0))

    voice_buttons = {}
    chooser = tk.Frame(frame, bg='#F4F1ED')
    chooser.pack(fill='x', pady=(14, 0))
    def choose_voice(voice_id):
        try:
            request_switch(root, voice_id)
            main.set('正在切换声线，当前语音结束后加载…')
        except (ValueError, OSError) as error:
            messagebox.showerror('切换声线', str(error))
    for row in voice_catalog(root):
        label = row['name'] + ' · ' + backend_label(row['engine']['backend'])
        button = tk.Button(chooser, text=label, command=lambda key=row['id']: choose_voice(key),
            font=('Microsoft YaHei UI', 10), relief='flat', bd=0, padx=15, pady=8)
        button.pack(side='left', padx=(0, 10))
        voice_buttons[row['id']] = button

    def invoke(operation):
        try:
            action(root, operation)
            main.set('正在启动…' if operation == 'start' else '正在停止并通知服务器…')
        except Exception as error:
            messagebox.showerror('电脑语音', '操作未完成：' + type(error).__name__)

    buttons = tk.Frame(frame, bg='#F4F1ED')
    buttons.pack(fill='x', pady=(23, 14))
    start = tk.Button(buttons, text='启动语音', command=lambda: invoke('start'), bg='#6F91AD', fg='white',
        activebackground='#5B809E', activeforeground='white', relief='flat', bd=0,
        font=('Microsoft YaHei UI', 11, 'bold'), padx=25, pady=9)
    start.pack(side='left')
    stop = tk.Button(buttons, text='停止并释放显存', command=lambda: invoke('stop'), bg='#E5D5D1', fg='#614B48',
        activebackground='#DAC5BE', relief='flat', bd=0, font=('Microsoft YaHei UI', 11), padx=18, pady=9)
    stop.pack(side='left', padx=12)
    tk.Label(frame, text='开机不自启。关闭此窗口不会停止语音服务。\n模型首次加载需要一些时间；停止后群聊继续用文字。',
        bg='#F4F1ED', fg='#7D858A', justify='left', font=('Microsoft YaHei UI', 9)).pack(anchor='w')
    pending, values = [False], []

    def refresh():
        if values:
            value = values.pop()
            values.clear()
            active = value['worker_running']
            switching = (value.get('voice_switch') or {}).get('phase') in {'queued','stopping','loading','rolling_back'}
            if not active:
                main.set('已停止' if not value['desired'] else '服务未运行，点击启动')
            elif not value['desired']:
                main.set('当前语音完成后停止…' if value['busy'] else '停止中，正在通知服务器…')
            elif value['phase'] == 'failed':
                main.set(value['error'] or '语音启动失败，请检查后重试')
            elif value['model_ready'] and value['cloud_confirmed']:
                main.set('已连接 · 可以请求语音')
            else:
                main.set('正在连接…')
            if switching:
                main.set('正在切换声线，请稍候…')
            catalog = {row['id']: row for row in value.get('voices', [])}
            for key, button in voice_buttons.items():
                selected = key == value.get('active_voice')
                if key in catalog:
                    row = catalog[key]
                    button.configure(text=row['name'] + ' · ' + backend_label(row['engine']['backend']))
                button.configure(bg='#6F91AD' if selected else '#E7E4E0', fg='white' if selected else '#34414D',
                                 state='disabled' if switching or selected else 'normal')
            label = backend_label(value.get('engine'))
            model.set('模型　' + label + ' · ' + ('已加载' if value['model_ready'] else '启动失败' if value['phase'] == 'failed' else '加载中' if active and value['desired'] else '已关闭'))
            cloud.set('云端　' + ('服务器已确认' if value['cloud_confirmed'] else '等待自动握手' if active and value['desired'] else '语音不可用，继续文字'))
            start.configure(state='disabled' if (active and value['phase'] != 'failed') or switching else 'normal')
            stop.configure(state='normal' if active else 'disabled')
        if not pending[0]:
            pending[0] = True
            def read():
                try:
                    values.append(snapshot(root))
                finally:
                    pending[0] = False
            threading.Thread(target=read, daemon=True).start()
        window.after(1000, refresh)

    if initial_action:
        invoke(initial_action)
    refresh()
    window.mainloop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', default=str(ROOT))
    parser.add_argument('--action', choices=['start', 'stop', 'status'], default='')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    root = Path(args.runtime)
    if args.headless:
        print(json.dumps(snapshot(root) if args.action == 'status' else action(root, args.action), ensure_ascii=False))
    else:
        run_panel(root, args.action if args.action != 'status' else '')
