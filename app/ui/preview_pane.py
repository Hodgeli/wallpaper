"""搜索页右侧的预览面板：缩略图先顶上 → 原图随后换上来，可缩放/平移/弹出大图。

从 `search_tab.py` 里拆出来的。拆的理由不是"行数不好看"，而是这块有**自己完整的
状态机**：

    选中 → 延迟 260ms（快速划过时不下载）→ 可中断下载 → 解码（含像素上限）
         → LRU 缓存 → 回填画布

以前这个状态机只能用 `feature_test` 间接测（要开窗口、要联网、还要等异步结果）。
现在它是独立的一类，而且 `decode_bytes_obj()` 和 `meta_text()` 都是纯函数，
`unit_test.py` 里能直接测。

**它不管下载**：`btn_apply` / `btn_download` 这两个按钮由这里创建（布局属于这一块），
但点下去调用的是构造时传进来的回调——真正下载要用到关键词、配置、下载记录，
那些都是搜索页的事。
"""
from __future__ import annotations

import io
import math
import tkinter as tk
import webbrowser
from collections import OrderedDict
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import Any

from PIL import Image

from ..images import DownloadAborted
from ..logsetup import get_logger
from . import theme
from .image_viewer import open_viewer
from .zoomcanvas import ZoomCanvas

log = get_logger("ui.preview")

# 原图预览的内存缓存条数。一张 4K 图解码后约 24MB，别留太多。
FULL_CACHE_SIZE = 3
# 超过这个体积就不自动拉原图做预览了，避免选一下就下 30MB
FULL_PREVIEW_MAX_BYTES = 25 * 1024 * 1024
# 原图预览解码后的像素上限。上面那个常量只挡**文件体积**，挡不住**解码后的像素体积**：
# 一张 8192x4606 的 PNG 可能只有几 MB，解码出来是 150MB，缓存 3 张就是 450MB。
# 16MP（RGB 约 48MB）够用——预览画布撑死一千多像素宽，再多也是浪费。
FULL_PREVIEW_MAX_PIXELS = 16_000_000
# 选中后等这么久再拉原图，快速划过缩略图时不会触发一堆下载
FULL_PREVIEW_DELAY_MS = 260
# 没在加载时的默认提示
QUALITY_HINT = "滚轮缩放 · 拖动平移"

CATEGORY_CN = {"general": "通用", "anime": "动漫", "people": "人物"}


def meta_text(item: dict[str, Any]) -> str:
    """面板中间那段信息文字。纯函数，方便单测。"""
    size = int(item.get("file_size") or 0)
    cat = CATEGORY_CN.get(str(item.get("category") or ""), item.get("category") or "—")
    colors = item.get("colors") or []
    # 压到三行，把竖向空间尽量让给预览画布
    lines = [
        f"分类：{cat}　纯净度：{item.get('purity') or '—'}　比例：{item.get('ratio') or '—'}",
        f"收藏：{item.get('favorites', 0)}　浏览：{item.get('views', 0)}　"
        f"体积：{size / 1024 / 1024:.2f} MB",
    ]
    if colors:
        lines.append("配色：" + "  ".join(str(c) for c in colors[:5]))
    return "\n".join(lines)


def decode_bytes_obj(data: bytes) -> tuple[Image.Image, bool]:
    """解码一张图，必要时按像素上限缩小。返回 (图, 源图是否超过上限)。纯函数。

    必须在线程里调用：`load()` 是纯 CPU 活。
    """
    img = Image.open(io.BytesIO(data))
    w, h = img.size
    over = w * h > FULL_PREVIEW_MAX_PIXELS
    if over:
        # JPEG 能按 1/2、1/4、1/8 降采样解码，先砍一刀，别把整张解出来再缩
        k = math.sqrt(w * h / FULL_PREVIEW_MAX_PIXELS)
        img.draft("RGB", (max(1, int(w / k)), max(1, int(h / k))))
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if img.width * img.height > FULL_PREVIEW_MAX_PIXELS:
        # 非 JPEG（PNG/WebP）没有 draft，解码峰值躲不掉；但至少别让它长期
        # 占着缓存——3 张 150MB 就是 450MB
        k = math.sqrt(img.width * img.height / FULL_PREVIEW_MAX_PIXELS)
        img = img.resize(
            (max(1, int(img.width / k)), max(1, int(img.height / k))),
            Image.LANCZOS)
    return img, over


