"""舰队管理 WebUI 页面。"""

import time
from html import escape

# 扫描按钮「扫描中」反馈的时间窗：alas 在独立进程，老前端拿不到当前任务名。
SCAN_WINDOW_SECONDS = 600

import json

from module.webui.app_dependencies import (
    BinarySwitchButton,
    deep_get,
    put_button,
    put_buttons,
    put_html,
    put_input,
    put_scope,
    put_select,
    put_text,
    t,
    toast,
    use_scope,
)
from module.webui.app_types import WebUIMixinBase
from module.webui.instance_action import spawn_instance_action
from module.webui.utils import scan_window_active

# 换船规则桥 pin：轮换规则编辑器的勾选状态由前端 JS 汇总成 JSON，
# 经这个隐藏输入回传服务端保存（同统计页 st_bridge_pin 的通道模式）。
FM_BRIDGE_PIN = "fm_bridge_pin"

# 舰队页签定义：(面板 id, i18n 键)。顺序即展示顺序。
FM_TABS = (
    ("info", "Task.FleetInfo.name"),
    ("rotation", "Gui.FleetRotation.TabTitle"),
    ("training", "Gui.FleetTraining.TabTitle"),
    ("equipment", "Gui.FleetEquipment.TabTitle"),
)

# 换船规则编辑器使用的舰队编号与舰位编号。
FM_NUMBERS = (1, 2, 3, 4, 5, 6)


# ----------------------------------------------------------------------
# 模块级渲染函数：页签条 / 各面板
# ----------------------------------------------------------------------




def _fm_bridge_script() -> str:
    """页签切换 + 轮换规则编辑器的前端脚本。

    页签：切 CSS 类 + 占位骨架已在服务端；点已加载的直接切，未加载的经桥
    pin 通知服务端渲染（渲染完成由 _fm_render_panel 的 run_js 点亮）。
    规则编辑器：勾选变化只改本地 state 与勾选样式；「保存」把整份规则
    JSON 经桥 pin 发给服务端（payload.action=saveRotation）。
    """
    tabs_json = json.dumps([panel for panel, _key in FM_TABS])
    return (
        "<script>"
        "(function(){"
        "var TABS=" + tabs_json + ";"
        "var bridge=function(){return document.querySelector('input[name=" + json.dumps(FM_BRIDGE_PIN) + "]')};"
        "var send=function(obj){var b=bridge();if(!b)return;"
        "b.value=JSON.stringify(obj);b.dispatchEvent(new Event('change',{bubbles:true}));};"
        "页签点击" + ""
        "var bar=document.querySelector('.fm-tabs');if(!bar)return;"
        "bar.addEventListener('click',function(ev){"
        "var btn=ev.target.closest('.fm-tab');if(!btn)return;"
        "var panel=btn.dataset.panel;"
        "document.querySelectorAll('.fm-tab').forEach(function(b){"
        "var on=b===btn;b.classList.toggle('fm-on',on);"
        "b.setAttribute('aria-selected',on?'true':'false');});"
        "document.querySelectorAll('.fm-page').forEach(function(p){"
        "p.classList.toggle('fm-on',p.id==='fm-page-'+panel);});"
        "var target=document.getElementById('fm-page-'+panel);"
        "if(target&&!target.dataset.loaded){send({action:'tab',panel:panel});}"
        "try{localStorage.setItem('__alasFmPanel',panel);}catch(e){}"
        "});"
        "// 规则编辑器：勾选即改样式；保存时收集整份规则" + ""
        "var state={};"
        "document.addEventListener('change',function(ev){"
        "var el=ev.target;"
        "if(!el.dataset||!el.dataset.fleet)return;"
        "var f=el.dataset.fleet,kind=el.dataset.kind,num=parseInt(el.dataset.num,10);"
        "var s=state[f]=state[f]||{slots:[],sources:[],favourite:false};"
        "if(kind==='favourite'){s.favourite=el.checked;}"
        "else{var arr=s[kind];var i=arr.indexOf(num);"
        "if(el.checked&&i<0)arr.push(num);if(!el.checked&&i>=0)arr.splice(i,1);"
        "arr.sort(function(a,b){return a-b;});}"
        "if(kind==='slots'){"
        "var labels=el.closest('.fm-checks').querySelectorAll('.fm-ck');"
        "labels.forEach(function(lb){var cb=lb.querySelector('input');"
        "var n=parseInt(cb.dataset.num,10);"
        "var tag=lb.querySelector('.fm-ck-tag');if(!tag)return;"
        "var on=s.slots.indexOf(n)>=0;"
        "tag.textContent=on?" + json.dumps(t("Gui.FleetRotation.Swap")) + ":" + json.dumps(t("Gui.FleetRotation.Keep")) + ";" +
        "tag.classList.toggle('keep',!on);}})}"
        "else if(kind==='sources'){"
        "el.closest('.fm-pills').querySelectorAll('.fm-pill').forEach(function(p){"
        "var cb=p.querySelector('input');"
        "p.classList.toggle('fm-on',cb.checked);});}"
        "});"
        "document.addEventListener('click',function(ev){"
        "var saveBtn=ev.target.closest('#fm-save-rules');"
        "var resetBtn=ev.target.closest('#fm-reset-state');"
        "if(saveBtn){"
        "document.querySelectorAll('.fm-panel[data-fleet]').forEach(function(card){"
        "var f=card.dataset.fleet;"
        "if(!state[f])return;"
        "var inputs=card.querySelectorAll('input[data-kind]');"
        "var any=Array.prototype.some.call(inputs,function(cb){return cb.checked;});"
        "if(any)state[f]=state[f];});"
        "send({action:'saveRotation',rules:state});}"
        "if(resetBtn){send({action:'saveRotation',rules:state,resetState:true});}"
        "});"
        "})();"
        "</script>"
    )


