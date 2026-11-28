"""WebUI 配置搜索的纯数据处理逻辑。"""

from dataclasses import dataclass, field
import json
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple


def normalize_search_text(value: Any) -> str:
    """规范化搜索文本，使大小写和连续空白不影响匹配。"""
    return " ".join(str(value or "").casefold().split())


@dataclass(frozen=True)
class ConfigSearchEntry:
    """一个可在 WebUI 中定位的配置参数。"""

    task: str
    group: str
    argument: str
    task_name: str
    group_name: str
    argument_name: str
    help_text: str = ""
    _primary_terms: Tuple[str, ...] = field(init=False, repr=False)
    _context_terms: Tuple[str, ...] = field(init=False, repr=False)
    _help_terms: Tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_primary_terms",
            tuple(
                term
                for term in (
                    normalize_search_text(self.argument_name),
                    normalize_search_text(self.key),
                )
                if term
            ),
        )
        object.__setattr__(
            self,
            "_context_terms",
            tuple(
                term
                for term in (
                    normalize_search_text(self.task_name),
                    normalize_search_text(self.group_name),
                    normalize_search_text(self.task),
                    normalize_search_text(self.group),
                )
                if term
            ),
        )
        object.__setattr__(
            self,
            "_help_terms",
            tuple(term for term in (normalize_search_text(self.help_text),) if term),
        )

    @property
    def key(self) -> str:
        """返回用户熟悉的技术配置路径。"""
        return f"{self.task}.{self.group}.{self.argument}"


def config_search_config_signature(config: Any) -> str:
    """生成用于判定搜索索引是否过期的配置签名。"""
    return json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def should_render_config_argument(
    task: str,
    group: str,
    argument: str,
    display: str | None,
    widget_type: str,
    options: Sequence[Any],
    value: Any,
) -> bool:
    """判断参数是否会在当前配置页实际渲染。"""
    if display == "hide":
        return False
    if widget_type == "storage" and value == {}:
        return False
    if (
        task == "GemsFarming"
        and group == "Campaign"
        and argument == "Event"
        and widget_type == "select"
        and len(options) == 1
    ):
        return False
    return True


def config_search_field_scope(task: str, group: str, argument: str) -> str:
    """生成参数外层稳定 scope 名称。"""
    return f"config_search_field_{task}_{group}_{argument}"


def build_config_search_focus_script(scope: str) -> str:
    """生成滚动、聚焦并短暂高亮搜索结果的浏览器脚本。"""
    scope_id = f"pywebio-scope-{scope}"
    return f"""
        (function () {{
            var attempts = 0;
            function focusTarget() {{
                var target = document.getElementById({json.dumps(scope_id)});
                var container = document.getElementById("pywebio-scope-groups");
                if (!target || !container) {{
                    attempts += 1;
                    if (attempts < 10) window.setTimeout(focusTarget, 50);
                    return;
                }}

                var offset = target.getBoundingClientRect().top
                    - container.getBoundingClientRect().top + container.scrollTop - 16;
                container.scrollTo({{top: Math.max(0, offset), behavior: "smooth"}});

                target.classList.remove("config-search-target");
                void target.offsetWidth;
                target.classList.add("config-search-target");
                window.setTimeout(function () {{
                    target.classList.remove("config-search-target");
                }}, 1800);

                // 等待 Bootstrap Select 完成原生下拉框替换后再聚焦可见控件。
                window.setTimeout(function () {{
                    var codeMirrorInput = target.querySelector(
                        ".CodeMirror textarea:not([disabled]):not([readonly])"
                    );
                    var controls = target.querySelectorAll(
                        "input:not([disabled]):not([readonly]), textarea:not([disabled]):not([readonly]), select:not([disabled])"
                    );
                    var control = codeMirrorInput && codeMirrorInput.getClientRects().length
                        ? codeMirrorInput
                        : Array.prototype.find.call(controls, function (candidate) {{
                            return candidate.getClientRects().length;
                        }});
                    if (!control) {{
                        control = target.querySelector(".task-priority-list");
                        if (control) control.tabIndex = 0;
                    }}
                    if (!control) return;

                    var focusControl = control;
                    if (control.matches("select")) {{
                        var selectContainer = control.closest(".bootstrap-select");
                        var selectButton = selectContainer && selectContainer.querySelector(
                            "button:not([disabled])"
                        );
                        if (selectButton && selectButton.getClientRects().length) {{
                            focusControl = selectButton;
                        }}
                    }}
                    if (focusControl.tabIndex < 0) focusControl.tabIndex = 0;
                    try {{
                        focusControl.focus({{preventScroll: true}});
                    }} catch (_) {{
                        focusControl.focus();
                    }}
                    if (document.activeElement !== focusControl) focusControl.focus();
                }}, 360);
            }}
            focusTarget();
        }})();
    """


