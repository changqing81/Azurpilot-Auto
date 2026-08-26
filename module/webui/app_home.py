"""WebUI首页和会话运行"""
import base64
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from module.webui.app_dependencies import (
    State,
    Switch,
    _t,
    actions,
    file_upload,
    input_group,
    alas_instance,
    eval_js,
    get_localstorage_values,
    get_window_visibility_state,
    go_app,
    is_oobe_needed,
    json,
    lang,
    load_webui_styles,
    logger,
    pin_on_change,
    put_buttons,
    put_html,
    put_input,
    put_markdown,
    put_text,
    register_thread,
    run_js,
    set_env,
    set_localstorage,
    t,
    threading,
    time,
    toast,
    updater,
    use_scope,
)


from module.webui.app_types import WebUIMixinBase


# Pixiv 图片反代域名列表，用于壁纸加载时并发测速，选中其中可访问且时延最低的
# 一个。反代服务可能随时变更或下线，如需新增/调整候选，请维护此列表即可。
_PIXIV_PROXY_DOMAINS = [
    "i.pixiv.re",
    "i.pixiv.nl",
    "pixiv.yuki.sh",
    "proxy.pixivel.moe",
    "i.yuki.sh",
    "i.suimoe.com",
    "pximg.cocomi.eu.org",
    "pximg.obfs.dev",
]

# LOLICON 图源接口
_LOLICON_API = "https://api.lolicon.app/setu/v2"
# nyan.run 图源接口，返回的图片地址已自带 Pixiv 反代，无需再走额外反代域名
_NYAN_API = "https://sex.nyan.run/api/v2/"

# 自定义背景图片扩展名 → MIME 映射
_CUSTOM_BG_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


# 主页右下角"纯背景模式"圆点的常驻注入脚本。
# 通过 JS 挂到 document 顶层，保证幂等且样式常驻：
# - 圆点仅存在一个（不存在才创建），定位样式常驻 head，切页后不会丢失；
# - 仅在 body 带有 alas-wallpaper-toggle-visible 类时显示，其余页面隐藏，
#   既符合"仅在主页显示"的设计，也不会退化为块级元素撑出垂直滚动条。
_WALLPAPER_TOGGLE_JS = r"""
(function () {
    function ensureStyle() {
        if (document.getElementById('alas-wallpaper-style')) return;
        var css = [
            '#alas-wallpaper-toggle{position:fixed;bottom:10px;right:10px;z-index:99999;width:24px;height:24px;border-radius:50%;background:rgba(255,255,255,0.6);backdrop-filter:blur(6px);border:1px solid rgba(0,0,0,0.12);display:none;align-items:center;justify-content:center;cursor:pointer;font-size:11px;line-height:1;box-shadow:0 1px 4px rgba(0,0,0,0.2);user-select:none;transition:background 0.15s;}',
            '#alas-wallpaper-toggle:hover{background:rgba(255,255,255,0.9);}',
            'body.alas-wallpaper-toggle-visible #alas-wallpaper-toggle{display:flex;}',
            'body.alas-wallpaper-mode #pywebio-scope-content,body.alas-wallpaper-mode #pywebio-scope-header,body.alas-wallpaper-mode #pywebio-scope-aside,body.alas-wallpaper-mode #pywebio-scope-menu{display:none !important;}'
        ].join('\n');
        var style = document.createElement('style');
        style.id = 'alas-wallpaper-style';
        style.type = 'text/css';
        style.appendChild(document.createTextNode(css));
        document.head.appendChild(style);
    }
    function ensureToggle() {
        var el = document.getElementById('alas-wallpaper-toggle');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'alas-wallpaper-toggle';
        el.title = '纯背景模式';
        el.textContent = '\u25C9';
        el.onclick = function () {
            document.body.classList.toggle('alas-wallpaper-mode');
        };
        document.body.appendChild(el);
        return el;
    }
    ensureStyle();
    ensureToggle();
    window.alasToggleWallpaper = function () {
        document.body.classList.toggle('alas-wallpaper-mode');
    };
    window.alasWallpaperToggle = function (visible) {
        ensureStyle();
        ensureToggle();
        document.body.classList.toggle('alas-wallpaper-toggle-visible', visible);
    };
    // 主页渲染默认显示
    document.body.classList.add('alas-wallpaper-toggle-visible');
})();
"""


