"""``module.meowfficer.score_report`` 的 WebUI 面板渲染测试。

面板结构对齐上游 wess09/AzurPilot PR #998 的 ``MeowfficerScorePanel``，
这里锁住「字段缺失不炸、加权口径不渲染 x+y、推断项有标注」这几条容易回归的行为。
数据统一走 ``evaluate`` + ``to_payload``，保证测试用的是真实契约而不是手写 dict。

最后一组是源码级落位锁与 i18n 完整性检查：面板挂在哪个页面、样式注册在哪、
五种语言的键是否齐全，都是单测跑不起整页渲染时最容易悄悄回归的地方。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from module.meowfficer.score import evaluate
from module.meowfficer.score_report import (
    panel_tier_class,
    render_panel,
    split_hit,
    to_payload,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 面板文案的桩：只保留占位符替换行为，文案内容与界面无关
LABELS = {
    'EmptyTitle': '还没跑过评分任务',
    'EmptyHint': '在「工具+ → 指挥喵评分」运行一次任务',
    'Summary': '共 {count} 只 · 生成时间 {time}',
    'Score': '参考分',
    'Maxed': '已满级',
    'Fixed': '已指定',
    'AxisX': 'X（彩天赋）',
    'AxisY': 'Y（有用普通）',
    'NoHits': '无命中',
    'OtherRubrics': '其他口径（{count}）',
    'Source': '依据：{source}',
    'Screenshot': '来源截图：{name}',
    'PointsSpent': '消耗 {count} 点',
    'Inferred': '推断条目，请核对',
}


def tr(key, **kwargs):
    """翻译桩：未知 key 原样返回，便于断言。"""
    return LABELS.get(key, key).format(**kwargs) if kwargs else LABELS.get(key, key)


def payload_of(talents, cat=None, name='sample'):
    """用真实评分链产出面板数据。"""
    return to_payload([(name, evaluate(talents, cat=cat))], generated_at='2026-09-21 12:00:00')


class TierClassTests(unittest.TestCase):
    """档位配色与上游 tierStyles 同序，先匹配到的关键词优先。"""

    def test_keyword_mapping(self):
        self.assertEqual(panel_tier_class('完美猫'), 'is-perfect')
        self.assertEqual(panel_tier_class('准毕业（偏上）'), 'is-near')
        self.assertEqual(panel_tier_class('毕业级'), 'is-graduate')
        self.assertEqual(panel_tier_class('过渡可用'), 'is-transition')
        self.assertEqual(panel_tier_class('零食（建议喂掉）'), 'is-snack')
        self.assertEqual(panel_tier_class('不适合低耗'), 'is-unsuitable')
        self.assertEqual(panel_tier_class('雷暴优秀'), 'is-thunder')

    def test_unknown_tier_is_neutral(self):
        self.assertEqual(panel_tier_class(''), 'is-other')
        self.assertEqual(panel_tier_class('说不清'), 'is-other')


class SplitHitTests(unittest.TestCase):
    """命中标签形如「雷击长·潜艇 Lv3」，等级要单独拆成徽章。"""

    def test_splits_level(self):
        self.assertEqual(split_hit('炮击新手·主力 Lv2'), ('炮击新手·主力', 2))

    def test_without_level(self):
        self.assertEqual(split_hit('侵略如火'), ('侵略如火', 0))


class RenderPanelTests(unittest.TestCase):
    """面板正文：空状态与卡片列表。"""

    def test_empty_payload_renders_empty_state(self):
        html = render_panel({}, tr)
        self.assertIn('meow-empty', html)
        self.assertIn('还没跑过评分任务', html)
        self.assertIn('工具+ → 指挥喵评分', html)

    def test_none_payload_renders_empty_state(self):
        self.assertIn('meow-empty', render_panel(None, tr))

    def test_cat_card_contains_tier_score_and_axes(self):
        payload = payload_of(['侵略如火', '新晋指挥官·战列', '炮击新手·主力'], cat='克雷喵')
        html = render_panel(payload, tr)
        self.assertIn('meow-cats', html)
        self.assertIn('克雷喵', html)
        self.assertIn('meow-tier', html)
        self.assertIn('参考分', html)
        self.assertIn('X（彩天赋）', html)
        self.assertIn('Y（有用普通）', html)
        # 彩天赋走描金标签，普通命中走等级徽章
        self.assertIn('meow-hit is-special', html)
        self.assertIn('meow-level', html)
        self.assertIn('来源截图：sample', html)

    def test_other_rubrics_are_collapsed(self):
        payload = payload_of(['侵略如火', '狼群之首', '新晋指挥官·潜艇'], cat='克雷喵')
        html = render_panel(payload, tr)
        self.assertIn('meow-others', html)
        self.assertIn('其他口径（', html)

    def test_torpedo_rubric_skips_formula(self):
        """雷暴口径是加权点制，后端不给 x/y，不能渲染成 x + y = 0 + 0.0。

        其他口径（水面/低耗）本来就可能出现 ``0 + 0.0``，所以断言要限定在
        雷暴那一块里，不能拿整页 HTML 做否定断言。
        """
        payload = payload_of(['水雷魂', '其疾如风'], cat='克雷喵')
        html = render_panel(payload, tr)
        start = html.index('雷暴猫')
        torpedo = html[start:html.index('低耗猫', start)]
        self.assertIn('加权命中', torpedo)
        self.assertNotIn('meow-formula', torpedo)

    def test_inferred_talent_is_marked(self):
        payload = payload_of(['侵略如火'], cat='克雷喵')
        payload['cats'][0]['talents'][0]['inferred'] = True
        html = render_panel(payload, tr)
        self.assertIn('is-inferred', html)
        self.assertIn('推断条目，请核对', html)

    def test_maxed_cat_shows_flag(self):
        payload = payload_of(['侵略如火', '新晋指挥官·战列', '炮击新手·主力', '装填新手·战列',
                              '新手整备士'], cat='克雷喵')
        payload['cats'][0]['maxed'] = True
        self.assertIn('已满级', render_panel(payload, tr))


class MeowfficerScorePanelWiringTests(unittest.TestCase):
    """源码级落位锁：面板挂在哪、样式注册在哪。

    单测跑不起整页 PyWebIO 渲染，用文本断言兜住「面板没被挂上 / 样式没被注册」
    这类只在真机才暴露的回归。
    """

    def _read(self, *parts):
        return (PROJECT_ROOT.joinpath(*parts)).read_text(encoding='utf-8')

    def test_panel_is_attached_on_the_task_page_before_config_groups(self):
        source = self._read('module', 'webui', 'app_overview.py')
        self.assertIn('task == "MeowfficerScore"', source)
        panel_call = source.index('self.put_meowfficer_score_panel()')
        config_loop = source.index('deep_iter(self.ALAS_ARGS[task], depth=1)')
        # 面板排在参数卡之前，与上游 PR #998 的面板位置一致
        self.assertLess(panel_call, config_loop)

    def test_mixin_is_registered_in_alas_gui(self):
        source = self._read('module', 'webui', 'app.py')
        self.assertIn('from module.webui.app_meowfficer_score import MeowfficerScoreMixin', source)
        self.assertIn('MeowfficerScoreMixin,', source)

    def test_stylesheet_is_registered_and_reinjected_on_theme_switch(self):
        self.assertTrue(
            PROJECT_ROOT.joinpath('assets', 'gui', 'css', 'meowfficer-score-alas.css').is_file()
        )
        self.assertIn('meowfficer-score-alas', self._read('module', 'webui', 'utils.py'))
        shell = self._read('module', 'webui', 'app_shell.py')
        # 切主题会清掉所有 alas-css-* 的 <style>，必须显式重注入
        self.assertIn('injected_styles.discard(filepath_css("meowfficer-score-alas"))', shell)
        self.assertIn('add_css_files((filepath_css("meowfficer-score-alas"),))', shell)

    def test_every_theme_defines_the_panel_variables(self):
        for name in ('light-alas.css', 'dark-alas.css', 'advanced-material-alas.css',
                     'transparent-alas.css'):
            with self.subTest(theme=name):
                css = self._read('assets', 'gui', 'css', name)
                self.assertIn('--meow-card-bg', css)
                self.assertIn('--meow-perfect', css)


class MeowfficerScoreI18nTests(unittest.TestCase):
    """锁住 Gui.MeowfficerScore 的 5 语言文案。

    文案只写进 i18n JSON 是不够的：键必须声明在 gui.yaml，否则下一次
    config_updater 会把它整体抹掉；同时 ``t()`` 会对字符串执行 ``.format()``，
    占位符写错会在渲染界面时抛 KeyError。
    """

    LANGUAGES = ('zh-CN', 'en-US', 'ja-JP', 'zh-TW', 'zh-MIAO')
    KEYS = (
        'Title', 'Refresh', 'Refreshing', 'OpenReport', 'EmptyTitle', 'EmptyHint', 'Summary',
        'Broken', 'Score', 'Maxed', 'Fixed', 'AxisX', 'AxisY', 'NoHits', 'OtherRubrics',
        'Source', 'Screenshot', 'PointsSpent', 'Inferred', 'Disclaimer',
    )
    # 渲染器实际用到的占位符，必须原样出现在对应文案里
    PLACEHOLDERS = {
        'Summary': ('{count}', '{time}'),
        'OtherRubrics': ('{count}',),
        'Source': ('{source}',),
        'Screenshot': ('{name}',),
        'PointsSpent': ('{count}',),
    }

    def _group(self, lang):
        data = json.loads(
            PROJECT_ROOT.joinpath('module', 'config', 'i18n', f'{lang}.json').read_text(encoding='utf-8')
        )
        return data['Gui']['MeowfficerScore']

    def test_all_languages_have_full_key_set(self):
        for lang in self.LANGUAGES:
            with self.subTest(lang=lang):
                group = self._group(lang)
                missing = [key for key in self.KEYS if not group.get(key)]
                self.assertEqual(missing, [])

    def test_keys_are_declared_in_gui_yaml(self):
        """键没在 gui.yaml 声明的话，下一次 config_updater 会把翻译整段抹掉。"""
        yaml_lines = PROJECT_ROOT.joinpath(
            'module', 'config', 'argument', 'gui.yaml'
        ).read_text(encoding='utf-8').splitlines()
        block = yaml_lines[yaml_lines.index('MeowfficerScore:'):]
        declared = set()
        for line in block[1:]:
            if line and not line.startswith(' '):
                break
            if line.strip():
                declared.add(line.strip().rstrip(':'))
        self.assertEqual([key for key in self.KEYS if key not in declared], [])

    def test_translations_are_not_placeholder_paths(self):
        """生成器给新键的默认值就是键路径本身，忘了翻译会以 Gui. 开头。"""
        for lang in self.LANGUAGES:
            with self.subTest(lang=lang):
                group = self._group(lang)
                untranslated = [k for k, v in group.items() if str(v).startswith('Gui.')]
                self.assertEqual(untranslated, [])

    def test_format_placeholders_survive_translation(self):
        for lang in self.LANGUAGES:
            for key, expected in self.PLACEHOLDERS.items():
                with self.subTest(lang=lang, key=key):
                    value = self._group(lang)[key]
                    for placeholder in expected:
                        self.assertIn(placeholder, value)
                    # 没有多余的花括号：t() 会 .format()，多余的会抛 KeyError
                    self.assertEqual(value.count('{'), value.count('}'))
                    self.assertEqual(value.count('{'), len(expected))


class ReportHtmlPathTests(unittest.TestCase):
    """HTML 报告路径解析：只认 log/ 目录里的那一个文件。

    这是「查看完整报告」的安全边界 —— 配置里的 ReportPath 是用户可改的，
    不能因为改成一个别的路径就把任意文件通过 HTTP 读出去。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / 'log').mkdir()
        (self.root / 'config').mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _resolve(self, report_value):
        from module.webui import app_meowfficer_score

        with patch('module.webui.log_export.get_project_root', return_value=self.root), \
                patch('module.config.utils.filepath_config', return_value=self.root / 'alas.json'), \
                patch('module.config.utils.read_file',
                      return_value={'MeowfficerScore': {'ReportPath': report_value}}):
            return app_meowfficer_score.report_html_path('alas')

    def test_returns_report_inside_log(self):
        report = self.root / 'log' / 'meowfficer_score.html'
        report.write_text('<html></html>', encoding='utf-8')
        self.assertEqual(self._resolve('./log/meowfficer_score.md'), report.resolve())

    def test_missing_file_returns_none(self):
        self.assertIsNone(self._resolve('./log/meowfficer_score.md'))

    def test_report_outside_log_is_rejected(self):
        outside = self.root / 'config' / 'secret.html'
        outside.write_text('<html>secret</html>', encoding='utf-8')
        self.assertIsNone(self._resolve('./config/secret.md'))

    def test_path_traversal_is_rejected(self):
        outside = self.root / 'secret.html'
        outside.write_text('<html>secret</html>', encoding='utf-8')
        self.assertIsNone(self._resolve('./log/../secret.md'))

    def test_empty_report_path_falls_back_to_default(self):
        report = self.root / 'log' / 'meowfficer_score.html'
        report.write_text('<html></html>', encoding='utf-8')
        self.assertEqual(self._resolve(''), report.resolve())


