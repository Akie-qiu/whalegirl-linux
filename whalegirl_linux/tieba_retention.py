"""Idle source retention with a reversible grace period and a narrow file boundary."""
import json
from pathlib import Path
import re
import time


class TiebaRetention:
    def __init__(self, service):
        self.service, self.store, self.config = service, service.store, service.config
        self.last_check = 0
        with self.store.lock:
            self.store.db.executescript('''
                CREATE TABLE IF NOT EXISTS tieba_lifecycle(
                    account TEXT,tid TEXT,created REAL NOT NULL,last_used REAL NOT NULL DEFAULT 0,
                    uses INTEGER NOT NULL DEFAULT 0,pinned INTEGER NOT NULL DEFAULT 0,
                    trashed_at REAL NOT NULL DEFAULT 0,deleted_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(account,tid));
                INSERT OR IGNORE INTO tieba_lifecycle(account,tid,created,pinned)
                    SELECT account,tid,created,reason='管理员手动收藏' FROM tieba_archives;
                INSERT OR IGNORE INTO tieba_lifecycle(account,tid,created)
                    SELECT account,tid,seen FROM tieba_posts;
                CREATE TABLE IF NOT EXISTS tieba_cleanup_log(
                    id INTEGER PRIMARY KEY,created REAL NOT NULL,account TEXT,tid TEXT,operation TEXT,
                    files INTEGER NOT NULL DEFAULT 0,bytes INTEGER NOT NULL DEFAULT 0,plan_json TEXT NOT NULL DEFAULT '[]');
            ''')
            self.store.db.commit()

    def execute(self, sql, args=()):
        return self.service.execute(sql, args)

    def add(self, tid, created=None):
        self.execute('INSERT OR IGNORE INTO tieba_lifecycle(account,tid,created) VALUES(?,?,?)',
                     (self.config.settings.account_id, str(tid), time.time() if created is None else created))

    def active(self, tid):
        row = self.store.db.execute('SELECT trashed_at,deleted_at FROM tieba_lifecycle WHERE account=? AND tid=?',
                                    (self.config.settings.account_id, str(tid))).fetchone()
        return not row or not (row['trashed_at'] or row['deleted_at'])

    def used(self, tid):
        self.add(tid)
        self.execute('UPDATE tieba_lifecycle SET last_used=?,uses=uses+1 WHERE account=? AND tid=? AND deleted_at=0',
                     (time.time(), self.config.settings.account_id, str(tid)))

    def protected(self, tid):
        s = self.config.settings
        if (self.service.busy or self.service.archives.lock.locked() or self.service.runtime.processing
                or self.service.context_busy or self.service.enrich_busy
                or getattr(getattr(self.service.runtime, 'proactive', None), 'busy', False)):
            return True
        return bool(self.store.db.execute("SELECT 1 FROM tieba_shares WHERE account=? AND tid=? AND state IN ('planned','queued','sending','uncertain')",
                                          (s.account_id, tid)).fetchone())

    def edit(self, tid, op):
        row = self.store.db.execute('SELECT * FROM tieba_lifecycle WHERE account=? AND tid=?',
                                    (self.config.settings.account_id, tid)).fetchone()
        if not row:
            raise ValueError('没有这条原文记录')
        if row['deleted_at']:
            raise ValueError('已永久清理，不能恢复原文')
        if op not in ('pin', 'unpin', 'restore'):
            raise ValueError('未知收藏操作')
        fields = 'pinned=1,trashed_at=0' if op == 'pin' else 'pinned=0' if op == 'unpin' else 'trashed_at=0,last_used='+str(time.time())
        self.execute('UPDATE tieba_lifecycle SET '+fields+' WHERE account=? AND tid=?', (row['account'], tid))
        return {'ok': True}

    def candidates(self, now=None):
        now = time.time() if now is None else now
        s = self.config.settings
        if not s.tieba_unused_days:
            return []
        return [dict(r) for r in self.store.db.execute('''SELECT * FROM tieba_lifecycle
            WHERE account=? AND pinned=0 AND deleted_at=0 AND
            ((trashed_at=0 AND max(created,last_used)<=?) OR (trashed_at>0 AND trashed_at<=?))
            ORDER BY max(created,last_used),tid LIMIT 100''',
            (s.account_id, now-s.tieba_unused_days*86400, now-s.tieba_trash_days*86400))]

    def file_plan(self, row):
        archive = self.service.archives
        doc = self.store.db.execute('SELECT * FROM tieba_archives WHERE account=? AND tid=?',
                                    (row['account'], row['tid'])).fetchone()
        if not doc:
            return None, []
        directory = archive.directory(doc)
        root = archive.root.resolve()
        if (not re.fullmatch(r'[0-9a-f]{32}', doc['id']) or archive.root.is_symlink()
                or directory.parent.is_symlink() or directory.is_symlink()
                or not directory.resolve().is_relative_to(root)):
            raise ValueError('收藏目录越界，未清理')
        files = list(directory.iterdir()) if directory.exists() else []
        if any(p.is_symlink() or not p.is_file() or not re.fullmatch(r'(?:source\.json|original\.txt|[0-9a-f]{24}\.bin)', p.name)
               for p in files):
            raise ValueError('收藏目录存在未知文件，未清理')
        return directory, files

    def cleanup_once(self, now=None):
        now = time.time() if now is None else now
        if self.last_check and now-self.last_check < 3600:
            return
        self.last_check = now
        for row in self.candidates(now):
            if self.protected(row['tid']):
                continue
            if not row['trashed_at']:
                self.execute('UPDATE tieba_lifecycle SET trashed_at=? WHERE account=? AND tid=? AND pinned=0',
                             (now, row['account'], row['tid']))
                self.execute('INSERT INTO tieba_cleanup_log(created,account,tid,operation) VALUES(?,?,?,?)',
                             (now, row['account'], row['tid'], 'trash'))
                continue
            # Review the complete exact file list before deleting any bytes. Unknown files block cleanup.
            try:
                directory, files = self.file_plan(row)
            except ValueError:
                continue
            size = sum(p.stat().st_size for p in files)
            # The log is the administrator-visible deletion report, persisted before filesystem mutation.
            plan = [str(p.relative_to(self.service.archives.root)) for p in files]
            self.execute('INSERT INTO tieba_cleanup_log(created,account,tid,operation,files,bytes,plan_json) VALUES(?,?,?,?,?,?,?)',
                         (now, row['account'], row['tid'], 'purge_planned', len(files), size, json.dumps(plan)))
            for path in files:
                path.unlink(missing_ok=True)
            if directory and directory.exists():
                directory.rmdir()
            with self.store.lock:
                args = (row['account'], row['tid'])
                db = self.store.db
                for table in ('tieba_meme_details', 'tieba_meme_sources', 'tieba_meme_enrichment', 'tieba_meme_usage'):
                    db.execute('DELETE FROM '+table+' WHERE meme_id IN (SELECT id FROM tieba_memes WHERE account=? AND tid=?)', args)
                db.execute('DELETE FROM tieba_archive_images WHERE archive_id IN (SELECT id FROM tieba_archives WHERE account=? AND tid=?)', args)
                for table in ('tieba_memes','tieba_archives','tieba_posts','tieba_post_quotes','tieba_post_interests','tieba_search_docs'):
                    db.execute('DELETE FROM '+table+' WHERE account=? AND tid=?', args)
                db.execute('UPDATE tieba_lifecycle SET deleted_at=? WHERE account=? AND tid=?', (now, *args))
                db.execute('INSERT INTO tieba_cleanup_log(created,account,tid,operation,files,bytes) VALUES(?,?,?,?,?,?)',
                           (now, *args, 'purged', len(files), size))
                db.commit()

    def snapshot(self):
        account = self.config.settings.account_id
        rows = [dict(r) for r in self.store.db.execute('''SELECT l.*,a.id AS archive_id,a.title
            FROM tieba_lifecycle l LEFT JOIN tieba_archives a ON a.account=l.account AND a.tid=l.tid
            WHERE l.account=? AND l.trashed_at>0 AND l.deleted_at=0 ORDER BY l.trashed_at DESC LIMIT 50''', (account,))]
        return {'unused_days': self.config.settings.tieba_unused_days, 'trash_days': self.config.settings.tieba_trash_days,
                'trash': rows, 'next_due_count': len(self.candidates()),
                'events': [dict(r) for r in self.store.db.execute('SELECT * FROM tieba_cleanup_log WHERE account=? ORDER BY id DESC LIMIT 20', (account,))]}
