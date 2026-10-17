"""舰船数据自动同步：从 data/ship 分支拉取 CI 生成的最新数据并应用到工作区。

上游链路（全程自动）：
    AzurLaneTools/AzurLaneLuaScripts（游戏解包 Lua，bot 跟随游戏日更）
      → .github/workflows/ship-data-sync.yml（每日提取，有变化才提交）
      → data/ship 分支（仅含 assets/ship 数据文件的专用分支）
      → 本模块（WebUI 后台任务，每 6 小时拉取一次）

写入新数据后，三个消费点（ShipNameMatcher、fleet_tech.catalog、
mind_calculator.catalog）都通过 file_cached 按文件 mtime 自动失效，
长驻进程无需重启即可读到新名单。
"""

import subprocess
from pathlib import Path

from module.logger import logger

ROOT = Path(__file__).parents[1]
DATA_REMOTE = 'origin'
DATA_BRANCH = 'data/ship'
DATA_FILES = (
    'assets/ship/ship_names.json',
    'assets/ship/ship_data.json',
    'assets/ship/fleet_tech.json',
)
GIT_TIMEOUT = 180


def _git(*args, binary=False):
    """执行 git 子进程；环境错误（git 缺失/超时）由调用方捕获。

    Args:
        *args: git 参数。
        binary: True 时 stdout 以 bytes 返回（取文件内容用）。

    Returns:
        subprocess.CompletedProcess: 子进程结果。
    """
    if binary:
        return subprocess.run(
            ['git', '-C', str(ROOT), *args],
            capture_output=True, timeout=GIT_TIMEOUT,
        )
    return subprocess.run(
        ['git', '-C', str(ROOT), *args],
        capture_output=True, text=True, encoding='utf-8', timeout=GIT_TIMEOUT,
    )


def _remote_blob(path):
    """远端分支上该文件的 blob 哈希；分支或文件不存在时返回 None。"""
    result = _git('rev-parse', f'{DATA_REMOTE}/{DATA_BRANCH}:{path}')
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.strip()


def _local_blob(path):
    """工作区该文件内容的 blob 哈希；文件缺失时返回 None。"""
    result = _git('hash-object', '--', path)
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.strip()


def sync_ship_data() -> dict:
    """从 data/ship 分支同步舰船数据文件，返回同步摘要。

    作为 WebUI 后台周期任务运行：网络或仓库异常只记录日志、不向上抛出，
    避免任务被 TaskHandler 因异常移除。

    Returns:
        dict: status 为 updated / uptodate / skipped / error 之一，
              files 记录各文件的 old/new blob 哈希。
    """
    summary = {'status': 'error', 'files': {}}
    try:
        fetch = _git('fetch', DATA_REMOTE, DATA_BRANCH)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f'[舰船数据] 拉取失败（Git 不可用或网络异常）: {exc}')
        return summary

    if fetch.returncode != 0:
        # data/ship 尚未由 CI 创建（或远端暂不可达）时静默跳过。
        detail = (fetch.stderr or '').strip().splitlines()
        logger.debug(
            f'[舰船数据] {DATA_REMOTE}/{DATA_BRANCH} 暂不可用: '
            f'{detail[-1] if detail else fetch.returncode}'
        )
        summary['status'] = 'skipped'
        return summary

    updated = []
    pending = False
    for path in DATA_FILES:
        remote = _remote_blob(path)
        if remote is None:
            continue
        local = _local_blob(path)
        summary['files'][path] = {'old': local, 'new': remote}
        if remote == local:
            continue
        pending = True
        fetched = _git('show', f'{DATA_REMOTE}/{DATA_BRANCH}:{path}', binary=True)
        if fetched.returncode != 0 or not fetched.stdout:
            logger.warning(f'[舰船数据] 读取 {path} 内容失败，跳过本轮')
            continue
        try:
            (ROOT / path).write_bytes(fetched.stdout)
        except OSError as exc:
            logger.warning(f'[舰船数据] 写入 {path} 失败: {exc}')
            continue
        updated.append(path)

    if updated:
        summary['status'] = 'updated'
        logger.info(f'[舰船数据] 已同步 {len(updated)} 个文件: {", ".join(updated)}')
    elif pending:
        # 检测到远端更新但未能应用（读取/写入失败），保留 error 便于排查。
        summary['status'] = 'error'
    else:
        summary['status'] = 'uptodate'
    return summary
