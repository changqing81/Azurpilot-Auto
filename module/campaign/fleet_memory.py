"""舰队等级记忆模块。

跨进程持久化低耗类任务（GemsFarming / ThreeOilLowCost / Ambush11）的舰队等级，
使任务被其他任务打断或调度器进程重启后恢复时，可以依据上次退出的等级直接决策，
不再盲目进船坞执行一遍初始换船检查（完整换船 UI 流程，耗时 1~4 分钟）。

等级数据来源：
- 战斗后 ``Level.lv_get()`` 的 6 位置 OCR（位置 0 为旗舰，与 ``lv32_triggered`` 口径一致）
- 换船流程中选中的 ``Ship.level``（精确值，确认完成后才记入）

数据持久化在 ``log/<config_name>.fleet_memory.json``，按任务 command 分 key::

    {
        "ThreeOilLowCost": {
            "flagship_lv": 5,
            "vanguard_lv": 3,
            "fleet_order": "fleet1_all",
            "record": "2026-09-30T10:00:00"
        }
    }

Pages:
    本模块不操作游戏界面，仅做数据记录。
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from module.logger import logger

# 记录有效期：超过后视为无记忆，回退进船坞实地核查（兜底行为与旧版一致）
MEMORY_TTL = timedelta(hours=24)
# 旗舰等级上限，达到 32 触发换船（与 module/combat/level.py 的 lv32_triggered 保持一致）
FLAGSHIP_CHANGE_LEVEL = 32

# 记忆文件固定存放目录
LOG_DIR = Path('./log')


def decide_flagship_check(memory: Optional[dict]) -> str:
    """根据舰队记忆决定任务开局是否执行初始换船检查。

    Args:
        memory (Optional[dict]): ``FleetMemory.read()`` 的结果，None 表示无有效记忆。

    Returns:
        str: 'skip'（记忆显示旗舰等级合格，跳过初始检查直接出击）、
             'change'（记忆显示旗舰已达到换船等级，立即换船）、
             'check'（无有效记忆，按旧流程进船坞实地核查）。
    """
    if not isinstance(memory, dict):
        return 'check'
    flagship = memory.get('flagship_lv')
    if not _valid_level(flagship):
        return 'check'
    if flagship >= FLAGSHIP_CHANGE_LEVEL:
        return 'change'
    return 'skip'


def lv_flagship_vanguard(lv) -> tuple:
    """从 ``lv_get()`` 的 6 位置 OCR 结果提取旗舰与先锋等级。

    位置 0 为旗舰（与 ``lv32_triggered`` 的判定口径一致），3~5 为先锋；
    无效位（未识别的 -1 等）直接忽略，先锋取有效位中的最高等级。

    Args:
        lv: ``Level.lv``，长度为 6 的等级列表。

    Returns:
        tuple[Optional[int], Optional[int]]: (旗舰等级, 先锋最高等级)，
            无有效数据的位置为 None。
    """
    if not isinstance(lv, (list, tuple)) or len(lv) != 6:
        return None, None
    flagship = lv[0] if isinstance(lv[0], int) and lv[0] > 0 else None
    candidates = [v for v in lv[3:6] if isinstance(v, int) and v > 0]
    vanguard = max(candidates) if candidates else None
    return flagship, vanguard


def _valid_level(value) -> bool:
    """判断等级是否为可记录的正整数。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class FleetMemory:
    """按任务持久化的舰队等级记忆。

    单写者（调度器进程），读取失败一律降级为无记忆，绝不影响任务运行。
    """

    def __init__(self, config_name: str):
        """
        Args:
            config_name (str): 配置实例名，如 `alas`。

        Raises:
            ValueError: 配置实例名含路径非法字符时抛出，防止路径穿越。
        """
        # 配置实例名用于拼接文件路径，严格拒绝 `..`、路径分隔符与盘符
        name = str(config_name).strip()
        if not name or '..' in name or '/' in name or '\\' in name or ':' in name:
            raise ValueError(f'非法的配置实例名: {config_name!r}')
        self.filepath = LOG_DIR / f'{name}.fleet_memory.json'

    def _guarded_path(self) -> Path:
        """规范化并校验路径必须限定在 ``log/`` 目录内，防止路径穿越。

        Returns:
            Path: 校验通过后的绝对路径。

        Raises:
            ValueError: 路径越出 log/ 目录时抛出。
        """
        root = LOG_DIR.resolve()
        path = self.filepath.resolve()
        # relative_to 在越界时抛 ValueError，是目录包含关系的权威校验
        path.relative_to(root)
        return path

    def read(self, command: str, fleet_order: str) -> Optional[dict]:
        """读取指定任务的舰队记忆。

        Args:
            command (str): 任务名，如 `ThreeOilLowCost`。
            fleet_order (str): 当前的 ``Fleet_FleetOrder`` 配置，用于校验记忆是否仍适用。

        Returns:
            Optional[dict]: 有效记忆条目；缺失、过期、舰队顺序变更时返回 None。
        """
        entry = self._load().get(command)
        if not isinstance(entry, dict):
            return None
        record = entry.get('record')
        try:
            recorded = datetime.fromisoformat(record)
        except (TypeError, ValueError):
            return None
        if datetime.now() - recorded > MEMORY_TTL:
            logger.attr('舰队记忆', f'{command} 记录已过期（{record}）')
            return None
        if entry.get('fleet_order') != fleet_order:
            logger.attr('舰队记忆', f'{command} 舰队顺序已变更，记录作废')
            return None
        return entry

    def write(self, command: str, flagship_lv=None, vanguard_lv=None, fleet_order=''):
        """写入/更新指定任务的舰队记忆（原子写）。

        等级参数无效（None 或非正数）时保留该字段旧值；两个字段都无效时不落盘，
        避免用空数据把有效记录的刷新时间顶掉。
        """
        if not _valid_level(flagship_lv) and not _valid_level(vanguard_lv):
            return
        data = self._load()
        entry = data.get(command)
        if not isinstance(entry, dict):
            entry = {}
        if _valid_level(flagship_lv):
            entry['flagship_lv'] = flagship_lv
        if _valid_level(vanguard_lv):
            entry['vanguard_lv'] = vanguard_lv
        entry['fleet_order'] = fleet_order
        entry['record'] = datetime.now().replace(microsecond=0).isoformat()
        data[command] = entry
        self._save(data)
        logger.attr(
            '舰队记忆',
            f"{command}: 旗舰 {entry.get('flagship_lv', '?')} 级 / "
            f"先锋 {entry.get('vanguard_lv', '?')} 级")

    def _load(self) -> dict:
        """读取记忆文件，任何异常都降级为空记录，绝不影响任务运行。"""
        try:
            filepath = self._guarded_path()
        except ValueError as e:
            logger.error(f'舰队记忆路径校验失败: {e}')
            return {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.warning(f'舰队记忆读取失败，按无记录处理: {self.filepath}, {e}')
            return {}

    def _save(self, data: dict):
        """原子写入记忆文件（临时文件 + os.replace）。"""
        try:
            filepath = self._guarded_path()
            # 临时文件基于已校验的路径派生，同样做一次包含校验
            tmp = filepath.with_name(filepath.name + '.tmp')
            tmp.relative_to(LOG_DIR.resolve())
        except ValueError as e:
            logger.error(f'舰队记忆路径校验失败，跳过写入: {e}')
            return
        filepath.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, filepath)


