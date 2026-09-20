"""
API 客户端模块
负责获取公告与更新日志

数据放在**独立的数据仓库** `changqing81/announcement-changelog`（不参与代码更新，
避免公告与配图被塞进更新包、也不污染代码仓库的提交历史）：
主源 jsdelivr CDN（国内可达，分支引用缓存约12小时，发布后可通过 purge.jsdelivr.net
立即刷新），备用源 GitHub raw（缓存约5分钟，主源不可达时兜底）
"""
import json
import time
from typing import Any, Dict, Optional

import requests

from module.logger import logger


class ApiClient:
    """公告客户端：拉取仓库公告文件，支持主源+备用源故障转移"""

    # 数据仓库里的公告文件（jsdelivr 对国内用户可达性最好作主源；raw.githubusercontent.com
    # 在国内普遍被阻断，仅作主源故障时的兜底）
    DATA_REPO = 'changqing81/announcement-changelog'
    DATA_BRANCH = 'main'

    ANNOUNCEMENT_PRIMARY_URL = (
        f'https://cdn.jsdelivr.net/gh/{DATA_REPO}@{DATA_BRANCH}/announcement.json'
    )
    ANNOUNCEMENT_FALLBACK_URL = (
        f'https://raw.githubusercontent.com/{DATA_REPO}/{DATA_BRANCH}/announcement.json'
    )

    # 公告检查间隔（秒），5分钟 = 300秒
    ANNOUNCEMENT_CHECK_INTERVAL = 300

    # 更新日志地址（同一数据仓库、同一套分发机制）
    CHANGELOG_PRIMARY_URL = (
        f'https://cdn.jsdelivr.net/gh/{DATA_REPO}@{DATA_BRANCH}/changelog.json'
    )
    CHANGELOG_FALLBACK_URL = (
        f'https://raw.githubusercontent.com/{DATA_REPO}/{DATA_BRANCH}/changelog.json'
    )

    @classmethod
    def get_announcement(cls, timeout: int = 1, current_id: int = None) -> Optional[Dict[str, Any]]:
        """
        获取公告信息（同步）

        Args:
            timeout: 单个源的请求超时时间（秒）
            current_id: 未使用，保留参数以兼容旧调用

        Returns:
            公告数据字典，None 表示无公告或获取失败
        """
        # 时间戳参数绕过 CDN 缓存
        timestamp = int(time.time())
        sources = [
            ('主源', f'{cls.ANNOUNCEMENT_PRIMARY_URL}?t={timestamp}'),
            ('备用源', f'{cls.ANNOUNCEMENT_FALLBACK_URL}?t={timestamp}'),
        ]
        last_error = None

        for name, url in sources:
            try:
                response = requests.get(
                    url,
                    timeout=timeout,
                    headers={'User-Agent': 'alas AzurPilot'}
                )
                if response.status_code != 200:
                    logger.warning(f'[基础-API] 公告{name}返回错误状态: {response.status_code}')
                    last_error = f'HTTP {response.status_code}'
                    continue

                response_text = response.text
                if not response_text.strip():
                    return None

                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError as e:
                    logger.warning(f'[Base] 解析公告JSON失败: {e}, response={response_text[:100]}')
                    last_error = str(e)
                    continue

                # 空数据或无ID视为无公告
                if not data or not data.get('announcementId'):
                    logger.info('[Base] 公告数据为空或无ID')
                    return None

                # 只要有标题，且有内容 OR 链接，就是有效公告
                if data.get('title') and (data.get('content') or data.get('url')):
                    return data
                return None

            except requests.exceptions.Timeout:
                logger.warning(f'[基础-API] 公告{name}请求超时')
                last_error = 'Timeout'
            except requests.exceptions.RequestException as e:
                logger.warning(f'[基础-API] 公告{name}请求失败: {e}')
                last_error = str(e)
            except Exception as e:
                logger.warning(f'[基础-API] 公告{name}发生异常: {e}')
                last_error = str(e)

        logger.warning(f'[Base] 获取公告失败: {last_error}')
        return None

    @classmethod
    def get_changelog(cls, timeout: int = 5) -> Optional[Dict[str, Any]]:
        """获取更新日志（同步）

        更新日志与公告同样放在远端 master 分支，客户端在更新前即可读到本次要更新什么。
        取不到时返回 None，调用方应静默降级（更新器页面显示占位提示），不要影响更新流程。

        Args:
            timeout: 单个源的请求超时时间（秒）

        Returns:
            含 entries 列表的字典，None 表示获取失败或数据不可用
        """
        timestamp = int(time.time())
        sources = [
            ('主源', f'{cls.CHANGELOG_PRIMARY_URL}?t={timestamp}'),
            ('备用源', f'{cls.CHANGELOG_FALLBACK_URL}?t={timestamp}'),
        ]
        last_error = None

        for name, url in sources:
            try:
                response = requests.get(
                    url,
                    timeout=timeout,
                    headers={'User-Agent': 'alas AzurPilot'}
                )
                if response.status_code != 200:
                    logger.warning(f'[基础-API] 更新日志{name}返回错误状态: {response.status_code}')
                    last_error = f'HTTP {response.status_code}'
                    continue

                if not response.text.strip():
                    last_error = 'EmptyBody'
                    continue

                data = json.loads(response.text)
                if isinstance(data, dict) and isinstance(data.get('entries'), list):
                    return data
                logger.warning('[基础-API] 更新日志结构不符合预期（缺少 entries 列表）')
                last_error = 'BadSchema'
            except json.JSONDecodeError as e:
                logger.warning(f'[基础-API] 解析更新日志JSON失败: {e}')
                last_error = str(e)
            except requests.exceptions.Timeout:
                logger.warning(f'[基础-API] 更新日志{name}请求超时')
                last_error = 'Timeout'
            except requests.exceptions.RequestException as e:
                logger.warning(f'[基础-API] 更新日志{name}请求失败: {e}')
                last_error = str(e)
            except Exception as e:
                logger.warning(f'[基础-API] 更新日志{name}发生异常: {e}')
                last_error = str(e)

        logger.warning(f'[Base] 获取更新日志失败: {last_error}')
        return None
