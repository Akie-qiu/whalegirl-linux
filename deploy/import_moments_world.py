"""Import the user's three cast images and source document; never open the live DB."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from whalegirl_linux.image_references import ReferenceLibrary
from whalegirl_linux.settings import Config, private_write


def main():
    folder = Path(sys.argv[1])
    expected = json.loads((folder / 'source-manifest.json').read_text())
    for name, checksum in expected.items():
        if name not in ('gpt.jpg', 'claude.jpg', 'gemini.jpg', 'source.txt') or hashlib.sha256((folder / name).read_bytes()).hexdigest() != checksum:
            raise RuntimeError('User source file mismatch: ' + name)
    config = Config(Path('/var/lib/whalegirl-linux/core'))
    if not config.settings.account_id:
        raise RuntimeError('No configured WeChat account')
    library = ReferenceLibrary(config)
    account = hashlib.sha256(config.settings.account_id.encode()).hexdigest()[:20]
    root = config.directory / 'moments-world' / account
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / 'cast.json'
    if target.exists():
        old = json.loads(target.read_text())
        if old.get('source_checksums') == expected:
            print(json.dumps({'already_imported': True, 'characters': len(old.get('characters', []))}))
            return
        raise RuntimeError('A different cast configuration already exists; preserve it for review')
    cards = []
    for name in ('gpt', 'claude', 'gemini'):
        item = library.store((folder / (name + '.jpg')).read_bytes())
        cards.append({'id': name, 'reference_id': item['id']})
    private_write(root / 'source.txt', (folder / 'source.txt').read_text(encoding='utf-8'))
    private_write(target, json.dumps({'enabled': True, 'characters': cards, 'source_checksums': expected,
        'source_name': 'AI娘四人详细角色扮演设定集', 'created': time.time()}, ensure_ascii=False))
    # Core runs under this existing service identity; only the imported files
    # and this reference directory need ownership assigned.
    owner = (config.directory / 'settings.json').stat()
    for path in (root, *root.iterdir(), library.root):
        os.chown(path, owner.st_uid, owner.st_gid)
    for card in cards:
        os.chown(library.path(card['reference_id']), owner.st_uid, owner.st_gid)
    print(json.dumps({'imported': len(cards), 'source_archived': True,
        'identities': [c['id'] for c in cards], 'source_checksums': expected}, ensure_ascii=False))


if __name__ == '__main__':
    main()
