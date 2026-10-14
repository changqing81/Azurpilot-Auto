"""舰队科技目录、累计扫描证据与人工校正，均不操作游戏设备。"""
import copy
import hashlib
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from module.runtime.mind_calculator import normalize_name

RECORD_PATH = 'MindCalculatorScan.MindCalculator.FleetTechRecord'
ROOT = Path(__file__).parents[2]


@lru_cache(maxsize=1)
def catalog():
    """把按群组生成的科技索引与现有共享舰船资料关联。"""
    index = json.loads((ROOT / 'assets/ship/fleet_tech.json').read_text(encoding='utf-8'))
    shared = json.loads((ROOT / 'assets/ship/ship_data.json').read_text(encoding='utf-8'))
    rows, aliases, points = [], defaultdict(set), defaultdict(int)
    for entry in index['ships']:
        gid = entry['group_type']
        base = shared[str(gid * 10 + 1)]
        row = dict(entry, name=base['name']['cn'], names=base['name'], nationality=base['nationality'],
                   rarity=('DR' if base['rarity_name'] in ('UR', 'DR') else 'PR') if entry['blueprint'] else base['rarity_name'],
                   type=base['type'])
        rows.append(row)
        if entry['technology']:
            points[str(row['nationality'])] += sum(entry['technology'][k] for k in ('pt_get', 'pt_upgrage', 'pt_level'))
        aliases[normalize_name(row['name'])].add(gid)
    # 改造名按真实群组归并，不能靠去掉“改”或相似名字猜测身份。
    known = {row['group_type'] for row in rows}
    for ship_id, row in shared.items():
        gid = row['group_type']
        if gid not in known or int(ship_id) // 10 != gid:
            continue
        for name in row.get('retrofit_names', []):
            aliases[normalize_name(name)].add(gid)
    return dict(version=index['version'], updated_at=index['updated_at'], source_commit=index['source_commit'],
                ships=rows, nations=index['nations'], nation_points=dict(points), max_points=sum(points.values()),
                aliases={name: next(iter(ids)) for name, ids in aliases.items() if len(ids) == 1})


def public_catalog():
    """名称索引仅供服务端确认身份，不重复传给浏览器。"""
    return {key: value for key, value in catalog().items() if key != 'aliases'}


def revision(record):
    """空记录也有稳定版本，供人工编辑进行并发保护。"""
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def merge_mind(record, ships):
    """累计已确认的正向证据；删除、排除计费及范围外缺失不会撤销历史。"""
    output = copy.deepcopy(record)
    observed = output.setdefault('observed', {})
    aliases = catalog()['aliases']
    for ship in ships:
        level = ship.get('level')
        if ship.get('review') or type(level) is not int or not 1 <= level <= 125:
            continue
        gid = aliases.get(normalize_name(ship.get('name', '')))
        if gid is None:
            continue
        key = str(gid)
        observed[key] = max(observed.get(key, 0), level)
    return output


def ship_type(value):
    """导驱与风帆各形态共用界面分类，但每条加成仅累计一次。"""
    return 1 if value in (20, 21) else 22 if value in (22, 23, 24) else value


def report(record):
    """按确认状态计算科技点、舰船加成及已录入的阵营科技加成。"""
    rows, bonuses = [], defaultdict(lambda: defaultdict(int))
    confirmed_points = completed = pending_star = 0

    def add(types, attr, value):
        for target in {ship_type(t) for t in types}:
            bonuses[str(target)][str(attr)] += value

    for info in catalog()['ships']:
        gid = str(info['group_type'])
        manual = record.get('manual', {}).get(gid, {})
        level = manual.get('level', record.get('observed', {}).get(gid))
        owned = manual.get('owned', True if level else None)
        full_star = manual.get('full_star')
        lv120 = manual.get('lv120', level >= 120 if level else None)
        if owned is not True:
            level = None
            full_star = lv120 = False if owned is False else None
        status = 'complete' if lv120 is True else 'unowned' if owned is False else 'unfinished' if owned and lv120 is False else 'pending'
        row = dict(group_type=info['group_type'], owned=owned, level=level, full_star=full_star, lv120=lv120,
                   status=status, manual=bool(manual), observed_level=record.get('observed', {}).get(gid), points=0)
        tech = info['technology']
        if tech:
            if owned:
                row['points'] += tech['pt_get']
                add(tech['add_get_shiptype'], tech['add_get_attr'], tech['add_get_value'])
                pending_star += full_star is None
            if full_star:
                row['points'] += tech['pt_upgrage']
            if lv120:
                row['points'] += tech['pt_level']
                add(tech['add_level_shiptype'], tech['add_level_attr'], tech['add_level_value'])
                completed += 1
        confirmed_points += row['points']
        rows.append(row)
    levels = record.get('nations', {})
    for key, value in levels.items():
        if value and key in catalog()['nations']:
            for types, attr, amount in catalog()['nations'][key][value - 1]:
                add(types, attr, amount)
    return dict(revision=revision(record), ships=rows, nation_levels=levels,
                bonuses={t: dict(attrs) for t, attrs in bonuses.items()}, confirmed_points=confirmed_points,
                completed=completed, pending_star=pending_star)
