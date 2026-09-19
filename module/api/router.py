"""显式方法注册表；所有阻塞业务操作在工作线程执行。"""
import os
from dataclasses import dataclass
from typing import Callable

from module.api import protocol as p
from module.runtime.process_manager import ProcessManager


@dataclass(frozen=True)
class Method:
    params: type[p.Params]
    handler: Callable
    mutates: bool = False


class Router:
    def __init__(self, configs, runtime):
        self.configs, self.runtime = configs, runtime
        self.methods = {
            'system.ping': Method(p.Params, lambda _: {'pong': True}),
            'schema.get': Method(p.SchemaParams, lambda x: configs.schema(x.language)),
            'instances.list': Method(p.Params, lambda _: runtime.instances()),
            'instances.create': Method(p.CreateParams, lambda x: configs.create(x.name, x.source), True),
            'instances.delete': Method(p.RevisionParams, self.delete, True),
            'config.get': Method(p.InstanceParams, lambda x: configs.get(x.instance)),
            'config.patch': Method(p.PatchParams, lambda x: configs.patch(x.instance, x.revision, x.changes), True),
            'shop_strategy.validate': Method(p.ShopStrategyValidateParams, self.validate_shop_strategy),
            'overview.get': Method(p.InstanceParams, lambda x: runtime.overview(x.instance)),
            'scheduler.start': Method(p.InstanceParams, lambda x: runtime.start(x.instance), True),
            'scheduler.stop': Method(p.InstanceParams, lambda x: runtime.stop(x.instance), True),
            'tasks.run': Method(p.TaskParams, lambda x: runtime.start(x.instance, x.task), True),
            'logs.get': Method(p.LogsParams, lambda x: runtime.logs(x.instance, x.after)),
            'logs.requestExport': Method(p.LogsExportParams, self.request_log_export),
            'preview.capture': Method(p.InstanceParams, lambda x: runtime.capture(x.instance)),
            'statistics.refreshLoot': Method(p.InstanceParams, self.refresh_loot, True),
            'statistics.report': Method(p.StatisticsReportParams, self.statistics_report),
            'statistics.resources': Method(p.StatisticsParams, lambda x: runtime.statistics(x.instance, x.days, x.resource)),
            'settings.get': Method(p.Params, self.settings),
            'settings.patch': Method(p.DeployParams, self.save_settings, True),
            'startup.get': Method(p.InstanceParams, self.get_startup),
            'startup.set': Method(p.StartupParams, self.set_startup, True),
            'updater.status': Method(p.Params, lambda _: self.updates.status()),
            'updater.commits': Method(p.CommitsParams, lambda x: self.updates.commits(x.offset, x.limit)),
            'updater.fetch': Method(p.Params, lambda _: self.updates.start('fetch'), True),
            'updater.apply': Method(p.Params, lambda _: self.updates.start('apply'), True),
            'updater.cancel': Method(p.Params, lambda _: self.updates.cancel(), True),
        }

    @property
    def updates(self):
        from module.api.update_service import update_service
        return update_service

    def refresh_loot(self, params):
        from module.api.statistics_service import refresh_loot
        return refresh_loot(self.configs, params.instance)

    def validate_shop_strategy(self, params):
        """校验高级商店策略，禁止客户端指定任意执行上下文。"""
        self.configs.path(params.instance)
        if 'ShopAdvanced' not in self.configs.args.get(params.task, {}):
            raise p.ApiError('INVALID_PARAMS', '不支持的商店任务')
        from module.shop_strategy import validate_strategy

        # 显式检查返回诊断而不是抛出参数错误，编辑器才能标出行列位置。
        return validate_strategy(params.script)

    def statistics_report(self, params):
        from module.api.statistics_service import report
        return report(self.configs, params.instance, params.category, params.month, params.days, params.period)

    def request_log_export(self, params):
        """打包日志并签发一次性下载令牌；文件本体经 HTTP GET 流式取回。"""
        from module.api import log_export

        # configs.path 校验实例存在且名称安全（防路径穿越）
        self.configs.path(params.instance)
        instance = params.instance
        scope = str(params.scope) if params.scope else None
        if params.kind == 'error':
            info = log_export.describe_error_log_dir(scope or log_export.SCOPE_FULL)
            if info['bytes'] > log_export.EXPORT_MAX_BYTES:
                raise p.ApiError(
                    'INVALID_PARAMS',
                    f"错误日志共 {info['human_bytes']}，超过导出上限；请改用“仅文本”范围",
                )
            path = log_export.build_error_log_zip(scope or log_export.SCOPE_FULL)
            token = log_export.register_download(path, path.name, temp_file=True)
            return {'url': f'/api/v1/export/{token}', 'filename': path.name, 'expiresIn': 120, **info}

        info = log_export.describe_runtime_logs(instance, scope or '')
        if info['bytes'] > log_export.EXPORT_MAX_BYTES:
            raise p.ApiError(
                'INVALID_PARAMS',
                f"运行日志共 {info['human_bytes']}，超过导出上限；请按日期分段导出",
            )
        path, filename, temp = log_export.build_runtime_log_bundle(instance, scope or '')
        token = log_export.register_download(path, filename, temp_file=temp)
        return {'url': f'/api/v1/export/{token}', 'filename': filename, 'expiresIn': 120, **info}

    def dispatch(self, method, params):
        entry = self.methods.get(method)
        if entry is None:
            raise p.ApiError('METHOD_NOT_FOUND', '未知 API 方法')
        validated = entry.params.model_validate(params)
        if entry.mutates and os.environ.get('DEMO') == '1':
            raise p.ApiError('READ_ONLY', '演示模式下禁止修改配置或运行任务')
        return entry.handler(validated)

    def delete(self, params):
        with ProcessManager._get_lifecycle_lock(params.instance):
            manager = self.runtime.manager(params.instance)
            if manager.alive:
                raise p.ApiError('INSTANCE_RUNNING', '请先停止实例再删除')
            result = self.configs.delete(params.instance, params.revision)
            manager.run_id = None
            ProcessManager.remove_manager(params.instance)
            self.runtime.logs_cache.pop(params.instance, None)
            from module.runtime.preview import hub
            hub.discard(params.instance)
            return result

    def settings(self, _):
        from module.runtime.deploy_settings import deploy_settings_schema
        result = deploy_settings_schema(self.configs.translate)
        # 密码只写不读，前端留空表示保持原密码。
        for group in result['groups']:
            for field in group['fields']:
                if field['key'] == 'Password':
                    field['value'] = ''
                    field['type'] = 'password'
        return result

    def save_settings(self, params):
        from module.runtime.deploy_settings import save_deploy_settings
        values = dict(params.values)
        if values.get('Password') == '':
            values.pop('Password')
        with self.configs.lock:
            return save_deploy_settings({'values': values})

    def get_startup(self, params):
        from module.runtime.deploy_settings import get_startup_run
        self.configs.path(params.instance)
        return get_startup_run(params.instance)

    def set_startup(self, params):
        from module.runtime.deploy_settings import set_startup_run
        self.configs.path(params.instance)
        with self.configs.lock:
            return set_startup_run(params.instance, params.enabled)
