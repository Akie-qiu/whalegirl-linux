"""Repair only the screenshot-proven old acknowledgement, without resending."""
import json
from pathlib import Path
import sqlite3
import time

DATA = Path('/var/lib/whalegirl-linux/core')
JOB = 'edbd567f00e44f79a02724c6a0cebde7'
ACTION = 'picture:' + JOB + '#part-0'
REPORT = Path('/opt/whalegirl-linux-assets/chat-world-receipts-20261030/screenshot-receipt-repair.json')


def main():
    if REPORT.exists():
        raise RuntimeError('Screenshot recovery already recorded')
    with sqlite3.connect('file:' + str(DATA / 'state.sqlite3') + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        job = db.execute('SELECT * FROM picture_jobs WHERE id=?', (JOB,)).fetchone()
        action = db.execute('SELECT * FROM outbound_actions WHERE id=?', (ACTION,)).fetchone()
        if not job or not action or job['state'] != 'uncertain' or action['state'] != 'uncertain':
            raise RuntimeError('Unexpected state; do not override it')
        rows = [dict(r) for r in db.execute("""SELECT key,local_id,timestamp FROM message_log WHERE account=?
            AND chat=? AND kind=3 AND is_self=1 AND local_id=8864 AND timestamp BETWEEN ? AND ?""",
            (job['account'], job['chat'], action['sent_at'] - 2, action['sent_at'] + 40))
            if r['key'].rsplit('/', 1)[-1].isdigit() and int(r['key'].rsplit('/', 1)[-1]) > 0]
        if len(rows) != 1 or action['reply'] != '[图片] ' + job['asset_id']:
            raise RuntimeError('Screenshot image does not have a unique stored server acknowledgement')
        receipt = rows[0]['key'].rsplit('/', 1)[-1]
        before = {'job': dict(job), 'action': dict(action)}
    backup = REPORT.with_name('screenshot-receipt-before.json')
    if backup.exists():
        raise RuntimeError('Existing recovery backup; inspect before retrying')
    backup.write_text(json.dumps(before, ensure_ascii=False))
    backup.chmod(0o600)
    details = {**json.loads(action['details_json'] or '{}'), 'asset_id': job['asset_id'],
        'local_id': 8864, 'confirmed_at': time.time(), 'final_result': 'sent',
        'confirmation': 'native_log_and_user_screenshot'}
    with sqlite3.connect(DATA / 'state.sqlite3', timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        one = db.execute("""UPDATE outbound_actions SET state='sent',receipt=?,error='',details_json=?
            WHERE id=? AND account=? AND chat=? AND state='uncertain'""",
            (receipt, json.dumps(details), ACTION, job['account'], job['chat'])).rowcount
        two = db.execute("""UPDATE picture_jobs SET state='sent',receipt=?,error='',updated=?
            WHERE id=? AND account=? AND chat=? AND state='uncertain' AND asset_id=?""",
            (receipt, time.time(), JOB, job['account'], job['chat'], job['asset_id'])).rowcount
        if (one, two) != (1, 1):
            raise RuntimeError('State changed; rollback screenshot recovery')
        db.execute('INSERT INTO safety_events(account,created,level,code,reason,chat) VALUES(?,?,?,?,?,?)',
            (job['account'], time.time(), 'info', 'send_receipt_resolved',
             '截图对应图片已按已保存的微信服务器回执核实成功，无重新发送', job['chat']))
    result = {'job': JOB, 'action_updated': one, 'job_updated': two,
        'receipt': receipt, 'state': 'sent', 'native_messages_sent': 0}
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    REPORT.chmod(0o600)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