class PreviewPane(ttk.Frame):
    """右侧预览面板。

    `on_apply` / `on_download` 是"下载并设为壁纸"/"仅下载"两个按钮的回调，
    由搜索页传进来（它才拿得到关键词和下载记录）。
    """

    def __init__(self, master: tk.Misc, app: Any, *,
                 on_apply: Callable[[], None],
                 on_download: Callable[[], None]) -> None:
        super().__init__(master, style="Panel.TFrame")
        self.app = app
        self.pal = app.pal
        self.ui = app.ui
        self._on_apply = on_apply
        self._on_download = on_download

        # 当前展示的是哪一张（弹出大图的副标题、"已下载"标签都靠它判断）
        self._iid: str = ""
        self._url = ""
        self._size_bytes = 0
        self._keyword = ""
        # 原图缓存：id -> PIL.Image，最多留 FULL_CACHE_SIZE 张
        self._full_cache: OrderedDict[str, Image.Image] = OrderedDict()
        self._full_job: str | None = None
        self._full_wanted: str | None = None

        # 宽度固定、不让子控件撑大它——这是搜索页里那一栏的既定尺寸
        self.configure(width=380)
        self.pack_propagate(False)
        self._build()
        self._wire_events()

    # ================================================================= 构建
    @property
    def downloader(self) -> Any:
        """下载器。**每次从 app 上取**，不在 `__init__` 里存一份。

        存一份的话，测试里 `use_fakes(app, downloader=...)` 换掉的只是
        `app.downloader`，这里手里那份还是真下载器——用例看着在跑，实际在连网。
        """
        return self.app.downloader

    def _build(self) -> None:
        head = ttk.Frame(self, style="Panel.TFrame")
        head.pack(side="top", fill="x", padx=12, pady=(10, 6))
        ttk.Label(head, text="预览", style="Panel.TLabel",
                  font=theme.font(11, True)).pack(side="left")
        self.btn_popout = ttk.Button(head, text="弹出大图", width=10,
                                     command=self._open_viewer, state="disabled")
        self.btn_popout.pack(side="right")

        # 工具条（放在底部块里，跟按钮一起从下往上排）
        btns = ttk.Frame(self, style="Panel.TFrame")
        btns.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        self.btn_apply = ttk.Button(btns, text="下载并设为壁纸", style="Accent.TButton",
                                    command=self._on_apply, state="disabled")
        self.btn_apply.pack(fill="x")
        self.btn_download = ttk.Button(btns, text="仅下载到保存目录",
                                       command=self._on_download, state="disabled")
        self.btn_download.pack(fill="x", pady=(6, 0))

        self.lbl_selected = ttk.Label(self, text="", style="PanelMuted.TLabel",
                                      font=theme.font(8), wraplength=380,
                                      justify="left")
        self.lbl_selected.pack(side="bottom", anchor="w", padx=12, pady=(0, 6))

        self.lbl_link = tk.Label(self, text="", bg=self.pal["panel"],
                                 fg=self.pal["accent"], cursor="hand2",
                                 font=theme.font(9, True))
        self.lbl_link.pack(side="bottom", anchor="w", padx=12)
        self.lbl_link.bind("<Button-1>", self._open_page)

        self.lbl_meta = ttk.Label(self, text="", style="PanelMuted.TLabel",
                                  justify="left", wraplength=380,
                                  font=theme.font(9))
        self.lbl_meta.pack(side="bottom", anchor="w", padx=12, pady=(6, 8))

        zoom_bar = ttk.Frame(self, style="Panel.TFrame")
        zoom_bar.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        ttk.Button(zoom_bar, text="适应", width=5,
                   command=lambda: self.canvas.fit()).pack(side="left")
        ttk.Button(zoom_bar, text="1:1", width=5,
                   command=lambda: self.canvas.actual_size()).pack(side="left", padx=4)
        ttk.Button(zoom_bar, text="－", width=3,
                   command=lambda: self.canvas.zoom_by(1 / 1.25)).pack(side="left")
        ttk.Button(zoom_bar, text="＋", width=3,
                   command=lambda: self.canvas.zoom_by(1.25)).pack(side="left", padx=(4, 0))
        self.lbl_zoom = ttk.Label(zoom_bar, text="", style="PanelMuted.TLabel",
                                  font=theme.font(8))
        self.lbl_zoom.pack(side="left", padx=8)
        self.lbl_quality = ttk.Label(zoom_bar, text=QUALITY_HINT,
                                     style="PanelMuted.TLabel", font=theme.font(8))
        self.lbl_quality.pack(side="right")

        self.lbl_title = ttk.Label(self, text="未选择图片", style="Panel.TLabel",
                                   font=theme.font(10, True), wraplength=380,
                                   justify="left")
        self.lbl_title.pack(side="top", anchor="w", padx=12, pady=(0, 4))

        # 画布最后 pack 且 expand，吃掉中间剩下的全部空间
        self.canvas = ZoomCanvas(self, bg=self.pal["thumb_bg"],
                                 on_view_change=self._on_zoom_change)
        self.canvas.pack(side="top", fill="both", expand=True, padx=12, pady=(0, 8))

    def _wire_events(self) -> None:
        """自己注册自己用的事件。

        以前这几个处理器是在 SearchTab._wire_events 里注册的，拆开之后
        "谁用谁注册"——顺手也就避免了"投了事件没人接"那种静默失败。
        """
        self.ui.on("preview_thumb", self._on_thumb_loaded)
        self.ui.on("preview_thumb_err", self._on_thumb_err)
        self.ui.on("preview_full", self._on_full_loaded)
        self.ui.on("preview_full_err", self._on_full_err)

    # ================================================================= 对外
    def set_keyword(self, keyword: str) -> None:
        """当前搜索用的关键词，只用于弹出大图时的副标题。"""
        self._keyword = keyword or ""

    def show_item(self, item: dict[str, Any], downloaded_name: str = "") -> None:
        """展示一张新选中的图：先填文字，再分别去要缩略图和原图。"""
        iid = str(item.get("id") or "")
        self._iid = iid
        self._url = str(item.get("url") or "")
        self._size_bytes = int(item.get("file_size") or 0)

        self.lbl_title.configure(text=str(item.get("resolution") or "未知分辨率"))
        self.lbl_meta.configure(text=meta_text(item))
        self.lbl_link.configure(text=self._url)
        self.lbl_selected.configure(
            text=f"已下载：{downloaded_name}" if downloaded_name else "")
        state = "normal" if iid else "disabled"
        for btn in (self.btn_apply, self.btn_download, self.btn_popout):
            btn.configure(state=state)

        # 先用 thumbs.large 立刻出图，再在后台把原图换上来
        thumb_url = ((item.get("thumbs") or {}).get("large")
                     or (item.get("thumbs") or {}).get("small") or "")
        if thumb_url and iid:
            self.ui.run_bg(self._thumb_job(iid, thumb_url),
                           on_ok="preview_thumb", on_error="preview_thumb_err")
        self._schedule_full(item)

    def mark_downloaded(self, iid: str, filename: str) -> None:
        """下载完成后更新"已下载："标签。只对当前正在展示的那张生效。"""
        if not self._iid or str(iid) != self._iid:
            return
        self.lbl_selected.configure(text=f"已下载：{filename}")

    def has_full_image(self, iid: str) -> bool:
        """原图解码好了没。给"等原图就绪再截图/再断言"这类用途。"""
        return str(iid) in self._full_cache

    def reset(self) -> None:
        """新一次搜索时清空，免得还挂着上一次选中的图。"""
        self._iid = ""
        self._url = ""
        self._size_bytes = 0
        self._full_wanted = None
        self.cancel_pending_timers()
        self._full_cache.clear()
        self.canvas.clear()
        self.lbl_title.configure(text="未选择图片")
        self.lbl_meta.configure(text="")
        self.lbl_link.configure(text="")
        self.lbl_selected.configure(text="")
        self.lbl_zoom.configure(text="")
        self.set_quality(QUALITY_HINT)
        for btn in (self.btn_apply, self.btn_download, self.btn_popout):
            btn.configure(state="disabled")

    def cancel_pending_timers(self) -> None:
        """取消还没到点的那次原图加载。

        关窗时 `App._on_close()` 也会调它——不取消的话窗口 `destroy()` 之后
        定时器照样触发，Tcl 会打 `invalid command name "..."`。
        """
        if not self._full_job:
            return
        try:
            self.after_cancel(self._full_job)
        except (tk.TclError, ValueError):
            pass
        self._full_job = None

    # ================================================================= 原图
    def _schedule_full(self, item: dict[str, Any]) -> None:
        """选中后延迟一小会儿再拉原图，快速划过缩略图时不会触发一堆下载。"""
        iid = str(item.get("id") or "")
        self._full_wanted = iid
        self.cancel_pending_timers()
        if not iid:
            return

        cached = self._full_cache.get(iid)
        if cached is not None:
            self._full_cache.move_to_end(iid)
            self.canvas.set_image(cached, reset_view=True)
            self.set_quality(f"原图 {cached.width}×{cached.height}")
            return

        self._full_job = self.after(FULL_PREVIEW_DELAY_MS,
                                    lambda: self._fetch_full(item))

    def _fetch_full(self, item: dict[str, Any]) -> None:
        self._full_job = None
        iid = str(item.get("id") or "")
        url = str(item.get("path") or "")
        if not iid or not url:
            return
        size = int(item.get("file_size") or 0)
        if size > FULL_PREVIEW_MAX_BYTES:
            self.set_quality(f"原图 {size / 1024 / 1024:.0f} MB，过大未加载")
            return
        self.set_quality("正在加载原图…")

        def _still_wanted() -> bool:
            """用户还停在这张图上吗？给下载器当中断开关用。

            工作线程里读主线程写的属性是安全的（有 GIL）。读到过期的值也不要紧——
            "过期"就意味着用户已经切走了，正是要放弃的情况。
            """
            return self._full_wanted != iid

        def _job() -> tuple[str, Image.Image, bool]:
            data = self.downloader.get_bytes(url, timeout=(8, 60),
                                             should_abort=_still_wanted)
            return (iid, *decode_bytes_obj(data))

        self.ui.run_bg(_job, on_ok="preview_full", on_error="preview_full_err")

    def _thumb_job(self, iid: str, url: str) -> Callable[[], tuple[str, Image.Image]]:
        """返回一个给工作线程用的闭包。

        **`iid` 必须在提交时绑好**，不能在闭包里读 `self._iid`：任务在池子里排队时
        用户可能已经点了另一张，那时读到的就是新 id、配的却是旧 url 的图，
        结果是"新的图位显示旧的图"。原来这行读的是 `self._selected`，同一个毛病。
        """
        def _job() -> tuple[str, Image.Image]:
            image, _clamped = decode_bytes_obj(self.downloader.get_bytes(url))
            return iid, image
        return _job

    def _on_thumb_loaded(self, payload: tuple[str, Image.Image]) -> None:
        """低清缩略图先顶上——原图没来之前至少能看个大概。"""
        iid, image = payload
        if not self._iid or iid != self._iid:
            return                      # 用户已经切走了，这张是过期的
        if self.canvas.has_image() and iid in self._full_cache:
            return                      # 原图已经到了，别用低清覆盖
        self.canvas.set_image(image, reset_view=True)
        if iid not in self._full_cache:
            self.set_quality("低清预览（原图加载中…）")

    def _on_thumb_err(self, exc: Exception) -> None:
        # 缩略图挂了不影响什么：原图那条路还在跑，界面上也不会留空
        log.debug("预览缩略图加载失败：%s", exc)

    def _on_full_loaded(self, payload: tuple[str, Image.Image, bool]) -> None:
        iid, image, clamped = payload
        self._full_cache[iid] = image
        self._full_cache.move_to_end(iid)
        while len(self._full_cache) > FULL_CACHE_SIZE:
            self._full_cache.popitem(last=False)
        if self._full_wanted != iid or not self._iid or self._iid != iid:
            return
        self.canvas.set_image(image, reset_view=True)
        text = f"原图 {image.width}×{image.height}"
        if clamped:
            text += "（源图过大，已按内存上限缩小）"
        self.set_quality(text)

    def _on_full_err(self, exc: Exception) -> None:
        if isinstance(exc, DownloadAborted):
            # 用户切走了，我们自己放弃的，不是故障
            log.debug("原图预览已放弃：用户切到了别的图")
            return
        log.info("原图预览加载失败：%s", exc)
        self.set_quality("原图加载失败，显示的是低清缩略图")

    # ================================================================= 交互
    def set_quality(self, text: str) -> None:
        """右下角那行小字（"正在加载原图…" / "原图 3840×2160" 之类）。"""
        try:
            self.lbl_quality.configure(text=text)
        except tk.TclError:
            pass

    def _on_zoom_change(self, scale: float, zoom: float) -> None:
        try:
            self.lbl_zoom.configure(text=f"{scale * 100:.0f}%")
        except tk.TclError:
            pass

    def _open_page(self, _event: tk.Event) -> None:
        if self._url:
            webbrowser.open(self._url)

    def _open_viewer(self) -> None:
        if not self._iid:
            return
        image = self._full_cache.get(self._iid)
        if image is None:
            messagebox.showinfo(
                "原图还没准备好",
                "正在下载原图，稍等一两秒再点「弹出大图」。\n"
                "（如果一直没出来，可能是原图太大或网络不通）",
                parent=self,
            )
            return
        open_viewer(
            self.app, self.pal, image,
            title=f"大图预览 — {self.lbl_title.cget('text')}",
            subtitle=f"{self._keyword or '—'}　·　ID {self._iid}　·　"
                     f"{self._size_bytes / 1024 / 1024:.2f} MB",
            on_apply=self._on_apply,
        )
