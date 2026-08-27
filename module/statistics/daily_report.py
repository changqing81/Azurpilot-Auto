import json
<<<<<<< HEAD
=======

from pathlib import Path
>>>>>>> b0f1ee2fb (fix(statistics): 修复日报引用不存在的全局配置属性、重复导入与推送卡片乱码键名)

from pathlib import Path

STATE_FILE = Path("./cache/report_state.json")
def load_state():
    if STATE_FILE.exists():
        return json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

    return {}
def save_state(data):
    STATE_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_FILE.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
def percent(current, delta):
    base = current - delta

    if base <= 0:
        return 0

    return delta / base * 100
def fmt(current, delta):
    return (
        f"{current} "
        f"({delta:+}, {percent(current, delta):+.2f}%)"
    )
from datetime import datetime

from module.statistics.cl1_database import db as cl1_db
def get_today_report(instance):

    month = datetime.now().strftime("%Y-%m")



    data = cl1_db.get_stats(

        instance,

        month,

    )

    ap_list = data.get(

        "ap_snapshots",

        []

    )

    today = datetime.now().date()



    ap_today = [

        x

        for x in ap_list

        if datetime.fromisoformat(

            x["ts"]

        ).date() == today

    ]
    if len(ap_today) < 2:
        return None
    first = ap_today[0]
    last = ap_today[-1]
    sea_now = last.get(
        "distance",
        0,
    )

    sea_delta = (
        sea_now
        - first.get(
            "distance",
            0,
        )
    )
    ap_now = last["ap"]

    ap_delta = (
        ap_now
        - first["ap"]
    )
    asset_now = last.get(
        "asset",
        0,
    )

    asset_delta = (
        asset_now
        - first.get(
            "asset",
            0,
        )
    )
    coin_list = data.get(
        "coins_snapshots",
        []
    )
    coin_today = [
        x
        for x in coin_list
        if datetime.fromisoformat(x["ts"]).date() == today
    ]

    if coin_today:
        first_coin = coin_today[0]
        last_coin = coin_today[-1]
        y_now = last_coin.get("yellow_coins", 0)
        y_delta = y_now - first_coin.get("yellow_coins", 0)
        p_now = last_coin.get("purple_coins", 0)
        p_delta = p_now - first_coin.get("purple_coins", 0)
    else:
        y_now = y_delta = p_now = p_delta = 0
    return [
        f"⚡ 行动力 {fmt(ap_now, ap_delta)}",
        f"🟡 黄币资源 {fmt(y_now, y_delta)}",
        f"🟣 紫币资源 {fmt(p_now, p_delta)}",
        f"🌊 海里 {fmt(sea_now, sea_delta)}",
        f"🏦 仓库资产 {fmt(round(asset_now), round(asset_delta))}",
        "",
        "数值说明：当前值 (本日变化, 变化百分比)  |  海里为总海里数 (本日增加)",
    ]
def should_send(config):
    report_time = getattr(
        config,
        "Report_TriggerTime",
        "23:50",
    )
    hour, minute = map(
        int,
        str(report_time).split(":")
    )
    now = datetime.now()
    return (
        now.hour == hour
        and now.minute >= minute
    )
def try_send_daily_report(
    instance,
    config,
):
    if not getattr(
        config,
        "Report_Enable",
        False,
    ):
        return
    if not should_send(config):
        return
    today = datetime.now().strftime(
        "%Y-%m-%d"
    )
    state = load_state()

    if state.get(
        "daily"
    ) == today:
        return
    from module.statistics.report_sender import (
        get_wecom_webhook,
        send_text_message,
    )
    # 日报使用 Report 组的 OnePush 配置（企业微信机器人渠道）
    webhook = get_wecom_webhook(
        getattr(config, "Report_OnePushConfig", "provider: null")
    )
    lines = get_today_report(
        instance
    )

    if not lines:
        return
    ok = send_text_message(
        webhook,
        f"📊 大世界日报 {today}",
        lines,
    )
    if ok:
        state["daily"] = today
        save_state(state)
