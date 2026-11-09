"""FLEET_UNAVAILABLE 资产回归测试。

背景（2026-11-09 事故）：
提交 40e6b2553f 移植上游「冬季赛季」代码时，info_handler.py 的
handle_urgent_commission 引用了 FLEET_UNAVAILABLE，但
module/handler/assets.py 里没有这个资产定义，也没有对应 PNG。

影响：handle_urgent_commission 处在几乎所有任务的公共路径
（ui_additional / handle_app_login 都会调），一旦跑到就
NameError: name 'FLEET_UNAVAILABLE' is not defined，任务连环失败，
还会被"未处理异常 3 次"判定拉去重启模拟器，最终停摆。

为什么 lint 抓不到：info_handler.py 用 from module.handler.assets import *，
ruff/pyflakes 遇到通配导入会放弃判断名字是否存在；CI 又显式 --ignore F821。

本测试锁住两件事：资产对象存在且参数正确；模板图能被真实加载
（appear(offset=...) 走模板匹配，缺 PNG 同样会炸）。
"""
import unittest

from module.base.utils import load_image
from module.handler.assets import FLEET_UNAVAILABLE


class TestFleetUnavailableAsset(unittest.TestCase):
    def test_asset_is_defined(self):
        """资产必须存在且坐标与上游一致。"""
        self.assertEqual(FLEET_UNAVAILABLE.area, (412, 333, 813, 357))
        self.assertEqual(FLEET_UNAVAILABLE.button, (553, 481, 728, 541))
        self.assertEqual(FLEET_UNAVAILABLE.color, (140, 146, 150))

    def test_template_image_loads(self):
        """模板图必须存在且可加载（1280x720 全屏图，由 area 裁剪）。"""
        image = load_image(FLEET_UNAVAILABLE.file)
        self.assertEqual(image.shape, (720, 1280, 3))


if __name__ == '__main__':
    unittest.main()
