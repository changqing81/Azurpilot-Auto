"""WebUI 数据导出工具。

把「数据」打包成单个 zip 供浏览器下载。文件范围与「导入旧数据」接收的
数据部分保持对称（配置文件除外，它由管理页每张实例卡片的「导出」单独提供）：

- ``config/*.db``                        AzurStats / 资源增减 / 每日总结等 sqlite 库
- ``log/cl1/**``                         各实例的 CL1 掉落记录
- ``log/azurstat_meowofficer_farming.csv`` 指挥喵养成的本地统计

zip 内保留 ``config/``、``log/`` 的相对目录结构，因此解压后可以直接在新机器上
通过「导入旧数据」选文件夹原样还原。

仅包含纯逻辑，由 module.webui.api 的路由调用，便于单独测试。
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator

from module.webui.log_export import format_bytes, get_project_root

# 指挥喵养成统计的固定路径（module/statistics/azurstats.py 的 LOCAL_MEOW_CSV）
MEOW_FARMING_CSV = "log/azurstat_meowofficer_farming.csv"


def iter_data_files() -> Iterator[Path]:
    """枚举项目内全部数据文件，按固定顺序（库 → 掉落记录 → csv）。

    只 yield 真实存在的文件：全新安装可能还没有任何统计库或掉落记录。
    """
    root = get_project_root()

    for path in sorted((root / "config").glob("*.db")):
        if path.is_file():
            yield path

    cl1_dir = root / "log" / "cl1"
    if cl1_dir.is_dir():
        for path in sorted(cl1_dir.rglob("*")):
            if path.is_file():
                yield path

    csv_path = root / MEOW_FARMING_CSV
    if csv_path.is_file():
        yield csv_path


def describe_data_files() -> dict:
    """统计待导出的数据文件，供界面在下载前展示真实体积。"""
    files = 0
    total = 0
    for path in iter_data_files():
        files += 1
        total += path.stat().st_size
    return {
        "files": files,
        "bytes": total,
        "human_bytes": format_bytes(total),
    }


def build_data_zip() -> Path:
    """把数据文件打包为单个 zip，返回临时文件路径。

    调用方负责在响应发送完成后删除该文件（见 api.py 的 BackgroundTask）。
    db / csv / json 都是可压缩的文本类内容，统一走 deflate；zip 内 arcname
    保持相对项目根的目录结构，与「导入旧数据」的路径约定一致。
    一个文件都没有时抛 FileNotFoundError（路由转 404）。
    """
    files = list(iter_data_files())
    if not files:
        raise FileNotFoundError("没有可导出的数据文件")

    root = get_project_root()
    handle, tmp_name = tempfile.mkstemp(prefix=f"azurpilot_data_{os.getpid()}_", suffix=".zip")
    os.close(handle)
    tmp_path = Path(tmp_name)

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, path.relative_to(root).as_posix())
    except Exception:
        # 打包中途失败（文件被占用、磁盘满等）不要留下半截临时文件
        tmp_path.unlink(missing_ok=True)
        raise

    return tmp_path
