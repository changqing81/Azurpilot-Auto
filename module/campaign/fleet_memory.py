"""舰队等级记忆模块。

跨进程持久化低耗类任务（GemsFarming / ThreeOilLowCost / Ambush11）的舰队等级，
使任务被其他任务打断或调度器进程重启后恢复时，可以依据上次退出的等级直接决策，
不再盲目进船坞执行一遍初始换船检查（完整换船 UI 流程，耗时 1~4 分钟）。

等级数据来源：
- 战斗后 ``Level.lv_get()`` 的 6 位置 OCR（位置 0 为旗舰，与 ``lv32_triggered`` 口径一致）
- 换船流程中选中的 ``Ship.level``（精确值，确认完成后才记入）

除等级外，还持久化一个「换船换装事务」标记 ``change_pending``：
换船/换装开始前（卸装之前）置 True，流程正常走完后置 False。
若进程被强杀或用户手动打断，标记会留在磁盘上，下次启动即强制重做
换船与换装，避免以空装备状态出击。
事务中导出的装备码同时持久化为 ``change_code``，使重启后即使目标船型
没有配置装备码，也能复用中断前导出的方案，而不会在空装备上导出空码。

数据持久化在 ``log/<config_name>.fleet_memory.json``，按任务 command 分 key::

    {
        "ThreeOilLowCost": {
            "flagship_lv": 5,
            "vanguard_lv": 3,
            "change_pending": false,
            "change_code": "MkpKQS9JUkUvRzNELzQ4TC80OElcMA==",
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
             'change'（上次换船/换装事务未完成，或旗舰已达到换船等级，需先换船换装）、
             'check'（无有效记忆，按旧流程进船坞实地核查）。
    """
    if not isinstance(memory, dict):
        return 'check'
    # 未收尾的换装事务优先级最高：装备可能已被卸下，必须先重做换装
    if memory.get('change_pending') is True:
        return 'change'
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

    def is_change_pending(self, command: str, fleet_order: str) -> bool:
        """读取「换船换装事务未完成」标记。

        Args:
            command (str): 任务名，如 `ThreeOilLowCost`。
            fleet_order (str): 当前的 ``Fleet_FleetOrder`` 配置。

        Returns:
            bool: 上一次换船/换装事务未正常收尾时返回 True。
        """
        entry = self.read(command, fleet_order)
        return bool(entry and entry.get('change_pending') is True)

    def set_change_pending(self, command: str, pending: bool, fleet_order: str = '', code=None):
        """写入/清除「换船换装事务未完成」标记（原子写）。

        标记在卸装之前落盘，进程被强杀或任务被用户手动打断后仍会保留，
        下次启动据此强制重做换船与换装，避免以空装备出击。
        与 ``write()`` 不同，本方法即使没有任何等级数据也会落盘——
        否则无法清除上一轮遗留的 True 标记。

        Args:
            command (str): 任务名。
            pending (bool): True 表示事务进行中，False 表示正常收尾。
            fleet_order (str): 当前的 ``Fleet_FleetOrder`` 配置。
            code (Optional[str]): 事务中导出的装备码。仅在 ``pending=True``
                且为合法字符串时写入（覆盖旧值）；``pending=True`` 且为 None
                时**保留**已有值，因为 ``_begin_change_transaction()`` 会在
                导出之前先落标记。``pending=False`` 时一律清除。
        """
        data = self._load()
        entry = data.get(command)
        if not isinstance(entry, dict):
            entry = {}
        entry['change_pending'] = bool(pending)
        if pending:
            if isinstance(code, str) and code:
                entry['change_code'] = code
        else:
            entry.pop('change_code', None)
        entry['fleet_order'] = fleet_order
        entry['record'] = datetime.now().replace(microsecond=0).isoformat()
        data[command] = entry
        self._save(data)
        logger.attr(
            '舰队记忆',
            f"{command}: 换装事务{'进行中（未完成）' if pending else '已完成'}")

    def get_change_code(self, command: str, fleet_order: str) -> Optional[str]:
        """读取事务中持久化的装备码（用于中断重启后复用）。

        Returns:
            Optional[str]: 合法装备码字符串；无记录或字段非法时返回 None。
        """
        entry = self.read(command, fleet_order)
        if not isinstance(entry, dict):
            return None
        code = entry.get('change_code')
        if isinstance(code, str) and code.strip():
            return code.strip()
        return None

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
        _change_pending (bool): 本次进程内是否处于换船/换装事务中。
        _previous_disable_task_switch (bool): 事务开始前的 ``_disable_task_switch`` 取值。
    """

    _fleet_memory = None
    _memory_flagship_lv = None
    _memory_vanguard_lv = None
    _change_pending = False
    _previous_disable_task_switch = False

    def _init_fleet_memory(self):
        """读取舰队记忆，决定首轮是否强制触发换船块（初始检查）。

        若上次换船/换装事务未正常收尾（进程被强杀或用户手动打断），
        无条件返回 True：本次必须先完成换船与换装，才允许出击。

        Returns:
            bool: True 表示需要执行初始检查（含换装事务重做）。
        """
        command = self.config.task.command
        self._fleet_memory = FleetMemory(self.config.config_name)
        self._memory_flagship_lv = None
        self._memory_vanguard_lv = None
        self._change_pending = False
        # 未收尾的换装事务优先级最高，先于「关闭换船 / 允许高等级旗舰」判断
        if self._fleet_memory.is_change_pending(command, self.config.Fleet_FleetOrder):
            logger.warning(
                f'[舰队记忆] {command} 上次换船/换装未完成，'
                f'本次出击前强制重做换船与换装')
            self._change_pending = True
            resume_code = self._fleet_memory.get_change_code(
                command, self.config.Fleet_FleetOrder)
            if resume_code and hasattr(self, 'resume_code'):
                # 内存中的 last_code 已随进程消失，用持久化的码兜住，
                # 避免在已卸空的装备上重新导出空码覆盖历史方案。
                self.resume_code = resume_code
                logger.info(
                    f'[舰队记忆] 已恢复中断前导出的装备码（{len(resume_code)} 字符）')
            return True
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

    def _begin_change_transaction(self):
        """进入换船/换装事务。

        两件事必须按此顺序完成：

        1. 先把「未完成」标记落盘——必须在卸装之前写，这样即使进程被强杀，
           下次启动也能据此重做换船与换装，不会以空装备出击；
        2. 临时禁止任务切换（``config._disable_task_switch``），
           换船换装期间不被其他任务打断。用户手动打断（stop_event /
           进程被强杀）不受此开关限制，会被判定为换装失败。
        """
        self._change_pending = True
        if self._fleet_memory is not None:
            try:
                self._fleet_memory.set_change_pending(
                    self.config.task.command, True,
                    fleet_order=self.config.Fleet_FleetOrder)
            except Exception as e:
                logger.warning(f'[换装事务] 写入未完成标记失败（不影响本次换装）: {e}')
        self._previous_disable_task_switch = getattr(
            self.config, '_disable_task_switch', False)
        self.config._disable_task_switch = True

    def _on_equip_code_exported(self, code):
        """装备码导出成功后的钩子：把方案持久化，供中断重启后复用。

        覆盖 ``EquipmentCodeHandler._on_equip_code_exported()``。
        装备码本身也已由 ``set_code()`` 写进任务配置，这里额外按任务持久化，
        是为了在「换船后才被打断」的场景下，重启时目标船型可能没有配置码，
        仍能拿到中断前导出的方案。
        """
        if self._fleet_memory is None:
            return
        try:
            self._fleet_memory.set_change_pending(
                self.config.task.command, True,
                fleet_order=self.config.Fleet_FleetOrder, code=code)
        except Exception as e:
            logger.warning(f'[换装事务] 持久化装备码失败（不影响本次换装）: {e}')

    def _clear_change_pending(self):
        """换装事务正常收尾后清除落盘标记（含持久化的装备码）。"""
        self._change_pending = False
        if hasattr(self, 'resume_code'):
            self.resume_code = None
        if self._fleet_memory is not None:
            try:
                self._fleet_memory.set_change_pending(
                    self.config.task.command, False,
                    fleet_order=self.config.Fleet_FleetOrder)
            except Exception as e:
                logger.warning(f'[换装事务] 清除未完成标记失败: {e}')

    def _restore_task_switch(self):
        """把任务切换开关还原到事务开始前的取值。"""
        self.config._disable_task_switch = getattr(
            self, '_previous_disable_task_switch', False)

    def _change_transaction(self, func):
        """在换装事务保护下执行换船/换装流程。

        流程正常走完（无论换船是否成功）即清除标记——换装流程末尾总会
        重新装备，装备状态是自洽的；一旦抛异常（用户手动打断 / 进程被强杀 /
        装备码应用失败），标记保留，下次启动 ``_init_fleet_memory()``
        会强制重做换船与换装。

        Args:
            func (Callable[[], bool]): 无参回调，执行换船与换装，返回是否成功。

        Returns:
            bool: ``func`` 的返回值。
        """
        self._begin_change_transaction()
        try:
            result = func()
        except BaseException:
            logger.warning('[换装事务] 换船/换装流程被中断，保留未完成标记，下次启动将重做')
            raise
        else:
            self._clear_change_pending()
            return result
        finally:
            self._restore_task_switch()

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
