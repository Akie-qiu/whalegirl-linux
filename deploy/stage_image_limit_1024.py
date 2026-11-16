"""Stage only the 1024px image-canvas limit on top of the live release."""
import base64
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from stage_tool_reply_guard import merge_delta, remote

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/image-limit-1024-20261109'
FILES = (
    'whalegirl_linux/image_canvas.py',
    'whalegirl_linux/images.py',
    'whalegirl_linux/images.js',
    'whalegirl_linux/settings.py',
    'whalegirl_linux/web.html',
    'tests/test_image_canvas.py',
)


def git_head(name):
    return subprocess.run(['git', 'show', 'HEAD:' + name], cwd=ROOT, check=True,
                          capture_output=True, text=True, encoding='utf-8').stdout


def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f'Expected one {label} anchor, found {count}')
    return source.replace(old, new, 1)


def patch_live_settings(source):
    if 'from .image_canvas import MAX_EDGE, cap_base_size' in source:
        raise RuntimeError('Image canvas settings integration already exists')
    source = replace_once(source,
        'from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator\n',
        'from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator\n'
        '\nfrom .image_canvas import MAX_EDGE, cap_base_size\n', 'settings canvas import')
    source = replace_once(source, "    @field_validator('moments_asset_ids')\n",
        "    @field_validator('image_size')\n"
        '    @classmethod\n'
        '    def cap_image_size(cls, value):\n'
        '        capped = cap_base_size(value)\n'
        '        if capped != value:\n'
        "            raise ValueError(f'图片基础分辨率最长边不得超过 {MAX_EDGE}px')\n"
        '        return value\n\n'
        "    @field_validator('moments_asset_ids')\n", 'image size validator')
    source = replace_once(source,
        "            self.active_model_profile = (saved.get('active_model_profile') if isinstance(saved, dict) else '') or self._infer_profile_id(saved)\n",
        "            if isinstance(saved, dict) and isinstance(saved.get('image_size'), str):\n"
        "                previous_image_size = saved['image_size']\n"
        '                saved[\'image_size\'] = cap_base_size(previous_image_size)\n'
        "                if saved['image_size'] != previous_image_size:\n"
        "                    private_write(self.path, json.dumps(saved, ensure_ascii=False))\n"
        "            self.active_model_profile = (saved.get('active_model_profile') if isinstance(saved, dict) else '') or self._infer_profile_id(saved)\n",
        'legacy image size migration')
    save_signature = '    def _save(self, settings: Settings, api_key: str = "", *, profile_id: str | None = None):\n'
    source = replace_once(source, save_signature,
        save_signature +
        '        image_size = cap_base_size(settings.image_size)\n'
        '        if image_size != settings.image_size:\n'
        "            settings = settings.model_copy(update={'image_size': image_size})\n",
        'image size save guard')
    return source


def patch_image_script_version(source):
    return replace_once(source, '/images.js?v=20261101-image-results',
                        '/images.js?v=20261110-image-limit-1024', 'image settings cache version')


def main():
    names = list(FILES)
    live = json.loads(remote(
        "import pathlib,base64,json\nr=pathlib.Path('/opt/whalegirl-linux/current').resolve()\nnames="
        + repr(names) +
        "\nprint(json.dumps({'root':str(r),'files':{n:base64.b64encode((r/n).read_bytes()).decode() for n in names}}))"))
    before, after, reviewed = {}, {}, {}
    OUT.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        raw = base64.b64decode(live['files'][name])
        production = raw.decode('utf-8')
        before[name] = hashlib.sha256(raw).hexdigest()
        if name == 'whalegirl_linux/settings.py':
            value = patch_live_settings(production)
        elif name == 'whalegirl_linux/web.html':
            value = patch_image_script_version(production)
        else:
            value, anchors = merge_delta(git_head(name),
                (ROOT / name).read_text(encoding='utf-8'), production, name)
            reviewed[name] = anchors
        if name.endswith('.py'):
            compile(value, name, 'exec')
        target = OUT / 'payload' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8', newline='\n')
        after[name] = hashlib.sha256(target.read_bytes()).hexdigest()
        (OUT / 'server-before' / name).parent.mkdir(parents=True, exist_ok=True)
        (OUT / 'server-before' / name).write_bytes(raw)

    (OUT / 'manifest.json').write_text(json.dumps({
        'root': live['root'], 'before': before, 'after': after,
        'unique_line_anchors': reviewed,
    }, indent=2), encoding='utf-8')
    with tarfile.open(OUT / 'release.tar', 'w') as archive:
        for name in FILES:
            archive.add(OUT / 'payload' / name, arcname=name)
    print(json.dumps({'root': live['root'], 'files': len(FILES),
                      'unique_line_anchors': reviewed}, ensure_ascii=False))
    for name in FILES:
        old = (OUT / 'server-before' / name).read_text(encoding='utf-8').splitlines(keepends=True)
        new = (OUT / 'payload' / name).read_text(encoding='utf-8').splitlines(keepends=True)
        delta = list(difflib.unified_diff(old, new, fromfile='live/' + name,
                                         tofile='candidate/' + name, n=2))
        print(''.join(delta), end='')


if __name__ == '__main__':
    main()
