"""Bounded native submission and durable receipt reconciliation over existing polls."""
import asyncio
import json
import time

import httpx


MAX_SEND_ATTEMPTS = 2  # Includes the original submission.


def matches_message(kind, details, row):
    if row.get('isSelf') is not True:
        return False
    expected_type = {'text': 1, 'sticker': 47, 'image': 3}[kind]
    if kind == 'text':
        content = str(row.get('content') or '').strip()
        text = str(details.get('text') or '').strip()
        mentions = set(details.get('mentions') or [])
        return (row.get('type') in (1, 49) and bool(text) and
                (content == text or bool(mentions and content.endswith(text) and
                 mentions.issubset(row.get('mentionedUserIds') or []))))
    if row.get('type') != expected_type:
        return False
    return kind == 'image' or (row.get('sticker') or {}).get('md5') == details.get('md5')


async def send_native(gateway, kind, chat, **values):
    from .gateway import Receipt, UIJobError, outbound_identity, message_id, server_acknowledged

    if not gateway.ui_jobs_directory:
        return Receipt(False, error='原生发送尚未配置', definite_failure=True)
    operation = {'text': 'send_text', 'sticker': 'send_sticker', 'image': 'send_image'}[kind]
    attempts_log = []
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        details = {key: values[key] for key in ('text', 'md5', 'asset_id') if key in values}
        details['mentions'] = [m['id'] for m in values.get('mentions', [])]
        details['started_at'] = time.time()
        crossed_boundary = False
        locked_at = time.monotonic()
        try:
            # Capture the baseline only after earlier UI jobs have finished.
            # Receipt polling itself never owns the shared UI lock.
            async with gateway.ui_lock:
                details['ui_lock_seconds'] = round(time.monotonic() - locked_at, 3)
                before = await gateway.messages(chat)
                details['before_ids'] = [outbound_identity(m) for m in before]
                details['before_local_id'] = max((int(m.get('localId') or 0) for m in before), default=0)
                submitted_at = time.monotonic()
                crossed_boundary = True
                result = await gateway.ui_job(operation, chat, **values)
                details['ui_seconds'] = round(time.monotonic() - submitted_at, 3)
                details['ui_timing'] = result.get('ui_timing') or {}
                details['job_id'] = result.get('job_id', '')
                details['submit_state'] = result.get('state', '')
                details['submitted_at'] = time.time()
            if result.get('state') != 'submitted':
                receipt = Receipt(False, error='微信原生任务未确认提交',
                                  definite_failure=result.get('state') == 'failed_before_send',
                                  attempts=attempt, details=details)
            else:
                deadline = time.monotonic() + (3 if kind == 'image' else 2)
                failed_local_id = None
                while time.monotonic() < deadline:
                    try:
                        rows = await asyncio.wait_for(gateway.messages(chat),max(.05,deadline-time.monotonic()))
                    except (httpx.HTTPError, ValueError, OSError, asyncio.TimeoutError):
                        rows = []
                    fresh = [m for m in rows if outbound_identity(m) not in details['before_ids']
                             and matches_message(kind, details, m)]
                    if len(fresh) == 1:
                        item = fresh[0]
                        details['local_id'] = int(item.get('localId') or 0)
                        if server_acknowledged(item):
                            details['attempt_log'] = attempts_log + [{'attempt':attempt,'confirmed':True}]
                            reference = item.get('reply') or {}
                            quoted = bool(values.get('quote_to') and str(reference.get('serverId')) ==
                                          str(values['quote_to'].get('server_id')))
                            return Receipt(True, message_id(item), attempts=attempt, details=details,
                                quoted=quoted,
                                quote_error=(result.get('quote_fallback') or '原生引用未应用')
                                    if values.get('quote_to') and not quoted else '',
                                mentioned_ids=tuple(item.get('mentionedUserIds') or []),
                                mention_error=result.get('mention_fallback', ''))
                        if item.get('nativeStatus') == 5:
                            if failed_local_id == item.get('localId'):
                                # Do not create another red failed bubble by clicking
                                # a favorite again after WeChat has rejected this row.
                                return Receipt(False, error='微信客户端明确报告发送失败（原生状态5）',
                                    definite_failure=True, retryable=False, attempts=attempt, details=details)
                            failed_local_id = item.get('localId')
                        else:
                            failed_local_id = None
                    elif len(fresh) > 1:
                        break  # No unique correspondence; never guess a receipt.
                    await asyncio.sleep(.2)
                receipt = Receipt(False, error='已提交微信，回执待核实；后续聊天继续，不自动重发',
                                  attempts=attempt, details=details)
        except UIJobError as exc:
            details['submit_state'] = exc.state
            receipt = Receipt(False, error=str(exc), definite_failure=exc.state == 'failed_before_send',
                              attempts=attempt, details=details)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            receipt = Receipt(False,
                error=('微信原生发送回执待核实，不自动重发' if crossed_boundary else
                       '发送前读取暂时失败：' + type(exc).__name__),
                definite_failure=not crossed_boundary, attempts=attempt, details=details)
        attempts_log.append({'attempt': attempt, 'definite_failure': receipt.definite_failure,
                             'error': receipt.error, 'ui_seconds': details.get('ui_seconds', 0)})
        receipt.details['attempt_log'] = attempts_log
        if receipt.confirmed or not receipt.definite_failure or not receipt.retryable:
            return receipt
        if attempt < MAX_SEND_ATTEMPTS:
            await asyncio.sleep(.2)
    return receipt


