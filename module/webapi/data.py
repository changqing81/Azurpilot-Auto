"""主页数据源：把现有业务函数包装成可序列化 dict。

数据归属（勿在此处重复实现）：
- 实例列表/状态 → module/webui/worker_registry.py + process_manager.py（经 State）
- 任务队列   → alas 配置实例的 get_next_task()（app_dashboard.py 同源）
- 仪表盘     → module/log_res/log_res.py LogRes + config dashboard.yaml 资源清单
- 版本       → module/webui/update_log.py / deploy config
"""
from __future__ import annotations

import json
import os
from typing import Any

from module.config.utils import read_file
from module.logger import logger

_DASHBOARD_MANIFEST_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config", "argument", "dashboard.yaml")
)


def _load_yaml(path: str) -> dict:
    """读 YAML；失败返回空表，避免主页因单个数据源挂掉而整页 500。"""
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 —— 数据源降级，主页骨架必须始终可渲染
        logger.warning(f"[webapi] 读取 {path} 失败: {e}")
        return {}


def get_version() -> dict:
    """版本信息（页脚 --version CSS 变量的数据源）。

    与 app_overview.py:416 同源：本地 git commit 短哈希，取不到用 Unknown。
    """
    import subprocess

    try:
        commit = subprocess.run(
            ["git", "log", "-1", "--format=%h"],
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return {"version": commit or "Unknown"}
    except Exception:  # noqa: BLE001 —— 打包发行版可能没有 .git
        return {"version": "Unknown"}


def get_instances() -> dict:
    """实例名列表 + 每个实例的运行状态（等价老 UI set_aside_status 的数据面）。

    状态码沿用老 UI 约定：1=running / 2=inactive / 3=warning / 4=updating。
    """
    from module.config.utils import alas_instance

    items = []
    try:
        for inst in alas_instance():
            items.append({
                "name": inst,
                # 运行状态由 /newapi/instance/{name}/status 提供（P2 接 ProcessManager）；先 inactive 兜底
                "state": 2,
            })
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 实例列表获取失败: {e}")
    return {"instances": items}


def get_dashboard(name: str) -> dict:
    """仪表盘数据（等价 alas_update_dashboard 的读取面）。

    数据真实来源（log_res.py:47-58 同源）：实例配置 JSON 的 Dashboard.<Item>.Value/Record。
    资源清单与老 UI dashboard.yaml 同源（Dashboard: [Oil, Coin, ...]）。
    默认只显示 Oil/Coin/Gem/Pt 四项（app_dashboard.py:608 的默认行为），全部清单
    交给前端按用户的开关决定。
    """
    from module.config.config import AzurLaneConfig
    from module.config.deep import deep_get

    manifest = _load_yaml(_DASHBOARD_MANIFEST_PATH).get("Dashboard") or []
    try:
        config = AzurLaneConfig(name, task=None)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[webapi] 实例配置加载失败({name}): {e}")
        config = None
    items: list[dict[str, Any]] = []
    if config is not None:
        for item in manifest:
            value = deep_get(config.data, keys=f"Dashboard.{item}.Value")
            record = deep_get(config.data, keys=f"Dashboard.{item}.Record")
            if value is None:
                continue
            items.append({"name": item, "value": value, "record": str(record or "")})
    return {"items": items}
