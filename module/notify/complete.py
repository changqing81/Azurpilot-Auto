"""借用错误推送通道，隔离发送内部工具及舰队扫描的完成通知。"""

import json
import queue
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from deploy.atomic import atomic_read_text
from module.logger import logger


@dataclass(frozen=True)
class CompletionMessage:
    """不可变的通知快照；不跨线程共享业务配置对象。"""

    instance: str
    title: str
    content: str


def warn_notify_failure() -> None:
    """通知属于附加功能，日志故障也不能影响原业务或泄露通道凭据。"""
    try:
        logger.warning('完成通知处理失败，已跳过，不影响任务结果')
    except Exception:
        pass


def _load_push_config(instance: str) -> str | None:
    """只读错误推送配置，不构造会写回配置的 AzurLaneConfig。"""
    import yaml

    data = json.loads(atomic_read_text(f'./config/{instance}.json') or '{}')
    raw = data.get('Alas', {}).get('Error', {}).get('OnePushConfig')
    if not isinstance(raw, str) or not raw.strip():
        return None
    config = {}
    for item in yaml.safe_load_all(raw):
        if item is not None:
            config.update(item)
    # provider: null 是既有错误推送的关闭方式，无需新增开关。
    if not config.get('provider'):
        return None
    # 规范化后按完整通道配置分组；不同账号的凭据绝不混用。
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=True)


class CompletionDispatcher:
    """常驻 WebUI 的单发送线程，负责聚合、限频及网络故障隔离。"""

    MERGE_SECONDS = 5.0
    SEND_INTERVAL = 10.0
    MAX_BATCH = 32

    def __init__(self):
        # 有界队列保护长期运行时的内存；饱和时非阻塞跳过通知。
        self._queue = queue.Queue(maxsize=256)
        self._start_lock = threading.Lock()
        self._thread = None
        self._last_sent = None

    def submit(self, message: CompletionMessage) -> bool:
        """只入队，不在 UI 或日志消费线程读取配置、访问网络。"""
        try:
            with self._start_lock:
                if self._thread is None or not self._thread.is_alive():
                    thread = threading.Thread(
                        target=self._run, daemon=True, name='CompletionNotify',
                    )
                    thread.start()
                    self._thread = thread
            self._queue.put_nowait(message)
            return True
        except Exception:
            warn_notify_failure()
            return False

    def _run(self) -> None:
        """短时间完成的任务合并发送，单条通知失败后仍可处理后续任务。"""
        while True:
            try:
                messages = [self._queue.get()]
                deadline = time.monotonic() + self.MERGE_SECONDS
                while len(messages) < self.MAX_BATCH:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        messages.append(self._queue.get(timeout=remaining))
                    except queue.Empty:
                        break
                self._send_batch(messages)
            except Exception:
                warn_notify_failure()

    def _send_batch(self, messages: list[CompletionMessage]) -> None:
        """复用 onepush 错误通道；失败不重试，避免网络故障积压和推送风暴。"""
        groups = defaultdict(list)
        for message in messages:
            try:
                config = _load_push_config(message.instance)
                if config is not None:
                    groups[config].append(message)
            except Exception:
                warn_notify_failure()

        for config, batch in groups.items():
            try:
                from module.notify.notify import handle_notify

                title = batch[0].title if len(batch) == 1 else f'[AP 完成通知汇总] {len(batch)} 项'
                content = '\n\n'.join(
                    f'{message.title}\n{message.content}' for message in batch
                )
                if self._last_sent is not None:
                    delay = self.SEND_INTERVAL - (time.monotonic() - self._last_sent)
                    if delay > 0:
                        # 只等待专用发送线程，不占用 UI、扫描或通用异步执行器。
                        time.sleep(delay)
                self._last_sent = time.monotonic()
                handle_notify(config, title=title, content=content)
            except Exception:
                warn_notify_failure()


_dispatcher = CompletionDispatcher()


def notify_custom_complete(instance: str, title: str, content: str) -> bool:
    """异步借用实例的错误推送通道；返回值仅表示是否进入发送队列。"""
    try:
        return _dispatcher.submit(CompletionMessage(instance, title, content))
    except Exception:
        warn_notify_failure()
        return False
