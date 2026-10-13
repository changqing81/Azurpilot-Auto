"""推送通知的代理失效兜底回归测试。

线上故障形态（2026-10-12 18:48 起）：
进程环境里 HTTP_PROXY=http://127.0.0.1:10808，代理软件退出后端口不再监听，
requests 抛 ProxyError，onepush 原版 Provider.request 静默吞掉异常返回 None，
handle_notify 只报「未收到推送服务器的响应」，所有企业微信推送全部失败。

修复：Provider.request 捕获 ProxyError 后忽略环境变量代理直连重试一次。
本文件验证该 failover 行为。
"""

import unittest
from unittest.mock import patch

import requests
from requests import Response
from requests.exceptions import ProxyError, SSLError

import module.notify.notify as notify_module
from onepush.core import Provider


def _make_response(status_code=200):
    resp = Response()
    resp.status_code = status_code
    return resp


class ProviderRequestTimeoutTests(unittest.TestCase):
    """原有 timeout 注入行为不能丢。"""

    def test_timeout_injected(self):
        seen = {}

        def fake_request(self, method, url, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return _make_response()

        with patch.object(
            requests.Session, "request", autospec=True, side_effect=fake_request
        ):
            resp = Provider.request("post", "https://example.com/hook", data={})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen["timeout"], notify_module.PUSH_REQUEST_TIMEOUT)


class ProviderRequestProxyFailoverTests(unittest.TestCase):
    """代理不可用时直连重试。"""

    def test_proxy_error_falls_back_to_direct(self):
        calls = []

        def fake_request(self, method, url, **kwargs):
            trust_env = getattr(self, "trust_env", True)
            calls.append(trust_env)
            if trust_env:
                # 进程带着 HTTP_PROXY=127.0.0.1:10808，端口已死
                raise ProxyError(
                    "Unable to connect to proxy: "
                    "[WinError 10061] 由于目标计算机积极拒绝，无法连接。"
                )
            return _make_response(200)

        with patch.object(
            requests.Session, "request", autospec=True, side_effect=fake_request
        ):
            resp = Provider.request(
                "post",
                "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
                json={"msgtype": "text"},
            )

        self.assertEqual(calls.count(True), 1, "首次必须仍按原路径走环境代理")
        self.assertEqual(calls.count(False), 1, "代理失败后必须直连重试一次")
        self.assertIsNotNone(resp, "ProxyError 不应再表现为静默 None")
        self.assertEqual(resp.status_code, 200)

    def test_direct_retry_also_fails_returns_none(self):
        def fake_request(self, method, url, **kwargs):
            raise ProxyError("dead proxy")

        with patch.object(
            requests.Session, "request", autospec=True, side_effect=fake_request
        ):
            resp = Provider.request("post", "https://example.com/hook", data={})

        self.assertIsNone(resp)

    def test_ssl_error_retry_disabled_verify(self):
        seen = []

        def fake_request(self, method, url, **kwargs):
            seen.append(kwargs.get("verify", True))
            if len(seen) == 1:
                raise SSLError("bad handshake")
            return _make_response(200)

        with patch.object(
            requests.Session, "request", autospec=True, side_effect=fake_request
        ):
            resp = Provider.request("post", "https://example.com/hook", data={})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seen, [True, False], "SSL 失败后必须 verify=False 重试")

    def test_ssl_error_on_direct_retry_also_handled(self):
        state = {"n": 0}

        def fake_request(self, method, url, **kwargs):
            state["n"] += 1
            if getattr(self, "trust_env", True):
                raise ProxyError("proxy down")
            if state["n"] == 2:
                raise SSLError("bad handshake")
            return _make_response(200)

        with patch.object(
            requests.Session, "request", autospec=True, side_effect=fake_request
        ):
            resp = Provider.request("post", "https://example.com/hook", data={})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(state["n"], 3)


class HandleNotifyProxyFailoverTests(unittest.TestCase):
    """handle_notify 层：代理死掉后整条链路应恢复为成功。"""

    def test_handle_notify_survives_dead_proxy(self):
        calls = []

        def fake_request(self, method, url, **kwargs):
            trust_env = getattr(self, "trust_env", True)
            calls.append(trust_env)
            if trust_env:
                raise ProxyError("Unable to connect to proxy")
            return _make_response(200)

        with patch("module.notify.notify.get_notifier") as get_notifier, patch.object(
            requests.Session, "request", autospec=True, side_effect=fake_request
        ):
            from onepush.providers.wechatworkbot import WechatWorkBot

            notifier = WechatWorkBot()
            get_notifier.return_value = notifier

            ok = notify_module.handle_notify(
                "provider: wechatworkbot\nkey: https://example.com/hook",
                title="t",
                content="c",
            )

        self.assertTrue(ok)
        self.assertEqual(calls.count(False), 1)


if __name__ == "__main__":
    unittest.main()
