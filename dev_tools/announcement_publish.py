"""AzurPilot 公告发布工具（GitHub API 直写，不经过本地 git）

公告客户端（module/base/api_client.py）从以下地址拉取公告：
    主源  https://cdn.jsdelivr.net/gh/changqing81/Azurpilot-Auto@master/announcement.json
    备用  https://raw.githubusercontent.com/changqing81/Azurpilot-Auto/master/announcement.json

因此公告必须落在**远端 master 分支的 announcement.json** 上。本工具通过 GitHub
REST API 直接改写该文件（以及上传公告图片），好处：
    * 不产生本地提交、不碰工作树，不受启动器 `git reset --hard` 影响
    * 不需要 `git add/commit/push`，一条 HTTP 请求即完成发布

凭据：默认从本机 Git Credential Manager 里取已存的 GitHub 凭据（无需新建 token），
可用环境变量 ALAS_ANNOUNCE_GCM / ALAS_GIT 覆盖。凭据只存在于进程内存，不落盘。

草稿格式（Markdown 子集，文件名随意，默认 .workbuddy/announcement/draft.md）：

    # 公告标题（第一行 `# ` 开头的行）

    正文第一段，可多行。
    图片单独占一行，支持三种写法：
    ![说明](file:D:/pics/a.png)      ← 本地文件，发布时自动上传
    ![说明](https://x/a.png)         ← 远端直链，直接使用
    D:/pics/b.png                    ← 裸本地路径 / 裸图片直链，同样识别

用法：

    # 1) 生成预览（本地 HTML，图片以 base64 内嵌，无需联网）
    python dev_tools/announcement_publish.py preview --draft .workbuddy/announcement/draft.md

    # 2) 用户确认后再发布（上传图片 → 写 announcement.json → 刷新 jsdelivr）
    python dev_tools/announcement_publish.py publish --draft .workbuddy/announcement/draft.md

    # 只演练不写入
    python dev_tools/announcement_publish.py publish --draft ... --dry-run
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "changqing81/Azurpilot-Auto"
BRANCH = "master"
ANNOUNCEMENT_PATH = "announcement.json"
ASSET_DIR = "announcement"  # 公告图片存放目录（仓库根下）
API_ROOT = "https://api.github.com"
CDN_ROOT = "https://cdn.jsdelivr.net/gh"
PURGE_ROOT = "https://purge.jsdelivr.net/gh"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DRAFT = REPO_ROOT / ".workbuddy" / "announcement" / "draft.md"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".svg"}
MD_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(\s*(\S+?)\s*\)$")
RAW_IMAGE_RE = re.compile(
    r"^<?(https?://[^\s<>\"'`]+\.(?:png|jpe?g|gif|webp|bmp|svg|avif)(?:\?[^\s<>\"'`]*)?)>?$",
    re.IGNORECASE,
)

MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
}


# --------------------------------------------------------------------------
# HTTP / 凭据
# --------------------------------------------------------------------------
def _opener() -> urllib.request.OpenerDirector:
    """默认绕开环境变量里的代理（本机代理会让 GitHub 请求 502）。"""
    if os.environ.get("ALAS_ANNOUNCE_USE_PROXY") == "1":
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _portable_git_candidates(filename: str) -> list[Path]:
    """PortableGit 常见安装位置（不同机器/版本路径不同，按模式探测而非写死）。"""
    bases = [
        Path.home() / ".workbuddy" / "binaries" / "PortableGit",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "PortableGit",
        Path("C:/Program Files/Git"),
        Path("C:/Program Files (x86)/Git"),
        Path("/usr"),
    ]
    found: list[Path] = []
    for base in bases:
        if not str(base) or not base.is_dir():
            continue
        found.extend(sorted(base.glob(f"*/mingw64/bin/{filename}")))
        found.extend(sorted(base.glob(f"*/bin/{filename}")))
        direct = base / "mingw64" / "bin" / filename
        if direct.is_file():
            found.append(direct)
    return found


def find_git() -> str:
    """定位 git 可执行文件：环境变量 ALAS_GIT → PATH → PortableGit 常见位置。"""
    override = os.environ.get("ALAS_GIT")
    if override:
        return override
    for name in ("git", "git.exe"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _portable_git_candidates("git.exe"):
        if candidate.is_file():
            return str(candidate)
    return "git"


def find_credential_helper() -> str:
    """定位 git-credential-manager：环境变量 ALAS_ANNOUNCE_GCM → PATH → PortableGit → 通用名。

    不写死本机用户名与版本号 —— 本文件会进公共仓库，硬编码路径既不可移植也泄漏环境信息。
    """
    override = os.environ.get("ALAS_ANNOUNCE_GCM")
    if override:
        return override
    for name in ("git-credential-manager", "git-credential-manager.exe"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in _portable_git_candidates("git-credential-manager.exe"):
        if candidate.is_file():
            return str(candidate)
    # GCM 装在 git 同目录时可直接用简名，由 git 自行解析
    return "manager"


def _request(method: str, url: str, token: str | None = None, payload: dict | None = None):
    data = None
    headers = {"User-Agent": "AzurPilot-announcement-publisher", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener().open(request, timeout=60) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} 失败：HTTP {error.code}\n{detail[:600]}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"{method} {url} 失败：{error.reason}") from None

    if not body.strip():
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def get_credential() -> str:
    """从本机 GCM 取出已存的 GitHub 凭据（仅内存使用，不落盘）。"""
    git = find_git()
    helper = find_credential_helper()
    command = [
        git,
        "-c", "credential.helper=",
        "-c", f"credential.helper={helper}",
        "-c", "credential.interactive=false",
        "credential", "fill",
    ]
    try:
        result = subprocess.run(
            command,
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
        )
    except FileNotFoundError:
        raise RuntimeError(f"找不到 git 可执行文件（{git}），可用环境变量 ALAS_GIT 指定完整路径") from None

    for line in result.stdout.splitlines():
        if line.startswith("password="):
            token = line[len("password="):].strip()
            if token:
                return token
    raise RuntimeError(
        "未能从凭据管理器取到 GitHub token。请确认本机已能正常 push，"
        "或设置 ALAS_ANNOUNCE_GCM 指向 git-credential-manager 的完整路径。\n"
        f"stderr: {result.stderr.strip()[:300]}"
    )


# --------------------------------------------------------------------------
# 远端读写
# --------------------------------------------------------------------------
def fetch_remote_announcement(token: str) -> tuple[dict | None, str | None]:
    """返回 (公告数据, blob sha)；文件不存在时返回 (None, None)。"""
    url = f"{API_ROOT}/repos/{REPO}/contents/{ANNOUNCEMENT_PATH}?ref={BRANCH}"
    try:
        data = _request("GET", url, token)
    except RuntimeError as error:
        if "HTTP 404" in str(error):
            return None, None
        raise
    if isinstance(data, dict) and data.get("content"):
        text = base64.b64decode(data["content"]).decode("utf-8")
        return json.loads(text), data.get("sha")
    return None, None


def put_file(token: str, path: str, content: bytes, message: str, sha: str | None) -> str:
    """写入/新增仓库文件，返回产生的 commit sha。"""
    payload = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        payload["sha"] = sha
    data = _request("PUT", f"{API_ROOT}/repos/{REPO}/contents/{path}", token, payload)
    commit_sha = (data.get("commit") or {}).get("sha")
    if not commit_sha:
        raise RuntimeError(f"写入 {path} 后没有拿到 commit sha：{str(data)[:300]}")
    return commit_sha


def purge_jsdelivr(paths: list[str]) -> list[str]:
    """尽力刷新 jsdelivr 分支引用缓存，返回失败的 URL 列表。"""
    failed = []
    for path in paths:
        url = f"{PURGE_ROOT}/{REPO}@{BRANCH}/{path}"
        try:
            _request("GET", url)
        except Exception:  # noqa: BLE001 - 刷新失败不影响发布结果，只提示
            failed.append(url)
    return failed


# --------------------------------------------------------------------------
# 草稿解析
# --------------------------------------------------------------------------
def read_draft(path: Path) -> tuple[str, list[str]]:
    if not path.exists():
        raise RuntimeError(f"草稿文件不存在：{path}")
    lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n")

    title = ""
    body_start = 0
    for index, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body_start = index + 1
            break
        if line.strip():
            break
    if not title:
        raise RuntimeError("草稿里没有找到标题：第一行请写成 `# 标题`")

    body = lines[body_start:]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    if not body:
        raise RuntimeError("草稿正文为空")
    return title, body


def is_local_image(line: str) -> Path | None:
    """整行是本地图片文件路径时返回该路径（含 `![alt](file:...)` 写法）。"""
    text = line.strip()
    matched = MD_IMAGE_RE.match(text)
    if matched:
        text = matched.group(2)
    elif RAW_IMAGE_RE.match(text):
        return None  # 远端直链
    if text.startswith("file:"):
        text = text[len("file:"):].strip()
    candidate = Path(text)
    if candidate.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    return candidate if candidate.is_file() else None


def rewrite_body(
    body: list[str],
    local_to_url: dict[str, str] | None,
    *,
    embed_local: bool,
) -> list[str]:
    """把正文里的本地图片路径替换掉。

    local_to_url 非空（发布）：替换成远端 URL；
    embed_local=True（预览）：替换成 base64 data URI，图片以 data:image/ 形式内嵌。
    """
    result = []
    for line in body:
        text = line.strip()
        alt = ""
        target = None
        matched = MD_IMAGE_RE.match(text)
        if matched:
            alt, target = matched.group(1), matched.group(2)
        else:
            target = text

        path = is_local_image(line)
        if path is None:
            result.append(line)
            continue

        key = str(path.resolve())
        if local_to_url is not None:
            url = local_to_url[key]
        elif embed_local:
            url = _to_data_uri(path)
        else:
            url = path.resolve().as_uri()
        result.append(f"![{alt}]({url})")
    return result


def _to_data_uri(path: Path) -> str:
    mime = MIME_BY_SUFFIX.get(path.suffix.lower())
    if not mime:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def next_announcement_id(current: str | None, today: str) -> str:
    """公告 ID 形如 YYYYMMDD-N，同一天再发一条则序号递增。"""
    if current:
        matched = re.match(r"^(\d{8})-(\d+)$", str(current))
        if matched and matched.group(1) == today:
            return f"{today}-{int(matched.group(2)) + 1}"
    return f"{today}-1"


# --------------------------------------------------------------------------
# 预览
# --------------------------------------------------------------------------
PREVIEW_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>公告预览 - {title}</title>
<style>
  body {{ margin: 0; padding: 24px; background: #eef1f5; font-family: "Microsoft YaHei", "PingFang SC", sans-serif; }}
  h1.page-title {{ font-size: 18px; color: #333; margin: 0 0 6px 0; }}
  p.page-note {{ font-size: 13px; color: #666; margin: 0 0 24px 0; }}
  .stage {{ display: flex; gap: 24px; flex-wrap: wrap; align-items: flex-start; }}
  .stage-item {{ flex: 1 1 420px; min-width: 320px; }}
  .stage-label {{ font-size: 13px; color: #666; margin-bottom: 8px; }}
  .overlay {{ display: flex; justify-content: center; align-items: center; padding: 20px; border-radius: 12px; }}
  .overlay.light {{ background: rgba(0, 0, 0, 0.5); }}
  .overlay.dark {{ background: rgba(0, 0, 0, 0.75); }}
  .modal {{ background: #fff; border-radius: 12px; padding: 24px; max-width: 500px; width: 100%; max-height: 80vh; overflow-y: auto; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3); }}
  .overlay.dark .modal {{ background: #2d3436; }}
  .modal h3 {{ margin: 0 0 12px 0; font-size: 1.25rem; color: #333; border-bottom: 2px solid #4fc3f7; padding-bottom: 8px; }}
  .overlay.dark .modal h3 {{ color: #dfe6e9; }}
  .alas-announcement-content {{ margin-bottom: 20px; }}
  .alas-announcement-text {{ font-size: 1rem; color: #555; line-height: 1.6; white-space: pre-wrap; }}
  .overlay.dark .alas-announcement-text {{ color: #b2bec3; }}
  .alas-announcement-image {{ display: block; max-width: 100%; height: auto; margin: 10px auto; border-radius: 8px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12); }}
  .btn-area {{ margin-top: 16px; text-align: center; }}
  .btn-area button {{ background: linear-gradient(90deg, #00b894, #0984e3); color: #fff; border: none; padding: 10px 32px; border-radius: 6px; font-size: 1rem; cursor: pointer; }}
  pre.json {{ margin-top: 28px; background: #1e272e; color: #d2dae2; padding: 16px; border-radius: 8px; font-size: 12.5px; line-height: 1.6; overflow-x: auto; white-space: pre-wrap; word-break: break-all; }}
</style>
</head>
<body>
  <h1 class="page-title">公告预览：{title}</h1>
  <p class="page-note">公告 ID：<code>{announcement_id}</code>　|　这是弹窗在 WebUI 里的实际样式（左：亮色主题，右：暗色主题）</p>
  <div class="stage">
    <div class="stage-item">
      <div class="stage-label">亮色主题</div>
      <div class="overlay light">
        <div class="modal">
          <h3>{title}</h3>
          <div class="alas-announcement-content">{content_html}</div>
          <div class="btn-area"><button>确认</button></div>
        </div>
      </div>
    </div>
  </div>
  <div class="stage">
    <div class="stage-item">
      <div class="stage-label">暗色主题</div>
      <div class="overlay dark">
        <div class="modal">
          <h3>{title}</h3>
          <div class="alas-announcement-content">{content_html}</div>
          <div class="btn-area"><button>确认</button></div>
        </div>
      </div>
    </div>
  </div>
  <pre class="json">{payload_json}</pre>
</body>
</html>
"""


