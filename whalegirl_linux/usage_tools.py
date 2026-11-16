"""Optional numeric read by the model. No polling, model calls or writes here."""
import json
import re
from datetime import datetime

from .token_usage import TZ


_SCOPE_PROVENANCE_PATTERNS = {
    '全账号': re.compile(r'(全账号|所有会话|全部会话|账号合计|账号总共|整个账号|总账户)'),
    '本群': re.compile(r'(本群|这个群|群里|当前群|当前会话)'),
    '本次私聊': re.compile(r'(本次私聊|这个私聊|当前私聊|私聊里|咱俩私聊|当前会话)'),
}

_PERIOD_PROVENANCE_PATTERNS = {
    '今天': re.compile(r'(今天|今日|当天)'),
    '最近7天': re.compile(r'(最近\s*7\s*天|近\s*7\s*天|这\s*7\s*天|7\s*天|最近七天|近七天|这七天|七天|这一周|最近一周|近一周|本周)'),
    '最近30天': re.compile(r'(最近\s*30\s*天|近\s*30\s*天|这\s*30\s*天|30\s*天|最近三十天|近三十天|这三十天|三十天|近一个月|最近一个月|本月)'),
    '全部已记录历史': re.compile(r'(全部已记录|全部历史|全历史|有记录以来|累计一共|全部时间|一直以来的全部)'),
}


def _has_usage_provenance(text: str, scope_label: str, period_label: str) -> bool:
    scope_pat = _SCOPE_PROVENANCE_PATTERNS.get(scope_label)
    period_pat = _PERIOD_PROVENANCE_PATTERNS.get(period_label)
    has_scope = bool(scope_pat.search(text)) if scope_pat else (scope_label in text)
    has_period = bool(period_pat.search(text)) if period_pat else (period_label in text)
    return has_scope and has_period


