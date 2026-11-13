"""运行中委托 / 科研快照（统计页「正在进行中」区块）回归测试。

覆盖三块：
1. cl1_database 的 running_projects 轻量表读写（整体覆盖、委托与科研互不干扰）；
2. running_projects 采集与展示元数据（委托类型映射、科研字典反查）；
3. app_stat_running 的 HTML 构造（三版样式、空态、异常吞掉）。

刻意不碰真实 cl1_data.db：数据库一律建在临时目录。
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2 - 1]))

from module.statistics.cl1_database import Cl1Database
from module.statistics import running_projects as rp
from module.webui.app_stat_running import RunningProjectsMixin


class RunningProjectsDatabaseTest(unittest.TestCase):
    """轻量表读写：委托与科研分别整体覆盖，互不清空对方。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Cl1Database(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_snapshot(self):
        snap = self.db.get_running_projects("alas")
        self.assertEqual(snap["commissions"], [])
        self.assertEqual(snap["research"], {})

    def test_commission_roundtrip(self):
        self.db.save_running_commissions(
            "alas",
            [{"name": "VIP", "genre": "urgent_gem", "duration_h": 2,
              "start_time": "2026-11-13T09:00:00",
              "finish_time": "2026-11-13T11:00:00", "items": ["Gem"]}],
        )
        snap = self.db.get_running_projects("alas")
        self.assertEqual(len(snap["commissions"]), 1)
        self.assertEqual(snap["commissions"][0]["genre"], "urgent_gem")
        self.assertTrue(snap["updated_at"])

    def test_overwrite_not_append(self):
        """整体覆盖语义：连续两次写入只保留后者。"""
        self.db.save_running_commissions("alas", [{"name": "A"}])
        self.db.save_running_commissions("alas", [{"name": "B"}, {"name": "C"}])
        names = [c["name"] for c in self.db.get_running_projects("alas")["commissions"]]
        self.assertEqual(names, ["B", "C"])

    def test_commission_and_research_isolated(self):
        """写科研不该清掉委托，反之亦然。"""
        self.db.save_running_commissions("alas", [{"name": "A"}])
        self.db.save_running_research("alas", {"records": [{"name": "G-531-MI"}]})
        snap = self.db.get_running_projects("alas")
        self.assertEqual(len(snap["commissions"]), 1)
        self.assertEqual(len(snap["research"]["records"]), 1)

        self.db.save_running_commissions("alas", [{"name": "B"}])
        snap = self.db.get_running_projects("alas")
        self.assertEqual(len(snap["research"]["records"]), 1, "写委托不该清掉科研")

    def test_instance_isolation(self):
        self.db.save_running_commissions("alas", [{"name": "A"}])
        self.assertEqual(self.db.get_running_projects("other")["commissions"], [])

    def test_broken_json_degrades(self):
        """库里是坏 JSON 时返回空结构，不抛异常。"""
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.db.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO running_projects (instance, data_json, updated_at) VALUES (?, ?, ?)",
                ("alas", "{not json", "2026-11-13T00:00:00"),
            )
            conn.commit()
        snap = self.db.get_running_projects("alas")
        self.assertEqual(snap["commissions"], [])


