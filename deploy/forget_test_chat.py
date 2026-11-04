"""Explicit, scoped administrator cleanup. Preview is read-only by default.

Only derived memory is removed. Original message_log/inbox/send ledgers stay.
Apply requires the core to be stopped and a previously reviewed plan digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

GROUP = '56614122712@chatroom'
DATA = Path('/var/lib/whalegirl-linux/core')
BACKUPS = Path('/var/backups/whalegirl')


def select(db, table, condition, args):
    return [dict(r) for r in db.execute(
        'SELECT rowid AS _cleanup_rowid,* FROM "' + table + '" WHERE ' + condition, args)]


def collect(db, account, chat):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    memories = select(db, 'memories', 'account=? AND chat=?', (account, chat))
    ids = {r['id'] for r in memories}
    # Include explicit dependencies only; never infer unrelated records by text.
    while ids:
        children = {r[0] for r in db.execute(
            'SELECT child_id FROM memory_dependencies WHERE parent_id IN (' + ','.join('?' for _ in ids) + ')', tuple(ids))}
        dependent = {i for i in children if not db.execute(
            'SELECT 1 FROM memory_dependencies WHERE child_id=? AND parent_id NOT IN (' + ','.join('?' for _ in ids) + ')',
            (i, *ids)).fetchone()}
        if dependent <= ids:
            break
        ids |= dependent
    selected = {}
    for table in ('memories', 'memory_revisions', 'memory_evidence', 'memory_origins',
                  'memory_suppressions', 'growth_insights'):
        if table not in tables:
            continue
        column = 'id' if table == 'memories' else 'memory_id'
        selected[table] = select(db, table, column + ' IN (' + ','.join('?' for _ in ids) + ')', tuple(ids)) if ids else []
    selected['memory_dependencies'] = select(db, 'memory_dependencies',
        'parent_id IN (' + ','.join('?' for _ in ids) + ') OR child_id IN (' + ','.join('?' for _ in ids) + ')',
        (*ids, *ids)) if ids else []
    learning = select(db, 'learning_proposals', 'account=? AND chat=?', (account, chat))
    proposal_ids = [r['id'] for r in learning]
    selected['learning_proposals'] = learning
    selected['learning_revisions'] = select(db, 'learning_revisions',
        'proposal_id IN (' + ','.join('?' for _ in proposal_ids) + ')', tuple(proposal_ids)) if proposal_ids else []
    for table in ('chat_digests', 'history', 'persona_mood_state', 'persona_group_masks',
                  'autonomy_intents', 'autonomy_events', 'affection_revisions',
                  'chat_style_profile_versions', 'chat_style_profile_state',
                  'chat_style_profile_suppressions', 'decision_tool_effects'):
        if table in tables:
            selected[table] = select(db, table, 'account=? AND chat=?', (account, chat))
    selected['memory_summaries'] = [dict(r) for r in db.execute(
        'SELECT rowid AS _cleanup_rowid,* FROM memory_summaries WHERE account=?', (account,))
        if r['chat'] == chat or ids.intersection(int(i) for i in json.loads(r['members']))]
    selected['memory_work_queue'] = [dict(r) for r in db.execute(
        'SELECT rowid AS _cleanup_rowid,* FROM memory_work_queue')
        if (lambda p: isinstance(p, list) and p and isinstance(p[0], dict)
            and p[0].get('account') == account and p[0].get('chat') == chat)(json.loads(r['payload']))]
    # Person scores are shared. Only remove textual fields still attributable
    # to this chat; independent later updates and numeric affinity are retained.
    profile_updates = []
    for r in selected['memory_origins']:
        if r['source'] != 'profile':
            continue
        memory = next((m for m in selected['memories'] if m['id'] == r['memory_id']), None)
        field = r['origin_id'].rsplit('/', 2)[-2]
        if not memory or field not in ('nickname', 'vibe_tags', 'interaction_rule', 'notable_events'):
            continue
        person = db.execute('SELECT * FROM affections WHERE account=? AND subject=?',
                            (account, memory['subject'])).fetchone()
        if not person:
            continue
        revisions = [dict(x) for x in db.execute('SELECT * FROM affection_revisions WHERE account=? AND subject=? ORDER BY id',
                                                (account, memory['subject']))]
        changes = [x for x in revisions if json.loads(x['before_json']).get(field) != json.loads(x['after_json']).get(field)]
        if not changes or changes[-1]['chat'] != chat:
            continue
        previous = next((json.loads(x['after_json']).get(field) for x in reversed(changes) if x['chat'] != chat), None)
        if previous is None:
            previous = json.loads(changes[0]['before_json']).get(field, '[]' if field == 'vibe_tags' else '')
        if isinstance(previous, list):
            previous = json.dumps(previous, ensure_ascii=False)
        if person[field] != previous:
            profile_updates.append({'account': account, 'subject': memory['subject'], 'field': field,
                                    'before': person[field], 'after': previous})
    latest = db.execute('SELECT max(rowid),max(timestamp) FROM message_log WHERE account=? AND chat=?', (account, chat)).fetchone()
    return selected, profile_updates, {'last_rowid': latest[0] or 0, 'last_time': latest[1] or 0}


def digest(selected, profiles):
    return hashlib.sha256(json.dumps([selected, profiles], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def archive_changes(account, chat):
    archive = DATA / 'memory-archive'
    specific = archive / 'chats' / hashlib.sha256((account + '/' + chat).encode()).hexdigest()[:24] / 'memories.json'
    candidates = [specific] + list((archive / 'people').glob('*/memories.json'))
    changes = []
    for path in candidates:
        if not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(archive.resolve()):
            raise ValueError('Archive path leaves the memory archive')
        before = path.read_text('utf-8')
        value = json.loads(before)
        rows = value.get('memories', [])
        retained = [r for r in rows if r.get('account') != account or r.get('chat') != chat]
        if len(retained) != len(rows):
            changes.append({'path': str(path), 'before': before,
                            'after': {**value, 'memories': retained}})
    return changes


def run(*, apply=False, expected='', output=None):
    settings = json.loads((DATA / 'settings.json').read_text('utf-8'))
    account = settings['account_id']
    if GROUP not in settings['allowed_chat_ids']:
        raise ValueError('Expected authorized test chat is absent')
    if apply and subprocess.run(['systemctl', 'is-active', '--quiet', 'whalegirl-linux']).returncode == 0:
        raise RuntimeError('Stop the core before cleanup; no records changed')
    database = DATA / 'state.sqlite3'
    uri = database.as_uri() + ('?mode=rw' if apply else '?mode=ro')
    with sqlite3.connect(uri, uri=True, timeout=10) as db:
        db.row_factory = sqlite3.Row
        if apply:
            db.execute('BEGIN IMMEDIATE')
        selected, profiles, cursor = collect(db, account, GROUP)
        archives = archive_changes(account, GROUP)
        fingerprint = digest(selected, [*profiles, *archives])
        report = {'chat': GROUP, 'counts': {k: len(v) for k, v in selected.items()},
                  'profile_fields_reset': len(profiles), 'plan_sha256': fingerprint,
                  'archive_files_reset': [a['path'] for a in archives],
                  'preserve_raw_chats': True, 'apply': apply}
        if not apply:
            return report
        if expected != fingerprint:
            raise ValueError('Cleanup scope changed; preview again before applying')
        if output is None or output.resolve().parent != BACKUPS or not output.name.startswith('forget-test-chat-'):
            raise ValueError('Unexpected private backup location')
        output.mkdir(mode=0o700, exist_ok=False)
        backup = output / 'derived-records-before-cleanup.json'
        backup.write_text(json.dumps({'selected': selected, 'profiles': profiles, 'archives': archives, 'report': report}, ensure_ascii=False), 'utf-8')
        backup.chmod(0o600)
        raw_before = {t: db.execute('SELECT count(*) FROM "' + t + '" WHERE account=? AND chat=?',
                                   (account, GROUP)).fetchone()[0] for t in ('inbox', 'message_log', 'outbound_actions')}
        others_before = {t: db.execute('SELECT count(*) FROM "' + t + '" WHERE account!=? OR chat!=?',
                                      (account, GROUP)).fetchone()[0] for t in ('memories', 'learning_proposals', 'history')}
        for table, rows in selected.items():
            for row in rows:
                db.execute('DELETE FROM "' + table + '" WHERE rowid=?', (row['_cleanup_rowid'],))
        now = time.time()
        for item in profiles:
            db.execute('UPDATE affections SET "' + item['field'] + '"=?,version=version+1,updated=? WHERE account=? AND subject=?',
                       (item['after'], now, account, item['subject']))
        # An empty digest starts after the old material. Forgetting never needs
        # to delete the original chat or disable future learning in this chat.
        latest = db.execute('SELECT timestamp,local_id FROM message_log WHERE account=? AND chat=? ORDER BY timestamp DESC,local_id DESC LIMIT 1',
                            (account, GROUP)).fetchone()
        if latest:
            db.execute('INSERT INTO chat_digests VALUES(?,?,?,?,?,?)',
                       (account, GROUP, '', latest['timestamp'], latest['local_id'], now))
        db.execute('UPDATE autonomy_chats SET cursor=?,growth_at=?,last_considered=?,last_anchor=? WHERE account=? AND chat=?',
                   (cursor['last_rowid'], now, now, '', account, GROUP))
        db.execute('INSERT OR REPLACE INTO memory_hub_meta(key,value) VALUES(?,?)',
                   ('forget_before:' + account + ':' + GROUP, str(max(now, cursor['last_time']))))
        for table, count in raw_before.items():
            if db.execute('SELECT count(*) FROM "' + table + '" WHERE account=? AND chat=?', (account, GROUP)).fetchone()[0] != count:
                raise RuntimeError('Raw chat/send records changed')
        for table, count in others_before.items():
            if db.execute('SELECT count(*) FROM "' + table + '" WHERE account!=? OR chat!=?', (account, GROUP)).fetchone()[0] != count:
                raise RuntimeError('Another chat was affected')
        db.commit()
        for item in archives:
            path = Path(item['path']); owner = path.stat()
            pending = path.with_name('memories-forget-test-chat.pending')
            pending.write_text(json.dumps({**item['after'], 'updated': now}, ensure_ascii=False, indent=2), 'utf-8')
            pending.chmod(0o600); os.chown(pending, owner.st_uid, owner.st_gid); os.replace(pending, path)
        report.update(backup=str(output), raw_counts_preserved=raw_before, other_chat_counts_preserved=others_before,
                      forgotten_before=max(now, cursor['last_time']))
        report_path = output / 'cleanup-report.json'
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
        report_path.chmod(0o600)
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected', default='')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(apply=args.apply, expected=args.expected, output=args.output), ensure_ascii=False))
