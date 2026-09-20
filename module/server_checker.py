"""游戏服务器状态检查器。

通过外部 API 查询碧蓝航线各服务器（CN/EN/JP/TW）的在线状态。
在任务调度前检查服务器是否可用，避免在维护期间执行无效操作。

公共 API 不可用时，回退为直连游戏网关查询（module.server_status），
两种来源均失败才进入快速重试与退避流程。
状态检查结果通过队列缓存，支持定时刷新和即时查询。
"""

from collections import deque
from json import JSONDecodeError

import requests

from module.base.timer import Timer
from module.config.server import VALID_SERVER_LIST as server_list
from module.exception import ScriptError
from module.logger import logger
from module.server_status import GatewayServer, ServerStatusQueryError, query_region

# 本地 VALID_SERVER_LIST 的 region 键 → module.server_status 的网关 region 键。
# 同一物理服务器在安卓 TCP / iOS / 渠道服多个网关入口均有条目，
# 按顺序尝试，任一入口命中即可。
GATEWAY_REGION_MAP: dict[str, tuple[str, ...]] = {
    'cn_android': ('cn', 'cn_ios', 'cn_channel'),
    'cn_ios': ('cn_ios', 'cn_channel'),
    'cn_channel': ('cn_channel', 'cn_ios'),
    'en': ('en',),
    'jp': ('jp',),
    'tw': ('tw',),
}

# 游戏网关状态文本 → 本调度器的可用性语义。
# full / reg_full 表示满员但可登录排队，与公共 API 的判定口径保持一致。
GATEWAY_AVAILABLE_STATES = {'normal', 'full', 'reg_full'}
GATEWAY_UNAVAILABLE_STATES = {'maintenance', 'unopened'}


