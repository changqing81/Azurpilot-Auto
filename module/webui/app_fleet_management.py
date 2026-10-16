"""舰队管理 WebUI 页面。"""

import time
from html import escape

# 扫描按钮「扫描中」反馈的时间窗：alas 在独立进程，老前端拿不到当前任务名。
SCAN_WINDOW_SECONDS = 600

from module.webui.app_dependencies import (
    BinarySwitchButton,
    deep_get,
    put_buttons,
    put_html,
    put_scope,
    put_text,
    t,
    toast,
    use_scope,
)
from module.webui.app_types import WebUIMixinBase
from module.webui.instance_action import spawn_instance_action
from module.webui.utils import scan_window_active


class FleetManagementMixin(WebUIMixinBase):
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
        """展示最近一次舰队扫描写入配置的数据。"""
        self.init_menu(name=task)
        self.set_title(t(f"Task.{task}.name"))

        config = self.alas_config.read_file(self.alas_name)
        result = deep_get(config, self.RESULT_PATH, default={})
        record = deep_get(config, self.RECORD_PATH)
        if not isinstance(result, dict) or not result:
            put_text(t("Gui.FleetManagement.NoResult"))
            return

        put_html(self._fleet_info_html(result, record))
