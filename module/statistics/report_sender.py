import os
import socket
import ipaddress
from urllib.parse import urlparse

import requests
import yaml

from module.logger import logger


def get_wecom_webhook(onepush_config):
    try:
        config = {}

        for item in yaml.safe_load_all(onepush_config):
            if item:
                config.update(item)

        provider = config.get("provider")

        if provider != "wechatworkbot":
            return None

        return config.get("key")

    except Exception:
        logger.exception("Parse OnePushConfig failed")
        return None


def send_template_card(webhook, title, content_lines):
    # Basic validation of webhook URL to reduce SSRF / misuse risk.
    def is_valid_webhook(url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False

        # Only allow http or https schemes (prefer https)
        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # Allowed hosts can be provided via environment variable
        allowed = os.environ.get("REPORT_ALLOWED_HOSTS")
        if allowed:
            allowed_hosts = {h.strip() for h in allowed.split(",") if h.strip()}
        else:
            # Default to official WeCom API host for enterprise robot
            allowed_hosts = {"qyapi.weixin.qq.com"}

        if hostname not in allowed_hosts:
            return False

        # Resolve hostname and ensure it does not point to private/loopback addresses
        try:
            infos = socket.getaddrinfo(hostname, None)
            for info in infos:
                addr = info[4][0]
                ip = ipaddress.ip_address(addr)
                if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_multicast:
                    return False
        except Exception:
            # If DNS resolution fails, reject
            return False

        return True

    if not is_valid_webhook(webhook):
        logger.warning(f"Rejected webhook by validation: {webhook}")
        return False

    # Ensure all content lines are strings
    try:
        content_lines = [str(x) for x in content_lines]
    except Exception:
        logger.exception("Invalid content_lines for template card")
        return False

    payload = {
        "msgtype": "template_card",
        "template_card": {
            "card_type": "text_notice",
            "source": {
                "desc": "AzurPilot"
            },
            "main_title": {
                "title": title,
                "desc": ""
            },
            "horizontal_content_list": [
                {
                    "keyname": "统计",
                    "value": "\n".join(content_lines)
                }
            ],
            "card_action": {
                "type": 1,
                "url": "https://github.com/LmeSzinc/AzurLaneAutoScript"
            }
        }
    }

    try:
        r = requests.post(
            webhook,
            json=payload,
            timeout=10,
        )

        logger.info(f"Daily report sent: {r.status_code} {r.text}")

        # Prefer checking provider-specific success code in JSON response
        try:
            data = r.json()
            if isinstance(data, dict):
                # WeCom bot returns errcode == 0 on success
                return data.get("errcode", 0) == 0
        except ValueError:
            # Not JSON, fallback to HTTP status
            pass

        return r.status_code == 200

    except Exception:
        logger.exception("Send template card failed")
        return False


def send_text_message(webhook, title, content_lines):
    """以纯文本消息发送日报（企业微信机器人 type 为 text）。

    WeCom 机器人文本消息单条长度上限约 2000 字节，超长会自动截断
    为多次消息。这里保持与 send_template_card 一致的 webhook 校验。
    """
    if not is_valid_webhook(webhook):
        logger.warning(f"Rejected webhook by validation: {webhook}")
        return False

    try:
        content_lines = [str(x) for x in content_lines]
    except Exception:
        logger.exception("Invalid content_lines for text message")
        return False

    body = "\n".join([title] + content_lines)

    # WeCom text 消息正文超过 2048 字节会报错，按字节切分发送
    def chunks(text, limit=2000):
        byte = text.encode("utf-8")
        parts = []
        while len(byte) > limit:
            cut = byte[:limit]
            # 避免在 UTF-8 多字节字符中间截断
            while cut and (cut[-1] & 0xC0) == 0x80:
                cut = cut[:-1]
            parts.append(cut.decode("utf-8", "ignore"))
            byte = byte[len(cut):]
        parts.append(byte.decode("utf-8", "ignore"))
        return parts

    try:
        for part in chunks(body):
            payload = {
                "msgtype": "text",
                "text": {"content": part},
            }
            r = requests.post(webhook, json=payload, timeout=10)
            logger.info(f"Daily report sent: {r.status_code} {r.text}")
            try:
                data = r.json()
                errcode_ok = isinstance(data, dict) and data.get("errcode", 0) == 0
            except ValueError:
                errcode_ok = False
            if not errcode_ok and r.status_code != 200:
                return False
        return True
    except Exception:
        logger.exception("Send text message failed")
        return False
