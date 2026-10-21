"""Read only the sanitized server log projection for authenticated operators."""
import json
from pathlib import Path
import time


def tail_json(path: Path, limit: int):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            position, blocks, count, size = stream.tell(), [], 0, 0
            while position > 0 and count <= limit and size < 4 * 1024 * 1024:
                length = min(8192, position)
                position -= length
                stream.seek(position)
                block = stream.read(length)
                blocks.append(block)
                count += block.count(b'\n')
                size += length
        rows = []
        for line in b''.join(reversed(blocks)).splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except (ValueError, UnicodeError):
                continue
        return rows
    except FileNotFoundError:
        return []


def read_stability(data: Path, *, export=False):
    root = data / 'stability'
    try:
        summary = json.loads((root / 'summary.json').read_text('utf-8'))
    except (FileNotFoundError, ValueError):
        return {'available': False, 'message': '等待服务器首次运行日志采样'}
    now = time.time()
    result = {'available': True, 'stale': now - summary.get('latest_at', 0) > 180,
              'summary': summary, 'events': list(reversed(tail_json(root / 'events.jsonl', 60)))}
    if export:
        samples = []
        for path in sorted(root.glob('samples-????-??-??.jsonl'))[-2:]:
            samples.extend(tail_json(path, 1500))
        result.update(exported_at=now, time_zone='Asia/Shanghai',
                      samples=[row for row in samples if now - 86400 <= row.get('at', 0) <= now][-1500:],
                      events=tail_json(root / 'events.jsonl', 1000))
    return result
