"""大世界任务跳过塞壬研究的回归测试。

锁定「不做塞壬研究任务」开关的五条行为：
1. 任务名判定只认「塞壬研究」前缀，塞壬研究·I 与 ·II 一视同仁；
2. 领取前逐行 OCR 任务名，识别到塞壬研究就跳过，不再依赖「研究」两字的模板位置；
3. OCR 读不出文字时退回「研究」模板判定，宁可少领一次，也不要把塞壬研究领掉；
4. 任务板接取侧同样要跳过塞壬研究：任务板出现塞壬研究时不点「一键接受」，
   改为逐行接受并跳过（塞壬研究在被接受的那一刻就完成兑换，扣作战补给凭证）；
5. 接取任务时选择任务结算行也要排除塞壬研究行，避免点「查看」把任务做掉。

历史问题：
- 2026-10-26：只匹配「研究」两字时，塞壬研究·I 与 ·II 任务名长度不同，
  「研究」二字在屏幕上左右移动，II 的相似度容易掉到阈值以下被漏判。
- 2026-10-29 / 2026-11：领奖侧已经正确跳过，但任务板接取侧没有保护，
  「一键接受」把整板任务（含塞壬研究）一起接走，塞壬研究照样被做掉。
"""

import unittest

import module.os_handler.mission as mission_module


class TestSirenResearchName(unittest.TestCase):
    """任务名判定：只认「塞壬研究」，不区分编号。"""

    def test_siren_research_i(self):
        self.assertTrue(mission_module.is_siren_research_mission('塞壬研究·I'))

    def test_siren_research_ii(self):
        self.assertTrue(mission_module.is_siren_research_mission('塞壬研究·II'))

    def test_siren_research_roman_unicode(self):
        self.assertTrue(mission_module.is_siren_research_mission('塞壬研究·Ⅱ'))

    def test_siren_research_with_spaces(self):
        self.assertTrue(mission_module.is_siren_research_mission('塞壬 研究 ·II'))

    def test_ocr_confuses_se_qi(self):
        """OCR 把「塞壬」读成「塞千」时仍要判定为塞壬研究。"""
        self.assertTrue(mission_module.is_siren_research_mission('塞千研究·II'))

    def test_research_keyword_fallback(self):
        """「塞壬」被读错但「研究」还在时，保守判定为塞壬研究。"""
        self.assertTrue(mission_module.is_siren_research_mission('塞壬研究中心·II'))
        self.assertTrue(mission_module.is_siren_research_mission('（研究）某项任务'))

    def test_ocr_confuses_yan(self):
        """「研」被读成异体字「硏」或「妍」时仍要判定为塞壬研究。"""
        self.assertTrue(mission_module.is_siren_research_mission('塞壬硏究·II'))
        self.assertTrue(mission_module.is_siren_research_mission('塞壬妍究·II'))

    def test_ocr_confuses_jiu(self):
        """「究」被读成「穷」「充」时仍要判定为塞壬研究。"""
        self.assertTrue(mission_module.is_siren_research_mission('塞壬研穷·II'))
        self.assertTrue(mission_module.is_siren_research_mission('塞壬研充·II'))
        self.assertTrue(mission_module.is_siren_research_mission('塞千研穷·II'))

    def test_ocr_confuses_ren_to_wang(self):
        """「壬」被读成「王」时仍要判定为塞壬研究。"""
        self.assertTrue(mission_module.is_siren_research_mission('塞王研究·II'))

    def test_other_mission(self):
        self.assertFalse(mission_module.is_siren_research_mission('击破敌舰'))
        self.assertFalse(mission_module.is_siren_research_mission('补给兑换·II'))
        self.assertFalse(mission_module.is_siren_research_mission('LL海域探索II'))

    def test_empty(self):
        self.assertFalse(mission_module.is_siren_research_mission(''))
        self.assertFalse(mission_module.is_siren_research_mission(None))


