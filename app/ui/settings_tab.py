"""设置页：保存位置、默认搜索配置、显示方式、内容与网络、配置文件位置。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from ..api import WallpaperError, resolve_proxies, system_proxy
from ..config import (
    CATEGORY_CHOICES,
    CONFIG_PATH,
    FILL_LABEL_TO_KEY,
    FILL_MODE_LABELS,
    NAMING_LABEL_TO_KEY,
    NAMING_MODES,
    RATIO_CHOICES,
    RESOLUTION_CHOICES,
    SORTING_CHOICES,
    THEME_LABEL_TO_KEY,
    THEME_MODES,
    as_int,
)
from ..images import cache_size_mb, clear_cache, evict_cache
from ..logsetup import get_logger
from ..service import migrate_wallpapers
from ..winwall import auto_resolution_text, describe_displays, open_in_explorer

log = get_logger("ui.settings")

AUTO_PREFIX = "自适应"
CUSTOM_LABEL = "自定义"


def auto_label() -> str:
    """自适应的显示文本。必须在运行时算——DPI 感知在构造主窗口时才开启，
    模块导入阶段拿到的是被系统缩放过的错误分辨率。"""
    return f"{AUTO_PREFIX}（{auto_resolution_text()}）"


class SettingsTab(ttk.Frame):
    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.pal = app.pal
        self.cfg = app.cfg
        self.ui = app.ui
        # 清掉"已保存 ✓"的那个定时器 id，关窗时要用它取消
        self._saved_job: str | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._build()
        self.load_from_config()
        self.ui.on("migrate_ok", self._on_migrate_done)
        self.ui.on("migrate_err", self._on_migrate_err)

    def cancel_pending_timers(self) -> None:
        """关窗时取消还没触发的定时器。

        不取消的话，窗口 `destroy()` 之后它照样触发，Tcl 会打
        `invalid command name "...<lambda>"` —— 纯噪音，但看着像坏了。
        """
        if self._saved_job is not None:
            try:
                self.after_cancel(self._saved_job)
            except (tk.TclError, ValueError):
                pass
            self._saved_job = None

    # ================================================================= 构建
    def _build(self) -> None:
        canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=self.pal["bg"])
        vbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        holder = ttk.Frame(canvas, style="TFrame")
        win = canvas.create_window((0, 0), window=holder, anchor="nw")

        def _resize(event: tk.Event) -> None:
            canvas.itemconfigure(win, width=event.width)

        def _scrollregion(_e: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        canvas.bind("<Configure>", _resize)
        holder.bind("<Configure>", _scrollregion)
        canvas.bind("<Enter>", lambda _e: canvas.bind_all(
            "<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        holder.columnconfigure(0, weight=1)
        pad = {"padx": 14, "pady": (10, 0), "sticky": "ew"}

        # ---------------------------------------------------- 保存与命名
        g1 = ttk.Labelframe(holder, text=" 保存与命名 ")
        g1.grid(row=0, column=0, **pad)
        g1.columnconfigure(1, weight=1)

        ttk.Label(g1, text="保存目录").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        self.var_save_dir = tk.StringVar()
        ttk.Entry(g1, textvariable=self.var_save_dir).grid(row=0, column=1, sticky="ew",
                                                          pady=8)
        btns = ttk.Frame(g1, style="TFrame")
        btns.grid(row=0, column=2, padx=8, pady=8)
        ttk.Button(btns, text="浏览…", width=8, command=self._pick_dir).pack(side="left")
        ttk.Button(btns, text="打开", width=6, command=self._open_save_dir).pack(
            side="left", padx=(6, 0))

        ttk.Label(g1, text="命名方式").grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))
        self.var_naming = tk.StringVar()
        ttk.Combobox(g1, textvariable=self.var_naming, state="readonly",
                     values=list(NAMING_MODES.values())).grid(row=1, column=1, sticky="w",
                                                              pady=(0, 8))
        ttk.Label(g1, text="按关键词自动建子目录，例如 nature\\nature_2500x1401_rddgwm.jpg",
                  style="Hint.TLabel").grid(row=2, column=0, columnspan=3, sticky="w",
                                            padx=10, pady=(0, 10))

        # ---------------------------------------------------- 默认搜索
        g2 = ttk.Labelframe(holder, text=" 默认搜索配置（「随机换一张」使用，关键词会预填到搜索页） ")
        g2.grid(row=1, column=0, **pad)
        g2.columnconfigure(1, weight=1)

        ttk.Label(g2, text="默认关键词").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        self.var_keyword = tk.StringVar()
        ttk.Entry(g2, textvariable=self.var_keyword).grid(row=0, column=1, sticky="ew",
                                                          pady=8, padx=(0, 10))
        ttk.Label(g2, text="可填多个，用逗号隔开（如 cyberpunk, anime, space）。"
                           "「随机换一张」每次从里面随机取一个，搜索页关键词框留空时也这样。\n"
                           "整个留空时回落到内置分类（nature、cyberpunk…）。"
                           "注意 wallhaven 只认英文关键词。",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w",
                                            padx=10, pady=(0, 10))

        ttk.Label(g2, text="默认分辨率").grid(row=2, column=0, sticky="w", padx=10, pady=(0, 8))
        self.var_resolution = tk.StringVar()
        self.cmb_res = ttk.Combobox(
            g2, textvariable=self.var_resolution, state="readonly",
            values=[auto_label()] + RESOLUTION_CHOICES[1:])
        self.cmb_res.grid(row=2, column=1, sticky="w", pady=(0, 8), padx=(0, 10))
        self.cmb_res.bind("<<ComboboxSelected>>", self._on_res_change)
        self.lbl_displays = ttk.Label(g2, text=describe_displays(), style="Hint.TLabel")
        self.lbl_displays.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 10))

        ttk.Label(g2, text="默认比例").grid(row=4, column=0, sticky="w", padx=10, pady=(0, 8))
        self.var_ratio = tk.StringVar()
        ttk.Combobox(g2, textvariable=self.var_ratio, state="readonly",
                     values=list(RATIO_CHOICES)).grid(row=4, column=1, sticky="w",
                                                      pady=(0, 8), padx=(0, 10))

        ttk.Label(g2, text="默认排序").grid(row=5, column=0, sticky="w", padx=10, pady=(0, 8))
        self.var_sorting = tk.StringVar()
        ttk.Combobox(g2, textvariable=self.var_sorting, state="readonly",
                     values=list(SORTING_CHOICES)).grid(row=5, column=1, sticky="w",
                                                        pady=(0, 8), padx=(0, 10))

        ttk.Label(g2, text="默认分类").grid(row=6, column=0, sticky="w", padx=10, pady=(0, 8))
        self.var_category = tk.StringVar()
        ttk.Combobox(g2, textvariable=self.var_category, state="readonly",
                     values=list(CATEGORY_CHOICES)).grid(row=6, column=1, sticky="w",
                                                         pady=(0, 8), padx=(0, 10))

        ttk.Label(g2, text="颜色筛选").grid(row=7, column=0, sticky="w", padx=10, pady=(0, 8))
        self.var_color = tk.StringVar()
        ttk.Entry(g2, textvariable=self.var_color, width=14).grid(
            row=7, column=1, sticky="w", pady=(0, 8), padx=(0, 10))
        ttk.Label(g2, text="填 16 进制色值（如 0066cc）只搜主色调接近的图，留空表示不筛选。",
                  style="Hint.TLabel").grid(row=8, column=0, columnspan=2, sticky="w",
                                            padx=10, pady=(0, 10))

        # ---------------------------------------------------- 显示与内容
        g3 = ttk.Labelframe(holder, text=" 显示与内容 ")
        g3.grid(row=2, column=0, **pad)
        g3.columnconfigure(1, weight=1)

        ttk.Label(g3, text="壁纸填充方式").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        self.var_fill = tk.StringVar()
        ttk.Combobox(g3, textvariable=self.var_fill, state="readonly",
                     values=list(FILL_MODE_LABELS.values())).grid(
            row=0, column=1, sticky="w", pady=8, padx=(0, 10))
        ttk.Label(g3, text="原脚本用的是「拉伸」，在非原生分辨率下会把图片拉变形；"
                           "「填充」等比放大铺满、不变形，是 Windows 的默认行为。",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w",
                                            padx=10, pady=(0, 10))

        ttk.Label(g3, text="界面主题").grid(row=2, column=0, sticky="w", padx=10, pady=(0, 8))
        self.var_theme = tk.StringVar()
        ttk.Combobox(g3, textvariable=self.var_theme, state="readonly",
                     values=list(THEME_MODES.values())).grid(
            row=2, column=1, sticky="w", pady=(0, 8), padx=(0, 10))
        ttk.Label(g3, text="配色是全局 ttk 样式，改完要重启程序才生效。",
                  style="Hint.TLabel").grid(row=3, column=0, columnspan=2, sticky="w",
                                            padx=10, pady=(0, 10))

        self.var_nsfw = tk.BooleanVar()
        ttk.Checkbutton(g3, text="允许 NSFW / Sketchy 内容（需要填 API Key，默认关闭）",
                        variable=self.var_nsfw).grid(row=4, column=0, columnspan=2,
                                                     sticky="w", padx=10, pady=(0, 6))

        ttk.Label(g3, text="wallhaven API Key").grid(row=5, column=0, sticky="w",
                                                     padx=10, pady=(0, 8))
        self.var_apikey = tk.StringVar()
        ttk.Entry(g3, textvariable=self.var_apikey, show="●").grid(
            row=5, column=1, sticky="ew", pady=(0, 8), padx=(0, 10))
        ttk.Label(g3, text="到 wallhaven.cc → My Account → Settings → API Key 免费申请。"
                           "留空则只能搜 SFW 内容。",
                  style="Hint.TLabel").grid(row=6, column=0, columnspan=2, sticky="w",
                                            padx=10, pady=(0, 10))

        # ---------------------------------------------------- 网络
        g4 = ttk.Labelframe(holder, text=" 网络代理 ")
        g4.grid(row=3, column=0, **pad)
        g4.columnconfigure(1, weight=1)

        ttk.Label(g4, text="代理模式").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        self.var_proxy_mode = tk.StringVar()
        ttk.Combobox(g4, textvariable=self.var_proxy_mode, state="readonly", width=26,
                     values=["自动跟随系统代理", "不使用代理", "手动指定"]).grid(
            row=0, column=1, sticky="w", pady=8, padx=(0, 10))
        self.var_proxy_mode.trace_add("write", lambda *_: self._sync_proxy_state())
        self.var_proxy_url = tk.StringVar()

        ttk.Label(g4, text="代理地址").grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))
        self.ent_proxy = ttk.Entry(g4, textvariable=self.var_proxy_url)
        self.ent_proxy.grid(row=1, column=1, sticky="ew", pady=(0, 8), padx=(0, 10))

        self.lbl_proxy_info = ttk.Label(g4, text="", style="Hint.TLabel", wraplength=760,
                                        justify="left")
        self.lbl_proxy_info.grid(row=2, column=0, columnspan=2, sticky="w", padx=10,
                                 pady=(0, 6))
        ttk.Button(g4, text="测试连接", command=self._test_connection).grid(
            row=3, column=0, sticky="w", padx=10, pady=(0, 10))

        # ---------------------------------------------------- 缓存与关于
        g5 = ttk.Labelframe(holder, text=" 缓存与配置文件 ")
        g5.grid(row=4, column=0, **pad)
        g5.columnconfigure(1, weight=1)

        ttk.Label(g5, text="缩略图缓存上限").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        box = ttk.Frame(g5, style="TFrame")
        box.grid(row=0, column=1, sticky="w", pady=8)
        self.var_cache_mb = tk.IntVar(value=200)
        ttk.Spinbox(box, from_=50, to=5000, increment=50, width=8,
                    textvariable=self.var_cache_mb).pack(side="left")
        ttk.Label(box, text=" MB", style="Muted.TLabel").pack(side="left")
        self.lbl_cache = ttk.Label(box, text="", style="Hint.TLabel")
        self.lbl_cache.pack(side="left", padx=10)
        ttk.Button(box, text="立即清理", command=self._clear_cache).pack(side="left")

        ttk.Label(g5, text="配置文件").grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))
        path_box = ttk.Frame(g5, style="TFrame")
        path_box.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        lbl = ttk.Label(path_box, text=str(CONFIG_PATH), style="Muted.TLabel")
        lbl.pack(side="left")
        ttk.Button(path_box, text="打开所在文件夹",
                   command=lambda: open_in_explorer(CONFIG_PATH)).pack(side="left", padx=10)

        # ---------------------------------------------------- 底部按钮
        bar = ttk.Frame(holder, style="TFrame")
        bar.grid(row=5, column=0, sticky="ew", padx=14, pady=16)
        ttk.Button(bar, text="保存设置", style="Accent.TButton",
                   command=self.save_to_config).pack(side="left")
        ttk.Button(bar, text="恢复默认", command=self._reset).pack(side="left", padx=8)
        self.lbl_saved = ttk.Label(bar, text="", style="Muted.TLabel")
        self.lbl_saved.pack(side="left", padx=10)

        self._refresh_cache_label()

    # ================================================================= 数据
    def load_from_config(self) -> None:
        cfg = self.cfg
        self.var_save_dir.set(str(cfg.save_dir))

        naming = cfg.get("naming") or "keyword_res_id"
        self.var_naming.set(NAMING_MODES.get(naming, NAMING_MODES["keyword_res_id"]))

        self.var_keyword.set(cfg.get("default_keyword") or "")

        res = (cfg.get("resolution") or "auto").strip()
        self._custom_res = None
        if res in ("", "auto"):
            self.var_resolution.set(auto_label())
        elif res in RESOLUTION_CHOICES:
            self.var_resolution.set(res)
        else:
            self._custom_res = res
            self.var_resolution.set(f"{CUSTOM_LABEL}：{res}")

        ratio = cfg.get("ratio")
        self.var_ratio.set(_label_of(RATIO_CHOICES, ratio, "16:9 + 16:10（默认）"))

        sorting = (cfg.get("sorting") or "favorites")
        self.var_sorting.set(_label_of(
            {k: v[0] for k, v in SORTING_CHOICES.items()}, sorting, "收藏数"))

        self.var_category.set(_label_of(CATEGORY_CHOICES, cfg.get("category") or "111", "全部"))

        self.var_color.set(cfg.get("color") or "")
        self.var_fill.set(FILL_MODE_LABELS.get(cfg.get("fill_mode") or "fill",
                                               FILL_MODE_LABELS["fill"]))
        self.var_theme.set(THEME_MODES.get(cfg.get("theme") or "auto",
                                           THEME_MODES["auto"]))
        self.var_nsfw.set(bool(cfg.get("nsfw")))
        self.var_apikey.set(cfg.get("api_key") or "")
        # 走 as_int：配置里的值可能是手改坏的（比如 "200MB"），
        # 这里直接 int() 的话抛在 App 初始化路径上，程序直接起不来
        self.var_cache_mb.set(as_int(cfg.get("thumb_cache_mb") or 200, 200))

        mode = cfg.get("proxy_mode") or "auto"
        self.var_proxy_mode.set({"auto": "自动跟随系统代理", "off": "不使用代理",
                                 "manual": "手动指定"}.get(mode, "自动跟随系统代理"))
        self.var_proxy_url.set(cfg.get("proxy_url") or "")
        self._sync_proxy_state()

    def save_to_config(self) -> None:
        cfg = self.cfg
        old_dir = Path(cfg.save_dir)
        save_dir = Path(self.var_save_dir.get().strip() or str(cfg.save_dir))
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("目录不可用",
                                 f"无法创建或写入这个目录：\n{save_dir}\n\n{exc}", parent=self)
            return

        res_text = self.var_resolution.get()
        if res_text.startswith(AUTO_PREFIX):
            resolution = "auto"
        elif res_text.startswith(CUSTOM_LABEL):
            resolution = self._custom_res or "auto"
        else:
            resolution = res_text

        color = self.var_color.get().strip().lstrip("#")
        if color and not _is_hex(color):
            messagebox.showerror("颜色格式不对", "颜色筛选请填 16 进制色值，例如 0066cc。",
                                 parent=self)
            return

        if self.var_nsfw.get() and not self.var_apikey.get().strip():
            if not messagebox.askokcancel(
                "缺少 API Key",
                "开启 NSFW 内容必须提供 wallhaven API Key，否则请求会被拒绝。\n\n"
                "确定要保存吗？（保存后请尽快补上 Key）",
                parent=self,
            ):
                return

        new_keyword = self.var_keyword.get().strip()

        cfg.update(
            save_dir=str(save_dir),
            naming=NAMING_LABEL_TO_KEY.get(self.var_naming.get(), "keyword_res_id"),
            default_keyword=new_keyword,
            resolution=resolution,
            ratio=RATIO_CHOICES.get(self.var_ratio.get(), "16x9,16x10"),
            sorting=SORTING_CHOICES.get(self.var_sorting.get(), ("favorites", "desc"))[0],
            order=SORTING_CHOICES.get(self.var_sorting.get(), ("favorites", "desc"))[1],
            category=CATEGORY_CHOICES.get(self.var_category.get(), "111"),
            color=color,
            fill_mode=FILL_LABEL_TO_KEY.get(self.var_fill.get(), "fill"),
            theme=THEME_LABEL_TO_KEY.get(self.var_theme.get(), "auto"),
            nsfw=bool(self.var_nsfw.get()),
            api_key=self.var_apikey.get().strip(),
            thumb_cache_mb=int(self.var_cache_mb.get() or 200),
            proxy_mode={"自动跟随系统代理": "auto", "不使用代理": "off",
                        "手动指定": "manual"}.get(self.var_proxy_mode.get(), "auto"),
            proxy_url=self.var_proxy_url.get().strip(),
        )
        cfg.save()

        # 把「默认关键词」同步给搜索页的输入框。要不要覆盖由搜索页自己判断：
        # 只有在框里是空的、或还停在上一版默认值上时才写进去，
        # 用户手敲的别的关键词不会被冲掉。
        search_tab = getattr(self.app, "search_tab", None)
        if search_tab is not None:
            try:
                search_tab.set_default_keyword(new_keyword)
            except Exception as exc:  # noqa: BLE001
                log.warning("同步默认关键词到搜索页失败：%s", exc)

        evict_cache(cfg.get("thumb_cache_mb"))
        self._refresh_cache_label()
        self.lbl_saved.configure(text="已保存 ✓")
        # 2.5 秒后把"已保存 ✓"清掉。先取消上一次的：连点两次保存时不会两个定时器
        # 叠着，关窗时也能干净地取消（见 cancel_pending_timers）。
        self.cancel_pending_timers()
        self._saved_job = self.after(
            2500, lambda: self.lbl_saved.configure(text=""))
        self.app.set_status("设置已保存", "ok")
        log.info("设置已保存：%s", {k: v for k, v in cfg.as_dict().items() if k != "api_key"})

        # 保存目录变了就把已经下载的壁纸一起搬过去，别让历史记录里的路径全失效
        if save_dir.resolve() != old_dir.resolve():
            self._start_migration(old_dir, save_dir)

    # ================================================================= 目录迁移
    def _start_migration(self, old_dir: Path, new_dir: Path) -> None:
        records = self.app.store.all()
        pending = [r for r in records
                   if Path(str(r.get("path") or "")).is_file()]
        if not pending:
            self.app.set_status(f"保存目录已改到 {new_dir}（没有需要迁移的文件）", "ok")
            return

        def _job() -> dict:
            return migrate_wallpapers(
                old_dir, new_dir, self.app.store,
                on_progress=lambda text: self.ui.post("stage", text),
            )

        self.app.set_status(f"保存目录已改，正在迁移 {len(pending)} 个已下载文件 …")
        self.app.set_progress(0.1)
        self.ui.run_bg(_job, on_ok="migrate_ok", on_error="migrate_err")

    def _on_migrate_done(self, stats: dict) -> None:
        self.app.set_progress(None)
        moved = int(stats.get("moved") or 0)
        already = int(stats.get("already") or 0)
        renamed = int(stats.get("renamed") or 0)
        missing = int(stats.get("missing") or 0)
        outside = int(stats.get("outside") or 0)
        failed = int(stats.get("failed") or 0)
        errors = stats.get("errors") or []

        done = moved + already
        bits = []
        if moved:
            bits.append(f"迁移 {moved} 个")
        if already:
            bits.append(f"{already} 个目标已有相同文件（源文件保留）")
        if renamed:
            bits.append(f"{renamed} 个重命名")
        if missing:
            bits.append(f"{missing} 个源文件已丢失")
        if outside:
            bits.append(f"{outside} 个不在旧目录、未处理")
        if failed:
            bits.append(f"{failed} 个失败")
        summary = "；".join(bits) or "没有需要迁移的文件"

        self.app.set_status(f"目录迁移完成：{summary}", "warn" if failed else "ok")
        self.app.refresh_history()
        log.info("目录迁移：%s", summary)

        # 「源文件已丢失」是迁移之前就存在的情况，不值得每次都弹窗打断，
        # 状态栏里说一句就够了；只有真的出错、或有文件没被搬走才弹窗。
        if failed or outside:
            detail = summary
            if errors:
                detail += "\n\n失败明细（前 5 条）：\n" + "\n".join(errors[:5])
            if outside:
                detail += "\n\n不在旧目录里的文件没有搬动——那些是你自己挪过位置的，" \
                          "程序不会擅自替你搬。"
            messagebox.showwarning("目录迁移完成，但有需要留意的地方", detail, parent=self)
        elif done:
            self.app.set_status(f"已迁移 {done} 个文件到新目录", "ok")

    def _on_migrate_err(self, exc: Exception) -> None:
        self.app.set_progress(None)
        log.error("目录迁移失败：%s", exc)
        self.app.set_status("目录迁移失败，详见日志", "error")
        messagebox.showerror(
            "目录迁移失败",
            f"{exc}\n\n保存目录已经改了，但旧文件还留在原处。\n"
            "历史记录里对应的文件会显示为「已丢失」，可以逐个「重新下载」。",
            parent=self,
        )

    def _reset(self) -> None:
        if not messagebox.askyesno("恢复默认", "把所有设置恢复成默认值？（不会删除已下载的壁纸）",
                                   parent=self):
            return
        self.cfg.reset()
        self.cfg.save()
        self.load_from_config()
        self.lbl_saved.configure(text="已恢复默认 ✓")
        self.app.set_status("设置已恢复默认", "ok")

    # ================================================================= 交互
    def _pick_dir(self) -> None:
        current = self.var_save_dir.get().strip()
        chosen = filedialog.askdirectory(
            title="选择壁纸保存目录",
            initialdir=current if Path(current).is_dir() else str(Path.home()),
            parent=self,
        )
        if chosen:
            self.var_save_dir.set(chosen)

    def _open_save_dir(self) -> None:
        folder = Path(self.var_save_dir.get().strip() or ".")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            open_in_explorer(folder)
        except OSError as exc:
            self.app.report_error(WallpaperError(f"打开目录失败：{exc}"))

    def _on_res_change(self, _event: tk.Event) -> None:
        if self.var_resolution.get() == CUSTOM_LABEL:
            value = self.app.ask_custom_resolution()
            if value:
                self._custom_res = value
                self.var_resolution.set(f"{CUSTOM_LABEL}：{value}")
            else:
                self.var_resolution.set(auto_label())

    def _sync_proxy_state(self) -> None:
        manual = self.var_proxy_mode.get() == "手动指定"
        self.ent_proxy.configure(state="normal" if manual else "disabled")
        auto = system_proxy()
        if self.var_proxy_mode.get() == "自动跟随系统代理":
            self.lbl_proxy_info.configure(
                text=(f"检测到系统代理：{auto}（来自 Windows 设置，Clash 等软件开「系统代理」就写在这里）"
                      if auto else
                      "当前系统没有配置代理。若访问 wallhaven 失败，请检查 Clash 是否开启了「系统代理」，"
                      "或改用手动指定。"))
        elif self.var_proxy_mode.get() == "不使用代理":
            self.lbl_proxy_info.configure(text="直连 wallhaven。国内网络通常无法直连，可能一直超时。")
        else:
            self.lbl_proxy_info.configure(text="手动填写形如 http://127.0.0.1:7890 的地址。")

    def _test_connection(self) -> None:
        # 先落盘再测，保证用的是界面上当前的设置
        self.save_to_config()
        self.lbl_proxy_info.configure(text="正在测试…")

        def _job() -> str:
            client = self.app.client
            proxies = resolve_proxies(self.cfg)
            result = client.search(keyword="nature", page=1)
            total = (result.get("meta") or {}).get("total")
            return f"连接正常：{total} 条结果，代理={proxies or '直连'}"

        def _ok(text: str) -> None:
            self.lbl_proxy_info.configure(text=text)

        def _err(exc: Exception) -> None:
            msg = exc.message if isinstance(exc, WallpaperError) else str(exc)
            self.lbl_proxy_info.configure(text=f"连接失败：{msg}")

        self.app.ui.run_bg(_job, on_ok="__settings_ok", on_error="__settings_err")
        self.app.ui.on("__settings_ok", _ok)
        self.app.ui.on("__settings_err", _err)

    def _clear_cache(self) -> None:
        clear_cache()
        self._refresh_cache_label()
        self.app.set_status("缩略图缓存已清空", "ok")

    def _refresh_cache_label(self) -> None:
        try:
            self.lbl_cache.configure(text=f"当前占用 {cache_size_mb():.1f} MB")
        except tk.TclError:
            pass


def _label_of(mapping: dict[str, Any], value: Any, fallback: str) -> str:
    for label, val in mapping.items():
        if val == value:
            return label
    return fallback


def _is_hex(text: str) -> bool:
    if len(text) not in (3, 6):
        return False
    try:
        int(text, 16)
        return True
    except ValueError:
        return False
