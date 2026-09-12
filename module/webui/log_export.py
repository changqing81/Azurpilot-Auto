"""WebUI 日志导出工具。

定位实例当天的运行日志、把错误日志目录打包成单个压缩文件。
仅包含纯逻辑，由 module.webui.api 的路由调用，便于单独测试。

日志文件命名约定来自 module.logger：RichTimedRotatingHandler 启动即把
`log/{name}.txt` 轮转为 `log/{YYYY-MM-DD}_{name}.txt` 并删掉未轮转的 base
文件，而实例进程用 config_name 作为 name（alas.py / process_manager.py 均调用
logger.set_file_logger(self.config_name)）。因此某实例当天的运行日志就是
`log/{今天}_{实例名}.txt`。
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

LOG_DIRNAME = "log"
ERROR_LOG_DIRNAME = "error"

# 已压缩格式用 ZIP_STORED：PNG/JPG 再 deflate 只是白烧 CPU，体积几乎不变
_STORED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".mp4"}

# 打包范围：full = log/error 下全部文件；text = 仅文本类文件（跳过截图，体积小得多）
SCOPE_FULL = "full"
SCOPE_TEXT = "text"
_REAL_SCOPES = (SCOPE_FULL, SCOPE_TEXT)

# 运行日志导出范围：today = 仅当天；all = 全部历史合并（默认，排查通常需要跨天上下文）
RUNTIME_SCOPE_ALL = "all"
RUNTIME_SCOPE_TODAY = "today"
_RUNTIME_SCOPES = (RUNTIME_SCOPE_ALL, RUNTIME_SCOPE_TODAY)

_TEXT_EXTS = {
    ".txt",
    ".log",
    ".json",
    ".csv",
    ".yaml",
    ".yml",
    ".md",
    ".ini",
    ".cfg",
}
# 文本类在 zip 里走 deflate，实测大致压到三成左右；图片是 STORED 不缩小。
# 只用于给用户一个量级预期，不追求精确。
_TEXT_COMPRESS_RATIO = 0.3


def get_project_root() -> Path:
    """项目根目录（含 module/、log/、config/）。

    本文件位于 module/webui/ 下，故取 parents[2]；用绝对路径而不是相对
    路径，避免受进程 cwd 影响（logger.py 会做模块级 os.chdir）。
    """
    return Path(__file__).resolve().parents[2]


def today_str() -> str:
    """当天日期字符串，与日志轮转的前缀格式一致。"""
    return datetime.now().strftime("%Y-%m-%d")


def validate_instance(name) -> str:
    """校验实例名，只接受 config/*.json 中真实存在的实例。

    白名单校验同时挡掉了路径穿越（如 `../`），非法值抛 ValueError 由路由转 400。
    """
    from module.config.utils import alas_instance

    name = str(name or "").strip()
    if not name:
        raise ValueError("缺少实例名")
    if name not in set(alas_instance()):
        raise ValueError(f"未知实例: {name}")
    return name


def find_today_runtime_log(instance: str) -> Path | None:
    """严格返回实例「今天」的运行日志，今天没跑过就是 None。

    这里刻意**不再回退到历史日志**：早先的回退会让用户点「导出当天日志」却拿到
    别天的内容（例如实例今天只跑了 9 秒、拿到 3.4KB，而昨天的有 4.7MB），
    极易被误解成"文件不完整/被截断"。历史内容改由 scope=all 显式提供。
    """
    path = get_project_root() / LOG_DIRNAME / f"{today_str()}_{instance}.txt"
    return path if path.is_file() else None


def normalize_runtime_scope(scope) -> str:
    """运行日志导出范围，非法值一律按 all（最完整的那个）。"""
    value = str(scope or "").strip().lower()
    return value if value in _RUNTIME_SCOPES else RUNTIME_SCOPE_ALL


def find_runtime_logs(instance: str, scope: str = RUNTIME_SCOPE_ALL) -> list:
    """按时间升序返回待导出的运行日志文件。

    scope=TODAY 只返回当天那份；scope=ALL 返回全部历史（含未轮转的 base 文件）。
    """
    if normalize_runtime_scope(scope) == RUNTIME_SCOPE_TODAY:
        today = find_today_runtime_log(instance)
        return [today] if today else []

    log_dir = get_project_root() / LOG_DIRNAME
    files = sorted(log_dir.glob(f"*_{instance}.txt"))
    base = log_dir / f"{instance}.txt"
    if base.is_file():
        files.append(base)
    return files


def describe_runtime_logs(instance: str, scope: str = RUNTIME_SCOPE_ALL) -> dict:
    """统计待导出的运行日志，供界面在导出前展示真实体积。"""
    scope = normalize_runtime_scope(scope)
    files = find_runtime_logs(instance, scope)
    total = sum(path.stat().st_size for path in files)
    return {
        "scope": scope,
        "instance": instance,
        "files": len(files),
        "bytes": total,
        "human_bytes": format_bytes(total),
    }


def build_runtime_log_bundle(instance: str, scope: str = RUNTIME_SCOPE_ALL) -> tuple:
    """把实例的运行日志整理成一个可供下载的 txt。

    返回 ``(路径, 建议文件名, 是否临时文件)``：
    - 只命中一个文件时直接返回原文件（**不复制**，调用方切勿删除）；
    - 命中多个文件时按日期升序拼接为临时文件，每份前面加一行分隔标题，
      便于在同一个文件里定位是哪天的日志。临时文件由调用方负责删除。

    没有任何日志时抛 FileNotFoundError（路由转 404）。
    """
    files = find_runtime_logs(instance, scope)
    if not files:
        raise FileNotFoundError(f"实例 {instance} 没有可导出的运行日志")

    if len(files) == 1:
        return files[0], files[0].name, False

    dates = sorted(path.name.split("_", 1)[0] for path in files if "_" in path.name)
    filename = f"{dates[0]}~{dates[-1]}_{instance}.txt" if dates else f"{instance}.txt"

    handle, tmp_name = tempfile.mkstemp(
        prefix=f"alas_runtime_log_{os.getpid()}_", suffix=".txt"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as out:
            for index, path in enumerate(files):
                if index:
                    out.write(b"\n")
                banner = f"{'═' * 79}\n[ {path.name} ]\n{'═' * 79}\n"
                out.write(banner.encode("utf-8"))
                out.write(path.read_bytes())
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return tmp_path, filename, True


def normalize_scope(scope) -> str:
    """把外部传入的 scope 归一化，非法值一律按 full 处理。"""
    value = str(scope or "").strip().lower()
    return value if value in _REAL_SCOPES else SCOPE_FULL


def _error_dir() -> Path:
    return get_project_root() / LOG_DIRNAME / ERROR_LOG_DIRNAME


def iter_error_files(scope: str = SCOPE_FULL):
    """按范围枚举 log/error 下的文件；目录不存在抛 FileNotFoundError。"""
    error_dir = _error_dir()
    if not error_dir.exists():
        raise FileNotFoundError(f"错误日志目录不存在: {error_dir}")

    text_only = normalize_scope(scope) == SCOPE_TEXT
    for path in sorted(error_dir.rglob("*")):
        if not path.is_file():
            continue
        if text_only and path.suffix.lower() not in _TEXT_EXTS:
            continue
        yield path


def describe_error_log_dir(scope: str = SCOPE_FULL) -> dict:
    """统计待打包内容，供前端在导出前展示真实体积。

    返回 files（文件数）、bytes（原始总字节）、estimate_bytes（压缩包大小的粗略估计）。
    """
    scope = normalize_scope(scope)
    files = 0
    total = 0
    stored = 0
    for path in iter_error_files(scope):
        size = path.stat().st_size
        files += 1
        total += size
        if path.suffix.lower() in _STORED_EXTS:
            stored += size

    compressible = total - stored
    return {
        "scope": scope,
        "files": files,
        "bytes": total,
        "estimate_bytes": stored + int(compressible * _TEXT_COMPRESS_RATIO),
        "human_bytes": format_bytes(total),
        "human_estimate": format_bytes(stored + int(compressible * _TEXT_COMPRESS_RATIO)),
    }


def build_error_log_zip(scope: str = SCOPE_FULL) -> Path:
    """把 log/error 下符合条件的文件递归打包为单个 zip，返回临时文件路径。

    调用方负责在响应发送完成后删除该文件（见 api.py 的 BackgroundTask）。
    `scope=SCOPE_FULL` 打包全部文件；`scope=SCOPE_TEXT` 只打包文本类文件
    （跳过 PNG/JPG 截图，体积通常从几十 MB 降到几十 KB）。
    目录结构原样保留；log/error 根下的散落文件作为 zip 根条目。
    目录不存在时抛 FileNotFoundError（路由转 404）；空范围产出合法的空 zip。
    """
    handle, tmp_name = tempfile.mkstemp(
        prefix=f"alas_error_log_{os.getpid()}_", suffix=".zip"
    )
    os.close(handle)
    tmp_path = Path(tmp_name)

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            error_dir = _error_dir()
            for path in iter_error_files(scope):
                arcname = path.relative_to(error_dir).as_posix()
                compress = (
                    zipfile.ZIP_STORED
                    if path.suffix.lower() in _STORED_EXTS
                    else zipfile.ZIP_DEFLATED
                )
                archive.write(path, arcname, compress_type=compress)
    except Exception:
        # 打包中途失败（目录不存在、磁盘满、文件被占用等）不要留下半截临时文件
        tmp_path.unlink(missing_ok=True)
        raise

    return tmp_path


def format_bytes(size: int) -> str:
    """把字节数格式化为人类可读字符串，用于日志与前端提示。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
