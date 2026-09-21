"""指挥喵评分面板（「工具+ → 指挥喵评分」任务页）。

面板结构对齐上游 wess09/AzurPilot PR #998 的 ``MeowfficerScorePanel``：
头部是标题、汇总与刷新按钮，正文按每只猫一张卡渲染档位徽章、参考分、
天赋标签，以及主口径的 x+y 公式、分数条与 X/Y 命中明细，其余口径折叠。

数据读任务产出的 ``log/meowfficer_score.json``——与 ``score_task.py`` 写出的
Markdown / HTML 报告同源，因此面板与报告不会出现口径不一致。报告按机器
共享一份（任务与实例同工作目录），不按实例隔离。
"""

import json
import os
from html import escape
from pathlib import Path
from urllib.parse import quote

from module.webui.app_dependencies import (
    put_button,
    put_html,
    put_row,
    put_scope,
    t,
    use_scope,
)
from module.webui.app_types import WebUIMixinBase
from module.webui.base import render_locked

# 与 module/meowfficer/score_task.py 的 ReportPath 默认值同源（后缀换成 .json）
REPORT_PATH = './log/meowfficer_score.json'
# HTML 报告是 ReportPath 的同名兄弟文件，路径本身由实例配置决定
REPORT_ARG_PATH = 'MeowfficerScore.ReportPath'
REPORT_DEFAULT_PATH = './log/meowfficer_score.md'
# 报告只允许从项目 log/ 目录里读：配置里填了任意路径时不能把别的文件读出去
REPORT_ALLOWED_DIR = 'log'

# 标题图标（lucide PawPrint），与上游面板一致
PAW_ICON = (
    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/>'
    '<path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.045Q6.52 17.48 4.46 16.84A3.5 '
    '3.5 0 0 1 5.5 10Z"/></svg>'
)


def report_html_path(instance: str):
    """解析某实例配置里「指挥喵评分」HTML 报告的绝对路径。

    只认「ReportPath 换成 ``.html`` 后缀、且落在项目 ``log/`` 目录内、文件真实
    存在」这一个文件：面板上的「查看完整报告」是给用户看自己那份报告的，
    配置里填了任意路径时不能借它把别的文件读出去。

    Args:
        instance: 实例名（调用方应先用 ``validate_instance`` 校验）。

    Returns:
        pathlib.Path | None: 可读的报告文件；不满足条件时为 ``None``。
    """
    from module.config.deep import deep_get
    from module.config.utils import filepath_config, read_file
    from module.webui.log_export import get_project_root

    config = read_file(filepath_config(instance))
    report = deep_get(config, REPORT_ARG_PATH, REPORT_DEFAULT_PATH) or REPORT_DEFAULT_PATH
    root = get_project_root()
    candidate = Path(os.path.splitext(str(report))[0] + '.html')
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate = candidate.resolve()
        allowed = (root / REPORT_ALLOWED_DIR).resolve()
    except OSError:
        return None
    if candidate.suffix != '.html':
        return None
    if allowed not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


class MeowfficerScoreMixin(WebUIMixinBase):
    """在指挥喵评分任务页的配置分组之后插入评分结果面板。"""

    @staticmethod
    def _meowfficer_tr(key, **kwargs):
        """面板文案翻译：key 为 ``Gui.MeowfficerScore`` 下的字段名。"""
        return t(f'Gui.MeowfficerScore.{key}', **kwargs)

    @staticmethod
    def _meowfficer_report():
        """读取评分报告。

        Returns:
            ``(payload, error)``：报告不存在时 ``(None, '')`` 走空状态；
            内容读不出或结构不对时 ``(None, 提示文案)``。
        """
        if not os.path.isfile(REPORT_PATH):
            return None, ''
        try:
            with open(REPORT_PATH, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None, t('Gui.MeowfficerScore.Broken')
        if not isinstance(data, dict) or not isinstance(data.get('cats'), list):
            return None, t('Gui.MeowfficerScore.Broken')
        return data, ''

    def _meowfficer_report_url(self, has_cats: bool):
        """「查看完整报告」的链接；没有评分结果或报告文件不存在时返回空串。"""
        if not has_cats:
            return ''
        name = getattr(self, 'alas_name', '')
        if not name:
            return ''
        try:
            if report_html_path(name) is None:
                return ''
        except (OSError, ValueError):
            return ''
        return f'/api/reports/meowfficer?instance={quote(str(name))}'

    def _put_meowfficer_score_panel(self) -> None:
        """把面板渲染到当前输出目标（面板 scope 内）。"""
        from module.meowfficer.score_report import render_panel

        payload, error = self._meowfficer_report()
        if payload is None:
            summary = ''
            body = (f'<div class="meow-body"><div class="meow-empty">'
                    f'<strong>{escape(error or t("Gui.MeowfficerScore.EmptyTitle"))}</strong>'
                    f'{"" if error else escape(t("Gui.MeowfficerScore.EmptyHint"))}</div></div>')
        else:
            summary = t(
                'Gui.MeowfficerScore.Summary',
                count=len(payload.get('cats') or []),
                time=payload.get('generatedAt') or '—',
            )
            body = render_panel(payload, self._meowfficer_tr)

        cells = [
            put_html(f'<div class="meow-panel-title">{PAW_ICON}'
                     f'{escape(t("Gui.MeowfficerScore.Title"))}</div>'),
            put_html(f'<span class="meow-summary">{escape(summary)}</span>'),
        ]
        sizes = ['auto', '1fr']
        report_url = self._meowfficer_report_url(
            bool(payload and payload.get('cats'))
        )
        if report_url:
            # 与上游面板一致：报告与面板读的是同一份产物，有报告才给入口；
            # 新开标签页，避免离开当前配置页。
            cells.append(put_html(
                f'<a class="meow-report-link" href="{escape(report_url)}" '
                f'target="_blank" rel="noopener noreferrer">'
                f'{escape(t("Gui.MeowfficerScore.OpenReport"))}</a>'
            ))
            sizes.append('auto')
        cells.append(put_button(
            label=t('Gui.MeowfficerScore.Refresh'),
            onclick=self.meowfficer_score_refresh,
            color='off',
        ))
        sizes.append('auto')

        put_row(cells, size=' '.join(sizes)).style(
            'align-items:center; gap:12px;'
            'padding:16px 20px; border-bottom:1px solid var(--meow-card-border);'
        )
        put_html(body)
        put_html(f'<p class="meow-disclaimer">'
                 f'{escape(t("Gui.MeowfficerScore.Disclaimer"))}</p>')

    def put_meowfficer_score_panel(self) -> None:
        """在配置分组之后插入面板（调用方需先进入 ``groups`` scope）。"""
        put_scope('meowfficer-score-panel')
        with use_scope('meowfficer-score-panel'):
            self._put_meowfficer_score_panel()

    @render_locked
    def meowfficer_score_refresh(self) -> None:
        """刷新按钮：重绘整个面板（头部汇总与卡片一起更新）。"""
        with use_scope('meowfficer-score-panel', clear=True):
            self._put_meowfficer_score_panel()
