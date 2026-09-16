"""搜索页：筛选栏 + 缩略图网格 + 右侧大图预览。

预览面板的实现搬到了 `preview_pane.py`（它有自己的状态机：延迟触发 → 可中断下载
→ 解码 → LRU 缓存）。这里只负责"选中了哪一张"和"下载要用的关键词/配置"。
"""
from __future__ import annotations

import random
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from ..api import WallpaperError
from ..config import (
    CATEGORY_CHOICES,
    QUICK_TAGS,
    RATIO_CHOICES,
    RESOLUTION_CHOICES,
    SORTING_CHOICES,
)
from ..images import evict_cache, fetch_thumbs
from ..logsetup import get_logger
from ..naming import primary_keyword
from ..service import (
    download_wallpaper,
    random_wallpaper,
    split_keywords,
)
from ..winwall import auto_resolution_text, describe_displays
from .preview_pane import PreviewPane
from .widgets import ThumbGrid, open_scaled

log = get_logger("ui.search")

# 缩略图缓存清理的最小间隔（秒）。每翻一页都扫一遍缓存目录纯属浪费，
# 而且这个清理丢在工作线程里做，跟界面无关，一分钟一次足够了。
EVICT_MIN_INTERVAL = 60.0

AUTO_LABEL = "自适应"
CUSTOM_LABEL = "自定义"


def auto_label() -> str:
    """自适应的显示文本。

    必须在运行时算：DPI 感知是在 WallpaperPickerApp 构造时才开启的，
    模块导入阶段算出来的分辨率是错的（会拿到被系统缩放过的值）。
    """
    return f"{AUTO_LABEL}（{auto_resolution_text()}）"