class UsageStatsTools:
    def __init__(self, runtime):
        self.runtime = runtime

    @staticmethod
    def prompt():
        return ('\n你有只读统计工具read_usage_stats，可按需查真实Token、费用估算或发言汇总。'
                '没有函数工具时可用JSON字段stats_request:{"view":"both","period":"today"}，'
                '同时actions留空等待结果。已授权主人无论群聊或私聊，查询用量默认查全账号；'
                '只有主人明确提出本群或本次私聊的用量时才使用scope=current_chat。其他人只可查当前会话。'
                '回答必须保留工具返回的统计范围和时间；不能把本群用量说成全账号。'
                '用1～2句自然微信口语把统计范围、时间段和核心数字顺进话里就行，不要照搬“全账号·最近7天：”或“七天账单来啦：”当播报头，'
                '也不要一问消耗就拆成三四条长清单（除非对方明确要每天或分项明细）。'
                '总Token已包含实际思考，不重复加思考明细，不把设置的思考上限当消耗。'
                '费用可来自官方参考价或管理员自定义价，以pricing_modes说明来源，不把自定义价说成官方价。'
                '不要猜测用量或把参考费用说成实际账单；平常闲聊无需读取。')

    @staticmethod
    def schemas(engine):
        if (getattr(engine, 'api_style', '') != 'openai' or getattr(engine, 'stream_enabled', False)
                or not hasattr(engine, 'chat_with_tools')):
            return []
        return [{'type': 'function', 'function': {'name': 'read_usage_stats',
            'description': '按北京时间读取真实用量、每日总Token（含实际思考）、费用估算和发言汇总。主人查用量默认全账号，群聊或私聊均可；只有明确要求本群或本次私聊时才选current_chat。其他人仅可查当前会话。查询每天明细可选7d或30d。回答保留返回的范围和日期。单独调用；不查消息正文或凭据。',
            'parameters': {'type': 'object', 'properties': {
                'view': {'type': 'string', 'enum': ['tokens', 'speech', 'both']},
                'period': {'type': 'string', 'enum': ['today', '7d', '30d', 'all']},
                'scope': {'type': 'string', 'enum': ['current_chat', 'account']}},
                'required': [], 'additionalProperties': False}}}]

    @staticmethod
    def native_decision(result):
        calls = result.get('tool_calls') or []
        selected = [call for call in calls if isinstance(call, dict)
                    and (call.get('function') or {}).get('name') == 'read_usage_stats']
        if not selected:
            return None
        try:
            arguments = json.loads(selected[0]['function'].get('arguments') or '{}')
        except (TypeError, ValueError):
            arguments = None
        if len(calls) != 1 or not isinstance(arguments, dict):
            arguments = {'invalid': True}
        return {'should_reply': True, 'actions': [], 'stats_request': arguments}

    def execute(self, first, request):
        s = self.runtime.config.settings
        if (not s.enabled or first.get('account') != s.account_id
                or first.get('chat') not in s.allowed_chat_ids):
            return {'ok': False, 'reason': '当前会话未授权读取统计'}
        if (not isinstance(request, dict) or set(request)-{'view', 'period', 'scope'}
                or request.get('view', 'both') not in ('tokens', 'speech', 'both')
                or request.get('period', 'today') not in ('today', '7d', '30d', 'all')
                or request.get('scope', 'current_chat') not in ('current_chat', 'account')):
            return {'ok': False, 'reason': '统计工具参数无效'}
        owner = (bool(first.get('sender_id')) and first['sender_id'] in s.owner_ids
                 and (first['chat'].endswith('@chatroom') or first['chat'] == first['sender_id']))
        scope = request.get('scope', 'account' if owner else 'current_chat')
        if scope == 'account' and not owner:
            return {'ok': False, 'reason': '全账号统计仅允许已授权主人读取，其他人可读取当前会话汇总'}
        data = self.runtime.statistics.snapshot(request.get('period', 'today'),
            chat=first['chat'] if scope == 'current_chat' else '', view=request.get('view', 'both'))
        result = {'ok': True, 'scope': scope, 'period': data['period'], 'timezone': data['timezone'],
                  'as_of': data['generated_at'], 'notice': '费用按请求时保存的官方参考价或管理员自定义价估算；pricing_modes区分来源。DeepSeek中转默认官方同价；缺失回执不猜测；不同币种不合并。'}
        result['scope_label'] = '全账号' if scope == 'account' else '本群' if first['chat'].endswith('@chatroom') else '本次私聊'
        result['period_label'] = {'today':'今天','7d':'最近7天','30d':'最近30天','all':'全部已记录历史'}[data['period']]
        result['as_of_beijing'] = datetime.fromtimestamp(data['generated_at'], TZ).isoformat(timespec='seconds')
        result['reply_heading'] = result['scope_label'] + '·' + result['period_label'] + '：'
        if 'tokens' in data:
            result['tokens'] = data['tokens']['summary']
            result['attribution_notice'] = ('包含所有会话以及后台和历史未归属调用。' if scope == 'account' else
                '仅此会话已记录归属的调用；不含其他会话或未归属后台用量，不能称为全账号总量。')
            fields = ('day','requests','input_tokens','output_tokens','total_tokens','reasoning_tokens',
                      'reasoning_tokens_reports','missing_token_requests','estimated_requests','official_costs','pricing_modes')
            days = data['tokens']['daily']
            result['daily_tokens'] = [{key: day[key] for key in fields} for day in days[:31]]
            result['daily_total_days'] = len(days)
            result['daily_omitted_days'] = max(0, len(days)-31)
            result['accounting_notice'] = ('输入包含缓存命中，输出包含实际思考，总量不重复相加。'
                'reasoning_tokens仅为已单独上报的思考明细，不代表全部接口都上报；'
                'missing_token_requests为缺少用量回执的模型尝试，已记录总量不包含其未知消耗。'
                '思考预算/max_tokens只是上限，不作为已消费Token；每日明细最多返回最近31天，合计覆盖所选完整范围。')
        if 'speech' in data:
            result['speech'] = data['speech']['summary']
            result['speech_notice'] = data['speech']['notice']
        return result

    @staticmethod
    def label_reply(decision, result):
        """Scope is factual provenance, preserved even when the model omits it."""
        if not result.get('ok') or not decision.get('should_reply'):
            return
        text_actions = [
            action for action in (decision.get('actions') or [])
            if isinstance(action, dict) and action.get('type') == 'text' and isinstance(action.get('text'), str)
        ]
        if not text_actions:
            return
        scope_label = str(result.get('scope_label') or '').strip()
        period_label = str(result.get('period_label') or '').strip()
        heading = str(result.get('reply_heading') or '').strip()
        if not scope_label or not period_label:
            if heading and not text_actions[0]['text'].startswith(heading):
                text_actions[0]['text'] = heading + ' ' + text_actions[0]['text']
            return
        combined = '\n'.join(action['text'] for action in text_actions)
        tag = f'（{scope_label}·{period_label}）'
        if (heading and heading.rstrip('：:') in combined) or tag in combined:
            return
        if _has_usage_provenance(combined, scope_label, period_label):
            return
        text_actions[-1]['text'] = text_actions[-1]['text'].rstrip() + tag