class ServerChecker:
    """游戏服务器状态检查器。

    通过 HTTP API 查询指定服务器的在线状态，支持：
    - 单服务器状态查询
    - 全服务器状态查询
    - 服务器列表获取

    Attributes:
        _server (str): 目标服务器标识，如 'cn'、'en'、'jp'、'tw'，或 'disabled'。
        _region (str): 目标服务器所属的本地 region 键（'cn_android' 等），
            'disabled' 时为空字符串，用于网关后备查询选择网关入口。
        _state (deque): 状态历史队列（最大长度 2），用于状态变化检测。
        _recover (bool): 服务器是否从不可用状态恢复。
        _retry (bool): 是否需要重试。
    """

    def __init__(self, server: str) -> None:
        # 注意：API 提供方未部署与本域名匹配的 TLS 证书（https 握手失败），
        # 只能使用 http。查询仅包含服务器名称、不携带任何敏感数据，
        # 响应仅影响任务调度判断；并通过下方严格的响应结构校验弥补完整性。
        self._base: str = 'http://sc.shiratama.cn'
        self._api: dict = {
            'get_state': '/server/get_state',           # POST 请求
            'get_all_state': '/server/get_all_state',   # POST 请求
            'list': '/server/list'                      # GET 请求
        }

        self._region: str = ''
        if server != 'disabled':
            server = server.split('-')
            self._region = server[0]
            server = server_list[server[0]][int(server[-1])]

        self._server: str = server
        self._state: deque = deque(maxlen=2)
        self._timestamp: int = 0
        self._expired: int = 0
        self._timer: Timer = Timer(0)

        # 状态标志
        self._recover: bool = False
        self._retry: bool = False

        self.check_now()

    def _load_server(self) -> None:
        """
        通过 API 获取服务器状态。

        公共 API 暂时不可用（连接失败或响应非 JSON）时，改为直连游戏网关。
        两种来源均无法取得状态才走既有的快速重试；API 出现结构异常时
        抛出 ScriptError，由顶层临时禁用检查器。
        """
        if self._server == 'disabled':
            self._state.append(True)
            return

        try:
            session = requests.Session()
            session.trust_env = False
            resp = session.post(
                url=f'{self._base}{self._api["get_state"]}',
                params={
                    'server_name': self._server
                },
                timeout=15
            )
            if resp.status_code == 200:
                j = resp.json()
                # 响应结构校验：防止 API 变更或被劫持后返回异常数据误导调度
                if not isinstance(j, dict) \
                        or not isinstance(j.get('state'), int) \
                        or not isinstance(j.get('last_update'), int):
                    raise ScriptError(
                        f'Invalid response structure. Response is {resp.text}'
                    )
                if j['state'] != 1:
                    self._state.append(True)
                    logger.info(f'[服务器检查] 服务器 "{self._server}" 可用。')
                else:
                    self._state.append(False)
                    logger.info(f'[服务器检查] 服务器 "{self._server}" 维护中。')

                # 检查 API 服务端是否已停止更新
                if j['last_update'] > self._timestamp:
                    self._timestamp = j['last_update']
                    self._expired = 0
                else:
                    self._expired += 1
                    if self._expired > 3:
                        logger.warning(f'[服务器检查] 时间戳 {self._timestamp} 已3次未更新。')
            elif resp.status_code == 404:
                # API 数据库可能未收录新增服务器（如"长弓计划"），
                # 检查本地服务器列表确认该服务器是否真实存在
                if self._server_in_local_list():
                    self._state.append(True)
                    logger.info(f'[服务器检查] 服务器 "{self._server}" 可用（本地已验证，API未知）。')
                else:
                    self._state.append(False)
                    raise ScriptError(f'Server "{self._server}" does not exist!')
            else:
                raise ScriptError(f'Get status_code {resp.status_code}. Response is {resp.text}')
        except (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout) as e:
            logger.error(e)
            logger.error('连接服务器检查API超时。')
            if self._load_gateway_server():
                return
            if self._retry:
                self._state.append(False)
            else:
                self._state.append(self.fast_retry())
        except JSONDecodeError:
            if self._load_gateway_server():
                return
            self._state.append(False)
            raise ScriptError(f'Response "{resp.text}" seems not to be a JSON.')
        except Exception as e:
            logger.error(e)
            self._state.append(False)
            raise e

    def _query_gateway(self) -> GatewayServer:
        """按服务器名在游戏网关中查找当前服务器。

        遍历本 region 对应的网关入口，逐个拉取完整服务器列表，
        以服务器名精确匹配（本地 VALID_SERVER_LIST 与网关返回的
        服务器名同源于游戏服务器列表）。

        Returns:
            GatewayServer: 名字匹配的服务器条目。

        Raises:
            ServerStatusQueryError: 所有相关网关入口均无法访问，或均未收录该服务器。
        """
        last_error: ServerStatusQueryError | None = None
        for region in GATEWAY_REGION_MAP.get(self._region, ()):
            try:
                servers = query_region(region, timeout=5)
            except ServerStatusQueryError as e:
                last_error = e
                continue
            for server in servers:
                if server.name == self._server:
                    return server
        raise last_error if last_error is not None else ServerStatusQueryError('not_found')

    def _load_gateway_server(self) -> bool:
        """公共 API 不可用时，直连游戏网关查询当前服务器。

        返回 True 说明已取得并记录服务器状态。网关失败只作为公共 API
        故障的后备处理，交由调用方进入原有的快速重试与退避流程。
        """
        try:
            server = self._query_gateway()
        except ServerStatusQueryError as e:
            logger.warning(f'[服务器检查] 直连游戏网关失败（{e.code}）。')
            return False

        if server.status in GATEWAY_AVAILABLE_STATES:
            self._state.append(True)
            logger.info(f'[服务器检查] 服务器 "{self._server}" 可用（游戏网关 {server.status}）。')
            return True
        if server.status in GATEWAY_UNAVAILABLE_STATES:
            self._state.append(False)
            logger.info(f'[服务器检查] 服务器 "{self._server}" 暂不可用（游戏网关 {server.status}）。')
            return True
        logger.warning(f'[服务器检查] 游戏网关返回了未知状态：{server.status}。')
        return False

    def wait_until_available(self) -> None:
        while not self.is_available():
            self._timer.wait()
            self.check_now()

    def check_now(self) -> None:
        """
        忽略计时器，立即获取服务器状态。

        若服务器可用，检查器保持静默。否则计时器间隔逐步从 2 分钟递增至 10 分钟。
        若发生 ScriptError，检查器将被临时强制禁用。
        """
        try:
            self._load_server()
            if self._state[-1]:
                self._timer.limit = 0
                # Recover 表示最新状态为可用（state[-1]=True），前一状态为不可用（state[0]=False）
                if not self._state[0]:
                    self._recover = True
            else:
                if self._timer.limit < 600:
                    self._timer.limit += 120
                logger.info(f'服务器检查er will retry after {self._timer.limit}s')
            self._timer.reset()
        except ScriptError as e:
            logger.warning(str(e))
            logger.warning('服务器检查可能有问题。')
            logger.warning('请联系开发者修复。')
            self.reset()
            self._server = 'disabled'
            self._recover = True
            self._state.append(True)
        except Exception as e:
            raise e

    def _server_in_local_list(self) -> bool:
        """
        检查服务器名称是否存在于本地 VALID_SERVER_LIST 中。

        当 API 返回 404 时，用于区分"服务器不存在"和"API 数据库未收录"两种情况。

        Returns:
            bool: 本地列表中存在该服务器时返回 True。
        """
        for servers in server_list.values():
            if self._server in servers:
                return True
        return False

    def reset(self) -> None:
        self._timestamp = 0
        self._expired = 0
        self._timer.limit = 0
        self._recover = False

    def is_available(self) -> bool:
        """
        使用缓存返回服务器状态。

        Returns:
            bool: 服务器可用时返回 True。
        """
        if self._timer.limit != 0 and self._timer.reached():
            self.check_now()

        return self._state[-1]  # 返回最新状态

    def is_recovered(self) -> bool:
        """
        服务器是否从不可用状态恢复。

        Returns:
            bool: 服务器刚从不可用恢复为可用时返回 True。
        """
        if len(self._state) < 2:
            self._recover = False
            return False

        if self._recover:
            self._recover = False
            return True

        return False

    def fast_retry(self) -> bool:
        """
        快速重试：通过访问百度判断网络是否连通。

        部分国内用户可能无法连接 API，但网络实际可用，因此借助百度进行网络可达性判断。

        Returns:
            bool: 网络可用时返回 True。
        """
        self._retry = True
        try:
            session = requests.Session()
            session.trust_env = False
            _ = session.get('https://www.baidu.com', timeout=5)
            network_available = True
        except Exception as e:
            logger.error(e)
            network_available = False

        logger.attr('network_available', network_available)
        if network_available:
            logger.info('触发快速重试。')
            last = self._state.copy()
            for _ in range(3):
                logger.info(f'重试 {_ + 1} times ...')
                self._load_server()
                if self._state[0]:
                    self._retry = False
                    self._state.extend(last)
                    return True

            logger.error('无法连接API. Please check you network or disable server checker.')
            self._retry = False
            self._state.extend(last)
            return False
        else:
            self._retry = False
            logger.error('网络不可用. Please check your network status.')
            return False
