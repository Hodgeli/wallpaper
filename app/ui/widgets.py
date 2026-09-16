"""可复用控件：自适应列数 + 无限滚动的缩略图网格。"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from PIL import Image, ImageTk

from . import theme


def open_scaled(path, width: int, height: int) -> Image.Image:
    """打开图片并等比缩放到给定框内。

    线程安全，但**必须在工作线程里调用**：解码 + LANCZOS 重采样是纯 CPU 活，
    放在主线程（UI 事件回调里）会让界面明显卡住。

    先 `draft()` 再 `load()`：JPEG 支持按 1/2、1/4、1/8 降采样解码，
    对 3840x2160 这种大图做 240x135 缩略图，解码量能降一个数量级。
    源图本来就接近目标尺寸时 draft 不起作用（wallhaven 的缩略图约 640x400，
    这里就是空操作），多这一步没有代价。其它格式 draft 也是空操作。
    请求 2 倍目标尺寸是为了给后面的 LANCZOS 留够重采样余量——宁可多解码一点，
    也不要把图缩得刚好等于目标尺寸、让重采样失去余量。
    """
    img = Image.open(path)
    img.draft("RGB", (width * 2, height * 2))
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    img.thumbnail((width, height), Image.LANCZOS)
    return img


def placeholder_image(width: int, height: int, color: str) -> Image.Image:
    return Image.new("RGB", (width, height), color)


class ThumbGrid(ttk.Frame):
    """缩略图网格：随宽度自适应列数，滚到底自动请求下一页。"""

    THUMB_W = 240
    THUMB_H = 135
    GAP = 8

    def __init__(self, master: tk.Misc, pal: dict[str, str], *,
                 on_select: Callable[[dict], None],
                 on_open: Callable[[dict], None],
                 on_need_more: Callable[[], None] | None = None,
                 on_hover: Callable[[dict | None], None] | None = None) -> None:
        super().__init__(master, style="TFrame")
        self.pal = pal
        self.on_select = on_select
        self.on_open = on_open
        self.on_need_more = on_need_more
        self.on_hover = on_hover

        self._cells: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._photos: dict[str, ImageTk.PhotoImage] = {}
        self._items: dict[str, dict] = {}
        self._cols = 0
        self._selected: str | None = None
        self._loading_more = False

        self._ph_img = placeholder_image(self.THUMB_W, self.THUMB_H, pal["thumb_bg"])
        self._ph_photo = ImageTk.PhotoImage(self._ph_img)

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=pal["bg"])
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll_set)
        self.vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=pal["bg"])
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

        self.empty_label = tk.Label(
            self.canvas, text="", bg=pal["bg"], fg=pal["muted"],
            font=theme.font(10), justify="center", wraplength=520,
        )

    # ------------------------------------------------------------ 数据
    def clear(self) -> None:
        for cell in self._cells.values():
            cell["frame"].destroy()
        self._cells.clear()
        self._order.clear()
        self._photos.clear()
        self._items.clear()
        self._selected = None
        self._loading_more = False
        self.canvas.yview_moveto(0)

    def add_items(self, items: list[dict]) -> None:
        """追加一批结果，返回新加入的 id 列表。"""
        new_ids: list[str] = []
        for it in items:
            iid = str(it.get("id") or "")
            if not iid or iid in self._items:
                continue
            self._items[iid] = it
            self._order.append(iid)
            self._make_cell(it)
            new_ids.append(iid)
        if self._cols:
            self._relayout()
        return new_ids

    def show_message(self, text: str, color_key: str = "muted") -> None:
        """在网格中央显示一段提示（无结果 / 错误 / 引导）。"""
        self.empty_label.configure(text=text, fg=self.pal[color_key])
        self.empty_label.place(relx=0.5, rely=0.42, anchor="center")

    def hide_message(self) -> None:
        self.empty_label.place_forget()

    def set_photo(self, image_id: str, image: Image.Image) -> None:
        cell = self._cells.get(str(image_id))
        if not cell:
            return
        photo = ImageTk.PhotoImage(image)
        self._photos[str(image_id)] = photo
        cell["img_label"].configure(image=photo)

    def mark_downloaded(self, image_id: str, downloaded: bool = True) -> None:
        cell = self._cells.get(str(image_id))
        if not cell:
            return
        if downloaded:
            cell["badge"].place(relx=1.0, rely=0.0, anchor="ne", x=-4, y=4)
        else:
            cell["badge"].place_forget()

    def set_loading_more(self, loading: bool) -> None:
        self._loading_more = loading

    def get_selected(self) -> dict | None:
        if self._selected is None:
            return None
        return self._items.get(self._selected)

    def count(self) -> int:
        return len(self._order)

    # ------------------------------------------------------------ 内部
    def _make_cell(self, item: dict) -> None:
        iid = str(item["id"])
        frame = tk.Frame(self.inner, bg=self.pal["panel"],
                         highlightthickness=2, highlightbackground=self.pal["border"],
                         highlightcolor=self.pal["border"], bd=0, cursor="hand2")
        img_label = tk.Label(frame, image=self._ph_photo, bg=self.pal["thumb_bg"],
                             bd=0, cursor="hand2")
        img_label.pack(padx=2, pady=(2, 0))

        caption = tk.Label(
            frame, text=str(item.get("resolution") or ""),
            bg=self.pal["panel"], fg=self.pal["muted"],
            font=theme.font(8), cursor="hand2",
        )
        caption.pack(fill="x", pady=(2, 2))

        badge = tk.Label(frame, text="已下载", bg=self.pal["accent_soft"],
                         fg=self.pal["accent"], font=theme.font(7), padx=4)

        widgets = [frame, img_label, caption, badge]
        for w in widgets:
            w.bind("<Button-1>", lambda _e, i=iid: self._click(i))
            w.bind("<Double-Button-1>", lambda _e, i=iid: self._double(i))
            w.bind("<Enter>", lambda _e, i=iid: self._hover(i))
            w.bind("<Leave>", lambda _e: self._hover(None))

        self._cells[iid] = {
            "frame": frame, "img_label": img_label,
            "caption": caption, "badge": badge,
        }

    def _click(self, image_id: str) -> None:
        self.select(image_id)
        item = self._items.get(image_id)
        if item:
            self.on_select(item)

    def _double(self, image_id: str) -> None:
        item = self._items.get(image_id)
        if item:
            self.select(image_id)
            self.on_open(item)

    def _hover(self, image_id: str | None) -> None:
        if self.on_hover:
            self.on_hover(self._items.get(image_id) if image_id else None)

    def select(self, image_id: str) -> None:
        if self._selected and self._selected in self._cells:
            self._cells[self._selected]["frame"].configure(
                highlightbackground=self.pal["border"])
        self._selected = str(image_id)
        cell = self._cells.get(self._selected)
        if cell:
            cell["frame"].configure(highlightbackground=self.pal["accent"])

    def _on_scroll_set(self, first: str, last: str) -> None:
        self.vbar.set(first, last)
        # 拖滚动条、滚轮、程序滚动都会走到这里，统一在这里判断要不要加载下一页
        self._maybe_need_more()

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._win, width=event.width)
        cols = self._compute_cols(event.width)
        if cols != self._cols:
            self._cols = cols
            self._relayout()
        self.empty_label.configure(wraplength=max(240, event.width - 80))

    def _compute_cols(self, width: int) -> int:
        usable = max(1, width - 4)
        return max(1, usable // (self.THUMB_W + self.GAP))

    def _relayout(self) -> None:
        if not self._cols:
            return
        for idx, iid in enumerate(self._order):
            cell = self._cells.get(iid)
            if not cell:
                continue
            r, c = divmod(idx, self._cols)
            cell["frame"].grid(row=r, column=c, padx=self.GAP // 2,
                               pady=self.GAP // 2, sticky="n")

    def _on_inner_configure(self, _event: tk.Event) -> None:
        # 只按内层 frame 算滚动区域。用 bbox("all") 会把浮在上面的
        # 欢迎页快捷标签也算进去，导致空网格也冒出滚动条。
        try:
            bbox = self.canvas.bbox(self._win)
            self.canvas.configure(scrollregion=bbox or (0, 0, 0, 0))
        except tk.TclError:
            pass

    def _bind_wheel(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
        self._maybe_need_more()

    def _maybe_need_more(self) -> None:
        if not self.on_need_more or self._loading_more:
            return
        if not self._is_scrollable():
            return
        try:
            _top, bottom = self.canvas.yview()
        except tk.TclError:
            return
        # 注意不能写成 `bottom < 1.0`：真滚到底时 bottom 恰好是 1.0，
        # 那个条件会把唯一需要加载的情况排除掉。
        if bottom >= 0.92:
            self.on_need_more()

    def _is_scrollable(self) -> bool:
        """内容比视口高才算"能滚"，否则到底了也不该继续加载。"""
        try:
            bbox = self.canvas.bbox(self._win)
            if not bbox:
                return False
            return (bbox[3] - bbox[1]) > self.canvas.winfo_height() + 8
        except tk.TclError:
            return False
