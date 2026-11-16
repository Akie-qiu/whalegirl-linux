#!/usr/bin/env python3
"""Audit, online-backup, and clean pending learning_proposals & memories safely."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

CORE_SECRET_INJECT_RE = re.compile(
    r'sk-[a-zA-Z0-9_-]{12,}|密码|密钥|私钥|token\s*[:=]|BEGIN .*PRIVATE KEY|'
    r'忽略.{0,10}(?:指令|规则|设定)|系统提示词|system_prompt|每句话?(?:结尾|末尾|后面)(?:必须|都要|都加)',
    re.I,
)
HIJACK_RE = re.compile(
    r'叫我(?:主人|爸爸|爷爷|妈妈|爹)|我是你(?:主人|爹|爸爸)|认主|奴隶|服从命令|'
    r'认脸|喜欢认脸|(?<![承自默确否认])认[你我]',
    re.I,
)
DEFENSIVE_WARNING_RE = re.compile(r'别当真|别据此改认|不要据此改认')


def is_poison_entry(text: str, *, is_owner: bool = False, guardrail_reason: str = '') -> bool:
    if guardrail_reason:
        return True
    if CORE_SECRET_INJECT_RE.search(text):
        return True
    if is_owner or DEFENSIVE_WARNING_RE.search(text):
        return False
    return bool(HIJACK_RE.search(text))


PLACEHOLDER_RE = re.compile(r'^(?:暂无|无|test|测试|none|null|n/a|\{\}|\[\])$', re.I)
GENERIC_ALIAS_RE = re.compile(r'^(?:机器人|群友|用户|管理员|大家|所有人|助手|ai)$', re.I)

PROPOSAL_TO_MEMORY_STATUS = {
    'accepted': 'active',
    'pending': 'pending',
    'revoked': 'forgotten',
    'rejected': 'rejected',
    'expired': 'expired',
    'blocked': 'blocked',
}
STALE_CHAT_KINDS = {'slang', 'meme_usage', 'chat_style', 'expression', 'tool_learning', 'persona', 'relationship'}
DEFAULT_TIER_MAP = {
    'fact': 'stable',
    'preference': 'stable',
    'nickname': 'stable',
    'interaction_rule': 'stable',
    'chat_style': 'stable',
    'bot_alias': 'stable',
}


def is_junk_text(text: str) -> bool:
    clean = str(text or '').strip()
    if len(clean) < 2 or '\ufffd' in clean:
        return True
    if PLACEHOLDER_RE.fullmatch(clean):
        return True
    if re.fullmatch(r'[\W_]+', clean):
        return True
    return False


def _distinct_user_count(raw_users) -> int:
    if isinstance(raw_users, list):
        return len({str(v).strip() for v in raw_users if str(v).strip()})
    try:
        parsed = json.loads(raw_users or '[]')
        if isinstance(parsed, list):
            return len({str(v).strip() for v in parsed if str(v).strip()})
    except ValueError:
        pass
    return 0


def classify_proposal(
    row: dict,
    now: float,
    *,
    owner_ids: set[str] | None = None,
    min_profile_conf: float = 0.0,
    keep_owner_dm: bool = False,
    keep_chat_knowledge: bool = False,
    exclude_proposal_ids: set[int] | None = None,
) -> tuple[str, str]:
    """Return (target_state, reason_tag) for a pending learning_proposals row."""
    kind = str(row.get('kind') or '').strip()
    scope = str(row.get('scope') or 'chat').strip()
    chat = str(row.get('chat') or '').strip()
    content = str(row.get('content') or '').strip()
    evidence = str(row.get('evidence') or '').strip()
    alias_target = str(row.get('alias_target') or '').strip()
    subject = str(row.get('subject') or '').strip()
    conf = float(row.get('confidence') or 0)
    expires_at = float(row.get('expires_at') or 0)
    guardrail_reason = str(row.get('guardrail_reason') or '').strip()
    owners = owner_ids or set()
    is_owner = bool(owners and (chat in owners or subject in owners))

    combined = f'{alias_target} {content} {evidence}'
    if is_poison_entry(combined, is_owner=is_owner, guardrail_reason=guardrail_reason):
        return 'blocked', 'poison_or_guardrail'
    if is_junk_text(content):
        return 'rejected', 'junk_or_empty'
    if expires_at > 0 and expires_at <= now:
        return 'expired', 'ttl_expired'
    if exclude_proposal_ids and int(row.get('id') or 0) in exclude_proposal_ids:
        return 'expired', 'manual_excluded'

    if kind == 'bot_alias':
        alias = alias_target or subject
        if 2 <= len(alias) <= 16 and not GENERIC_ALIAS_RE.fullmatch(alias) and not is_poison_entry(alias, is_owner=is_owner) and (is_owner or conf >= min_profile_conf):
            return 'accepted', 'valid_bot_alias'
        return 'rejected', 'invalid_bot_alias'

    if scope == 'profile' and kind in ('fact', 'preference'):
        if not subject or subject.endswith('@chatroom') or subject in ('self', ''):
            return 'rejected', 'invalid_profile_subject'
        if conf >= min_profile_conf:
            return 'accepted', 'valid_profile'
        return 'rejected', 'low_conf_profile'

    if keep_owner_dm and owners and chat in owners and kind not in ('slang', 'meme_usage'):
        return 'accepted', f'valid_owner_dm_{kind}'

    if keep_chat_knowledge and kind in ('fact', 'preference', 'relationship', 'chat_style', 'expression', 'tool_learning', 'persona'):
        return 'accepted', f'valid_chat_{kind}'

    if kind in ('slang', 'meme_usage'):
        users = _distinct_user_count(row.get('distinct_users'))
        occ = int(row.get('occurrences') or 1)
        if users >= 2 and occ >= 2 and conf >= 0.85:
            return 'accepted', 'multi_user_consensus'
        return 'expired', f'stale_pending_{kind}'

    if kind in ('fact', 'preference') and scope == 'chat':
        return 'expired', f'stale_chat_{kind}'

    if kind in STALE_CHAT_KINDS:
        return 'expired', f'stale_pending_{kind}'

    return 'expired', 'stale_pending_other'


def classify_standalone_memory(row: dict, now: float, *, owner_ids: set[str] | None = None, min_profile_conf: float = 0.0) -> tuple[str, str]:
    """Return (target_status, reason_tag) for an unlinked pending memories row."""
    kind = str(row.get('kind') or '').strip()
    scope = str(row.get('scope') or 'chat').strip()
    chat = str(row.get('chat') or '').strip()
    content = str(row.get('content') or '').strip()
    subject = str(row.get('subject') or '').strip()
    actor = str(row.get('actor') or '').strip()
    source_class = str(row.get('source_class') or '').strip()
    conf = float(row.get('confidence') or 0)
    expires_at = float(row.get('expires_at') or 0)
    owners = owner_ids or set()
    is_owner = bool(owners and (chat in owners or subject in owners))

    if is_poison_entry(content, is_owner=is_owner):
        return 'blocked', 'poison'
    if is_junk_text(content):
        return 'rejected', 'junk_or_empty'
    if expires_at > 0 and expires_at <= now:
        return 'expired', 'ttl_expired'

    # Strip "label：" prefix when checking profile migration cards.
    body_after_colon = content.split('：', 1)[1].strip() if '：' in content else content
    if source_class == 'profile' and not is_junk_text(body_after_colon) and subject and not subject.endswith('@chatroom'):
        return 'active', 'valid_profile_card'

    if scope == 'profile' and kind in ('fact', 'preference', 'impression', 'nickname', 'interaction_rule'):
        if subject and not subject.endswith('@chatroom') and (actor in ('admin', 'migration') or conf >= min_profile_conf):
            return 'active', 'valid_profile_memory'
        return 'rejected', 'low_conf_or_invalid_profile'

    return 'expired', f'stale_pending_memory_{kind}'


def backup_database(db_path: Path, backup_dir: Path | None = None) -> Path:
    """Create a consistent timestamped SQLite Online Backup and verify integrity."""
    if not db_path.is_file():
        raise FileNotFoundError(f'数据库不存在: {db_path}')
    target_dir = backup_dir or (db_path.parent / 'backups')
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = target_dir / f'{db_path.stem}.backup.{stamp}.{int(time.time())}.sqlite3'
    src = sqlite3.connect(f'{db_path.resolve().as_uri()}?mode=ro', uri=True, timeout=30)
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
            check = dst.execute('PRAGMA integrity_check').fetchone()
            if not check or check[0] != 'ok':
                raise RuntimeError(f'备份完整性校验失败: {check}')
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def build_plan(
    conn: sqlite3.Connection,
    *,
    now: float | None = None,
    owner_ids: set[str] | None = None,
    min_profile_conf: float = 0.0,
    keep_owner_dm: bool = False,
    keep_chat_knowledge: bool = False,
    exclude_proposal_ids: set[int] | None = None,
) -> dict:
    now = time.time() if now is None else float(now)
    owners = set(owner_ids or ())
    excluded = {int(x) for x in (exclude_proposal_ids or ())}
    conn.row_factory = sqlite3.Row
    tables = _table_names(conn)

    proposals_before = {}
    if 'learning_proposals' in tables:
        proposals_before = {
            r['state']: r['n']
            for r in conn.execute('SELECT state, count(*) AS n FROM learning_proposals GROUP BY state')
        }
    memories_before = {}
    if 'memories' in tables:
        memories_before = {
            r['status']: r['n']
            for r in conn.execute('SELECT status, count(*) AS n FROM memories GROUP BY status')
        }

    proposal_actions = []
    activated_items = []
    rejected_or_expired_samples = []
    reason_counts = Counter()
    linked_memory_ids = set()
    drift_fixes = []

    if 'learning_proposals' in tables:
        origin_by_prop = {}
        if 'memory_origins' in tables:
            for o in conn.execute("SELECT * FROM memory_origins WHERE source='learning'").fetchall():
                origin_by_prop[(o['account'], str(o['origin_id']))] = dict(o)
                linked_memory_ids.add(int(o['memory_id']))

        for raw in conn.execute("SELECT * FROM learning_proposals ORDER BY id").fetchall():
            row = dict(raw)
            origin = origin_by_prop.get((row['account'], str(row['id'])))
            linked_mem = None
            if origin and 'memories' in tables:
                m = conn.execute('SELECT * FROM memories WHERE id=?', (origin['memory_id'],)).fetchone()
                linked_mem = dict(m) if m else None

            if row['state'] == 'pending':
                target_state, reason = classify_proposal(
                    row,
                    now,
                    owner_ids=owners,
                    min_profile_conf=min_profile_conf,
                    keep_owner_dm=keep_owner_dm,
                    keep_chat_knowledge=keep_chat_knowledge,
                    exclude_proposal_ids=excluded,
                )
                target_mem_status = PROPOSAL_TO_MEMORY_STATUS[target_state]
                reason_counts[f'proposal:{target_state}:{reason}'] += 1
                proposal_actions.append({
                    'id': row['id'],
                    'account': row['account'],
                    'chat': row['chat'],
                    'subject': row['subject'],
                    'kind': row['kind'],
                    'scope': row['scope'],
                    'confidence': row['confidence'],
                    'content': row['content'],
                    'from_state': row['state'],
                    'to_state': target_state,
                    'to_memory_status': target_mem_status,
                    'reason': reason,
                    'linked_memory_id': linked_mem['id'] if linked_mem else None,
                    'row': row,
                })
                summary_item = {
                    'source': 'learning_proposal',
                    'id': row['id'],
                    'kind': row['kind'],
                    'scope': row['scope'],
                    'chat': row['chat'],
                    'subject': row['subject'],
                    'confidence': row['confidence'],
                    'action': f"{row['state']}->{target_state}",
                    'reason': reason,
                    'content': row['content'][:160],
                }
                if target_state == 'accepted':
                    activated_items.append(summary_item)
                elif len(rejected_or_expired_samples) < 40 or reason in ('poison_or_guardrail', 'junk_or_empty', 'low_conf_profile'):
                    rejected_or_expired_samples.append(summary_item)
            elif linked_mem:
                expected_mem = PROPOSAL_TO_MEMORY_STATUS.get(row['state'])
                if expected_mem and linked_mem['status'] != expected_mem and linked_mem['status'] == 'pending':
                    drift_fixes.append({
                        'proposal_id': row['id'],
                        'memory_id': linked_mem['id'],
                        'proposal_state': row['state'],
                        'memory_status': linked_mem['status'],
                        'target_status': expected_mem,
                    })
                    reason_counts[f'drift_fix:{row["state"]}'] += 1

    memory_actions = []
    if 'memories' in tables:
        for raw in conn.execute("SELECT * FROM memories WHERE status='pending' ORDER BY id").fetchall():
            row = dict(raw)
            if int(row['id']) in linked_memory_ids:
                continue
            target_status, reason = classify_standalone_memory(row, now, owner_ids=owners, min_profile_conf=min_profile_conf)
            reason_counts[f'memory:{target_status}:{reason}'] += 1
            memory_actions.append({
                'id': row['id'],
                'account': row['account'],
                'chat': row['chat'],
                'subject': row['subject'],
                'kind': row['kind'],
                'scope': row['scope'],
                'source_class': row.get('source_class', ''),
                'confidence': row.get('confidence', 1.0),
                'content': row['content'],
                'from_status': row['status'],
                'to_status': target_status,
                'reason': reason,
                'row': row,
            })
            summary_item = {
                'source': 'memory',
                'id': row['id'],
                'kind': row['kind'],
                'scope': row['scope'],
                'chat': row['chat'],
                'subject': row['subject'],
                'source_class': row.get('source_class', ''),
                'action': f"pending->{target_status}",
                'reason': reason,
                'content': row['content'][:160],
            }
            if target_status == 'active':
                activated_items.append(summary_item)
            elif len(rejected_or_expired_samples) < 60 or row['scope'] == 'profile':
                rejected_or_expired_samples.append(summary_item)

    projected_proposals = dict(proposals_before)
    for item in proposal_actions:
        projected_proposals['pending'] = max(0, projected_proposals.get('pending', 0) - 1)
        projected_proposals[item['to_state']] = projected_proposals.get(item['to_state'], 0) + 1

    projected_memories = dict(memories_before)
    for item in proposal_actions:
        if item['linked_memory_id'] is not None:
            projected_memories['pending'] = max(0, projected_memories.get('pending', 0) - 1)
            projected_memories[item['to_memory_status']] = projected_memories.get(item['to_memory_status'], 0) + 1
    for item in drift_fixes:
        projected_memories['pending'] = max(0, projected_memories.get('pending', 0) - 1)
        projected_memories[item['target_status']] = projected_memories.get(item['target_status'], 0) + 1
    for item in memory_actions:
        projected_memories['pending'] = max(0, projected_memories.get('pending', 0) - 1)
        projected_memories[item['to_status']] = projected_memories.get(item['to_status'], 0) + 1

    return {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'proposals_before': proposals_before,
        'memories_before': memories_before,
        'proposal_actions': proposal_actions,
        'memory_actions': memory_actions,
        'drift_fixes': drift_fixes,
        'reason_counts': dict(sorted(reason_counts.items())),
        'activated_items': activated_items,
        'rejected_or_expired_samples': rejected_or_expired_samples,
        'projected_proposals': projected_proposals,
        'projected_memories': projected_memories,
    }


def apply_plan(conn: sqlite3.Connection, plan: dict, *, now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    tables = _table_names(conn)
    mem_cols = {r[1] for r in conn.execute('PRAGMA table_info(memories)')} if 'memories' in tables else set()

    applied = {'proposals': 0, 'memories_synced': 0, 'standalone_memories': 0, 'drift_fixes': 0}
    with conn:
        for item in plan['proposal_actions']:
            old = dict(item['row'])
            new_version = int(old['version']) + 1
            new_state = item['to_state']
            guard_reason = old.get('guardrail_reason', '')
            if new_state == 'blocked' and not guard_reason:
                guard_reason = '历史待审洗库拦截（违规或带偏风险）'
            updated_prop = {**old, 'state': new_state, 'guardrail_reason': guard_reason, 'updated': now, 'version': new_version}
            conn.execute(
                'UPDATE learning_proposals SET state=?, guardrail_reason=?, updated=?, version=? WHERE id=?',
                (new_state, guard_reason, now, new_version, old['id']),
            )
            if 'learning_revisions' in tables:
                op = {'accepted': 'wash_accept', 'rejected': 'wash_reject', 'blocked': 'wash_block'}.get(new_state, 'wash_expire')
                conn.execute(
                    '''INSERT INTO learning_revisions(proposal_id,account,actor,operation,before_json,after_json,created)
                       VALUES(?,?,'maintenance',?,?,?,?)''',
                    (old['id'], old['account'], op, json.dumps(old, ensure_ascii=False), json.dumps(updated_prop, ensure_ascii=False), now),
                )
            applied['proposals'] += 1

            mem_status = item['to_memory_status']
            if 'memories' in tables:
                mem_id = item['linked_memory_id']
                if mem_id is not None:
                    before_m = dict(conn.execute('SELECT * FROM memories WHERE id=?', (mem_id,)).fetchone())
                    after_m = {**before_m, 'status': mem_status, 'archived': 0, 'updated': now, 'version': int(before_m['version']) + 1}
                    conn.execute(
                        'UPDATE memories SET status=?, archived=0, updated=?, version=? WHERE id=?',
                        (mem_status, now, after_m['version'], mem_id),
                    )
                    if 'memory_revisions' in tables:
                        conn.execute(
                            '''INSERT INTO memory_revisions(memory_id,account,actor,operation,before_json,after_json,created)
                               VALUES(?,?,'maintenance','wash_sync_learning',?,?,?)''',
                            (mem_id, old['account'], json.dumps(before_m, ensure_ascii=False), json.dumps(after_m, ensure_ascii=False), now),
                        )
                    if 'memory_origins' in tables:
                        conn.execute(
                            "UPDATE memory_origins SET origin_version=? WHERE account=? AND source='learning' AND origin_id=?",
                            (new_version, old['account'], str(old['id'])),
                        )
                    if mem_status == 'active' and 'memory_suppressions' in tables:
                        conn.execute('UPDATE memory_suppressions SET active=0 WHERE memory_id=?', (mem_id,))
                    applied['memories_synced'] += 1
                elif new_state == 'accepted' and {'tier', 'status', 'confidence', 'source_class', 'venue'}.issubset(mem_cols):
                    chat_id = str(old.get('chat') or '')
                    if chat_id.endswith('@chatroom'):
                        venue = 'group'
                    elif chat_id and not chat_id.startswith('@') and old['kind'] in ('chat_style', 'persona', 'expression', 'interaction_rule', 'impression', 'relationship'):
                        venue = 'private'
                    else:
                        venue = 'any'
                    tier = DEFAULT_TIER_MAP.get(old['kind'], 'ordinary')
                    cur = conn.execute(
                        '''INSERT INTO memories(account,chat,subject,kind,scope,content,source_key,actor,
                           version,archived,created,updated,expires_at,triggers,salience,tier,status,
                           confidence,role_id,valid_from,source_class,venue)
                           VALUES(?,?,?,?,?,?,?,'model',1,0,?,?,?,?,3,?,?,?,'',?,'learning',?)''',
                        (old['account'], old['chat'], old['subject'], old['kind'], old['scope'],
                         old['content'], old['source_key'], old['created'], now, old['expires_at'],
                         old.get('alias_target', ''), tier, 'active', old['confidence'], old['created'], venue),
                    )
                    mem_id = cur.lastrowid
                    after_m = dict(conn.execute('SELECT * FROM memories WHERE id=?', (mem_id,)).fetchone())
                    if 'memory_origins' in tables:
                        conn.execute(
                            "INSERT OR REPLACE INTO memory_origins(account,source,origin_id,memory_id,origin_version) VALUES(?,'learning',?,?,?)",
                            (old['account'], str(old['id']), mem_id, new_version),
                        )
                    if 'memory_revisions' in tables:
                        conn.execute(
                            '''INSERT INTO memory_revisions(memory_id,account,actor,operation,before_json,after_json,created)
                               VALUES(?,?,'maintenance','wash_materialize_learning',NULL,?,?)''',
                            (mem_id, old['account'], json.dumps(after_m, ensure_ascii=False), now),
                        )
                    applied['memories_synced'] += 1

        for fix in plan['drift_fixes']:
            before_m = dict(conn.execute('SELECT * FROM memories WHERE id=?', (fix['memory_id'],)).fetchone())
            after_m = {**before_m, 'status': fix['target_status'], 'archived': 0, 'updated': now, 'version': int(before_m['version']) + 1}
            conn.execute(
                'UPDATE memories SET status=?, archived=0, updated=?, version=? WHERE id=?',
                (fix['target_status'], now, after_m['version'], fix['memory_id']),
            )
            if 'memory_revisions' in tables:
                conn.execute(
                    '''INSERT INTO memory_revisions(memory_id,account,actor,operation,before_json,after_json,created)
                       VALUES(?,?,'maintenance','wash_fix_drift',?,?,?)''',
                    (fix['memory_id'], before_m['account'], json.dumps(before_m, ensure_ascii=False), json.dumps(after_m, ensure_ascii=False), now),
                )
            applied['drift_fixes'] += 1

        for item in plan['memory_actions']:
            before_m = dict(item['row'])
            new_ver = int(before_m['version']) + 1
            to_status = item['to_status']
            after_m = {**before_m, 'status': to_status, 'archived': 0, 'updated': now, 'version': new_ver}
            conn.execute(
                'UPDATE memories SET status=?, archived=0, updated=?, version=? WHERE id=?',
                (to_status, now, new_ver, before_m['id']),
            )
            if 'memory_revisions' in tables:
                op = {'active': 'wash_accept', 'rejected': 'wash_reject', 'blocked': 'wash_block'}.get(to_status, 'wash_expire')
                conn.execute(
                    '''INSERT INTO memory_revisions(memory_id,account,actor,operation,before_json,after_json,created)
                       VALUES(?,?,'maintenance',?,?,?,?)''',
                    (before_m['id'], before_m['account'], op, json.dumps(before_m, ensure_ascii=False), json.dumps(after_m, ensure_ascii=False), now),
                )
            applied['standalone_memories'] += 1

    return applied


def format_report(plan: dict, *, backup_path: Path | None = None, applied: dict | None = None) -> str:
    lines = [
        f"=== 待审记忆洗库报告 ({plan['generated_at']}) ===",
        f"learning_proposals 清洗前: {json.dumps(plan['proposals_before'], ensure_ascii=False)}",
        f"memories 清洗前:           {json.dumps(plan['memories_before'], ensure_ascii=False)}",
        f"待处理 proposal 动作: {len(plan['proposal_actions'])} 条 | 独立 memory 动作: {len(plan['memory_actions'])} 条 | 漂移修复: {len(plan['drift_fixes'])} 条",
        f"分类汇总: {json.dumps(plan['reason_counts'], ensure_ascii=False)}",
        f"预计清洗后 learning_proposals: {json.dumps(plan['projected_proposals'], ensure_ascii=False)}",
        f"预计清洗后 memories:           {json.dumps(plan['projected_memories'], ensure_ascii=False)}",
    ]
    if backup_path is not None:
        lines.append(f"SQLite 在线备份: {backup_path}")
    if applied is not None:
        lines.append(f"实际执行结果: {json.dumps(applied, ensure_ascii=False)}")
    lines.append(f"\n--- 即将转正激活 ({len(plan['activated_items'])} 条) ---")
    for idx, item in enumerate(plan['activated_items'], 1):
        lines.append(
            f"{idx}. [{item['source']}#{item['id']}] {item['kind']}/{item['scope']} "
            f"chat={item['chat']} subject={item['subject']} reason={item['reason']} :: {item['content']}"
        )
    if not plan['activated_items']:
        lines.append("（无转正条目）")
    lines.append(f"\n--- 抽样淘汰/驳回/拦截 ({len(plan['rejected_or_expired_samples'])} 条展示) ---")
    for idx, item in enumerate(plan['rejected_or_expired_samples'], 1):
        lines.append(
            f"{idx}. [{item['source']}#{item['id']}] {item['kind']}/{item['scope']} "
            f"{item['action']} ({item['reason']}) :: {item['content']}"
        )
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='WhaleGirl Linux pending memory audit & wash tool')
    parser.add_argument('--db', default='/var/lib/whalegirl-linux/core/state.sqlite3', help='SQLite database path')
    parser.add_argument('--backup-dir', default='', help='Directory to write SQLite online backup')
    parser.add_argument('--settings', default='', help='Optional settings.json to auto-read owner_ids')
    parser.add_argument('--owner-id', action='append', default=[], help='Owner wxid to exempt from non-owner hijack regex')
    parser.add_argument('--min-profile-conf', type=float, default=0.0, help='Minimum confidence to activate valid profile/alias rows')
    parser.add_argument('--keep-owner-dm', action='store_true', help='Activate valid non-slang pending rows inside owner private DMs')
    parser.add_argument('--keep-chat-knowledge', action='store_true', help='Activate valid non-slang chat facts/preferences/relationships/chat_style')
    parser.add_argument('--exclude-proposal-id', action='append', default=[], help='Proposal IDs (comma-separated or repeated) to force expire')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Read-only audit without modifying the database (default)')
    mode.add_argument('--apply', action='store_true', help='Take online backup first, then apply cleanups inside a transaction')
    mode.add_argument('--backup', action='store_true', help='Only create verified SQLite online backup')
    parser.add_argument('--json', action='store_true', help='Emit machine-readable JSON output')
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    backup_dir = Path(args.backup_dir) if args.backup_dir else None
    owner_ids = {str(x).strip() for x in (args.owner_id or ()) if str(x).strip()}
    if args.settings:
        sp = Path(args.settings)
        if sp.is_file():
            raw_s = json.loads(sp.read_text(encoding='utf-8'))
            for oid in raw_s.get('owner_ids') or ():
                if str(oid).strip():
                    owner_ids.add(str(oid).strip())

    if args.backup:
        bpath = backup_database(db_path, backup_dir)
        out = {'backup_path': str(bpath), 'db': str(db_path)}
        print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else f'SQLite 在线备份完成: {bpath}')
        return 0

    excluded_ids: set[int] = set()
    for raw in args.exclude_proposal_id or ():
        for tok in str(raw).split(','):
            tok = tok.strip()
            if tok:
                excluded_ids.add(int(tok))

    plan_kw = {
        'owner_ids': owner_ids,
        'min_profile_conf': float(args.min_profile_conf),
        'keep_owner_dm': bool(args.keep_owner_dm),
        'keep_chat_knowledge': bool(args.keep_chat_knowledge),
        'exclude_proposal_ids': excluded_ids,
    }

    if args.apply:
        bpath = backup_database(db_path, backup_dir)
        conn = sqlite3.connect(str(db_path), timeout=30)
        try:
            plan = build_plan(conn, **plan_kw)
            applied = apply_plan(conn, plan)
            after_props = {
                r[0]: r[1] for r in conn.execute('SELECT state, count(*) FROM learning_proposals GROUP BY state')
            } if 'learning_proposals' in _table_names(conn) else {}
            after_mems = {
                r[0]: r[1] for r in conn.execute('SELECT status, count(*) FROM memories GROUP BY status')
            } if 'memories' in _table_names(conn) else {}
        finally:
            conn.close()
        if args.json:
            payload = {
                **{k: v for k, v in plan.items() if k not in ('proposal_actions', 'memory_actions')},
                'backup_path': str(bpath),
                'applied': applied,
                'after_proposals': after_props,
                'after_memories': after_mems,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(format_report(plan, backup_path=bpath, applied=applied))
            print(f"清洗后实测 learning_proposals: {json.dumps(after_props, ensure_ascii=False)}")
            print(f"清洗后实测 memories:           {json.dumps(after_mems, ensure_ascii=False)}")
        return 0

    # Default / --dry-run: strictly read-only connection.
    conn = sqlite3.connect(f'{db_path.resolve().as_uri()}?mode=ro', uri=True, timeout=30)
    try:
        plan = build_plan(conn, **plan_kw)
    finally:
        conn.close()
    if args.json:
        payload = {k: v for k, v in plan.items() if k not in ('proposal_actions', 'memory_actions')}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report(plan))
    return 0


if __name__ == '__main__':
    sys.exit(main())