def build_config_search_result_click_script(pin_name: str) -> str:
    """生成将结果点击转发给单一 Pin 回调的浏览器脚本。"""
    return f"""
        (function () {{
            var listenerName = "__alasConfigSearchResultClick";
            if (window[listenerName]) return;

            window[listenerName] = function (event) {{
                var source = event.target;
                var result = source instanceof Element
                    ? source.closest(".config-search-result[data-config-search-key]")
                    : null;
                if (!result) return;
                event.preventDefault();

                var selectionInput = document.querySelector(
                    'input[name="' + {json.dumps(pin_name)} + '"]'
                );
                if (!selectionInput) return;

                selectionInput.value = result.dataset.configSearchKey;
                selectionInput.dispatchEvent(new Event("input", {{bubbles: true}}));
                selectionInput.dispatchEvent(new Event("change", {{bubbles: true}}));
            }};
            document.addEventListener("click", window[listenerName]);
        }})();
    """


def _visible_options(
    arg_definition: Dict[str, Any], server: str | None
) -> List[Any]:
    """按当前服务器裁剪某参数的 option，与老 UI _iter_group_arguments 同口径。

    老 UI 里 select 的可见选项会先被 option_{server} 白名单过滤，再允许
    option_{server} 整表替换。这一步会影响「只剩一个选项时不渲染」的判定
    （例如 GemsFarming.Campaign.Event 在部分服务器上只剩一个选项），
    所以搜索索引必须走同一套裁剪，否则会搜出页面上根本没渲染的参数。
    """
    options = list(arg_definition.get("option") or [])
    if not server:
        return options
    whitelist = arg_definition.get(f"option_{server}")
    if isinstance(whitelist, list):
        options = [option for option in options if option in whitelist]
    server_options = arg_definition.get(f"option_{server}")
    if arg_definition.get("type") == "select" and isinstance(server_options, list):
        if server_options:
            options = list(server_options)
    return options


