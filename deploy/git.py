import requests
import time

from deploy.config import DeployConfig, ExecutionError
from deploy.git_over_cdn.client import GitOverCdnClient
from deploy.git_over_cdn.endpoints import CLOUDFLARE_UPDATE_URLS, FALLBACK_UPDATE_URLS
from deploy.logger import logger
from deploy.utils import *


class GitManager(DeployConfig):
    @cached_property
    def git(self):
        exe = self.filepath('GitExecutable')
        if os.path.exists(exe):
            return exe

        logger.warning(f'GitExecutable: {exe} does not exist, use `git` instead')
        return 'git'

    @staticmethod
    def remove(file):
        try:
            os.remove(file)
            logger.info(f'Removed file: {file}')
        except FileNotFoundError:
            logger.info(f'File not found: {file}')

    def git_repository_init(
            self, repo, source='origin', branch='master', proxy='', ssl_verify=True
    ):
        logger.hr('Git Init', 1)
        if not self.execute(f'"{self.git}" init', allow_failure=True):
            self.remove('./.git/config')
            self.remove('./.git/index')
            self.remove('./.git/HEAD')
            self.execute(f'"{self.git}" init')

        logger.hr('Set Git Proxy', 1)
        if proxy:
            self.execute(f'"{self.git}" config --local http.proxy {proxy}')
            self.execute(f'"{self.git}" config --local https.proxy {proxy}')
        else:
            self.execute(f'"{self.git}" config --local --unset http.proxy', allow_failure=True)
            self.execute(f'"{self.git}" config --local --unset https.proxy', allow_failure=True)

        if ssl_verify:
            self.execute(f'"{self.git}" config --local http.sslVerify true', allow_failure=True)
        else:
            self.execute(f'"{self.git}" config --local http.sslVerify false', allow_failure=True)

        logger.hr('Set Git Repository', 1)
        if not self.execute(f'"{self.git}" remote set-url {source} {repo}', allow_failure=True):
            self.execute(f'"{self.git}" remote add {source} {repo}')

        logger.hr('Fetch Repository Branch', 1)
        self.execute(f'"{self.git}" fetch {source} {branch}')

        logger.hr('Pull Repository Branch', 1)
        # 移除 git 锁文件
        for lock_file in [
            './.git/index.lock',
            './.git/HEAD.lock',
            './.git/refs/heads/master.lock',
        ]:
            if os.path.exists(lock_file):
                logger.info(f'Lock file {lock_file} exists, removing')
                os.remove(lock_file)
        self.execute(f'"{self.git}" reset --hard {source}/{branch}')
        self.execute(f'"{self.git}" pull --ff-only {source} {branch}')

        logger.hr('Show Version', 1)
        self.execute(f'"{self.git}" --no-pager log --no-merges -1')

    @property
    def goc_client(self):
        client = GitOverCdnClient(
            url=CLOUDFLARE_UPDATE_URLS,
            fallback_urls=FALLBACK_UPDATE_URLS,
            folder=self.root_filepath,
            source='origin',
            branch='master',
            git=self.git,
        )
        client.logger = logger
        return client

    def cloud_update_control_url(self):
        """当前生效的云端更新开关地址。

        来自 config/deploy.yaml 的 CloudUpdateControl。
        返回 None 表示不启用远程开关（始终允许更新）。
        """
        url = getattr(self, 'CloudUpdateControl', None)
        if isinstance(url, str):
            url = url.strip()
        if not url or url.lower() == 'null':
            return None
        return url

    def cloud_auto_update_enabled(self):
        """检查云端更新开关。

        返回 True 表示允许更新，False 表示明确禁止更新（永不再返回 None）。

        设计原则是 fail-open：只有明确读到 false 才阻止更新；
        开关地址未配置、请求失败、被限流、响应无法识别时一律按「允许更新」处理，
        避免因为一个遥不可及的开关把用户挡在启动流程之外。
        """
        url = self.cloud_update_control_url()
        if url is None:
            logger.info('Cloud update control is not configured, allow update')
            return True

        # 结果缓存，避免多次调用同一接口导致 429
        now = time.time()
        last = getattr(GitManager, '_cloud_control_last_check', 0.0)
        cached = getattr(GitManager, '_cloud_control_cached', None)
        if cached is not None and now - last < 300:
            return cached

        logger.info(f'Check cloud update control: {url}')
        try:
            resp = requests.get(url, timeout=5, headers={'User-Agent': 'alas AzurPilot'})
            if resp.status_code == 429:
                # 被限流时视为允许更新，不阻塞用户
                logger.warning('Cloud update control returned 429, allow update (fail-open)')
                GitManager._cloud_control_cached = True
                GitManager._cloud_control_last_check = now
                return True
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f'Cloud update control unreachable, allow update (fail-open): {e}')
            GitManager._cloud_control_cached = True
            GitManager._cloud_control_last_check = now
            return True

        text = resp.text.strip()
        try:
            data = resp.json()
        except ValueError:
            data = text

        if data is True or (isinstance(data, str) and data.lower() in ('true', 'ture')):
            logger.info('Cloud update control is enabled')
            result = True
        elif data is False or (isinstance(data, str) and data.lower() in ('false', 'fales')):
            logger.info('Cloud update control is disabled')
            result = False
        else:
            # 返回内容无法识别时按允许更新处理，避免误伤
            logger.warning(f'Cloud update control response is unrecognized ({text!r}), allow update (fail-open)')
            result = True

        GitManager._cloud_control_cached = result
        GitManager._cloud_control_last_check = now
        return result

    def cloud_update_access_failed(self, fatal=True):
        logger.hr('Cloud Update Control Failed', 0)
        if fatal:
            logger.warning('Failed to access cloud update control, stopping startup')
            raise ExecutionError
        else:
            logger.warning('Failed to access cloud update control, skip update check')

    def git_install(self):
        logger.hr('Update AzurPilot', 0)

        if not self.cloud_auto_update_enabled():
            logger.info('Cloud update control disabled, skip')
            return

        if self.GitOverCdn:
            if self.goc_client.update():
                return

        self.git_repository_init(
            repo=self.Repository,
            source='origin',
            branch=self.Branch,
            proxy=self.GitProxy,
            ssl_verify=self.SSLVerify,
        )


if __name__ == '__main__':
    self = GitManager()
    self.goc_client.get_status()
