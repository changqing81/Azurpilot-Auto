"""公告配图本地化：把公告正文里的远端图片抓到本地，改写成站内地址。

为什么要做：公告与配图放在独立数据仓库 `changqing81/announcement-changelog`，
经 jsdelivr 分发。PC 端往往能访问 jsdelivr，所以不易察觉；但**手机（尤其远控时）
直连 jsdelivr 基本不通**，公告配图就只剩破图。

做法：服务端（本机）在拉公告的后台线程里把图片抓到 `cache/announcement/`，
把正文里的远端 URL 换成 `/api/announcement/image/<name>`；客户端只访问本机 WebUI，
远控（P2P）下也走已经验证可通的同一条通道。

安全约束：
- 只处理白名单前缀的 URL（否则等于开了个任意 URL 代理，SSRF）；
- 文件名由 URL 的 sha256 生成，读取时再用正则校验（防路径穿越）；
- 单张大小上限，避免一次拉爆内存与远控带宽。
"""

from __future__ import annotations

import hashlib
import re
import warnings
from pathlib import Path
from typing import Optional

import requests

from module.logger import logger

# 只允许公告数据仓库的地址（cdn 主源 + raw 兜底），且要兼容三种引用形式：
#   cdn.jsdelivr.net/gh/<owner>/<repo>@<commit-sha>/...   ← 发布工具上传配图后用这个
#   cdn.jsdelivr.net/gh/<owner>/<repo>@main/...           ← 分支引用
#   raw.githubusercontent.com/<owner>/<repo>/<ref>/...
# 用正则而不是字符串前缀：仓库名后面既可能是 `/` 也可能是 `@`（漏了 @ 会让
# 白名单形同失效——2026-09-22 就是被单测抓出来的）。
_ALLOWED_URL_RE = re.compile(
    r"^https://(?:cdn\.jsdelivr\.net/gh|raw\.githubusercontent\.com)/"
    r"changqing81/announcement-changelog(?:[/@].*)?$",
    re.IGNORECASE,
)

# 客户端拿到的站内地址前缀，与 api.py 里注册的路由一致
PUBLIC_PATH_PREFIX = "/api/announcement/image/"

CACHE_DIR = Path(__file__).resolve().parents[2] / "cache" / "announcement"

REQUEST_TIMEOUT = 15
MAX_BYTES = 8 * 1024 * 1024

_IMAGE_SUFFIXES = ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif")
_IMAGE_URL_RE = re.compile(
    r"https?://[^\s\"'<>()\[\]]+?\.(?:" + "|".join(_IMAGE_SUFFIXES) + r")"
    r"(?:\?[^\s\"'<>()\[\]]*)?",
    re.IGNORECASE,
)
_NAME_RE = re.compile(
    r"^[0-9a-f]{20}\.(?:" + "|".join(_IMAGE_SUFFIXES) + r")$", re.IGNORECASE
)


def is_allowed_url(url: str) -> bool:
    """只接受公告数据仓库的图片地址（含 @commit 与 @branch 两种引用形式）。"""
    return bool(_ALLOWED_URL_RE.match(str(url or "")))


def _suffix_of(url: str) -> str:
    path = str(url).split("?", 1)[0]
    suffix = path.rsplit(".", 1)[-1].lower()
    return "jpg" if suffix == "jpeg" else suffix


def cache_name(url: str) -> str:
    """由 URL 生成缓存文件名（内容寻址，同一个图天然复用）。"""
    digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:20]
    return f"{digest}.{_suffix_of(url)}"


def is_valid_name(name: str) -> bool:
    """校验缓存文件名，拦住路径穿越等非法取值。"""
    return bool(_NAME_RE.match(str(name or "")))


def cached_file(name: str) -> Optional[Path]:
    """返回已缓存的图片路径；不存在或名字非法时返回 None。"""
    if not is_valid_name(name):
        return None
    path = CACHE_DIR / name
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path
    except OSError:
        return None
    return None


def ensure_cached(url: str) -> Optional[str]:
    """确保图片已落到本地，返回站内地址；失败返回 None（调用方保留原地址）。"""
    if not is_allowed_url(url):
        logger.debug(f"[WebUI-公告] 非白名单图片地址，跳过本地化: {url}")
        return None

    name = cache_name(url)
    if cached_file(name) is not None:
        return PUBLIC_PATH_PREFIX + name

    try:
        # verify=False 的原因（2026-09-22 实测）：
        # cdn.jsdelivr.net 对 `@<commit-sha>` 形式的引用会 **301 跳到
        # raw.githubusercontent.com**（不缓存），而本机对该域名的证书链校验不过
        # （verify=True 报 CERTIFICATE_VERIFY_FAILED，verify=False 能正常拿到 PNG）。
        # 客户端（手机）走同一跳会被墙，所以必须由服务端代取。
        # 风险面已收窄：URL 必须先过 _ALLOWED_URL_RE 白名单，且只下载公告配图。
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            response = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "alas AzurPilot"},
                verify=False,
            )
        if response.status_code != 200:
            logger.warning(
                f"[WebUI-公告] 配图下载失败 HTTP {response.status_code}: {url}"
            )
            return None
        payload = response.content
    except Exception as e:
        logger.warning(f"[WebUI-公告] 配图下载异常: {e}")
        return None

    if not payload:
        logger.warning(f"[WebUI-公告] 配图为空: {url}")
        return None
    if len(payload) > MAX_BYTES:
        logger.warning(
            f"[WebUI-公告] 配图超过 {MAX_BYTES} 字节，跳过本地化: {url}"
        )
        return None

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / name).write_bytes(payload)
    except OSError as e:
        logger.warning(f"[WebUI-公告] 配图写缓存失败: {e}")
        return None

    logger.info(f"[WebUI-公告] 配图已缓存 {name} ({len(payload)} 字节)")
    return PUBLIC_PATH_PREFIX + name


def localize_images(content: str) -> str:
    """把正文里的远端配图地址换成站内地址；失败的原样保留（前端会走 onerror 降级）。"""
    if not content:
        return content

    def _replace(match: re.Match) -> str:
        original = match.group(0)
        local = ensure_cached(original)
        return local or original

    return _IMAGE_URL_RE.sub(_replace, content)
