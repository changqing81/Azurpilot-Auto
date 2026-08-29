"""WebUI首页和会话运行"""
import base64
import re

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path
from urllib.parse import urlencode, urlparse

from module.webui.app_dependencies import (
    State,
    Switch,
    _t,
    actions,
    file_upload,
    input as _p_input,
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
    put_link,
    put_markdown,
    put_text,
    radio as _p_radio,
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
    popup,
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
# imgapi.lie.moe 图源接口：通用 JSON 随机图，返回 {"pic": [url, ...]}
_IMGAPI_LIEMOE_API = "https://imgapi.lie.moe/random"

# 内置默认图源列表：首次使用或“恢复默认”时以此为准。
# - type=lolicon 走专用反代测速逻辑（tag 等参数保持代码默认，不开放修改）
# - type=imgapi 走通用 JSON 提取逻辑
# - enabled 状态会在配置文件中持久化，用户可禁用/启用
_BUILTIN_SOURCES = [
    {
        "key": "lolicon",
        "type": "lolicon",
        "name": "LOLICON",
        "url": _LOLICON_API,
        "image_path": "data[0].url",
        "enabled": True,
    },
    {
        "key": "imgapi",
        "type": "imgapi",
        "name": "imgapi.lie.moe",
        "url": _IMGAPI_LIEMOE_API,
        "image_path": "pic[0]",
        "enabled": True,
    },
]
# 内置源专属类型标记；恢复默认仅作用于这些源
_BUILTIN_TYPES = {"lolicon", "imgapi"}

# 图源配置文件相关
# 配置文件存于 wallpapers/ 目录（已被 .gitignore 忽略，且不会被当成 Alas 配置识别）
_SOURCES_FILE_NAME = "wallpaper_sources.json"
# 自定义图源默认的图片地址 JSON 提取路径
_DEFAULT_IMAGE_PATH = "data[0].url"

# 自定义背景压缩参数：最长边与 JPEG 质量。大图会被缩放重编码，
# 控制内联进页面的 data URI 体积，适配远控低带宽环境
_CUSTOM_BG_MAX_EDGE = 1920
_CUSTOM_BG_JPEG_QUALITY = 82

# 自定义背景图片扩展名 → MIME 映射
_CUSTOM_BG_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# 视频背景扩展名集合（浏览器原生可播格式），上传时跳过图片压缩
_CUSTOM_VIDEO_EXTS = {".mp4", ".m4v", ".webm", ".mov"}

# 图源 URL 的视频后缀识别（供前端竞赛用 video 元素加载）
_VIDEO_URL_RE = re.compile(r"\.(mp4|m4v|webm|mov|ogv)(?:[?#]|$)", re.IGNORECASE)


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

# 背景图前端竞赛脚本（幂等注入，常驻全局）：
# 服务端把全部图源候选 URL 一次性交给浏览器，浏览器并发发起图片下载，
# 实际最先下载完成的图立即成为背景——竞赛标准从"API 响应速度"升级为
# "图片本体下载速度"，慢速 CDN 的图源不再拖慢背景显示。
# 胜者记录在 window.__alasWallpaperWinner，供"下载当前背景图"功能读取，
# 确保下载的就是用户当前看到的那张。
# 全部候选 30 秒内都未加载完成时保留原背景并清空胜者记录；
# 胜者决出后立即中止其余候选的下载，避免带宽浪费。
_WALLPAPER_RACE_JS = r"""
(function () {
    if (window.alasWallpaperRace) return;
    var done = false;
    var timer = null;
    var images = [];
    function removeVideoBg() {
        var v = document.getElementById('alas-bg-video');
        if (v) { v.parentNode.removeChild(v); }
    }
    function finish(winner, el) {
        if (done) return;
        done = true;
        if (timer) clearTimeout(timer);
        window.__alasWallpaperWinner = winner || null;
        // 冠军已决出，中止其余候选的下载，避免带宽浪费（已加载完成的不受影响）
        images.forEach(function (item) {
            if (item === el) return;
            try { item.src = ''; } catch (e) {}
        });
        if (winner && winner.video && el) {
            // 视频胜者：复用探测用的 video 元素铺满置底，静音循环播放
            removeVideoBg();
            el.id = 'alas-bg-video';
            el.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;'
                + 'object-fit:cover;z-index:-1;pointer-events:none;';
            el.loop = true;
            document.body.appendChild(el);
            var p = el.play();
            if (p && p.catch) { p.catch(function () {}); }
        } else if (winner && winner.url) {
            removeVideoBg();
            document.documentElement.style.setProperty(
                '--alas-apple-bg-image',
                'url("' + winner.url + '")'
            );
        }
    }
    function startOne(c) {
        var el;
        if (c.video) {
            el = document.createElement('video');
            el.muted = true;
            el.preload = 'auto';
            el.oncanplay = function () { finish(c, el); };
            el.onerror = function () {};
            el.src = c.url;
        } else {
            el = new Image();
            el.onload = function () { finish(c, el); };
            el.onerror = function () {};
            el.src = c.url;
        }
        images.push(el);
    }
    // 开赛：重置状态后并发加载全部候选（图片与视频混合竞赛）
    window.alasWallpaperRace = function (candidates) {
        done = false;
        images = [];
        if (timer) clearTimeout(timer);
        window.__alasWallpaperWinner = null;
        timer = setTimeout(function () { finish(null); }, 30000);
        candidates.forEach(startOne);
    };
    // 增量加入：后到的候选仅在竞赛尚未决出胜负时参与
    window.alasWallpaperRaceAppend = function (c) {
        if (!done) startOne(c);
    };
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
                    {
                        "label": "管理图源",
                        "value": "sources",
                        "color": "light",
                    },
                    {
                        "label": "媒体类型",
                        "value": "media",
                        "color": "light",
                    },
                ],
                onclick=[
                    self._upload_custom_background,
                    self._switch_to_random_background,
                    self._manage_wallpaper_sources,
                    self._set_media_preference,
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
        线程并发尝试全部启用的图源（内置 LOLICON 多反代测速、imgapi.lie.moe，
        以及用户添加的自定义 JSON 图源），收集全部有效候选后交给前端竞赛，
        浏览器并发下载候选图片，实际最快的图成为背景，避免网络请求阻塞页面
        首次渲染，也避免慢速 CDN 的图片拖慢背景显示。
        """
        if getattr(self, "wallpaper_url", None):
            return

        # 标记为空字符串，避免重复触发
        self.wallpaper_url = ""

        # 用户启用了自定义背景且存在自定义图片/视频时，直接应用并跳过随机图源
        if self._load_background_mode() == "custom":
            url, is_video = self._custom_background_url()
            if url:
                self._inject_custom_background(url, is_video)
                logger.info("[WebUI] 已应用自定义背景")
                return

        def _fetch_wallpaper():
            # 全部启用的图源并发请求；首个有效候选到达立即开赛，
            # 后到的候选增量加入竞赛，避免被最慢图源的超时卡住
            fetcher_items = []
            for source in self._load_sources():
                if not source.get("enabled", True):
                    continue
                source_type = source.get("type")
                if source_type == "lolicon":
                    fetcher_items.append(
                        (
                            source.get("name", "LOLICON"),
                            self._fetch_lolicon_wallpaper,
                            None,
                        )
                    )
                elif source_type == "imgapi":
                    fetcher_items.append(
                        (
                            source.get("name", "imgapi.lie.moe"),
                            self._fetch_imgapi_wallpaper,
                            None,
                        )
                    )
                else:
                    fetcher_items.append(
                        (
                            source.get("name", "自定义"),
                            partial(self._fetch_custom_source, source),
                            source,
                        )
                    )
            if not fetcher_items:
                logger.info("[WebUI] 没有启用的图源，已跳过")
                return

            raced = False
            # 媒体类型偏好：仅图片 / 仅视频 / 混合（auto），不符的候选直接丢弃
            media = self._load_media_preference()
            # 线程数等于图源数，上限 4：图源过多时排队请求，降低触发风控的风险
            with ThreadPoolExecutor(
                max_workers=min(4, len(fetcher_items))
            ) as executor:
                futures = {
                    executor.submit(func): (name, source)
                    for name, func, source in fetcher_items
                }
                for fut in as_completed(futures):
                    name, source = futures[fut]
                    try:
                        image_url = fut.result()
                    except Exception as e:
                        logger.info(f"[WebUI] 图源 [{name}] 异常: {e}")
                        continue
                    if not image_url:
                        continue
                    is_video = bool(_VIDEO_URL_RE.search(image_url))
                    if (media == "image" and is_video) or (
                        media == "video" and not is_video
                    ):
                        logger.info(
                            f"[WebUI] 图源 [{name}] 返回的媒体类型与偏好不符，已跳过"
                        )
                        continue
                    if not raced:
                        self._race_wallpaper(image_url, source)
                        raced = True
                    else:
                        self._append_race_candidate(image_url, source)

            if not raced:
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

    def _fetch_imgapi_wallpaper(self):
        """从 imgapi.lie.moe 获取壁纸：绿色健康随机二次元图，JSON 输出图片地址。
        返回图片地址；未取到有效数据时返回 None。
        """
        try:
            response = requests.get(
                _IMGAPI_LIEMOE_API,
                params={"type": "json"},
                timeout=10,
            )
            response.raise_for_status()
            pics = response.json().get("pic") or []
            if not pics:
                logger.info("[WebUI] imgapi.lie.moe 未返回有效图片数据")
                return None
            return pics[0]
        except Exception as e:
            logger.info(f"[WebUI] imgapi.lie.moe 获取图源失败: {e}")
            return None

    def _race_wallpaper(self, image_url, source=None):
        """用首个候选开启前端图片竞赛，实际下载最快的图成为背景。

        服务端以该候选作为兜底记录（前端竞赛结束前下载功能回退使用）；
        竞赛胜者由前端写入 window.__alasWallpaperWinner，下载功能读取该值
        以与实际显示保持一致。source 为直链图源时额外记录其配置，供下载
        功能识别——直链源服务端无法访问且每次请求返回新随机图，下载须交
        由浏览器处理。后续到达的候选由 _append_race_candidate 增量加入。
        """
        self.wallpaper_url = image_url
        self._direct_wallpaper_source = (
            {"name": (source or {}).get("name", "内置图源")}
            if source and source.get("direct")
            else None
        )
        logger.info(f"[WebUI] 首个候选背景图就绪，开启前端竞赛: {image_url}")

        # 幂等注入竞赛脚本后触发竞赛（消息按序执行，脚本必然先于调用就绪）
        run_js(_WALLPAPER_RACE_JS)
        self._append_race_candidate(image_url, source)

    def _append_race_candidate(self, image_url, source=None):
        """把单个候选加入尚未决出胜负的前端竞赛（已决出则忽略）。"""
        if not image_url:
            return
        payload = {
            "url": image_url,
            "name": (source or {}).get("name", "内置图源"),
            "direct": bool(source and source.get("direct")),
            "video": bool(_VIDEO_URL_RE.search(image_url)),
        }
        run_js(
            "window.alasWallpaperRaceAppend && "
            f"window.alasWallpaperRaceAppend({json.dumps(payload)});"
        )

    # ---------- 图源管理 ----------

    def _sources_file(self) -> Path:
        """自定义图源配置文件路径（wallpapers/ 已被 .gitignore 忽略）。"""
        return self._wallpapers_dir() / _SOURCES_FILE_NAME

    @staticmethod
    def _default_sources() -> list:
        """返回内置默认图源列表副本（lolicon / imgapi）。

        默认图源的 type、URL、tag 等参数由 _BUILTIN_SOURCES 决定，保持代码
        默认行为；enabled 状态会持久化到配置文件，用户可单独禁用或恢复。
        """
        return json.loads(json.dumps(_BUILTIN_SOURCES))

    def _load_sources(self) -> list:
        """读取全部图源配置（内置默认源 + 用户自定义源）。

        - 配置文件缺失或损坏时返回默认源列表（不写盘）；
        - 兼容旧版本 {"custom_sources": [...]} 结构，自动迁移为
          {"sources": [...]} 并补全内置默认源，自定义源保持不变；
        - 内置源以配置文件中持久化的 enabled 状态为准。
        """
        try:
            data = json.loads(
                self._sources_file().read_text(encoding="utf-8")
            )
        except Exception:
            return self._default_sources()
        if not isinstance(data, dict):
            return self._default_sources()

        sources = data.get("sources")
        if sources is None:
            # 兼容旧版本自定义源配置，迁移到新结构
            sources = [dict(s) for s in (data.get("custom_sources") or [])]
            for s in sources:
                s.setdefault("type", "custom")
            sources = self._default_sources() + sources
            self._save_sources(sources)
            return sources
        return [dict(s) for s in sources]

    def _save_sources(self, sources: list) -> None:
        """保存全部图源配置。"""
        try:
            self._wallpapers_dir().mkdir(parents=True, exist_ok=True)
            self._sources_file().write_text(
                json.dumps(
                    {"sources": sources},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[WebUI] 保存图源配置失败: {e}")

    @staticmethod
    def _get_by_path(data, path: str):
        """按点号/数字路径从 JSON 结构中提取值，支持 "data[0].url"、"[0].url" 等写法。"""
        if not path:
            return None
        node = data
        # 将 "[0]" 统一转换为 ".0" 便于按点号分段取值
        for seg in path.replace("[", ".").replace("]", "").split("."):
            seg = seg.strip()
            if not seg:
                continue
            if isinstance(node, list) and seg.isdigit():
                idx = int(seg)
                node = node[idx] if idx < len(node) else None
            elif isinstance(node, dict):
                node = node.get(seg)
            else:
                return None
            if node is None:
                return None
        return node

    def _fetch_custom_source(self, source: dict):
        """通用自定义图源获取：请求配置的 API 并提取图片地址。

        兼容两类接口：
        - JSON 型：按 image_path 从 JSON 响应中提取图片地址；
        - 直链型：API 直接返回图片二进制（Content-Type 为 image/*，
          如 api.yppp.net/api.php），此时将携带参数的 API 地址本身
          作为图片地址交由浏览器加载。
        提取结果必须是 http(s) 开头的字符串，否则视为无效。
        """
        url = (source.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            logger.info(f"[WebUI] 自定义图源 [{source.get('name')}] API 地址无效")
            return None
        params = source.get("params") or {}

        # 直链模式：跳过服务端探测，直接把 API 地址交给浏览器加载。
        # 部分站点（如 i.mukyu.ru）对非浏览器请求拖延响应，服务端探测
        # 必然超时，但浏览器可以正常访问，此时直链模式是唯一可行路径。
        if source.get("direct"):
            if params:
                query = urlencode(params)
                url += ("&" if "?" in url else "?") + query
            return url

        try:
            response = requests.get(
                url,
                params=params,
                timeout=10,
            )
            response.raise_for_status()

            # 直链型图源：响应本体就是图片
            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type.startswith("image/"):
                query = urlencode(params)
                return f"{url}?{query}" if query else url

            image_url = self._get_by_path(
                response.json(),
                (source.get("image_path") or _DEFAULT_IMAGE_PATH).strip(),
            )
            if isinstance(image_url, str) and image_url.startswith(
                ("http://", "https://")
            ):
                return image_url
            logger.info(f"[WebUI] 自定义图源 [{source.get('name')}] 未提取到有效图片地址")
            return None
        except Exception as e:
            logger.info(f"[WebUI] 自定义图源 [{source.get('name')}] 获取失败: {e}")
            return None

    def _set_media_preference(self) -> None:
        """设置随机背景的媒体类型偏好：仅图片 / 仅视频 / 混合。"""
        current = self._load_media_preference()
        resp = input_group(
            "背景媒体类型",
            [
                _p_radio(
                    label="随机背景使用的媒体类型",
                    name="media",
                    options=[
                        {
                            "label": "仅图片",
                            "value": "image",
                            "selected": current == "image",
                        },
                        {
                            "label": "仅视频",
                            "value": "video",
                            "selected": current == "video",
                        },
                        {
                            "label": "混合（图片和视频一起竞赛）",
                            "value": "auto",
                            "selected": current == "auto",
                        },
                    ],
                    required=True,
                ),
                actions(
                    name="cmd",
                    buttons=[
                        {
                            "label": "确定",
                            "value": "confirm",
                            "type": "submit",
                            "color": "primary",
                        },
                        {
                            "label": "取消",
                            "type": "cancel",
                        },
                    ],
                ),
            ],
        )
        if resp is None:
            return
        media = resp["media"]
        self._save_media_preference(media)
        label = {"image": "仅图片", "video": "仅视频", "auto": "混合"}.get(
            media, media
        )
        toast(f"背景媒体类型已切换: {label}", color="success")
        # 自定义背景下偏好暂不生效，切回随机背景后起作用
        if self._load_background_mode() == "custom":
            return
        self._refresh_random_wallpaper()

    def _refresh_random_wallpaper(self) -> None:
        """清空当前背景地址并重新从随机图源拉取一张（自定义背景下不覆盖）。"""
        self.wallpaper_url = ""
        self._direct_wallpaper_source = None
        # 同步清空前端竞赛胜者记录，避免下载功能读到上一次的旧图
        run_js("window.__alasWallpaperWinner = null;")
        if self._load_background_mode() == "custom":
            return
        self.init_wallpaper()

    def _manage_wallpaper_sources(self) -> None:
        """图源管理：添加/删除图源、启用禁用图源、恢复默认图源设置。"""
        action = input_group(
            "图源管理",
            [
                _p_radio(
                    label="选择操作",
                    name="action",
                    options=[
                        "添加图源",
                        "启用/禁用图源",
                        "删除图源",
                        "恢复默认图源设置",
                    ],
                    required=True,
                ),
                actions(
                    name="cmd",
                    buttons=[
                        {
                            "label": "确定",
                            "value": "ok",
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
        if not action:
            return
        operation = action["action"]
        if operation == "添加图源":
            self._add_custom_source_dialog()
        elif operation == "启用/禁用图源":
            self._toggle_source_dialog()
        elif operation == "删除图源":
            self._remove_source_dialog()
        elif operation == "恢复默认图源设置":
            self._reset_default_sources()

    def _add_custom_source_dialog(self) -> None:
        """弹窗填写自定义 JSON 图源信息并保存。"""
        resp = input_group(
            "添加图源",
            [
                _p_input(
                    label="名称（如：我的图源）",
                    name="name",
                    required=True,
                    placeholder="图源名称",
                ),
                _p_input(
                    label="API 地址（GET 请求，返回 JSON 或直接返回图片均可）",
                    name="url",
                    required=True,
                    placeholder="https://example.com/api/img",
                ),
                _p_input(
                    label="附加参数（可选），格式：key=value,key2=value2，如 keyword=azur lane,r18=0",
                    name="params",
                    placeholder="留空表示不带参数",
                ),
                _p_input(
                    label=f"图片地址 JSON 路径（可选），默认 {_DEFAULT_IMAGE_PATH}",
                    name="image_path",
                    value=_DEFAULT_IMAGE_PATH,
                ),
                _p_radio(
                    label="直链模式（API 直接返回图片但服务端探测超时时选是，跳过探测由浏览器直接加载）",
                    name="direct",
                    options=["否（自动识别，默认）", "是"],
                    value="否（自动识别，默认）",
                ),
                _p_radio(
                    label="是否启用",
                    name="enabled",
                    options=["是", "否"],
                    value="是",
                ),
                actions(
                    name="cmd",
                    buttons=[
                        {
                            "label": "添加",
                            "value": "ok",
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
        if not resp:
            return
        name = (resp["name"] or "").strip()
        url = (resp["url"] or "").strip()
        if not name or not url:
            toast("名称和 API 地址不能为空", color="error")
            return
        params = {}
        for pair in (resp.get("params") or "").split(","):
            pair = pair.strip()
            if "=" in pair:
                key, _, value = pair.partition("=")
                params[key.strip()] = value.strip()
        sources = self._load_sources()
        sources.append(
            {
                "type": "custom",
                "name": name,
                "url": url,
                "params": params,
                "image_path": (
                    (resp.get("image_path") or _DEFAULT_IMAGE_PATH).strip()
                    or _DEFAULT_IMAGE_PATH
                ),
                "direct": resp.get("direct") == "是",
                "enabled": resp.get("enabled") == "是",
            }
        )
        self._save_sources(sources)
        toast(f"已添加图源: {name}", color="success")
        logger.info(f"[WebUI] 已添加自定义图源: {name} -> {url}")
        self._refresh_random_wallpaper()

    def _toggle_source_dialog(self) -> None:
        """弹窗列出全部图源并选择切换启用/禁用状态。"""
        sources = self._load_sources()
        if not sources:
            toast("暂无图源", color="warning")
            return
        resp = input_group(
            "启用/禁用图源",
            [
                _p_radio(
                    label="选择图源（括号内为当前状态）",
                    name="index",
                    options=[
                        f"{i + 1}. {s.get('name', '未命名')} - "
                        f"{'已启用' if s.get('enabled', True) else '已禁用'} "
                        f"({s.get('url', '')})"
                        for i, s in enumerate(sources)
                    ],
                    required=True,
                ),
                actions(
                    name="cmd",
                    buttons=[
                        {
                            "label": "切换",
                            "value": "ok",
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
        if not resp:
            return
        index = resp["index"].split(".")[0].strip()
        if not index.isdigit():
            return
        index = int(index) - 1
        if 0 <= index < len(sources):
            current = sources[index].get("enabled", True)
            sources[index]["enabled"] = not current
            self._save_sources(sources)
            toast(
                f"图源 [{sources[index].get('name', '未命名')}] "
                f"{'已禁用' if current else '已启用'}",
                color="success",
            )
            logger.info(
                f"[WebUI] 图源 [{sources[index].get('name')}] -> "
                f"{'禁用' if current else '启用'}"
            )
            self._refresh_random_wallpaper()

    def _remove_source_dialog(self) -> None:
        """弹窗列出可删除的自定义图源并选择删除（内置默认源不可删除）。"""
        sources = self._load_sources()
        removable = [
            s for s in sources if s.get("type") not in _BUILTIN_TYPES
        ]
        if not removable:
            toast("没有可删除的自定义图源（内置源不可删除，可禁用）", color="warning")
            return
        resp = input_group(
            "删除图源",
            [
                _p_radio(
                    label="选择要删除的自定义图源",
                    name="index",
                    options=[
                        f"{i + 1}. {s.get('name', '未命名')} ({s.get('url', '')})"
                        for i, s in enumerate(removable)
                    ],
                    required=True,
                ),
                actions(
                    name="cmd",
                    buttons=[
                        {
                            "label": "删除",
                            "value": "ok",
                            "type": "submit",
                            "color": "danger",
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
        if not resp:
            return
        index = resp["index"].split(".")[0].strip()
        if not index.isdigit():
            return
        index = int(index) - 1
        if 0 <= index < len(removable):
            removed = removable.pop(index)
            # 按元素身份从完整列表移除
            new_sources = [
                s
                for s in sources
                if s is not removed
            ]
            self._save_sources(new_sources)
            toast(f"已删除图源: {removed.get('name', '未命名')}", color="success")
            logger.info(f"[WebUI] 已删除自定义图源: {removed.get('name')}")
            self._refresh_random_wallpaper()

    def _reset_default_sources(self) -> None:
        """恢复默认图源设置：内置默认源重置并重新启用，自定义源不受影响。"""
        sources = self._load_sources()
        defaults = self._default_sources()
        for default_entry in defaults:
            found = False
            for entry in sources:
                if entry.get("key") == default_entry["key"]:
                    entry.update(
                        {
                            "type": default_entry["type"],
                            "name": default_entry["name"],
                            "url": default_entry["url"],
                            "image_path": default_entry["image_path"],
                            "enabled": True,
                        }
                    )
                    found = True
                    break
            if not found:
                sources.append(default_entry)
        self._save_sources(sources)
        toast("已恢复默认图源设置", color="success")
        logger.info("[WebUI] 已恢复默认图源设置")
        self._refresh_random_wallpaper()

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
        """保存当前背景模式："random" 随机图源 / "custom" 自定义图片或视频。

        同文件中还持久化媒体类型偏好，写入时保留该字段避免被覆盖。
        """
        self._write_background_setting({"mode": mode})

    def _load_media_preference(self) -> str:
        """读取随机背景的媒体类型偏好。

        "auto" 混合（默认）/ "image" 仅图片 / "video" 仅视频。
        """
        try:
            data = json.loads(
                self._background_mode_file().read_text(encoding="utf-8")
            )
            return data.get("media", "auto")
        except Exception:
            return "auto"

    def _save_media_preference(self, media: str) -> None:
        """保存随机背景的媒体类型偏好，不影响同文件中的背景模式。"""
        self._write_background_setting({"media": media})

    def _write_background_setting(self, update: dict) -> None:
        """向背景设置文件合并写入字段，保留其余字段。"""
        try:
            data = {}
            setting_file = self._background_mode_file()
            if setting_file.exists():
                data = json.loads(setting_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
            data.update(update)
            setting_file.write_text(
                json.dumps(data), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[WebUI] 保存背景设置失败: {e}")

    def _custom_background_url(self) -> tuple:
        """返回 (自定义背景地址, 是否视频)。

        图片沿用 data URI 方案：随页面注入、不发起任何二次请求，本地/远控
        行为完全一致（图片已在保存时服务端压缩，内联体积可控）。视频体积
        大且需要流式缓冲与拖动，不适合内联，改走 HTTP 接口
        /api/custom_background_video（FileResponse 支持 Range 请求）。
        无自定义背景文件时返回 ("", False)。
        """
        try:
            files = sorted(self._wallpapers_dir().glob("custom_background.*"))
            if not files:
                return "", False
            suffix = files[0].suffix.lower()
            if suffix in _CUSTOM_VIDEO_EXTS:
                return "/api/custom_background_video", True
            content = files[0].read_bytes()
            mime = _CUSTOM_BG_MIMES.get(suffix, "image/jpeg")
            encoded = base64.b64encode(content).decode("ascii")
            return f"data:{mime};base64,{encoded}", False
        except Exception as e:
            logger.warning(f"[WebUI] 读取自定义背景失败: {e}")
            return "", False

    def _inject_custom_background(self, image_url: str, is_video: bool = False) -> None:
        """注入自定义背景，对所有主题生效。

        图片：以带 !important 的 body 规则覆盖所有主题背景，仅注入短小的
        CSS 规则；视频：注入置底 <video> 元素（静音循环自动播放）并清空
        body 背景，避免主题背景遮住 z-index 为负的视频层。
        """
        if is_video:
            # 注意：模板含 CSS 百分号（width:100%），不能用 % 格式化，
            # 用占位符替换注入 URL，避免 %; 被误认为格式符
            run_js(
                """
                (function () {
                    var css = 'body{background-image:none !important;}'
                        + '#alas-bg-video{position:fixed;inset:0;width:100%;'
                        + 'height:100%;object-fit:cover;z-index:-1;'
                        + 'pointer-events:none;}';
                    var style = document.getElementById('alas-custom-bg-style');
                    if (style) { style.textContent = css; }
                    else {
                        style = document.createElement('style');
                        style.id = 'alas-custom-bg-style';
                        style.textContent = css;
                        document.head.appendChild(style);
                    }
                    var old = document.getElementById('alas-bg-video');
                    if (old) { old.parentNode.removeChild(old); }
                    var video = document.createElement('video');
                    video.id = 'alas-bg-video';
                    video.src = __BG_URL__;
                    video.muted = true;
                    video.loop = true;
                    video.autoplay = true;
                    video.playsInline = true;
                    video.setAttribute('playsinline', '');
                    document.body.appendChild(video);
                    var p = video.play();
                    if (p && p.catch) { p.catch(function () {}); }
                })();
                """.replace("__BG_URL__", json.dumps(image_url))
            )
            return
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
            % image_url
        )

    def _clear_custom_background(self) -> None:
        """移除自定义背景注入样式与视频元素，恢复主题默认背景。"""
        run_js(
            "(function(){var el=document.getElementById('alas-custom-bg-style');"
            "if(el){el.parentNode.removeChild(el);}"
            "var v=document.getElementById('alas-bg-video');"
            "if(v){v.parentNode.removeChild(v);}})();"
        )

    def _upload_custom_background(self) -> None:
        """上传自定义背景图片或视频并立即应用，同时对所有主题生效。"""
        resp = input_group(
            label="上传自定义背景",
            inputs=[
                file_upload(
                    label="选择图片或视频（PNG/JPG/WebP/MP4/WebM 等）",
                    name="file",
                    placeholder="选择图片或视频",
                    accept="image/*,video/mp4,video/webm,video/quicktime",
                    required=True,
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
        if ext not in _CUSTOM_BG_MIMES and ext not in _CUSTOM_VIDEO_EXTS:
            ext = ".png"

        # 视频不重编码（浏览器原生解码，服务端重编码得不偿失）；图片压缩体积
        is_video = ext in _CUSTOM_VIDEO_EXTS
        if not is_video:
            content, ext = self._compress_custom_background_bytes(content, ext)

        wallpapers_dir = self._wallpapers_dir()
        wallpapers_dir.mkdir(parents=True, exist_ok=True)
        # 只保留一份自定义背景，避免多文件读取歧义
        for old in wallpapers_dir.glob("custom_background.*"):
            old.unlink(missing_ok=True)
        target = wallpapers_dir / f"custom_background{ext}"
        target.write_bytes(content)

        self._save_background_mode("custom")
        url, is_video = self._custom_background_url()
        self._inject_custom_background(url, is_video)
        toast(f"自定义背景已应用: {target.resolve()}", color="success")
        logger.info(f"[WebUI] 自定义背景已保存: {target.resolve()}")

    @staticmethod
    def _compress_custom_background_bytes(content: bytes, ext: str):
        """压缩自定义背景图片，返回 (压缩后字节, 保存扩展名)。

        - GIF 动图不做重编码，原样保留动画；
        - 其余格式解码后若最长边超过上限则等比缩放，再编码为 JPEG 控制体积；
        - 带 alpha 通道的图合成到白底，避免变黑；
        - 解码或编码失败时保留原始字节，避免上传中断。
        """
        if ext == ".gif":
            return content, ".gif"
        try:
            import cv2
            import numpy as np

            image = cv2.imdecode(
                np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_UNCHANGED
            )
            if image is None:
                return content, (ext or ".png")
            if image.ndim == 3 and image.shape[2] == 4:
                alpha = image[:, :, 3:4].astype(np.float32) / 255.0
                rgb = image[:, :, :3].astype(np.float32)
                image = (rgb * alpha + 255 * (1 - alpha)).astype(np.uint8)

            height, width = image.shape[:2]
            scale = max(width, height) / _CUSTOM_BG_MAX_EDGE
            if scale > 1:
                image = cv2.resize(
                    image,
                    (round(width / scale), round(height / scale)),
                    interpolation=cv2.INTER_AREA,
                )
            ok, encoded = cv2.imencode(
                ".jpg",
                image,
                [int(cv2.IMWRITE_JPEG_QUALITY), _CUSTOM_BG_JPEG_QUALITY],
            )
            if ok and len(encoded) < len(content):
                return encoded.tobytes(), ".jpg"
            return content, (ext or ".png")
        except Exception as e:
            logger.warning(f"[WebUI] 压缩自定义背景失败，保留原图: {e}")
            return content, (ext or ".png")

    def _switch_to_random_background(self) -> None:
        """切换回随机背景：清理自定义背景注入并重新拉取随机壁纸。"""
        self._save_background_mode("random")
        self._clear_custom_background()
        # 重置后重新触发随机图源加载
        self.wallpaper_url = ""
        self._direct_wallpaper_source = None
        # 同步清空前端竞赛胜者记录，避免下载功能读到上一次的旧图
        run_js("window.__alasWallpaperWinner = null;")
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

        # 前端竞赛已决出胜者时，以浏览器实际加载成功的图为准，
        # 保证下载的就是用户当前看到的这张背景
        try:
            winner = eval_js("window.__alasWallpaperWinner || null")
        except Exception as e:
            logger.info(f"[WebUI] 读取前端背景图竞赛结果失败: {e}")
            winner = None
        if isinstance(winner, dict) and winner.get("url"):
            self.wallpaper_url = winner["url"]
            self._direct_wallpaper_source = (
                {"name": winner.get("name", "未命名")}
                if winner.get("direct")
                else None
            )

        # 直链图源：服务端请求会被站点防爬拦截（超时），且每次请求返回
        # 新的随机图，服务端下载既会失败、得到的图也与当前显示的不同。
        # 改为弹窗提供链接，由浏览器直接打开图片后用户自行另存。
        if getattr(self, "_direct_wallpaper_source", None):
            source = self._direct_wallpaper_source
            popup(
                "保存直链图源图片",
                [
                    put_text(
                        f"图源 [{source.get('name', '未命名')}] 为直链随机图源："
                        "每次请求都会返回不同的图片，且该站点拦截服务端请求，"
                        "无法由服务端代为下载当前显示的这张。"
                    ),
                    put_text("请点击下方链接在新标签页打开图片，然后在图片上右键另存："),
                    put_link("打开图片", url=self.wallpaper_url),
                ],
                size="middle",
            )
            return

        # 视频体积大，下载超时放宽；扩展名回退用 .mp4
        is_video = bool(_VIDEO_URL_RE.search(self.wallpaper_url))
        try:
            response = requests.get(
                self.wallpaper_url,
                timeout=60 if is_video else 10,
            )
            response.raise_for_status()

            # 按 URL 后缀推断格式，避免一律存成 .jpg 与实际格式不符
            ext = Path(urlparse(self.wallpaper_url).path).suffix or (
                ".mp4" if is_video else ".jpg"
            )
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
