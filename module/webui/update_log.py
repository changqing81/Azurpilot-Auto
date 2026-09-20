"""更新日志（changelog）的解析与渲染。

数据源是仓库根目录的 `changelog.json`，与公告（`announcement.json`）走同一套分发
机制（jsdelivr 主源 + GitHub raw 备用，master 分支）：客户端在**执行更新之前**就能
读到这一版要改什么，这正是"每次更新前总结更新内容"的前提。

JSON 结构：

    {
      "entries": [
        {
          "id": "20260920-1",
          "date": "2026-09-20",
          "title": "公告支持配图，新增公告发布工具",
          "content": "正文，图片单独占一行：![](https://.../a.png) 或裸图片直链",
          "sha": "bbe42f1d3"
        }
      ]
    }

正文解析规则与公告弹窗保持一致（整行 `![说明](图片URL)` 或整行裸图片直链渲染成图，
其余按纯文本保留换行），区别是本模块在服务端生成 HTML，因此：
    * 所有文本一律 `html.escape` 后再拼接；
    * 图片地址只放行 `http(s)` 与 `data:image/`，其它（`javascript:` 等）直接丢弃。
"""

import html
import re
from typing import Any

# 更新器页面最多展示几条（最新的在最上面，默认展开）
MAX_ENTRIES = 5

TEXT_CLASS = "update-log-text"
IMAGE_CLASS = "update-log-image"

_MD_IMAGE = re.compile(r"^!\[([^\]]*)\]\(\s*(\S+?)\s*\)$")
_RAW_IMAGE = re.compile(
    r"^<?(https?://[^\s<>\"'`]+\.(?:png|jpe?g|gif|webp|bmp|svg|avif)(?:\?[^\s<>\"'`]*)?)>?$",
    re.IGNORECASE,
)
_UNSAFE_PROTOCOL = re.compile(r"^\s*(javascript|vbscript):", re.IGNORECASE)


def is_safe_image_url(url: str) -> bool:
    """只放行 http(s) 与 data:image/ 的图片地址。"""
    text = str(url or "").strip()
    if not text:
        return False
    if text.lower().startswith("data:image/"):
        return True
    if _UNSAFE_PROTOCOL.match(text):
        return False
    return text.lower().startswith(("http://", "https://"))


def parse_image_line(line: str) -> tuple[str, str] | None:
    """整行是图片时返回 (url, alt)，否则返回 None。"""
    text = str(line or "").strip()
    matched = _MD_IMAGE.match(text)
    if matched:
        url, alt = matched.group(2), matched.group(1)
    else:
        raw = _RAW_IMAGE.match(text)
        if not raw:
            return None
        url, alt = raw.group(1), ""
    if not is_safe_image_url(url):
        return None
    return url, alt


def render_content_html(content: Any) -> str:
    """把正文渲染成 HTML：连续文本行合并成一个保留换行的块，图片行生成 <img>。"""
    lines = str(content if content is not None else "").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    chunks: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer or not "".join(buffer).strip():
            buffer.clear()
            return
        text = html.escape("\n".join(buffer))
        chunks.append(f'<div class="{TEXT_CLASS}">{text}</div>')
        buffer.clear()

    for line in lines:
        image = parse_image_line(line)
        if image:
            flush()
            url, alt = image
            chunks.append(
                f'<img class="{IMAGE_CLASS}" src="{html.escape(url, quote=True)}"'
                f' alt="{html.escape(alt, quote=True)}" loading="lazy">'
            )
        else:
            buffer.append(line)
    flush()
    return "".join(chunks)


def normalize_entries(data: Any, limit: int = MAX_ENTRIES) -> list[dict[str, str]]:
    """从远端 JSON 里取出可用条目，坏数据直接跳过而不是抛错。"""
    if isinstance(data, dict):
        raw_entries = data.get("entries")
    elif isinstance(data, list):
        raw_entries = data
    else:
        raw_entries = None
    if not isinstance(raw_entries, list):
        return []

    result: list[dict[str, str]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = item.get("content")
        content = "" if content is None else str(content)
        if not title and not content.strip():
            continue
        result.append(
            {
                "id": str(item.get("id") or "").strip(),
                "date": str(item.get("date") or "").strip(),
                "title": title,
                "content": content,
                "sha": str(item.get("sha") or "").strip(),
            }
        )
        if len(result) >= limit:
            break
    return result


def render_entries_html(entries: list[dict[str, str]]) -> str:
    """渲染成可折叠卡片列表：第一条默认展开，其余收起（原生 details，无需 JS）。"""
    blocks: list[str] = []
    for index, entry in enumerate(entries):
        title = html.escape(entry.get("title") or "")
        date = html.escape(entry.get("date") or "")
        sha = html.escape((entry.get("sha") or "")[:10])
        meta = " · ".join(part for part in (date, sha) if part)
        open_attr = " open" if index == 0 else ""
        blocks.append(
            f'<details class="update-log-entry"{open_attr}>'
            f'<summary class="update-log-summary">'
            f'<span class="update-log-title">{title or "&nbsp;"}</span>'
            f'<span class="update-log-meta">{meta}</span>'
            f"</summary>"
            f'<div class="update-log-body">{render_content_html(entry.get("content"))}</div>'
            f"</details>"
        )
    return f'<div class="update-log">{"".join(blocks)}</div>'


def render_placeholder_html(message: str) -> str:
    return f'<div class="update-log-placeholder">{html.escape(str(message))}</div>'
