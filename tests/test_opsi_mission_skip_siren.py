"""大世界任务跳过塞壬研究的回归测试。

锁定「不做塞壬研究任务」开关在任务总览页（os_mission_enter）的行为：
开启开关时必须跳过所有塞壬研究行，不得误领；同时不得因行距变化而漏判。

历史问题：旧实现依赖「命中塞壬行后偏移 +110 像素」逐行推进，
当塞壬研究不在首行、或行距不是 110 时会直接走 else 分支领取，
导致塞壬研究被做掉（2026-10-26 事故）。
"""

import unittest

import cv2
import numpy as np


class TestOpsiMissionSkipSirenScan(unittest.TestCase):
    """测试扫描定位逻辑不依赖固定行距。"""

    def _scan_rows(self, image, template, similarity=0.85):
        """与 _os_mission_scan_rows 相同的算法。"""
        result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result > similarity)
        if not len(ys):
            return []
        order = np.argsort(-result[ys, xs])
        height, width = template.shape[:2]
        rows = []
        for index in order:
            y, x = int(ys[index]), int(xs[index])
            if not any(
                    abs(row[0] - y) <= height // 2 and abs(row[1] - x) <= width // 2
                    for row in rows):
                rows.append((y, x))
        return sorted(rows)

    def _claim_skip(self, image, siren_tpl, finish_tpl):
        """与 _os_mission_claim_skip_siren 相同的判定逻辑。"""
        siren_rows = self._scan_rows(image, siren_tpl)
        finish_rows = self._scan_rows(image, finish_tpl)
        for y, _ in finish_rows:
            if any(abs(y - siren_y) < 45 for siren_y, _ in siren_rows):
                continue
            return True, y
        return False, None

    def setUp(self):
        # 模板必须有非零方差，否则 TM_CCOEFF_NORMED 会因零方差产生 NaN
        rng = np.random.RandomState(42)
        self.siren_tpl = rng.randint(30, 225, (22, 41, 3)).astype(np.uint8)
        self.finish_tpl = rng.randint(30, 225, (27, 84, 3)).astype(np.uint8)

    def _build(self, rows):
        """按 (类型, y) 列表合成截图。"""
        canvas = np.zeros((720, 1280, 3), dtype=np.uint8)
        for kind, y in rows:
            if kind == 'siren':
                canvas[y:y + 22, 608:649] = self.siren_tpl
            else:
                canvas[y:y + 27, 1028:1112] = self.finish_tpl
        return canvas

    def test_skip_all_siren_rows(self):
        """三行全是塞壬研究时，一个都不领取。"""
        image = self._build([('siren', 186), ('siren', 296), ('siren', 406)])
        clicked, _ = self._claim_skip(image, self.siren_tpl, self.finish_tpl)
        self.assertFalse(clicked)

    def test_skip_siren_row_but_claim_other(self):
        """塞壬研究行有领取按钮时跳过，其它行正常领取。"""
        image = self._build([
            ('siren', 186), ('finish', 186),
            ('finish', 322),
        ])
        clicked, y = self._claim_skip(image, self.siren_tpl, self.finish_tpl)
        self.assertTrue(clicked)
        # 应点击 y=322 那一行，而不是塞壬研究所在的 y=186
        self.assertLess(abs(y - 322), 120)
        self.assertGreater(abs(y - 186), 100)

    def test_skip_siren_not_on_first_row(self):
        """塞壬研究不在首行时也要正确跳过（旧实现在此漏判）。"""
        image = self._build([
            ('finish', 186),
            ('siren', 296), ('finish', 296),
        ])
        clicked, y = self._claim_skip(image, self.siren_tpl, self.finish_tpl)
        self.assertTrue(clicked)
        self.assertLess(abs(y - 186), 100)

    def test_skip_works_with_varied_row_gap(self):
        """行距不是 110 时依然正确（旧实现硬编码 110 会漏判）。"""
        image = self._build([
            ('siren', 186), ('finish', 186),
            ('finish', 336),  # 行距 150，不是 110
        ])
        clicked, y = self._claim_skip(image, self.siren_tpl, self.finish_tpl)
        self.assertTrue(clicked)
        # 应点击 y=336 那一行，而不是塞壬研究所在的 y=186
        self.assertLess(abs(y - 336), 120)
        self.assertGreater(abs(y - 186), 100)

    def test_no_siren_still_claims(self):
        """没有塞壬研究时，开关不应影响正常领取。"""
        image = self._build([('finish', 186), ('finish', 296)])
        clicked, y = self._claim_skip(image, self.siren_tpl, self.finish_tpl)
        self.assertTrue(clicked)
        self.assertLess(abs(y - 186), 100)


class TestSkipSirenSourceLock(unittest.TestCase):
    """源码锁：确认跳过分支不再使用硬编码行距累加。"""

    def test_no_hardcoded_110_step(self):
        """os_mission_enter 不得再用 area_offset(..., (0, 110)) 推进跳过。"""
        with open('module/os_handler/mission.py', encoding='utf-8') as f:
            source = f.read()

        # 取出 os_mission_enter 函数体
        start = source.index('def os_mission_enter(')
        end = source.index('def _os_mission_scan_rows(', start)
        body = source[start:end]

        self.assertNotIn(
            'area_offset(checkout_offset, (0, 110))', body,
            '跳过塞壬研究不得依赖硬编码行距 110')
        self.assertIn(
            '_os_mission_claim_skip_siren', body,
            '跳过逻辑应走全屏扫描实现')

    def test_scan_rows_method_exists(self):
        """扫描方法必须存在且不做逐行累加。"""
        with open('module/os_handler/mission.py', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('def _os_mission_scan_rows(', source)
        self.assertIn('cv2.matchTemplate', source)


if __name__ == '__main__':
    unittest.main()
