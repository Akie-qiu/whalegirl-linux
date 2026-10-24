"""Role-card persistence, prompt compatibility and activation boundaries."""
from pathlib import Path
import tempfile
import unittest

from whalegirl_linux.personas import PersonaLibrary, PersonaSaveRequest, PersonaActionRequest, PersonaSpec, compile_persona
from whalegirl_linux.settings import Config, Settings

ROOT=Path(__file__).resolve().parents[1]


class PersonaTests(unittest.TestCase):
    def setUp(self):
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        self.directory=Path(tempfile.mkdtemp(prefix='personas-',dir=runs))
        self.config=Config(self.directory)
        self.library=PersonaLibrary(self.config,'原本的人设')

    def test_existing_prompt_is_preserved_without_config_save(self):
        self.config.save(Settings(system_prompt_override='原有自定义人设\n保持原样',context_messages=40))
        generation=self.config.generation;before=self.config.path.read_bytes()
        snapshot=self.library.snapshot()
        self.assertEqual(snapshot['effective_prompt'],'原有自定义人设\n保持原样')
        active=next(c for c in snapshot['cards'] if c['id']==snapshot['active_id'])
        self.assertEqual(active['card']['raw_prompt'],snapshot['effective_prompt'])
        self.assertEqual(self.config.path.read_bytes(),before)
        self.assertEqual(self.config.generation,generation)
        self.assertEqual(len(PersonaLibrary(self.config,'原本的人设').snapshot()['cards']),5)

    def test_draft_save_does_not_activate_and_activation_only_changes_persona(self):
        self.config.save(Settings(context_messages=40,quote_replies_enabled=True,moments_reply_own_enabled=True))
        before=self.config.settings.model_dump()
        spec=PersonaSpec(name='阿禾',identity='温柔的成年聊天伙伴',private_style='温柔亲近',group_style='公开场合有分寸')
        result=self.library.save(PersonaSaveRequest(card=spec))
        self.assertEqual(self.config.settings.model_dump(),before)
        identity=result['saved_id']
        self.library.action(identity,PersonaActionRequest(op='activate',revision=1))
        self.assertIn('公开场合有分寸',self.config.settings.system_prompt_override)
        self.assertIn('私聊中的相处方式',self.config.settings.system_prompt_override)
        self.assertEqual({k:v for k,v in self.config.settings.model_dump().items()
            if k not in ('active_persona_id','active_persona_revision','system_prompt_override')},
            {k:v for k,v in before.items() if k not in ('active_persona_id','active_persona_revision','system_prompt_override')})
        self.assertEqual(Config(self.directory).settings.active_persona_id,identity)

    def test_edit_active_draft_retains_published_version_and_old_revision_is_rejected(self):
        identity=self.library.save(PersonaSaveRequest(card=PersonaSpec(name='卡片',identity='旧身份')))['saved_id']
        self.library.action(identity,PersonaActionRequest(op='activate',revision=1))
        published=self.config.settings.system_prompt_override
        self.library.save(PersonaSaveRequest(id=identity,revision=1,card=PersonaSpec(name='卡片',identity='新身份')))
        self.assertEqual(self.config.settings.system_prompt_override,published)
        self.assertEqual(self.library.snapshot()['active_revision'],1)
        with self.assertRaises(ValueError):self.library.action(identity,PersonaActionRequest(op='activate',revision=1))
        self.library.action(identity,PersonaActionRequest(op='activate',revision=2))
        self.assertIn('新身份',self.config.settings.system_prompt_override)

    def test_builtin_protection_and_archive_restore(self):
        with self.assertRaises(ValueError):
            self.library.save(PersonaSaveRequest(id='builtin-whale',revision=1,card=PersonaSpec(name='改写',identity='身份')))
        identity=self.library.save(PersonaSaveRequest(card=PersonaSpec(name='自定义',identity='身份')))['saved_id']
        self.library.action(identity,PersonaActionRequest(op='archive',revision=1))
        with self.assertRaises(ValueError):self.library.action(identity,PersonaActionRequest(op='activate',revision=2))
        self.library.action(identity,PersonaActionRequest(op='restore',revision=2))
        self.library.action(identity,PersonaActionRequest(op='activate',revision=3))
        with self.assertRaises(ValueError):self.library.action(identity,PersonaActionRequest(op='archive',revision=3))
        self.library.action('builtin-whale',PersonaActionRequest(op='activate',revision=1))
        self.assertEqual(self.config.settings.system_prompt_override,'')

    def test_preview_validates_mode_and_prompt_length_without_model_calls(self):
        with self.assertRaises(ValueError):compile_persona(PersonaSpec(name='空角色'))
        self.assertEqual(compile_persona(PersonaSpec(name='旧卡',mode='raw',raw_prompt='精确保留提示词')),'精确保留提示词')
        with self.assertRaises(ValueError):compile_persona(PersonaSpec(name='空提示词',mode='raw'))


class PersonaApiTests(unittest.TestCase):
    def test_authenticated_save_preview_activate_and_legacy_prompt_compatibility(self):
        from fastapi.testclient import TestClient
        from whalegirl_linux.app import create_app
        from test_chat_habits import Gateway,Engine
        runs=ROOT/'test-runs';runs.mkdir(exist_ok=True)
        folder=Path(tempfile.mkdtemp(prefix='personas-api-',dir=runs))
        app=create_app(folder,gateway=Gateway(),engine_factory=Engine,run_background=False)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/personas').status_code,401)
            h={'Authorization':'Bearer '+app.state.config.admin_token}
            spec={'name':'API角色','identity':'成年群聊伙伴','group_style':'群聊有分寸'}
            preview=client.post('/api/personas/preview',headers=h,json=spec)
            self.assertEqual(preview.status_code,200);self.assertIn('群聊有分寸',preview.json()['prompt'])
            saved=client.post('/api/personas/save',headers=h,json={'card':spec})
            self.assertEqual(saved.status_code,200);identity=saved.json()['saved_id']
            self.assertEqual(client.post('/api/personas/'+identity+'/actions',headers=h,json={'op':'activate','revision':1}).status_code,200)
            client.post('/api/config',headers=h,json={'settings':{'context_messages':40}})
            self.assertEqual(app.state.config.settings.active_persona_id,identity)
            client.post('/api/prompt',headers=h,json={'system_prompt':'旧版入口手动改写'})
            state=client.get('/api/personas',headers=h).json()
            self.assertTrue(state['active_id'].startswith('imported-'))
            self.assertEqual(state['effective_prompt'],'旧版入口手动改写')
            self.assertEqual(client.get('/personas.js').status_code,200)
            self.assertEqual(client.get('/personas.css').status_code,200)


if __name__=='__main__':unittest.main()