class RunningProjectsMetaTest(unittest.TestCase):
    """采集与展示元数据。"""

    def test_commission_display_name(self):
        self.assertEqual(rp.commission_display_name("urgent_gem", "x"), "要员护送")
        self.assertEqual(rp.commission_display_name("unknown_type", "ORIGINAL"), "ORIGINAL")
        self.assertEqual(rp.commission_display_name("", ""), "未知委托")

    def test_commission_expected_items(self):
        self.assertEqual(rp.commission_expected_items("urgent_gem"), ["Gem"])
        self.assertEqual(rp.commission_expected_items("ordinary"), [])
        self.assertEqual(rp.commission_expected_items(""), [])

    def test_research_items_from_dictionary(self):
        """科研产出走 project_data 字典反查，条目带中文名与图标键。"""
        items = rp.research_display_items("G-531-MI", 1)
        self.assertTrue(items, "G-531-MI 应有产出")
        self.assertIn("label", items[0])

    def test_research_unknown_project(self):
        self.assertEqual(rp.research_display_items("ZZZ-999-MI", 1), [])
        self.assertEqual(rp.research_display_items("", 1), [])

    def test_research_duration_hours(self):
        self.assertEqual(rp.research_duration_hours("G-531-MI", 1), 4.0)
        self.assertEqual(rp.research_duration_hours("ZZZ-999-MI", 1, fallback=1.5), 1.5)

    def test_build_records_running_slot_uses_remaining_time(self):
        """队首 running：显示时长是剩余倒计时，完成时间 = now + 剩余。"""
        now = datetime(2026, 11, 13, 10, 0)
        records = rp.build_research_records(
            [{"name": "G-531-MI", "series": 1, "duration_h": 4, "slot": "running", "in_queue": True}],
            durations=[timedelta(hours=1, minutes=30)],
            now=now,
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["finish_time"], "2026-11-13T11:30:00")
        self.assertEqual(records[0]["slot"], "running")

    def test_build_records_waiting_chains_after_previous(self):
        """排队项接着上一个的完成时间排：FIFO 链条。"""
        now = datetime(2026, 11, 13, 10, 0)
        records = rp.build_research_records(
            [
                {"name": "G-531-MI", "series": 1, "duration_h": 4, "slot": "running", "in_queue": True},
                {"name": "C-185-MI", "series": 1, "duration_h": 8, "slot": "waiting", "in_queue": True},
            ],
            durations=[timedelta(hours=1), timedelta(hours=8)],
            now=now,
        )
        self.assertEqual(records[0]["finish_time"], "2026-11-13T11:00:00")
        self.assertEqual(records[1]["start_time"], "2026-11-13T11:00:00")
        self.assertEqual(records[1]["finish_time"], "2026-11-13T19:00:00")

    def test_build_records_finished_slot(self):
        """已完成待领取：保留卡片但标 finished，且已完成。"""
        now = datetime(2026, 11, 13, 10, 0)
        records = rp.build_research_records(
            [{"name": "G-531-MI", "series": 1, "duration_h": 4, "slot": "finished", "in_queue": True}],
            durations=[timedelta(0)],
            now=now,
        )
        self.assertEqual(records[0]["slot"], "finished")
        self.assertEqual(records[0]["finish_time"], "2026-11-13T10:00:00")

    def test_build_records_sixth_slot_independent(self):
        """队列外第 6 位独立计时，不参与 FIFO 链条。"""
        now = datetime(2026, 11, 13, 10, 0)
        records = rp.build_research_records(
            [
                {"name": "G-531-MI", "series": 1, "duration_h": 4, "slot": "running", "in_queue": True},
                {"name": "D-731-MI", "series": 1, "duration_h": 2.5, "slot": "running", "in_queue": False},
            ],
            durations=[timedelta(hours=1), timedelta(hours=2)],
            now=now,
        )
        self.assertFalse(records[1]["in_queue"])
        # 队列外从 now 起算，不受队首完成时间影响
        self.assertEqual(records[1]["finish_time"], "2026-11-13T12:00:00")

    def test_build_records_skips_unknown_duration(self):
        """查不到时长的项目直接跳过，不猜。"""
        now = datetime(2026, 11, 13, 10, 0)
        records = rp.build_research_records(
            [{"name": "ZZZ-999-MI", "series": 1, "duration_h": 0, "slot": "running", "in_queue": True}],
            durations=[timedelta(0)],
            now=now,
        )
        self.assertEqual(records, [])

    def test_build_records_empty_queue_is_empty(self):
        """空队列产出空记录（旧实现会残留历史项，这里必须为空）。"""
        self.assertEqual(rp.build_research_records([], durations=[], now=datetime.now()), [])

    def test_clear_research_snapshot(self):
        """显式清空：队列空时不留任何历史记录。"""
        from unittest.mock import patch, MagicMock
        import module.statistics.cl1_database as cdb
        fake = MagicMock()
        with patch.object(cdb, "db", fake):
            rp.clear_research_snapshot("alas")
        payload = fake.save_running_research.call_args[0][1]
        self.assertEqual(payload["records"], [])

    def test_save_commission_snapshot_filters_non_running(self):
        """只写 status == running 的委托；空列表不写。"""
        from unittest.mock import patch, MagicMock
        recorded = {}

        class FakeComm:
            def __init__(self, status):
                self.status = status
                self.name = "COMM"
                self.genre = "urgent_gem"
                self.duration = timedelta(hours=2)
                self.create_time = datetime(2026, 11, 13, 9, 0)
                self.finish_time = datetime(2026, 11, 13, 11, 0)

        import module.statistics.cl1_database as cdb
        fake_db = MagicMock()
        fake_db.save_running_commissions.side_effect = lambda i, r: recorded.setdefault("rows", r)
        with patch.object(cdb, "db", fake_db):
            rp.save_commission_snapshot("alas", [FakeComm("running"), FakeComm("finished")])
        self.assertEqual(len(recorded.get("rows", [])), 1, "只该记录 running 的那条")


