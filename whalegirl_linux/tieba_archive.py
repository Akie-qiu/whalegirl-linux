"""Durable source snapshots, separate from summarized memes and reusable image assets."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image
from .resources import native_pressure


def original_text(source):
    """Add provenance headings without trimming or rewriting any source text."""
    parts = [source.get('title', ''), '原帖：' + source['url'], '贴吧：' + source.get('forum', '')]
    posts = source.get('posts', [])
    if not any(p.get('floor') == 1 for p in posts):
        parts.extend(['\n【列表中的正文；主楼未读到时不视为完整主楼】', source.get('text', '')])
    for p in posts:
        parts.extend(['\n【' + str(p.get('floor') or '?') + '楼 · ' + p.get('author', '') + '】', p['url'], p['text']])
        for c in p.get('comments', []):
            parts.extend(['\n【楼中楼 · ' + c.get('author', '') + '】', c['url'], c['text']])
    parts.append('\n【范围】主楼及本次实际读到的楼层/楼中楼；未读取的后续内容不在本收藏内')
    return '\n'.join(parts)


class TiebaArchive:
    def __init__(self, service):
        self.service = service
        self.config, self.store = service.config, service.store
        self.root = self.config.directory / 'tieba-archives'
        self.lock = asyncio.Lock()
        with self.store.lock:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS tieba_archives(
                    id TEXT PRIMARY KEY,account TEXT NOT NULL,tid TEXT NOT NULL,forum TEXT NOT NULL,title TEXT NOT NULL,
                    url TEXT NOT NULL,reason TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,
                    created REAL NOT NULL,updated REAL NOT NULL,text_bytes INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
                    UNIQUE(account,tid));
                CREATE TABLE IF NOT EXISTS tieba_archive_images(
                    archive_id TEXT NOT NULL,image_id TEXT NOT NULL,pid TEXT NOT NULL,source_url TEXT NOT NULL,
                    post_url TEXT NOT NULL,original_available INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending',
                    bytes INTEGER NOT NULL DEFAULT 0,sha256 TEXT NOT NULL DEFAULT '',mime TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',PRIMARY KEY(archive_id,image_id));
            ''')
            self.store.db.execute("UPDATE tieba_archives SET state='partial',error='保存中断，将在空闲时继续' WHERE state='saving'")
            self.store.db.commit()

    def execute(self, sql, args=()):
        return self.service.execute(sql, args)

    def row(self, identity):
        row = self.store.db.execute('SELECT * FROM tieba_archives WHERE id=? AND account=?',
                                    (identity, self.config.settings.account_id)).fetchone()
        return dict(row) if row else None

    def directory(self, row):
        return self.root / hashlib.sha256(row['account'].encode()).hexdigest()[:24] / row['id']

    def used_bytes(self):
        return (self.store.db.execute('SELECT coalesce(sum(text_bytes),0) FROM tieba_archives').fetchone()[0]
                + self.store.db.execute('SELECT coalesce(sum(bytes),0) FROM tieba_archive_images').fetchone()[0])

    def stats(self):
        account = self.config.settings.account_id
        return {'count': self.store.db.execute('''SELECT count(*) FROM tieba_archives a
                    LEFT JOIN tieba_lifecycle l ON l.account=a.account AND l.tid=a.tid WHERE a.account=?
                    AND coalesce(l.trashed_at,0)=0 AND coalesce(l.deleted_at,0)=0''', (account,)).fetchone()[0],
                'used_bytes': self.used_bytes(), 'limit_mb': self.config.settings.tieba_archive_limit_mb}

    def images(self, identity):
        return [dict(r) for r in self.store.db.execute('SELECT * FROM tieba_archive_images WHERE archive_id=? ORDER BY rowid',
                                                      (identity,))]

    def summaries(self, offset=0, limit=20):
        offset, limit = max(0, offset), max(1, min(100, limit))
        rows = self.store.db.execute('''SELECT a.id,a.tid,a.forum,a.title,a.url,a.reason,a.state,a.created,a.updated,a.error,
            coalesce(l.pinned,0) AS pinned,coalesce(l.last_used,0) AS last_used,coalesce(l.uses,0) AS uses,
            count(i.image_id) AS image_count,coalesce(sum(i.state='saved'),0) AS saved_images
            FROM tieba_archives a LEFT JOIN tieba_archive_images i ON i.archive_id=a.id
            LEFT JOIN tieba_lifecycle l ON l.account=a.account AND l.tid=a.tid WHERE a.account=?
            AND coalesce(l.trashed_at,0)=0 AND coalesce(l.deleted_at,0)=0
            GROUP BY a.id ORDER BY a.created DESC LIMIT ? OFFSET ?''',
            (self.config.settings.account_id, limit, offset)).fetchall()
        return {'items': [dict(r) for r in rows], 'total': self.stats()['count'], 'offset': offset}

    def add(self, source, reason):
        """Queue exact v2 data, or re-fetch legacy previews rather than calling them originals."""
        account = self.config.settings.account_id
        retention = self.service.retention
        if not retention.active(source['tid']):
            return None
        retention.add(source['tid'])
        old = self.store.db.execute('SELECT id FROM tieba_archives WHERE account=? AND tid=?', (account, source['tid'])).fetchone()
        if old:
            return old[0]
        identity, now = uuid.uuid4().hex, time.time()
        self.execute('''INSERT INTO tieba_archives(id,account,tid,forum,title,url,reason,payload,state,created,updated)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (identity, account, source['tid'], source['forum'], source['title'],
            source['url'], reason[:300], json.dumps(source, ensure_ascii=False), 'pending' if source.get('source_version') == 2 else 'requested', now, now))
        return identity

    def request(self, tid):
        if not self.config.settings.tieba_enabled or not self.config.settings.tieba_archive_enabled:
            raise ValueError('请先开启贴吧浏览和原文收藏')
        row = self.store.db.execute('SELECT payload FROM tieba_posts WHERE account=? AND tid=?',
                                    (self.config.settings.account_id, tid)).fetchone()
        if not row:
            raise ValueError('没有这条浏览记录')
        identity = self.add(json.loads(row[0]), '管理员手动收藏')
        if not identity:
            raise ValueError('内容已在回收站，请先恢复；永久清理的原文不能恢复')
        self.service.retention.edit(tid, 'pin')
        return {'queued': True, 'id': identity}

    def retry(self, identity):
        row = self.row(identity)
        if not row:
            raise ValueError('没有这条收藏')
        if row['state'] not in ('saving', 'complete'):
            state = 'partial' if json.loads(row['payload']).get('source_version') == 2 else 'requested'
            self.execute('UPDATE tieba_archives SET state=?,attempts=0,retry_at=0,error=\'\' WHERE id=?', (state, identity))
        return {'queued': True}

    def current(self, row):
        return (self.service.current(row['account']) and self.config.settings.tieba_archive_enabled
                and self.service.retention.active(row['tid']) and not native_pressure(self.config))

    def persist_source(self, row, source):
        directory = self.directory(row)
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        raw = json.dumps(source, ensure_ascii=False, indent=2).encode('utf-8')
        text = original_text(source).encode('utf-8')
        if self.used_bytes() - row['text_bytes'] + len(raw) + len(text) > self.config.settings.tieba_archive_limit_mb * 1024**2:
            raise ValueError('原文收藏空间达到上限，调高容量后可继续，不会删除旧收藏')
        for name, data in [('source.json', raw), ('original.txt', text)]:
            path = directory / name
            path.write_bytes(data)
            path.chmod(0o600)
        self.execute('UPDATE tieba_archives SET payload=?,title=?,text_bytes=? WHERE id=?',
                     (json.dumps(source, ensure_ascii=False), source['title'], len(raw) + len(text), row['id']))
        self.service.tools.index(source)
        for image in source.get('images', []):
            self.execute('''INSERT OR IGNORE INTO tieba_archive_images
                (archive_id,image_id,pid,source_url,post_url,original_available) VALUES(?,?,?,?,?,?)''',
                (row['id'], image['id'], str(image['pid']), image['url'], image['post_url'], bool(image.get('original_available', True))))

    async def download(self, image, *, available, max_bytes=32 * 1024**2):
        # Reuse the same CDN boundary as the existing reader; no credential forwarding or redirects.
        from .tieba import image_url
        if not image_url(image['source_url']):
            raise ValueError('原图地址不可下载')
        data = bytearray()
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            async with client.stream('GET', image['source_url']) as response:
                response.raise_for_status()
                if not response.headers.get('content-type', '').lower().startswith('image/'):
                    raise ValueError('原图接口未返回图片')
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ValueError('单张图片超过32MB，未保存完整，原链接已保留')
                    if len(data) > available:
                        raise ValueError('原文收藏空间达到上限，调高容量后可继续')
        with Image.open(io.BytesIO(data)) as picture:
            mime = {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp', 'GIF': 'image/gif'}.get(picture.format)
        if not mime:
            raise ValueError('图片格式暂不支持，原链接已保留')
        return bytes(data), mime

    def cached_image(self, tid, image_id):
        row = self.store.db.execute('''SELECT a.* FROM tieba_archives a JOIN tieba_archive_images i ON i.archive_id=a.id
            WHERE a.account=? AND a.tid=? AND i.image_id=? AND i.state='saved' ''',
            (self.config.settings.account_id, tid, image_id)).fetchone()
        path = self.directory(row) / (image_id + '.bin') if row else None
        return path.read_bytes() if path and path.is_file() else None

    async def process(self, identity):
        async with self.lock:
            row = self.row(identity)
            if not row or not self.current(row) or row['state'] == 'complete':
                return False
            now = time.time()
            self.execute("UPDATE tieba_archives SET state='saving',attempts=attempts+1,updated=? WHERE id=?", (now, identity))
            try:
                source = json.loads(row['payload'])
                original_source = source
                if source.get('source_version') != 2 or not source.get('coverage', {}).get('main_floor_present'):
                    async with self.service.reader_factory(self.config.tieba_credentials()) as reader:
                        source = await reader.detail(source)
                    if source.get('source_version') != 2:
                        raise ValueError('未读到完整来源，未将预览当作原文')
                if not self.current(row):
                    return False
                if not row['text_bytes'] or source != original_source:
                    await asyncio.to_thread(self.persist_source, row, source)
                for image in self.images(identity):
                    if image['state'] == 'saved':
                        continue
                    if not self.current(row):
                        return False
                    try:
                        available = self.config.settings.tieba_archive_limit_mb * 1024**2 - self.used_bytes()
                        raw, mime = await self.download(image, available=available)
                        if not self.current(row):
                            return False
                        path = self.directory(row) / (image['image_id'] + '.bin')
                        await asyncio.to_thread(path.write_bytes, raw)
                        path.chmod(0o600)
                        self.execute("UPDATE tieba_archive_images SET state='saved',bytes=?,sha256=?,mime=?,error='' WHERE archive_id=? AND image_id=?",
                            (len(raw), hashlib.sha256(raw).hexdigest(), mime, identity, image['image_id']))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        error = str(exc)[:180] if isinstance(exc, ValueError) else '图片下载未完成：' + type(exc).__name__
                        self.execute("UPDATE tieba_archive_images SET state='failed',error=? WHERE archive_id=? AND image_id=?",
                                     (error, identity, image['image_id']))
                images = self.images(identity)
                missing = sum(i['state'] != 'saved' for i in images)
                main_present = source.get('coverage', {}).get('main_floor_present', False)
                error = ('有' + str(missing) + '张图片未保存完整；可稍后继续' if missing else '')
                if not main_present:
                    error = '未读到主楼，已保存实际读取的楼层。' + error
                self.execute('UPDATE tieba_archives SET state=?,error=?,updated=?,retry_at=? WHERE id=?',
                    ('partial' if missing or not main_present else 'complete', error, time.time(), time.time() + 300, identity))
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = str(exc)[:200] if isinstance(exc, ValueError) else '收藏未完成：' + type(exc).__name__
                self.execute("UPDATE tieba_archives SET state='partial',error=?,updated=?,retry_at=? WHERE id=?",
                             (error, time.time(), time.time() + 300, identity))
                return False
            finally:
                self.execute("UPDATE tieba_archives SET state='partial',error='保存中断，将在空闲时继续' WHERE id=? AND state='saving'", (identity,))

    async def resume_once(self):
        if not self.config.settings.tieba_archive_enabled or not self.config.settings.tieba_enabled or native_pressure(self.config):
            return False
        row = self.store.db.execute('''SELECT a.id FROM tieba_archives a LEFT JOIN tieba_lifecycle l
            ON l.account=a.account AND l.tid=a.tid WHERE a.account=? AND state IN ('requested','pending','partial')
            AND coalesce(l.trashed_at,0)=0 AND coalesce(l.deleted_at,0)=0
            AND attempts<3 AND retry_at<=? ORDER BY a.created LIMIT 1''',
            (self.config.settings.account_id, time.time())).fetchone()
        return await self.process(row[0]) if row else False

    def document(self, identity):
        row = self.row(identity)
        if not row:
            raise ValueError('没有这条收藏')
        if self.service.retention.active(row['tid']):
            self.service.retention.used(row['tid'])
        source = json.loads(row.pop('payload'))
        return {**row, 'source': source, 'images': self.images(identity),
                'original_text': original_text(source) if row['text_bytes'] else ''}

    def image_file(self, identity, image_id):
        row = self.row(identity)
        if not row or not re.fullmatch(r'[0-9a-f]{24}', image_id):
            raise ValueError('没有这张收藏图片')
        image = next((i for i in self.images(identity) if i['image_id'] == image_id and i['state'] == 'saved'), None)
        path = self.directory(row) / (image_id + '.bin')
        if not image or not path.is_file():
            raise ValueError('图片尚未保存成功')
        return path, image['mime']
