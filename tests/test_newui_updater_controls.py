"""更新器页面（新 UI）回归测试。

覆盖 2026-11-20 补齐的三块缺口（长卿：
「新UI的更新器是不是没有更新入口啊？…有立即更新的那个地方吗？
…更新器的那个屏蔽更新呢，老UI都有的，新UI怎么没有啊？」）：

1. 后端新增 5 个端点（state / check / run / cancel / hide-notice）
2. 隐藏更新提示开关能真正落盘 —— 依赖 deploy.template.yaml 里有该键
   （poor_yaml_write 按模板正则替换，模板缺键就静默丢弃，实测过）
3. Python 的 updater.state 可能是 True/False，前端必须归一成 1/0
   否则状态机所有分支都命中不了（实测界面显示 state: false）
"""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "config" / "deploy.template.yaml"
UPDATE_TSX = REPO_ROOT / "frontend" / "src" / "pages" / "update" / "index.tsx"


class TestDeployTemplateHasHideUpdateNotice(unittest.TestCase):
    """模板必须包含 HideUpdateNotice 键。

    poor_yaml_write() 不直接写用户传入的键，而是「读模板 → 按键做正则替换
    → 原子写」。模板里没有这一行，正则匹配不到，值就被静默丢弃 ——
    写 true 后回读仍是 false，文件里也不出现该行（2026-11-20 实测）。
    老 UI 更新器页顶部的同款开关一直因此无效。
    """

    def test_key_present_with_default_false(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            "HideUpdateNotice:",
            text,
            "deploy.template.yaml 缺 HideUpdateNotice 键，开关写盘会被静默丢弃",
        )
        pattern = r"^\s*HideUpdateNotice:\s*(\S+)\s*$"
        m = re.search(pattern, text, re.MULTILINE)
        self.assertIsNotNone(m, "HideUpdateNotice 行格式不合法")
        self.assertEqual(m.group(1).lower(), "false")


class TestDeployConfigPersistsHideUpdateNotice(unittest.TestCase):
    """端到端验证该键能落盘并回读（用临时文件，不碰用户的 deploy.yaml）。"""

    def test_write_read_roundtrip(self):
        from deploy.utils import poor_yaml_read
        from module.webui.config import DeployConfig

        if "HideUpdateNotice" not in poor_yaml_read(str(TEMPLATE)):
            self.skipTest("模板缺键，本用例无意义")

        tmpdir = tempfile.mkdtemp()
        try:
            target = os.path.join(tmpdir, "deploy.yaml")
            Path(target).write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")

            cfg = DeployConfig(file=target)
            self.assertFalse(cfg.HideUpdateNotice)
            cfg.config["HideUpdateNotice"] = True
            cfg.write()

            reloaded = DeployConfig(file=target)
            self.assertTrue(reloaded.HideUpdateNotice, "写入 true 后回读仍为 false")
            self.assertIn(
                "HideUpdateNotice: true",
                Path(target).read_text(encoding="utf-8"),
            )

            reloaded.config["HideUpdateNotice"] = False
            reloaded.write()
            self.assertFalse(DeployConfig(file=target).HideUpdateNotice)
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


class TestUpdaterStateNormalization(unittest.TestCase):
    """前端必须把 Python 的 True/False 归一成 1/0。

    老 UI 的 u(state) 里 state == 0 对 False 成立（Python 语义）；
    经 JSON 到 JS 后 false 变成字符串 false，既不等于 0 也不等于 1，
    状态机全部分支落空 —— 实测点完「检查更新」显示 state: false。
    """

    def test_frontend_has_normalizer(self):
        src = UPDATE_TSX.read_text(encoding="utf-8")
        self.assertIn(
            "function normalizeState",
            src,
            "update/index.tsx 缺 normalizeState()，布尔状态会显示成 state: false",
        )
        body = src[src.index("function normalizeState") :]
        body = body[: body.index(chr(10) + "}") + 2]
        self.assertIn("=== true", body)
        self.assertIn("=== false", body)

    def test_state_text_map_covers_all_ui_branches(self):
        """状态文案表必须覆盖老 UI u(state) 的每个分支。"""
        src = UPDATE_TSX.read_text(encoding="utf-8")
        # 带空格/数字的 key 在 TS 对象字面量里必须加引号，纯标识符可以裸写
        for key in ("run update",):
            marker = chr(39) + key + chr(39) + ":"
            self.assertIn(marker, src, "STATE_TEXT 缺分支 " + key)
        for key in ("checking", "failed", "start", "wait", "reload", "finish", "cancel"):
            bare = key + ":"
            quoted = chr(39) + key + chr(39) + ":"
            self.assertTrue(
                bare in src or quoted in src, "STATE_TEXT 缺分支 " + key
            )
        self.assertIn(chr(39) + "0" + chr(39) + ":", src)
        self.assertIn(chr(39) + "1" + chr(39) + ":", src)


class TestUpdateApiSurface(unittest.TestCase):
    """新增端点必须挂在 webapi 路由表里，且 state 端点如实透出。"""

    def test_routes_registered(self):
        from starlette.applications import Starlette

        from module.webapi.app import mount_webapi

        app = Starlette()
        mount_webapi(app)
        paths = {getattr(r, "path", "") for r in app.routes}
        for p in (
            "/newapi/update/info",
            "/newapi/update/state",
            "/newapi/update/check",
            "/newapi/update/run",
            "/newapi/update/cancel",
            "/newapi/update/hide-notice",
        ):
            self.assertIn(p, paths, "缺路由 " + p)

    def test_state_endpoint_reports_updater_state(self):
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        from module.webapi.app import mount_webapi
        from module.webui.setting import State
        from module.webui.updater import updater

        app = Starlette()
        mount_webapi(app)
        client = TestClient(app)

        original = updater.state
        original_event = State.restart_event
        try:
            State.restart_event = Mock()
            for value in (0, 1, "checking", "failed", "wait", "cancel"):
                updater.state = value
                r = client.get("/newapi/update/state")
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertEqual(body["state"], value)
                self.assertTrue(body["restart_enabled"])
        finally:
            updater.state = original
            State.restart_event = original_event


if __name__ == "__main__":
    unittest.main()
