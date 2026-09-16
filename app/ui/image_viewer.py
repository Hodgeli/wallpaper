"""弹出式大图查看窗口：滚轮以光标为中心缩放、拖动平移、快捷键切换适应/1:1。"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from PIL import Image

from . import theme
from .zoomcanvas import ZoomCanvas


class ImageViewer(tk.Toplevel):
    """独立窗口显示原图。同一时间只保留一个实例。"""

    def __init__(self, master: tk.Misc, pal: dict[str, str], image: Image.Image,
                 *, title: str = "大图预览", subtitle: str = "",
                 on_apply: Callable[[], None] | None = None) -> None:
        super().__init__(master)
        self.pal = pal
        self.on_apply = on_apply

        self.title(title)
        self.configure(background=pal["bg"])
        self.minsize(720, 520)

        # 默认占屏幕 82%，居中
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = int(sw * 0.82), int(sh * 0.82)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ---------------------------------------------------- 顶部信息
        head = ttk.Frame(self, style="TFrame")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text=title, style="TLabel",
                  font=theme.font(11, True)).grid(row=0, column=0, sticky="w")
        if subtitle:
            ttk.Label(head, text=subtitle, style="Muted.TLabel",
                      font=theme.font(9)).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(head, text="滚轮缩放 · 按住拖动平移 · 双击切换适应/1:1",
                  style="Hint.TLabel").grid(row=0, column=1, rowspan=2, sticky="e")

        # ---------------------------------------------------- 画布
        self.canvas = ZoomCanvas(self, bg=pal["thumb_bg"], on_view_change=self._on_view)
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=12)

        # ---------------------------------------------------- 工具条
        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=2, column=0, sticky="ew", padx=12, pady=10)

        ttk.Button(bar, text="适应窗口", command=self.canvas.fit).pack(side="left")
        ttk.Button(bar, text="1:1", command=self.canvas.actual_size).pack(side="left", padx=6)
        ttk.Button(bar, text="－", width=3,
                   command=lambda: self.canvas.zoom_by(1 / 1.25)).pack(side="left")
        ttk.Button(bar, text="＋", width=3,
                   command=lambda: self.canvas.zoom_by(1.25)).pack(side="left", padx=(6, 0))

        self.lbl_zoom = ttk.Label(bar, text="100%", style="Muted.TLabel", width=18)
        self.lbl_zoom.pack(side="left", padx=12)

        ttk.Button(bar, text="关闭", command=self.destroy).pack(side="right")
        if on_apply:
            ttk.Button(bar, text="下载并设为壁纸", style="Accent.TButton",
                       command=self._apply).pack(side="right", padx=8)

        # ---------------------------------------------------- 快捷键
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<plus>", lambda _e: self.canvas.zoom_by(1.25))
        self.bind("<equal>", lambda _e: self.canvas.zoom_by(1.25))
        self.bind("<minus>", lambda _e: self.canvas.zoom_by(1 / 1.25))
        self.bind("<Key-0>", lambda _e: self.canvas.fit())
        self.bind("<Key-1>", lambda _e: self.canvas.actual_size())

        self.canvas.set_image(image)
        self.after(60, self.canvas.focus_set)
        self.transient(master)

    # ================================================================= 内部
    def _on_view(self, scale: float, zoom: float) -> None:
        try:
            self.lbl_zoom.configure(
                text=f"{scale * 100:.0f}%　(适应×{zoom:.1f})")
        except tk.TclError:
            pass

    def _apply(self) -> None:
        if self.on_apply:
            self.on_apply()
        self.destroy()


_viewer: ImageViewer | None = None


def open_viewer(master: tk.Misc, pal: dict[str, str], image: Image.Image, **kwargs: Any) -> ImageViewer:
    """打开大图窗口；已经有的话先关掉，避免堆一堆窗口。"""
    global _viewer
    if _viewer is not None:
        try:
            _viewer.destroy()
        except tk.TclError:
            pass
    _viewer = ImageViewer(master, pal, image, **kwargs)
    return _viewer
