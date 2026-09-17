import copy
import sys
from typing import Optional, Union

from deploy.geo import get_country_code
from deploy.logger import logger
from deploy.utils import *


GIT_OVER_CDN_REPOSITORY = 'git://git.pull/AzurPilot'
GIT_OVER_CDN_FALLBACK_REPOSITORY = 'https://gitcode.com/ddl2/AzurLaneAutoScript'

# AzurPilot 自有仓库：国外走 GitHub，国内走 GitCode 镜像。
GITHUB_REPOSITORY = 'https://github.com/changqing81/Azurpilot-Auto'
CN_REPOSITORY = 'https://gitcode.com/gcw_BYvq9jGu/AzurPilot'

# 哨兵值：Repository 填这个值时按网络所在地区自动选择更新源。
# 填任何其它具体地址（包括 GITHUB_REPOSITORY）都表示"我就要用这个地址"，不会被改写。
AUTO_REPOSITORY = 'auto'

# 旧版本 / 上游仓库地址，读到这些值时自动迁移为 AUTO_REPOSITORY。
LEGACY_REPOSITORIES = (
    'https://github.com/wess09/AzurPilot',
    'https://github.com/Maratrain/AzurPilot',
)

# 云端更新开关。指向一个内容为纯文本 true / false 的地址：
#   true  -> 允许更新
#   false -> 跳过更新
#   取不到 / 解析失败 / 限流 -> 允许更新（fail-open，不会阻塞启动）
# 置为 None 或空字符串表示本机不启用远程开关（始终允许更新）。
# 修改开关：编辑仓库里的 switch/updata 文件（jsDelivr 有 CDN 缓存延迟，不会立刻生效）。
CLOUD_UPDATE_CONTROL_URL = 'https://cdn.jsdelivr.net/gh/changqing81/Azurpilot-Auto@master/switch/updata'


class ExecutionError(Exception):
    pass


class ConfigModel:
    # Git 配置
    # Repository 填 'auto' 表示按网络所在地区自动选择更新源（国内 GitCode / 其他 GitHub）；
    # 填任何具体地址则表示锁定该地址，不会被自动改写。
    Repository: str = AUTO_REPOSITORY
    Branch: str = "master"
    GitExecutable: str = "./.venv/Scripts/git/cmd/git.exe" if sys.platform == "win32" else "./.venv/bin/git"
    GitProxy: Optional[str] = None
    SSLVerify: bool = False
    # 云端更新开关地址；None / 空字符串 = 不启用远程开关（始终允许更新）。
    # 可在 config/deploy.yaml 里覆盖。
    CloudUpdateControl: Optional[str] = CLOUD_UPDATE_CONTROL_URL

    # Python 配置
    PythonExecutable: str = "./.venv/Scripts/python.exe" if sys.platform == "win32" else "./.venv/bin/python"
    PypiMirror: Optional[str] = None
    InstallDependencies: bool = True

    # ADB 配置
    AdbExecutable: str = "./.venv/Scripts/adb.exe" if sys.platform == "win32" else "./.venv/bin/adb"
    ReplaceAdb: bool = True
    AutoConnect: bool = True
    InstallUiautomator2: bool = True

    # OCR 配置
    UseOcrServer: bool = False
    StartOcrServer: bool = False
    OcrServerPort: int = 22268
    OcrClientAddress: str = "127.0.0.1:22268"

    # 更新配置
    EnableReload: bool = True
    CheckUpdateInterval: int = 5
    AutoRestartTime: str = "03:50"
    HideUpdateNotice: bool = False
    HideAnnouncement: bool = True

    # 杂项
    DiscordRichPresence: bool = False

    # 远程访问
    EnableRemoteAccess: bool = False
    RemoteAccessMode: str = "auto"
    SSHUser: Optional[str] = None
    SSHServer: Optional[str] = None
    SSHExecutable: Optional[str] = None
    SignalingServer: Optional[str] = None
    StunServers: Optional[str] = '["stun:stun.l.google.com:19302"]'
    TurnServers: Optional[str] = None
    TurnCredentialMode: str = "static"

    # WebUI 配置
    WebuiHost: str = "0.0.0.0"
    WebuiPort: int = 25548
    WebuiSSLKey: Optional[str] = None
    WebuiSSLCert: Optional[str] = None
    Language: str = "en-US"
    Theme: str = "default"
    DpiScaling: bool = True
    Password: Optional[str] = None
    CDN: Union[str, bool] = False
    Run: Optional[str] = None

    # 动态配置
    GitOverCdn: bool = False