def _fm_tabs_html() -> str:
    """药丸式页签条。交互由 fm_bridge JS 接管（切页签 + 懒加载）。"""
    buttons = []
    for i, (panel, key) in enumerate(FM_TABS):
        on = " fm-on" if i == 0 else ""
        buttons.append(
            '<button class="fm-tab' + on + '" type="button" role="tab"'
            ' data-panel="' + panel + '"'
            ' id="fm-tab-' + panel + '"'
            ' aria-controls="fm-page-' + panel + '"'
            ' aria-selected="' + ("true" if i == 0 else "false") + '">'
            + escape(t(key)) + "</button>"
        )
    return '<div class="fm-tabs" role="tablist">' + "".join(buttons) + "</div>"


def _fm_render_info_panel(gui) -> str:
    """舰队信息面板：扫描开关 + 最近一次扫描结果卡片。"""
    config = gui.alas_config.read_file(gui.alas_name)
    result = deep_get(config, gui.RESULT_PATH, default={})
    record = deep_get(config, gui.RECORD_PATH)
    html = ['<div class="fm-panel"><div class="fm-panel-head">',
            '<h2>' + escape(t("Gui.FleetManagement.ScanTitle")) + '</h2>',
            '<div class="fm-head-actions" id="fm-scan-switch"></div>',
            '</div><div class="fm-panel-body">',
            '<p class="fm-hint">' + escape(t("Gui.FleetRotation.Duplicates")) + '</p>',
            '<div id="fm-info-result">']
    if isinstance(result, dict) and result:
        html.append(gui._fleet_info_html(result, record))
    else:
        html.append('<p class="fm-empty">' + escape(t("Gui.FleetManagement.NoResult")) + '</p>')
    html.append('</div></div></div>')
    return "".join(html)


