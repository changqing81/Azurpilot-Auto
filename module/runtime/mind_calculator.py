"""船坞心智单元计算：基础稀有度计费、最高等级合并与人工核对。"""
import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

RARITIES = ('UR', 'SSR', 'SR', 'R', 'N')
RARITY_NAMES = dict(zip(RARITIES, ('海上传奇', '超稀有', '精锐', '稀有', '普通')))
MIND_COSTS = {
    'N': (60, 120, 180, 300), 'R': (80, 160, 240, 400),
    'SR': (120, 240, 360, 600), 'SSR': (200, 400, 600, 1000), 'UR': (300, 600, 900, 1500),
}
RESULT_PATH = 'MindCalculatorScan.MindCalculator.Result'


@lru_cache(maxsize=1)
def catalog():
    """内置公共舰船资料，离线可用，不读取原工具的私人配置。"""
    return json.loads((Path(__file__).parents[2] / 'assets/ship/mind_calculator.json').read_text(encoding='utf-8'))


def normalize_name(name):
    return re.sub(r'[\s.·・．。]', '', unicodedata.normalize('NFKC', name)).casefold()


def find_ship(name):
    ships = catalog()['ships']
    if name in ships:
        return ships[name]
    key = normalize_name(name)
    candidates = [ship for candidate, ship in ships.items() if normalize_name(candidate) == key]
    if len(candidates) == 1:
        return candidates[0]
    # 只补全画面明确显示的截断名；歧义名称留给人工核对。
    if re.search(r'(?:\.{2,}|…+|⋯+)$', name):
        prefix = normalize_name(re.sub(r'(?:\.{2,}|…+|⋯+)$', '', name))
        candidates = [ship for candidate, ship in ships.items() if normalize_name(candidate).startswith(prefix)]
        if len(prefix) >= 2 and len(candidates) == 1:
            return candidates[0]
    return None


def revision(ships):
    return hashlib.sha256(json.dumps(ships, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def enrich(ship):
    """资料优先确定稀有度，未知改造船必须显式提供基础稀有度。"""
    item = dict(ship)
    info = find_ship(item['name'])
    if info:
        item['name'] = info['name']
        item['rarity'] = info['rarity']
        item['base_rarity'] = info['base_rarity']
        item['group'] = info['group']
        item['base_name'] = info['base_name']
    else:
        item['group'] = ''
        item['base_name'] = re.sub(r'[.·・]?改$', '', item['name']).strip()
        if not item.get('base_rarity') and item['base_name'] == item['name']:
            item['base_rarity'] = item.get('rarity', '')
    item.setdefault('rarity', '')
    item.setdefault('base_rarity', '')
    item.setdefault('excluded', False)
    item.setdefault('review', False)
    item.setdefault('source', '')
    return item


def base_key(ship):
    # META 身份不能与普通舰或改造舰合并。
    name = ship['name'] if ship['group'] == 'META' or 'meta' in ship['name'].casefold() else ship['base_name']
    return normalize_name(name)


def calculate(ships):
    """等级只能推断觉醒阶段：116 级起已走完四阶，目标限定为 120 级。"""
    rows = [enrich(ship) for ship in ships]
    highest = {}
    for index, ship in enumerate(rows):
        name = ship['name']
        if ship['excluded'] or '兵装' in name or 'μ' in name.casefold() or ship['group'] == '幼体':
            ship['status'] = 'excluded'
        elif ship['review'] or not 1 <= ship['level'] <= 125 or ship['base_rarity'] not in RARITIES:
            ship['status'] = 'review'
        else:
            ship['status'] = 'merged'
            key = base_key(ship)
            previous = highest.get(key)
            if previous is None or rows[previous]['level'] < ship['level']:
                highest[key] = index
        ship['mind'] = ship['gold'] = 0
    summary = {rarity: dict(rarity=rarity, stages=[0] * 6, stage_mind=[0] * 4,
                           count=0, mind=0, gold=0) for rarity in RARITIES}
    for index in highest.values():
        ship = rows[index]
        ship['status'] = 'included'
        level, rarity = ship['level'], ship['base_rarity']
        stage = 0 if level <= 100 else 1 if level <= 105 else 2 if level <= 110 else 3 if level <= 115 else 4
        bucket = summary[rarity]
        bucket['stages'][stage if level < 120 else 5] += 1
        ship['mind'] = sum(MIND_COSTS[rarity][stage:])
        ship['gold'] = ship['mind'] * 10
        bucket['mind'] += ship['mind']
        bucket['gold'] += ship['gold']
        bucket['count'] += 1
        for remaining in range(stage, 4):
            bucket['stage_mind'][remaining] += MIND_COSTS[rarity][remaining]
    return dict(ships=rows, summary=list(summary.values()),
                mind=sum(row['mind'] for row in rows), gold=sum(row['gold'] for row in rows),
                included=len(highest), merged=sum(row['status'] == 'merged' for row in rows),
                excluded=sum(row['status'] == 'excluded' for row in rows),
                review=sum(row['status'] == 'review' for row in rows))


def recognize(image, source='', *, name_ocr=None, level_ocr=None, row_origins=None):
    """读取完整卡片；保留多轮识别提示，全部要求人工核对。"""
    from module.runtime.mind_recognition import recognize_cards
    return [card.ship for card in recognize_cards(image, source, name_ocr=name_ocr,
                                                  level_ocr=level_ocr, row_origins=row_origins)]


def detect_rows(image, ocr):
    """兼容等级锚点检测入口。"""
    from module.runtime.mind_recognition import detect_rows as locate
    return locate(image, ocr)
