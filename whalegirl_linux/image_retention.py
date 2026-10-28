"""Low-frequency library retention; leave reference images and post archives intact."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time

SOURCES = ('uploaded', 'generated', 'tieba')


class ImageRetention:
    def __init__(self, images):
        self.images = images
        self.store, self.config = images.store, images.config
        self.next_scan = 0
        self.last_scan = {}
        with self.store.lock:
            columns = {r[1] for r in self.store.db.execute('PRAGMA table_info(picture_assets)')}
            for name, spec in (
                ('deleted_at', 'REAL NOT NULL DEFAULT 0'),
                ('retention_exempt', 'INTEGER NOT NULL DEFAULT 0'),
            ):
                if name not in columns:
                    self.store.db.execute('ALTER TABLE picture_assets ADD COLUMN '+name+' '+spec)
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS picture_deletions(
                    id INTEGER PRIMARY KEY,account TEXT NOT NULL,asset_id TEXT NOT NULL,
                    source TEXT NOT NULL,name TEXT NOT NULL,reason TEXT NOT NULL,created REAL NOT NULL,
                    bytes_removed INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'pending',
                    error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS picture_retention_due
                    ON picture_assets(account,source,deleted_at,created);
            ''')
            self.store.db.commit()

    def files(self, asset):
        """Only these two exact files are owned by the library. Never recurse."""
        if asset['source'] not in SOURCES or not re.fullmatch(r'[0-9a-f]{64}', asset['id']):
            raise ValueError('图片路径无效，未删除文件')
        paths = [(self.images.root, self.images.path(asset))]
        native = getattr(self.images.runtime.gateway, 'ui_jobs_directory', None)
        if native:
            paths.append((Path(native), Path(native)/'moments-assets'/(asset['id']+'.png')))
        result = []
        for root, path in paths:
            root = root.resolve()
            # Reject symlinks, including a symlinked source/staging directory.
            if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('图片路径指向图库之外，未删除文件')
            if path.exists() and not path.is_file():
                raise ValueError('图片路径不是普通文件，未删除')
            result.append(path)
        return result

    def protection(self, asset):
        if asset['state'] == 'tagging':
            return '图片正在AI整理，请稍后再删除'
        if asset['id'] in self.config.settings.moments_asset_ids:
            return '图片是朋友圈固定配图，请先从固定配图中移除'
        runtime = self.images.runtime
        # Moments staging and draft updates run inside this existing lock.
        if getattr(getattr(runtime, 'moments', None), 'lock', None) and runtime.moments.lock.locked():
            return '朋友圈正在处理配图，请稍后再删除'
        if self.store.db.execute("""SELECT 1 FROM picture_jobs WHERE account=? AND asset_id=?
                AND state IN ('queued','generating','ready','sending','uncertain') LIMIT 1""",
                (asset['account'], asset['id'])).fetchone():
            return '图片有待处理或待核实任务，请先取消任务或核实发送结果'
        if self.store.db.execute("""SELECT 1 FROM picture_jobs j,json_each(j.reference_json,'$.inputs') r
                WHERE j.account=? AND j.state IN ('queued','generating','ready','sending','uncertain')
                AND json_extract(r.value,'$.asset_id')=? LIMIT 1""",
                (asset['account'],asset['id'])).fetchone():
            return '图片被待处理生图任务作为角色参考，请先处理该任务'
        for row in self.store.db.execute("""SELECT payload FROM moments_actions WHERE account=?
                AND state NOT IN ('sent','cancelled','skipped')""", (asset['account'],)):
            payload = json.loads(row[0])
            if asset['id'] in payload.get('assets', []):
                return '图片被朋友圈草稿或待核实发布引用，请先移除配图或处理该任务'
        return ''

    def plan(self, identity):
        with self.store.lock:
            asset = self.images.asset(identity)
            if not asset:
                raise ValueError('图片不存在或已经删除')
            files = self.files(asset)
            return dict(id=asset['id'], name=asset['name'], source=asset['source'],
                        files=[p.name for p in files if p.exists()],
                        bytes=sum(p.stat().st_size for p in files if p.exists()),
                        blocked=self.protection(asset))

    def delete(self, identity, *, reason='admin', now=None):
        now = time.time() if now is None else now
        with self.store.lock:
            asset = self.images.asset(identity)
            if not asset:
                raise ValueError('图片不存在或已经删除')
            if reason == 'expired':
                days = getattr(self.config.settings, 'image_retention_'+asset['source']+'_days', 0)
                if not days or asset['retention_exempt'] or asset['created'] > now-days*86400:
                    raise ValueError('保留设置已变化，跳过该图片')
            blocked = self.protection(asset)
            if blocked:
                raise ValueError(blocked)
            self.files(asset)  # Validate every target before changing any state or file.
            self.store.db.execute("UPDATE picture_assets SET deleted_at=?,enabled=0,state='deleted' WHERE id=? AND account=?",
                                  (now, identity, asset['account']))
            audit = self.store.db.execute("""INSERT INTO picture_deletions
                (account,asset_id,source,name,reason,created) VALUES(?,?,?,?,?,?)""",
                (asset['account'], identity, asset['source'], asset['name'], reason, now)).lastrowid
            self.store.db.commit()
            # Tombstone first: a restart cannot re-import this asset or choose it for sending.
            self.images.sync_authority()
            return self.finish(audit, asset)

    def finish(self, audit, asset):
        with self.store.lock:
            current = self.store.db.execute('SELECT deleted_at FROM picture_assets WHERE id=? AND account=?',
                                           (asset['id'], asset['account'])).fetchone()
            record = self.store.db.execute('SELECT state FROM picture_deletions WHERE id=?', (audit,)).fetchone()
            if not current or not current[0] or not record or record[0] != 'pending':
                return dict(id=asset['id'], state='superseded', bytes_removed=0, error='')
            removed = 0
            try:
                for path in self.files(asset):
                    if path.is_file():
                        size = path.stat().st_size
                        path.unlink()
                        removed += size
                state, error = 'deleted', ''
            except (OSError, ValueError):
                state, error = 'pending', '图片已从图库移除，文件清理未完成，将在下一轮重试'
            self.store.db.execute("""UPDATE picture_deletions
                SET bytes_removed=bytes_removed+?,state=?,error=? WHERE id=?""", (removed, state, error, audit))
            self.store.db.commit()
            return dict(id=asset['id'], state=state, bytes_removed=removed, error=error)

    def sweep(self, now=None):
        now = time.time() if now is None else now
        account = self.config.settings.account_id
        result = dict(at=now, deleted=0, skipped=0, bytes_removed=0)
        # Finish only tombstoned files; an explicit re-upload must never be erased.
        with self.store.lock:
            pending = [dict(r) for r in self.store.db.execute("""SELECT a.*,d.id AS audit
                FROM picture_deletions d JOIN picture_assets a ON a.id=d.asset_id AND a.account=d.account
                WHERE d.account=? AND d.state='pending' AND a.deleted_at>0 LIMIT 100""", (account,))]
        for asset in pending:
            item = self.finish(asset['audit'], asset)
            result['bytes_removed'] += item['bytes_removed']
        for source in SOURCES:
            days = getattr(self.config.settings, 'image_retention_'+source+'_days')
            if not days:
                continue
            with self.store.lock:
                ids = [r[0] for r in self.store.db.execute("""SELECT id FROM picture_assets
                    WHERE account=? AND source=? AND deleted_at=0 AND retention_exempt=0 AND created<=?
                    ORDER BY created LIMIT 100""", (account, source, now-days*86400))]
            for identity in ids:
                try:
                    item = self.delete(identity, reason='expired', now=now)
                    result['deleted'] += 1
                    result['bytes_removed'] += item['bytes_removed']
                except (ValueError, OSError):
                    result['skipped'] += 1
        self.last_scan = result
        return result

    def snapshot(self):
        rows = self.store.db.execute("""SELECT asset_id,name,source,reason,created,bytes_removed,state,error
            FROM picture_deletions WHERE account=? ORDER BY id DESC LIMIT 20""",
            (self.config.settings.account_id,)).fetchall()
        return {'last_scan': self.last_scan, 'interval_minutes': 60,
                'recent': [dict(row) for row in rows]}
