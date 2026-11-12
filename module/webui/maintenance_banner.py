"""维护等待横幅：状态读取与 DOM 更新脚本。

实例侧（`alas.py` 的 `_maintenance_wait`）在进入维护等待时把状态写进
`log/<name>.maintenance.json`，退出时删除。WebUI 是独立进程，读不到实例内存，
只能靠这份文件；拿到后由概览页的后台任务发一条 `run_js` 增量改 DOM，
不触发整页重渲染（守住懒渲染 / 骨架屏的性能约定）。

本模块刻意零 WebUI 依赖（不 import pywebio / app_dependencies）：
`app_dashboard` 的导入链带 WebUI 全家桶副作用，测试里导入会污染同进程的
pywebio 会话注册表。文案由调用方传入 labels，模块本身不查 i18n。
"""

import json
import os

BANNER_ID = "alas-maint-banner"

# 横幅元素上的选择器（与 build_html 生成的 class 对应）
_SEL_STATE = ".alas-maint-banner__state"
_SEL_DESC = ".alas-maint-banner__desc"
_SEL_SINCE = ".alas-maint-banner__since"
_SEL_NEXT = ".alas-maint-banner__next"


def state_path(alas_name: str) -> str:
    """维护状态文件路径（与 alas.py 写入侧保持一致）。"""
    return os.path.join("log", f"{alas_name}.maintenance.json")


def read_state(alas_name: str):
    """读取维护状态；文件缺失或内容损坏返回 None。"""
    try:
        with open(state_path(alas_name), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def normalize(state, alive: bool):
    """把原始状态收敛成「该不该点亮横幅」的判定结果。

    - 没在等待（waiting 为假）→ None
    - 实例已停止 → None（进程被杀不会清状态文件，避免横幅永远挂着）
    """
    if not state or not state.get("waiting"):
        return None
    if not alive:
        return None
    return state


def build_html(labels: dict) -> str:
    """横幅静态外壳（默认隐藏），文案由 build_js 填入。"""
    return (
        f'<div id="{BANNER_ID}" class="alas-maint-banner" style="display:none">'
        '<span class="alas-maint-banner__dot"></span>'
        '<span class="alas-maint-banner__body">'
        '<b class="alas-maint-banner__state"></b>'
        '<span class="alas-maint-banner__desc"></span>'
        "</span>"
        '<span class="alas-maint-banner__meta">'
        f'<span>{labels["elapsed"]} '
        '<b class="alas-maint-banner__since">--</b></span>'
        f'<span>{labels["next"]} '
        '<b class="alas-maint-banner__next">--</b></span>'
        "</span>"
        "</div>"
    )


def build_js(state, labels: dict) -> str:
    """生成横幅更新脚本：state 为 None 时隐藏，否则显示并每秒走表。

    「已等待」按 since 累计，「下次查询」按 last_poll + poll_seconds 倒计时。
    两个数字都在浏览器端每秒刷新，服务端不必高频轮询。
    """
    if not state:
        return (
            f"(function(){{var el=document.getElementById('{BANNER_ID}');"
            "if(!el)return;el.style.display='none';"
            "if(window.__alasMaintTimer){clearInterval(window.__alasMaintTimer);"
            "window.__alasMaintTimer=null;}})();"
        )
    cfg = {
        "state": labels["state"],
        "desc": labels["desc"],
        "since": float(state.get("since") or 0) * 1000,
        "next": (
            float(state.get("last_poll") or 0)
            + float(state.get("poll_seconds") or 60)
        )
        * 1000,
    }
    return (
        f"(function(){{var el=document.getElementById('{BANNER_ID}');"
        "if(!el)return;var cfg=" + json.dumps(cfg, ensure_ascii=False) + ";"
        "el.style.display='';"
        f"el.querySelector('{_SEL_STATE}').textContent=cfg.state;"
        f"el.querySelector('{_SEL_DESC}').textContent=cfg.desc;"
        "function pad(n){return n<10?'0'+n:''+n;}"
        "function fmt(ms){if(ms<0)ms=0;var s=Math.floor(ms/1000);"
        "var h=Math.floor(s/3600),m=Math.floor((s%3600)/60),ss=s%60;"
        "return (h?h+':':'')+pad(m)+':'+pad(ss);}"
        "function tick(){var now=Date.now();"
        f"el.querySelector('{_SEL_SINCE}').textContent=fmt(now-cfg.since);"
        "var left=cfg.next-now;"
        f"el.querySelector('{_SEL_NEXT}').textContent=left>0?fmt(left):'...';}}"
        "if(window.__alasMaintTimer)clearInterval(window.__alasMaintTimer);"
        "tick();window.__alasMaintTimer=setInterval(tick,1000);})();"
    )
