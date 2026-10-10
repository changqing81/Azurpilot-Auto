"""用游戏公开 Lua 数据补齐原计算器缓存缺少的联动舰，保留已有资料。"""
import argparse
import json
import re
from pathlib import Path

from dev_tools.ship_data_extractor import RARITY_NAME_MAP, parse_lua_ship_blocks, parse_ship_data_by_type
from module.runtime.mind_calculator import RARITIES


def supplement(catalog, lua_repo, names):
    """只加入名单中存在且稀有度明确的普通联动舰，不猜测改造基础身份。"""
    root = Path(lua_repo) / 'CN'
    codes = parse_lua_ship_blocks(str(root / 'sharecfg/name_code.lua'))
    stats = parse_lua_ship_blocks(str(root / 'sharecfgdata/ship_data_statistics.lua'))
    types = parse_ship_data_by_type(str(root / 'sharecfg/ship_data_by_type.lua'))
    candidates = {}
    for row in stats.values():
        if not 101 <= row.get('nationality', 0) <= 117:
            continue
        name = re.sub(r'\{namecode:(\d+)\}', lambda match: codes[int(match[1])]['name'], row['name']).strip()
        rarity = RARITY_NAME_MAP.get(row.get('rarity'))
        if name not in names or name.endswith('改') or rarity not in RARITIES:
            continue
        if name in catalog['ships']:
            catalog['ships'][name]['group'] = '联动'
            continue
        info = dict(name=name, rarity=rarity, base_rarity=rarity, base_name=name,
                    group='联动', type=types.get(row.get('type'), ''))
        if name in candidates and candidates[name] != info:
            raise ValueError(f'联动舰资料存在冲突：{name}')
        candidates[name] = info
    catalog['ships'].update(sorted(candidates.items()))
    catalog['supplement_source'] = 'AzurLaneLuaScripts CN ship_data_statistics 联动舰名称及基础稀有度'
    return len(candidates)


def main():
    """按现有名称白名单补齐计费资料，可重复生成而不覆盖原缓存。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lua-repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('assets/ship/mind_calculator.json'))
    args = parser.parse_args()
    data = json.loads(args.output.read_text(encoding='utf-8'))
    names = json.loads(Path('assets/ship/ship_names.json').read_text(encoding='utf-8'))['cn']
    count = supplement(data, args.lua_repo, names)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'补齐 {count} 艘联动舰，共 {len(data["ships"])} 艘')


if __name__ == '__main__':
    main()
