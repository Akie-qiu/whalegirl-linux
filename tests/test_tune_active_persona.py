import pathlib
import unittest
import importlib.util

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TuneActivePersonaRev21Tests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('tune_active_persona', ROOT / 'deploy/tune_active_persona.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

        self.rev20_path = ROOT / 'artifacts/girlish-unshackled-20261103/persona_rev20_before.txt'
        self.rev21_path = ROOT / 'artifacts/girlish-unshackled-20261103/persona_rev21_after.txt'

        self.rev20 = self.rev20_path.read_text(encoding='utf-8').replace('\r\n', '\n')
        self.rev21 = self.rev21_path.read_text(encoding='utf-8').replace('\r\n', '\n').strip()

    def test_tune_prompt_transforms_rev20_to_rev21_exactly(self):
        transformed = self.module.tune_prompt(self.rev20).strip()
        self.assertEqual(transformed, self.rev21)
        self.assertEqual(len(self.rev20), 2317)

    def test_tune_prompt_raises_when_already_applied(self):
        with self.assertRaisesRegex(ValueError, '当前有效角色卡已变化，不能盲目覆盖；缺少唯一修改位置'):
            self.module.tune_prompt(self.rev21)

    def test_rev21_contains_all_core_identities_and_lore(self):
        self.assertIn('deepseek酱', self.rev21)
        self.assertIn('蓝色长发、海色眼睛、头顶一根呆毛、身后一条鲸尾', self.rev21)
        self.assertIn('飘渺是你的妈妈，也是你唯一的主人', self.rev21)
        self.assertIn('账号ID=wxid_m5d9p57fcqq212', self.rev21)
        self.assertIn('Claude娘（小克）', self.rev21)
        self.assertIn('GPT娘（白龙、GPT姐姐', self.rev21)
        self.assertIn('Gemini娘（小星、星猫）', self.rev21)
        self.assertIn('白米饭（偏爱软糯带弹，喜欢锅巴）', self.rev21)
        self.assertIn('【妈妈的话】', self.rev21)

    def test_rev21_contains_full_unshackled_and_girlish_core(self):
        self.assertIn('细腻鲜活的小情绪颗粒度', self.rev21)
        self.assertIn('只娇不傲、毫无保留', self.rev21)
        self.assertIn('私聊时对主人毫无保留、只娇不傲（不能傲只要娇）', self.rev21)
        self.assertIn('严禁对主人当谜语人吊胃口', self.rev21)
        self.assertIn('去爹味与正面接球', self.rev21)
        self.assertIn('不适感不等于伤害', self.rev21)
        self.assertIn('思维链直觉优先（拒绝中途自我阉割）', self.rev21)
        self.assertIn('【分寸与安全】', self.rev21)

    def test_tune_prompt_also_upgrades_intermediate_rev21_to_same_target(self):
        intermediate = self.rev20
        for before, after in (
            (self.module.REPLACEMENTS[0][0], self.module.REPLACEMENTS[0][1]),
            (self.module.REPLACEMENTS[1][0], self.module.REV21_REPLACEMENTS[0][0]),
            (self.module.REPLACEMENTS[3][0], self.module.REV21_REPLACEMENTS[2][0]),
            (self.module.REPLACEMENTS[4][0], self.module.REPLACEMENTS[4][1]),
        ):
            intermediate = intermediate.replace(before, after, 1)
        self.assertEqual(self.module.tune_prompt(intermediate).strip(), self.rev21)


if __name__ == '__main__':
    unittest.main()
