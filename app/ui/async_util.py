"""线程与 tkinter 主线程之间的桥。

tkinter 不是线程安全的：所有网络请求都在工作线程里跑，
结果统一投递到这个队列，再由主线程按固定间隔取出来处理。
"""
from __future__ import annotations

import queue
import threading
import traceback
from collections.abc import Callable
from typing import Any

from ..logsetup import get_logger

log = get_logger("ui.async")

# 后台线程上限。原来是「一次 run_bg 起一个线程」，快速划过缩略图时每次选中都会
# 起一个预览任务，线程数没有上限。8 个够用：同一时刻真正并发的任务通常只有
# 搜索 / 缩略图批次 / 原图预览 / 下载这几种。
UI_WORKERS = 8


class TaskPool:
    """固定大小的后台线程池。

    没用 `concurrent.futures.ThreadPoolExecutor`：它的工作线程不是 daemon，
    解释器退出时会 join 它们——用户在下载原图时点关闭，窗口没了但进程会一直
    挂到下载超时（最长 60 秒）才退。这里自己起 daemon 线程，关窗就是立刻退。
    """

    def __init__(self, workers: int) -> None:
        self._q: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._closed = False
        for i in range(max(1, workers)):
            threading.Thread(target=self._loop, name=f"bg{i}", daemon=True).start()

    def submit(self, job: Callable[[], None]) -> None:
        if self._closed:
            return
        self._q.put(job)

    def _loop(self) -> None:
        while True:
            job = self._q.get()
            if job is None:          # 关闭信号
                return
            try:
                job()
            except Exception as exc:  # noqa: BLE001 - 兜底，别让线程死掉
                log.error("后台任务异常退出：%s", exc)

    def close(self) -> None:
        """停掉池子：丢弃还没开跑的排队任务，已经在跑的让它跑完。

        线程是 daemon，进程退出时不需要 join——这正是没用 ThreadPoolExecutor
        的理由（那个会在解释器退出时 join 工作线程）。
        """
        self._closed = True
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break


class UiQueue:
    """把工作线程的结果安全地送回主线程。"""

    def __init__(self, widget: Any, interval: int = 60,
                 workers: int = UI_WORKERS) -> None:
        self.widget = widget
        self.interval = interval
        self._q: queue.Queue[tuple[str, tuple]] = queue.Queue()
        self._handlers: dict[str, list[Callable[..., None]]] = {}
        self._pool = TaskPool(workers)
        self._running = True
        self._job: str | None = None      # 已排好的那一次 _poll，stop() 时要取消
        self._schedule()

    # ------------------------------------------------------------ 注册/投递
    def on(self, kind: str, handler: Callable[..., None]) -> None:
        self._handlers.setdefault(kind, []).append(handler)

    def post(self, kind: str, *payload: Any) -> None:
        self._q.put((kind, payload))

    def run_bg(self, fn: Callable[..., Any], *args: Any,
               on_ok: str | None = None, on_error: str = "error", **kwargs: Any) -> None:
        """在工作线程里跑 fn；成功投递 on_ok，异常投递 on_error。

        线程来自固定大小的池子（`UI_WORKERS` 个），任务排队执行。
        注意：**任务只是"排队"，不保证一定会跑**——关闭窗口时排队的任务会被丢掉。
        """
        def _worker() -> None:
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 统一转成 UI 事件
                log.debug("后台任务异常：%s\n%s", exc, traceback.format_exc())
                self.post(on_error, exc)
            else:
                if on_ok:
                    self.post(on_ok, result)
        self._pool.submit(_worker)

    # ------------------------------------------------------------ 主线程循环
    def _schedule(self) -> None:
        if not self._running:
            return
        try:
            self._job = self.widget.after(self.interval, self._poll)
        except Exception:
            self._running = False

    def _poll(self) -> None:
        handled = 0
        while handled < 60:
            try:
                kind, payload = self._q.get_nowait()
            except queue.Empty:
                break
            handled += 1
            for handler in self._handlers.get(kind, []):
                try:
                    handler(*payload)
                except Exception as exc:  # noqa: BLE001
                    log.error("UI 事件处理失败 [%s]：%s\n%s", kind, exc,
                              traceback.format_exc())
        self._schedule()

    def stop(self) -> None:
        """停掉轮询和线程池。

        顺手 `after_cancel` 掉已经排好的那一次 `_poll`：不取消的话，窗口
        `destroy()` 之后它还会被 Tcl 触发一次，打出一串
        `invalid command name "..._poll"` —— 看着像 bug，其实是自己留的尾巴。
        """
        self._running = False
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._pool.close()
