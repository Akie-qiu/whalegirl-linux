"""Bounded native submission and durable receipt reconciliation over existing polls."""
import asyncio
import json
import time
from datetime import datetime

import httpx
from .ui_lock import send_access


MAX_SEND_ATTEMPTS = 2  # Includes the original submission.


def sticker_picker_layout_error(error):
    """Recognize a stale/virtualized picker before any send click."""
    text = str(error or '')
    return any(marker in text for marker in (
        'Sticker button is not unique',
        'Native favorite panel did not open',
        'Destination changed while opening the favorite panel',
        'outside the currently exposed native page',
        'identity changed before sending',
        'position became ambiguous before sending',
        'favorite panel changed before sending',
        'Native favorite layout needs refresh',
        'Native favorite boundary did not settle',
        'Native favorite grid could not be verified',
    ))


STICKER_ANCHOR_CACHE = 'sticker-panel-anchors.cache'


def misdelivered_stickers(details, rows):
    """Own, acknowledged sticker rows from this submission with another favorite."""
    from .gateway import outbound_identity, server_acknowledged

    expected = str(details.get('md5') or '').lower()
    submitted_at = float(details.get('submitted_at') or details.get('started_at') or 0)
    before_local_id = int(details.get('before_local_id') or 0)
    return [m for m in rows if m.get('isSelf') is True and m.get('type') == 47
            and outbound_identity(m) not in details.get('before_ids', ())
            and int(m.get('localId') or 0) > before_local_id
            and server_acknowledged(m)
            and (not sticker_message_timestamp(m) or
                 submitted_at - 2 <= sticker_message_timestamp(m) <= time.time() + 1)
            and str((m.get('sticker') or {}).get('md5') or '').lower() not in ('', expected)]


def sticker_message_timestamp(message):
    value = message.get('timestamp')
    if isinstance(value, (float, int)):
        return value / 1000 if value > 10**12 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            return 0
    return 0


def invalidate_sticker_anchors(gateway):
    """Mark stale anchors unusable while retaining the cache file and evidence."""
    directory = getattr(gateway, 'ui_jobs_directory', None)
    if not directory:
        return False
    path = directory / STICKER_ANCHOR_CACHE
    try:
        cached = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(cached, dict):
            cached = {'previous_cache': cached}
        cached['invalidated'] = True
        cached['invalidated_at'] = time.time()
        path.write_text(json.dumps(cached, ensure_ascii=False), encoding='utf-8')
        return True
    except (OSError, ValueError, TypeError):
        return False


def misdelivered_sticker_receipt(gateway, details, row, attempt):
    from .gateway import Receipt

    wrong_md5 = str((row.get('sticker') or {}).get('md5') or '')
    details.update(wrong_sticker_md5=wrong_md5,
                   local_id=int(row.get('localId') or 0),
                   anchors_invalidated=invalidate_sticker_anchors(gateway))
    return Receipt(False, error='发错表情：微信实际发出了另一张收藏表情，已标记定位缓存失效，不重发',
                   definite_failure=True, retryable=False, attempts=attempt, details=details)


