"""船坞卡片定位、多轮 OCR 与滚动截图的位置合并。

参考原计算器 OcrService / AutoScanService 的分层流程，适配 AP 的 1280×720 船坞。
"""
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

import cv2
import numpy as np

from module.base.utils import extract_letters
from module.runtime.mind_calculator import catalog, find_ship, normalize_name

CARD_COLUMNS = tuple(round(93 + col * (164 + 2 / 3)) for col in range(7))
MAX_ROW_ORIGIN = 720 - 183


@dataclass
class Card:
    """位置用于跨屏合并，舰船字段用于持久化与人工核对。"""
    x: int
    y: int
    col: int
    ship: dict
    quality: int = 0


def parse_level(text):
    import re
    text = ''.join(text).strip()
    match = re.fullmatch(r'(?:[Ll1I][VvYy][.\s]*)?([1-9]\d{0,2})', text)
    return int(match[1]) if match and int(match[1]) <= 125 else 0


def level_vote(texts, digit_count=0):
    """数字位数作为约束，无法一致确认的等级保留提示，不补猜百位。"""
    counts = Counter(value for text in texts if (value := parse_level(text)))
    compatible = {value: count for value, count in counts.items()
                  if not digit_count or len(str(value)) == digit_count}
    if not compatible:
        return (counts.most_common(1)[0][0] if counts else 0), '等级未能通过数字位数核验'
    level = max(compatible, key=lambda value: compatible[value])
    if len(counts) > 1 or compatible[level] < 2:
        return level, '等级候选需核对：' + ' / '.join(map(str, sorted(counts)))
    return level, ''


def _clusters(values, gap):
    groups = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > gap:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [round(float(np.median(group))) for group in groups]


def _runs(mask):
    edges = np.diff(np.pad(mask.astype(np.int8), (1, 1)))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def rarity_masks(pixels):
    """原工具的金、紫、蓝、灰 RGB 条件，使用有符号数防止差值溢出。"""
    red, green, blue = pixels.astype(np.int16).transpose(2, 0, 1)
    return {
        'SSR': (red > 200) & (green > 150) & (red - blue > 55) & (green - blue > 10),
        'SR': (blue > 200) & (blue - green > 60) & (red > 100) & (abs(red - blue) < 120),
        'R': (blue > 140) & (blue - red > 25) & (blue - green > 15),
        'N': (np.minimum(np.minimum(red, green), blue) > 150)
             & (np.maximum(np.maximum(red, green), blue) < 235)
             & (np.maximum(np.maximum(red, green), blue) - np.minimum(np.minimum(red, green), blue) < 45),
    }


def color_rows(pixels):
    """找卡头的连续稀有度色带，排除卡面、文字条和画面边缘。"""
    mask = np.logical_or.reduce(list(rarity_masks(pixels).values()))
    candidates = []
    for y in range(65, MAX_ROW_ORIGIN + 1):
        spans = [(left, right) for left, right in _runs(mask[y, 80:1230])
                 if 90 <= right - left <= 150
                 and any(abs(left + 80 - x) <= 8 and abs(right + 80 - x - 138) <= 12
                         for x in CARD_COLUMNS)]
        if spans:
            candidates.append(y)
    rows = []
    for start, end in _runs(np.isin(np.arange(720), candidates)):
        if 2 <= end - start <= 10:
            rows.append(int(start))
    return rows


def detect_rows(image, ocr):
    """等级锚点作为色带检测的补充；只返回能容纳完整船名条的行。"""
    headers = []
    for text, box, score in ocr.det(np.array(image)):
        if score >= .7 and parse_level(text) and text.strip().lower().startswith(('lv', '1v', 'iy', 'ly')):
            y = min(point[1] for point in box)
            if 65 <= y <= MAX_ROW_ORIGIN:
                headers.append(round(y))
    return _clusters(headers, 20)


def _name_image(pixels):
    gray = cv2.min(extract_letters(pixels), extract_letters(pixels, (255, 170, 206), 108))
    count, components, stats, _ = cv2.connectedComponentsWithStats((gray < 120).astype(np.uint8), connectivity=8)
    for component in range(1, count):
        left, _, width, _, _ = stats[component]
        if left == 0 or left + width == gray.shape[1]:
            gray[components == component] = 255
    return gray


def _digit_count(pixels):
    # 去掉左侧 Lv，只对右侧数字统计；过短噪点不能作为百位证据。
    mask = (cv2.cvtColor(pixels[:, 25:], cv2.COLOR_RGB2GRAY) > 220).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    groups = []
    for left, top, width, height, area in sorted(stats[1:count], key=lambda item: item[0]):
        if groups and left < groups[-1][1]:
            group = groups[-1]
            group[1] = max(group[1], left + width)
            group[2] = min(group[2], top)
            group[3] = max(group[3], top + height)
            group[4] += area
        else:
            groups.append([left, left + width, top, top + height, area])
    # 字形的上下两段可能断开，按横坐标归并；细竖线 1 也属于有效数字。
    return sum(bottom - top >= 9 and 1 <= right - left <= 14 and area >= 12
               for left, right, top, bottom, area in groups)