class RunningProjectsHtmlTest(unittest.TestCase):
    """HTML 构造：三版样式、空态、异常安全。"""

    def setUp(self):
        self.obj = RunningProjectsMixin.__new__(RunningProjectsMixin)
        self.now = datetime(2026, 11, 13, 10, 30)
        self.snapshot = {
            "commissions": [
                {"display_name": "要员护送", "genre": "urgent_gem", "duration_h": 2,
                 "start_dt": self.now - timedelta(hours=1),
                 "finish_dt": self.now + timedelta(hours=1), "items": ["Gem"]},
                {"display_name": "已完成委托", "genre": "daily_resource", "duration_h": 8,
                 "start_dt": self.now - timedelta(hours=9),
                 "finish_dt": self.now - timedelta(minutes=5), "items": ["Oil"]},
            ],
            "research": [
                {"name": "G-531-MI", "genre": "G", "genre_label": "资金研究", "duration_h": 4,
                 "queue_index": 0, "in_queue": True,
                 "start_dt": self.now - timedelta(hours=1),
                 "finish_dt": self.now + timedelta(hours=3), "items": []},
                {"name": "C-185-MI", "genre": "C", "genre_label": "活动研究", "duration_h": 8,
                 "queue_index": 1, "in_queue": True,
                 "start_dt": self.now + timedelta(hours=3),
                 "finish_dt": self.now + timedelta(hours=11), "items": []},
                {"name": "D-731-MI", "genre": "D", "genre_label": "舰船研究", "duration_h": 2.5,
                 "queue_index": 6, "in_queue": False,
                 "start_dt": self.now,
                 "finish_dt": self.now + timedelta(hours=2.5), "items": []},
            ],
        }

    def test_commission_html_contains_expected_content(self):
        html = self.obj._build_running_commission_html(self.snapshot, self.now)
        self.assertIn("st-running-card", html)
        self.assertIn("要员护送", html)
        self.assertIn("2\u5c0f\u65f6", html)      # 2小时
        self.assertIn("\u5f00\u59cb", html)      # 开始
        self.assertIn("st-running-state--settle", html, "已完成项应标待结算")

    def test_commission_empty_state(self):
        html = self.obj._build_running_commission_html({"commissions": [], "research": []}, self.now)
        self.assertIn("st-running-empty", html)
        self.assertIn("\u6ca1\u6709\u6b63\u5728\u8fd0\u884c", html)   # 没有正在运行

    def test_research_html_states(self):
        html = self.obj._build_running_research_html(self.snapshot, self.now)
        self.assertIn("st-running-state--running", html)
        self.assertIn("st-running-state--queueoff", html, "队列外项目应有徽标")
        self.assertIn("G-531-MI", html)
        self.assertEqual(html.count("st-running-card"), 3)

    def test_research_empty_state(self):
        html = self.obj._build_running_research_html({"commissions": [], "research": []}, self.now)
        self.assertIn("st-running-empty", html)

    def test_three_styles_all_render(self):
        """三种版式都能产出卡片，切换常量不炸。"""
        import module.webui.app_stat_running as asr
        original = asr._RUNNING_STYLE
        try:
            for style in ("cards", "table", "compact"):
                asr._RUNNING_STYLE = style
                html = self.obj._build_running_commission_html(self.snapshot, self.now)
                self.assertIn("st-running-card", html, f"{style} 版应出卡片")
                self.assertIn("\u8981\u5458\u62a4\u9001", html, f"{style} 版应含委托名")
        finally:
            asr._RUNNING_STYLE = original

    def test_metrics_empty_does_not_raise(self):
        html = self.obj._build_running_commission_html({"commissions": []}, self.now)
        self.assertIsInstance(html, str)

    def test_bad_input_swallowed(self):
        """脏数据不能让统计面板白屏：异常必须被吞掉并返回字符串。"""
        html = self.obj._build_running_commission_html({"commissions": "not-a-list"}, self.now)
        self.assertIsInstance(html, str)

    def test_fmt_helpers(self):
        from module.webui.app_stat_running import _fmt_hours, _fmt_remain, _fmt_time
        self.assertEqual(_fmt_hours(4), "4\u5c0f\u65f6")
        self.assertEqual(_fmt_hours(0), "")
        self.assertEqual(_fmt_hours(0.5), "30\u5206")
        done, _ = _fmt_remain(self.now - timedelta(minutes=1), self.now)
        self.assertTrue(done)
        done, text = _fmt_remain(self.now + timedelta(hours=1, minutes=5), self.now)
        self.assertFalse(done)
        self.assertIn("1\u5c0f\u65f6", text)
        self.assertEqual(_fmt_time(None, self.now), "\u2014")


