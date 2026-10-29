"""One UI recovery per failure episode, with no resend or model request."""
import asyncio
import time


SEND_HOLDS = {'failure_circuit', 'sticker_retry_exhausted', 'ui_recovery_failed'}


class RecoveryService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = asyncio.Lock()
        self.event = asyncio.Event()
        self.busy = False
        runtime.safety.recovery_callback = self.event.set

    def snapshot(self):
        r = self.runtime
        state = r.safety.state(r.config.settings.account_id)
        return {'enabled':r.config.settings.send_auto_recovery_enabled, 'busy':self.busy,
                'status':state.get('recovery_status', 'idle'),
                'requested_at':state.get('recovery_requested_at'),
                'completed_at':state.get('recovery_completed_at'),
                'error':state.get('recovery_error', ''),
                'last_send_error':state.get('last_send_error', ''),
                'consecutive_failures':state.get('consecutive_failures', 0)}

    def save(self, account, **values):
        guard = self.runtime.safety
        with guard.store.lock:
            state = guard.state(account)
            state.update(values)
            guard._save(account, state)
            return state

    def adopt_legacy(self):
        """Recover the existing send-failure hold once after this upgrade."""
        r = self.runtime
        s = r.config.settings
        state = r.safety.state(s.account_id)
        if (s.enabled and s.send_auto_recovery_enabled
                and state.get('hold_code') in SEND_HOLDS
                and not state.get('recovery_used')):
            self.save(s.account_id, hold_code='', hold_reason='', hold_since=None,
                      recovery_status='pending', recovery_used=True,
                      recovery_requested_at=time.time(), recovery_error='')

    async def run(self, *, manual=False, resume=True):
        r = self.runtime
        async with self.lock:
            s = r.config.settings
            account, generation = s.account_id, r.config.generation
            state = r.safety.state(account)
            prior_state = dict(state)
            if not manual and state.get('recovery_status') != 'pending':
                return self.snapshot()
            if not manual and (not s.enabled or not s.send_auto_recovery_enabled):
                if s.enabled and not s.send_auto_recovery_enabled:
                    self.save(account, recovery_status='failed', recovery_error='自动恢复已关闭')
                    r.safety.hold(account, 'failure_circuit', '连续发送失败，自动恢复已关闭；请在顶部“异常与恢复”处理')
                return self.snapshot()
            if state.get('hold_reason') and state.get('hold_code') not in SEND_HOLDS:
                if not manual:
                    return self.snapshot()
            if not manual and r.paused_reason:
                return self.snapshot()
            self.busy = True
            self.save(account, recovery_status='recovering', recovery_used=True, recovery_error='')
            try:
                async with r.chat_turn_lock:
                    auth = await r.gateway.status()
                    if auth.get('status') != 'logged_in' or auth.get('loggedInUser') != account:
                        raise ValueError('微信未登录授权账号，请在顶部登录入口处理')
                    notice = await r.gateway.safety_notice()
                    from .safety import client_warning
                    if client_warning(notice):
                        raise ValueError('微信仍有异常提示，需要先在微信中处理')
                    state = r.safety.state(account)
                    chat = state.get('recovery_chat')
                    if chat not in s.allowed_chat_ids:
                        chat = next(iter(s.allowed_chat_ids), '')
                    if not chat:
                        raise ValueError('尚未配置授权会话，无法执行界面恢复')
                    reset = getattr(r.gateway, 'reset_ui', None)
                    if reset is None:
                        raise ValueError('当前网关未提供界面恢复功能')
                    result = await reset(chat, account)
                    if not isinstance(result, dict) or result.get('main_ready') is not True:
                        raise ValueError('微信聊天主界面尚未恢复')
                    await r.stickers.refresh_available(r.gateway)
                    r.sticker_library.sync(account)
                    if generation != r.config.generation or account != r.config.settings.account_id:
                        raise ValueError('恢复期间设置已变化，请重新操作')
                    current = r.safety.state(account)
                    if (current.get('hold_reason') and current.get('hold_code') not in SEND_HOLDS
                            and (not manual or (current.get('hold_code'), current.get('hold_since'))
                                 != (state.get('hold_code'), state.get('hold_since')))):
                        raise ValueError('恢复期间出现新的暂停原因，请先处理微信提示')
                    values = dict(recovery_status='awaiting_send', recovery_completed_at=time.time(),
                                  recovery_error='', recovery_used=True)
                    if resume:
                        values.update(hold_reason='', hold_code='', hold_since=None)
                        if manual:
                            r.paused_reason = ''
                    else:
                        values['recovery_status'] = 'refreshed'
                        values['recovery_used'] = bool(prior_state.get('recovery_used') or prior_state.get('hold_reason'))
                    self.save(account, **values)
                    r.safety.record(account, 'ui_recovery_done',
                        '已返回微信聊天主界面并刷新会话、表情位置；继续处理新消息，不补发旧消息'
                        if resume else '管理员已刷新微信界面，原有暂停保持', level='info')
                    return {**self.snapshot(), 'busy':False}
            except asyncio.CancelledError:
                self.save(account, recovery_status='failed', recovery_error='界面恢复中断')
                r.safety.hold(account, 'ui_recovery_failed', '界面恢复中断，请在顶部“异常与恢复”重试')
                raise
            except Exception as exc:
                reason = str(exc)[:220] if isinstance(exc, ValueError) else '微信界面恢复未完成：' + type(exc).__name__
                self.save(account, recovery_status='failed', recovery_error=reason)
                r.safety.hold(account, 'ui_recovery_failed', reason)
                if manual:
                    raise ValueError(reason) from exc
                return self.snapshot()
            finally:
                self.busy = False

    async def loop(self):
        r = self.runtime
        state = r.safety.state(r.config.settings.account_id)
        if state.get('recovery_status') == 'recovering':
            self.save(r.config.settings.account_id, recovery_status='failed', recovery_error='上次界面恢复被服务重启中断')
            r.safety.hold(r.config.settings.account_id, 'ui_recovery_failed', '上次界面恢复中断，请在顶部“异常与恢复”处理')
        self.adopt_legacy()
        self.event.set()
        while True:
            try:
                await asyncio.wait_for(self.event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self.event.clear()
            self.adopt_legacy()
            await self.run()