def body_to_html(body: list[str]) -> str:
    """复刻 alas-utils.js::renderAnnouncementContent 的输出结构。"""
    import html as html_module

    chunks: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer or not "".join(buffer).strip():
            buffer.clear()
            return
        text = html_module.escape("\n".join(buffer))
        chunks.append(f'<div class="alas-announcement-text">{text}</div>')
        buffer.clear()

    for line in body:
        text = line.strip()
        matched = MD_IMAGE_RE.match(text)
        alt = ""
        src = None
        if matched:
            alt, src = matched.group(1), matched.group(2)
        else:
            raw = RAW_IMAGE_RE.match(text)
            if raw:
                src = raw.group(1)
        if src:
            flush()
            chunks.append(f'<img class="alas-announcement-image" src="{html_module.escape(src, quote=True)}" alt="{html_module.escape(alt, quote=True)}">')
        else:
            buffer.append(line)
    flush()
    return "".join(chunks)


def write_preview(title: str, announcement_id: str, body: list[str], payload: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    html = PREVIEW_TEMPLATE.format(
        title=title,
        announcement_id=announcement_id,
        content_html=body_to_html(body),
        payload_json=json.dumps(payload, ensure_ascii=False, indent=4),
    )
    out.write_text(html, encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def cmd_preview(args: argparse.Namespace) -> int:
    draft = Path(args.draft).resolve()
    title, body = read_draft(draft)
    announcement_id = args.id or time.strftime("%Y%m%d") + "-preview"
    body_preview = rewrite_body(body, None, embed_local=True)
    payload = {"announcementId": announcement_id, "title": title, "content": "\n".join(body_preview), "url": ""}

    out = Path(args.out).resolve() if args.out else (REPO_ROOT / ".workbuddy" / "announcement" / f"preview-{announcement_id}.html")
    write_preview(title, announcement_id, body_preview, payload, out)

    print(f"预览已生成：{out}")
    print(f"标题：{title}")
    print(f"公告 ID：{announcement_id}（预览占位，正式发布时按当天序号生成）")
    print(f"本地图片：{sum(1 for line in body if is_local_image(line))} 张")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    draft = Path(args.draft).resolve()
    title, body = read_draft(draft)

    token = get_token_or_die()
    remote, remote_sha = fetch_remote_announcement(token)
    announcement_id = args.id or next_announcement_id(
        (remote or {}).get("announcementId"), time.strftime("%Y%m%d")
    )

    local_images: list[Path] = []
    for line in body:
        path = is_local_image(line)
        if path is not None:
            key = str(path.resolve())
            if key not in [str(item.resolve()) for item in local_images]:
                local_images.append(path)

    print(f"公告 ID：{announcement_id}")
    print(f"标题：{title}")
    print(f"待上传图片：{len(local_images)} 张")
    for path in local_images:
        print(f"  - {path}（{path.stat().st_size / 1024:.1f} KB）")

    if args.dry_run:
        print("\n[dry-run] 未做任何写入。去掉 --dry-run 才会真正发布。")
        return 0

    image_ref = BRANCH
    local_to_url: dict[str, str] = {}
    uploaded: list[str] = []
    for index, path in enumerate(local_images, start=1):
        repo_path = f"{ASSET_DIR}/{announcement_id}/{path.name}"
        commit_sha = put_file(
            token,
            repo_path,
            path.read_bytes(),
            f"chore(announcement): 上传公告图片 {announcement_id} - {path.name}",
            sha=None,
        )
        if index == 1 and args.image_ref == "sha":
            image_ref = commit_sha
        local_to_url[str(path.resolve())] = f"{CDN_ROOT}/{REPO}@{image_ref}/{repo_path}"
        uploaded.append(repo_path)
        print(f"已上传 {path.name} -> commit {commit_sha[:10]}")

    body_published = rewrite_body(body, local_to_url, embed_local=False)
    payload = {
        "announcementId": announcement_id,
        "title": title,
        "content": "\n".join(body_published),
        "url": "",
    }
    content = (json.dumps(payload, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
    commit_sha = put_file(
        token,
        ANNOUNCEMENT_PATH,
        content,
        f"chore(announcement): 发布公告 {announcement_id} - {title}",
        sha=remote_sha,
    )
    print(f"已发布 {ANNOUNCEMENT_PATH} -> commit {commit_sha[:10]}")

    # 本地文件同步成远端内容，避免下次 dev -> master 同步时把公告顶回去（不提交）
    local_file = REPO_ROOT / ANNOUNCEMENT_PATH
    local_file.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    print(f"已同步本地 {ANNOUNCEMENT_PATH}（未提交，避免误导后续同步）")

    failed = purge_jsdelivr([ANNOUNCEMENT_PATH, *uploaded])
    if failed:
        print("以下 jsdelivr 缓存刷新失败（不影响生效，最多等 12 小时缓存过期）：")
        for url in failed:
            print(f"  - {url}")
    else:
        print("已请求刷新 jsdelivr 缓存。")

    print("\n发布完成。验证地址：")
    print(f"  {CDN_ROOT}/{REPO}@{BRANCH}/{ANNOUNCEMENT_PATH}")
    return 0


def get_token_or_die() -> str:
    try:
        return get_credential()
    except RuntimeError as error:
        print(f"无法获取凭据：{error}", file=sys.stderr)
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AzurPilot 公告发布工具（GitHub API 直写）")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--draft", default=str(DEFAULT_DRAFT), help="草稿 md 路径")
    common.add_argument("--id", default="", help="指定公告 ID（默认按当天序号自动生成）")

    preview = sub.add_parser("preview", parents=[common], help="生成本地预览 HTML（不联网、不写入）")
    preview.add_argument("--out", default="", help="预览 HTML 输出路径")
    preview.set_defaults(func=cmd_preview)

    publish = sub.add_parser("publish", parents=[common], help="上传图片并发布公告到远端 master")
    publish.add_argument("--image-ref", choices=["sha", "master"], default="sha",
                         help="图片外链使用固定 commit sha（默认，立即生效）还是 master 分支引用")
    publish.add_argument("--dry-run", action="store_true", help="只打印计划，不做任何写入")
    publish.set_defaults(func=cmd_publish)
    return parser


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 老终端不支持时忽略
        pass
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
