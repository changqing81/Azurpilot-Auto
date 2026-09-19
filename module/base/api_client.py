"""
API 客户端模块
负责获取公告信息
公告文件由本仓库根目录的 announcement.json 发布：主源 jsdelivr CDN（国内可达，
分支引用缓存约12小时，发新公告后可通过 purge.jsdelivr.net 立即刷新），
备用源 GitHub raw（缓存约5分钟，主源不可达时兜底）
"""
import json
import time
from typing import Any, Dict, Optional

import requests

from module.logger import logger


class ApiClient:
    """公告客户端：拉取仓库公告文件，支持主源+备用源故障转移"""

    # 公告文件地址（jsdelivr 对国内用户可达性最好作主源；raw.githubusercontent.com
    # 在国内普遍被阻断，仅作主源故障时的兜底）
    ANNOUNCEMENT_PRIMARY_URL = (
        'https://cdn.jsdelivr.net/gh/changqing81/Azurpilot-Auto@master/announcement.json'
    )
    ANNOUNCEMENT_FALLBACK_URL = (
        'https://raw.githubusercontent.com/changqing81/Azurpilot-Auto/master/announcement.json'
    )

    # 公告检查间隔（秒），5分钟 = 300秒
    ANNOUNCEMENT_CHECK_INTERVAL = 300

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