class DeployConfig(ConfigModel):
    def __init__(self, file=DEPLOY_CONFIG):
        """初始化部署配置。

        Args:
            file (str): 用户部署配置文件路径。
        """
        self.file = file
        self.template_file = get_deploy_template()
        self.config = {}
        self.config_template = {}
        self._github_location_checked = False
        self.read()

        self.show_config()

    def show_config(self):
        logger.hr("Show deploy config", 1)
        for k, v in self.config.items():
            if k in ("Password", "SSHUser"):
                continue
            if self.config_template.get(k) == v:
                continue
            logger.info(f"{k}: {v}")

        logger.info(f"Rest of the configs are the same as default")

    def read(self):
        """读取并更新部署配置，将配置值复制到属性。"""
        self.config = poor_yaml_read(self.template_file)
        self.config_template = copy.deepcopy(self.config)
        origin = poor_yaml_read(self.file)
        self.config.update(origin)

        for key, value in self.config.items():
            if hasattr(self, key):
                super().__setattr__(key, value)

        self.config_redirect()

        if self.config != origin:
            self.write()

    def write(self):
        poor_yaml_write(self.config, self.file, template_file=self.template_file)

    def config_redirect(self):
        """部署配置重定向，处理旧配置到新配置的迁移。

        每次 `read()` 之后必须调用。
        """
        self.config.pop('AutoUpdate', None)
        self._redirect_github_repository()
        if self.Repository in [
            'https://gitee.com/LmeSzinc/AzurLaneAutoScript',
            'https://gitee.com/lmeszinc/azur-lane-auto-script-mirror',
            'https://e.coding.net/llop18870/alas/AzurLaneAutoScript.git',
            'https://e.coding.net/saarcenter/alas/AzurLaneAutoScript.git',
            'https://git.saarcenter.com/LmeSzinc/AzurLaneAutoScript.git',
            'git://git.lyoko.io/AzurLaneAutoScript',
            'https://gitcode.com/ddl2/AzurLaneAutoScript',
            'https://gitcode.com/ZhangMusan/AzurLaneAutoScript',
            'https://gitcode.com/nerom/AzurLaneAutoScript',
            'https://gitee.com/wqeaxc/AzurLaneAutoScript1',
            'https://git.nanoda.work/git/AzurLaneAutoScript',
            'https://git.nanoda.work/git/AzurPilot',
            'https://git.nanoda.work',
        ]:
            self.Repository = GIT_OVER_CDN_REPOSITORY
            self.config['Repository'] = GIT_OVER_CDN_REPOSITORY
        if self.PypiMirror in [
            'https://pypi.tuna.tsinghua.edu.cn/simple'
        ]:
            self.PypiMirror = 'https://mirrors.aliyun.com/pypi/simple'
            self.config['PypiMirror'] = 'https://mirrors.aliyun.com/pypi/simple'

        # 绕过 webui.config.DeployConfig.__setattr__()，不写入 deploy.yaml
        super().__setattr__(
            'GitOverCdn',
            self.Repository == GIT_OVER_CDN_REPOSITORY and self.Branch == 'master'
        )
        if self.Repository == GIT_OVER_CDN_REPOSITORY:
            super().__setattr__('Repository', GIT_OVER_CDN_FALLBACK_REPOSITORY)
        if self.Repository in ['global']:
            super().__setattr__('Repository', GITHUB_REPOSITORY)
        if self.Repository in ['cn']:
            super().__setattr__('Repository', CN_REPOSITORY)

    def _redirect_github_repository(self):
        """处理更新源的选择与迁移。

        规则：
        1. Repository 为哨兵值 'auto' -> 按网络所在地区选择：中国大陆用 GitCode，其余用 GitHub。
           此时不会把结果写回配置文件，因此换个网络环境下次启动会自动重新判断。
        2. Repository 是具体地址（含 GITHUB_REPOSITORY）-> 完全尊重，绝不改写。
        3. Repository 是旧版本 / 上游地址 -> 迁移为 'auto'（用户从未主动选过它，交给地区判断）。
        4. 地区检测失败 -> 使用 GitHub，不误判到国内源。
        """
        if self._github_location_checked:
            return

        repository = self.Repository
        if isinstance(repository, str):
            repository = repository.strip()

        if repository in LEGACY_REPOSITORIES:
            logger.info(f'检测到旧更新源 {repository}，迁移为 {AUTO_REPOSITORY}')
            self.Repository = AUTO_REPOSITORY
            self.config['Repository'] = AUTO_REPOSITORY
            repository = AUTO_REPOSITORY

        if repository != AUTO_REPOSITORY:
            # 用户自己填了地址，保持原样
            return

        self._github_location_checked = True
        country_code = get_country_code()
        if country_code == 'cn':
            logger.info('检测到中国大陆网络，使用 GitCode 国内更新源')
            super().__setattr__('Repository', CN_REPOSITORY)
        elif country_code is None:
            logger.warning('无法检测网络所在国家，使用 GitHub 更新源')
            super().__setattr__('Repository', GITHUB_REPOSITORY)
        else:
            logger.info('当前网络不在中国大陆，使用 GitHub 更新源')
            super().__setattr__('Repository', GITHUB_REPOSITORY)

    def filepath(self, key):
        """根据配置键获取绝对文件路径。

        Args:
            key (str): 配置键名。

        Returns:
            str: 绝对文件路径。
        """
        return (
            os.path.abspath(os.path.join(self.root_filepath, self.config[key]))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    @cached_property
    def root_filepath(self):
        return (
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    def execute(self, command, allow_failure=False, output=True):
        """执行系统命令。

        Args:
            command (str): 要执行的命令。
            allow_failure (bool): 是否允许失败。
            output (bool): 是否显示输出。

        Returns:
            bool: 是否成功。失败且不允许失败时终止安装流程。
        """
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        if not output:
            # Windows 用 nul 设备，POSIX（如 Termux）用 /dev/null
            if sys.platform == "win32":
                command = command + ' >nul 2>nul'
            else:
                command = command + ' >/dev/null 2>&1'
        logger.info(command)
        error_code = os.system(command)
        if error_code:
            if allow_failure:
                logger.info(f"[ allowed failure ], error_code: {error_code}")
                return False
            else:
                logger.info(f"[ failure ], error_code: {error_code}")
                self.show_error(command)
                raise ExecutionError
        else:
            logger.info(f"[ success ]")
            return True

    def show_error(self, command=None):
        logger.hr("Update failed", 0)
        self.show_config()
        logger.info("")
        logger.info(f"Last command: {command}")
        logger.info(
            "Please check your deploy settings in config/deploy.yaml "
            "and re-open AzurPilot.exe"
        )
        logger.info("Take the screenshot of entire window if you need help")