def matches_message(kind, details, row):
    if row.get('isSelf') is not True:
        return False
    if kind == 'pat':
        pat = row.get('pat') or {}
        return row.get('event') == 'pat' and bool(details.get('target_id')) and pat.get('target') == details.get('target_id')
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
    operation = {'text': 'send_text', 'sticker': 'send_sticker', 'image': 'send_image', 'pat': 'send_pat'}[kind]
    from .fast_send import current
    attempts_log = []
    for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
        details = {key: values[key] for key in ('text', 'md5', 'asset_id', 'target_id', 'target_name') if key in values}
        details['kind'] = kind
        details['mentions'] = [m['id'] for m in values.get('mentions', [])]
        details['started_at'] = time.time()
        crossed_boundary = False
        locked_at = time.monotonic()
        try:
            # Capture the baseline only after earlier UI jobs have finished.
            # Receipt polling itself never owns the shared UI lock.
            session=current(gateway,chat) if kind in ('text','sticker','pat') else None
            if session:
                before=await _recent_messages(gateway,chat)
                details['before_ids']=[outbound_identity(m) for m in before]
                details['before_local_id']=max((int(m.get('localId') or 0) for m in before),default=0)
                submitted_at=time.monotonic()
                result=await session.submit(kind,values)
                details['ui_seconds']=round(time.monotonic()-submitted_at,3)
                if result is not None:
                    crossed_boundary=result.get('state') not in ('failed_before_send',)
                    details.update(ui_timing=result.get('ui_timing') or {},fast_session=True,
                        job_id=result.get('job_id',''),part_index=result.get('part_index'),
                        submit_state=result.get('state',''),submitted_at=result.get('submitted_at') or time.time())
                    details['submission_timestamp_observed']=bool(result.get('submitted_at'))
            else:result=None
            if result is None:
                async with send_access(gateway.ui_lock):
                    details['ui_lock_seconds'] = round(time.monotonic() - locked_at, 3)
                    before = await _recent_messages(gateway, chat)
                    details['before_ids'] = [outbound_identity(m) for m in before]
                    details['before_local_id'] = max((int(m.get('localId') or 0) for m in before), default=0)
                    submitted_at = time.monotonic()
                    crossed_boundary = True
                    result = await gateway.ui_job(operation, chat, **values)
                    details['ui_seconds'] = round(time.monotonic() - submitted_at, 3)
                    details['ui_timing'] = result.get('ui_timing') or {}
                    details['job_id'] = result.get('job_id', '')
                    details['submit_state'] = result.get('state', '')
                    details['submitted_at'] = result.get('submitted_at') or time.time()
                    details['submission_timestamp_observed'] = bool(result.get('submitted_at'))
            if result.get('cancelled'):
                details['cancelled']=True
                return Receipt(False,error=result.get('error','本轮已取消，未发送'),definite_failure=True,
                               retryable=False,attempts=attempt,details=details)
            if kind == 'pat' and result.get('pat_skipped'):
                return Receipt(False, error=result.get('reason') or result.get('error') or '拍一拍目标未定位，已自动跳过',
                               definite_failure=True, retryable=False, attempts=attempt,
                               details={**details, 'pat_skipped': True})
            valid_states = ('submitted', 'done') if kind == 'pat' else ('submitted',)
            if result.get('state') not in valid_states:
                receipt = Receipt(False, error=result.get('error') or '微信原生任务未确认提交',
                                  definite_failure=result.get('state') == 'failed_before_send',
                                  retryable=kind != 'pat', attempts=attempt, details=details)
            else:
                deadline = time.monotonic() + (3 if kind == 'image' else 1.2 if kind == 'pat' else 2)
                failed_local_id = None
                pending_reads = 0
                misdelivered = []
                while time.monotonic() < deadline:
                    try:
                        rows = await asyncio.wait_for(_recent_messages(gateway, chat),max(.05,deadline-time.monotonic()))
                    except (httpx.HTTPError, ValueError, OSError, asyncio.TimeoutError):
                        rows = []
                    fresh = [m for m in rows if outbound_identity(m) not in details['before_ids']
                             and matches_message(kind, details, m)]
                    if kind == 'sticker' and rows and not fresh:
                        misdelivered = misdelivered_stickers(details, rows)
                        if len(misdelivered) == 1:
                            return misdelivered_sticker_receipt(gateway, details, misdelivered[0], attempt)
                    if len(fresh) == 1:
                        item = fresh[0]
                        details.setdefault('local_seen_at', time.time())
                        details['local_id'] = int(item.get('localId') or 0)
                        if (server_acknowledged(item) or
                                (kind == 'pat' and item.get('nativeStatus') != 5 and
                                 (item.get('type') in (10000, 10002) or result.get('pat_confirmed')))):
                            details['attempt_log'] = attempts_log + [{'attempt':attempt,'confirmed':True}]
                            details['confirmed_at'] = time.time()
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
                    # Most accepted rows acquire their server ID within the
                    # first fraction of a second. Recheck those twice sooner;
                    # keep the same deadline and unique server receipt rules.
                    delay=(.05,.1)[pending_reads] if pending_reads<2 else .2
                    pending_reads+=1
                    await asyncio.sleep(min(delay,max(0,deadline-time.monotonic())))
                if kind == 'pat' and result.get('pat_confirmed') and result.get('pat_local_id'):
                    details['local_id'] = int(result['pat_local_id'])
                    details['attempt_log'] = attempts_log + [{'attempt': attempt, 'confirmed': True}]
                    return Receipt(True, f"local:{details['local_id']}", attempts=attempt, details=details)
                receipt = Receipt(False, error='已提交微信，回执待核实；后续聊天继续，不自动重发',
                                  retryable=kind != 'pat', attempts=attempt, details=details)
        except UIJobError as exc:
            details['submit_state'] = exc.state
            receipt = Receipt(False, error=str(exc), definite_failure=exc.state == 'failed_before_send',
                              retryable=kind != 'pat', attempts=attempt, details=details)
        except (httpx.HTTPError, ValueError, OSError) as exc:
            receipt = Receipt(False,
                error=('微信原生发送回执待核实，不自动重发' if crossed_boundary else
                       '发送前读取暂时失败：' + type(exc).__name__),
                definite_failure=not crossed_boundary, retryable=kind != 'pat', attempts=attempt, details=details)
        if (kind == 'sticker' and receipt.definite_failure and
                details.get('submit_state') == 'failed_before_send' and
                sticker_picker_layout_error(receipt.error)):
            # The picker never clicked a tile. Repeating against the same
            # virtualized panel only adds latency and can create an account
            # level recovery episode, so leave the optional sticker out.
            receipt.retryable = False
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
                              "AND (state='uncertain' OR (state='sending' AND sent_at<?) "
                              "OR (state='failed' AND kind='image' AND inbox_key LIKE 'picture:%')) "
                              "AND sent_at>? ORDER BY sent_at,id",
                              (account, chat, time.time() - 180, time.time() - 86400)).fetchall()
    if not pending:
        return []
    proposals = []
    for saved in pending:
        action = dict(saved)
        details = json.loads(action['details_json'] or '{}')
        if action['state'] == 'sending' and action['kind'] in ('text', 'sticker', 'image', 'pat'):
            # A missing receipt is never permission to repeat a UI submission.
            store.action_finish(action['id'], 'uncertain', error='发送超时遗留，等待原生回执核对，不自动重发', details=details)
            action['state'] = 'uncertain'
            turn = store.get(action['inbox_key'])
            if turn and turn['state'] == 'sending' and not any(
                    row['state'] == 'sending' for row in store.actions(action['inbox_key'])):
                store.update(action['inbox_key'], 'uncertain', reply=turn['reply'], receipt=turn['receipt'],
                             error='发送超时遗留，等待原生回执核对，不自动重发')
        if action['state'] == 'failed' and not details.get('local_id'):
            continue  # Only the exact previously rejected native row can recover.
        if action['kind'] not in ('text', 'sticker', 'image', 'pat') or 'before_ids' not in details:
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
        confirmed = server_acknowledged(row) or (
            action['kind'] == 'pat' and row.get('nativeStatus') != 5 and row.get('type') in (10000, 10002))
        if action['state'] == 'failed' and not confirmed:
            continue
        if not confirmed and row.get('nativeStatus') != 5:
            continue
        state = 'sent' if confirmed else 'failed'
        details.update(local_id=int(row.get('localId') or 0), confirmed_at=time.time(),
                       confirmation='native_poll', final_result=state)
        store.action_finish(action['id'], state, message_id(row) if confirmed else '',
                            '' if confirmed else '微信后续确认发送失败（原生状态5）', details=details)
        if action['kind'] == 'image' and action['inbox_key'].startswith('picture:') and store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='picture_jobs'").fetchone():
            # Image tasks and their outbound action must share the final receipt.
            # Reuse the normal poll; do not wait longer or submit a second image.
            with store.lock, store.db:
                store.db.execute("""UPDATE picture_jobs SET state=?,receipt=?,error=?,updated=?
                    WHERE id=? AND account=? AND chat=? AND asset_id=? AND state IN ('uncertain','failed')""",
                    (state, message_id(row) if confirmed else '',
                     '' if confirmed else '微信后续确认发送失败（原生状态5）', time.time(),
                     action['inbox_key'].removeprefix('picture:'), account, chat, details.get('asset_id', '')))
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


async def _recent_messages(gateway, chat, limit=25):
    try:
        return await gateway.messages(chat, limit=limit)
    except TypeError as exc:
        if 'limit' in str(exc) or 'keyword' in str(exc):
            return await gateway.messages(chat)
        raise