def reconcile_outbound(store, account, chat, rows):
    """Use already-read native rows; never submit or fetch anything here."""
    from .gateway import outbound_identity, server_acknowledged, message_id
    from .runtime import timestamp

    pending = store.db.execute("SELECT * FROM outbound_actions WHERE account=? AND chat=? "
                              "AND state='uncertain' AND sent_at>? ORDER BY sent_at,id",
                              (account, chat, time.time() - 86400)).fetchall()
    if not pending:
        return []
    proposals = []
    for saved in pending:
        action = dict(saved)
        details = json.loads(action['details_json'] or '{}')
        if action['kind'] not in ('text', 'sticker', 'image') or 'before_ids' not in details:
            continue
        candidates = [m for m in rows if matches_message(action['kind'], details, m)
                      and outbound_identity(m) not in details['before_ids']
                      and (int(m.get('localId') or 0) == details['local_id'] if details.get('local_id') else
                           action['sent_at'] - 2 <= timestamp(m.get('timestamp')) <=
                           details.get('submitted_at', action['sent_at'] + 35) + 2)]
        if len(candidates) == 1:
            proposals.append((action, details, candidates[0]))
    counts = {}
    for _, _, row in proposals:
        identity = outbound_identity(row)
        counts[identity] = counts.get(identity, 0) + 1
    settled = []
    for action, details, row in proposals:
        if counts[outbound_identity(row)] != 1:
            continue
        confirmed = server_acknowledged(row)
        if not confirmed and row.get('nativeStatus') != 5:
            continue
        state = 'sent' if confirmed else 'failed'
        details.update(local_id=int(row.get('localId') or 0), confirmed_at=time.time(),
                       confirmation='native_poll', final_result=state)
        store.action_finish(action['id'], state, message_id(row) if confirmed else '',
                            '' if confirmed else '微信后续确认发送失败（原生状态5）', details=details)
        metric = store.db.execute('SELECT metrics FROM reply_metrics WHERE key=?', (action['inbox_key'],)).fetchone()
        if metric:
            metrics = json.loads(metric[0])
            for result in metrics.get('actions', []):
                if result.get('id') == action['id']:
                    result.update(confirmed=confirmed, state=state, confirmation='native_poll',
                                  confirmed_at=details['confirmed_at'])
            metrics['confirmed'] = all(a.get('confirmed') for a in metrics.get('actions', []))
            store.metrics(action['inbox_key'], metrics)
        refresh_turn(store, action['inbox_key'])
        settled.append({**action, 'state': state, 'details': details})
    return settled


def refresh_turn(store, key):
    turn = store.get(key)
    if not turn or turn['state'] != 'uncertain':
        return
    actions = store.actions(key)
    if not actions or any(a['state'] in ('sending', 'uncertain') for a in actions):
        return
    sent = [a for a in actions if a['state'] == 'sent']
    state = 'sent' if len(sent) == len(actions) else 'partial' if sent else 'failed'
    store.update(key, state, reply='\n'.join(a['reply'] for a in sent),
                 receipt='parts:' + str(len(sent)), error='' if state == 'sent' else '部分动作明确发送失败，详见动作记录')
