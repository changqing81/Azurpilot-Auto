"""大世界任务跳过塞壬研究的回归测试。

锁定「不做塞壬研究任务」开关的三条行为：
1. 任务名判定只认「塞壬研究」前缀，塞壬研究·I 与 ·II 一视同仁；
2. 领取前逐行 OCR 任务名，识别到塞壬研究就跳过，不再依赖「研究」两字的模板位置；
3. OCR 读不出文字时退回「研究」模板判定，宁可少领一次，也不要把塞壬研究领掉。

历史问题（2026-10-26 / 2026-10-29）：
只匹配「研究」两字时，塞壬研究·I 与 ·II 任务名长度不同，「研究」二字在屏幕上
左右移动，II 的相似度容易掉到阈值以下被漏判，进而被当成普通任务领取。
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

    def test_other_mission(self):
        self.assertFalse(mission_module.is_siren_research_mission('击破敌舰'))

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


if __name__ == '__main__':
    unittest.main()
