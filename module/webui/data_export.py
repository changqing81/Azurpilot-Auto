"""WebUI 数据导出工具。

把「数据」打包成单个 zip 供浏览器下载。文件范围与「导入旧数据」接收的
数据部分保持对称（配置文件除外，它由管理页每张实例卡片的「导出」单独提供）：

- ``config/*.db``                          AzurStats / 资源增减 / 每日总结等 sqlite 库
- ``log/cl1/<实例>/``                      各实例的 CL1 掉落记录
- ``log/azurstat_meowofficer_farming.csv`` 指挥喵养成的本地统计

两种导出范围：

- 全量（instance=None）：所有文件原样打包，zip 保留 ``config/``、``log/``
  目录结构，解压后可直接在新机器上通过「导入旧数据」原样还原；
- 单实例（instance=名称）：CL1 记录只取该实例目录；共享统计库复制成临时
  副本并删除其他实例的行（无 instance 列的全局表原样保留），因此包里
  不会混入其他实例的记录，体积也小得多。

仅包含纯逻辑，由 module.webui.api 的路由调用，便于单独测试。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator, Optional

from module.logger import logger
from module.webui.log_export import format_bytes, get_project_root

# 指挥喵养成统计的固定路径（module/statistics/azurstats.py 的 LOCAL_MEOW_CSV）
MEOW_FARMING_CSV = "log/azurstat_meowofficer_farming.csv"

# 「导出范围」下拉里表示全部实例的保留值，与路由 path 参数共用
ALL_INSTANCES = "all"

# 单实例导出时允许按 instance 列过滤的表。SQL 必须内联在 execute 调用里
# （安全扫描要求 execute 的 SQL 为字面量、实例值走 ? 参数绑定），
# 因此这里只登记表名，删除语句在 filter_db_by_instance 中按分支内联；
# 未登记但含 instance 列的表整表保留（降级为全局数据，不丢数据）。
_INSTANCE_FILTER_TABLES = {"resource_snapshots", "cl1_data", "resource_delta_events"}


def iter_shared_dbs() -> Iterator[Path]:
    """项目内全部统计 sqlite 库（``config/*.db``，存在才收）。"""
    root = get_project_root()
    for path in sorted((root / "config").glob("*.db")):
        if path.is_file():
            yield path


def iter_cl1_files(instance: Optional[str] = None) -> Iterator[Path]:
    """CL1 掉落记录：全量时取全部实例目录，单实例时只取该实例目录。"""
    root = get_project_root()
    base = root / "log" / "cl1"
    if instance:
        base = base / instance
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            if path.is_file():
                yield path


def iter_data_files(instance: Optional[str] = None) -> Iterator[Path]:
    """枚举待导出的**原样文件**。

    单实例模式下共享库的过滤副本由 :func:`build_data_zip` 打包时现做，
    这里只负责文件清单与体积统计。
    """
    yield from iter_shared_dbs()
    yield from iter_cl1_files(instance)
    csv_path = get_project_root() / MEOW_FARMING_CSV
    if csv_path.is_file():
        yield csv_path


def describe_data_files(instance: Optional[str] = None) -> dict:
    """统计待导出数据的体积，供界面在下载前展示量级。

    单实例模式下统计库显示的是**过滤前**的原始大小——预览不做一遍过滤
    拷贝，那等于把打包提前跑了一次；实际 zip 会小得多，界面文案已注明。
    """
    files = 0
    total = 0
    for path in iter_data_files(instance):
        files += 1
        total += path.stat().st_size
    return {
        "instance": instance or ALL_INSTANCES,
        "files": files,
        "bytes": total,
        "human_bytes": format_bytes(total),
    }


def filter_db_by_instance(src: Path, instance: str) -> Path:
    """把 sqlite 库过滤成只含单实例记录的副本，返回临时文件路径。

    先用 backup API 整库复制（零 SQL），再对登记过的表删除其他实例的行
    （删除语句逐分支内联、实例值走 ? 参数绑定），最后 VACUUM 回收空间，
    因此副本文件体积与剩余数据量相称。未登记但含 instance 列的表整表
    保留并记日志。调用方负责删除临时文件。
    """
    handle, tmp_name = tempfile.mkstemp(
        prefix=f"azurpilot_data_{os.getpid()}_", suffix=".db"
    )
    os.close(handle)
    tmp_path = Path(tmp_name)

    try:
        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        dst_conn = sqlite3.connect(tmp_path)
        try:
            src_conn.backup(dst_conn)
            tables = [
                row[0]
                for row in dst_conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                columns = [
                    row[0]
                    for row in dst_conn.execute(
                        "SELECT name FROM pragma_table_info(?)", (table,)
                    )
                ]
                if "instance" not in columns:
                    continue
                if table not in _INSTANCE_FILTER_TABLES:
                    logger.warning(
                        f"[WebUI] 数据导出: {src.name} 的表 {table} 含 instance 列"
                        "但未登记过滤，整表保留"
                    )
                    continue
                if table == "cl1_data":
                    dst_conn.execute(
                        "DELETE FROM cl1_data "
                        "WHERE instance IS NOT NULL AND instance <> ?",
                        (instance,),
                    )
                elif table == "resource_snapshots":
                    dst_conn.execute(
                        "DELETE FROM resource_snapshots "
                        "WHERE instance IS NOT NULL AND instance <> ?",
                        (instance,),
                    )
                elif table == "resource_delta_events":
                    dst_conn.execute(
                        "DELETE FROM resource_delta_events "
                        "WHERE instance IS NOT NULL AND instance <> ?",
                        (instance,),
                    )
            dst_conn.commit()
            dst_conn.execute("VACUUM")
        finally:
            src_conn.close()
            dst_conn.close()
    except Exception:
        # 过滤中途失败（文件被占用、磁盘满等）不要留下半截临时文件
        tmp_path.unlink(missing_ok=True)
        raise

    return tmp_path


def build_data_zip(instance: Optional[str] = None) -> Path:
    """把数据文件打包为单个 zip，返回临时文件路径，调用方负责删除。

    单实例模式下共享统计库先经 :func:`filter_db_by_instance` 过滤成临时
    副本再入包，zip 内 arcname 保持相对项目根的目录结构不变。
    一个文件都没有时抛 FileNotFoundError（路由转 404）。
    """
    files = list(iter_data_files(instance))
    if not files:
        raise FileNotFoundError("没有可导出的数据文件")

    root = get_project_root()
    handle, tmp_name = tempfile.mkstemp(
        prefix=f"azurpilot_data_{os.getpid()}_", suffix=".zip"
    )
    os.close(handle)
    tmp_path = Path(tmp_name)
    filtered_copies: list[Path] = []

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                arcname = path.relative_to(root).as_posix()
                is_shared_db = path.parent.name == "config" and path.suffix == ".db"
                if instance and is_shared_db:
                    copy = filter_db_by_instance(path, instance)
                    filtered_copies.append(copy)
                    archive.write(copy, arcname)
                else:
                    archive.write(path, arcname)
    except Exception:
        # 打包中途失败（文件被占用、磁盘满等）不要留下半截临时文件
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        for copy in filtered_copies:
            copy.unlink(missing_ok=True)

    return tmp_path
