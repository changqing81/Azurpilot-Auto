"""GUI 单次工具的完成事件，不向自动调度或循环扫描注入推送。"""

import json
import time
from dataclasses import dataclass
from functools import lru_cache

import inflection

from deploy.atomic import atomic_read_text
from module.notify.complete import notify_custom_complete, warn_notify_failure


@dataclass(frozen=True)
class ToolCompletion:
    """跨进程传递最终结果，避免任务退出时丢失后台网络请求。"""

    instance: str
    task: str
    success: bool
    elapsed: float
    reason: str = ''
    summary: str = ''


@lru_cache(maxsize=1)
def _task_names() -> dict:
    """沿用已有中文工具名称，通知不依赖 WebUI 会话语言状态。"""
    return json.loads(atomic_read_text('./module/config/i18n/zh-CN.json')).get('Task', {})


def fleet_summary(result: dict) -> str:
    """将本次完整扫描结果转为编队、舰名与数量摘要。"""
    lines = []
    count = 0
    for key, label in (('vanguard', '前排'), ('main', '主力'), ('submarine', '潜艇')):
        fleets = result.get(key, {})
        for fleet, ships in fleets.items():
            names = [
                str(ship.get('name') or '未知舰船') if isinstance(ship, dict) else str(ship)
                for ship in ships
            ]
            if names:
                count += len(names)
                lines.append(f'舰队 {fleet} {label}（{len(names)} 艘）：' + '、'.join(names))
    return f'共扫描到 {count} 艘舰船' + ('\n' + '\n'.join(lines) if lines else '')


def run_internal_tool(config_name: str, task: str, result_queue):
    """保持单次工具的返回值及异常语义，额外提交一次完成事件。"""
    started = time.monotonic()
    runner = None
    success = False
    reason = '任务未正常结束'
    try:
        from alas import AzurLaneAutoScript

        runner = AzurLaneAutoScript(config_name=config_name)
        result = runner.run(inflection.underscore(task), skip_first_screenshot=True)
        success = result is True
        if not success:
            reason = getattr(runner, '_last_task_error', None) or '任务返回失败，详见实例日志'
            if result == 'recoverable':
                reason = f'{reason}（需要恢复，本次单次执行未完成）'
        return result
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        reason = f'{type(exc).__name__}: {exc}'[:500]
        raise
    finally:
        # 这里只借用错误推送通道提交完成事件；任何通知故障都不得覆盖业务异常。
        try:
            summary = ''
            if success and task == 'FleetScan':
                try:
                    # run() 成功意味着保存结果及船坞恢复均已结束，取本次数据快照。
                    summary = fleet_summary(runner.config.cross_get('FleetInfo.FleetInfo.Result', default={}))
                except Exception:
                    summary = '扫描已完成，摘要读取失败，请在舰队信息页面查看结果'
            result_queue.put_nowait(ToolCompletion(
                config_name, task, success, time.monotonic() - started,
                '' if success else reason, summary,
            ))
        except Exception:
            warn_notify_failure()


def dispatch_tool_completion(event: ToolCompletion) -> None:
    """在常驻 WebUI 中异步借用错误推送通道，扫描只产生一条专用通知。"""
    try:
        try:
            name = _task_names().get(event.task, {}).get('name', event.task)
        except Exception:
            name = event.task
        status = '成功' if event.success else '失败'
        title = (f'[舰队扫描完成] {status}' if event.task == 'FleetScan'
                 else f'[AP 内部工具完成] {name}')
        content = (f'实例：{event.instance}\n工具名称：{name}（{event.task}）'
                   f'\n耗时：{event.elapsed:.1f} 秒\n执行状态：{status}')
        if event.success and event.summary:
            content += f'\n{event.summary}'
        elif not event.success:
            content += f'\n失败原因：{event.reason}'
        notify_custom_complete(event.instance, title, content)
    except Exception:
        warn_notify_failure()