class FleetMemoryMixin:
    """低耗任务共用的舰队记忆能力：开局读记忆决策，任务退出时写回最后已知等级。

    依赖宿主类提供：``config``（含 task / config_name / Fleet_FleetOrder /
    GemsFarming_AllowHighFlagshipLevel）、``change_flagship`` 属性、``campaign`` 属性。

    Attributes:
        _memory_flagship_lv (Optional[int]): 换船流程记下的旗舰精确等级。
        _memory_vanguard_lv (Optional[int]): 换船流程记下的先锋精确等级。
    """

    _fleet_memory = None
    _memory_flagship_lv = None
    _memory_vanguard_lv = None

    def _init_fleet_memory(self):
        """读取舰队记忆，决定首轮是否强制触发换船块（初始检查）。

        Returns:
            bool: True 表示需要执行初始检查。
        """
        command = self.config.task.command
        self._fleet_memory = FleetMemory(self.config.config_name)
        self._memory_flagship_lv = None
        self._memory_vanguard_lv = None
        if not self.change_flagship or self.config.GemsFarming_AllowHighFlagshipLevel:
            return False
        memory = self._fleet_memory.read(command, self.config.Fleet_FleetOrder)
        decision = decide_flagship_check(memory)
        if decision == 'skip':
            logger.attr('舰队记忆', f'{command} 旗舰等级合格，跳过初始换船检查')
            return False
        if decision == 'change':
            logger.info(f'[舰队记忆] {command} 旗舰已达到换船等级，出击前先换船')
        else:
            logger.info(f'[舰队记忆] {command} 无有效记录，进船坞实地核查旗舰等级（旧行为）')
        return True

    def _save_fleet_memory(self):
        """把最后已知的舰队等级写入舰队记忆。

        优先使用换船流程记下的精确等级（``_memory_*_lv``，确认完成后才记入），
        缺失的字段回退到 ``self.campaign.lv`` 的最后一次战斗 OCR；
        两者都没有时不落盘，保留旧记录。
        """
        if self._fleet_memory is None:
            return
        flagship_lv = self._memory_flagship_lv
        vanguard_lv = self._memory_vanguard_lv
        if flagship_lv is None or vanguard_lv is None:
            campaign = getattr(self, 'campaign', None)
            ocr_flagship, ocr_vanguard = lv_flagship_vanguard(
                getattr(campaign, 'lv', None))
            if flagship_lv is None:
                flagship_lv = ocr_flagship
            if vanguard_lv is None:
                vanguard_lv = ocr_vanguard
        self._fleet_memory.write(
            self.config.task.command,
            flagship_lv=flagship_lv, vanguard_lv=vanguard_lv,
            fleet_order=self.config.Fleet_FleetOrder)
