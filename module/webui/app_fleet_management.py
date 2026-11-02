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
    pin_on_change,
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
        # 整段脚本每次进页都会重新注入；document 级监听器不随 scope 清理，
        # 重复注册会让一次点击触发 N 次 send（双 toast 的根源）。
        "if(window.__fmBridgeInstalled)return;window.__fmBridgeInstalled=true;"
        "var TABS=" + tabs_json + ";"
        "var bridge=function(){return document.querySelector('input[name=" + json.dumps(FM_BRIDGE_PIN) + "]')};"
        "var send=function(obj){var b=bridge();if(!b)return;"
        "b.value=JSON.stringify(obj);b.dispatchEvent(new Event('input',{bubbles:true}));b.dispatchEvent(new Event('change',{bubbles:true}));};"
        "var sendTab=function(panel){"
        "var box=document.getElementById('pywebio-scope-fm-hidden-actions');"
        "var btns=box?box.querySelectorAll('button'):[];"
        "for(var k=0;k<btns.length;k++){if((btns[k].textContent||'').trim()==='tab:'+panel){btns[k].click();return true;}}"
        "send({action:'tab',panel:panel});return false;};"
        "\n// 页签点击（document 委托：页签条随 scope 重渲染，监听器只绑一次）\n"
        "document.addEventListener('click',function(ev){"
        "var btn=ev.target.closest('.fm-tab');if(!btn)return;"
        "var panel=btn.dataset.panel;"
        "document.querySelectorAll('.fm-tab').forEach(function(b){"
        "var on=b===btn;b.classList.toggle('fm-on',on);"
        "b.setAttribute('aria-selected',on?'true':'false');});"
        "document.querySelectorAll('.fm-page').forEach(function(p){"
        "p.classList.toggle('fm-on',p.dataset.panel===panel);});"
        "var target=document.querySelector('.fm-page[data-panel='+JSON.stringify(panel)+']');"
        "if(target&&!target.dataset.loaded){target.dataset.loaded='1';sendTab(panel);}"
        "try{localStorage.setItem('__alasFmPanel',panel);}catch(e){}"
        "});"
        "// 规则编辑器：勾选即改样式；保存时收集整份规则\n" + ""
        "var state={};"
        "document.addEventListener('change',function(ev){"
        "var el=ev.target;"
        # 调度设置：开关（fm-sw-*）与参数（data-fm-setting）即点即存（JS 内另有 // 注释）
        "if(el.id&&el.id.indexOf('fm-sw-')===0){"
        "var key=el.id.slice(6);"
        "var map={rotation:'rotation.enabled',level:'level.enabled',affinity:'affinity.enabled',dorm:'dorm.enabled',equipment:'equipment.enabled'};"
        "var card=el.closest('.fm-panel');"
        "if(card){card.classList.toggle('fm-state-on',el.checked);card.classList.toggle('fm-state-off',!el.checked);}"
        "send({action:'saveTraining',settings:{},switch:key+'|'+(el.checked?'1':'0'),target:map[key]||''});"
        "return;}"
        "if(el.dataset&&el.dataset.fmSetting){"
        "send({action:'saveTraining',settings:{},setting:el.dataset.fmSetting+'|'+el.value,target:''});"
        "return;}"
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
        "tag.classList.toggle('keep',!on);})}"
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


def _fm_hero_html(gui) -> str:
    """页头横幅：纯白卡片风，右侧显示四路调度启用数。"""
    config = gui.alas_config.read_file(gui.alas_name)
    flags = [
        deep_get(config, "FleetInfo.FleetRotation.Enabled", default=False),
        deep_get(config, "FleetInfo.FleetLevelTraining.Enabled", default=False),
        deep_get(config, "FleetInfo.FleetAffinityTraining.Enabled", default=False),
        deep_get(config, "FleetInfo.FleetDorm.Enabled", default=False),
    ]
    enabled = sum(1 for f in flags if f is True)
    return (
        '<div class="fm-hero"><div><h1>' + escape(t("Gui.FleetManagement.PageTitle")) + "</h1>"
        '<div class="fm-hero-sub">' + escape(t("Gui.FleetRotation.Help")) + "</div></div>"
        '<div class="fm-hero-badge"><span class="fm-dot"></span>'
        + escape(t("Gui.FleetRotation.EnabledCount", count=enabled))
        + "</div></div>"
    )


