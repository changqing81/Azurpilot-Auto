"""后宅每日任务喂食的纯逻辑。

把「今天是否已经喂过」「该选哪一档食物」这类不依赖设备与配置的判断单独放在这里，
方便单元测试直接覆盖，不需要启动模拟器。

每日任务喂食的业务约束：
- 一天只喂一次，喂完记录，直到下一次服务器刷新才允许再喂；
- 只喂最低等的食物，仅为了完成游戏的每日任务，不追求喂满；
- 低等级食物缺货时逐级向上回退（1000 -> 2000 -> ... -> 20000），
  每天独立按库存重新选择，不沿用前一天的结果。
"""

from datetime import datetime

# 食物等级，从低到高排列。下标即食物按钮在喂食界面里的顺序，
# 每日任务喂食按这个顺序取第一个有库存的等级。
FOOD_FEED_AMOUNT = [1000, 2000, 3000, 5000, 10000, 20000]


def select_food_index(amounts, fill):
    """
    按「最低等优先」挑选每日任务要投喂的食物。

    从 1000 开始逐级向上回退，取第一个「有库存且剩余饱食度装得下」的等级。
    只要求装得下 1 份即可完成任务，不追求喂满。

    每次调用都基于当前库存与饱食度重新选择，因此今天 1000 缺货喂了 2000，
    明天 1000 补货后仍然会优先选中 1000，不会沿用前一天的结果。

    Args:
        amounts (list[int]): 各等级食物的库存数量，顺序与 FOOD_FEED_AMOUNT 一致。
        fill (int): 剩余可投喂量，至少能装下一份所选食物才能投喂。

    Returns:
        int | None: 选中食物的下标；没有可投喂的食物时返回 None。
    """
    for index, feed in enumerate(FOOD_FEED_AMOUNT):
        if index < len(amounts) and amounts[index] > 0 and fill >= feed:
            return index
    return None


def is_done_today(record, last_update):
    """
    判断今日的每日任务喂食是否已经完成。

    记录值早于「当前的上一次服务器刷新时间」，说明已经跨天，允许再次喂食。
    这样跨天判定完全复用 ALAS 现有的服务器刷新机制，不需要自己比较日期。

    Args:
        record (datetime): 上次喂食完成时写入的记录（当时的上一次服务器刷新时间）。
        last_update (datetime): 当前的上一次服务器刷新时间。

    Returns:
        bool: 今日已完成返回 True。

    记录类型异常（例如被手动改成字符串）时按「未完成」处理：
    宁可多喂一次，也不因为脏数据漏掉每日任务。
    """
    if not isinstance(record, datetime):
        return False
    return record >= last_update
