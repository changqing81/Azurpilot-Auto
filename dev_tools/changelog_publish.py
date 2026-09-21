"""更新日志（changelog.json）的生成与发布。

与公告发布（`dev_tools/announcement_publish.py`）同一套思路：更新日志放在仓库根
`changelog.json`，经 jsdelivr / GitHub raw 的 master 分支分发，客户端（更新器页面）
在**执行更新之前**就能读到这一版改了什么 —— 这正是"每次更新前总结更新内容"的落点。

流程：

    # 1) 把总结写进本地 changelog.json（自动填 id / 日期 / 当前 HEAD 短 sha）
    python dev_tools/changelog_publish.py add --draft .workbuddy/announcement/changelog-draft.md

    # 2) 生成预览（复刻更新器页面的渲染与样式，浅色/暗色双版），交给用户审阅
    python dev_tools/changelog_publish.py preview

    # 3) 用户确认后发布到远端 master（上传配图 + 写入 changelog.json + 刷新 jsdelivr）
    python dev_tools/changelog_publish.py publish [--dry-run]

草稿格式与公告一致（Markdown 子集）：第一行 `# 标题`，其余为正文；图片单独占一行，
支持 `![说明](file:D:/a.png)`、`![说明](https://.../a.png)` 或裸本地路径 / 裸图片直链。
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import subprocess
import sys
import time
from pathlib import Path

# 允许以脚本方式直接运行（python dev_tools/changelog_publish.py ...）：
# 这种情况下 sys.path[0] 是 dev_tools/ 而不是仓库根，补上仓库根才能 import dev_tools.* / module.*
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dev_tools.announcement_publish import (
    CDN_ROOT,
    DATA_BRANCH,
    DATA_REPO,
    REPO_ROOT,
    WORK_DIR,
    find_git,
    get_credential,
    is_local_image,
    purge_jsdelivr,
    put_file,
    read_draft,
    request_json,
    rewrite_body,
)
from module.webui.update_log import MAX_ENTRIES, render_entries_html, render_placeholder_html

CHANGELOG_PATH = "changelog.json"
ASSET_DIR = "changelog"  # 配图目录：changelog/<entry-id>/<文件名>
MAX_LOCAL_ENTRIES = 20  # 本地文件保留的历史条数（页面只展示最新几条）
CHANGELOG_FILE = WORK_DIR / CHANGELOG_PATH  # 本地工作副本（gitignore，不进版本库）
DEFAULT_DRAFT = WORK_DIR / "changelog-draft.md"
DEFAULT_PREVIEW = WORK_DIR / "changelog-preview.html"


# --------------------------------------------------------------------------
# 本地文件读写
# --------------------------------------------------------------------------
def load_changelog(path: Path = CHANGELOG_FILE) -> dict:
    if not path.exists():
        return {"updatedAt": "", "entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{path} 不是合法 JSON：{e}") from None
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise RuntimeError(f"{path} 结构不符合预期（应为含 entries 列表的对象）")
    return data


def save_changelog(data: dict, path: Path = CHANGELOG_FILE) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
        newline="\n",  # 固定 LF：Windows 下默认会写成 CRLF，混入仓库会产生整文件 diff
    )


def next_entry_id(entries: list, today: str) -> str:
    """条目 id 形如 YYYYMMDD-N，同一天再发一条则序号递增。"""
    used = 0
    for item in entries:
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get("id") or "")
        if entry_id.startswith(f"{today}-"):
            try:
                used = max(used, int(entry_id.rsplit("-", 1)[1]))
            except ValueError:
                continue
    return f"{today}-{used + 1}"


def head_sha() -> str:
    try:
        result = subprocess.run(
            [find_git(), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001 - 拿不到就留空，不影响发布
        return ""


# --------------------------------------------------------------------------
# 合并（远端已有的条目不能丢）
# --------------------------------------------------------------------------
def merge_entries(local_entries: list, remote_entries: list) -> list:
    """本地条目优先，远端独有的按 id 追加在后。"""
    result: list = []
    seen: set[str] = set()
    for item in list(local_entries) + list(remote_entries):
        if not isinstance(item, dict):
            continue
        entry_id = str(item.get("id") or "")
        key = entry_id or json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:MAX_LOCAL_ENTRIES]


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_add(args: argparse.Namespace) -> int:
    draft = Path(args.draft).resolve()
    title, body = read_draft(draft)

    data = load_changelog()
    today = time.strftime("%Y%m%d")
    entry_id = args.id or next_entry_id(data["entries"], today)
    entry = {
        "id": entry_id,
        "date": f"{today[:4]}-{today[4:6]}-{today[6:]}",
        "title": title,
        "content": "\n".join(body),
        "sha": args.sha or head_sha(),
    }

    data["entries"] = merge_entries([entry], data["entries"])
    data["updatedAt"] = entry["date"]
    save_changelog(data)

    print(f"已写入本地 {CHANGELOG_PATH}")
    print(f"  条目 id : {entry_id}")
    print(f"  标题    : {title}")
    print(f"  提交    : {entry['sha'] or '(未取到)'}")
    print(f"  本地图片: {sum(1 for line in body if is_local_image(line))} 张")
    print(f"  现有条目: {len(data['entries'])} 条（页面展示最新 {MAX_ENTRIES} 条）")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    data = load_changelog()
    entries = [item for item in data.get("entries", []) if isinstance(item, dict)][:MAX_ENTRIES]
    if not entries:
        print("changelog.json 里没有条目，先跑 add", file=sys.stderr)
        return 2

    # 预览时把本地图片转成 data URI 内嵌，离线也能看（与公告预览一致）
    preview_entries = []
    for entry in entries:
        body = rewrite_body(str(entry.get("content") or "").split("\n"), None, embed_local=True)
        preview_entries.append({**entry, "content": "\n".join(body)})

    out = Path(args.out).resolve() if args.out else DEFAULT_PREVIEW
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_preview_html(preview_entries, data, build_sample_html()), encoding="utf-8"
    )
    print(f"预览已生成：{out}")
    print(f"条目数：{len(preview_entries)}（页面最多展示 {MAX_ENTRIES} 条）")
    return 0


def build_sample_html() -> str:
    """预览里附一段「图片渲染示例」，证明正文里的整行图片链接能正常出图。"""
    sample_image = REPO_ROOT / ".workbuddy" / "announcement" / "demo-banner.png"
    if not sample_image.is_file():
        return ""
    uri = "data:image/png;base64," + base64.b64encode(sample_image.read_bytes()).decode("ascii")
    sample_entry = {
        "id": "sample",
        "date": "",
        "title": "图片渲染示例",
        "content": f"![示例配图]({uri})",
    }
    return (
        '<p class="panel-note" style="margin-top:24px">'
        "图片渲染示例（正文里单独占一行的图片链接会渲染成图片，点击标题可展开）：</p>"
        + render_entries_html([sample_entry])
    )


def cmd_publish(args: argparse.Namespace) -> int:
    data = load_changelog()
    entries = [item for item in data.get("entries", []) if isinstance(item, dict)]
    if not entries:
        print("changelog.json 里没有条目，先跑 add", file=sys.stderr)
        return 2

    token = get_credential()

    # 远端已有条目不能丢：拉下来合并（本地优先）
    remote = None
    try:
        remote = request_json(
            "GET",
            f"https://api.github.com/repos/{DATA_REPO}/contents/{CHANGELOG_PATH}?ref={DATA_BRANCH}",
            token,
        )
    except RuntimeError:
        remote = None

    remote_entries: list = []
    remote_sha = None
    if isinstance(remote, dict) and remote.get("content"):
        remote_sha = remote.get("sha")
        try:
            remote_entries = json.loads(base64.b64decode(remote["content"]).decode("utf-8")).get("entries", [])
        except Exception:  # noqa: BLE001 - 远端坏数据时以本地为准
            remote_entries = []

    merged = merge_entries(entries, remote_entries)
    local_to_url: dict[str, str] = {}
    uploaded: list[str] = []

    local_images: list[Path] = []
    for entry in merged:
        for line in str(entry.get("content") or "").split("\n"):
            path = is_local_image(line)
            if path is not None and str(path.resolve()) not in [str(p.resolve()) for p in local_images]:
                local_images.append(path)

    print(f"条目数：{len(merged)}（新增 {len(entries)}，远端独有 {len(remote_entries)}）")
    print(f"待上传配图：{len(local_images)} 张")

    if args.dry_run:
        for entry in merged[:MAX_ENTRIES]:
            print(f"  - [{entry.get('id')}] {entry.get('title')}")
        print("\n[dry-run] 未做任何写入。")
        return 0

    for path in local_images:
        repo_path = f"{ASSET_DIR}/{merged[0].get('id') or 'misc'}/{path.name}"
        commit_sha = put_file(
            token,
            repo_path,
            path.read_bytes(),
            f"chore(changelog): 上传更新日志配图 - {path.name}",
            sha=None,
        )
        # 每张图必须引用**它自己那次上传**产生的 commit：逐张上传会各生成一个
        # commit，只有该 commit 里才有这个文件。原先只在第一张时记录 image_ref，
        # 第 2 张起沿用第 1 张的 commit —— 那个 commit 里没有后续文件，jsdelivr
        # 直接 404（2026-09-21 实测的坏链就是这么来的）。
        ref = commit_sha if args.image_ref == "sha" else DATA_BRANCH
        local_to_url[str(path.resolve())] = f"{CDN_ROOT}/{DATA_REPO}@{ref}/{repo_path}"
        uploaded.append(repo_path)
        print(f"已上传 {path.name} -> commit {commit_sha[:10]}")

    published = []
    for entry in merged:
        body = rewrite_body(str(entry.get("content") or "").split("\n"), local_to_url, embed_local=False)
        published.append({**entry, "content": "\n".join(body)})

    payload = {
        "updatedAt": time.strftime("%Y-%m-%d"),
        "entries": published,
    }
    content = (json.dumps(payload, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
    commit_sha = put_file(
        token,
        CHANGELOG_PATH,
        content,
        f"chore(changelog): 更新更新日志（{published[0].get('id')}）",
        sha=remote_sha,
    )
    print(f"已发布 {CHANGELOG_PATH} -> commit {commit_sha[:10]}")

    save_changelog({"updatedAt": payload["updatedAt"], "entries": published})
    print(f"已同步本地 {CHANGELOG_PATH}")

    failed = purge_jsdelivr([CHANGELOG_PATH, *uploaded])
    if failed:
        print("以下 jsdelivr 缓存刷新失败（不影响生效，最多等缓存过期）：")
        for url in failed:
            print(f"  - {url}")
    else:
        print("已请求刷新 jsdelivr 缓存。")

    print("\n发布完成。验证地址：")
    print(f"  {CDN_ROOT}/{DATA_REPO}@{DATA_BRANCH}/{CHANGELOG_PATH}")
    return 0


# --------------------------------------------------------------------------
# 预览页面（复刻更新器页面的渲染与 .update-log* 样式）
# --------------------------------------------------------------------------
PREVIEW_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>更新日志预览</title>
<style>
  :root {{
    --alas-entry-text: #2f3542;
    --alas-entry-muted: #7f8c9b;
    --alas-entry-surface: #ffffff;
    --alas-entry-border: #dde0e5;
    --alas-entry-panel-shadow: 0 1px 3px rgba(15, 23, 42, .06);
  }}
  .theme-dark {{
    --alas-entry-text: #e6e9ee;
    --alas-entry-muted: #9aa5b1;
    --alas-entry-surface: #2b3138;
    --alas-entry-border: #3b424a;
    --alas-entry-panel-shadow: 0 1px 3px rgba(0, 0, 0, .35);
  }}
  body {{ margin: 0; padding: 24px; background: #eef1f5; font-family: "Microsoft YaHei", "PingFang SC", sans-serif; }}
  .stage {{ display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }}
  .stage-item {{ flex: 1 1 420px; min-width: 320px; }}
  .stage-label {{ font-size: 13px; color: #666; margin-bottom: 8px; }}
  .panel {{ padding: 16px; border-radius: 12px; background: #fff; border: 1px solid #dde0e5; }}
  .theme-dark .panel {{ background: #23282e; border-color: #3b424a; }}
  .panel-title {{ margin: 0 0 4px; font-size: 1rem; font-weight: 600; color: var(--alas-entry-text); }}
  .panel-note {{ margin: 0 0 12px; font-size: .82rem; color: var(--alas-entry-muted); }}

  /* ↓↓↓ 与 assets/gui/css/entry-alas.css 的 .update-log* 规则保持同步 ↓↓↓ */
  .update-log {{ display: flex; flex-direction: column; gap: .5rem; margin-top: .5rem; }}
  .update-log-entry {{
    padding: .75rem 1rem; color: var(--alas-entry-text); background: var(--alas-entry-surface);
    border: 1px solid var(--alas-entry-border); border-radius: 16px; box-shadow: var(--alas-entry-panel-shadow);
  }}
  .update-log-summary {{ display: flex; gap: .5rem; align-items: baseline; cursor: pointer; list-style: none; }}
  .update-log-summary::-webkit-details-marker {{ display: none; }}
  .update-log-summary::before {{ content: "▸"; color: var(--alas-entry-muted); font-size: .85rem; }}
  .update-log-entry[open] > .update-log-summary::before {{ content: "▾"; }}
  .update-log-title {{ flex: 1 1 auto; color: var(--alas-entry-text); font-size: 1rem; font-weight: 600; }}
  .update-log-meta {{ flex: 0 0 auto; color: var(--alas-entry-muted); font-size: .8rem; font-weight: 400; }}
  .update-log-body {{ margin-top: .75rem; }}
  .update-log-text {{ color: var(--alas-entry-text); font-size: .92rem; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }}
  .update-log-image {{
    display: block; max-width: 100%; height: auto; margin: .5rem auto;
    border: 1px solid var(--alas-entry-border); border-radius: 12px;
  }}
  .update-log-placeholder {{
    margin-top: .5rem; padding: .75rem 1rem; color: var(--alas-entry-muted);
    background: var(--alas-entry-surface); border: 1px dashed var(--alas-entry-border);
    border-radius: 16px; font-size: .9rem;
  }}
  pre.json {{ margin-top: 28px; background: #1e272e; color: #d2dae2; padding: 16px; border-radius: 8px; font-size: 12.5px; line-height: 1.6; overflow-x: auto; white-space: pre-wrap; word-break: break-all; }}
</style>
</head>
<body>
  <h1 class="panel-title">更新器页面 · 更新日志区块预览</h1>
  <p class="panel-note">区块位于「详细提交历史」上方；点标题行可展开/收起。下图分别为亮色与暗色主题下的实际样式。</p>
  <div class="stage">
    <div class="stage-item">
      <div class="stage-label">亮色主题</div>
      <div class="panel">
        <div class="panel-title">{changelog_title}</div>
        {entries_html}
      </div>
    </div>
    <div class="stage-item">
      <div class="stage-label">暗色主题</div>
      <div class="panel theme-dark">
        <div class="panel-title">{changelog_title}</div>
        {entries_html}
      </div>
    </div>
  </div>
  <p class="panel-note" style="margin-top:24px">取不到数据时的占位（同款样式）：</p>
  {placeholder_html}
  {sample_html}
  <pre class="json">{payload_json}</pre>
</body>
</html>
"""