def _match_name(texts, frame_rarity):
    def consistent(info):
        if not frame_rarity or info['rarity'] == frame_rarity or frame_rarity == 'SSR' and info['rarity'] == 'UR':
            return True
        # 内置资料沿用原工具的基础稀有度，改造卡框在游戏内会升一档。
        upgraded = {'N': 'R', 'R': 'SR', 'SR': 'SSR', 'SSR': 'UR'}
        return info['group'] == '改造' and upgraded.get(info['base_rarity']) == frame_rarity
    for text in texts:
        info = find_ship(text)
        if info and consistent(info):
            return info, ''
    # 名称模糊校正必须唯一；META、改造和兵装身份不跨组猜测。
    candidates = {}
    for text in texts:
        key = normalize_name(text)
        if len(key) < 3:
            continue
        for name, info in catalog()['ships'].items():
            candidate = normalize_name(name)
            if not consistent(info):
                continue
            if ('meta' in key) != ('meta' in candidate) or ('改' in key) != ('改' in candidate):
                continue
            if ('兵装' in key or 'μ' in key) != ('兵装' in candidate or 'μ' in candidate):
                continue
            score = SequenceMatcher(None, key, candidate).ratio()
            if score >= .8 and abs(len(key) - len(candidate)) <= 1:
                candidates[name] = max(score, candidates.get(name, 0))
    ranked = sorted(candidates, key=candidates.get, reverse=True)
    if ranked and (len(ranked) == 1 or candidates[ranked[0]] - candidates[ranked[1]] >= .15):
        name = ranked[0]
        return catalog()['ships'][name], f'船名经资料校正：{texts[0]} → {name}'
    return None, '船名未能唯一匹配资料'


def recognize_cards(image, source='', *, name_ocr=None, level_ocr=None, row_origins=None):
    """整屏定位、分卡裁剪、多倍率重读；保留读不全的卡片供核对。"""
    import re
    from module.ocr.al_ocr import AlOcr, OcrSettings
    if image.size != (1280, 720):
        raise ValueError('请使用 1280×720 的模拟器船坞截图')
    settings = OcrSettings('onnx', 'cpu', False, 'standard')
    name_ocr = name_ocr or AlOcr(name='ppocr_v6', settings=settings)
    level_ocr = level_ocr or AlOcr(name='azur_lane', settings=settings)
    pixels = np.array(image)
    words = name_ocr.det(pixels)
    headers = [(text, box, score) for text, box, score in words if score >= .7 and parse_level(text)
               and re.match(r'[Ll1I][VvYy]', text.strip())]
    anchor_rows = _clusters([round(min(point[1] for point in box)) for _, box, _ in headers
                             if 65 <= min(point[1] for point in box) <= MAX_ROW_ORIGIN], 20)
    colored = color_rows(pixels)
    # 色带须与等级锚点吻合。OCR 读不到等级时仍可保留已定位的色框。
    rows = list(row_origins) if row_origins is not None else [
        min(colored, key=lambda y: abs(y - anchor))
        if colored and min(abs(y - anchor) for y in colored) <= 12 else anchor for anchor in anchor_rows]
    if not rows:
        rows = colored
    if not rows:
        raise ValueError('未能定位船坞卡片，请使用加载完成的船坞截图')
    rows = _clusters([y for y in rows if 65 <= y <= MAX_ROW_ORIGIN], 20)
    boxes, name_images, level_images, frame_rarities, header_texts = [], [], [], [], []
    for y in rows:
        for col, x in enumerate(CARD_COLUMNS):
            masks = rarity_masks(pixels[y:y + 5, x:x + 138])
            frame_rarity = max(masks, key=lambda key: masks[key].sum())
            if masks[frame_rarity].mean() < .3:
                frame_rarity = ''
            anchors = [text for text, box, _ in headers
                       if x + 60 <= np.mean([p[0] for p in box]) <= x + 144
                       and y - 12 <= np.mean([p[1] for p in box]) <= y + 35]
            name_region = pixels[y + 164:y + 183, x - 6:x + 142]
            # 空格不凭固定网格造船；有颜色框、等级或名字条内容则保留。
            if not frame_rarity and not anchors and not np.any(_name_image(name_region) < 120):
                continue
            boxes.append((x, y, col))
            frame_rarities.append(frame_rarity)
            header_texts.append(anchors)
            name_images.append(name_region)
            level_images.append(pixels[y + 5:y + 27, x + 74:x + 138])
    if not boxes:
        raise ValueError('船坞卡片为空或尚未加载')
    names = name_ocr.ocr_for_single_lines([_name_image(region) for region in name_images])
    # 原色、两倍白字和三倍白字独立重读，数字位数用于排除漏位候选。
    binary = [extract_letters(region, threshold=108) for region in level_images]
    digit_reads = [level_ocr.ocr_for_single_lines(level_images)]
    for scale in (2, 3):
        digit_reads.append(level_ocr.ocr_for_single_lines([
            cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) for region in binary]))
    cards = []
    for index, (x, y, col) in enumerate(boxes):
        candidates = [names[index].strip()]
        info, name_note = _match_name(candidates, frame_rarities[index])
        if not info:
            region = name_images[index]
            retry = [cv2.resize(region, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC),
                     cv2.resize(_name_image(region), None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)]
            candidates.extend(name_ocr.ocr_for_single_lines(retry))
            full_names = [text for text, box, _ in words if x - 10 <= np.mean([p[0] for p in box]) <= x + 148
                          and y + 153 <= np.mean([p[1] for p in box]) <= y + 188]
            candidates.extend(full_names)
            info, name_note = _match_name(candidates, frame_rarities[index])
        value, level_note = level_vote([*[reads[index] for reads in digit_reads], *header_texts[index]],
                                      _digit_count(level_images[index]))
        name = info['name'] if info else candidates[0]
        if not name or re.fullmatch(r'[\W\d_]+', name):
            name = f'未识别舰船（第 {rows.index(y) + 1} 行第 {col + 1} 列）'
        notes = [note for note in (name_note, level_note) if note]
        ship = dict(name=name, level=value, rarity=info['rarity'] if info else frame_rarities[index],
                    base_rarity='', excluded=False, review=True,
                    source='；'.join([source, *notes]).strip('；')[:200])
        quality = (4 if info else 0) + (2 if value and not level_note else 0)
        cards.append(Card(x, y, col, ship, quality))
    return cards


