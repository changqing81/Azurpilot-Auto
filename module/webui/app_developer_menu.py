"""WebUI开发菜单和预览入口"""

from module.webui.app_dependencies import (
    go_app,
    lang,
    put_button,
    t,
    toast,
    use_scope,
)
from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked


class DeveloperMenuMixin(WebUIMixinBase):
    """WebUI开发菜单和预览入口"""

    @use_scope("menu", clear=True)
    def dev_set_menu(self) -> None:
        self.init_menu(collapse_menu=False, name="Develop")

        put_button(
            label=t("Gui.MenuDevelop.HomePage"),
            onclick=self.show_home,
            color="menu",
        ).style(f"--menu-HomePage--")

        # put_button(
        #     label=t("Gui.MenuDevelop.Translate"),
        #     onclick=self.dev_translate,
        #     color="menu",
        # ).style(f"--menu-Translate--")

        put_button(
            label=t("Gui.MenuDevelop.Update"),
            onclick=self.dev_update,
            color="menu",
        ).style(f"--menu-Update--")

        put_button(
            label=t("Gui.MenuDevelop.Remote"),
            onclick=self.dev_remote,
            color="menu",
        ).style(f"--menu-Remote--")

        put_button(
            label=t("Gui.MenuDevelop.Setting"),
            onclick=self.dev_setting,
            color="menu",
        ).style(f"--menu-Setting--")

        put_button(
            label=t("Gui.MenuDevelop.Announcement"),
            onclick=lambda: self.ui_check_announcement(force=True),
            color="menu",
        ).style(f"--menu-Announcement--")

        put_button(
            label=t("Gui.MenuDevelop.Utils"),
            onclick=self.dev_utils,
            color="menu",
        ).style(f"--menu-Utils--")

    def dev_translate(self) -> None:
        go_app("translate", new_window=True)
        lang.TRANSLATE_MODE = True
        self.show_home()

    def _preview_update_notice(self) -> None:
        def handle_preview_click():
            self._close_update_notice()
            toast("success", color="success")

        self._show_update_notice(handle_preview_click)

    @render_locked
    def ui_develop(self) -> None:
        if self.is_mobile and self._active_aside == "Home":
            # 手机端主页内容页会把二级菜单折叠隐藏（alas-mobile.css 将折叠类渲染
            # 为 display:none），主页成了唯一没有菜单入口的页面，远控时无法进入
            # 更新器/远程控制。与 ui_alas / ui_manage 的约定保持一致：再次点击
            # 已激活的侧栏图标仅展开二级菜单，不重渲染内容。
            self.expand_menu()
            return
        self.show_home()