def render_preview_html(entries: list[dict], data: dict, sample_html: str = "") -> str:
    return PREVIEW_TEMPLATE.format(
        changelog_title=html.escape("更新日志"),
        entries_html=render_entries_html(entries),
        placeholder_html=render_placeholder_html("暂无更新日志"),
        sample_html=sample_html,
        payload_json=html.escape(
            json.dumps(
                {"updatedAt": data.get("updatedAt", ""), "entries": entries},
                ensure_ascii=False,
                indent=4,
            )
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AzurPilot 更新日志（changelog.json）工具")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="把一条更新总结写进本地 changelog.json")
    add.add_argument("--draft", default=str(DEFAULT_DRAFT), help="草稿 md 路径")
    add.add_argument("--id", default="", help="指定条目 id（默认按当天序号）")
    add.add_argument("--sha", default="", help="条目对应的提交（默认取当前 HEAD 短 sha）")
    add.set_defaults(func=cmd_add)

    preview = sub.add_parser("preview", help="生成更新日志预览 HTML")
    preview.add_argument("--out", default="", help="输出路径")
    preview.set_defaults(func=cmd_preview)

    publish = sub.add_parser("publish", help="发布到远端 master 并刷新 jsdelivr")
    publish.add_argument("--image-ref", choices=["sha", "master"], default="sha")
    publish.add_argument("--dry-run", action="store_true", help="只打印计划，不写入")
    publish.set_defaults(func=cmd_publish)
    return parser


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