class TestOpsiMissionRowCheck(unittest.TestCase):
    """逐行判定与领取行为。"""

    def setUp(self):
        self.handler = mission_module.MissionHandler.__new__(mission_module.MissionHandler)

    def _mock_rows(self, rows):
        self.handler._os_mission_scan_rows = (
            lambda button, similarity=0.85: rows if button is mission_module.MISSION_FINISH else [])

    def test_row_is_siren_by_ocr_ii(self):
        """OCR 认出「塞壬研究·II」时判定为塞壬研究行。"""
        self.handler._os_mission_row_name = lambda y: '塞壬研究·II'
        self.handler._os_mission_scan_rows = lambda *args, **kwargs: []
        self.assertTrue(self.handler._os_mission_row_is_siren(205))

    def test_row_is_siren_by_ocr_i(self):
        self.handler._os_mission_row_name = lambda y: '塞壬研究·I'
        self.handler._os_mission_scan_rows = lambda *args, **kwargs: []
        self.assertTrue(self.handler._os_mission_row_is_siren(205))

    def test_row_normal_mission(self):
        self.handler._os_mission_row_name = lambda y: '击破敌舰'
        self.handler._os_mission_scan_rows = lambda *args, **kwargs: []
        self.assertFalse(self.handler._os_mission_row_is_siren(205))

    def test_row_ocr_empty_falls_back_to_template(self):
        """OCR 没读出文字时退回模板判定。"""
        self.handler._os_mission_row_name = lambda y: ''
        self.handler._os_mission_scan_rows = lambda *args, **kwargs: [(186, 608)]
        self.assertTrue(self.handler._os_mission_row_is_siren(205))
        self.assertFalse(self.handler._os_mission_row_is_siren(400))

    def _mock_device(self):
        clicked = []

        class _Device:
            @staticmethod
            def click(button):
                clicked.append(button.area)

        self.handler.device = _Device()
        return clicked

    def test_claim_skips_siren_rows_and_claims_other(self):
        """塞壬研究行（含 II）全部跳过，普通任务行照常领取。"""
        self._mock_rows([(205, 1030), (315, 1030), (425, 1030)])
        self.handler._os_mission_row_is_siren = lambda y: y in (205, 315)
        clicked = self._mock_device()

        self.assertTrue(self.handler._os_mission_claim_skip_siren())
        self.assertEqual(len(clicked), 1)
        self.assertEqual(clicked[0][1], 425)

    def test_claim_all_siren_returns_false(self):
        """只剩塞壬研究时一个都不领。"""
        self._mock_rows([(205, 1030), (315, 1030)])
        self.handler._os_mission_row_is_siren = lambda y: True
        clicked = self._mock_device()

        self.assertFalse(self.handler._os_mission_claim_skip_siren())
        self.assertEqual(clicked, [])

    def test_claim_no_rows(self):
        self._mock_rows([])
        clicked = self._mock_device()
        self.assertFalse(self.handler._os_mission_claim_skip_siren())
        self.assertEqual(clicked, [])