def _fm_tabs_html(active: str = "info") -> str:
    """药丸式页签条。交互由 fm_bridge JS 接管（切页签 + 懒加载）。"""
    buttons = []
    for i, (panel, key) in enumerate(FM_TABS):
        on = " fm-on" if panel == active else ""
        buttons.append(
            '<button class="fm-tab' + on + '" type="button" role="tab"'
            ' data-panel="' + panel + '"'
            ' id="fm-tab-' + panel + '"'
            ' aria-controls="fm-page-' + panel + '"'
            ' aria-selected="' + ("true" if i == 0 else "false") + '">'
            + escape(t(key)) + "</button>"
        )
    return '<div class="fm-tabs" role="tablist">' + "".join(buttons) + "</div>"


def _fm_render_panel_content(gui, panel: str) -> str:
    """按面板 id 渲染内容（容器与懒加载分派共用）。"""
    if panel == "rotation":
        return _fm_render_rotation_panel(gui)
    if panel == "training":
        return _fm_render_training_panel(gui)
    if panel == "equipment":
        return _fm_render_equipment_panel(gui)
    return _fm_render_info_panel(gui)


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

    actions = (
        '<div class="fm-actions">'
        '<button class="fm-btn fm-primary" id="fm-save-rules">'
        + escape(t("Gui.FleetRotation.Save")) + "</button>"
        '<button class="fm-btn" id="fm-reset-state">'
        + escape(t("Gui.FleetRotation.Reset")) + "</button>"
        "</div>"
    )

    return (
        '<p class="fm-hint">' + escape(t("Gui.FleetRotation.Help")) + "</p>"
        '<p class="fm-hint">' + escape(t("Gui.FleetRotation.Duplicates")) + "</p>"
        + actions
        + '<div class="fm-grid">' + "".join(cards) + "</div>"
    )


def _fm_switch_html(switch_id: str, checked: bool) -> str:
    """可点击的开关控件（纯 HTML，状态经桥保存）。"""
    return (
        '<label class="fm-switch"><input type="checkbox" id="' + switch_id + '"'
        + (" checked" if checked else "") + '><span class="fm-switch-track"></span></label>'
    )