def _fm_render_rotation_panel(gui) -> str:
    """换船配置面板：六队规则编辑器（勾选舰位 + 候补药丸 + 常用）。"""
    config = gui.alas_config.read_file(gui.alas_name)
    rules_raw = deep_get(config, "FleetInfo.FleetRotation.Rules", default="{}")
    state_raw = deep_get(config, "FleetInfo.FleetRotation.State", default={})
    try:
        rules = json.loads(rules_raw) if isinstance(rules_raw, str) else (rules_raw or {})
    except (TypeError, ValueError):
        rules = {}
    if not isinstance(rules, dict):
        rules = {}
    state = state_raw if isinstance(state_raw, dict) else {}

    cards = []
    for fleet in FM_NUMBERS:
        rule = rules.get(str(fleet), {})
        slots = rule.get("slots", []) if isinstance(rule, dict) else []
        sources = rule.get("sources", []) if isinstance(rule, dict) else []
        favourite = bool(rule.get("favourite")) if isinstance(rule, dict) else False
        progress = state.get(str(fleet), {}) if isinstance(state, dict) else {}
        progress = progress if isinstance(progress, dict) else {}

        slot_rows = []
        for slot in FM_NUMBERS:
            on = slot in slots
            slot_rows.append(
                '<label class="fm-ck"><input type="checkbox" data-fleet="' + str(fleet)
                + '" data-kind="slots" data-num="' + str(slot) + '"'
                + (" checked" if on else "") + '>舰位 ' + str(slot)
                + ' · <span class="fm-ck-tag' + ("" if on else " keep") + '">'
                + escape(t("Gui.FleetRotation.Swap" if on else "Gui.FleetRotation.Keep"))
                + '</span></label>'
            )
        pills = []
        for other in FM_NUMBERS:
            if other == fleet:
                continue
            on = other in sources
            pills.append(
                '<label class="fm-pill' + (" fm-on" if on else "")
                + '"><input type="checkbox" data-fleet="' + str(fleet)
                + '" data-kind="sources" data-num="' + str(other) + '"'
                + (" checked" if on else "") + ">" + str(other) + "</label>"
            )
        pills.append(
            '<label class="fm-pill' + (" fm-on" if favourite else "")
            + '"><input type="checkbox" data-fleet="' + str(fleet)
            + '" data-kind="favourite" data-num="0"'
            + (" checked" if favourite else "") + ">"
            + escape(t("Gui.FleetRotation.Favourite")) + "</label>"
        )

        if progress.get("pending") or progress.get("rescan"):
            progress_text = escape(t("Gui.FleetRotation.Uncertain"))
        elif progress.get("favourite"):
            progress_text = escape(t("Gui.FleetRotation.LastFavourite"))
        elif progress.get("last_source"):
            progress_text = escape(t("Gui.FleetRotation.Last", number=progress["last_source"]))
        else:
            progress_text = escape(t("Gui.FleetRotation.None"))

        cards.append(
            '<section class="fm-panel" data-fleet="' + str(fleet) + '">'
            '<div class="fm-panel-head"><h2>'
            + escape(t("Gui.FleetRotation.FleetTitle", number=fleet)) + '</h2></div>'
            '<div class="fm-col"><h3>' + escape(t("Gui.FleetRotation.Slots")) + '</h3>'
            '<div class="fm-checks">' + "".join(slot_rows) + "</div></div>"
            '<div class="fm-col"><h3>' + escape(t("Gui.FleetRotation.Sources")) + '</h3>'
            '<div class="fm-pills">' + "".join(pills) + "</div>"
            '<p class="fm-hint">' + escape(t("Gui.FleetRotation.FavouriteHelp")) + "</p></div>"
            '<div class="fm-col"><p class="fm-hint" style="margin:0">' + progress_text + "</p></div>"
            "</section>"
        )

    return (
        '<p class="fm-hint">' + escape(t("Gui.FleetRotation.Help")) + "</p>"
        '<p class="fm-hint">' + escape(t("Gui.FleetRotation.Duplicates")) + "</p>"
        '<div class="fm-grid">' + "".join(cards) + "</div>"
        '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px">'
        '<button class="fm-btn fm-primary" id="fm-save-rules">'
        + escape(t("Gui.FleetRotation.Save")) + "</button>"
        '<button class="fm-btn" id="fm-reset-state">'
        + escape(t("Gui.FleetRotation.Reset")) + "</button>"
        "</div>"
    )


