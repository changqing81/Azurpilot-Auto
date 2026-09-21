import logging
from typing import Any, Callable

from rich.console import Console, ConsoleRenderable
from rich.highlighter import RegexHighlighter
from rich.logging import RichHandler
from rich.theme import Theme

class HTMLConsole(Console): ...
class Highlighter(RegexHighlighter): ...

WEB_THEME: Theme

logger_debug: bool
pyw_name: str

# 允许写文件日志的入口脚本名 / 不写文件日志的多进程内部进程名
FILE_LOG_ENTRIES: tuple[str, ...]
SKIP_PROCESS_NAMES: tuple[str, ...]

file_formatter: logging.Formatter
console_formatter: logging.Formatter
web_formatter: logging.Formatter

stdout_console: Console
console_hdlr: RichHandler

def resolve_log_name(
    name: str | None = None,
    pname: str | None = None,
    argv0: str | None = None,
) -> str | None: ...
def set_file_logger(
    name: str | None = None,
) -> None: ...
def set_func_logger(
    func: Callable[[ConsoleRenderable], None],
) -> None: ...

class __logger(logging.Logger):
    def rule(
        self,
        title: str = "",
        *,
        characters: str = "-",
        style: str = "rule.line",
        end: str = "\n",
        align: str = "center",
    ) -> None: ...
    def hr(
        self,
        title,
        level: int = 3,
    ) -> None: ...
    def attr(
        self,
        name,
        text,
    ) -> None: ...
    def attr_align(
        self,
        name,
        text,
        front="",
        align: int = 22,
    ) -> None: ...
    def set_file_logger(
        self,
        name: str | None = None,
    ) -> None: ...
    def set_func_logger(
        self,
        func: Callable[[ConsoleRenderable], None],
    ) -> None: ...
    def print(
        self,
        *objects: ConsoleRenderable,
        **kwargs,
    ) -> None: ...

logger: __logger
