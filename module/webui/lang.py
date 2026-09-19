"""
Web界面多语言支持。

管理 WebUI 的语言切换和翻译文本加载。从 i18n JSON 文件读取翻译，
通过 t() 函数获取当前语言的翻译文本，支持 zh-CN/en-US/ja-JP/zh-TW。
"""

from typing import Dict

from module.config.deep import deep_iter
from module.config.utils import LANGUAGES, filepath_i18n, read_file
from module.submodule.utils import list_mod_dir
from module.webui.setting import State

LANG = "zh-CN"
TRANSLATE_MODE = False


def set_language(s: str, refresh=False):
    global LANG
    for i, lang in enumerate(LANGUAGES):
        # pywebio.session.info.user_language return `zh-CN` or `zh-cn`, depends on browser
        if lang.lower() == s.lower():
            LANG = LANGUAGES[i]
            break
    else:
        LANG = "en-US"

    State.deploy_config.Language = LANG

    # 按需加载：切换到的语言若尚未加载，立即补齐，避免翻译缺失
    load_language(LANG)

    if refresh:
        from pywebio.session import run_js

        run_js("location.reload();")


def t(s, *args, **kwargs):
    """
    Get translation.
    other args, kwargs pass to .format()
    """
    if TRANSLATE_MODE:
        return s
    return _t(s, LANG).format(*args, **kwargs)


def _t(s, lang=None):
    """
    Get translation, ignore TRANSLATE_MODE
    """
    if not lang:
        lang = LANG
    try:
        return dic_lang[lang][s]
    except KeyError:
        print(f"Language key ({s}) not found")
        return s


dic_lang: Dict[str, Dict[str, str]] = {}


def load_language(lang: str) -> None:
    """加载一种语言的全部翻译；已加载的语言直接跳过。

    全量加载 5 种语言需要读入 ~1.4MB JSON，是 WebUI 就绪的显著开销，
    因此启动时只加载当前语言（与作为回退底座的 en-US），
    其余语言在切换语言时惰性加载。
    """
    existing = dic_lang.get(lang)
    if existing:
        return

    dic_lang.setdefault(lang, {})

    for mod_name, dir_name in list_mod_dir():
        for path, v in deep_iter(read_file(filepath_i18n(lang, mod_name)), depth=3):
            dic_lang[lang][".".join(path)] = v

    for path, v in deep_iter(read_file(filepath_i18n(lang)), depth=3):
        dic_lang[lang][".".join(path)] = v

    if lang == "ja-JP":
        # 日文翻译缺失的键回退英文，英文底座必须先就位
        load_language("en-US")
        for key in dic_lang["ja-JP"].keys():
            if dic_lang["ja-JP"][key] == key:
                dic_lang["ja-JP"][key] = dic_lang["en-US"][key]


def reload():
    """加载当前语言（及英文回退底座）；其余语言在切换时惰性加载。"""
    global LANG
    if LANG not in LANGUAGES:
        # deploy 配置的语言值非法时回退英文，避免 t() 全部命中原文
        LANG = "en-US"
    load_language(LANG)
    if LANG != "en-US":
        load_language("en-US")


def reload_all():
    """加载全部语言；仅供翻译模式等需要全量语言的场景使用。"""
    for lang_name in LANGUAGES:
        load_language(lang_name)
