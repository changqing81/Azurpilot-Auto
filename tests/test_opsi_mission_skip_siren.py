"""大世界任务跳过塞壬研究的回归测试。

锁定「不做塞壬研究任务」开关的四条行为：
1. 任务名判定只认「塞壬研究」前缀，塞壬研究·I 与 ·II 一视同仁；
2. 领取前逐行 OCR 任务名，识别到塞壬研究就跳过，不再依赖「研究」两字的模板位置；
3. 日常任务板接取前同样逐行 OCR，识别到塞壬研究就不点「接受」；
   任务板上存在塞壬研究时不得使用「一键接受」（它会把所有任务一起接掉）；
4. OCR 读不出文字时保守跳过，宁可少接一次，也不要把塞壬研究当普通任务处理。

历史问题：
- 2026-10-26：只匹配「研究」两字时，塞壬研究·I 与 ·II 任务名长度不同，
  「研究」二字在屏幕上左右移动，II 的相似度容易掉到阈值以下被漏判。
- 2026-10-29：领奖侧已经跳过，但「任务总览接取」仍在点「一键接受 + 接受」，
  塞壬研究在接取那一刻就完成兑换（消耗作战补给凭证），只拦领取拦不住。
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


class TestOpsiMissionOverviewSkipSiren(unittest.TestCase):
    """日常任务板接取行为：塞壬研究任务不接。

    塞壬研究任务在任务总览被「接受」时就会完成兑换（消耗作战补给凭证），
    所以必须在接取这一步拦住，只拦领取是拦不住的（2026-10-29 事故）。
    """

    def setUp(self):
        self.handler = mission_module.MissionHandler.__new__(mission_module.MissionHandler)

    def _mock_scan(self, rows):
        """只让「接受」按钮扫描返回结果，其余按钮返回空。"""
        self.handler._os_mission_scan_rows = (
            lambda button, similarity=0.85:
            rows if button is mission_module.MISSION_OVERVIEW_ACCEPT_SINGLE else [])

    def _mock_device(self):
        clicked = []

        class _Device:
            @staticmethod
            def click(button):
                clicked.append(button.area)

        self.handler.device = _Device()
        return clicked

    def test_rows_classified_by_ocr(self):
        """两行塞壬研究（I 与 II）都要判定成塞壬研究。"""
        self._mock_scan([(121, 1066), (272, 1066)])
        names = {121: '塞壬研究I', 272: '塞壬研究II'}
        self.handler._os_mission_row_name = lambda y, area=None: names[y]

        rows = self.handler._os_mission_overview_rows()
        self.assertEqual([row[0] for row in rows], [121, 272])
        self.assertTrue(all(row[1] for row in rows))

    def test_rows_classified_mixed(self):
        """混合任务板：只有塞壬研究行被标记。"""
        self._mock_scan([(121, 1066), (272, 1066)])
        names = {121: '塞壬研究II', 272: '击破敌舰'}
        self.handler._os_mission_row_name = lambda y, area=None: names[y]

        rows = self.handler._os_mission_overview_rows()
        self.assertTrue(rows[0][1])
        self.assertFalse(rows[1][1])

    def test_accept_one_skips_siren(self):
        """逐行接受时跳过塞壬研究行，只接受普通任务。"""
        self._mock_scan([(121, 1066), (272, 1066)])
        names = {121: '塞壬研究II', 272: '击破敌舰'}
        self.handler._os_mission_row_name = lambda y, area=None: names[y]
        clicked = self._mock_device()

        rows = self.handler._os_mission_overview_rows()
        self.assertTrue(self.handler._os_mission_overview_accept_one(rows))
        self.assertEqual(len(clicked), 1)
        self.assertEqual(clicked[0][1], 272)

    def test_accept_one_all_siren_returns_false(self):
        """全是塞壬研究时一个都不接。"""
        self._mock_scan([(121, 1066), (272, 1066)])
        self.handler._os_mission_row_name = lambda y, area=None: '塞壬研究I'
        clicked = self._mock_device()

        rows = self.handler._os_mission_overview_rows()
        self.assertFalse(self.handler._os_mission_overview_accept_one(rows))
        self.assertEqual(clicked, [])

    def test_accept_one_empty_rows(self):
        self._mock_scan([])
        clicked = self._mock_device()
        self.assertFalse(self.handler._os_mission_overview_accept_one([]))
        self.assertEqual(clicked, [])

    def test_ocr_empty_skips_conservatively(self):
        """任务名读不出时保守判定为塞壬研究，宁可不接也不误接。"""
        self.handler._os_mission_row_name = lambda y, area=None: ''
        self.handler._os_mission_scan_rows = (
            lambda button, similarity=0.85:
            [(121, 1066)] if button is mission_module.MISSION_OVERVIEW_ACCEPT_SINGLE else [])

        rows = self.handler._os_mission_overview_rows()
        self.assertTrue(rows[0][1])
        self.assertEqual(rows[0][2], '')


class TestOverviewAcceptSourceLock(unittest.TestCase):
    """源码锁：日常任务板接受必须查塞壬研究，且不能让一键接受绕过。"""

    def _read(self):
        with open('module/os_handler/mission.py', encoding='utf-8') as f:
            return f.read()

    def test_accept_loop_checks_siren_rows(self):
        source = self._read()
        start = source.index('def os_mission_overview_accept(')
        end = source.index('def is_in_opsi_explore(', start)
        body = source[start:end]
        self.assertIn('_os_mission_overview_rows', body)
        self.assertIn('has_siren', body)
        self.assertIn('_os_mission_overview_accept_one', body)

    def test_accept_all_gated_by_has_siren(self):
        """「一键接受」必须被 has_siren 挡住，否则会一次接掉所有塞壬研究。"""
        source = self._read()
        start = source.index('def os_mission_overview_accept(')
        end = source.index('def is_in_opsi_explore(', start)
        body = source[start:end]
        gate = body.index('if not has_siren:')
        click = body.index('appear_then_click(MISSION_OVERVIEW_ACCEPT,')
        self.assertLess(gate, click, '一键接受必须在「没有塞壬研究」分支内')

    def test_overview_name_area_defined(self):
        source = self._read()
        self.assertIn('OS_MISSION_OVERVIEW_NAME_AREA', source)


if __name__ == '__main__':
    unittest.main()
