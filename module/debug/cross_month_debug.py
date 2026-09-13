"""跨月每日预演工具。

把今天当成大世界跨月时刻：跳过时间检查与等待重置，真机执行跨月后续阶段，
用于月末实测前的流程验证：

    大世界初始化 → 每日+（可选）→ 塞壬要塞 → 隐秘海域 → 深渊坐标 → 耄耋相接 → 推送

注意：
- 预演会真实操作游戏：可能消耗行动力与仓库道具，并按推送开关发送真实通知
- 预演不修改任务调度（不写 NextRun、不调用 task_stop），真实的跨月任务
  仍会按原计划在月末执行
- 运行前请先停止该实例的 Alas 调度器，避免两个进程抢占同一台模拟器

用法：
    .venv/Scripts/python.exe -m module.debug.cross_month_debug --instance alas
    .venv/Scripts/python.exe -m module.debug.cross_month_debug --instance alas --with-daily
    .venv/Scripts/python.exe -m module.debug.cross_month_debug --instance alas --dry
"""

import argparse
import json
import sys


def _read_flag(section, key):
    """读取布尔配置，兼容 WebUI checkbox 历史值 [] / [True]。"""
    value = section.get(key, False)
    if isinstance(value, list):
        return any(bool(item) for item in value)
    return value is True


def show_plan(instance, with_daily, dry):
    """只读检查配置并打印执行计划，返回 True 表示配置可用。"""
    from module.logger import logger

    try:
        with open(f'./config/{instance}.json', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f'找不到配置文件 ./config/{instance}.json，请确认实例名（见 config 目录）')
        return False

    task = data.get('OpsiCrossMonth', {})
    section = task.get('OpsiCrossMonth', {})
    if section:
        cleanup_enable = _read_flag(section, 'ActionPointCleanupEnable')
        push_enable = _read_flag(section, 'PushNotify')
        try:
            preserve = int(section.get('ActionPointPreserve') or 50)
        except (TypeError, ValueError):
            preserve = 50
    else:
        # 配置文件尚未写入新参数组（调度器未用新代码重启过），
        # 运行时 config_update 会按 argument.yaml 默认值补全
        logger.warning('配置文件中尚无跨月参数组，重启 Alas 后将按默认值生成：清理=开，保留值=50，推送=开')
        cleanup_enable, preserve, push_enable = True, 50, True
    next_run = task.get('Scheduler', {}).get('NextRun')

    logger.hr('跨月每日预演 - 配置检查', level=1)
    logger.info(f'实例: {instance}')
    logger.info(f'OpsiCrossMonth 当前 NextRun: {next_run}（预演不会修改它，真实跨月仍按此时间执行）')
    logger.info(
        '执行计划: 大世界初始化 → 每日+（'
        f'{"执行" if with_daily else "跳过"}）→ 塞壬要塞 → 隐秘海域 → 深渊坐标 → 耄耋相接 → 推送'
    )
    logger.info(f'清理开关: {cleanup_enable}（预演强制执行清理）；保留值: {preserve}；推送开关: {push_enable}')
    if not push_enable:
        logger.warning('推送开关当前为关，预演过程中不会收到推送通知')
    return True


def main():
    parser = argparse.ArgumentParser(
        prog='python -m module.debug.cross_month_debug',
        description='跨月每日预演：把今天当成大世界跨月时刻，跳过等待直接执行塞壬要塞检查与行动力清理',
    )
    parser.add_argument('--instance', default='alas', help='实例名（config 目录下的 json 文件名），默认 alas')
    parser.add_argument('--with-daily', action='store_true',
                        help='连同大世界每日+一起执行（默认跳过，只测塞壬要塞及后续清理）')
    parser.add_argument('--dry', action='store_true', help='只检查配置并打印执行计划，不连接模拟器')
    parser.add_argument('--yes', action='store_true', help='跳过执行前的确认提示')
    args = parser.parse_args()

    if not show_plan(args.instance, args.with_daily, args.dry):
        sys.exit(1)
    if args.dry:
        from module.logger import logger
        logger.hr('dry 模式结束：未连接模拟器、未做任何修改', level=1)
        return

    if not args.yes:
        try:
            input('预演将真实操作游戏（消耗行动力/仓库道具、发送推送）。确认请回车，取消请 Ctrl+C：')
        except EOFError:
            print('检测到非交互环境，如需直接执行请加 --yes')
            sys.exit(1)

    from module.config.config import AzurLaneConfig
    from module.logger import logger
    from module.os.operation_siren import OperationSiren

    logger.info('初始化配置与设备（请确保已停止该实例的 Alas 调度器）...')
    config = AzurLaneConfig(config_name=args.instance, task='OpsiCrossMonth')
    az = OperationSiren(config, device=None)
    try:
        az.os_cross_month_debug(skip_daily=not args.with_daily)
    except KeyboardInterrupt:
        logger.warning('预演被手动中止')
        sys.exit(130)


if __name__ == '__main__':
    main()
