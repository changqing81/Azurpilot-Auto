"""
伪造 PIL 模块。

在子进程启动时注入虚拟的 PIL 模块到 sys.modules，避免加载真实的
图像处理库。用于减少进程管理器等非图像处理场景的启动开销。

⚠️ 两个函数都是「真实 PIL 友好」的：
- 真实 PIL 已在 sys.modules 时 `import_fake_pil_module()` **不替换**。替换会让
  后续 `from PIL import Image` 拿到假模块；更糟的是 `remove_fake_pil_module()`
  弹掉真模块后再导入，会得到一个「插件模块已缓存、不再注册」的空注册表 Image，
  此后 `Image.open()` 对任何图片都抛 UnidentifiedImageError。
- `remove_fake_pil_module()` 只在假模块在位时移除，绝不弹真实 PIL。

全量单测里 WebUI 测试先导入 `module.webui.process_manager`（导入即注入），
若没有上述保护就会污染后续所有用 PIL 解码的测试（仓库已知问题，参见
`tests/test_statistics_amount_digits.py` 的说明）。
"""

import sys
from types import ModuleType

_FAKE_FLAG = '__alas_fake_pil__'


def import_fake_pil_module():
    current = sys.modules.get('PIL')
    if current is not None and not getattr(current, _FAKE_FLAG, False):
        # 真实 PIL 已加载：此时替换既省不下开销，还会破坏其解码器注册表。
        return
    fake_pil_module = ModuleType('PIL')
    setattr(fake_pil_module, _FAKE_FLAG, True)
    fake_pil_module.Image = ModuleType('PIL.Image')
    fake_pil_module.Image.Image = type('MockPILImage', (), dict(__init__=None))
    sys.modules['PIL'] = fake_pil_module
    sys.modules['PIL.Image'] = fake_pil_module.Image


def remove_fake_pil_module():
    if getattr(sys.modules.get('PIL'), _FAKE_FLAG, False):
        sys.modules.pop('PIL', None)
        sys.modules.pop('PIL.Image', None)