def _fm_render_training_panel(gui) -> str:
    """换船调度面板：四个调度开关卡 + 培养实读表。"""
    config = gui.alas_config.read_file(gui.alas_name)

    def flag(path):
        return deep_get(config, path, default=False) is True

    switches = [
        ("Gui.FleetTraining.MoraleTitle", flag("FleetInfo.FleetRotation.Enabled")),
        ("Gui.FleetTraining.LevelTitle", flag("FleetInfo.FleetLevelTraining.Enabled")),
        ("Gui.FleetTraining.AffinityTitle", flag("FleetInfo.FleetAffinityTraining.Enabled")),
        ("Gui.FleetTraining.DormTitle", flag("FleetInfo.FleetDorm.Enabled")),
    ]
    cards = []
    for key, on in switches:
        cards.append(
            '<section class="fm-panel"><div class="fm-panel-head"><h2>'
            + escape(t(key)) + '</h2>'
            + ('<span class="fm-badge ok">' + escape(t("Gui.FleetTraining.On")) + '</span>'
               if on else '<span class="fm-badge low">' + escape(t("Gui.FleetTraining.Off")) + '</span>')
            + "</div></section>"
        )

    interval = deep_get(config, "FleetInfo.FleetTraining.CheckIntervalHours", default=6.0)
    dorm_floor = deep_get(config, "FleetInfo.FleetDorm.Floor", default="floor_1")

    training_state = deep_get(config, "FleetInfo.FleetTraining.State", default={})
    training_state = training_state if isinstance(training_state, dict) else {}
    rows = []
    for fleet, item in sorted(training_state.get("training", {}).items()):
        item = item if isinstance(item, dict) else {}
        rows.append(
            "<tr><td>" + escape(t("Gui.FleetRotation.FleetTitle", number=fleet)) + "</td><td>"
            + escape(str(item.get("actual", "-"))) + "</td><td>"
            + escape(str(item.get("checked_at", "-"))) + "</td></tr>"
        )
    training_table = (
        '<div class="fm-table-wrap"><table class="fm-table"><thead><tr><th>'
        + escape(t("Gui.FleetTraining.Fleet")) + "</th><th>"
        + escape(t("Gui.FleetTraining.Actual")) + "</th><th>"
        + escape(t("Gui.FleetTraining.CheckedAt")) + "</th></tr></thead><tbody>"
        + ("".join(rows) or '<tr><td colspan="3" class="fm-empty">'
           + escape(t("Gui.FleetTraining.Empty")) + "</td></tr>")
        + "</tbody></table></div>"
    )

    floor_key = "Gui.FleetDorm.Floor1" if dorm_floor == "floor_1" else "Gui.FleetDorm.Floor2"
    return (
        '<p class="fm-hint">' + escape(t("Gui.FleetTraining.Help")) + "</p>"
        '<div class="fm-grid">' + "".join(cards) + "</div>"
        '<div class="fm-panel" style="margin-top:4px"><div class="fm-panel-head">'
        '<h2>' + escape(t("Gui.FleetTraining.IntervalTitle")) + '</h2></div>'
        '<div class="fm-panel-body"><p class="fm-hint">'
        + escape(t("Gui.FleetTraining.IntervalHelp", hours=interval)) + "</p></div></div>"
        '<div class="fm-panel" style="margin-top:14px"><div class="fm-panel-head">'
        '<h2>' + escape(t("Gui.FleetTraining.RecordTitle")) + '</h2></div>'
        '<div class="fm-panel-body">' + training_table + "</div></div>"
        '<p class="fm-hint" style="margin-top:10px">'
        + escape(t("Gui.FleetTraining.DormNote", floor=escape(t(floor_key))))
        + "</p>"
    )