class ReportRouteTests(unittest.TestCase):
    """报告路由的状态码与 Content-Type（只挂这一条路由，避免拉起整页依赖）。"""

    def setUp(self):
        from module.webui.fake_pil_module import remove_fake_pil_module

        remove_fake_pil_module()

    def _client(self):
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.testclient import TestClient

        from module.webui.api import api_meowfficer_report

        app = Starlette(routes=[
            Route('/api/reports/meowfficer', api_meowfficer_report),
            Route('/api/reports/meowfficer/{instance}', api_meowfficer_report),
        ])
        return TestClient(app)

    def test_invalid_instance_is_rejected(self):
        response = self._client().get('/api/reports/meowfficer', params={'instance': '../x'})
        self.assertEqual(response.status_code, 400)

    def test_missing_report_is_404(self):
        with patch('module.webui.app_meowfficer_score.report_html_path', return_value=None), \
                patch('module.webui.api.validate_instance', return_value='alas'):
            response = self._client().get('/api/reports/meowfficer', params={'instance': 'alas'})
        self.assertEqual(response.status_code, 404)

    def test_existing_report_is_served_as_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'report.html'
            report.write_text('<html><body>ok</body></html>', encoding='utf-8')
            with patch('module.webui.app_meowfficer_score.report_html_path', return_value=report), \
                    patch('module.webui.api.validate_instance', return_value='alas'):
                response = self._client().get('/api/reports/meowfficer', params={'instance': 'alas'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
        self.assertIn('ok', response.text)

    def test_path_form_works_without_query(self):
        """远控 P2P 代理会剥掉 query string，path 形式必须同样可用。"""
        with patch('module.webui.app_meowfficer_score.report_html_path', return_value=None), \
                patch('module.webui.api.validate_instance', return_value='alas'):
            response = self._client().get('/api/reports/meowfficer/alas')
        self.assertEqual(response.status_code, 404)


class DaemonOverviewLogVisibilityTests(unittest.TestCase):
    """「指挥喵评分」页去掉日志内容区，但保留顶部日志工具栏。

    ``use_scope`` 对不存在的 scope 会在 ROOT 下创建孤儿容器（仓库里踩过这个坑），
    所以少渲染一个 scope 就必须把对应的 ``use_scope`` 与后台日志任务一并挡掉；
    容器行/列模板是写死行数的，也要一并改掉，否则空出来的那一行会把工具栏拉成 1fr。
    这里用 Mock 断言实际调用，比源码文本断言更能兜住「漏挡一处」。
    """

    def _render(self, task, is_mobile=False):
        from unittest.mock import MagicMock, Mock

        from module.webui import app_overview

        scopes, targeted, scripts = [], [], []

        def put_scope(name, *args, **kwargs):
            scopes.append(name)
            return Mock()

        def use_scope(name, *args, **kwargs):
            targeted.append(name)
            return MagicMock()

        fake = Mock()
        fake.is_mobile = is_mobile
        fake.alas_name = 'alas'
        fake.ALAS_ARGS = {task: {}}

        raw = app_overview.OverviewMixin.alas_daemon_overview.__wrapped__
        with patch.object(app_overview, 'put_scope', put_scope), \
                patch.object(app_overview, 'use_scope', use_scope), \
                patch.object(app_overview, 'put_none', Mock()), \
                patch.object(app_overview, 'put_html', Mock()), \
                patch.object(app_overview, 'put_text', Mock()), \
                patch.object(app_overview, 'put_button', Mock()), \
                patch.object(app_overview, 'run_js', lambda script: scripts.append(script)), \
                patch.object(app_overview, 'RichLog', Mock()), \
                patch.object(app_overview, 'BinarySwitchButton', Mock()):
            raw(fake, task)
        return scopes, targeted, scripts

    def test_score_page_drops_log_content_but_keeps_toolbar(self):
        scopes, targeted, scripts = self._render('MeowfficerScore')
        # 日志内容区整块不渲染，对应的 use_scope 也不能碰
        self.assertNotIn('log', scopes)
        self.assertNotIn('log', targeted)
        # 顶部日志工具栏保留
        self.assertIn('log-bar', scopes)
        self.assertIn('log-bar-btns', scopes)
        self.assertIn('log-bar', targeted)
        # 调度条与参数区照旧
        self.assertIn('scheduler-bar', scopes)
        self.assertIn('groups', scopes)
        # 行模板与列模板都要改：否则空行会把工具栏拉成 1fr、两侧各留 1/8 空白
        joined = ' '.join(scripts)
        self.assertIn('grid-template-rows', joined)
        self.assertIn('grid-template-columns', joined)

    def test_score_page_mobile_drops_the_trailing_log_row(self):
        _, _, scripts = self._render('MeowfficerScore', is_mobile=True)
        joined = ' '.join(scripts)
        self.assertIn('grid-template-rows', joined)
        # 移动端本来就是单列铺满，不需要动列模板
        self.assertNotIn('grid-template-columns', joined)

    def test_other_tool_pages_keep_the_log(self):
        scopes, targeted, scripts = self._render('OcrBenchmark')
        self.assertIn('log', scopes)
        self.assertIn('log-bar', scopes)
        self.assertIn('log-bar', targeted)
        self.assertFalse(any('grid-template-rows' in script for script in scripts))
        self.assertFalse(any('grid-template-columns' in script for script in scripts))


if __name__ == '__main__':
    unittest.main()