class SearchTab(ttk.Frame):
    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.pal = app.pal
        self.cfg = app.cfg
        self.store = app.store
        self.ui = app.ui

        self._page = 0
        self._last_page = 1
        self._keyword = ""
        self._seed: str | None = None
        self._inflight_pages = 0
        self._token = 0
        self._selected: dict | None = None
        self._sash_set = False
        self._busy = False
        # 设置页里的「默认关键词」原串（可能是逗号分隔的多个），用于同步判断
        self._default_keyword = (self.cfg.get("default_keyword") or "").strip()
        # 由程序自己写进关键词框的值。用户手敲的内容不在这个集合里，
        # 同步默认关键词时只覆盖「空」或「程序填的」，不冲掉用户输入。
        self._program_keywords: set[str] = set()
        # 上次清理缩略图缓存的时间戳（time.monotonic），用来做节流
        self._last_evict = 0.0

        self._build()
        self._wire_events()
        # 关键词框默认留空，统一由这里按「默认关键词」填一个可直接搜索的单值
        self.set_default_keyword(self._default_keyword)

    # ------------------------------------------------------------- 两个外部依赖
    @property
    def client(self) -> Any:
        """搜索用的客户端。**每次从 app 上取**，别在 `__init__` 里存一份。

        存一份的话，测试里 `use_fakes(app, client=...)` 换掉的只是 `app.client`，
        页面手里那份还是真客户端——测试就会真去连网，"离线可跑"就成了假的。
        """
        return self.app.client

    @property
    def downloader(self) -> Any:
        """下载器。同上，必须每次从 `app` 上取。"""
        return self.app.downloader

    # ================================================================= 构建
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_filter_bar()
        body = ttk.PanedWindow(self, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 6))
        self.body = body
        body.bind("<Configure>", self._on_body_configure)

        grid_wrap = ttk.Frame(body, style="TFrame")
        self.grid_view = ThumbGrid(
            grid_wrap, self.pal,
            on_select=self.select_item,
            on_open=self.set_as_wallpaper,
            on_need_more=self._maybe_load_next_page,
        )
        self.grid_view.pack(fill="both", expand=True)
        body.add(grid_wrap, weight=4)

        self.preview = PreviewPane(
            body, self.app,
            on_apply=lambda: self.set_as_wallpaper(self._selected),
            on_download=lambda: self.download_only(self._selected),
        )
        body.add(self.preview, weight=1)

        self.show_welcome()

    def _build_filter_bar(self) -> None:
        box = ttk.Labelframe(self, text=" 搜索条件 ")
        box.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 6))
        box.columnconfigure(0, weight=1)

        row = ttk.Frame(box, style="TFrame")
        row.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 2))

        pad = {"padx": (0, 6)}

        ttk.Label(row, text="关键词").grid(row=0, column=0, **pad)
        self.var_keyword = tk.StringVar()
        self.ent_keyword = ttk.Entry(row, textvariable=self.var_keyword, width=22)
        self.ent_keyword.grid(row=0, column=1, padx=(0, 14))

        ttk.Label(row, text="分类").grid(row=0, column=2, **pad)
        self.var_category = tk.StringVar(value="全部")
        ttk.Combobox(row, textvariable=self.var_category, state="readonly", width=8,
                     values=list(CATEGORY_CHOICES)).grid(row=0, column=3, padx=(0, 14))

        ttk.Label(row, text="分辨率").grid(row=0, column=4, **pad)
        self.var_resolution = tk.StringVar(value=auto_label())
        self.cmb_resolution = ttk.Combobox(
            row, textvariable=self.var_resolution, state="readonly", width=14,
            values=[auto_label()] + RESOLUTION_CHOICES[1:],
        )
        self.cmb_resolution.grid(row=0, column=5, padx=(0, 14))

        ttk.Label(row, text="比例").grid(row=0, column=6, **pad)
        self.var_ratio = tk.StringVar(value="16:9 + 16:10（默认）")
        ttk.Combobox(row, textvariable=self.var_ratio, state="readonly", width=17,
                     values=list(RATIO_CHOICES)).grid(row=0, column=7, padx=(0, 14))

        ttk.Label(row, text="排序").grid(row=0, column=8, **pad)
        self.var_sorting = tk.StringVar(value="收藏数")
        ttk.Combobox(row, textvariable=self.var_sorting, state="readonly", width=10,
                     values=list(SORTING_CHOICES)).grid(row=0, column=9, padx=(0, 14))

        self.btn_search = ttk.Button(row, text="搜索", style="Accent.TButton",
                                     command=lambda: self.do_search(reset=True))
        self.btn_search.grid(row=0, column=10, padx=(0, 8))

        self.btn_random = ttk.Button(row, text="随机换一张", command=self.do_random)
        self.btn_random.grid(row=0, column=11)

        self.lbl_hint = ttk.Label(
            box,
            text=("提示：只认英文关键词（如 nature / cyberpunk），也支持 #标签、@作者 语法。"
                  "关键词框留空时会从设置页的「默认关键词」里随机挑一个，清空再搜可换一个。\n"
                  "当前显示器：" + describe_displays()),
            style="Hint.TLabel",
        )
        self.lbl_hint.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))

    def set_default_keyword(self, raw: str) -> None:
        """把「默认关键词」同步到搜索框。

        配置里可以填逗号分隔的多个关键词，但框里只放一个能直接搜的单值：
        多个就随机挑一个，一个就用它，一个都没有就留空。
        只在框里是空的、或还停在上一次由程序填进去的值时才覆盖，
        用户自己敲的内容不会被冲掉。
        """
        self._default_keyword = (raw or "").strip()
        current = self.var_keyword.get().strip()
        if current and current not in self._program_keywords:
            return
        items = split_keywords(self._default_keyword)
        picked = random.choice(items) if items else ""
        self.var_keyword.set(picked)
        self._program_keywords = {picked} if picked else set()

    def _resolve_keyword(self) -> str:
        """搜索前定关键词：框里填了就用它，空着就从「默认关键词」里随机挑一个。

        挑中的会写回框里，让你看得见这次搜的是什么（想换一个就清空再搜）。
        配置也是空的时候就保持空着，交给 wallhaven 返回「不限关键词」的结果。
        """
        typed = self.var_keyword.get().strip()
        if typed:
            return typed
        items = split_keywords(self._default_keyword)
        if not items:
            return ""
        picked = random.choice(items)
        self.var_keyword.set(picked)
        self._program_keywords.add(picked)
        if len(items) > 1:
            self.app.set_status(f"从 {len(items)} 个关键词中随机选了「{picked}」")
        return picked

    # ================================================================= 事件
    def _wire_events(self) -> None:
        # preview_* 那几个事件由 PreviewPane 自己注册（谁用谁注册），这里不再管
        self.ui.on("search_ok", self._on_search_ok)
        self.ui.on("search_err", self._on_search_err)
        self.ui.on("thumb", self._on_thumb)
        self.ui.on("stage", self._on_stage)
        self.ui.on("bytes", self._on_bytes)
        self.ui.on("apply_ok", self._on_apply_ok)
        self.ui.on("apply_err", self._on_apply_err)
        self.ent_keyword.bind("<Return>", lambda _e: self.do_search(reset=True))
        self.cmb_resolution.bind("<<ComboboxSelected>>", self._on_resolution_change)

    def _on_resolution_change(self, _event: tk.Event) -> None:
        if self.var_resolution.get() == CUSTOM_LABEL:
            value = self.app.ask_custom_resolution()
            if value:
                self._custom_resolution = value
                self.var_resolution.set(f"{CUSTOM_LABEL}：{value}")
            else:
                self.var_resolution.set(auto_label())

    # ================================================================= 欢迎页
    def show_welcome(self) -> None:
        self.grid_view.clear()
        self._reset_preview()
        self.grid_view.show_message(
            "输入关键词后点「搜索」，或直接点下面的标签快速开始\n"
            "（双击任意缩略图 = 下载并设为桌面壁纸）"
        )
        tags = ttk.Frame(self.grid_view.canvas, style="TFrame")
        for i, tag in enumerate(QUICK_TAGS):
            btn = ttk.Button(tags, text=tag, width=13,
                             command=lambda t=tag: self._quick_search(t))
            btn.grid(row=i // 4, column=i % 4, padx=4, pady=4)
        self.grid_view.canvas.create_window(
            self.grid_view.canvas.winfo_reqwidth() // 2, 0, window=tags,
            anchor="n", tags="quicktags",
        )
        self._quicktags = tags
        self.grid_view.canvas.bind("<Configure>", self._place_quicktags, add="+")
        self.after(80, self._place_quicktags)

    def _place_quicktags(self, _event: tk.Event | None = None) -> None:
        if not getattr(self, "_quicktags", None) or not self._quicktags.winfo_exists():
            return
        w = self.grid_view.canvas.winfo_width()
        h = self.grid_view.canvas.winfo_height()
        self.grid_view.canvas.coords("quicktags", w // 2, int(h * 0.55))
        self.grid_view.canvas.itemconfigure("quicktags", state="normal")

    def _clear_welcome(self) -> None:
        if getattr(self, "_quicktags", None) is not None:
            try:
                self._quicktags.destroy()
                self.grid_view.canvas.delete("quicktags")
            except tk.TclError:
                pass
            self._quicktags = None
        self.grid_view.hide_message()

    def _quick_search(self, tag: str) -> None:
        self.var_keyword.set(tag)
        self.do_search(reset=True)

    # ================================================================= 搜索
    def _current_filters(self) -> dict[str, Any]:
        res_raw = self.var_resolution.get()
        if res_raw.startswith(AUTO_LABEL):
            atleast = auto_resolution_text()
        elif res_raw.startswith(CUSTOM_LABEL):
            atleast = getattr(self, "_custom_resolution", None) or auto_resolution_text()
        else:
            atleast = res_raw

        sorting, order = SORTING_CHOICES[self.var_sorting.get()]
        return {
            "keyword": self.var_keyword.get().strip(),
            "categories": CATEGORY_CHOICES[self.var_category.get()],
            "atleast": atleast,
            "ratios": RATIO_CHOICES[self.var_ratio.get()],
            "sorting": sorting,
            "order": order,
        }

    def do_search(self, reset: bool = False) -> None:
        if self._busy:
            return
        self._resolve_keyword()
        filters = self._current_filters()
        if reset:
            self._clear_welcome()
            self.grid_view.clear()
            self._page = 0
            self._last_page = 1
            self._seed = None
            self._inflight_pages = 0
            self._token += 1
            self._keyword = filters["keyword"]
            self.preview.set_keyword(self._keyword)   # 弹出大图的副标题要用
            self.grid_view.show_message("正在搜索…")
            self._reset_preview()
        self._request_page(self._page + 1, filters)

    def cancel_pending_timers(self) -> None:
        """关窗时把还挂着的 `after` 定时器取消掉（见 PreviewPane 里的说明）。"""
        self.preview.cancel_pending_timers()

    def _reset_preview(self) -> None:
        """新一次搜索时把预览面板清空，免得还挂着上一次选中的图。"""
        self._selected = None
        self.preview.reset()

    def _request_page(self, page: int, filters: dict[str, Any]) -> None:
        token = self._token
        self._inflight_pages += 1
        self.grid_view.set_loading_more(True)
        self.app.set_status(f"正在请求第 {page} 页…")
        self.app.set_progress(0.35)

        def _job() -> dict:
            result = self.client.search(
                keyword=filters["keyword"],
                page=page,
                categories=filters["categories"],
                atleast=filters["atleast"],
                ratios=filters["ratios"],
                sorting=filters["sorting"],
                order=filters["order"],
                seed=self._seed,
                on_wait=lambda secs, why: self.ui.post("stage", f"{why}（{secs}s 后重试）"),
            )
            return {"page": page, "result": result, "token": token}

        self.ui.run_bg(_job, on_ok="search_ok", on_error="search_err")

    def _maybe_load_next_page(self) -> None:
        if self._busy or self._page >= self._last_page:
            return
        if self._inflight_pages >= 2:      # 最多预取 2 页，防止撞限流
            return
        self._request_page(self._page + 1, self._current_filters())

    def _on_search_ok(self, payload: dict) -> None:
        self._inflight_pages = max(0, self._inflight_pages - 1)
        self.grid_view.set_loading_more(False)
        if payload.get("token") != self._token:
            return

        result = payload["result"]
        page = int(payload["page"])
        items = result.get("items") or []
        meta = result.get("meta") or {}

        self._page = page
        self._last_page = int(meta.get("last_page") or 1)
        if not self._seed and meta.get("seed"):
            self._seed = meta["seed"]

        if page == 1 and not items:
            self.grid_view.show_message(
                "没有搜到结果。换个关键词试试，或把「分辨率 / 比例」放宽一些。")
            self.app.set_status("没有搜到结果", "warn")
            self.app.set_progress(None)
            return

        new_ids = self.grid_view.add_items(items)
        self.grid_view.hide_message()
        self.app.set_status(
            f"第 {self._page}/{self._last_page} 页，已加载 {self.grid_view.count()} 张"
            f"（本页 {len(new_ids)} 张）"
        )
        self.app.set_progress(None)
        self._load_photos([it for it in items if str(it.get("id")) in set(new_ids)])
        self._maybe_evict()

    def _on_search_err(self, exc: Exception) -> None:
        self._inflight_pages = max(0, self._inflight_pages - 1)
        self.grid_view.set_loading_more(False)
        self.app.set_progress(None)
        if isinstance(exc, WallpaperError):
            self.app.report_error(exc)
        else:
            self.app.report_error(WallpaperError(f"搜索失败：{exc}"))
        if self.grid_view.count() == 0:
            self.grid_view.show_message("搜索失败，详情见状态栏与日志。", "error")

    def _maybe_evict(self) -> None:
        """按需清理缩略图缓存，节流 + 后台执行。

        以前每加载一页都在主线程全量扫一遍缓存目录（glob 加逐文件 stat），
        翻页多了就是实打实的卡顿，而且绝大多数时候根本不需要清。现在 60 秒内
        最多跑一次，并且丢给工作线程，主线程不再为它停一下。
        """
        now = time.monotonic()
        if now - self._last_evict < EVICT_MIN_INTERVAL:
            return
        self._last_evict = now
        max_mb = self.cfg.get("thumb_cache_mb")
        self.ui.run_bg(lambda: evict_cache(max_mb), on_error="evict_err")

    # ================================================================= 缩略图
    def _load_photos(self, items: list[dict]) -> None:
        if not items:
            return

        def _job() -> None:
            fetch_thumbs(
                items, self.downloader,
                on_done=lambda iid, img, err: self.ui.post("thumb", iid, img, err),
                # 解码交给工作线程：一页 24 张缩略图几乎同时到达，主线程逐张做
                # LANCZOS 缩放会连着卡好几百毫秒，滚动时手感很差
                decode=lambda p: open_scaled(p, ThumbGrid.THUMB_W, ThumbGrid.THUMB_H),
            )

        self.ui.run_bg(_job, on_error="thumb_batch_err")

    def _on_thumb(self, image_id: str, img: Any, err: Exception | None) -> None:
        """收到一张**已经解码好**的缩略图（解码在 fetch_thumbs 的工作线程里做）。"""
        if err is not None or img is None:
            return
        self.grid_view.set_photo(image_id, img)
        if self.store.has(str(image_id)):
            self.grid_view.mark_downloaded(str(image_id))

    def _on_thumb_batch_err(self, exc: Exception) -> None:
        """整批缩略图任务本身失败了（单张失败不会走到这里，只记日志）。

        这种情况用户看到的是「一片空白格子」，得给句话，不然像是程序卡住了。
        """
        log.warning("缩略图批量任务失败：%s", exc)
        self.app.set_status(f"缩略图加载失败：{exc}", "warn")

    # ================================================================= 选中/预览
    def select_item(self, item: dict | None) -> None:
        """网格里选中一张：记下它，剩下的展示交给预览面板。"""
        if not item:
            return
        self._selected = item
        iid = str(item.get("id") or "")
        self.grid_view.select(iid)
        record = self.store.get(iid) if iid else None
        self.preview.show_item(item, record["filename"] if record else "")

    # ---------------------------------------------------------------- 预览区自适应
    def _on_body_configure(self, event: tk.Event) -> None:
        """首次布局时把分隔条挪到合适位置：预览面板约占 40%，最少 480px。"""
        if self._sash_set or event.width < 760:
            return
        try:
            preview_w = max(480, min(700, int(event.width * 0.40)))
            self.body.sashpos(0, event.width - preview_w)
            self._sash_set = True
        except tk.TclError:
            pass

    # ================================================================= 下载
    def set_as_wallpaper(self, item: dict | None) -> None:
        if not item or self._busy:
            return
        iid = str(item.get("id") or "")
        keyword = self._keyword or primary_keyword(self.var_keyword.get()) or "wallpaper"

        def _job() -> dict:
            return download_wallpaper(
                item, keyword, self.cfg, self.downloader, self.store, apply=True,
                on_progress=lambda done, total: self.ui.post("bytes", done, total),
                on_stage=lambda text: self.ui.post("stage", text),
            )

        self._set_busy(True)
        self.app.set_status("正在下载并设置壁纸…")
        self.ui.run_bg(_job, on_ok="apply_ok", on_error="apply_err")
        log.info("用户请求设为壁纸：%s（%s）", iid, keyword)

    def download_only(self, item: dict | None) -> None:
        if not item or self._busy:
            return
        keyword = self._keyword or primary_keyword(self.var_keyword.get()) or "wallpaper"

        def _job() -> dict:
            return download_wallpaper(
                item, keyword, self.cfg, self.downloader, self.store, apply=False,
                on_progress=lambda done, total: self.ui.post("bytes", done, total),
                on_stage=lambda text: self.ui.post("stage", text),
            )

        self._set_busy(True)
        self.app.set_status("正在下载…")
        self.ui.run_bg(_job, on_ok="apply_ok", on_error="apply_err")

    def _on_apply_ok(self, record: dict) -> None:
        self._set_busy(False)
        self.app.set_progress(None)
        self.grid_view.mark_downloaded(str(record.get("id")))
        if self._selected and str(self._selected.get("id")) == str(record.get("id")):
            self.preview.mark_downloaded(str(record.get("id") or ""),
                                         str(record.get("filename") or ""))
        self.app.set_status(f"完成：{record.get('filename')}", "ok")
        self.app.add_history_record(record)

    def _on_apply_err(self, exc: Exception) -> None:
        self._set_busy(False)
        self.app.set_progress(None)
        if isinstance(exc, WallpaperError):
            self.app.report_error(exc)
        else:
            self.app.report_error(WallpaperError(f"操作失败：{exc}"))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.btn_search.configure(state=state)
        self.btn_random.configure(state=state)

    # ================================================================= 随机换一张
    def do_random(self) -> None:
        if self._busy:
            return
        keywords = split_keywords(self.cfg.get("default_keyword"))
        if not keywords:
            use_fallback = messagebox.askokcancel(
                "默认关键词为空",
                "设置里的「默认关键词」是空的，将回落到内置分类列表随机挑选一张。\n\n"
                "要现在去设置页填关键词吗？\n（点「确定」继续随机，点「取消」去设置）",
                parent=self,
            )
            if not use_fallback:
                self.app.select_tab("settings")
                return

        def _job() -> dict:
            return random_wallpaper(
                self.cfg, client=self.client, downloader=self.downloader,
                store=self.store,
                on_stage=lambda text: self.ui.post("stage", text),
            )

        self._set_busy(True)
        self.app.set_status("正在随机挑一张壁纸…")
        self.app.set_progress(0.3)
        self.ui.run_bg(_job, on_ok="apply_ok", on_error="apply_err")

    # ================================================================= 状态
    def _on_stage(self, text: str) -> None:
        self.app.set_status(text)

    def _on_bytes(self, done: int, total: int) -> None:
        if total > 0:
            self.app.set_progress(min(0.99, done / total))
            self.app.set_status(
                f"下载中 {done / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB")
        else:
            self.app.set_status(f"下载中 {done / 1024 / 1024:.1f} MB")
