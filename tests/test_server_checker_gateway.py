"""ServerChecker 游戏网关后备查询（本地适配）的无网络测试。

公共 API（sc.shiratama.cn）不可用时，ServerChecker 应回退为
按服务器名直连游戏网关（module.server_status）查询状态；
两种来源均失败才进入既有的快速重试流程。
"""

import unittest
from json import JSONDecodeError
from unittest.mock import patch

import requests

from module.exception import ScriptError
from module.server_checker import ServerChecker
from module.server_status import GatewayServer, ServerStatusQueryError

OK_SERVER = GatewayServer(1, '莱茵演习', 0, 0, 1)


class GatewayFallbackTestCase(unittest.TestCase):
    """在 API 与网关均被 mock 的环境下检验后备查询行为。"""

    def setUp(self) -> None:
        session_patcher = patch('module.server_checker.requests.Session')
        self.session_cls = session_patcher.start()
        self.addCleanup(session_patcher.stop)
        # ServerChecker 内部 requests.Session() 拿到的实例
        self.session = self.session_cls.return_value

    def _api_connection_error(self) -> None:
        """让公共 API 的所有 POST 请求连接失败。"""
        self.session.post.side_effect = requests.exceptions.ConnectionError('api down')

    def _api_bad_json(self) -> None:
        """让公共 API 返回无法解析为 JSON 的响应。"""
        response = self.session.post.return_value
        response.text = 'not json'
        response.json.side_effect = JSONDecodeError('invalid', 'not json', 0)

    def _api_ok(self) -> None:
        """让公共 API 返回正常的可用状态。"""
        response = self.session.post.return_value
        response.status_code = 200
        response.text = '{}'
        response.json.return_value = {'state': 0, 'last_update': 1}

    def _build_checker(self, gateway_return=None, gateway_side_effect=None) -> ServerChecker:
        """构造 API 不可用、网关行为可控的 ServerChecker。

        构造过程即触发一次 check_now → _load_server，断言直接针对
        构造完成后的状态进行。fast_retry 须由调用方在外层自行打桩
        （便于断言其调用情况），此处仅接管 API 与网关。
        """
        self._api_connection_error()
        query_patcher = patch(
            'module.server_checker.query_region',
            return_value=gateway_return,
            side_effect=gateway_side_effect,
        )
        query_patcher.start()
        self.addCleanup(query_patcher.stop)
        return ServerChecker('cn_android-0')

    def test_gateway_used_when_api_connection_fails(self):
        """API 连接失败时按服务器名走网关后备，并取得可用状态。"""
        with patch.object(ServerChecker, 'fast_retry', return_value=True) as retry:
            checker = self._build_checker(gateway_return=[OK_SERVER])

        self.assertTrue(checker.is_available())
        retry.assert_not_called()

    def test_gateway_reports_maintenance(self):
        """网关返回维护状态时记录不可用，且不再走快速重试。"""
        gateway = GatewayServer(1, '莱茵演习', 1, 0, 1)
        with patch.object(ServerChecker, 'fast_retry', return_value=True) as retry:
            checker = self._build_checker(gateway_return=[gateway])

        self.assertFalse(checker.is_available())
        retry.assert_not_called()

    def test_gateway_failure_falls_back_to_fast_retry(self):
        """网关也无法访问时，回到既有的快速重试流程。"""
        with patch.object(ServerChecker, 'fast_retry', return_value=True) as retry:
            checker = self._build_checker(
                gateway_side_effect=ServerStatusQueryError('timeout'))

        self.assertTrue(checker.is_available())
        retry.assert_called()

    def test_gateway_name_mismatch_falls_back(self):
        """网关列表中找不到该服务器名时，同样回到快速重试流程。"""
        gateway = GatewayServer(1, '不存在的服务器', 0, 0, 1)
        with patch.object(ServerChecker, 'fast_retry', return_value=True) as retry:
            checker = self._build_checker(gateway_return=[gateway])

        self.assertTrue(checker.is_available())
        retry.assert_called()

    def test_unknown_gateway_state_falls_back(self):
        """网关返回未知状态文本时视为后备失败，回到快速重试流程。"""
        gateway = GatewayServer(1, '莱茵演习', 77, 0, 1)
        with patch.object(ServerChecker, 'fast_retry', return_value=True) as retry:
            checker = self._build_checker(gateway_return=[gateway])

        self.assertTrue(checker.is_available())
        retry.assert_called()

    def test_gateway_region_selected_from_local_key(self):
        """本地 region 键 cn_android 应映射到首个网关入口 'cn'。"""
        with patch('module.server_checker.query_region') as query, \
                patch.object(ServerChecker, 'fast_retry', return_value=True):
            self._api_connection_error()
            ServerChecker('cn_android-0')

        self.assertEqual(query.call_args_list[0][0][0], 'cn')

    def test_json_error_with_gateway_success(self):
        """API 返回非 JSON 时先尝试网关后备，成功则不抛 ScriptError。"""
        self._api_bad_json()
        with patch('module.server_checker.query_region', return_value=[OK_SERVER]), \
                patch.object(ServerChecker, 'fast_retry', return_value=True):
            checker = ServerChecker('cn_android-0')

        self.assertTrue(checker.is_available())

    def test_json_error_with_gateway_failure_raises(self):
        """API 返回非 JSON 且网关失败时，_load_server 维持原有的 ScriptError 行为。"""
        self._api_ok()
        with patch('module.server_checker.query_region', return_value=[OK_SERVER]), \
                patch.object(ServerChecker, 'fast_retry', return_value=True):
            checker = ServerChecker('cn_android-0')

        self._api_bad_json()
        with patch(
            'module.server_checker.query_region',
            side_effect=ServerStatusQueryError('network_error'),
        ), patch.object(ServerChecker, 'fast_retry', return_value=True):
            with self.assertRaises(ScriptError):
                checker._load_server()


if __name__ == '__main__':
    unittest.main()
