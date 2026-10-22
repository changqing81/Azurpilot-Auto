"""「活动结束后关卡」配置位置回归测试，不连接游戏。

它原本挂在 GemsFarming 组（界面上的"紧急委托"分组），2026-10-22 移到
Campaign 组（「关卡设置」），排在「道中失败处理」之后，与其它关卡选项同处一格。

覆盖：
- 参数定义确实在 Campaign 组，且 GemsFarming 组里不再有它；
- 只有低耗打捞类任务（GemsFarming / ThreeOilLowCost）在界面上显示，
  其余任务（含 Ambush11）隐藏 —— Campaign 组被 22 个任务共用；
- 存量配置的旧路径值会迁移到新路径，用户填过的 16-4 不会丢回 2-4。
"""

import unittest

from module.config.config_updater import ConfigUpdater
from module.config.utils import read_file


TASKS = ('GemsFarming', 'ThreeOilLowCost')


class TestEventFallbackStageLocation(unittest.TestCase):
    """参数位置：关卡设置（Campaign）组，紧跟道中失败处理。"""

    def setUp(self):
        self.args = read_file('module/config/argument/args.json')

    def test_argument_lives_in_campaign_group(self):
        for task in TASKS:
            with self.subTest(task=task):
                campaign = self.args[task]['Campaign']
                self.assertIn('EventFallbackStage', campaign)
                definition = campaign['EventFallbackStage']
                self.assertNotEqual(definition.get('display'), 'hide')
                self.assertEqual(definition['type'], 'input')
                self.assertEqual(str(definition['value']), '2-4')
                # 顺序：就在「道中失败处理」之后
                keys = list(campaign)
                self.assertEqual(
                    keys[keys.index('DefeatWithdraw') + 1], 'EventFallbackStage')

    def test_argument_removed_from_gems_farming_group(self):
        for task in TASKS + ('Ambush11',):
            with self.subTest(task=task):
                self.assertNotIn('EventFallbackStage', self.args[task]['GemsFarming'])

    def test_other_tasks_hide_the_argument(self):
        shown = []
        for task, groups in self.args.items():
            if not isinstance(groups, dict) or 'Campaign' not in groups:
                continue
            definition = groups['Campaign'].get('EventFallbackStage')
            if definition is None:
                continue
            if definition.get('display') != 'hide':
                shown.append(task)
        self.assertEqual(sorted(shown), sorted(TASKS),
                         '只有低耗打捞类任务该显示这个设置')

    def test_ambush11_is_hidden_and_pinned_to_zero(self):
        definition = self.args['Ambush11']['Campaign']['EventFallbackStage']
        self.assertEqual(definition.get('display'), 'hide')
        self.assertEqual(str(definition['value']), '0')

    def test_i18n_follows_the_new_group(self):
        for language in ('zh-CN', 'en-US', 'ja-JP', 'zh-TW'):
            with self.subTest(language=language):
                translated = read_file(f'module/config/i18n/{language}.json')
                entry = translated['Campaign']['EventFallbackStage']
                self.assertNotIn('.', entry['name'], 'name 未翻译')
                self.assertNotEqual(entry['help'], 'Campaign.EventFallbackStage.help')
                self.assertNotIn('EventFallbackStage', translated['GemsFarming'])


class TestEventFallbackStageMigration(unittest.TestCase):
    """存量配置：GemsFarming.EventFallbackStage -> Campaign.EventFallbackStage。"""

    def test_old_value_moves_to_new_path(self):
        updater = ConfigUpdater()
        for task in TASKS + ('Ambush11',):
            with self.subTest(task=task):
                old = {task: {'GemsFarming': {'EventFallbackStage': '16-4'}}}
                new = updater.config_update(old, is_template=False)
                new = updater.config_redirect(old, new)
                self.assertEqual(
                    new[task]['Campaign']['EventFallbackStage'], '16-4')

    def test_missing_old_value_keeps_default(self):
        updater = ConfigUpdater()
        old = {'ThreeOilLowCost': {'Campaign': {'Name': '12-4'}}}
        new = updater.config_update(old, is_template=False)
        new = updater.config_redirect(old, new)
        self.assertEqual(
            new['ThreeOilLowCost']['Campaign']['EventFallbackStage'], '2-4')


if __name__ == '__main__':
    unittest.main()
