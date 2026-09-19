"""活动计算器数据服务：从 Wiki 同步活动数据并解析价格与关卡点数。"""
import ipaddress
import json
import os
import re
import socket
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit

import requests

from module.logger import logger


WIKI_RAW_URL = (
    "https://wiki.biligame.com/blhx/"
    "%E6%B4%BB%E5%8A%A8%E8%AE%A1%E7%AE%97%E5%99%A8?action=raw"
)
# 备用源：MediaWiki 标准 API。`action=raw` 走 CDN 裸取原文，偶发被站点风控拦成
# 非标准状态码（实测见过 567 Server Error），api.php 通常不在拦截名单里。
# 2026-09-18 实测两个源返回的 wikitext 解析结果逐字段一致（商品 29 / 总价 199575 / 关卡 12）。
WIKI_API_URL = (
    "https://wiki.biligame.com/blhx/api.php"
    "?action=parse&page=%E6%B4%BB%E5%8A%A8%E8%AE%A1%E7%AE%97%E5%99%A8"
    "&prop=wikitext&format=json&formatversion=2"
)
# 站点 CDN 会对脚本型 UA 更严格，统一带常规浏览器 UA
WIKI_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
CACHE_FILE = "./cache/wiki_event_calculator.json"
CACHE_VERSION = 2

# 站点风控是按来源 IP 记的：连续点「重新拉取」会把 IP 拉进黑名单（两个源一起返回
# 非标准错误码）。失败后进入冷却期，期间不再打网络，直接复用缓存或提示稍后再试。
FETCH_FAILURE_COOLDOWN_SECONDS = 60
_failure_state: Dict[str, Any] = {"at": 0.0, "message": ""}


def _record_fetch_failure(message: str) -> None:
    _failure_state["at"] = time.time()
    _failure_state["message"] = message


def _fetch_cooldown_remaining() -> int:
    elapsed = time.time() - float(_failure_state.get("at") or 0.0)
    remain = FETCH_FAILURE_COOLDOWN_SECONDS - elapsed
    return int(remain) if remain > 0 else 0


# 请求目标只允许固定 Wiki 域名；该校验防止未来改动引入动态地址后，
# 服务端请求被指向内网/环回/云元数据等地址（SSRF）。
WIKI_ALLOWED_HOST = "wiki.biligame.com"