class HomeMixin(WebUIMixinBase):
    """WebUI首页和会话运行"""

    def show(self) -> None:
        self.mount_shell()
        self.show_home()

    def show_home(self) -> None:
        self.mount_shell()
        self._set_manage_mode(False)
        self._active_aside = "Home"
        self.init_aside(name="Home")
        self.dev_set_menu()
        self.init_menu(name="HomePage")
        self.set_title(t("Gui.MenuDevelop.HomePage"))
        self.alas_name = ""
        self._menu_rendered = False
        self._menu_data_sig = None
        if hasattr(self, "alas"):
            del self.alas
        self.set_status(0)

        def set_language(l):
            lang.set_language(l)
            self.show_home()
            self.refresh_aside_labels()

        def set_theme(t):
            self.set_theme(t)
            set_localstorage("aside", "Home")
            go_app("index", new_window=False)

        if self.wallpaper_url:
            put_html(
                f"""
                <style>
                :root {{
                    --alas-apple-bg-image: url("{self.wallpaper_url}");
                }}
                </style>
                """
            )

        with use_scope("content"):
            put_text("Select your language / 选择语言").style(
                "text-align: center; font-weight: 600"
            )
            put_buttons(
                [
                    {"label": "简体中文", "value": "zh-CN"},
                    {"label": "喵体中文", "value": "zh-MIAO"},
                    {"label": "繁體中文", "value": "zh-TW"},
                    {"label": "English", "value": "en-US"},
                    {"label": "日本語", "value": "ja-JP"},
                ],
                onclick=lambda l: set_language(l),
            ).style("text-align: center")
            put_text("Change theme / 更改主题").style("text-align: center")
            put_buttons(
                [
                    {"label": "Light", "value": "default", "color": "light"},
                    {"label": "Dark", "value": "dark", "color": "dark"},
                    {
                        "label": "高级材质",
                        "value": "advanced_material",
                        "color": "primary",
                    },
                    {
                        "label": "高级材质（暗色）",
                        "value": "dark_advanced_material",
                        "color": "dark",
                    },
                ],
                onclick=lambda t: set_theme(t),
            ).style("text-align: center")
            put_buttons(
                [
                    {
                        "label": "下载当前背景图",
                        "value": "download",
                        "color": "light",
                    }
                ],
                onclick=lambda _: self.download_wallpaper(),
            ).style(
                "text-align: center"
            )
            put_text("Background / 背景").style("text-align: center; font-weight: 600")
            put_buttons(
                [
                    {
                        "label": "上传自定义背景",
                        "value": "upload",
                        "color": "light",
                    },
                    {
                        "label": "随机背景",
                        "value": "random",
                        "color": "dark",
                    },
                ],
                onclick=[
                    lambda _: self._upload_custom_background(),
                    lambda _: self._switch_to_random_background(),
                ],
            ).style("text-align: center")
            put_html('<div class="alas-home-marker" aria-hidden="true"></div>')
            # 一次性、常驻注入右下角"纯背景模式"圆点，仅在主页显示
            self._inject_wallpaper_toggle()
            # show something
            put_markdown(
                """
            AzurPilot 是基于上游项目 Alas (AzurLaneAutoScript) 的修改版本，采用 GPL-3.0 许可证，免费开源。如果你在任何渠道付费购买，那你一定是个大傻逼，请申请退款。

            """
            ).style("text-align: center")

        if lang.TRANSLATE_MODE:
            lang.reload()

            def _disable():
                lang.TRANSLATE_MODE = False
                self.show_home()

            toast(
                _t("Gui.Toast.DisableTranslateMode"),
                duration=0,
                position="right",
                onclick=_disable,
            )
    def _inject_wallpaper_toggle(self) -> None:
        """一次性、常驻注入右下角"纯背景模式"圆点。

        圆点及其定位样式通过 JS 直接挂到 document 顶层，并常驻用于幂等注入：
        - 圆点常驻且仅在 ``body.alas-wallpaper-toggle-visible`` 时显示，避免每次
          进入主页重复创建、以及切页后样式被清空导致圆点退化为块级元素撑出
          垂直滚动条；
        - 主页加载时默认显示，其余页面由 base.py 的导航钩子调用
          ``window.alasWallpaperToggle(visible)`` 控制隐藏。
        """
        run_js(_WALLPAPER_TOGGLE_JS)

    def init_wallpaper(self):
        """异步获取壁纸 URL，页面先渲染不阻塞。

        若用户启用了自定义背景且图片存在，则直接应用自定义背景；否则在后台
        线程并发尝试候选图源（LOLICON 多反代测速、nyan.run），先返回有效图片
        地址者胜出并应用，成功后通过 JS 动态注入，避免网络请求阻塞页面首次渲染。
        """
        if getattr(self, "wallpaper_url", None):
            return

        # 标记为空字符串，避免重复触发
        self.wallpaper_url = ""

        # 用户启用了自定义背景且存在自定义图片时，直接应用并跳过随机图源
        if self._load_background_mode() == "custom":
            data_url = self._custom_background_data_url()
            if data_url:
                self._inject_custom_background(data_url)
                logger.info("[WebUI] 已应用自定义背景")
                return

        def _fetch_wallpaper():
            # 并发尝试两个图源，先返回有效图片地址者胜出并应用，响应快者优先
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(self._fetch_lolicon_wallpaper),
                    executor.submit(self._fetch_nyan_wallpaper),
                ]
                for fut in as_completed(futures):
                    image_url = fut.result()
                    if image_url:
                        self._apply_wallpaper(image_url)
                        return
            logger.info("[WebUI] 所有图源获取壁纸失败，已跳过")

        thread = threading.Thread(target=_fetch_wallpaper, daemon=True)
        register_thread(thread)
        thread.start()

    def _fetch_lolicon_wallpaper(self):
        """从 LOLICON 获取壁纸：先取一张图的路径，再并发对候选反代各自测速，
        返回可访问且时延最低的反代图片地址；图源不可用时返回 None。
        """
        try:
            response = requests.get(
                _LOLICON_API,
                params={
                    "r18": 0,
                    "num": 1,
                    "size": "original",
                    "excludeAI": True,
                    "aspectRatio": "gt1",
                    "dsc": False,
                    "tag": "碧蓝航线|AzurLane|Azur Lane|アズールレーン",
                },
                timeout=10,
            )
            response.raise_for_status()

            # 仅保留路径部分，便于再用不同反代域名拼接后对比速度
            image_path = urlparse(
                response.json()["data"][0]["urls"]["original"]
            ).path
            if not image_path:
                return None
        except Exception as e:
            logger.info(f"[WebUI] LOLICON 获取图源失败: {e}")
            return None

        # 并发对同一图片各自测速，返回顺序与输入一致
        with ThreadPoolExecutor(
            max_workers=len(_PIXIV_PROXY_DOMAINS)
        ) as executor:
            results = executor.map(
                lambda p: (p, self._probe_image(f"https://{p}{image_path}")),
                _PIXIV_PROXY_DOMAINS,
            )

        # 过滤出可访问的反代，取其中时延最低者
        reachable = [
            (latency, proxy) for proxy, (latency, ok) in results if ok
        ]
        if reachable:
            _, best = min(reachable, key=lambda x: x[0])
            logger.info(f"[WebUI] 测速选中最快反代 [{best}]")
            return f"https://{best}{image_path}"

        # 全部不可访问时回退到列表首项，保证至少能尝试渲染
        logger.info("[WebUI] 所有反代测速均不可访问，回退使用首个反代")
        return f"https://{_PIXIV_PROXY_DOMAINS[0]}{image_path}"

    def _fetch_nyan_wallpaper(self):
        """从 nyan.run 获取壁纸：该图源返回的图片地址自带 Pixiv 反代，可直接使用。
        返回图片地址；未取到有效数据时返回 None。
        """
        try:
            response = requests.get(
                _NYAN_API,
                params={
                    "r18": 0,
                    "num": 1,
                    "keyword": "azur lane",
                },
                timeout=10,
            )
            response.raise_for_status()

            payload = response.json()
            if not payload.get("data"):
                logger.info("[WebUI] nyan.run 未返回有效图片数据")
                return None
            return payload["data"][0]["url"]
        except Exception as e:
            logger.info(f"[WebUI] nyan.run 获取图源失败: {e}")
            return None

    @staticmethod
    def _probe_image(url, timeout=8):
        """探测图片反代加载时延与可访问性，返回 (时延秒, 是否可访问)。

        流式请求只取首块，记录响应耗时，不下载完整图片。
        """
        start = time.perf_counter()
        try:
            resp = requests.get(url, timeout=timeout, stream=True)
            ok = resp.status_code == 200
            latency = time.perf_counter() - start
            resp.close()
            return latency, ok
        except Exception:
            return float("inf"), False

    def _apply_wallpaper(self, image_url):
        """应用壁纸：记录 URL 并通过 JS 注入 CSS 变量切换背景。"""
        self.wallpaper_url = image_url
        logger.info(f"[WebUI] 当前背景图: {self.wallpaper_url}")

        css_value = f'url("{image_url}")'
        run_js(
            'document.documentElement.style.setProperty('
            '"--alas-apple-bg-image", '
            f'{json.dumps(css_value)}'
            ');'
        )

    # ---------- 自定义背景 ----------

    def _wallpapers_dir(self) -> Path:
        """壁纸保存目录。"""
        return Path(__file__).resolve().parents[2] / "wallpapers"

    def _background_mode_file(self) -> Path:
        """自定义背景模式配置文件路径。"""
        return self._wallpapers_dir() / "background_setting.json"

    def _load_background_mode(self) -> str:
        """读取当前背景模式，默认随机（"random"）。"""
        try:
            data = json.loads(
                self._background_mode_file().read_text(encoding="utf-8")
            )
            return data.get("mode", "random")
        except Exception:
            return "random"

    def _save_background_mode(self, mode: str) -> None:
        """保存当前背景模式："random" 随机图源 / "custom" 自定义图片。"""
        try:
            self._background_mode_file().write_text(
                json.dumps({"mode": mode}), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[WebUI] 保存背景模式失败: {e}")

    def _custom_background_data_url(self) -> str:
        """构造自定义背景的 data URL；无自定义图片时返回空字符串。"""
        try:
            files = sorted(self._wallpapers_dir().glob("custom_background.*"))
            if not files:
                return ""
            content = files[0].read_bytes()
            ext = files[0].suffix.lower()
            mime = _CUSTOM_BG_MIMES.get(ext, "image/png")
            data_url = f"data:{mime};base64," + base64.b64encode(
                content
            ).decode("ascii")
            return data_url
        except Exception:
            return ""

    def _inject_custom_background(self, data_url: str) -> None:
        """注入自定义背景：以带 !important 的 body 规则覆盖所有主题背景。"""
        run_js(
            """
            (function () {
                var css = 'body{'
                    + 'background-image:url("%s") !important;'
                    + 'background-repeat:no-repeat !important;'
                    + 'background-size:cover !important;'
                    + 'background-attachment:fixed !important;'
                    + 'background-position:center !important;'
                    + '}';
                var el = document.getElementById('alas-custom-bg-style');
                if (el) { el.textContent = css; }
                else {
                    el = document.createElement('style');
                    el.id = 'alas-custom-bg-style';
                    el.textContent = css;
                    document.head.appendChild(el);
                }
            })();
            """
            % data_url
        )

    def _clear_custom_background(self) -> None:
        """移除自定义背景注入样式，恢复主题默认背景。"""
        run_js(
            "(function(){var el=document.getElementById('alas-custom-bg-style');"
            "if(el){el.parentNode.removeChild(el);}})();"
        )

    def _upload_custom_background(self) -> None:
        """上传自定义背景图片并立即应用，同时对所有主题生效。"""
        resp = input_group(
            label="上传自定义背景",
            inputs=[
                file_upload(
                    label="选择图片（PNG/JPG/WebP 等）",
                    name="file",
                    placeholder="选择图片",
                    accept="image/*",
                    required=True,
                    max_size="5M",
                ),
                actions(
                    name="action",
                    buttons=[
                        {
                            "label": "上传",
                            "value": "confirm",
                            "type": "submit",
                            "color": "primary",
                        },
                        {
                            "label": "取消",
                            "type": "cancel",
                            "color": "light",
                        },
                    ],
                ),
            ],
        )
        if resp is None:
            return

        upload = resp["file"]
        content = upload["content"]
        filename = upload["filename"]

        ext = Path(filename).suffix.lower()
        if ext not in _CUSTOM_BG_MIMES:
            ext = ".png"

        wallpapers_dir = self._wallpapers_dir()
        wallpapers_dir.mkdir(parents=True, exist_ok=True)
        # 只保留一份自定义背景，避免多文件读取歧义
        for old in wallpapers_dir.glob("custom_background.*"):
            old.unlink(missing_ok=True)
        target = wallpapers_dir / f"custom_background{ext}"
        target.write_bytes(content)

        self._save_background_mode("custom")
        mime = _CUSTOM_BG_MIMES[ext]
        data_url = f"data:{mime};base64," + base64.b64encode(content).decode(
            "ascii"
        )
        self._inject_custom_background(data_url)
        toast(f"自定义背景已应用: {target.resolve()}", color="success")
        logger.info(f"[WebUI] 自定义背景已保存: {target.resolve()}")

    def _switch_to_random_background(self) -> None:
        """切换回随机背景：清理自定义背景注入并重新拉取随机壁纸。"""
        self._save_background_mode("random")
        self._clear_custom_background()
        # 重置后重新触发随机图源加载
        self.wallpaper_url = ""
        self.init_wallpaper()
        toast("已切换为随机背景", color="success")

    def download_wallpaper(self):
        """
        保存当前背景图：随机背景下从远程 URL 下载；自定义背景下直接复制本地图片。
        """
        # 自定义背景：当前展示的是本地文件，直接复制一份到壁纸目录
        if self._load_background_mode() == "custom":
            files = sorted(self._wallpapers_dir().glob("custom_background.*"))
            if not files:
                toast(
                    "当前没有自定义背景图",
                    color="error",
                )
                return
            src = files[0]
            filename = time.strftime(
                f"wallpaper_%Y-%m-%d_%H-%M-%S{src.suffix.lower()}"
            )
            file_path = (self._wallpapers_dir() / filename).resolve()
            file_path.write_bytes(src.read_bytes())
            toast(
                f"已保存当前背景: {file_path}",
                color="success",
            )
            logger.info(f"[WebUI] 背景图已保存: {file_path}")
            return

        if not getattr(self, "wallpaper_url", None):
            toast(
                "当前没有背景图地址",
                color="error",
            )
            return

        try:
            response = requests.get(
                self.wallpaper_url,
                timeout=10,
            )
            response.raise_for_status()

            # 按 URL 后缀推断图片格式，避免一律存成 .jpg 与实际格式不符
            ext = Path(urlparse(self.wallpaper_url).path).suffix or ".jpg"
            filename = time.strftime(f"wallpaper_%Y-%m-%d_%H-%M-%S{ext}")

            # 统一保存到项目根目录下的 wallpapers 文件夹，目录不存在时自动创建
            wallpaper_dir = Path(__file__).resolve().parents[2] / "wallpapers"
            wallpaper_dir.mkdir(parents=True, exist_ok=True)
            file_path = (wallpaper_dir / filename).resolve()

            with open(file_path, "wb") as f:
                f.write(response.content)

            toast(
                f"下载完成，已保存到: {file_path}",
                color="success",
            )

            logger.info(
                f"[WebUI] 背景图已保存: {file_path}"
            )

        except Exception as e:
            logger.error(
                f"[WebUI] 下载背景图失败: {e}"
            )

            toast(
                f"下载失败: {e}",
                color="error",
            )

    def _fetch_announcement_thread(self, force=False):
        """
        在后台线程中获取公告数据（非阻塞）
        """
        try:
            from module.base.api_client import ApiClient

            data = ApiClient.get_announcement(timeout=10)
            self._announcement_result = {"data": data, "force": force}
        except Exception as e:
            logger.error(f"[WebUI-主页] 获取公告失败: {e}")
            self._announcement_result = {"data": None, "force": force, "error": str(e)}
        finally:
            self._announcement_fetching = False
            # 后台请求完成后立即唤醒会话调度器，无需依靠高频轮询收结果。
            self.task_handler.wake_task("announcement_checker")

    def _start_announcement_fetch(self, force=False):
        """
        启动异步公告获取。如果已在获取中则跳过。
        """
        if self._announcement_fetching:
            return
        self._announcement_fetching = True
        self._announcement_result = None
        threading.Thread(
            target=self._fetch_announcement_thread, args=(force,), daemon=True
        ).start()

    def _process_announcement_result(self):
        """
        处理异步获取的公告结果并推送到前端。
        在 TaskHandler 循环中调用（非阻塞）。
        Returns:
            True 如果结果已处理，False 如果还在等待
        """
        if self._announcement_fetching or self._announcement_result is None:
            return False

        result = self._announcement_result
        self._announcement_result = None

        # 请求结果与请求发起时的 force 一起打包，避免读到旧请求的过期状态
        data = result.get("data")
        force = result.get("force", False)
        error = result.get("error")

        if error:
            if force:
                toast(f"Check failed: {error}", color="error")
            return True

        if data:
            announcement_id = data.get("announcementId")

            # If force is False, check if we need to update
            if not force:
                if announcement_id and announcement_id == self._last_announcement_id:
                    return True

                # Check if browser has seen it (only if not forced)
                try:
                    announcement_id_json = json.dumps(announcement_id)
                    has_shown = eval_js(
                        f"window.alasHasBeenShown({announcement_id_json})"
                    )
                    if has_shown:
                        self._last_announcement_id = announcement_id
                        return True
                except Exception:
                    pass

            title_json = json.dumps(data.get("title", ""))
            content_json = json.dumps(data.get("content", ""))
            announcement_id_json = json.dumps(announcement_id)
            url_json = json.dumps(data.get("url", ""))
            force_json = "true" if force else "false"

            logger.info(f"[WebUI-主页] 推送公告: {data.get('title')}")
            run_js(
                f"window.alasShowAnnouncement({title_json}, {content_json}, {announcement_id_json}, {url_json}, {force_json});"
            )

            # Pushing to launcher
            from module.notify.notify import notify_webui

            notify_webui(
                instance="Alas",
                title=data.get("title", ""),
                content=data.get("content", ""),
                updata=False,
            )

            self._last_announcement_id = announcement_id

        elif force:
            toast("暂无公告 / No announcement", color="info")

        return True

    def ui_check_announcement(self, force=False) -> None:
        """
        Check for announcements (non-blocking).
        Starts async fetch; result is processed in announcement_checker.
        Args:
            force (bool): If True, show announcement even if already shown.
        """
        if force:
            if self._announcement_fetching:
                # 已有请求在途：记下 force 意图并丢弃旧结果，
                # 请求完成后 checker 会立即按新的 force 重新拉取。
                self._announcement_force = force
                self._announcement_result = None
            else:
                self._announcement_force = False
        self._start_announcement_fetch(force=force)
        if force:
            toast("正在获取公告... / Fetching announcement...", color="info")

    def _load_deferred_client_assets(self) -> None:
        """在首次绘制后再加载非关键的分析和交互脚本。"""
        run_js(
            "(function() {"
            "function load() {"
            "if (!document.getElementById('microsoft-clarity-script')) {"
            "(function(c,l,a,r,i,t,y){"
            "c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};"
            "t=l.createElement(r);t.id='microsoft-clarity-script';t.async=1;"
            "t.src='https://www.clarity.ms/tag/'+i;"
            "y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);"
            "})(window,document,'clarity','script','xszl2nrp3q');"
            "}"
            "if (!document.querySelector('link[rel=\"manifest\"]')) {"
            "var manifest=document.createElement('link');"
            "manifest.rel='manifest';manifest.href='static/assets/spa/manifest.json';"
            "document.head.appendChild(manifest);"
            "}"
            "if (!document.getElementById('alas-utils-script')) {"
            "var script=document.createElement('script');"
            "script.id='alas-utils-script';script.async=true;"
            "script.src='static/assets/gui/js/alas-utils.js';"
            "document.head.appendChild(script);"
            "}"
            "}"
            "if (window.requestIdleCallback) {"
            "window.requestIdleCallback(load, {timeout: 3000});"
            "} else { window.setTimeout(load, 0); }"
            "})();"
        )

    def run(self, initial_page="home", localstorage=None) -> None:
        # 初始化背景图
        self.init_wallpaper()
        # setup gui
        set_env(title="AzurPilot", output_animation=False)
        load_webui_styles(theme=self.theme, is_mobile=self.is_mobile)

        # OOBE 不依赖浏览器偏好，直接绘制，避免一次无意义的 WebSocket 往返。
        if is_oobe_needed():
            from module.webui.oobe import OOBEWizard

            OOBEWizard(self).start()
            self._load_deferred_client_assets()
            return

        # 先发送页面骨架，再读取恢复页面所需的 localStorage。即使浏览器端
        # RPC 较慢，用户也能立即看到真实外壳，且该读取不再阻塞首条内容。
        self.mount_shell()

        # 窗口可见性改为前端事件推送：visibilitychange 触发时通过 Pin 回调
        # 上报状态，服务端 Switch 只读缓存，不再阻塞式 eval_js 轮询浏览器。
        # 必须在 mount_shell() 之后创建隐藏输入框——_show() 会 clear ROOT，
        # 提前创建会被抹掉。
        if not getattr(self, "_visibility_listener_installed", False):
            self._visibility_listener_installed = True
            from module.webui.utils import set_window_visibility_state

            def _on_visibility_change(visible):
                # 前端上报的是字符串 "True"/"False"
                set_window_visibility_state(str(visible).lower() == "true")

            put_input(
                name="__alas_window_visible",
                value="True",
            ).style("display:none")
            pin_on_change(
                name="__alas_window_visible",
                onchange=_on_visibility_change,
                clear=True,
                serial_mode=True,
            )
            run_js(
                """
                (function () {
                    if (window.__alasVisibilityHooked) return;
                    window.__alasVisibilityHooked = true;
                    var attempts = 0;
                    function install() {
                        var input = document.querySelector(
                            'input[name="__alas_window_visible"]'
                        );
                        if (!input) {
                            attempts += 1;
                            if (attempts < 20) window.setTimeout(install, 100);
                            return;
                        }
                        var notify = function () {
                            var visible = !document.hidden;
                            if (input.value === String(visible)) return;
                            input.value = String(visible);
                            input.dispatchEvent(new Event('input', {bubbles: true}));
                            input.dispatchEvent(new Event('change', {bubbles: true}));
                        };
                        document.addEventListener('visibilitychange', notify);
                        notify();
                    }
                    install();
                })();
                """
            )

        if localstorage is None:
            localstorage = get_localstorage_values(("clarity_notice_shown", "aside"))
        aside = localstorage.get("aside")
        self._stored_aside = aside
        show_clarity_notice = localstorage.get("clarity_notice_shown") != "1"
        restore_instance = initial_page == "home" and aside in alas_instance()
        if initial_page == "manage":
            self.ui_manage()
        elif not restore_instance:
            self.show_home()

        # save config
        _thread_save_config = threading.Thread(target=self._alas_thread_update_config)
        register_thread(_thread_save_config)
        _thread_save_config.start()

        visibility_state_switch = Switch(
            status={
                True: [
                    lambda: self.__setattr__("visible", True),
                    lambda: (
                        self.alas_update_overview_task()
                        if self.page == "Overview"
                        else 0
                    ),
                    lambda: self.task_handler._task.__setattr__("delay", 15),
                ],
                False: [
                    lambda: self.__setattr__("visible", False),
                    lambda: self.task_handler._task.__setattr__("delay", 1),
                ],
            },
            get_state=get_window_visibility_state,
            name="visibility_state",
        )

        self.state_switch = Switch(
            status=self.set_status,
            get_state=lambda: getattr(getattr(self, "alas", -1), "state", 0),
            name="state",
        )

        def goto_update():
            self.ui_develop()
            self.dev_update()
            self._close_update_notice()

        def show_update_toast():
            if self._update_notified:
                return
            if State.deploy_config.HideUpdateNotice:
                return
            self._update_notified = True

            from module.notify.notify import notify_webui

            notify_webui(
                instance="Alas",
                title=t("Gui.Toast.ClickToUpdate"),
                content="检测到了新更新喵~ 指挥官快来更新喵~",
                updata=True,
            )

            self._show_update_notice(goto_update)

        update_switch = Switch(
            status={1: show_update_toast},
            get_state=lambda: updater.state,
            name="update_state",
        )

        self.task_handler.add(self.state_switch.g(), 2)
        self.task_handler.add(self.set_aside_status, 2)
        self.task_handler.add(visibility_state_switch.g(), 15)
        self.task_handler.add(update_switch.g(), 1)

        # 公告检查功能（非阻塞）
        def announcement_checker():
            from module.base.api_client import ApiClient

            logger.info("[WebUI] 公告检查任务启动")
            th = yield  # 获取任务处理器引用
            # 首次检查：触发异步获取
            self._start_announcement_fetch(force=False)
            next_periodic_check = time.time() + ApiClient.ANNOUNCEMENT_CHECK_INTERVAL
            while True:
                # 处理已有结果（来自定期检查或手动点击）
                self._process_announcement_result()
                # 手动点击强制查看且请求已结束时，立即发起强制拉取。
                # 标记仅在“已有请求在途、需要延迟重拉”时由 ui_check_announcement 置位，
                # 发起的强制请求完成后会再次进入这里，此时标记已清除，不会重复拉取。
                if (
                    self._announcement_force
                    and not self._announcement_fetching
                    and self._announcement_result is None
                ):
                    self._start_announcement_fetch(force=True)
                    self._announcement_force = False
                # 定期触发新的异步获取
                if (
                    not self._announcement_fetching
                    and time.time() >= next_periodic_check
                ):
                    self._start_announcement_fetch(force=False)
                    next_periodic_check = (
                        time.time() + ApiClient.ANNOUNCEMENT_CHECK_INTERVAL
                    )
                # 后台线程会在完成时主动唤醒；1 秒仅作为请求中的兜底。
                # 空闲时直接睡到下次周期检查，避免每个会话持续轮询。
                if self._announcement_fetching:
                    th._task.delay = 1
                else:
                    remaining = max(0.25, next_periodic_check - time.time())
                    th._task.delay = remaining
                yield

        # 首次立即执行，后续间隔由任务自身根据请求状态动态调整。
        self.task_handler.add(announcement_checker(), delay=5)

        if restore_instance:
            self.ui_alas(aside)

        if show_clarity_notice:
            set_localstorage("clarity_notice_shown", "1")
            toast(
                "本 WebUI 使用 Microsoft Clarity 收集页面访问、点击交互和性能数据，用于分析并改进使用体验。",
                color="info",
                duration=12,
            )
        self._load_deferred_client_assets()

        # 启动任务处理器
        self.task_handler.start()