def _fm_render_equipment_panel(gui) -> str:
    """换船装备面板：按舰种的装备码槽位（只读展示，编辑走搜索配置表单）。"""
    config = gui.alas_config.read_file(gui.alas_name)
    codes_raw = deep_get(config, "FleetInfo.FleetEquipment.Codes", default="{}")
    try:
        codes = json.loads(codes_raw) if isinstance(codes_raw, str) else (codes_raw or {})
    except (TypeError, ValueError):
        codes = {}
    if not isinstance(codes, dict):
        codes = {}

    ship_types = [
        ("DD", "驱逐舰"), ("CL", "轻巡洋舰"), ("CA", "重巡洋舰"), ("CB", "大型巡洋舰"),
        ("BC", "战列巡洋舰"), ("BB", "战列舰"), ("CVL", "轻型航空母舰"), ("CV", "航空母舰"),
        ("BBV", "航空战列舰"), ("SS", "潜艇"), ("SSV", "潜水空母"), ("AR", "维修舰"),
        ("BM", "重炮舰"), ("AE", "运输舰"), ("IX", "风帆舰"),
    ]
    cards = []
    for code, name_cn in ship_types:
        slots = codes.get(code, [])
        if not isinstance(slots, list):
            slots = []
        inputs = []
        for idx in range(3):
            value = slots[idx] if idx < len(slots) and isinstance(slots[idx], str) else ""
            inputs.append(
                '<label class="fm-ck" style="display:flex;gap:8px;align-items:center">'
                "<span style='width:14px;color:var(--fm-muted)'>" + str(idx + 1) + "</span>"
                '<input class="fm-input" style="flex:1" readonly value="' + escape(value)
                + '" placeholder="' + escape(t("Gui.FleetEquipment.Placeholder")) + '"></label>'
            )
        cards.append(
            '<section class="fm-panel"><div class="fm-panel-head"><h2>' + name_cn
            + " " + code + '</h2><span class="fm-hint">'
            + escape(t("Gui.FleetEquipment.Code")) + "</span></div>"
            '<div class="fm-col">' + "".join(inputs) + "</div></section>"
        )

    enabled = deep_get(config, "FleetInfo.FleetEquipment.Enabled", default=False) is True
    state_key = "Gui.FleetEquipment.Enabled" if enabled else "Gui.FleetEquipment.Disabled"
    return (
        '<p class="fm-hint">' + escape(t("Gui.FleetEquipment.Help")) + "</p>"
        '<p class="fm-note">' + escape(t("Gui.FleetEquipment.EditViaForm"))
        + "<b>" + escape(t(state_key)) + "</b></p>"
        '<div class="fm-grid">' + "".join(cards) + "</div>"
    )



