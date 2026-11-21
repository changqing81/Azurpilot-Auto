"""新 UI（REST 面板）大世界收获的回归测试。

覆盖 2026-11-21 长卿报的两个问题：
- 「收获合计」点击不动：chip 的 data-meow-strip 由前端事件委托接管，
  REST 上下文把回传的 item 写进 _meow_item_filter（老 UI _on_meow_strip_click
  的 REST 等价物），缓存键必须含 item 否则切换筛选命中旧缓存。
- 「为什么只显示一部分」：REST 没有「其余 N 项」按钮（挂回调的 Output
  塞不进纯 HTML 通道），_get_opsi_panels_html_impl 必须置
  _meow_show_all_rows=True 全量平铺明细行。
"""

import unittest
from unittest.mock import patch

from module.webapi.data import (
    _get_opsi_panels_html_impl,
    get_opsi_panels_html,
)


class _Captured(BaseException):
    """携带状态位跳出 impl 后续渲染的哨兵。

    继承 BaseException：impl 外层是 except Exception 兜底，
    普通 Exception 会被吞成 {"success": False}，跳不出来。
    """

    def __init__(self, ctx):
        super().__init__("captured")
        self.item_filter = getattr(ctx, "_meow_item_filter", None)
        self.show_all = getattr(ctx, "_meow_show_all_rows", False)
        self.task_filter = getattr(ctx, "_meow_task_filter", None)


class _FakeCtx:
    """impl 的构造替身：记下状态位后在镜像/渲染前抛 _Captured 跳出。

    impl 会把两个 mixin 的方法镜像到 ctx 上（_build* 前缀的方法即使已
    存在也会被覆盖），所以「桩方法提前返回」挡不住真渲染 —— 用异常
    在 _build_opsi_month_cards 被调用时跳出，避免拉起真渲染链。
    """

    def __init__(self, _instance_name: str):
        self.alas_name = _instance_name
        self._stat_year, self._stat_month = (2026, 11)
        self._meow_task_filter = None
        self._meow_item_filter = None

    def _build_opsi_month_cards(self):
        raise _Captured(self)


class OpsiRestItemFilterTest(unittest.TestCase):
    """item 参数 → ctx._meow_item_filter 的传递链。"""

    def _run_impl(self, task="", item=""):
        """跑一遍 impl，抓住它构造的 ctx 状态位。"""
        # impl 会把两个 mixin 的方法镜像到 ctx（_build* 前缀的方法即使 ctx
        # 已有也会被覆盖），桩方法会被真方法顶掉 → 卡进真渲染链。
        # 把 mixin 源替换成空壳类，镜像循环无事可做，桩就保得住。
        import module.webui.app_stat_opsi as _opsi_mod
        import module.webui.app_stat_opsi_export as _export_mod

        class _EmptyMixin:
            pass

        with patch.object(_opsi_mod, "OpsiStatisticsMixin", _EmptyMixin), \
                patch.object(_export_mod, "OpsiExportMixin", _EmptyMixin), \
                patch("module.webapi.data._StatRenderCtx", _FakeCtx):
            try:
                _get_opsi_panels_html_impl("alas", "", task, item)
            except _Captured as cap:
                return {
                    "item_filter": cap.item_filter,
                    "show_all": cap.show_all,
                    "task_filter": cap.task_filter,
                }
        # 没走到 _build_opsi_month_cards 说明构造阶段就失败了
        raise AssertionError("impl 未按预期执行到 _build_opsi_month_cards")

    def test_item_writes_item_filter(self):
        state = self._run_impl(item="Plate")
        self.assertEqual(state["item_filter"], "Plate")

    def test_item_none_and_all_mean_no_filter(self):
        for item in ("", "__all__", "None"):
            state = self._run_impl(item=item)
            self.assertIsNone(state["item_filter"], msg=f"item={item!r}")

    def test_show_all_rows_set_for_rest(self):
        state = self._run_impl()
        self.assertTrue(state["show_all"])

    def test_cache_key_contains_item(self):
        """item 不进缓存键时，切筛选会命中旧 HTML（chip 选中态/明细行不更新）。"""
        from unittest.mock import patch as _patch

        recorded = {}

        def _fake_cached(instance_name, panel, builder, month=""):
            recorded["panel"] = panel

        with _patch("module.webapi.data._cached_stat_html", _fake_cached):
            get_opsi_panels_html("alas", "", "MeowfficerFarming", "CatT3")
        self.assertEqual(recorded["panel"], "opsi:MeowfficerFarming:CatT3")


class OpsiRouteItemParamTest(unittest.TestCase):
    """路由层：item 参数经 _p 路径段 / query 两条通道都能到达 handler。

    远控中继剥 query，前端走 panelPathWithParams（_p 段）；
    电脑直连走 query。_arg() 的三级回退必须两条都接得住。
    """

    @staticmethod
    def _capture_item(route_path):
        """打真路由，抓 opsi handler 收到的 item 实参。"""
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from module.webapi.app import mount_webapi

        captured = {}

        def _fake_impl(instance_name, month="", task="", item=""):
            captured["month"] = month
            captured["task"] = task
            captured["item"] = item
            return {"success": True, "html": "", "error": ""}

        with patch("module.webapi.data.get_opsi_panels_html", _fake_impl):
            app = Starlette()
            mount_webapi(app)
            client = TestClient(app)
            r = client.get(route_path)
            assert r.status_code == 200, r.status_code
        return captured

    def test_item_via_query(self):
        cap = self._capture_item(
            "/newapi/stat/alas/panels_html?panel=opsi&item=CatT3"
        )
        self.assertEqual(cap["item"], "CatT3")

    def test_item_via_p_segment(self):
        """远控通道：参数整体编码进 _p 路径段（k=v&k=v，空值占位 _）。"""
        cap = self._capture_item(
            "/newapi/stat/alas/panels_html/opsi/_p/"
            "month=2026-10&task=MeowfficerFarming&item=Plate"
        )
        self.assertEqual(cap["item"], "Plate")
        self.assertEqual(cap["task"], "MeowfficerFarming")
        self.assertEqual(cap["month"], "2026-10")


if __name__ == "__main__":
    unittest.main()