def _assert_public_wiki_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname != WIKI_ALLOWED_HOST:
        raise ValueError(f"拒绝请求非白名单 Wiki 地址: {url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise ValueError(f"Wiki 域名解析失败: {e}") from e
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError(f"Wiki 域名解析到非公网地址 {address}，已拒绝请求")

EVENT_SHOP_FILTER_MAP = [
    ("深潜许可", "URpt"),
    ("建造券", "GachaTicket"),
    ("魔方", "Cube"),
    ("心智单元II", "Chip"),
    ("心智单元", "Array"),
    ("外观装备箱", "SkinBox"),
    ("META", "Meta"),
    ("定向蓝图·八期", "PRS8"),
    ("高级定向蓝图·八期", "DRS8"),
    ("定向蓝图", "PR"),
    ("高级定向蓝图", "DR"),
    ("特殊兵装核心", "AugmentCoreT3"),
    ("兵装强化石T2", "AugmentEnhanceT2"),
    ("兵装重构核心T2", "AugmentChangeT2"),
    ("兵装重构核心T1", "AugmentChangeT1"),
    ("喵箱SSR", "CatT3"),
    ("喵箱SR", "CatT2"),
    ("喵箱R", "CatT1"),
    ("科技箱T4", "BoxT4"),
    ("通用部件T3", "PlateGeneralT3"),
    ("主炮部件T3", "PlateGunT3"),
    ("鱼雷部件T3", "PlateTorpedoT3"),
    ("防空炮部件T3", "PlateAntiairT3"),
    ("舰载机部件T3", "PlatePlaneT3"),
    ("物资", "Coin"),
    ("石油", "Oil"),
    ("酸素可乐", "FoodT1"),
]


def _clean_wikitext(raw: str) -> str:
    return re.sub(r"<!--.*?-->", "", raw, flags=re.S)


def _extract_table(raw: str, table_id: str) -> str:
    match = re.search(rf'\{{\|[^\n]*id="{re.escape(table_id)}"[^\n]*\n', raw)
    if match is None:
        return ""
    start = match.end()
    end_candidates = []
    for marker in ("\n{|", "\n|}", "\n=="):
        pos = raw.find(marker, start)
        if pos >= 0:
            end_candidates.append(pos)
    end = min(end_candidates) if end_candidates else len(raw)
    return raw[start:end]


def _strip_cell_attr(cell: str) -> str:
    if re.match(r'^[A-Za-z0-9_:-]+="[^"]*"\|', cell):
        return cell.split("|", 1)[1]
    return cell


def _parse_table_rows(table: str) -> List[List[str]]:
    rows: List[List[str]] = []
    for block in re.split(r"\n\|-\s*\n", table):
        cells: List[str] = []
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("!") or line.startswith("|}"):
                continue
            if line.startswith("|-"):
                continue
            if line.startswith("||"):
                cell_line = line[2:]
            elif line.startswith("|"):
                cell_line = line[1:]
            else:
                continue
            cell_line = _strip_cell_attr(cell_line.strip())
            cells.extend(part.strip() for part in cell_line.split("||"))
        if cells:
            rows.append(cells)
    return rows


def _clean_name(text: str) -> str:
    text = re.sub(r"\[\[文件:[^\]]+\]\]", "", text)
    text = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\{\{[^{}|]+\|([^{}]+?)\}\}", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("'''", "").replace("''", "")
    return text.strip()


def _to_int(value: str, default: int = 0) -> int:
    match = re.search(r"-?\d+", str(value).replace(",", ""))
    if match is None:
        return default
    return int(match.group(0))


def match_event_shop_filter(name: str, price: int, quantity: int) -> str:
    for pattern, filter_name in EVENT_SHOP_FILTER_MAP:
        if pattern in name:
            return filter_name
    if price == 8000 and quantity <= 2:
        return "ShipSSR"
    if price == 2000 and quantity == 1:
        return "EquipSSR"
    if price == 10000:
        return "EquipUR"
    return ""


def _parse_shop(rows: List[List[str]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        if len(row) < 3:
            continue
        price = _to_int(row[1])
        quantity = _to_int(row[2])
        if price <= 0 or quantity < 0:
            continue
        out.append(
            {
                "name": _clean_name(row[0]) or "未命名项目",
                "price": price,
                "quantity": quantity,
                "filter": match_event_shop_filter(_clean_name(row[0]), price, quantity),
            }
        )
    return out


def _extract_vardefine(raw: str, variable_name: str) -> str:
    """提取 Wiki ``#vardefine`` 变量的完整内容，保留嵌套模板。"""
    match = re.search(
        rf"\{{\{{#vardefine:\s*{re.escape(variable_name)}\s*\|", raw
    )
    if match is None:
        return ""

    start = match.end()
    depth = 1
    position = start
    while position < len(raw):
        opening = raw.find("{{", position)
        closing = raw.find("}}", position)
        if closing < 0:
            return ""
        if 0 <= opening < closing:
            depth += 1
            position = opening + 2
            continue

        depth -= 1
        if depth == 0:
            return raw[start:closing]
        position = closing + 2
    return ""


def _parse_shop_vardefine(raw: str) -> List[Dict[str, Any]]:
    """解析 Wiki 动态商店表使用的 ``_shop_items`` 变量。"""
    content = _extract_vardefine(raw, "_shop_items")
    rows = []
    for line in content.splitlines():
        row = line.rsplit(",", 2)
        if len(row) != 3:
            continue
        rows.append([cell.strip() for cell in row])
    return _parse_shop(rows)


def _parse_points(rows: List[List[str]], key_name: str) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        if len(row) < 2:
            continue
        points = _to_int(row[1])
        if points <= 0:
            continue
        out.append({"name": _clean_name(row[0]), key_name: points})
    return out


def _parse_event_name(raw: str) -> str:
    match = re.search(r"当前活动：\[\[[^\]|]+(?:\|([^\]]+))?\]\]", raw)
    if match is None:
        return ""
    return _clean_name(match.group(1) or match.group(0))


def parse_event_calculator(raw: str) -> Dict[str, Any]:
    """解析 Wiki 活动计算器页面原文。"""
    cleaned = _clean_wikitext(raw)
    time_rows = _parse_table_rows(_extract_table(cleaned, "ECALCTime"))
    end_date = time_rows[0][0] if time_rows and time_rows[0] else ""
    shop_items = _parse_shop(_parse_table_rows(_extract_table(cleaned, "ECALCPt")))
    if not shop_items:
        shop_items = _parse_shop_vardefine(cleaned)

    data = {
        "event_name": _parse_event_name(cleaned),
        "end_date": end_date.replace("/", "-"),
        "shop_items": shop_items,
        "daily": _parse_points(
            _parse_table_rows(_extract_table(cleaned, "ECALCDaily")), "points"
        ),
        "extra": _parse_points(
            _parse_table_rows(_extract_table(cleaned, "ECALCExtra")), "points"
        ),
        "stages": _parse_points(
            _parse_table_rows(_extract_table(cleaned, "ECALC")), "points"
        ),
        "source_url": WIKI_RAW_URL,
        "updated_at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
    }
    data["shop_total"] = sum(
        item["price"] * item["quantity"] for item in data["shop_items"]
    )
    return data


def _read_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"[WebUI-计算器] 读取Wiki活动计算器缓存失败: {e}")
        return {}


def _write_cache(data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        data["cache_version"] = CACHE_VERSION
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[WebUI-计算器] 写入Wiki活动计算器缓存失败: {e}")


def _fetch_wiki_text() -> Tuple[str, str]:
    """拉取活动计算器页面原文，返回 (wikitext, 实际生效的地址)。

    依次尝试 `action=raw` 与 `api.php`：前者走 CDN 裸取、偶发被风控拦成非标准状态码
    （实测 567），后者是标准 API，通常可用。两个源都失败才抛错。

    Raises:
        RuntimeError: 附上每个源的失败原因，便于用户判断是网络还是站点风控。
    """
    errors = []
    for url, from_api in ((WIKI_RAW_URL, False), (WIKI_API_URL, True)):
        try:
            _assert_public_wiki_url(url)
            response = requests.get(
                url, timeout=10, headers=WIKI_REQUEST_HEADERS, allow_redirects=False
            )
            response.raise_for_status()
            if from_api:
                payload = response.json()
                text = (payload.get("parse") or {}).get("wikitext") or ""
            else:
                text = response.text
            if text.strip():
                return text, url
            errors.append(f"{'api.php' if from_api else 'action=raw'} 返回内容为空")
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            errors.append(f"{'api.php' if from_api else 'action=raw'} 返回 HTTP {code}")
        except Exception as e:
            errors.append(f"{'api.php' if from_api else 'action=raw'} {type(e).__name__}")

    raise RuntimeError("Wiki 请求失败（" + "；".join(errors) + "），稍后重试即可")


def load_event_calculator(force_refresh: bool = False) -> Dict[str, Any]:
    """读取 Wiki 活动计算器数据，失败时回退到缓存。"""
    cache = _read_cache()
    cache_valid = cache.get("cache_version") == CACHE_VERSION
    if cache and cache_valid and not force_refresh:
        return {**cache, "from_cache": True}

    # 冷却期内不再打网络：连点「重新拉取」只会让风控把 IP 关得更久
    cooldown = _fetch_cooldown_remaining()
    if cooldown:
        message = _failure_state.get("message") or "Wiki 请求失败"
        if cache:
            return {**cache, "from_cache": True, "error": message}
        return {
            "error": f"{message}；请 {cooldown} 秒后再试（短时间内重复请求会被站点风控）",
            "from_cache": False,
        }

    try:
        wikitext, source_url = _fetch_wiki_text()
        data = parse_event_calculator(wikitext)
        if not data["shop_items"] or not data["stages"]:
            # 两种版式都解析不出来，基本是 Wiki 又改版了 —— 提示直接说明原因，
            # 免得用户以为是自己网络或配置的问题
            raise ValueError("Wiki 页面未解析出商品/关卡数据，页面版式可能已更新")
        data["source_url"] = source_url
        _write_cache(data)
        _failure_state["at"] = 0.0
        return {**data, "from_cache": False}
    except Exception as e:
        logger.warning(f"[WebUI-计算器] 获取Wiki活动计算器失败: {e}")
        _record_fetch_failure(str(e))
        if cache:
            return {**cache, "from_cache": True, "error": str(e)}
        return {"error": str(e), "from_cache": False}
