"""Read-only dashboard projections over the existing numeric/delivery ledgers."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3
import threading
import time

from .token_usage import FIELDS, TZ
from .usage_pricing import catalogue, quote

PERIODS = ('today', '7d', '30d', 'all')


def period_start(period, now):
    if period not in PERIODS:
        raise ValueError('统计时间范围无效')
    day = datetime.fromtimestamp(now, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return {'today': day.timestamp(), '7d': (day-timedelta(days=6)).timestamp(),
            '30d': (day-timedelta(days=29)).timestamp(), 'all': 0}[period]


def _new_tokens():
    return {'requests': 0, 'successful': 0, 'failed': 0, 'estimated_requests': 0,
            'model_requests': 0, 'search_requests': 0,
            'missing_token_requests': 0,
            **{field: None for field in FIELDS}, **{field+'_reports': 0 for field in FIELDS},
            'elapsed_ms': 0, 'cache_input': 0, 'cache_reads': 0, 'invalid_cache_requests': 0,
            'official_costs': {}, 'reported_costs': {}, 'pricing_modes': {},
            'unpriced_requests': 0, 'repriced_requests': 0, 'unattributed_requests': 0}


def _add_tokens(target, row, price):
    modes=target.setdefault('pricing_modes',{})
    mode=price.get('pricing_mode','official')
    modes[mode]=modes.get(mode,0)+1
    target['requests'] += 1
    target['search_requests' if row['operation'] in ('search','fetch','growth_search','growth_fetch') else 'model_requests'] += 1
    if row['operation'] not in ('search','fetch','growth_search','growth_fetch') and row['total_tokens'] is None:
        target['missing_token_requests'] += 1
    target['successful' if row['status'] == 'success' else 'failed'] += 1
    target['elapsed_ms'] += max(0, row['elapsed_ms'] or 0)
    estimated = row.get('estimated_fields') or '[]'
    target['estimated_requests'] += estimated != '[]'
    target['unattributed_requests'] += not bool(row.get('chat'))
    for field in FIELDS:
        if row[field] is not None:
            target[field] = (target[field] or 0) + row[field]
            target[field+'_reports'] += 1
    if row['cached_tokens'] is not None and row['input_tokens'] is not None:
        if 0 <= row['cached_tokens'] <= row['input_tokens']:
            target['cache_input'] += row['input_tokens']
            target['cache_reads'] += row['cached_tokens']
        else:
            target['invalid_cache_requests'] += 1
    if price.get('low') is not None:
        item = target['official_costs'].setdefault(price['currency'], {'currency': price['currency'],
            'low': 0., 'high': 0., 'requests': 0, 'estimated_token_requests': 0})
        item['low'] += price['low']; item['high'] += price['high']; item['requests'] += 1
        item['estimated_token_requests'] += bool(price.get('token_estimated'))
        target['repriced_requests'] += price.get('basis') == 'current_price_recalculation'
    else:
        target['unpriced_requests'] += 1
    if row.get('cost_value') is not None:
        currency = row.get('cost_currency') or 'provider_units'
        item = target['reported_costs'].setdefault(currency, {'currency': currency, 'value': 0., 'requests': 0,
                                                            'estimated_requests':0})
        item['value'] += row['cost_value']; item['requests'] += 1
        item['estimated_requests'] += bool(row.get('cost_estimated'))


def _finish_tokens(target):
    requests = target['requests']
    if not requests:
        for field in FIELDS:target[field]=0
    target['average_ms'] = round(target.pop('elapsed_ms') / requests) if requests else None
    cache_input = target.pop('cache_input')
    cache_reads = target.pop('cache_reads')
    target['cache_hit_rate'] = round(cache_reads*100/cache_input, 1) if cache_input else None
    target['total_tokens_complete'] = target['missing_token_requests'] == 0
    target['total_tokens_exact'] = target['total_tokens_complete'] and not target['estimated_requests']
    for field in ('official_costs', 'reported_costs'):
        target[field] = [dict(item, **{k: round(item[k], 8) for k in ('low', 'high', 'value') if k in item})
                         for item in target[field].values()]
    return target


class UsageDashboard:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.store
        self.cache = {}
        self.cache_lock = threading.RLock()
        self.query_gate = threading.RLock()

    def _connect(self):
        # An independent read transaction avoids holding the chat writer lock.
        with self.store.lock:
            path = self.store.db.execute('PRAGMA database_list').fetchone()[2]
        db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return db

    def snapshot(self, period='today', *, chat='', model='', view='both', now=None):
        # Concurrent browser refreshes share a bounded cache and one reader.
        # This gate is independent of the chat writer and model concurrency.
        with self.query_gate:
            return self._snapshot(period,chat=chat,model=model,view=view,now=now)

    def _snapshot(self, period='today', *, chat='', model='', view='both', now=None):
        now = time.time() if now is None else now
        since = period_start(period, now)
        if view not in ('tokens', 'speech', 'both') or len(chat) > 200 or len(model) > 120:
            raise ValueError('统计筛选参数无效')
        account = self.runtime.config.settings.account_id
        key = (account, period, chat, model, view, int(now//10))
        with self.cache_lock:
            if key in self.cache:
                return self.cache[key]
        started = time.monotonic()
        with closing(self._connect()) as db:
            first = min([row[0] or now for row in (
                db.execute('SELECT min(created) FROM model_token_usage WHERE account=?', (account,)).fetchone(),
                db.execute('SELECT min(sent_at) FROM outbound_actions WHERE account=?', (account,)).fetchone(),
                db.execute('SELECT min(timestamp) FROM message_log WHERE account=?', (account,)).fetchone())])
            chart_since = since or first
            monthly = not since and now-chart_since > 90*86400
            def bucket(timestamp):
                dt = datetime.fromtimestamp(timestamp, TZ)
                return dt.strftime('%Y-%m' if monthly else '%Y-%m-%dT%H:00' if period == 'today' else '%Y-%m-%d')
            timeline = {}
            cursor = datetime.fromtimestamp(chart_since, TZ).replace(hour=0, minute=0, second=0, microsecond=0)
            if monthly:
                cursor = cursor.replace(day=1)
            end = datetime.fromtimestamp(now, TZ)
            while cursor <= end:
                stamp = bucket(cursor.timestamp())
                timeline[stamp] = {'time': stamp, 'requests': 0, 'input_tokens': 0, 'output_tokens': 0,
                                  'total_tokens': 0, 'token_reports': 0, 'input_reports': 0, 'output_reports': 0,
                                  'received': 0, 'sent': 0, 'failed': 0,
                                  'official_costs': {}}
                cursor = ((cursor.replace(year=cursor.year+1, month=1) if cursor.month == 12 else
                           cursor.replace(month=cursor.month+1)) if monthly else
                          cursor + timedelta(hours=1) if period == 'today' else cursor + timedelta(days=1))
            result = {'period': period, 'timezone': 'Asia/Shanghai', 'since': since, 'until': now,
                      'generated_at': now, 'filters': {'chat': chat, 'model': model}, 'bucket': 'month' if monthly else
                      'hour' if period == 'today' else 'day', 'pricing': catalogue()}
            if view in ('tokens', 'both'):
                result['tokens'] = self._tokens(db, account, since, now, chat, model, timeline, bucket)
            if view in ('speech', 'both'):
                result['speech'] = self._speech(db, account, since, now, chat, timeline, bucket)
            for point in timeline.values():
                point['official_costs']=[dict(value,low=round(value['low'],8),high=round(value['high'],8))
                                         for value in point['official_costs'].values()]
            result['timeline'] = list(timeline.values())
        # sqlite context manager does not close a connection.
        result['query_ms'] = round((time.monotonic()-started)*1000)
        with self.cache_lock:
            if len(self.cache) >= 24:
                self.cache.clear()
            self.cache[key] = result
        return result

    def _tokens(self, db, account, since, now, chat, model, timeline, bucket):
        where = 'u.account=? AND u.created>=? AND u.created<=?'
        args = [account, since, now]
        if chat:
            where += ' AND c.chat=?'; args.append(chat)
        if model:
            where += ' AND u.model=?'; args.append(model)
        query = ('SELECT u.*,d.estimated_fields,d.cost_value,d.cost_currency,d.cost_estimated,c.chat,c.pricing_json '
                 'FROM model_token_usage u LEFT JOIN model_token_details d ON d.id=u.id '
                 'LEFT JOIN model_usage_context c ON c.id=u.id AND c.account=u.account WHERE '+where)
        summary, models, operations, latency = _new_tokens(), {}, {}, Counter()
        daily = {}
        reasons = Counter()
        for raw in db.execute(query, args):
            row = dict(raw)
            try:
                price = json.loads(row.get('pricing_json') or '{}')
            except (TypeError, ValueError):
                price = {}
            if not price.get('version'):
                price = quote({**row, 'estimated_fields': json.loads(row.get('estimated_fields') or '[]')}, historical=True)
            if price.get('status') == 'unknown':
                reasons[price.get('reason') or '无价格'] += 1
            group = (row['provider'], row['model'])
            target = models.setdefault(group, {'provider': row['provider'], 'model': row['model'], **_new_tokens()})
            operation = operations.setdefault(row['operation'], {'operation': row['operation'], **_new_tokens()})
            day = datetime.fromtimestamp(row['created'], TZ).date().isoformat()
            day_total = daily.setdefault(day, {'day': day, **_new_tokens()})
            for item in (summary, target, operation, day_total):
                _add_tokens(item, row, price)
            latency[min(3000, max(0, row['elapsed_ms'] or 0)//100)] += 1
            stamp = bucket(row['created'])
            point = timeline.get(stamp)
            if point is not None:
                point['requests'] += 1
                for field in ('input_tokens', 'output_tokens', 'total_tokens'):
                    point[field] += row[field] or 0
                point['token_reports'] += row['total_tokens'] is not None
                point['input_reports'] += row['input_tokens'] is not None
                point['output_reports'] += row['output_tokens'] is not None
                if price.get('low') is not None:
                    cost=point['official_costs'].setdefault(price['currency'],
                        {'currency':price['currency'],'low':0.,'high':0.})
                    cost['low']+=price['low'];cost['high']+=price['high']
        percentiles = {}
        for percentile in (50, 95):
            count = 0; threshold = math.ceil(summary['requests']*percentile/100)
            for value, frequency in sorted(latency.items()):
                count += frequency
                if count >= threshold:
                    percentiles['p'+str(percentile)+'_ms'] = (value+1)*100
                    break
        unattributed = db.execute('SELECT count(*) FROM model_token_usage u LEFT JOIN model_usage_context c ON c.id=u.id '
            "WHERE u.account=? AND u.created>=? AND u.created<=? AND coalesce(c.chat,'')=''", (account, since, now)).fetchone()[0]
        # Empty calendar days are explicit within bounded period queries. The
        # all-history view retains every recorded day, even when its chart is monthly.
        if since:
            cursor = datetime.fromtimestamp(since, TZ).date()
            while cursor <= datetime.fromtimestamp(now, TZ).date():
                day = cursor.isoformat()
                daily.setdefault(day, {'day': day, **_new_tokens()})
                cursor += timedelta(days=1)
        return {'summary': _finish_tokens(summary), 'models': sorted((_finish_tokens(v) for v in models.values()),
                key=lambda r: r['total_tokens'] or 0, reverse=True),
                'daily': [_finish_tokens(daily[day]) for day in sorted(daily, reverse=True)],
                'operations': [_finish_tokens(v) for v in operations.values()], 'latency': percentiles,
                'latency_note': '100毫秒分桶的耗时近似分位；超300秒落入末桶',
                'unattributed_account_requests': unattributed,
                'unknown_price_reasons': [{'reason': key, 'requests': value} for key, value in reasons.items()]}

    def _speech(self, db, account, since, now, chat, timeline, bucket):
        where = 'account=? AND timestamp>=? AND timestamp<=? AND is_self=0'
        args = [account, since, now]
        if chat:
            where += ' AND chat=?'; args.append(chat)
        chats, people = {}, set()
        summary = {'received': 0, 'sent': 0, 'reply_rounds': 0, 'failed': 0, 'uncertain': 0, 'sending': 0,
                   'proactive_rounds': 0, 'text_characters': 0}
        # Pending/server copies of the SAME native message count only once.
        rows = db.execute("SELECT chat,sender_id,min(timestamp) timestamp FROM message_log WHERE "+where+
            " GROUP BY chat,CASE WHEN local_id>0 THEN 'local:'||local_id ELSE key END", args)
        for row in rows:
            summary['received'] += 1
            target = chats.setdefault(row['chat'], {'chat': row['chat'], 'received': 0, 'sent': 0, 'failed': 0, 'uncertain': 0})
            target['received'] += 1
            if row['sender_id']:
                people.add(row['sender_id'])
            if bucket(row['timestamp']) in timeline:
                timeline[bucket(row['timestamp'])]['received'] += 1
        where = 'account=? AND sent_at>=? AND sent_at<=?'
        args = [account, since, now]
        if chat:
            where += ' AND chat=?'; args.append(chat)
        kinds, rounds, proactive = Counter(), set(), set()
        for row in db.execute('SELECT inbox_key,chat,kind,state,sent_at,CASE WHEN kind=\'text\' THEN length(reply) ELSE 0 END chars '
                              'FROM outbound_actions WHERE '+where, args):
            state = row['state']
            target = chats.setdefault(row['chat'], {'chat': row['chat'], 'received': 0, 'sent': 0, 'failed': 0, 'uncertain': 0})
            if state in ('sent', 'failed', 'uncertain', 'sending'):
                summary[state] += 1
                if state in target:
                    target[state] += 1
            if state == 'sent':
                rounds.add(row['inbox_key']); kinds[row['kind']] += 1
                summary['text_characters'] += row['chars'] or 0
                if row['inbox_key'].startswith('proactive:'):
                    proactive.add(row['inbox_key'])
            if state in ('sent', 'failed') and bucket(row['sent_at']) in timeline:
                timeline[bucket(row['sent_at'])][state] += 1
        summary.update(reply_rounds=len(rounds), proactive_rounds=len(proactive), speakers=len(people),
                       active_chats=len(chats), success_rate=round(summary['sent']*100/(summary['sent']+summary['failed']), 1)
                       if summary['sent']+summary['failed'] else None)
        return {'summary': summary, 'kinds': [{'kind': key, 'sent': value} for key, value in kinds.items()],
                'chats': sorted(chats.values(), key=lambda r: r['sent'], reverse=True),
                'notice': '发言只计确认成功的发送动作；一轮可含多条消息。失败和待核对不算送达。收录真人消息含导入历史，不是即时接收次数。'}

    def options(self):
        account = self.runtime.config.settings.account_id
        with closing(self._connect()) as db:
            models = [r[0] for r in db.execute('SELECT DISTINCT model FROM model_token_usage WHERE account=? ORDER BY model', (account,))]
            chats = [r[0] for r in db.execute('SELECT chat FROM message_log WHERE account=? UNION '
                'SELECT chat FROM outbound_actions WHERE account=? UNION SELECT chat FROM model_usage_context '
                "WHERE account=? AND chat!='' ORDER BY chat", (account, account, account))]
        labels = {r['id']: r.get('name', r['id']) for r in self.runtime.chats_cache}
        labels.update(self.runtime.chat_labels)
        return {'models': models, 'chats': [{'id': chat, 'name': labels.get(chat, '群聊' if chat.endswith('@chatroom') else '私聊')}
                                           for chat in chats]}
