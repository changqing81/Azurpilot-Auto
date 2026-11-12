"""服务器检查：社区 API 未收录新服时应回退官方网关。

背景（2026-11-12 实测）：
社区状态 API sc.shiratama.cn 收录 69 个服务器，本地 VALID_SERVER_LIST 有 71 个，
差集正是「冬月计划」与「长弓计划」两个新服。对它们 /server/get_state 返回
404 {"detail":"Server name does not exist!"}。

老逻辑在 404 时只查本地列表，确认名字存在就**假定可用**（_push_state(True)），
后果是这两个服维护期间永远判为「可用」→ 不去等待 → 照样点游戏 → 画面推不动
→ 触发卡死检测 → 连续 3 次未处理异常 → 重启模拟器。

实测官方网关（cn，TCP 118.178.152.242:80）0.1 秒返回完整 30 服列表，
「长弓计划」id=28 / 「冬月计划」id=27 均在列，与本地列表同名可直接匹配。
因此 404 时优先用网关确认真实状态，网关也查不到才退回原有假定。
"""
import unittest
from unittest.mock import MagicMock, patch

from module.server_checker import ServerChecker


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('no json')
        return self._payload


def _make_checker():
    """构造检查器但不触发 __init__ 里的首次查询。"""
    checker = object.__new__(ServerChecker)
    checker._server = '长弓计划'
    checker._region = 'cn_android'
    checker._base = 'http://sc.shiratama.cn'
    checker._api = {'get_state': '/server/get_state'}
    checker._state = __import__('collections').deque(maxlen=2)
    checker._timestamp = 0
    checker._expired = 0
    checker._retry = False
    checker._recover = False
    checker._timer = MagicMock()
    return checker


class TestApi404FallsBackToGateway(unittest.TestCase):
    def test_uses_gateway_when_api_404(self):
        """404 时应先查网关；网关确认维护 → 判定为不可用。"""
        checker = _make_checker()
        with patch('module.server_checker.requests.Session') as session_cls, \
                patch.object(ServerChecker, '_load_gateway_server', return_value=True) as gw, \
                patch.object(checker, '_push_state') as push:
            session_cls.return_value.post.return_value = _FakeResponse(404)
            checker._load_server()
        gw.assert_called_once()
        # 网关接管后不应再走「假定可用」分支
        push.assert_not_called()

    def test_gateway_records_maintenance_state(self):
        """网关反馈维护中时，状态必须记为不可用（核心回归点）。"""
        checker = _make_checker()
        with patch('module.server_checker.requests.Session') as session_cls, \
                patch.object(ServerChecker, '_query_gateway') as query:
            session_cls.return_value.post.return_value = _FakeResponse(404)
            query.return_value = MagicMock(status='maintenance')
            checker._load_server()
        self.assertFalse(checker._state[-1], '维护中必须记为 False')

    def test_gateway_records_available_state(self):
        checker = _make_checker()
        with patch('module.server_checker.requests.Session') as session_cls, \
                patch.object(ServerChecker, '_query_gateway') as query:
            session_cls.return_value.post.return_value = _FakeResponse(404)
            query.return_value = MagicMock(status='normal')
            checker._load_server()
        self.assertTrue(checker._state[-1])

    def test_falls_back_to_local_assumption_when_gateway_fails(self):
        """网关也查不到时，退回原有「本地列表存在即可用」的保守假定。"""
        checker = _make_checker()
        with patch('module.server_checker.requests.Session') as session_cls, \
                patch.object(ServerChecker, '_load_gateway_server', return_value=False), \
                patch.object(ServerChecker, '_server_in_local_list', return_value=True):
            session_cls.return_value.post.return_value = _FakeResponse(404)
            checker._load_server()
        self.assertTrue(checker._state[-1])

    def test_unknown_server_still_raises(self):
        """网关查不到且不在本地列表 → 保持原有 ScriptError。"""
        from module.exception import ScriptError

        checker = _make_checker()
        checker._server = '不存在的服'
        with patch('module.server_checker.requests.Session') as session_cls, \
                patch.object(ServerChecker, '_load_gateway_server', return_value=False), \
                patch.object(ServerChecker, '_server_in_local_list', return_value=False):
            session_cls.return_value.post.return_value = _FakeResponse(404)
            with self.assertRaises(ScriptError):
                checker._load_server()


if __name__ == '__main__':
    unittest.main()