class GhostCardRegressionTest(unittest.TestCase):
    """回归：旧实现留下的「幽灵卡片」问题。

    旧实现按「只写本轮启动的项目」累加，领奖/完成后没有清理点，
    导致统计页残留已结束的项目，尤其是队列外第 6 位会永久显示
    成「队列外 + 待结算」。这里钉死修复后的行为。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Cl1Database(Path(self.tmp.name) / "ghost.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _save(self, projects, durations=None, now=None):
        from unittest.mock import patch
        import module.statistics.cl1_database as cdb
        with patch.object(cdb, "db", self.db):
            rp.save_research_snapshot("alas", projects, durations=durations, now=now)

    def test_finished_project_disappears_after_rebuild(self):
        """项目跑完并从队列消失后，再次重建不应留下它。"""
        now = datetime(2026, 11, 13, 10, 0)
        self._save(
            [{"name": "G-531-MI", "series": 1, "duration_h": 4, "slot": "running", "in_queue": True}],
            durations=[timedelta(hours=1)], now=now,
        )
        self.assertEqual(len(self.db.get_running_projects("alas")["research"]["records"]), 1)

        # 下一次读队列：队列已空 -> 传空列表，重建后必须归零
        self._save([], durations=[], now=now + timedelta(hours=2))
        self.assertEqual(
            self.db.get_running_projects("alas")["research"]["records"], [],
            "已完成的项目不能残留",
        )

    def test_sixth_slot_does_not_linger(self):
        """队列外第 6 位结束后不应残留成永久卡片。"""
        now = datetime(2026, 11, 13, 10, 0)
        self._save(
            [{"name": "D-731-MI", "series": 1, "duration_h": 2.5, "slot": "running", "in_queue": False}],
            durations=[timedelta(hours=1)], now=now,
        )
        records = self.db.get_running_projects("alas")["research"]["records"]
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["in_queue"])

        # 第 6 位跑完、领走 -> 下次快照里没有它
        self._save([], durations=[], now=now + timedelta(hours=3))
        self.assertEqual(self.db.get_running_projects("alas")["research"]["records"], [])

    def test_queue_shrinks_after_receive(self):
        """领走队首后，队列从 3 项缩到 2 项，不能留第 3 项。"""
        now = datetime(2026, 11, 13, 10, 0)
        three = [
            {"name": f"P{i}-MI", "series": 1, "duration_h": 2,
             "slot": "running" if i == 0 else "waiting", "in_queue": True}
            for i in range(3)
        ]
        self._save(three, durations=[timedelta(hours=1)] + [timedelta(hours=2)] * 2, now=now)
        self.assertEqual(len(self.db.get_running_projects("alas")["research"]["records"]), 3)

        two = three[1:]
        two[0]["slot"] = "running"
        self._save(two, durations=[timedelta(hours=1), timedelta(hours=2)], now=now)
        records = self.db.get_running_projects("alas")["research"]["records"]
        self.assertEqual(len(records), 2, "领走的项目不该残留")
        self.assertEqual(records[0]["name"], "P1-MI")

    def test_commission_snapshot_clears_when_empty(self):
        """委托全部结束、扫描到 empty 时覆盖成空（不留幽灵委托）。"""
        from unittest.mock import patch
        import module.statistics.cl1_database as cdb
        with patch.object(cdb, "db", self.db):
            rp.save_commission_snapshot(
                "alas",
                [type("C", (), {"status": "running", "name": "A", "genre": "urgent_gem",
                                "duration": timedelta(hours=2),
                                "create_time": datetime(2026, 11, 13, 9, 0),
                                "finish_time": datetime(2026, 11, 13, 11, 0)})()],
            )
            self.assertEqual(len(self.db.get_running_projects("alas")["commissions"]), 1)
            # 委托跑完 -> 调用方传空列表，应覆盖成空
            rp.save_commission_snapshot("alas", [])
        self.assertEqual(self.db.get_running_projects("alas")["commissions"], [])

class DurationFormatTest(unittest.TestCase):
    """时长格式化：小数小时要显示成「X小时Y分」，不能出现 2.67 这种。

    游戏真实的委托档位里就有 1.5 / 2.67 / 5.33 这类值，
    直接显示「2.67小时」让人读不出是 2 小时 40 分。
    """

    def test_integer_hours(self):
        from module.webui.app_stat_running import _fmt_hours
        self.assertEqual(_fmt_hours(2), "2小时")
        self.assertEqual(_fmt_hours(10), "10小时")

    def test_decimal_hours_show_minutes(self):
        from module.webui.app_stat_running import _fmt_hours
        self.assertEqual(_fmt_hours(1.5), "1小时30分")
        self.assertEqual(_fmt_hours(2.5), "2小时30分")

    def test_no_decimal_point_in_output(self):
        """任何档位都不能输出小数点，否则就是误导。"""
        from module.webui.app_stat_running import _fmt_hours
        for value in (1.5, 2.67, 5.33, 1.17, 1.33, 1.67, 2.25, 1.75):
            text = _fmt_hours(value)
            self.assertNotIn(".", text, f"{value} 输出成了 {text}")

    def test_real_log_slots_render_readable(self):
        """真实日志里出现过的档位都能渲染成可读文本。"""
        from module.webui.app_stat_running import _fmt_hours
        expected = {
            1.5: "1小时30分",
            2.67: "2小时40分",
            5.33: "5小时20分",
            1.17: "1小时10分",
            1.33: "1小时20分",
            1.67: "1小时40分",
        }
        for hours, want in expected.items():
            self.assertEqual(_fmt_hours(hours), want)

    def test_under_one_hour(self):
        from module.webui.app_stat_running import _fmt_hours
        self.assertEqual(_fmt_hours(0.5), "30分")
        self.assertEqual(_fmt_hours(0.75), "45分")

    def test_invalid_input(self):
        from module.webui.app_stat_running import _fmt_hours
        self.assertEqual(_fmt_hours(0), "")
        self.assertEqual(_fmt_hours(None), "")
        self.assertEqual(_fmt_hours("abc"), "")

class CommissionDurationRegressionTest(unittest.TestCase):
    """回归：运行中委托的时长档被当成剩余时间。

    委托卡片在「运行中」状态显示的是倒计时，Commission.duration 读到的是
    剩余时间。早期实现直接拿它当总时长写库，界面上就出现「要员护送 28分」
    这种不存在的档位（钻石委托只有 2/4/8 小时三档）。

    修复策略不是「反推档位」（仅凭剩余时间数学上无法唯一确定，真实日志
    873 条里只有 10% 能定），而是区分两类委托：
    - 本任务刚启动的：duration 是启动前卡片的总时长，可信；
    - 扫描时已在跑的：只有剩余时间，只记 remaining_h，不编造总时长。
    """

    @staticmethod
    def _comm(name, genre, remaining_h, started):
        """构造一个假的 Commission 对象。"""

        class Comm:
            pass

        c = Comm()
        c.status = "running"
        c.name = name
        c.genre = genre
        c.duration = timedelta(hours=remaining_h)
        c.create_time = datetime(2026, 11, 13, 13, 0)
        c.finish_time = c.create_time + c.duration
        c._started_this_run = started
        return c

    def _save(self, comms):
        from unittest.mock import patch, MagicMock
        import module.statistics.cl1_database as cdb

        fake = MagicMock()
        with patch.object(cdb, "db", fake):
            rp.save_commission_snapshot("alas", comms)
        return fake.save_running_commissions.call_args[0][1]

    def test_started_this_run_uses_duration(self):
        """本次刚启动的委托：duration 就是总时长，直接采信。"""
        rows = self._save([self._comm("要员护送", "urgent_gem", 2.0, True)])
        self.assertEqual(rows[0]["duration_h"], 2.0)

    def test_scanned_running_has_no_duration(self):
        """扫描到的老委托：只剩剩余时间，duration_h 必须是 None。"""
        rows = self._save([self._comm("NYB度假护卫", "urgent_gem", 28 / 60, False)])
        self.assertIsNone(rows[0]["duration_h"], "不能把 28 分剩余当成总时长")

    def test_scanned_running_keeps_remaining(self):
        """老委托仍要记下剩余时长，展示层要用它。"""
        rows = self._save([self._comm("支援马拉基岛", "urgent_book", 1.33, False)])
        self.assertAlmostEqual(rows[0]["remaining_h"], 1.33, places=2)

    def test_card_hides_slot_when_unknown(self):
        """档位未知时卡片不显示时长胶囊，只显示剩余时间。"""
        obj = RunningProjectsMixin.__new__(RunningProjectsMixin)
        now = datetime(2026, 11, 13, 10, 30)
        html = obj._build_running_commission_html(
            {"commissions": [{"display_name": "要员护送", "genre": "urgent_gem",
                             "duration_h": None, "remaining_h": 0.47,
                             "start_dt": now, "finish_dt": now + timedelta(minutes=28),
                             "items": ["Gem"]}],
             "research": []},
            now,
        )
        self.assertIn("要员护送", html)
        self.assertIn("剩余 28分", html)
        self.assertNotIn("分钟</span>", html)
        self.assertNotIn("st-running-hours", html, "档位未知时不该出时长胶囊")

    def test_card_shows_slot_when_known(self):
        """档位确定时（刚启动的委托）正常显示时长胶囊。"""
        obj = RunningProjectsMixin.__new__(RunningProjectsMixin)
        now = datetime(2026, 11, 13, 10, 30)
        html = obj._build_running_commission_html(
            {"commissions": [{"display_name": "要员护送", "genre": "urgent_gem",
                             "duration_h": 2, "remaining_h": 2,
                             "start_dt": now, "finish_dt": now + timedelta(hours=2),
                             "items": ["Gem"]}],
             "research": []},
            now,
        )
        self.assertIn("约2小时", html)
        self.assertIn("st-running-hours", html)


if __name__ == "__main__":
    unittest.main()
