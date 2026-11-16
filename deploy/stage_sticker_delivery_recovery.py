"""Stage the native-sticker recovery fix against the live release only."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/sticker-delivery-recovery'
FILES = [
    'whalegirl_linux/gateway.py',
    'whalegirl_linux/delivery.py',
    'whalegirl_linux/safety.py',
    'whalegirl_linux/runtime.py',
    'deploy/chat_select_linux.py',
]


def remote(code):
    return subprocess.run(
        ['ssh.exe', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
         'root@120.46.186.50', 'python3', '-'],
        input=code, capture_output=True, text=True, encoding='utf-8',
        check=True, timeout=60,
    ).stdout


def live_files():
    code = (
        'import base64,json,pathlib\n'
        "root=pathlib.Path('/opt/whalegirl-linux/current').resolve()\n"
        f'names={FILES!r}\n'
        "print(json.dumps({'root':str(root),'files':{n:base64.b64encode((root/n).read_bytes()).decode() for n in names}}))"
    )
    return json.loads(remote(code))


def replace_once(text, old, new, label):
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError(f'Live anchor is not unique: {label}')
    return text.replace(old, new, 1)


def transform(name, production):
    value = production
    if name == 'whalegirl_linux/gateway.py':
        old = '    details: dict = field(default_factory=dict)\n'
        new = old + '''\n    @property\n    def sticker_pre_send_failure(self):\n        """An optional sticker picker failed before any native send click."""\n        return (self.definite_failure and isinstance(self.details, dict)\n                and self.details.get('kind') == 'sticker'\n                and self.details.get('submit_state') == 'failed_before_send')\n'''
        value = replace_once(value, old, new, 'Receipt.sticker_pre_send_failure')
    elif name == 'whalegirl_linux/delivery.py':
        old = 'MAX_SEND_ATTEMPTS = 2  # Includes the original submission.\n\n\n'
        new = old + '''def sticker_picker_layout_error(error):\n    """Recognize a stale/virtualized picker before any send click."""\n    text = str(error or '')\n    return any(marker in text for marker in (\n        'outside the currently exposed native page',\n        'identity changed before sending',\n        'position became ambiguous before sending',\n        'favorite panel changed before sending',\n        'Native favorite layout needs refresh',\n    ))\n\n\n'''
        value = replace_once(value, old, new, 'sticker picker classifier')
        old = "        details = {key: values[key] for key in ('text', 'md5', 'asset_id') if key in values}\n"
        new = old + "        details['kind'] = kind\n"
        value = replace_once(value, old, new, 'delivery kind metadata')
        old = '''        except (httpx.HTTPError, ValueError, OSError) as exc:\n            receipt = Receipt(False,\n                error=('微信原生发送回执待核实，不自动重发' if crossed_boundary else\n                       '发送前读取暂时失败：' + type(exc).__name__),\n                definite_failure=not crossed_boundary, attempts=attempt, details=details)\n        attempts_log.append({'attempt': attempt, 'definite_failure': receipt.definite_failure,\n'''
        new = '''        except (httpx.HTTPError, ValueError, OSError) as exc:\n            receipt = Receipt(False,\n                error=('微信原生发送回执待核实，不自动重发' if crossed_boundary else\n                       '发送前读取暂时失败：' + type(exc).__name__),\n                definite_failure=not crossed_boundary, attempts=attempt, details=details)\n        if (kind == 'sticker' and receipt.definite_failure and\n                details.get('submit_state') == 'failed_before_send' and\n                sticker_picker_layout_error(receipt.error)):\n            # The picker never clicked a tile. Repeating against the same\n            # virtualized panel only adds latency and can create an account\n            # level recovery episode, so leave the optional sticker out.\n            receipt.retryable = False\n        attempts_log.append({'attempt': attempt, 'definite_failure': receipt.definite_failure,\n'''
        value = replace_once(value, old, new, 'non-retryable picker failure')
    elif name == 'whalegirl_linux/safety.py':
        old = '''                    self._save(account,state)\n                return\n            if not receipt.definite_failure or (not settings.safety_enabled\n'''
        new = '''                    self._save(account,state)\n                return\n            if getattr(receipt, 'sticker_pre_send_failure', False):\n                # An optional sticker failed before any native send click.\n                # Keep an auditable warning, but never pause the account.\n                self.record(account, 'sticker_pre_send_failed',\n                            str(receipt.error or '表情定位失败，未提交发送')[:240],\n                            chat=chat, level='warning')\n                return\n            if not receipt.definite_failure or (not settings.safety_enabled\n'''
        value = replace_once(value, old, new, 'safety sticker exemption')
    elif name == 'whalegirl_linux/runtime.py':
        old = "                reason=receipt.error;end_state='partial' if sent else 'failed';break\n"
        new = '''                reason=receipt.error\n                if getattr(receipt, 'sticker_pre_send_failure', False):\n                    # A misplaced optional sticker must not discard later text\n                    # actions from the same reply or block other chats.\n                    omitted += 1\n                    end_state = 'partial'\n                    continue\n                end_state='partial' if sent else 'failed';break\n'''
        value = replace_once(value, old, new, 'runtime optional sticker continuation')
    elif name == 'deploy/chat_select_linux.py':
        old = '''    if len(tiles)==total:\n        known.update({n['native_ref']:i for i,n in enumerate(tiles) if n.get('native_ref')})\n        return tiles[index] if 0<=index<len(tiles) else None\n    offsets={known[n['native_ref']]-i for i,n in enumerate(tiles) if n.get('native_ref') in known}\n'''
        new = '''    if len(tiles)==total:\n        known.update({n['native_ref']:i for i,n in enumerate(tiles) if n.get('native_ref')})\n        return tiles[index] if 0<=index<len(tiles) else None\n    # A final refresh can briefly report conflicting offsets while Qt recycles\n    # virtual tiles. An already-known exact object is safe to select.\n    target_refs=[ref for ref,absolute in known.items() if absolute==index]\n    if len(target_refs)==1:\n        exact=[n for n in tiles if n.get('native_ref')==target_refs[0]]\n        if len(exact)==1:\n            return exact[0]\n    offsets={known[n['native_ref']]-i for i,n in enumerate(tiles) if n.get('native_ref') in known}\n'''
        value = replace_once(value, old, new, 'exact favorite anchor fallback')
    compile(value, name, 'exec')
    return value


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    live = live_files()
    before, after, reviewed = {}, {}, {}
    for name in FILES:
        raw = base64.b64decode(live['files'][name])
        before[name] = hashlib.sha256(raw).hexdigest()
        target_before = OUT / 'server-before' / name
        target_before.parent.mkdir(parents=True, exist_ok=True)
        target_before.write_bytes(raw)
        value = transform(name, raw.decode('utf-8'))
        target = OUT / 'payload' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8', newline='\n')
        after[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        reviewed[name] = {'before': before[name], 'after': after[name]}
    manifest = {'root': live['root'], 'before': before, 'after': after, 'reviewed': reviewed}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    with tarfile.open(OUT / 'release.tar', 'w') as archive:
        for name in FILES:
            archive.add(OUT / 'payload' / name, arcname=name)
    print(json.dumps({'root': live['root'], 'files': FILES, 'manifest': str(OUT / 'manifest.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
