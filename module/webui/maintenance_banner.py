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
import time

BANNER_ID = "alas-maint-banner"

# 横幅元素上的选择器（与 build_html 生成的 class 对应）
_SEL_STATE = ".alas-maint-banner__state"
_SEL_SERVER = ".alas-maint-banner__server"
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


# 「服务器已开服」绿色横幅的停留时长（秒）。与 alas.py 的
# MAINTENANCE_OPENED_TTL 保持一致：实例写完 opened 状态就不再管，
# 由 WebUI 按这个 TTL 自行隐藏。
OPENED_TTL = 8.0


def normalize(state, alive: bool, now: float = None):
    """把原始状态收敛成「该不该点亮横幅」的判定结果。

    - 等待中（waiting）→ 原样返回，横幅显示维护态
    - 已开服（opened）→ 仅在 OPENED_TTL 内返回，超时视为 None
    - 实例已停止 → None（进程被杀不会清状态文件，避免横幅永远挂着）

    Args:
        state: 状态文件内容（可能为 None）。
        alive: 实例进程是否存活。
        now: 当前时间戳，默认 time.time()（测试可注入）。
    """
    if not state:
        return None
    if not alive:
        return None
    if state.get("opened"):
        finished = float(state.get("finished_at") or 0)
        current = time.time() if now is None else now
        return state if current - finished <= OPENED_TTL else None
    if not state.get("waiting"):
        return None
    return state


def _server_text(server) -> str:
    """把配置里的服务器名整理成可显示文本。

    需要处理的"空值"形态（`Emulator_ServerName` 的默认值就是 'disabled'）：
    - None / 空串 / 纯空白 → 空串（不显示）
    - 'disabled' → 空串（表示"不检查服务器状态"，不是真的服务器名）
    - 'auto' → 空串（还没解析出具体服务器，显示出来反而误导）
    其余原样返回，由调用方决定怎么拼接。
    """
    if server is None:
        return ""
    text = str(server).strip()
    if not text or text.lower() in ("disabled", "auto", "none"):
        return ""
    return text


def build_html(labels: dict) -> str:
    """横幅静态外壳（默认隐藏），文案由 build_js 填入。"""
    return (
        f'<div id="{BANNER_ID}" class="alas-maint-banner" style="display:none">'
        '<span class="alas-maint-banner__dot"></span>'
        '<span class="alas-maint-banner__body">'
        '<b class="alas-maint-banner__state"></b>'
        # 服务器名单独一个元素：为空时由 JS 直接清空并隐藏，不影响整行排版
        '<span class="alas-maint-banner__server"></span>'
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
    opened = bool(state.get("opened"))
    # B 部分：按 emulator_closed 选描述措辞（关闭中 vs 保持运行）。
    # 开服态优先，它有自己的描述文案。
    if opened:
        desc = labels["desc_open"]
    elif state.get("emulator_closed"):
        desc = labels["desc"]
    else:
        desc = labels["desc_running"]
    # 服务器名：空配置（默认 disabled）时整段不显示，避免出现「服务器维护中 ·  · 」
    server = _server_text(state.get("server"))
    cfg = {
        "state": labels["state_open"] if opened else labels["state"],
        "server": server,
        "desc": desc,
        "opened": opened,
        "since": float(state.get("since") or 0) * 1000,
        "next": (
            float(state.get("last_poll") or 0)
            + float(state.get("poll_seconds") or 60)
        )
        * 1000,
        # opened 态不显示倒计时（已经在启动模拟器了），只显示耗时
        "open_ttl": int(OPENED_TTL * 1000),
    }
    return (
        f"(function(){{var el=document.getElementById('{BANNER_ID}');"
        "if(!el)return;var cfg=" + json.dumps(cfg, ensure_ascii=False) + ";"
        "el.style.display='';"
        # opened 态切绿色配色（在容器上加 class，样式见 dark-alas.css / alas.css）
        "el.classList.toggle('is-opened',cfg.opened);"
        f"el.querySelector('{_SEL_STATE}').textContent=cfg.state;"
        # 服务器名：有值才显示，用「· 名字 · 」与前后文案自然衔接
        f"var srv=el.querySelector('{_SEL_SERVER}');"
        "if(srv){if(cfg.server){srv.textContent='\u00b7 '+cfg.server+' \u00b7';"
        "srv.style.display='';}else{srv.textContent='';srv.style.display='none';}}"
        f"el.querySelector('{_SEL_DESC}').textContent=cfg.desc;"
        "function pad(n){return n<10?'0'+n:''+n;}"
        "function fmt(ms){if(ms<0)ms=0;var s=Math.floor(ms/1000);"
        "var h=Math.floor(s/3600),m=Math.floor((s%3600)/60),ss=s%60;"
        "return (h?h+':':'')+pad(m)+':'+pad(ss);}"
        "var born=Date.now();"
        "function tick(){var now=Date.now();"
        f"el.querySelector('{_SEL_SINCE}').textContent=fmt(now-cfg.since);"
        "if(cfg.opened){"
        # 开服态：右边不再显示倒计时（已经在拉起模拟器），改显「本次维护」总时长，
        # 停留 open_ttl 后自动收起横幅
        f"el.querySelector('{_SEL_NEXT}').textContent=fmt(now-cfg.since);"
        "if(now-born>cfg.open_ttl){el.style.display='none';"
        "if(window.__alasMaintTimer){clearInterval(window.__alasMaintTimer);"
        "window.__alasMaintTimer=null;}return;}"
        "}else{var left=cfg.next-now;"
        f"el.querySelector('{_SEL_NEXT}').textContent=left>0?fmt(left):'...';}}}}"
        "if(window.__alasMaintTimer)clearInterval(window.__alasMaintTimer);"
        "tick();window.__alasMaintTimer=setInterval(tick,1000);})();"
    )