def estimate_scroll(previous, current):
    """核验分段拖动的正负位移；向下微调和零位移也必须有像素证据。"""
    before = cv2.cvtColor(previous[65:640, 85:1225], cv2.COLOR_RGB2GRAY).astype(np.float32)[:, ::8]
    after = cv2.cvtColor(current[65:640, 85:1225], cv2.COLOR_RGB2GRAY).astype(np.float32)[:, ::8]
    if np.mean(np.abs(before - after)) < 1:
        return 0
    scores = []
    for shift in range(-454, 455):
        if shift > 0:
            a, b = before[shift:][::2], after[:-shift][::2]
        elif shift < 0:
            a, b = before[:shift][::2], after[-shift:][::2]
        else:
            a, b = before[::2], after[::2]
        # 背景黑色不能占据大部分匹配权重，船名和卡面共同提供证据。
        active = np.maximum(a, b) > 35
        error = float(np.abs(a - b)[active].mean()) if active.sum() > 300 else float('inf')
        scores.append((error, shift))
    scores.sort()
    best, shift = scores[0]
    other = min(score for score, candidate in scores if abs(candidate - shift) > 10)
    if best > 20 or other < best + 2:
        raise ValueError('相邻船坞截图没有明确的重叠位移，停止扫描以免漏船')
    return shift


class ScanMerger:
    """跨页按列和绝对行坐标合并；同名同级的不同格子仍然保留。"""
    def __init__(self):
        self.previous = None
        self.offset = 0
        self.slots = []

    def advance(self, image):
        """中间截图只核验位移，让无直接重叠的三排整页仍可准确累计坐标。"""
        pixels = np.array(image)
        shift = estimate_scroll(self.previous, pixels) if self.previous is not None else 0
        self.offset += shift
        self.previous = pixels.copy()
        return shift

    def add(self, image, cards):
        shift = self.advance(image)
        for card in cards:
            absolute_y = card.y + self.offset
            existing = next((item for item in self.slots if item[0].col == card.col and abs(item[1] - absolute_y) <= 12), None)
            if existing:
                previous = existing[0]
                if previous.quality < card.quality:
                    existing[0] = card
                selected = existing[0]
                if previous.ship['name'] != card.ship['name'] or previous.ship['level'] != card.ship['level']:
                    selected.ship['source'] = (selected.ship['source'] + '；跨屏读数不一致，请核对')[:200]
            else:
                self.slots.append([card, absolute_y])
        return shift

    def ships(self):
        return [card.ship for card, _ in sorted(self.slots, key=lambda item: (item[1], item[0].col))]