class TestSkipSirenSourceLock(unittest.TestCase):
    """源码锁：确认跳过逻辑走整行 OCR 判定。"""

    def _read(self):
        with open('module/os_handler/mission.py', encoding='utf-8') as f:
            return f.read()

    def test_claim_uses_row_ocr_check(self):
        source = self._read()
        start = source.index('def _os_mission_claim_skip_siren(')
        end = source.index('def os_mission_quit(', start)
        body = source[start:end]
        self.assertIn('_os_mission_row_is_siren', body)
        self.assertIn('_os_mission_scan_rows(MISSION_FINISH)', body)

    def test_row_check_uses_ocr_and_keyword(self):
        source = self._read()
        self.assertIn('def _os_mission_row_name(', source)
        self.assertIn("lang='cnocr'", source)
        self.assertIn('is_siren_research_mission', source)

    def test_no_hardcoded_110_step(self):
        """跳过逻辑不得再用 area_offset(..., (0, 110)) 逐行推进。"""
        source = self._read()
        start = source.index('def os_mission_enter(')
        end = source.index('def _os_mission_scan_rows(', start)
        body = source[start:end]
        self.assertNotIn('area_offset(checkout_offset, (0, 110))', body)
        self.assertIn('_os_mission_claim_skip_siren', body)

    def test_scan_rows_method_exists(self):
        source = self._read()
        self.assertIn('def _os_mission_scan_rows(', source)
        self.assertIn('cv2.matchTemplate', source)

    # ===== 任务板接取侧（2026-11 事故）=====

    def test_overview_accept_guards_siren(self):
        """任务板接取在开关开启时不得无条件点「一键接受」。"""
        source = self._read()
        start = source.index('def os_mission_overview_accept(')
        end = source.index('def is_in_opsi_explore(', start)
        body = source[start:end]
        self.assertIn('if skip_siren_mission:', body, '任务板接取必须先判断开关')
        self.assertIn('_os_mission_overview_rows', body, '应先扫描任务板逐行判定')
        self.assertIn('_os_mission_overview_accept_one', body, '有塞壬研究时应逐行接受')

    def test_overview_rows_defined(self):
        source = self._read()
        self.assertIn('def _os_mission_overview_rows(', source)
        self.assertIn('def _os_mission_overview_accept_one(', source)
        self.assertIn('OS_MISSION_OVERVIEW_NAME_AREA', source)

    def test_checkout_skips_siren_rows(self):
        """接取任务时选结算行必须能排除塞壬研究行。"""
        source = self._read()
        start = source.index('def _os_find_checkout_offset_skip_monthly_boss(')
        end = source.index('def get_mission_zone(', start)
        body = source[start:end]
        self.assertIn('skip_siren_mission', body)
        self.assertIn('MISSION_SIREN_RESEARCH', body)


class TestOverviewRowsDecision(unittest.TestCase):
    """任务板逐行判定的行为测试（不依赖真实截图）。"""

    def setUp(self):
        self.handler = mission_module.MissionHandler.__new__(mission_module.MissionHandler)
        self.handler.interval_timer = {}
        self.handler.OS_MISSION_OVERVIEW_NAME_AREA = mission_module.MissionHandler.OS_MISSION_OVERVIEW_NAME_AREA

    def _mock(self, scan_rows, names):
        self.handler._os_mission_scan_rows = lambda *a, **kw: scan_rows
        self.handler._os_mission_row_name = lambda y, area=None, name='': names.get(y, '')

    def test_rows_marks_siren_by_name(self):
        self._mock([(121, 1066), (234, 1066)], {121: '塞壬研究I', 234: '补给兑换II'})
        rows = self.handler._os_mission_overview_rows()
        self.assertEqual([r[1] for r in rows], [True, False])

    def test_rows_marks_ocr_failure_as_siren(self):
        """任务名读不出来时保守跳过该行。"""
        self._mock([(121, 1066)], {121: ''})
        rows = self.handler._os_mission_overview_rows()
        self.assertTrue(rows[0][1])

    def test_accept_one_skips_siren_row(self):
        clicked = []

        class _Device:
            @staticmethod
            def click(button):
                clicked.append(button.area)

        self.handler.device = _Device()
        rows = [(121, True, '塞壬研究I'), (234, False, '补给兑换II')]
        self.assertTrue(self.handler._os_mission_overview_accept_one(rows))
        self.assertEqual(len(clicked), 1)
        # 点的是 y=234 那一行
        self.assertEqual(clicked[0][1], 234)

    def test_accept_one_returns_false_when_all_siren(self):
        clicked = []

        class _Device:
            @staticmethod
            def click(button):
                clicked.append(button.area)

        self.handler.device = _Device()
        rows = [(121, True, '塞壬研究I'), (234, True, '塞壬研究II')]
        self.assertFalse(self.handler._os_mission_overview_accept_one(rows))
        self.assertEqual(clicked, [])


if __name__ == '__main__':
    unittest.main()