def _fm_render_training_panel(gui) -> str:
    """换船调度面板：四个调度的真实设置卡（开关 + 参数）+ 培养实读表。

    开关与参数改动即时经桥保存（saveTraining action），无需额外保存按钮。
    """
    config = gui.alas_config.read_file(gui.alas_name)

    def flag(path):
        return deep_get(config, path, default=False) is True

    rotation_on = flag("FleetInfo.FleetRotation.Enabled")
    selected_only = flag("FleetInfo.FleetRotation.SelectedOnly")
    level_on = flag("FleetInfo.FleetLevelTraining.Enabled")
    level_target = deep_get(config, "FleetInfo.FleetLevelTraining.Target", default=120)
    affinity_on = flag("FleetInfo.FleetAffinityTraining.Enabled")
    affinity_target = deep_get(config, "FleetInfo.FleetAffinityTraining.Target", default=100)
    dorm_on = flag("FleetInfo.FleetDorm.Enabled")
    dorm_floor = deep_get(config, "FleetInfo.FleetDorm.Floor", default="floor_1")
    stop_on_complete = flag("FleetInfo.FleetTraining.StopOnComplete")
    notify = flag("FleetInfo.FleetTraining.Notify")
    interval = deep_get(config, "FleetInfo.FleetTraining.CheckIntervalHours", default=6.0)

    def card(title_key, body, switch_id=None, on=False):
        head = '<h2>' + escape(t(title_key)) + '</h2>'
        state = ""
        if switch_id:
            head += _fm_switch_html(switch_id, on)
            state = " fm-state-on" if on else " fm-state-off"
        return (
            '<section class="fm-panel' + state + '"><div class="fm-panel-head">' + head + '</div>'
            '<div class="fm-panel-body">' + body + "</div></section>"
        )

    morale_body = (
        '<label class="fm-ck fm-sub"><input type="checkbox" data-fm-setting="rotation.selectedOnly"'
        + (" checked" if selected_only else "") + '>'
        + escape(t("Gui.FleetRotation.SelectedOnly")) + "</label>"
        '<p class="fm-hint">' + escape(t("Gui.FleetRotation.SelectedOnly")) + "</p>"
    )
    level_body = (
        '<label class="fm-num">' + escape(t("Gui.FleetRotation.LevelTarget"))
        + '<input class="fm-input" type="number" min="1" max="125" data-fm-setting="level.target" value="'
        + str(level_target) + '"></label>'
    )
    affinity_body = (
        '<label class="fm-num">' + escape(t("Gui.FleetRotation.AffinityTarget"))
        + '<input class="fm-input" type="number" min="1" max="200" data-fm-setting="affinity.target" value="'
        + str(affinity_target) + '"></label>'
    )
    floor1 = '<option value="floor_1"' + (" selected" if dorm_floor == "floor_1" else "") + ">" \
        + escape(t("Gui.FleetDorm.Floor1")) + "</option>"
    floor2 = '<option value="floor_2"' + (" selected" if dorm_floor == "floor_2" else "") + ">" \
        + escape(t("Gui.FleetDorm.Floor2")) + "</option>"
    dorm_body = (
        '<p class="fm-hint">' + escape(t("Gui.FleetDorm.Help")) + "</p>"
        '<label class="fm-num">' + escape(t("Gui.FleetDorm.Floor"))
        + '<select class="fm-input" data-fm-setting="dorm.floor">' + floor1 + floor2 + "</select></label>"
    )
    run_body = (
        '<label class="fm-ck fm-sub"><input type="checkbox" data-fm-setting="training.stopOnComplete"'
        + (" checked" if stop_on_complete else "") + '>' + escape(t("Gui.FleetTraining.StopOnComplete")) + "</label>"
        '<label class="fm-ck fm-sub"><input type="checkbox" data-fm-setting="training.notify"'
        + (" checked" if notify else "") + '>' + escape(t("Gui.FleetTraining.Notify")) + "</label>"
        '<label class="fm-num">' + escape(t("Gui.FleetTraining.CheckIntervalHours"))
        + '<input class="fm-input" type="number" min="1" max="24" step="0.5" data-fm-setting="training.interval" value="'
        + str(interval) + '"></label>'
    )

    cards = (
        card("Gui.FleetTraining.MoraleTitle", morale_body, "fm-sw-rotation", rotation_on)
        + card("Gui.FleetTraining.LevelTitle", level_body, "fm-sw-level", level_on)
        + card("Gui.FleetTraining.AffinityTitle", affinity_body, "fm-sw-affinity", affinity_on)
        + card("Gui.FleetTraining.DormTitle", dorm_body, "fm-sw-dorm", dorm_on)
        + card("Gui.FleetTraining.IntervalTitle", run_body)
    )

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
        '<div class="fm-grid">' + cards + "</div>"
        '<div class="fm-panel" style="margin-top:4px"><div class="fm-panel-head">'
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
    switch_html = (
        '<div class="fm-equip-head">'
        + '<span class="fm-hint" style="margin:0">' + escape(t("Gui.FleetEquipment.EditViaForm")) + "</span>"
        + _fm_switch_html("fm-sw-equipment", enabled)
        + "</div>"
    )
    return (
        '<p class="fm-hint">' + escape(t("Gui.FleetEquipment.Help")) + "</p>"
        + switch_html
        + '<div class="fm-grid">' + "".join(cards) + "</div>"
    )



