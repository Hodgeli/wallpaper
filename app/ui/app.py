"""主窗口：三个标签页 + 状态栏 + 全局错误提示。"""
from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from ..api import WallhavenClient, WallpaperError
from ..config import APP_NAME, APP_VERSION, Config, get_config
from ..images import Downloader
from ..logsetup import get_logger
from ..store import get_store
from ..winwall import enable_dpi_awareness, system_dpi
from . import theme
from .async_util import UiQueue
from .history_tab import HistoryTab
from .search_tab import SearchTab
from .settings_tab import SettingsTab

log = get_logger("ui.app")

# 需要弹窗打断用户的错误类型；其余只在状态栏提示
SEVERE_KINDS = {"network", "auth", "disk", "timeout"}
POPUP_COOLDOWN = 30.0

ICON_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "assets" / "icon.ico",
    Path(__file__).resolve().parent.parent.parent / "assets" / "icon.ico",
]


class WallpaperPickerApp(tk.Tk):
    def __init__(self, cfg: Config | None = None) -> None:
        dpi_mode = enable_dpi_awareness()
        super().__init__()

        self.cfg = cfg or get_config()
        # 主题在窗口构造前就要定下来：ttk 样式是全局的，运行时换不了
        self.pal = theme.apply_theme(self, self.cfg.get("theme"))
        self._apply_scaling()
        log.info("启动 %s %s（DPI 感知=%s）", APP_NAME, APP_VERSION, dpi_mode)

        self.title(f"{APP_NAME} — wallhaven 壁纸助手 v{APP_VERSION}")
        self.minsize(1000, 640)
        self._restore_geometry()
        self._set_icon()

        self.client = WallhavenClient(self.cfg)
        self.downloader = Downloader(self.cfg)
        self.store = get_store()
        self.ui = UiQueue(self)
        self._last_popup: dict[str, float] = {}
        # 每个标签页各自记一条状态，切回来时恢复，避免「在设置页看到搜索的分页信息」
        self._status_by_tab: dict[str, tuple[str, str]] = {}

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.set_status("就绪")

    # ================================================================= 初始化
    def _apply_scaling(self) -> None:
        try:
            self.tk.call("tk", "scaling", max(1.0, system_dpi() / 72.0))
        except Exception:
            pass

    def _set_icon(self) -> None:
        for path in ICON_CANDIDATES:
            if path.is_file():
                try:
                    self.iconbitmap(default=str(path))
                    return
                except tk.TclError:
                    continue

    def _restore_geometry(self) -> None:
        w, h, x, y = self.cfg.window_geometry()
        w = max(1000, int(w))
        h = max(640, int(h))
        if x is not None and y is not None:
            try:
                x, y = int(x), int(y)
                sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
                if -w + 100 < x < sw - 100 and 0 <= y < sh - 80:
                    self.geometry(f"{w}x{h}+{x}+{y}")
                    return
            except (TypeError, ValueError, tk.TclError):
                pass
        # 居中
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def _save_geometry(self) -> None:
        try:
            self.update_idletasks()
            self.cfg.set("window", {
                "w": self.winfo_width(), "h": self.winfo_height(),
                "x": self.winfo_x(), "y": self.winfo_y(),
            })
            self.cfg.save()
        except tk.TclError:
            pass

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))

        self.search_tab = SearchTab(self.notebook, self)
        self.history_tab = HistoryTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)

        self._tabs = {"search": self.search_tab, "history": self.history_tab,
                      "settings": self.settings_tab}
        self.notebook.add(self.search_tab, text="  搜索  ")
        self.notebook.add(self.history_tab, text="  历史  ")
        self.notebook.add(self.settings_tab, text="  设置  ")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=1, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        self.status_wrap = tk.Frame(bar, bg=self.pal["status_bg"])
        self.status_wrap.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 8))
        self.status_wrap.columnconfigure(0, weight=1)

        self.lbl_status = tk.Label(self.status_wrap, text="", anchor="w",
                                   bg=self.pal["status_bg"], fg=self.pal["fg"],
                                   font=theme.font(9), padx=8, pady=4)
        self.lbl_status.grid(row=0, column=0, sticky="ew")

        self.progress = ttk.Progressbar(self.status_wrap, mode="determinate",
                                        maximum=1.0, length=200)
        self._progress_visible = False

    # ================================================================= 状态栏
    def _current_tab_name(self) -> str:
        try:
            cur = str(self.notebook.select())
        except tk.TclError:
            return "search"
        for name, tab in getattr(self, "_tabs", {}).items():
            if str(tab) == cur:
                return name
        return "search"

    def _default_status(self, name: str) -> tuple[str, str]:
        if name == "history":
            return f"共 {len(self.store.all())} 条下载记录", "info"
        if name == "settings":
            return "设置改动会立即保存", "info"
        return "就绪", "info"

    def _on_tab_changed(self, _event=None) -> None:
        name = self._current_tab_name()
        text, kind = self._status_by_tab.get(name) or self._default_status(name)
        self._paint_status(text, kind)

    def set_status(self, text: str, kind: str = "info") -> None:
        self._status_by_tab[self._current_tab_name()] = (text, kind)
        self._paint_status(text, kind)

    def _paint_status(self, text: str, kind: str) -> None:
        colors = {"info": self.pal["fg"], "ok": self.pal["ok"],
                  "warn": self.pal["warn"], "error": self.pal["error"]}
        lbl = getattr(self, "lbl_status", None)
        if lbl is None:          # _build() 还没跑到状态栏
            return
        try:
            lbl.configure(text=text, fg=colors.get(kind, self.pal["fg"]))
        except tk.TclError:
            pass

    def set_progress(self, value: float | None) -> None:
        try:
            if value is None:
                if self._progress_visible:
                    self.progress.grid_forget()
                    self._progress_visible = False
            else:
                self.progress.configure(value=max(0.0, min(1.0, value)))
                if not self._progress_visible:
                    self.progress.grid(row=0, column=1, sticky="e", padx=10)
                    self._progress_visible = True
        except tk.TclError:
            pass

    def report_error(self, exc: WallpaperError) -> None:
        log.error("用户可见错误 [%s]：%s", exc.kind, exc)
        self.set_progress(None)
        self.set_status(exc.message, "error")

        if exc.kind not in SEVERE_KINDS:
            return
        now = time.monotonic()
        if now - self._last_popup.get(exc.kind, 0.0) < POPUP_COOLDOWN:
            return
        self._last_popup[exc.kind] = now
        detail = f"\n\n技术细节：{exc.detail}" if exc.detail else ""
        messagebox.showerror(exc.title, f"{exc.message}{detail}", parent=self)

    # ================================================================= 工具
    def photo(self, image: Image.Image) -> ImageTk.PhotoImage:
        return ImageTk.PhotoImage(image)

    def select_tab(self, name: str) -> None:
        tab = self._tabs.get(name)
        if tab is not None:
            self.notebook.select(tab)

    def refresh_history(self) -> None:
        """整表重建。保存目录迁移这种"所有记录都变了"的场景才需要。"""
        try:
            self.history_tab.refresh()
        except Exception as exc:  # noqa: BLE001
            log.warning("刷新历史记录失败：%s", exc)

    def add_history_record(self, record: dict) -> None:
        """下载完一张就往历史里加一条，不重建整表。"""
        try:
            self.history_tab.add_record(record)
        except Exception as exc:  # noqa: BLE001
            log.warning("新增历史记录失败：%s", exc)
            self.refresh_history()

    def ask_custom_resolution(self) -> str | None:
        """弹出对话框让用户填 WxH，返回规范化的字符串或 None。"""
        value = simpledialog.askstring(
            "自定义分辨率",
            "输入最小分辨率（宽 x 高），例如 2560x1440：",
            parent=self,
            initialvalue="2560x1440",
        )
        if not value:
            return None
        text = value.strip().lower().replace(" ", "").replace("*", "x")
        parts = text.split("x")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            messagebox.showerror("格式不对", "请按「宽x高」的格式填写，例如 2560x1440。",
                                 parent=self)
            return None
        w, h = int(parts[0]), int(parts[1])
        if not (320 <= w <= 20000 and 200 <= h <= 20000):
            messagebox.showerror("数值不合理", "宽高请填 320~20000 之间的整数。", parent=self)
            return None
        return f"{w}x{h}"

    # ================================================================= 收尾
    def _on_close(self) -> None:
        self._save_geometry()
        # 先把各页还挂着的 after 定时器取消掉。窗口 destroy() 之后它们照样会触发，
        # Tcl 于是打一串 `invalid command name "..."`——纯噪音，但很容易被当成 bug。
        # 各页自己实现 cancel_pending_timers()，没有这个方法的页面跳过。
        for tab in (self.search_tab, self.history_tab, self.settings_tab):
            cancel = getattr(tab, "cancel_pending_timers", None)
            if cancel is None:
                continue
            try:
                cancel()
            except Exception as exc:  # noqa: BLE001 - 关窗不能被这里拖住
                log.debug("取消定时器失败：%s", exc)
        try:
            self.ui.stop()
        except Exception:
            pass
        log.info("程序退出")
        self.destroy()


def run_gui(cfg: Config | None = None) -> int:
    app = WallpaperPickerApp(cfg)
    app.mainloop()
    return 0