class FleetManagementMixin(WebUIMixinBase):
    """舰队管理：船坞扫描结果展示与智能换船四页签界面。"""
    """提供舰队扫描触发与已保存舰队信息展示。"""

    RESULT_PATH = "FleetInfo.FleetInfo.Result"
    RECORD_PATH = "FleetInfo.FleetInfo.Record"

    @staticmethod
    def _fleet_info_ships(fleets: dict, fleet: int) -> str:
        ships = fleets.get(str(fleet), fleets.get(fleet, []))
        if not isinstance(ships, list):
            return '<div class="fleet-info-empty">-</div>'

        entries = []
        for ship in ships:
            if isinstance(ship, dict):
                name = str(ship.get("name", "")).strip()
                level = ship.get("level")
            else:
                name = str(ship).strip()
                level = None
            if not name:
                continue

            level_html = ""
            if isinstance(level, int) and level > 0:
                level_html = f'<span class="fleet-info-level">Lv.{level}</span>'
            entries.append(
                f'<div class="fleet-info-ship"><span>{escape(name)}</span>{level_html}</div>'
            )

        if not entries:
            return '<div class="fleet-info-empty">-</div>'
        return ''.join(entries)

    def _fleet_info_html(self, result: dict, record) -> str:
        surface_cards = []
        for fleet in range(1, 7):
            vanguard = result.get("vanguard", {})
            main = result.get("main", {})
            vanguard = vanguard if isinstance(vanguard, dict) else {}
            main = main if isinstance(main, dict) else {}
            surface_cards.append(
                "<section class=\"fleet-info-card\">"
                f"<h2>{escape(t('Gui.FleetManagement.Fleet'))} {fleet}</h2>"
                "<div class=\"fleet-info-columns\">"
                "<div class=\"fleet-info-column fleet-info-vanguard\">"
                f"<h3>{escape(t('Gui.FleetManagement.Vanguard'))}</h3>"
                f"<div class=\"fleet-info-ships\">{self._fleet_info_ships(vanguard, fleet)}</div>"
                "</div>"
                "<div class=\"fleet-info-column fleet-info-main\">"
                f"<h3>{escape(t('Gui.FleetManagement.Main'))}</h3>"
                f"<div class=\"fleet-info-ships\">{self._fleet_info_ships(main, fleet)}</div>"
                "</div>"
                "</div>"
                "</section>"
            )

        submarine = result.get("submarine", {})
        submarine = submarine if isinstance(submarine, dict) else {}
        submarine_cards = []
        for fleet in range(1, 7):
            ships = submarine.get(str(fleet), submarine.get(fleet, []))
            if not isinstance(ships, list) or not any(str(ship).strip() for ship in ships):
                continue
            submarine_cards.append(
                "<section class=\"fleet-info-card fleet-info-submarine-card\">"
                f"<h2>{escape(t('Gui.FleetManagement.Fleet'))} {fleet}</h2>"
                f"<div class=\"fleet-info-ships\">{self._fleet_info_ships(submarine, fleet)}</div>"
                "</section>"
            )

        submarine_html = ""
        if submarine_cards:
            submarine_html = (
                "<div class=\"fleet-info-group-title fleet-info-underwater-title\">"
                f"{escape(t('Gui.FleetManagement.UnderwaterFleet'))}"
                "</div>"
                f"<div class=\"fleet-info-grid fleet-info-submarine-grid\">{''.join(submarine_cards)}</div>"
            )

        return f"""
        <style>
          .fleet-info-header {{
            display: flex;
            align-items: baseline;
            gap: .6rem;
            margin: .25rem 0 1rem;
            color: #5c6470;
            font-size: .9rem;
          }}
          .fleet-info-header strong {{ color: #1f2937; font-weight: 600; }}
          .fleet-info-grid {{
            display: grid;
            grid-template-columns: minmax(0, 1fr);
            gap: .9rem;
          }}
          .fleet-info-card {{
            overflow: hidden;
            border: 1px solid #d9e1e8;
            border-radius: 6px;
            background: #ffffff;
            box-shadow: 0 1px 2px rgba(15, 23, 42, .06);
          }}
          .fleet-info-card h2 {{
            margin: 0;
            padding: .7rem .85rem;
            border-bottom: 1px solid #d9e1e8;
            background: #eef5fa;
            color: #1f4c6e;
            font-size: 1rem;
            font-weight: 600;
          }}
          .fleet-info-columns {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }}
          .fleet-info-column {{
            min-width: 0;
            padding: .7rem .85rem .8rem;
          }}
          .fleet-info-column + .fleet-info-column {{ border-left: 1px solid #edf1f4; }}
          .fleet-info-column h3 {{
            margin: 0 0 .45rem;
            font-size: .85rem;
            font-weight: 600;
          }}
          .fleet-info-main h3 {{ color: #9a5a12; }}
          .fleet-info-vanguard h3 {{ color: #176b69; }}
          .fleet-info-ships {{
            min-width: 0;
            color: #26323c;
            font-size: .9rem;
            line-height: 1.55;
            overflow-wrap: anywhere;
          }}
          .fleet-info-ship {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: .5rem;
            min-height: 1.55em;
          }}
          .fleet-info-level {{
            color: #697783;
            font-size: .8rem;
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
          }}
          .fleet-info-empty {{ color: #a0a8af; }}
          .fleet-info-group-title {{
            margin: 1.35rem 0 .65rem;
            color: #59617e;
            font-size: .95rem;
            font-weight: 600;
          }}
          .fleet-info-surface-title {{ margin-top: 0; color: #1f4c6e; }}
          .fleet-info-submarine-card h2 {{ background: #f1f1f7; color: #4d5473; }}
          .fleet-info-submarine-card .fleet-info-ships {{ padding: .75rem .85rem; }}
        </style>
        <div class=\"fleet-info-header\">
          <span>{escape(t('Gui.FleetManagement.LastScan'))}</span>
          <strong>{escape(str(record or '-'))}</strong>
        </div>
        <div class=\"fleet-info-group-title fleet-info-surface-title\">
          {escape(t('Gui.FleetManagement.SurfaceFleet'))}
        </div>
        <div class=\"fleet-info-grid\">{''.join(surface_cards)}</div>
        {submarine_html}
        """

    def _start_scan_task(self, task: str) -> bool:
        """启动扫描任务；返回 False 表示已有实例操作进行中。

        ``ProcessManager.start`` 是进程级启动器：worker 存活时它是 no-op
        （既不切换任务也不报错），所以扫描必须走「先停止（后台等待收尾）
        再以扫描任务启动」两段——与主页启动按钮同一套语义与设施
        （``spawn_instance_action`` 把长耗时的 stop 挪到后台线程）。
        """
        if not getattr(self, "alas", None):
            return False
        if not self.alas.alive:
            self.alas.start(task)
            return True

        def action():
            self.alas.stop_by_user()
            deadline = time.monotonic() + 240
            while self.alas.alive and time.monotonic() < deadline:
                time.sleep(2)
            self.alas.start(task)

        return spawn_instance_action(self.alas, action, "start")

    def _fleet_scan_running(self) -> bool:
        """扫描按钮的运行窗。

        alas 跑在独立进程，老前端拿不到跨进程的当前任务名；以本会话
        时间窗提供「扫描中」反馈，窗口外按钮始终可以再次启动扫描。
        停止→启动切换进行中（``_ui_action_running``）同样显示运行态。
        """
        alas = getattr(self, "alas", None)
        if alas is not None and getattr(alas, "_ui_action_running", False):
            return True
        started = getattr(self, "_fleet_scan_started_at", None)
        return scan_window_active(started, SCAN_WINDOW_SECONDS)

    def _fleet_scan_start(self) -> None:
        if self._fleet_scan_running():
            toast(t("Gui.FleetManagement.ScanAlreadyRunning"), color="warn")
            return

        if not getattr(self, "alas", None):
            toast(t("Gui.FleetManagement.ScanStartFailed"), color="error")
            return
        if not self._start_scan_task("FleetScan"):
            toast(t("Gui.FleetManagement.ScanAlreadyRunning"), color="warn")
            return
        if self.alas.alive:
            self._fleet_scan_started_at = time.monotonic()
            toast(t("Gui.FleetManagement.ScanStarted"), color="info")
        else:
            toast(t("Gui.FleetManagement.ScanStartFailed"), color="error")

    def _fleet_scan_running_click(self) -> None:
        toast(t("Gui.FleetManagement.ScanAlreadyRunning"), color="warn")

    def _fm_save_rotation(self, payload: dict) -> None:
        """保存轮换规则（可带清空轮换记录）。"""
        rules = payload.get("rules")
        if not isinstance(rules, dict):
            toast(t("Gui.FleetRotation.InvalidRules"), color="error")
            return
        try:
            json.dumps(rules)
        except (TypeError, ValueError):
            toast(t("Gui.FleetRotation.InvalidRules"), color="error")
            return
        changes = {"FleetInfo.FleetRotation.Rules": json.dumps(rules, ensure_ascii=False)}
        if payload.get("resetState"):
            changes["FleetInfo.FleetRotation.State"] = {}
        self.alas_config.cross_set_many(changes)
        toast(t("Gui.FleetRotation.Saved"), color="success")

    @use_scope("content", clear=True)
    def fleet_scan_page(self, task: str = "FleetScan") -> None:
        """展示舰队扫描的一次性触发入口。"""
        self.init_menu(name=task)
        self.set_title(t(f"Task.{task}.name"))
        put_scope("fleet_scan_button")

        button = BinarySwitchButton(
            get_state=self._fleet_scan_running,
            label_on=t("Gui.FleetManagement.Scanning"),
            label_off=t("Gui.FleetManagement.StartScan"),
            onclick_on=self._fleet_scan_running_click,
            onclick_off=self._fleet_scan_start,
            color_on="off",
            color_off="on",
            scope="fleet_scan_button",
        )
        self.task_handler.add(button.g(), 1, True)

    @use_scope("content", clear=True)
    def fleet_info_page(self, task: str = "FleetInfo") -> None:
        """舰队管理主页：舰队信息 + 智能换船三个子页（页签容器）。"""
        self.init_menu(name=task)
        self.set_title(t("Gui.FleetManagement.PageTitle"))

        # 页签条（裸 HTML，点击经桥 pin 回调服务端懒加载其余面板）
        put_html(self._fm_tabs_html())

        # 四个面板容器：info 直接渲染，其余先放骨架，切页签时经桥按需渲染
        put_scope("fm-page-info fm-on", [_fm_render_info_panel(self)])
        for panel, _key in FM_TABS[1:]:
            put_scope(
                f"fm-page-{panel}",
                [put_html('<div class="fm-empty" id="fm-skeleton" data-for="'
                          + panel + '">' + escape(t("Gui.FleetTraining.Loading"))
                          + '</div>')],
            )

        # 桥 pin + 页签/规则编辑器的交互脚本
        put_scope("fm-bridge", [put_input(name=FM_BRIDGE_PIN, value="")])
        put_html(_fm_bridge_script())
        self._fm_bind_bridge()

    def _fm_bind_bridge(self) -> None:
        """绑定页签/规则桥的 pin 监听；重复进入页面不重复绑定。"""
        if getattr(self, "_fm_callbacks_bound", False):
            return
        pin_on_change(
            name=FM_BRIDGE_PIN,
            onchange=self._fm_on_bridge,
            clear=True,
            serial_mode=True,
        )
        self._fm_callbacks_bound = True

    def _fm_on_bridge(self, value) -> None:
        """桥 pin 回调：按 payload 分派页签切换与规则保存。"""
        try:
            payload = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        action = payload.get("action")
        if action == "tab":
            panel = payload.get("panel")
            if panel in ("info", "rotation", "training", "equipment"):
                self._fm_render_panel(panel)
        elif action == "saveRotation":
            self._fm_save_rotation(payload)

    def _fm_render_panel(self, panel: str) -> None:
        """按需渲染指定页签的面板内容（懒加载）。"""
        with use_scope(f"fm-page-{panel}", clear=True):
            if panel == "info":
                put_html(_fm_render_info_panel(self))
            elif panel == "rotation":
                put_html(_fm_render_rotation_panel(self))
            elif panel == "training":
                put_html(_fm_render_training_panel(self))
            elif panel == "equipment":
                put_html(_fm_render_equipment_panel(self))
        # 渲染完成后通知前端撤骨架、点亮已加载标记
        from pywebio.session import run_js
        run_js(
            "(function(){var p=document.getElementById('fm-page-"
            + panel
            + "');"
            "if(p){p.classList.add('fm-on');p.dataset.loaded='1';}"
            "var sk=document.getElementById('fm-skeleton');"
            "if(sk&&sk.dataset.for==='" + panel + "'){sk.remove();}"
            "})();"
        )
