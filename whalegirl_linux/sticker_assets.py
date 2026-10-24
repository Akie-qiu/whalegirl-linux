"""Owned sticker work files and durable, small previews; never WeChat caches."""
import base64
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import time

from PIL import Image


class StickerAssets:
    def __init__(self, directory):
        self.work = Path(directory)/'sticker-work-cache'
        self.previews = Path(directory)/'sticker-previews'
        self.cleaned = 0

    def _path(self, root, md5, suffix):
        if not re.fullmatch('[0-9a-f]{32}', md5):
            raise ValueError('表情 ID 无效')
        if root.is_symlink():
            raise ValueError('表情缓存目录不允许符号链接')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = root/(md5+suffix)
        if path.is_symlink() or path.resolve().parent != root.resolve():
            raise ValueError('表情缓存路径无效')
        return path

    def remember(self, md5, data):
        raw = base64.b64decode(data, validate=True)
        if len(raw) > 4*1024*1024 or hashlib.md5(raw).hexdigest() != md5:
            raise ValueError('原生表情原件校验未通过')
        path = self._path(self.work, md5, '.media')
        with path.open('wb') as stream:
            stream.write(raw)
        os.chmod(path, 0o600)
        self.preview(md5, raw)
        return raw

    def cached(self, md5):
        path = self._path(self.work, md5, '.media')
        return base64.b64encode(path.read_bytes()).decode() if path.is_file() else ''

    def preview(self, md5, raw):
        path = self._path(self.previews, md5, '.webp')
        if path.is_file():
            return path
        source = Image.open(BytesIO(raw))
        frames, durations = [], []
        count = min(getattr(source, 'n_frames', 1), 16)
        indices = sorted({round(i*(getattr(source, 'n_frames', 1)-1)/max(1, count-1)) for i in range(count)})
        for index in indices:
            source.seek(index)
            frame = source.convert('RGBA')
            frame.thumbnail((160, 160))
            frames.append(frame.copy())
            durations.append(max(70, min(300, source.info.get('duration', 100))))
        stream=BytesIO()
        frames[0].save(stream, 'WEBP', save_all=len(frames)>1, append_images=frames[1:],
                      duration=durations, loop=0, quality=65, method=1)
        path.write_bytes(stream.getvalue())
        os.chmod(path, 0o600)
        return path

    def preview_data(self, md5):
        path = self._path(self.previews, md5, '.webp')
        return 'data:image/webp;base64,'+base64.b64encode(path.read_bytes()).decode() if path.is_file() else ''

    def discard(self, md5):
        # One validated, flat, owned file. No glob deletion, recursion or native paths.
        path = self._path(self.work, md5, '.media')
        if path.is_file():
            path.unlink()
            self.cleaned += 1

    def expire(self, hours, active=()):
        if not self.work.exists() or self.work.is_symlink():
            return
        for path in self.work.iterdir():
            if re.fullmatch(r'[0-9a-f]{32}\.media', path.name) and path.stem not in active:
                if not path.is_symlink() and path.is_file() and time.time()-path.stat().st_mtime > hours*3600:
                    self.discard(path.stem)