def build_config_search_index(
    args: Dict[str, Any],
    config: Dict[str, Any],
    tasks: Iterable[str],
    translate: Callable[..., str],
    server: str | None = None,
) -> List[ConfigSearchEntry]:
    """按「当前实例实际可见参数」构建配置搜索索引。

    老 UI（PyWebIO）与新 UI（React）共用这一份口径，两边的数据源相同：
    args.json 给 schema，用户配置给 storage 判空依据，translate 给任务名 /
    组名 / 参数名 / 帮助文本。与 TaskConfigMixin._build_config_search_entries
    的区别只是把「取菜单里 page=setting 的任务」这一步交给调用方，
    避免把 PyWebIO 会话对象拖进这个纯数据模块。

    Args:
        args: args.json 全量定义（task → group → argument）。
        config: 当前实例的用户配置（task → group → argument → value）。
        tasks: 参与搜索的任务名序列（重复项自动跳过）。
        translate: 翻译函数，签名与 module.webui.lang.t 一致；
            缺键时返回键本身即为「没有翻译」，这里据此回退技术名。
        server: 当前服务器（cn/en/jp/tw），用于裁剪服务器限定的选项；
            省略时不做裁剪。

    Returns:
        按菜单顺序排列的 ConfigSearchEntry 列表。
    """
    from module.config.deep import deep_get

    def _text(key: str, fallback: str) -> str:
        """取翻译，缺失（返回键本身）时回退技术名。"""
        translated = translate(key)
        return fallback if translated == key else translated

    entries: List[ConfigSearchEntry] = []
    seen_tasks = set()
    for task in tasks:
        if task in seen_tasks or task not in args:
            continue
        seen_tasks.add(task)
        task_name = _text(f"Task.{task}.name", task)
        for group_name, group_args in args[task].items():
            if not isinstance(group_args, dict) or group_name.startswith("_"):
                continue
            group_title = _text(f"{group_name}._info.name", group_name)
            for arg_name, arg_definition in group_args.items():
                if arg_name.startswith("_") or not isinstance(arg_definition, dict):
                    continue
                widget_type = arg_definition.get("type", "input")
                display = arg_definition.get("display", None)
                # storage 的 visibility 规则（见 should_render_config_argument）
                # 看的是「当前值是否为空 dict」；模板默认值本来就空时按空处理。
                raw_value = deep_get(config, [task, group_name, arg_name], None)
                if raw_value is None:
                    raw_value = arg_definition.get("value")
                value = {} if raw_value in (None, {}) else raw_value
                if not should_render_config_argument(
                    task,
                    group_name,
                    arg_name,
                    display,
                    widget_type,
                    _visible_options(arg_definition, server),
                    value,
                ):
                    continue
                entries.append(
                    ConfigSearchEntry(
                        task=task,
                        group=group_name,
                        argument=arg_name,
                        task_name=task_name,
                        group_name=group_title,
                        argument_name=_text(f"{group_name}.{arg_name}.name", arg_name),
                        help_text=_text(f"{group_name}.{arg_name}.help", ""),
                    )
                )
    return entries


def config_search_entry_to_dict(entry: ConfigSearchEntry) -> Dict[str, str]:
    """序列化一条索引项，供新 UI 的 REST 接口回传。

    key 是三段全路径（Task.Group.Arg），前端用它做跳转与参数定位；
    其余驼峰字段直接给结果列表渲染。
    """
    return {
        "key": entry.key,
        "task": entry.task,
        "group": entry.group,
        "argument": entry.argument,
        "taskName": entry.task_name,
        "groupName": entry.group_name,
        "argumentName": entry.argument_name,
        "help": entry.help_text,
    }


def _term_match_rank(query: str, terms: Iterable[str], base_rank: int) -> int | None:
    """返回某类搜索词的匹配优先级，未命中时返回 ``None``。"""
    ranks: List[int] = []
    for term in terms:
        if query not in term:
            continue
        if term == query:
            ranks.append(base_rank)
        elif term.startswith(query):
            ranks.append(base_rank + 1)
        else:
            ranks.append(base_rank + 2)
    return min(ranks) if ranks else None


def search_config_entries(
    entries: Iterable[ConfigSearchEntry], query: str, limit: int = 20
) -> Tuple[List[ConfigSearchEntry], int]:
    """按匹配相关性筛选配置参数，并保留原始菜单顺序作为次级排序。"""
    normalized_query = normalize_search_text(query)
    if not normalized_query or limit <= 0:
        return [], 0

    matched: List[Tuple[int, int, ConfigSearchEntry]] = []
    for index, entry in enumerate(entries):
        ranks = (
            _term_match_rank(normalized_query, entry._primary_terms, 0),
            _term_match_rank(normalized_query, entry._context_terms, 3),
            _term_match_rank(normalized_query, entry._help_terms, 6),
        )
        rank = min((value for value in ranks if value is not None), default=None)
        if rank is not None:
            matched.append((rank, index, entry))

    matched.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in matched[:limit]], len(matched)
