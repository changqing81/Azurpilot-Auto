"""AzurPilot 环境重置脚本。

用途：把仓库目录恢复到「只有基础文件」的状态，让下次启动（或运行 一键启动.bat）
自动重新下载 Python 并重建 .venv。

设计要点：
- 默认交互确认，直接回车 = 取消，避免误触。
- 保留清单之外的内容才会被删除；保留清单里的东西在任何情况下都不动。
- 删除前会先结束占用本目录的进程（Windows 上 .venv 常被 python.exe / adb.exe 占用）。
- 删除前断言每个目标都在仓库根目录之下，防止误删仓库外的文件。

用法：
    python deploy/reset/reset_env.py            # 交互式
    python deploy/reset/reset_env.py --yes      # 跳过确认
    python deploy/reset/reset_env.py --list     # 只列出将被删除的内容，不删除
    python deploy/reset/reset_env.py --clean-uv-cache   # 同时清空 uv 全局缓存
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

# 仓库根目录：本文件位于 <root>/deploy/reset/reset_env.py
REPO_ROOT = Path(__file__).resolve().parents[2]

# 保留清单：这些名字（在仓库根目录下）永不删除
KEEP_NAMES = {
    # 用户数据与基础目录
    "config",            # 配置 + 数据库
    "deploy",            # 部署脚本（含本脚本自身）
    "log",               # 日志
    # 启动器与安装器
    "alas-launcher.exe",
    "unins000.dat",      # Inno Setup 卸载信息，删了会导致无法卸载
    "unins000.exe",
    # 打包产物的运行时脚手架
    "bootstrap",
    # 入口脚本（删掉会让用户失去入口；它们也是仓库跟踪文件，下次更新会回来）
    "重置环境.bat",
    "一键启动.bat",
}

# 额外的保护路径（绝对路径），例如本脚本自身
KEEP_PATHS = {
    Path(__file__).resolve(),
}


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def dir_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def is_protected(path: Path) -> bool:
    """判断某个根目录下的条目是否应当保留。"""
    resolved = path.resolve()
    if resolved in KEEP_PATHS:
        return True
    if resolved.parent != REPO_ROOT:
        # 只处理仓库根目录的直接子项
        return True
    return path.name in KEEP_NAMES or path.name.lower() in {n.lower() for n in KEEP_NAMES}


def collect_targets() -> list[Path]:
    targets = []
    try:
        entries = sorted(REPO_ROOT.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        print(f"[!] 无法读取仓库目录 {REPO_ROOT}: {e}")
        return []
    for entry in entries:
        if is_protected(entry):
            continue
        targets.append(entry)
    return targets


def kill_processes_using_repo() -> None:
    """结束 exe 或工作目录位于仓库内的进程。"""
    me = os.getpid()
    killed = []

    try:
        import psutil  # type: ignore
    except ImportError:
        print("[i] 未安装 psutil，跳过占用进程检查（若删除失败请手动关闭启动器/游戏脚本后重试）")
        return

    for proc in psutil.process_iter(["pid", "name", "exe", "cwd"]):
        try:
            if proc.pid == me:
                continue
            info = proc.info
            exe = info.get("exe")
            cwd = info.get("cwd")
            inside = False
            for candidate in (exe, cwd):
                if not candidate:
                    continue
                try:
                    if Path(candidate).resolve().is_relative_to(REPO_ROOT):
                        inside = True
                        break
                except OSError:
                    continue
            if inside:
                killed.append(f"{info.get('name')}({proc.pid})")
                proc.kill()
        except Exception:
            continue

    if killed:
        print(f"[i] 已结束占用进程: {', '.join(killed)}")
        time.sleep(0.8)
    else:
        print("[i] 没有发现占用本目录的进程")


def clear_readonly(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def remove_target(path: Path, retries: int = 10) -> None:
    """删除文件或目录，带只读清除与重试。"""
    if not path.exists() and not path.is_symlink():
        return

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            if path.is_symlink() or path.is_file():
                clear_readonly(path)
                path.unlink()
            else:
                shutil.rmtree(path, onerror=_on_rm_error)
            return
        except Exception as e:  # noqa: BLE001
            last_error = e
            if not path.exists() and not path.is_symlink():
                return
            time.sleep(0.25 + attempt * 0.15)

    raise RuntimeError(f"删除失败: {path} -> {last_error}")


def _on_rm_error(func, path, exc_info):  # noqa: ANN001
    try:
        clear_readonly(Path(path))
        func(path)
    except Exception:
        pass


def clean_uv_cache() -> None:
    """清空 uv 全局缓存。默认不做，因为它会让重建环境变慢。"""
    candidates = [
        REPO_ROOT / ".venv" / "Scripts" / "uv.exe",
        REPO_ROOT / ".venv" / "bin" / "uv",
    ]
    uv = next((c for c in candidates if c.exists()), None)
    if uv is None:
        uv = shutil.which("uv")
    if uv is None:
        print("[i] 未找到 uv，跳过缓存清理")
        return

    print(f"[i] 正在清空 uv 缓存: {uv}")
    try:
        subprocess.run([str(uv), "cache", "clean"], check=False)
        print("[i] uv 缓存已清理")
    except Exception as e:  # noqa: BLE001
        print(f"[!] uv 缓存清理失败: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="AzurPilot 环境重置")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认，直接执行")
    parser.add_argument("--list", action="store_true", help="只列出将被删除的内容")
    parser.add_argument("--clean-uv-cache", action="store_true", help="同时清空 uv 全局缓存")
    args = parser.parse_args()

    print("=" * 62)
    print("  AzurPilot 环境重置")
    print("=" * 62)
    print(f"仓库目录: {REPO_ROOT}")
    print()

    if not (REPO_ROOT / "deploy").is_dir():
        print("[!] 这里看起来不是 AzurPilot 仓库目录，已中止。")
        print("    请把本脚本放在 <仓库根目录>/deploy/env/ 下运行。")
        return 1

    targets = collect_targets()

    print("【保留】")
    for name in sorted(KEEP_NAMES):
        if (REPO_ROOT / name).exists():
            print(f"    {name}")
    print()

    print("【将删除】")
    if not targets:
        print("    (没有需要删除的内容)")
    total = 0
    for path in targets:
        try:
            size = dir_size(path) if path.is_dir() else path.stat().st_size
        except OSError:
            size = 0
        total += size
        kind = "目录" if path.is_dir() else "文件"
        print(f"    [{kind}] {path.name}  ({human(size)})")
    print()
    print(f"合计约 {human(total)}")

    if not targets:
        print("\n环境已经是干净状态，无需操作。")
        return 0

    if args.list:
        return 0

    print()
    if not args.yes:
        print("按 y + 回车确认执行，其它任意输入 = 取消。")
        try:
            answer = input("确认重置环境？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("y", "yes"):
            print("已取消，未做任何改动。")
            return 0

    print()
    kill_processes_using_repo()

    failures = []
    for path in targets:
        # 再次断言：只允许删除仓库根目录之下的条目
        try:
            if path.resolve().parent != REPO_ROOT:
                print(f"[!] 跳过非根目录条目: {path}")
                continue
        except OSError:
            continue
        print(f"[>] 删除 {path}")
        try:
            remove_target(path)
        except Exception as e:  # noqa: BLE001
            failures.append(f"{path} -> {e}")

    if args.clean_uv_cache:
        print()
        clean_uv_cache()

    print()
    print("=" * 62)
    if failures:
        print(f"[!] 有 {len(failures)} 项删除失败：")
        for item in failures:
            print(f"    {item}")
        print("    请关闭占用这些文件/目录的程序后重试。")
        return 2

    print("[OK] 环境已重置。")
    print("     下次运行 一键启动.bat 或启动器时会自动重新下载 Python 并重建依赖。")
    print("     config/ 与 log/ 里的内容没有被改动。")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    raise SystemExit(main())
