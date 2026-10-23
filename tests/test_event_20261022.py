"""飓风与雾霭回廊（event_20261022_cn）的关卡接入与 SP 固定敌人回归。

用例借鉴自 wess09/AzurPilot 提交 83d0c31e
（feat(campaign): 适配飓风与雾霭回廊并更新舰船资料）。

与上游原用例的差异（本地化改写）：

- 上游用 \`campaign.navigation\` 这个聚合对象记录导航调用顺序，本地没有该属性，
  改为把四个导航方法逐个替换成 Mock 后分别检查调用记录。
- 上游通过 \`config.override(SERVER=...)\` 在 cn/en/jp 之间切换；本地服务器由
  \`module.config.server\` 全局决定，且本活动的导航分支（page_sp / ASIDE_SWITCH）
  与 SERVER 无关，因此不逐服跑，只在 docstring 记录该差异。
- 配置沿用上游写法 \`AzurLaneConfig('template')\` + \`merge(地图文件.Config())\`：
  直接用 \`Config()\` 会缺少 ManualConfig 提供的 MAP_CHAPTER_SWITCH_20260326 等属性。
"""

import copy
import importlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock

from campaign.event_20261022_cn import sp, t1
from module.config.config import AzurLaneConfig


EVENT = 'event_20261022_cn'
ROOT = Path(__file__).resolve().parents[1]

# 导航方法名：上游在 navigation 上聚合，本地逐个替换
NAV_METHODS = (
    'ui_goto_sp',
    'campaign_ensure_mode_20241219',
    'campaign_ensure_aside_20241219',
    'campaign_ensure_chapter',
)


def make_campaign(map_module=t1):
    """构造可测试的 Campaign：真实配置 + 真实地图，仅替换导航与设备交互。"""
    config = AzurLaneConfig('template')
    config.merge(map_module.Config())
    campaign = object.__new__(map_module.Campaign)
    campaign.config = config
    campaign.device = Mock()
    for name in NAV_METHODS:
        setattr(campaign, name, Mock(name=name))
    return campaign


def nav_calls(campaign):
    """按固定顺序取出导航调用记录，便于与上游断言对齐。"""
    return [getattr(campaign, name).call_args for name in NAV_METHODS]


class WindFogCorridorTests(unittest.TestCase):
    """验证 T1~T6 上下篇分区、SP 独立分区与非法关卡拒绝导航。"""

    def test_stage_routes_use_correct_part_for_t1_to_t6(self):
        for stage, aside in ((1, 'part1'), (2, 'part1'), (3, 'part1'),
                             (4, 'part2'), (5, 'part2'), (6, 'part2')):
            with self.subTest(stage=stage):
                module = importlib.import_module(f'campaign.{EVENT}.t{stage}')
                campaign = make_campaign(module)
                campaign.campaign_set_chapter(f't{stage}')
                calls = nav_calls(campaign)
                # 上篇 T1~T3 走 part1，下篇 T4~T6 走 part2
                self.assertEqual(calls[0], ((),))                       # ui_goto_sp()
                self.assertEqual(calls[1], (('combat',),))              # ensure_mode
                self.assertEqual(calls[2], ((aside,),))                 # ensure_aside
                self.assertEqual(calls[3], (('t',),))                   # ensure_chapter
                self.assertFalse(campaign.config.MAP_HAS_MODE_SWITCH)
                campaign.device.click.assert_not_called()

    def test_daily_sp_uses_sp_partition_without_difficulty_switch(self):
        campaign = make_campaign(sp)
        campaign.campaign_set_chapter('sp')
        calls = nav_calls(campaign)
        self.assertEqual(calls[0], ((),))                               # ui_goto_sp()
        self.assertEqual(calls[1], (('combat',),))
        self.assertEqual(calls[2], (('sp',),))                          # SP 分区
        self.assertEqual(calls[3], (('ex_sp',),))
        self.assertTrue(campaign.config.MAP_IS_ONE_TIME_STAGE)
        self.assertFalse(campaign.config.MAP_HAS_MODE_SWITCH)

    def test_invalid_t_stage_falls_back_to_warning_branch(self):
        """上游 bd3926de2（Lme 实现）对未知关卡改为 warning，不再抛 CampaignNameError。

        真实行为：chapter=='t' 先无条件进入活动页（ui_goto_sp + combat 模式），
        stage 不在 1~6 时不再切侧边栏，只记 warning。锚定「不切侧边栏」这一
        关键外部可见行为，防止把 warning 分支误改成抛异常或误切分区。
        """
        campaign = make_campaign()
        campaign.campaign_set_chapter('t7')
        campaign.ui_goto_sp.assert_called_once()
        campaign.campaign_ensure_mode_20241219.assert_called_once_with('combat')
        # 无效关卡不得切换侧边栏，但章节仍切到 T（stage 无效由 warning 兜底）
        campaign.campaign_ensure_aside_20241219.assert_not_called()
        campaign.campaign_ensure_chapter.assert_called_once_with('t')

    def test_sp_uses_siren_template_not_fixed_spawn(self):
        """上游 bd3926de2（Lme 实现）改用 TempestaGhostBoss02 模板识别 SP 塞壬。

        旧 83d0c31e 实现按 Lua 固定出生格把 may_siren 标成 is_siren；
        上游最终态改为模板匹配，因此这里断言模板配置与不可移动敌人生效。
        """
        campaign = make_campaign(sp)
        self.assertEqual(campaign.config.MAP_SIREN_TEMPLATE, ['TempestaGhostBoss02'])
        self.assertFalse(campaign.config.MAP_HAS_MOVABLE_ENEMY)
        self.assertFalse(campaign.config.MAP_SIREN_HAS_BOSS_ICON_SMALL)
        self.assertTrue(campaign.config.MAP_IS_ONE_TIME_STAGE)

    def test_registered_event_uses_shared_directory_for_cn_en_jp(self):
        args = json.loads(
            (ROOT / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        for task in ('Event', 'Event2', 'Event3', 'EventA', 'EventB',
                     'EventC', 'EventD', 'EventSp'):
            for server in ('cn', 'en', 'jp'):
                with self.subTest(task=task, server=server):
                    self.assertEqual(
                        args[task]['Campaign']['Event'][f'option_{server}'],
                        [EVENT],
                    )

    def test_localized_event_names_survive_config_generation(self):
        for language, expected in (
            ('zh-CN', '飓风与雾霭回廊'),
            ('en-US', 'Tempesta and the Corridor of Mist'),
            ('ja-JP', 'テンペスタと霧靄回廊'),
        ):
            with self.subTest(language=language):
                path = ROOT / f'module/config/i18n/{language}.json'
                translated = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(translated['Campaign']['Event'][EVENT], expected)


if __name__ == '__main__':
    unittest.main()
