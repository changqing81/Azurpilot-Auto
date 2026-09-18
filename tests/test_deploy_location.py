import unittest
from unittest.mock import patch

from deploy import config as deploy_config
from deploy import geo
from deploy.Windows import config as windows_config


class TestIp9Location(unittest.TestCase):
    @patch('deploy.geo.requests.get')
    def test_returns_lowercase_country_code(self, get):
        response = get.return_value
        response.json.return_value = {'data': {'country_code': 'CN'}}

        self.assertEqual(geo.get_country_code(), 'cn')

        get.assert_called_once_with(
            geo.IP9_LOCATION_URL,
            timeout=5,
            headers={'User-Agent': 'AzurPilot'},
        )
        response.raise_for_status.assert_called_once_with()

    @patch('deploy.geo.requests.get')
    def test_invalid_response_returns_none(self, get):
        response = get.return_value
        response.json.return_value = {'data': {}}

        self.assertIsNone(geo.get_country_code())


class TestDeployLocation(unittest.TestCase):
    """更新源选择：`auto` 哨兵按地区解析，具体地址一律照用不改写。

    deploy/config.py（WebUI 侧）与 deploy/Windows/config.py（安装器侧）必须保持
    同一套语义，因此所有用例对两个模块各跑一遍。
    """

    def make_config(self, config_module, repository=None):
        instance = object.__new__(config_module.DeployConfig)
        instance.config = {
            'Repository': repository or config_module.AUTO_REPOSITORY,
        }
        instance.Repository = instance.config['Repository']
        instance.Branch = 'master'
        instance._github_location_checked = False
        return instance

    def test_auto_uses_gitcode_in_china(self):
        for config_module in (deploy_config, windows_config):
            with self.subTest(config_module=config_module.__name__), patch.object(
                config_module, 'get_country_code', return_value='cn'
            ) as get_country_code:
                config = self.make_config(config_module)

                config.config_redirect()

                # 解析结果只落在属性上，不写回配置文件（换网络后下次启动会重新判断）
                self.assertEqual(config.config['Repository'], config_module.AUTO_REPOSITORY)
                self.assertEqual(config.Repository, config_module.CN_REPOSITORY)
                self.assertFalse(config.GitOverCdn)
                get_country_code.assert_called_once_with()

    def test_auto_keeps_github_outside_china_and_only_checks_once(self):
        for config_module in (deploy_config, windows_config):
            with self.subTest(config_module=config_module.__name__), patch.object(
                config_module, 'get_country_code', return_value='us'
            ) as get_country_code:
                config = self.make_config(config_module)

                config.config_redirect()
                config.config_redirect()

                self.assertEqual(config.config['Repository'], config_module.AUTO_REPOSITORY)
                self.assertEqual(config.Repository, config_module.GITHUB_REPOSITORY)
                self.assertFalse(config.GitOverCdn)
                get_country_code.assert_called_once_with()

    def test_auto_failed_lookup_keeps_github(self):
        for config_module in (deploy_config, windows_config):
            with self.subTest(config_module=config_module.__name__), patch.object(
                config_module, 'get_country_code', return_value=None
            ):
                config = self.make_config(config_module)

                config.config_redirect()

                self.assertEqual(config.Repository, config_module.GITHUB_REPOSITORY)
                self.assertFalse(config.GitOverCdn)

    def test_auto_shorthand_is_case_insensitive(self):
        for config_module in (deploy_config, windows_config):
            with self.subTest(config_module=config_module.__name__), patch.object(
                config_module, 'get_country_code', return_value='cn'
            ):
                config = self.make_config(config_module, 'Auto')

                config.config_redirect()

                # 写法被规范成小写并落盘，避免用户填 'AUTO' 时哨兵失效
                self.assertEqual(config.config['Repository'], config_module.AUTO_REPOSITORY)
                self.assertEqual(config.Repository, config_module.CN_REPOSITORY)

    def test_custom_repository_does_not_query_location(self):
        for config_module in (deploy_config, windows_config):
            with self.subTest(config_module=config_module.__name__), patch.object(
                config_module, 'get_country_code'
            ) as get_country_code:
                config = self.make_config(config_module, 'https://github.com/example/custom')

                config.config_redirect()

                get_country_code.assert_not_called()
                self.assertEqual(config.Repository, 'https://github.com/example/custom')

    def test_upstream_repository_is_never_rewritten(self):
        """上游地址（wess09 / Maratrain）填什么就是什么，方便切回去做对比测试。"""
        for config_module in (deploy_config, windows_config):
            with self.subTest(config_module=config_module.__name__), patch.object(
                config_module, 'get_country_code', return_value='cn'
            ) as get_country_code:
                config = self.make_config(config_module, 'https://github.com/wess09/AzurPilot')

                config.config_redirect()

                get_country_code.assert_not_called()
                self.assertEqual(config.Repository, 'https://github.com/wess09/AzurPilot')

    def test_legacy_mirror_migrates_to_cdn(self):
        """历史 gitee / coding / gitcode 镜像地址统一迁移到旧 CDN 源。"""
        legacy = [
            'https://gitee.com/LmeSzinc/AzurLaneAutoScript',
            'https://gitcode.com/ddl2/AzurLaneAutoScript',
            'https://git.nanoda.work/git/AzurPilot',
            'https://git.nanoda.work',
        ]
        for config_module in (deploy_config, windows_config):
            for repository in legacy:
                with self.subTest(config_module=config_module.__name__, repository=repository):
                    config = self.make_config(config_module, repository)

                    config.config_redirect()

                    # config 里记录迁移结果，实际使用地址落到 CDN 回退源
                    self.assertEqual(config.config['Repository'], config_module.GIT_OVER_CDN_REPOSITORY)
                    self.assertEqual(config.Repository, config_module.GIT_OVER_CDN_FALLBACK_REPOSITORY)
                    self.assertTrue(config.GitOverCdn)

    def test_cn_and_global_shorthand(self):
        for config_module in (deploy_config, windows_config):
            for shorthand, attr in (
                ('cn', 'CN_REPOSITORY'),
                ('global', 'GITHUB_REPOSITORY'),
                ('CN', 'CN_REPOSITORY'),
                ('Global', 'GITHUB_REPOSITORY'),
            ):
                with self.subTest(config_module=config_module.__name__, shorthand=shorthand):
                    config = self.make_config(config_module, shorthand)

                    config.config_redirect()

                    self.assertEqual(config.Repository, getattr(config_module, attr))


if __name__ == '__main__':
    unittest.main()
