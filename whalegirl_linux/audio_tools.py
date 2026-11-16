"""Native optional audio tools; model decisions create jobs, never claim delivery."""
import json

from pydantic import ValidationError


class AudioTools:
    names = {'send_voice': 'speech', 'create_music': 'music'}

    def __init__(self, service):
        self.service = service

    def schemas(self, engine, first):
        if (getattr(engine, 'api_style', '') != 'openai' or getattr(engine, 'stream_enabled', False)
                or not hasattr(engine, 'chat_with_tools')):
            return []
        snapshot = self.service.settings.snapshot()
        _, settings = self.service.settings.current()
        tools = []
        for name, kind in self.names.items():
            profile = settings.speech_profile if kind == 'speech' else settings.music_profile
            if (not self.service.permitted(kind, first['chat']) or not snapshot['keys_configured'].get(profile)):
                continue
            if settings.providers[profile].backend == 'pc_bridge' and not getattr(self.service.provider, 'pc_ready', lambda: False)():
                # Connectivity is refreshed by the independent audio loop. Never
                # add a network wait to ordinary chat or advertise an offline PC.
                continue
            if kind == 'speech':
                properties = {'text': {'type': 'string', 'minLength': 1,
                    'maxLength': 600 if settings.providers[profile].backend == 'pc_bridge' else 1200,
                    'description': '本次实际要说出的自然话语，沿用当前人设，不含工具说明或朗读指令。'}}
                if settings.providers[profile].backend == 'pc_bridge':
                    languages = getattr(self.service.provider, 'pc_languages', lambda: None)()
                    if not languages:
                        languages = ['auto', 'zh', 'yue', 'en', 'ja', 'ko']
                    properties['language'] = {'type': 'string', 'enum': languages,
                        'description': '本次实际发音的语种。只选择当前声线支持的选项；跨语种混说或不确定用auto。省略则自动识别。'}
                if settings.ai_voice_dynamics_enabled:
                    controls = {
                        'speed': {'type': 'number', 'minimum': .85, 'maximum': 1.2,
                            'description': '相对基础声线的语速倍数。活泼短句可稍快，安慰或晚安可稍慢；省略为1。'},
                        'pitch': {'type': 'number', 'minimum': -1.5, 'maximum': 1.5,
                            'description': '相对基础音高的半音变化，小幅选择；省略为0。'},
                        'gain_db': {'type': 'number', 'minimum': -4, 'maximum': 3,
                            'description': '相对基础音量的dB变化。轻声可略低，兴奋可略高；省略为0。'},
                    }
                    if settings.providers[profile].backend == 'pc_bridge':
                        controls['pause_seconds'] = {'type': 'number', 'minimum': .12, 'maximum': .5,
                            'description': '本地模型句片段之间的停顿秒数，默认0.3；不是每个字之间的间隔。'}
                    properties['delivery'] = {'type': 'object', 'additionalProperties': False,
                        'properties': controls, 'description': '可选的本句说话方式，按语境轻微变化，不改变声线或永久设置。'}
                if settings.ai_preset_ids:
                    ids = settings.ai_preset_ids
                    if settings.providers[profile].backend == 'pc_bridge':
                        active = getattr(self.service.provider, 'pc_presets', lambda: None)()
                        if active is not None:
                            ids = [key for key in ids if settings.presets[key].voice in active]
                    if ids:
                        properties['voice_preset_id'] = {'type': 'string', 'enum': ids,
                            'description': '可省略，默认沿用管理员选定的声线。'}
                required = ['text']
                description = ('把本次回复合成为微信语音。根据当前消息判断语音是否合适，也可以继续文字或旁观。'
                    '此工具异步排队，成功受理不等于已发送；不重复输出一份相同文字。'
                    '只提交实际说出的内容，不把记忆资料或角色卡整段朗读。')
                mode = settings.voice_mode
            else:
                properties = {
                    'prompt': {'type': 'string', 'minLength': 1, 'maxLength': 1600,
                        'description': '原创音乐的曲风、乐器、人声、语言和情绪。不能承诺原曲翻唱或固定歌手声音。'},
                    'lyrics': {'type': 'string', 'maxLength': 5000, 'description': '可选原创或用户提供的歌词。'},
                    'instrumental': {'type': 'boolean', 'description': 'true为纯音乐，false为带人声歌曲。'},
                    'duration': {'type': 'integer', 'minimum': 5, 'maximum': 180, 'description': '期望秒数，实际时长由模型决定。'},
                    'delivery': {'type': 'string', 'enum': ['file', 'voice'], 'description': '完整歌曲优先file；只唱一句可用voice。'},
                }
                required = ['prompt']
                description = ('异步创作歌曲或纯音乐并发送；根据当前消息选择是否调用。'
                    '每次生成收费，受理后不重复提交；短唱可选择语音，完整作品默认音频文件。'
                    '生成没有完成前不得声称已唱完或已发出。')
                mode = settings.music_mode
            if mode == 'requested':
                description += '管理员当前选择仅在用户明确要求该能力时调用。'
            description += '本轮如调用音频工具，单独调用一次；需要检索资料时先完成检索。'
            tools.append({'type': 'function', 'function': {'name': name, 'description': description,
                'parameters': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}}})
        return tools

    def handle(self, result, first, generation, *, batch=None):
        calls = result.get('tool_calls') or []
        audio = [call for call in calls if isinstance(call, dict) and (call.get('function') or {}).get('name') in self.names]
        if not audio:
            return None
        if len(audio) != 1 or len(calls) != 1:
            return {'ok': False, 'state': 'rejected', 'reason': '本轮混合或重复音频工具，未提交收费请求'}
        if generation != self.service.config.generation:
            return {'ok': False, 'state': 'rejected', 'reason': '聊天配置变化，未提交音频任务'}
        function = audio[0]['function']
        try:
            arguments = json.loads(function.get('arguments') or '{}')
            if not isinstance(arguments, dict):
                raise ValueError()
            job = self.service.enqueue(self.names[function['name']], arguments, first=first)
            self.service.bind_sources(job['id'], batch or [first])
        except (ValueError, ValidationError, TypeError):
            return {'ok': False, 'state': 'rejected', 'reason': '音频参数、密钥或会话权限未通过检查，未生成'}
        return {'ok': True, 'id': job['id'], 'state': job['state'], 'sent': job['state'] == 'sent',
                'kind': job['kind'], 'reason': '音频任务已受理，实际送达由任务回执确认'}
