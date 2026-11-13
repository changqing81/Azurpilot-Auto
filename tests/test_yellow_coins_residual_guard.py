"""黄币读数残缺防护的行为测试，不连接游戏、不需要 OCR 模型。

覆盖：OCR 把数字读短（残缺）时的判定与复查、合法暴跌不误判、
重置清仓窗口跳过判定、复查兜底沿用基线、以及补黄币起始值不被脏读数污染。
"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


class GuardTestBase(unittest.TestCase):
    """构造一个不碰设备的 OSStatus 实例。"""

    def make_handler(self, last_yellow_coins=0, guard_enabled=True):
        from module.os_handler.os_status import OSStatus

        handler = OSStatus.__new__(OSStatus)
        handler.config = SimpleNamespace(
            data={'Dashboard': {'YellowCoin': {'Value': 0, 'Record': ''}}},
            modified={},
            save=lambda: None,
        )
        handler.device = SimpleNamespace(sleep=lambda s: None, image=None)
        handler._cache_lock = threading.Lock()
        handler._last_yellow_coins = last_yellow_coins
        handler.appear_then_click = lambda *args, **kwargs: False
        # loop() 给足迭代次数：可疑复查 3 帧 + 正常双读 2 帧
        handler.loop = lambda: iter(range(12))
        # 固定重置窗口判定，避免测试结果随真实日期漂移
        handler._is_os_reset_window = lambda: not guard_enabled
        return handler


class TestSuspiciousJudgement(GuardTestBase):
    """_is_yellow_coins_reading_suspicious 的判据。"""

    def setUp(self):
        self.handler = self.make_handler()

    def test_legit_drops_are_not_suspicious(self):
        """买行动力 / 清港口 / 商店进货都让黄币合法暴跌，不能判残缺。"""
        cases = [
            (10000, 2000, '买 200 行动箱子'),
            (30000, 3000, '手动清港口'),
            (80000, 8000, '脚本买港口物品'),
            (218990, 20000, '掉到五位数'),
            (218990, 218990, '余额没变'),
            (218990, 250000, '余额上涨'),
        ]
        for baseline, value, reason in cases:
            with self.subTest(reason=reason):
                self.assertFalse(
                    self.handler._is_yellow_coins_reading_suspicious(value, baseline),
                    f'{reason} 不该判残缺：{baseline} -> {value}',
                )

    def test_shortened_digits_are_suspicious(self):
        """位数明显变少才是残缺。"""
        cases = [
            (218990, 4, '2026-11-11 事故'),
            (218990, 42, '只露末两位'),
            (218990, 542, '只露末三位'),
            (105745, 530, '2026-11-13 的三位数误读'),
            (68955, 450, '2026-11-12 的三位数误读'),
        ]
        for baseline, value, reason in cases:
            with self.subTest(reason=reason):
                self.assertTrue(
                    self.handler._is_yellow_coins_reading_suspicious(value, baseline),
                    f'{reason} 应判残缺：{baseline} -> {value}',
                )

    def test_narrow_text_is_suspicious(self):
        """文本包围盒过窄直接判残缺（OCR 层的第一道门）。"""
        self.assertTrue(
            self.handler._is_yellow_coins_reading_suspicious(
                218990, 218990, text_width=13,
            ),
            '完整六位数宽约 59px，13px 说明只画出了末位',
        )
        self.assertFalse(
            self.handler._is_yellow_coins_reading_suspicious(
                218990, 218990, text_width=59,
            ),
            '正常宽度不应判残缺',
        )

    def test_no_baseline_falls_back_to_floor(self):
        """没有基线（脚本刚启动）时退化为绝对下限。"""
        self.assertTrue(
            self.handler._is_yellow_coins_reading_suspicious(4, None),
            '无基线时个位数应判残缺',
        )
        self.assertFalse(
            self.handler._is_yellow_coins_reading_suspicious(218990, None),
            '无基线时六位数正常',
        )

    def test_zero_is_not_judged_here(self):
        """0 属于既有的「未加载」路径，不由残缺判定处理。"""
        self.assertFalse(self.handler._is_yellow_coins_reading_suspicious(0, 218990))


class TestRecheckFlow(GuardTestBase):
    """get_yellow_coins 在残缺时的复查行为。"""

    def _run(self, handler, values, screen_stable=False):
        """按顺序喂 OCR 读数，返回 get_yellow_coins() 的结果与 OCR 调用次数。

        Args:
            handler: 被测试的 OSStatus 实例。
            values: OCR 依次返回的读数（用完后重复最后一个）。
            screen_stable: 是否模拟「画面静止」。True 表示连拍两帧像素一致
                且复读一致（黄币真的变少时的状态）；False 表示画面在动
                （数字动画/加载中，OCR 残缺的成因）。
        """
        from module.os_handler import os_status as os_status_module

        state = {'i': 0, 'calls': 0}

        def fake_ocr(image):
            state['calls'] += 1
            index = min(state['i'], len(values) - 1)
            state['i'] += 1
            return values[index]

        # 画面稳定性：稳定时两帧像素一致，不稳定时每帧都不同
        frame = {'n': 0}

        def fake_crop(*args, **kwargs):
            frame['n'] += 1
            return np.full((21, 90, 3), frame['n'], dtype=np.uint8)

        handler.image_crop = fake_crop
        handler._yellow_coins_reading_is_confirmed = (
            lambda value, text_width=None: screen_stable
        )

        with patch.object(os_status_module, 'OCR_SHOP_YELLOW_COINS') as fake_digit, \
                patch.object(os_status_module, 'LogRes'):
            fake_digit.ocr = fake_ocr
            fake_digit.last_text_width = 59
            value = handler.get_yellow_coins()
        return value, state['calls']

    def test_transient_residual_is_recovered(self):
        """残缺后立刻读到真值：采信真值，不退回基线。"""
        handler = self.make_handler(last_yellow_coins=0)
        value, _ = self._run(handler, [4, 218990])
        self.assertEqual(value, 218990, '复查读到真值就该采信真值')

    def test_persistent_residual_falls_back_to_baseline(self):
        """画面一直没画完：沿用基线，不把个位数当真。"""
        handler = self.make_handler(last_yellow_coins=0)
        handler._last_yellow_coins = 218990
        value, _ = self._run(handler, [4, 4, 4, 4])
        self.assertEqual(value, 218990, '持续残缺时应沿用基线而不是 4')

    def test_legit_drop_is_accepted(self):
        """合法暴跌（买箱子）走正常双读，不触发复查。"""
        handler = self.make_handler(last_yellow_coins=0)
        handler._last_yellow_coins = 10000
        value, calls = self._run(handler, [2000, 2000])
        self.assertEqual(value, 2000, '买行动力后的真实余额必须采信')
        self.assertEqual(calls, 2, '合法读数只需双读，不应额外复查')

    def test_month_end_cleanup_skips_guard(self):
        """重置清仓窗口内黄币合法变成三位数，不复查、直接采信。"""
        handler = self.make_handler(last_yellow_coins=0, guard_enabled=False)
        handler._last_yellow_coins = 218990
        value, calls = self._run(handler, [100, 100])
        self.assertEqual(value, 100, '月末清仓把黄币花到 100 是合法状态')
        self.assertEqual(calls, 2, '清仓窗口不应触发复查')

    def test_stable_screen_short_reading_is_accepted(self):
        """画面静止且复读一致：黄币真的变少了，立即采信。

        对应场景：用户手动清港口 / 脚本清仓 / 重置。此时画面是静止的，
        与 OCR 残缺（画面在动）可区分，不需要等次数，也不会误判。
        """
        handler = self.make_handler(last_yellow_coins=0)
        handler._last_yellow_coins = 218990
        value, _ = self._run(handler, [520, 520, 520], screen_stable=True)
        self.assertEqual(value, 520, '画面静止说明黄币真的变少了，必须马上采信')

    def test_moving_screen_short_reading_keeps_baseline(self):
        """画面在动：判为 OCR 残缺，沿用基线。

        2026-11-11 事故场景：真值 218990，画面没画完读出 4。
        """
        handler = self.make_handler(last_yellow_coins=0)
        handler._last_yellow_coins = 218990
        value, _ = self._run(handler, [4, 4, 4], screen_stable=False)
        self.assertEqual(value, 218990, '画面在动时的小读数不可信，应沿用基线')

    def test_task_switch_interval_is_irrelevant(self):
        """判定只看画面稳不稳，与两次调用间隔多久无关。

        实测相邻调用间隔 p50=107s、p90=346s，被 Hard/Main2 等任务插队时
        最长 1903s（31.7 分钟）。按秒或按次数判定都会失准，按画面则不受影响。
        """
        handler = self.make_handler(last_yellow_coins=0)
        handler._last_yellow_coins = 218990

        # 画面在动（残缺）：无论隔多久都沿用基线
        value, _ = self._run(handler, [4, 4, 4], screen_stable=False)
        self.assertEqual(value, 218990)

        # 画面静止（真的变少）：立即采信，不用等
        value, _ = self._run(handler, [520, 520, 520], screen_stable=True)
        self.assertEqual(value, 520)

    def test_stability_check_detects_moving_screen(self):
        """_yellow_coins_reading_is_confirmed 本身：两帧不同就判未确认。"""
        from module.os_handler import os_status as os_status_module
        from module.os_handler.os_status import OSStatus

        handler = self.make_handler()
        handler.device = SimpleNamespace(sleep=lambda s: None, image=None,
                                         screenshot=lambda: None)
        frames = {'n': 0}

        def fake_crop(*args, **kwargs):
            frames['n'] += 1
            # 每帧都不同 → 画面在动
            return np.full((21, 90, 3), frames['n'], dtype=np.uint8)

        handler.image_crop = fake_crop
        with patch.object(os_status_module, 'OCR_SHOP_YELLOW_COINS') as fake_digit:
            fake_digit.ocr = lambda image: 4
            handler.device.image = object()
            self.assertFalse(
                handler._yellow_coins_reading_is_confirmed(4),
                '两帧像素不一致说明画面在动，不能确认读数',
            )

    def test_stability_check_accepts_frozen_screen(self):
        """画面静止且复读一致：确认通过。"""
        from module.os_handler import os_status as os_status_module

        handler = self.make_handler()
        handler.device = SimpleNamespace(sleep=lambda s: None, image=object(),
                                         screenshot=lambda: None)
        handler.image_crop = lambda *a, **k: np.zeros((21, 90, 3), dtype=np.uint8)

        with patch.object(os_status_module, 'OCR_SHOP_YELLOW_COINS') as fake_digit:
            fake_digit.ocr = lambda image: 520
            # 直接走内部步骤，把被 except 吞掉的异常暴露出来
            handler.device.screenshot()
            first = handler.image_crop(os_status_module.SHOP_YELLOW_COINS)
            handler.device.sleep(0.15)
            handler.device.screenshot()
            second = handler.image_crop(os_status_module.SHOP_YELLOW_COINS)
            self.assertTrue(np.array_equal(first, second), '测试桩本身应产出相同两帧')
            self.assertEqual(fake_digit.ocr(handler.device.image), 520)
            self.assertTrue(
                handler._yellow_coins_reading_is_confirmed(520),
                '两帧一致且复读一致应确认',
            )

    def test_stability_check_rejects_narrow_text_before_screenshot(self):
        """文本宽度与位数不匹配时直接否掉，不用拍帧。"""
        handler = self.make_handler()
        self.assertFalse(
            handler._yellow_coins_reading_is_confirmed(218990, text_width=13),
            '六位数却只有 13px 宽，说明只画出末位，不必拍帧即可否掉',
        )

    def test_recheck_value_not_written_to_cache(self):
        """沿用基线的值不代表当前余额，不能写进 30 秒 TTL 缓存。"""
        handler = self.make_handler(last_yellow_coins=0)
        handler._last_yellow_coins = 218990
        self._run(handler, [4, 4, 4, 4])
        self.assertIsNone(
            getattr(handler.config, '_yellow_coins_cache', None),
            '复查兜底值不得写入 TTL 缓存',
        )


class TestLedgerCoinGate(unittest.TestCase):
    """账本侧的黄币位数门。"""

    def test_short_reading_rejected(self):
        from module.os_handler.action_point_ledger import ActionPointLedger

        for value in (4, 42, 450, 530, 999):
            with self.subTest(value=value):
                cleaned, reason = ActionPointLedger.sanitize_coin_value(value)
                self.assertIsNone(cleaned, f'{value} 位数过短应被拒绝')
                self.assertTrue(reason.startswith('len<'))

    def test_normal_reading_accepted(self):
        from module.os_handler.action_point_ledger import ActionPointLedger

        for value in (1000, 81451, 218990, 1234567):
            with self.subTest(value=value):
                cleaned, reason = ActionPointLedger.sanitize_coin_value(value)
                self.assertEqual(cleaned, value)
                self.assertEqual(reason, 'ok')


class TestCoinStartSelfHeal(unittest.TestCase):
    """补黄币起始值被脏读数污染后能自愈。"""

    def _mixin(self, stored_start):
        from module.os.tasks.scheduling import CoinTaskMixin

        mixin = CoinTaskMixin.__new__(CoinTaskMixin)
        state = {'start': stored_start, 'writes': []}

        mixin._get_smart_scheduling_state_value = (
            lambda key, default=None: state['start']
        )

        def fake_set(key, value):
            state['start'] = value
            state['writes'].append(value)

        mixin._set_smart_scheduling_state_value = fake_set
        mixin._get_smart_scheduling_operation_coins_return_threshold = lambda: 250000
        return mixin, state

    def test_legit_start_is_kept(self):
        mixin, state = self._mixin(28642)
        target, start, _ = mixin._get_coin_replenish_target(43892, 100000)
        self.assertEqual(start, 28642, '合法的起始值应保留')
        self.assertEqual(target, 350000, '目标值仍是保留值 + 回补阈值')

    def test_polluted_start_is_reset(self):
        mixin, state = self._mixin(4)
        target, start, _ = mixin._get_coin_replenish_target(222188, 100000)
        self.assertEqual(start, 222188, '被读成 4 的起始值应自愈为当前读数')
        self.assertEqual(target, 350000)

    def test_start_not_reset_for_normal_drop(self):
        """合法暴跌（比如从 30000 掉到 5000）不到两个数量级，不重置。"""
        mixin, state = self._mixin(30000)
        target, start, _ = mixin._get_coin_replenish_target(5000, 100000)
        self.assertEqual(start, 5000, '下跌时起点跟随当前读数（原逻辑）')


if __name__ == '__main__':
    unittest.main()
