"""装饰器工具模块。

提供基于配置的方法分发装饰器 Config.when()，以及 cached_property、
timer、function_drop、run_once 等常用装饰器，用于控制方法的执行行为。
"""

import os
import random
import re
from functools import wraps
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class Config:
    """根据配置调用同名不同实现的装饰器。

    func_list 结构示例:
    func_list = {
        'func1': [
            {'options': {'ENABLE': True}, 'func': 1},
            {'options': {'ENABLE': False}, 'func': 1}
        ]
    }
    """
    func_list = {}

    @classmethod
    def when(cls, **kwargs):
        """
        Args:
            **kwargs: AzurLaneConfig 中的任意配置项。

        Examples:
            @Config.when(USE_ONE_CLICK_RETIREMENT=True)
            def retire_ships(self, amount=None, rarity=None):
                pass

            @Config.when(USE_ONE_CLICK_RETIREMENT=False)
            def retire_ships(self, amount=None, rarity=None):
                pass
        """
        from module.logger import logger
        options = kwargs

        def decorate(func):
            name = func.__name__
            data = {'options': options, 'func': func}
            if name not in cls.func_list:
                cls.func_list[name] = [data]
            else:
                override = False
                for record in cls.func_list[name]:
                    if record['options'] == data['options']:
                        record['func'] = data['func']
                        override = True
                if not override:
                    cls.func_list[name].append(data)

            @wraps(func)
            def wrapper(self, *args, **kwargs):
                """
                Args:
                    self: ModuleBase 实例。
                    *args: 位置参数。
                    **kwargs: 关键字参数。
                """
                for record in cls.func_list[name]:

                    flag = [value is None or self.config.__getattribute__(key) == value
                            for key, value in record['options'].items()]
                    if not all(flag):
                        continue

                    return record['func'](self, *args, **kwargs)

                logger.warning(f'[装饰器] 没有选项适合 {name}，使用最后定义的函数')
                return func(self, *args, **kwargs)

            return wrapper

        return decorate


class cached_property(Generic[T]):
    """带类型支持的缓存属性装饰器。

    来源: https://github.com/pydanny/cached-property
    原始实现: https://github.com/bottlepy/bottle/commit/fa7733e075da0d790d809aa3d2f53071897e6f76

    每个实例只计算一次属性值，之后替换为普通属性。
    删除该属性后会重置缓存。
    """

    def __init__(self, func: Callable[..., T]):
        self.func = func

    def __get__(self, obj, cls) -> T:
        if obj is None:
            return self

        value = obj.__dict__[self.func.__name__] = self.func(obj)
        return value


def del_cached_property(obj, name):
    """安全地删除缓存属性。

    Args:
        obj: 目标对象。
        name: 属性名称。
    """
    try:
        del obj.__dict__[name]
    except KeyError:
        pass


def has_cached_property(obj, name):
    """检查属性是否已被缓存。

    Args:
        obj: 目标对象。
        name: 属性名称。

    Returns:
        如果属性已缓存则返回 True，否则返回 False。
    """
    return name in obj.__dict__


def set_cached_property(obj, name, value):
    """设置缓存属性。

    Args:
        obj: 目标对象。
        name: 属性名称。
        value: 属性值。
    """
    obj.__dict__[name] = value


def function_drop(rate=0.5, default=None):
    """随机丢弃函数调用，用于模拟模拟器卡死的测试场景。

    Args:
        rate: 丢弃概率，取值范围 0 到 1。
        default: 被丢弃时返回的默认值。

    Examples:
        @function_drop(0.3)
        def click(self, button, record_check=True):
            pass

        30% 概率:
        INFO | Dropped: module.device.device.Device.click(REWARD_GOTO_MAIN, record_check=True)
        70% 概率:
        INFO | Click (1091,  628) @ REWARD_GOTO_MAIN
    """
    from module.logger import logger

    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if random.uniform(0, 1) > rate:
                return func(*args, **kwargs)
            else:
                cls = ''
                arguments = [str(arg) for arg in args]
                if len(arguments):
                    matched = re.search('<(.*?) object at', arguments[0])
                    if matched:
                        cls = matched.group(1) + '.'
                        arguments.pop(0)
                arguments += [f'{k}={v}' for k, v in kwargs.items()]
                arguments = ', '.join(arguments)
                logger.info(f'[装饰器] 已丢弃: {cls}{func.__name__}({arguments})')
                return default

        return wrapper

    return decorate


def run_once(f):
    """确保函数只执行一次，无论被调用多少次。

    Examples:
        @run_once
        def my_function(foo, bar):
            return foo + bar

        while 1:
            my_function()

    Examples:
        def my_function(foo, bar):
            return foo + bar

        action = run_once(my_function)
        while 1:
            action()
    """

    def wrapper(*args, **kwargs):
        if not wrapper.has_run:
            wrapper.has_run = True
            return f(*args, **kwargs)

    wrapper.has_run = False
    return wrapper


def file_cached(*files):
    """按依赖文件 mtime 自动失效的缓存装饰器。

    与 functools.lru_cache 的差别：缓存项绑定依赖文件的修改时间戳，
    文件被外部更新（如舰船数据自动同步写入新名单）后，下一次调用即
    自动重新计算——长驻进程（WebUI、任务子进程）无需重启或手工清缓存。

    Args:
        *files: 依赖文件路径；任一项可以是返回路径的可调用对象（延迟解析，
            适配测试中替换类属性的场景）。任一文件 mtime 变化即整体失效。

    Examples:
        @file_cached('assets/ship/ship_data.json')
        def catalog():
            pass

        catalog()  # 首次计算
        catalog()  # 命中缓存
        # 文件被更新后
        catalog()  # 自动重新计算
    """

    def decorate(func):
        cache = {}

        def current_stamp():
            values = []
            for file in files:
                path = file() if callable(file) else file
                try:
                    values.append((os.fspath(path), os.stat(path).st_mtime_ns))
                except OSError:
                    values.append((os.fspath(path), None))
            return tuple(values)

        @wraps(func)
        def wrapper(*args, **kwargs):
            # 先完成计算再落缓存，避免并发读到半构造状态；
            # 文件读取失败的 None 戳同样成立（加载函数自身兜底空值）。
            key = (args, tuple(sorted(kwargs.items())))
            stamp = current_stamp()
            entry = cache.get(key)
            if entry is None or entry[0] != stamp:
                cache[key] = (stamp, func(*args, **kwargs))
            return cache[key][1]

        def cache_clear():
            cache.clear()

        wrapper.cache_clear = cache_clear
        return wrapper

    return decorate