class FleetManagementMixin(WebUIMixinBase):
    """舰队管理：船坞扫描结果展示与智能换船四页签界面。"""
    """提供舰队扫描触发与已保存舰队信息展示。"""

    RESULT_PATH = "FleetInfo.FleetInfo.Result"
    RECORD_PATH = "FleetInfo.FleetInfo.Record"

    @staticmethod
    def _fleet_info_ships(fleets: dict, fleet: int) -> str:
        """上游结构的一列舰船：船名 + Lv.等级 · 心情 值。"""
        ships = fleets.get(str(fleet), fleets.get(fleet, []))
        if not isinstance(ships, list):
            return '<div class="fleet-info-empty">-</div>'

        entries = []
        for ship in ships:
            if isinstance(ship, dict):
                name = str(ship.get("name", "")).strip()
                level = ship.get("level")
                emotion = ship.get("emotion")
            else:
                name = str(ship).strip()
                level = emotion = None
            if not name:
                continue
            meta = []
            if isinstance(level, int) and level > 0:
                meta.append(f"Lv.{level}")
            if isinstance(emotion, int) and emotion > 0:
                meta.append(f"心情 {emotion}")
            meta_html = ('<small>' + ' · '.join(meta) + '</small>') if meta else '<small>—</small>'
            entries.append(
                f'<div class="row"><span class="nm">{escape(name)}</span>{meta_html}</div>'
            )
        if not entries:
            return '<div class="fleet-info-empty">-</div>'
        return ''.join(entries)

    def _fleet_info_html(self, result: dict, record) -> str:
        """上游 1:1 结构：.fleet-grid 六张竖卡片，先锋/主力两列，Lv·心情同行。"""
        vanguard = result.get("vanguard", {})
        main = result.get("main", {})
        vanguard = vanguard if isinstance(vanguard, dict) else {}
        main = main if isinstance(main, dict) else {}

        cards = []
        for fleet in range(1, 7):
            cards.append(
                '<section class="panel fm-info-panel">'
                f'<div class="panel-heading"><h2>{escape(t("Gui.FleetManagement.Fleet"))} {fleet}</h2></div>'
                '<div class="fleet-column">'
                f'<h3>{escape(t("Gui.FleetManagement.Vanguard"))}</h3>'
                f'{self._fleet_info_ships(vanguard, fleet)}'
                '</div>'
                '<div class="fleet-column">'
                f'<h3>{escape(t("Gui.FleetManagement.Main"))}</h3>'
                f'{self._fleet_info_ships(main, fleet)}'
                '</div>'
                '</section>'
            )

        submarine = result.get("submarine", {})
        submarine = submarine if isinstance(submarine, dict) else {}
        sub_cards = []
        for fleet in range(1, 7):
            ships = submarine.get(str(fleet), submarine.get(fleet, []))
            if not isinstance(ships, list) or not any(
                (s.get("name") if isinstance(s, dict) else str(s)).strip() for s in ships if s
            ):
                continue
            sub_cards.append(
                '<section class="panel fm-info-panel fm-info-submarine">'
                f'<div class="panel-heading"><h2>{escape(t("Gui.FleetManagement.Fleet"))} {fleet} · {escape(t("Gui.FleetManagement.UnderwaterFleet"))}</h2></div>'
                f'<div class="fleet-column">{self._fleet_info_ships(submarine, fleet)}</div>'
                '</section>'
            )
        sub_html = ('<div class="fleet-grid fm-info-sub-grid">' + ''.join(sub_cards) + '</div>') if sub_cards else ''

        return f"""
        <div class="fleet-grid fm-info-grid">{''.join(cards)}</div>
        {sub_html}
        <div class="fm-hint" style="margin-top:10px">
          {escape(t('Gui.FleetManagement.LastScan'))}：{escape(str(record or '-'))}
        </div>
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

    _FM_SWITCH_PATHS = {
        "rotation": "FleetInfo.FleetRotation.Enabled",
        "level": "FleetInfo.FleetLevelTraining.Enabled",
        "affinity": "FleetInfo.FleetAffinityTraining.Enabled",
        "dorm": "FleetInfo.FleetDorm.Enabled",
        "equipment": "FleetInfo.FleetEquipment.Enabled",
    }
    _FM_SETTING_PATHS = {
        "rotation.selectedOnly": "FleetInfo.FleetRotation.SelectedOnly",
        "level.target": "FleetInfo.FleetLevelTraining.Target",
        "affinity.target": "FleetInfo.FleetAffinityTraining.Target",
        "dorm.floor": "FleetInfo.FleetDorm.Floor",
        "training.stopOnComplete": "FleetInfo.FleetTraining.StopOnComplete",
        "training.notify": "FleetInfo.FleetTraining.Notify",
        "training.interval": "FleetInfo.FleetTraining.CheckIntervalHours",
    }

    def _fm_save_training(self, payload: dict) -> None:
        """保存调度设置（开关或单项参数，即点即存）。"""
        changes = {}
        switch = payload.get("switch")
        if isinstance(switch, str) and "|" in switch:
            key, val = switch.split("|", 1)
            path = self._FM_SWITCH_PATHS.get(key)
            if path:
                changes[path] = val == "1"
        setting = payload.get("setting")
        if isinstance(setting, str) and "|" in setting:
            key, val = setting.split("|", 1)
            path = self._FM_SETTING_PATHS.get(key)
            if path:
                if key in ("level.target", "affinity.target"):
                    try:
                        val = max(1, int(float(val)))
                    except (TypeError, ValueError):
                        return
                elif key == "training.interval":
                    try:
                        val = min(24.0, max(1.0, float(val)))
                    except (TypeError, ValueError):
                        return
                changes[path] = val
        if not changes:
            return
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
    def fleet_info_page(self, task: str = "FleetInfo", tab: str = "info") -> None:
        """舰队管理主页：舰队信息 + 智能换船三个子页（页签容器）。

        Args:
            task: 任务名（固定 FleetInfo）。
            tab: 初始页签；搜索配置跳转按命中组落到对应页签。
        """
        self.init_menu(name=task)
        self.set_title(t("Gui.FleetManagement.PageTitle"))

        # 页头横幅：标题 + 副标题 + 四路调度启用统计（B 版仪表盘风格）
        put_html(_fm_hero_html(self))

        # 页签条（裸 HTML，点击经桥 pin 回调服务端懒加载其余面板）
        put_html(_fm_tabs_html(active=tab))

        # 四个面板容器：默认页签直接渲染，其余放骨架，切页签时经桥按需渲染。
        # scope 名不能带空格（PyWebIO 校验字母/数字/-/_），显隐类挂在内层 div 上。
        for panel, _key in FM_TABS:
            on = panel == tab
            inner = ('<div class="fm-page' + (' fm-on' if on else '')
                     + '" data-panel="' + panel + '">')
            if on:
                put_scope(
                    f"fm-page-{panel}",
                    [put_html(inner + _fm_render_panel_content(self, panel) + "</div>")],
                )
            else:
                put_scope(
                    f"fm-page-{panel}",
                    [put_html(inner + '<div class="fm-empty" id="fm-skeleton" data-for="'
                              + panel + '">' + escape(t("Gui.FleetTraining.Loading"))
                              + '</div></div>')],
                )

        # 桥 pin + 隐藏真实按钮 + 页签/规则编辑器的交互脚本。
        # 主通道是隐藏按钮（真实回调，远控 P2P 链路不丢）；pin 兜底——
        # 统计页实测（2026-10-21 真机）：pin 的 change 事件在部分环境服务端收不到。
        with use_scope("fm-hidden-actions", clear=True):
            put_buttons(
                [
                    {"label": "tab:info", "value": "tab:info"},
                    {"label": "tab:rotation", "value": "tab:rotation"},
                    {"label": "tab:training", "value": "tab:training"},
                    {"label": "tab:equipment", "value": "tab:equipment"},
                    {"label": "save", "value": "save"},
                ],
                onclick=self._fm_on_buttons,
            )
        put_scope("fm-bridge", [put_input(name=FM_BRIDGE_PIN, value="")])
        put_html(_fm_bridge_script())
        self._fm_bind_bridge()

    def _fm_bind_bridge(self) -> None:
        """绑定桥 pin 监听；重复进入页面不重复绑定。"""

        if getattr(self, "_fm_callbacks_bound", False):
            return
        pin_on_change(
            name=FM_BRIDGE_PIN,
            onchange=self._fm_on_bridge,
            clear=True,
            serial_mode=True,
        )
        self._fm_callbacks_bound = True

    def _fm_on_buttons(self, value: str) -> None:
        """隐藏按钮回调：页签切换的可靠通道（payload 经桥 pin 传递）。"""
        panel = str(value).split(":", 1)[-1]
        if panel in ("info", "rotation", "training", "equipment"):
            self._fm_render_panel(panel)

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
        elif action == "saveTraining":
            self._fm_save_training(payload)

    def _fm_render_panel(self, panel: str) -> None:
        """按需渲染指定页签的面板内容（懒加载）。"""
        with use_scope(f"fm-page-{panel}", clear=True):
            inner = '<div class="fm-page fm-on" data-panel="' + panel + '">'
            put_html(inner + _fm_render_panel_content(self, panel) + "</div>")
        # 渲染完成后通知前端撤骨架
        from pywebio.session import run_js
        run_js(
            "(function(){var sk=document.getElementById('fm-skeleton');"
            "if(sk&&sk.dataset.for==='" + panel + "'){sk.remove();}"
            "})();"
        )
