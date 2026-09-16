"""可缩放、可平移的图片画布。

- 滚轮：以鼠标光标为锚点缩放（光标下的那个点保持不动）
- 按住左键拖动：平移
- 双击：在「适应窗口」和「1:1」之间切换

渲染策略：只裁剪当前视口对应的源图区域再缩放，而不是把整张图放大后贴上去，
所以放到 12 倍也不会生成巨大的 PhotoImage。拖动过程中用双线性快速渲染，
停手 180ms 后再用 LANCZOS 重渲染一遍，兼顾流畅与清晰。
"""
from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable

from PIL import Image, ImageTk

MIN_ZOOM = 1.0     # 相对「适应窗口」的最小倍数
MAX_ZOOM = 12.0
ZOOM_STEP = 1.15   # 每格滚轮的缩放倍率
FAST_RESAMPLE = Image.BILINEAR
FINAL_RESAMPLE = Image.LANCZOS
PIXEL_RESAMPLE = Image.NEAREST   # 放得很大时直接看像素，别糊成一团
PIXEL_ZOOM_THRESHOLD = 6.0


class ZoomCanvas(tk.Canvas):
    def __init__(self, master: tk.Misc, *, bg: str = "#e3e6ea",
                 on_view_change: Callable[[float, float], None] | None = None) -> None:
        super().__init__(master, highlightthickness=0, bd=0, bg=bg)
        self._src: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._img_id = self.create_image(0, 0, anchor="nw")
        self._fit = 1.0          # 适应窗口时的缩放比
        self._zoom = 1.0         # 相对适应的倍数
        self._cx = 0.0           # 视口中心在源图坐标系里的位置
        self._cy = 0.0
        self._drag_from: tuple[int, int, float, float] | None = None
        self._settle_job: str | None = None
        self._last_fast = 0.0
        self.on_view_change = on_view_change

        self.bind("<Configure>", self._on_configure)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<Enter>", lambda _e: self.focus_set())
        self.configure(cursor="fleur")

    # ================================================================= 对外
    def set_image(self, image: Image.Image | None, *, reset_view: bool = True) -> None:
        """换图。reset_view=False 时保持当前的缩放与位置（用于低清换高清）。"""
        self._src = image
        if reset_view:
            self._zoom = 1.0
            self._recompute_fit()
            self._center()
        self._render(final=True)

    def clear(self) -> None:
        self._src = None
        self._photo = None
        self.itemconfigure(self._img_id, image="")

    def has_image(self) -> bool:
        return self._src is not None

    def fit(self) -> None:
        self._zoom = 1.0
        self._recompute_fit()
        self._center()
        self._render(final=True)

    def actual_size(self) -> None:
        """1:1，即一个源图像素对应一个屏幕像素。"""
        if not self._src or self._fit <= 0:
            return
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, 1.0 / self._fit))
        self._render(final=True)

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        self._clamp_center()
        self._render(final=True)

    def zoom_by(self, factor: float, anchor: tuple[int, int] | None = None) -> None:
        if not self._src:
            return
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            return
        if anchor is None:
            anchor = (self.winfo_width() // 2, self.winfo_height() // 2)
        self._zoom_at(new_zoom, *anchor)

    def zoom_ratio(self) -> float:
        """相对源图的真实缩放比（1.0 表示一个源图像素占一个屏幕像素）。"""
        return self._fit * self._zoom

    def image_size(self) -> tuple[int, int]:
        return self._src.size if self._src else (0, 0)

    # ================================================================= 内部
    def _recompute_fit(self) -> None:
        if not self._src:
            self._fit = 1.0
            return
        cw = max(1, self.winfo_width())
        ch = max(1, self.winfo_height())
        iw, ih = self._src.size
        self._fit = min(cw / iw, ch / ih)

    def _center(self) -> None:
        if self._src:
            self._cx = self._src.width / 2
            self._cy = self._src.height / 2

    def _clamp_center(self) -> None:
        """把视口中心限制在源图范围内，避免拖出图片外面。"""
        if not self._src:
            return
        iw, ih = self._src.size
        cw, ch = max(1, self.winfo_width()), max(1, self.winfo_height())
        scale = max(1e-6, self._fit * self._zoom)
        half_w, half_h = cw / (2 * scale), ch / (2 * scale)
        self._cx = iw / 2 if half_w * 2 >= iw else min(max(self._cx, half_w), iw - half_w)
        self._cy = ih / 2 if half_h * 2 >= ih else min(max(self._cy, half_h), ih - half_h)

    def _zoom_at(self, new_zoom: float, ex: float, ey: float) -> None:
        """以画布坐标 (ex, ey) 为锚点缩放——光标下的那个源图点保持不动。"""
        if not self._src:
            return
        cw, ch = max(1, self.winfo_width()), max(1, self.winfo_height())
        old_scale = self._fit * self._zoom
        # 锚点对应的源图坐标
        sx = self._cx + (ex - cw / 2) / old_scale
        sy = self._cy + (ey - ch / 2) / old_scale

        self._zoom = new_zoom
        new_scale = self._fit * self._zoom
        self._cx = sx - (ex - cw / 2) / new_scale
        self._cy = sy - (ey - ch / 2) / new_scale
        self._clamp_center()
        self._render(final=True)

    # ------------------------------------------------------------ 渲染
    def _render(self, final: bool = True) -> None:
        cw = self.winfo_width()
        ch = self.winfo_height()
        if cw < 4 or ch < 4:
            return
        src = self._src
        if src is None:
            self.itemconfigure(self._img_id, image="")
            return

        scale = max(1e-6, self._fit * self._zoom)
        iw, ih = src.size

        # 视口在源图坐标系里的矩形
        view_w = min(iw, cw / scale)
        view_h = min(ih, ch / scale)
        left = min(max(self._cx - view_w / 2, 0.0), max(0.0, iw - view_w))
        top = min(max(self._cy - view_h / 2, 0.0), max(0.0, ih - view_h))

        x0, y0 = int(round(left)), int(round(top))
        x1, y1 = int(round(left + view_w)), int(round(top + view_h))
        x0 = max(0, min(x0, iw - 1))
        y0 = max(0, min(y0, ih - 1))
        x1 = max(x0 + 1, min(x1, iw))
        y1 = max(y0 + 1, min(y1, ih))

        out_w = max(1, int(round((x1 - x0) * scale)))
        out_h = max(1, int(round((y1 - y0) * scale)))

        if final:
            resample = PIXEL_RESAMPLE if scale > PIXEL_ZOOM_THRESHOLD else FINAL_RESAMPLE
        else:
            resample = FAST_RESAMPLE

        try:
            crop = src.crop((x0, y0, x1, y1))
            if crop.size != (out_w, out_h):
                crop = crop.resize((out_w, out_h), resample)
            self._photo = ImageTk.PhotoImage(crop)
            self.itemconfigure(self._img_id, image=self._photo)
            self.coords(self._img_id, (x0 - left) * scale, (y0 - top) * scale)
        except (tk.TclError, ValueError, MemoryError):
            return

        if self.on_view_change:
            self.on_view_change(scale, self._zoom)

    def _render_fast(self) -> None:
        """拖动/滚轮过程中限流渲染，别把主线程堵死。"""
        now = time.monotonic()
        if now - self._last_fast > 0.05:
            self._last_fast = now
            self._render(final=False)
        if self._settle_job:
            try:
                self.after_cancel(self._settle_job)
            except (tk.TclError, ValueError):
                pass
        self._settle_job = self.after(180, self._render_final)

    def _render_final(self) -> None:
        self._settle_job = None
        self._render(final=True)

    # ------------------------------------------------------------ 事件
    def _on_configure(self, _event: tk.Event) -> None:
        had = self._src is not None
        self._recompute_fit()
        if had:
            self._clamp_center()
        self._render(final=True)

    def _on_wheel(self, event: tk.Event) -> None:
        if not self._src:
            return
        steps = int(event.delta / 120) or (1 if event.delta > 0 else -1)
        factor = ZOOM_STEP ** steps
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            return
        self._zoom_at(new_zoom, event.x, event.y)

    def _on_press(self, event: tk.Event) -> None:
        if not self._src:
            return
        self._drag_from = (event.x, event.y, self._cx, self._cy)
        self.configure(cursor="fleur")

    def _on_motion(self, event: tk.Event) -> None:
        if not self._drag_from or not self._src:
            return
        x0, y0, cx0, cy0 = self._drag_from
        scale = max(1e-6, self._fit * self._zoom)
        self._cx = cx0 - (event.x - x0) / scale
        self._cy = cy0 - (event.y - y0) / scale
        self._clamp_center()
        self._render_fast()

    def _on_release(self, _event: tk.Event) -> None:
        self._drag_from = None

    def _on_double(self, _event: tk.Event) -> None:
        if not self._src:
            return
        if self._zoom > 1.05:
            self.fit()
        else:
            self.actual_size()
