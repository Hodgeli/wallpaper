"""历史页：已经下载/设置过的壁纸，可重新设为壁纸、重新下载或删除本地文件。"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from ..api import WallpaperError
from ..images import fetch_thumbs
from ..logsetup import get_logger
from ..service import delete_wallpaper, redownload_wallpaper
from ..winwall import open_in_explorer, set_wallpaper
from . import theme
from .widgets import open_scaled

log = get_logger("ui.history")

THUMB_W, THUMB_H = 64, 36
GROUP_PREFIX = "grp::"          # 分组节点的 iid 前缀，用来跟图片 id 区分开
UNGROUPED = "（未记录关键词）"


class HistoryTab(ttk.Frame):
    def __init__(self, master: tk.Misc, app: Any) -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.pal = app.pal
        self.cfg = app.cfg
        self.store = app.store
        self.ui = app.ui
        self._photos: dict[str, Any] = {}
        self._rows: dict[str, dict] = {}
        self._grouped = bool(self.cfg.get("history_grouped"))
        self._pending_apply = False

        self._build()
        self.ui.on("hist_thumb", self._on_thumb)
        self.ui.on("hist_batch_err", self._on_batch_err)
        self.ui.on("redownload_ok", self._on_redownload_ok)
        self.ui.on("redownload_err", self._on_redownload_err)

    # ================================================================= 构建
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        head = ttk.Frame(self, style="TFrame")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        ttk.Label(head, text="历史记录", style="Title.TLabel").pack(side="left")
        self.lbl_count = ttk.Label(head, text="", style="Muted.TLabel")
        self.lbl_count.pack(side="left", padx=12)
        ttk.Button(head, text="刷新", command=self.refresh).pack(side="right")
        ttk.Button(head, text="清理失效记录", command=self._prune).pack(side="right", padx=(0, 8))
        self.var_grouped = tk.BooleanVar(value=self._grouped)
        ttk.Checkbutton(head, text="按关键词分组", variable=self.var_grouped,
                        command=self._on_group_toggle).pack(side="right", padx=(0, 12))

        wrap = ttk.Frame(self, style="TFrame")
        wrap.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 6))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        columns = ("time", "keyword", "resolution", "size", "filename")
        self.tree = ttk.Treeview(wrap, columns=columns, show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=THUMB_W + 12, minwidth=THUMB_W + 12, stretch=False,
                         anchor="center")
        headings = {
            "time": ("设置时间", 150, "w"),
            "keyword": ("关键词", 190, "w"),
            "resolution": ("分辨率", 100, "w"),
            "size": ("大小", 90, "e"),
            "filename": ("文件", 340, "w"),
        }
        for key, (text, width, anchor) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key == "filename"))

        self.tree.grid(row=0, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-Button-1>", self._on_double_click)

        self.lbl_path = ttk.Label(self, text="", style="Muted.TLabel",
                                  wraplength=1100, justify="left")
        self.lbl_path.grid(row=2, column=0, sticky="w", padx=12)

        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=3, column=0, sticky="ew", padx=12, pady=10)
        self.btn_apply = ttk.Button(bar, text="设为桌面壁纸", style="Accent.TButton",
                                    command=self.apply_selected, state="disabled")
        self.btn_apply.pack(side="left")
        self.btn_open = ttk.Button(bar, text="在资源管理器中打开", command=self.open_selected,
                                   state="disabled")
        self.btn_open.pack(side="left", padx=8)
        self.btn_redownload = ttk.Button(bar, text="重新下载", command=self.redownload_selected,
                                         state="disabled")
        self.btn_redownload.pack(side="left")
        self.btn_delete = ttk.Button(bar, text="删除本地文件", command=self.delete_selected,
                                     state="disabled")
        self.btn_delete.pack(side="left", padx=8)

        self.refresh()

    # ================================================================= 数据
    def refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        records = self.store.all()

        if self._grouped:
            self._fill_grouped(records)
        else:
            for rec in records:
                self._insert_record("", rec)
            self.lbl_count.configure(text=f"共 {len(records)} 条")

        try:
            self.tree.tag_configure("missing", foreground=self.pal["error"])
            self.tree.tag_configure("group", foreground=self.pal["fg"],
                                    font=theme.font(10, True))
        except tk.TclError:
            pass

        self._load_thumbs(records)
        if not records:
            self.lbl_path.configure(text="还没有下载过任何壁纸。去「搜索」页挑一张吧。")

    def _fill_grouped(self, records: list[dict]) -> None:
        groups: dict[str, list[dict]] = {}
        for rec in records:
            key = str(rec.get("keyword") or "").strip() or UNGROUPED
            groups.setdefault(key, []).append(rec)

        missing_total = 0
        for name in sorted(groups, key=lambda s: (s == UNGROUPED, s.lower())):
            items = groups[name]
            size = sum(int(r.get("file_size") or 0) for r in items)
            gone = sum(1 for r in items
                       if not Path(str(r.get("path") or "")).is_file())
            missing_total += gone
            label = f"▾ {name}"
            if gone:
                label += f"（{gone} 个已丢失）"
            gid = f"{GROUP_PREFIX}{name}"
            self.tree.insert(
                "", "end", iid=gid, text="",
                values=(f"共 {len(items)} 张", label, "",
                        f"{size / 1024 / 1024:.1f} MB", ""),
                open=True, tags=("group",),
            )
            for rec in items:
                self._insert_record(gid, rec)

        text = f"共 {len(records)} 条 · {len(groups)} 个关键词"
        if missing_total:
            text += f"（{missing_total} 个文件已丢失）"
        self.lbl_count.configure(text=text)

    def _insert_record(self, parent: str, rec: dict,
                       index: str | int = "end") -> None:
        iid = str(rec.get("id") or "")
        path = Path(str(rec.get("path") or ""))
        exists = path.is_file()
        size = rec.get("file_size") or 0
        self.tree.insert(
            parent, index, iid=iid, text="",
            values=(
                self._fmt_time(rec.get("added_at")),
                rec.get("keyword") or "—",
                rec.get("resolution") or "—",
                f"{int(size) / 1024 / 1024:.2f} MB" if size else "—",
                ("[已丢失] " if not exists else "") + str(rec.get("filename") or path.name),
            ),
            tags=() if exists else ("missing",),
        )
        self._rows[iid] = rec

    def add_record(self, rec: dict) -> None:
        """只把这一条记录放进表格，不重建整张表。

        下载完成时走这里。整表重建在记录上百条时要重排全部行，还要给每条没有
        磁盘缓存的记录重新发一次缩略图请求——只为了多一行，代价太大。
        """
        iid = str(rec.get("id") or "")
        if not iid:
            return
        if self._grouped:
            # 分组视图下位置取决于关键词和组内排序，增量插容易插错地方。
            # 分组视图切换本身是低频操作，重建就重建了。
            self.refresh()
            return

        if self.tree.exists(iid):
            # 已存在（重新下载、重新设为壁纸）：原地替换，别把它挪到最前面。
            # store 里 added_at 保留的是**首次**加入时间，排序位置本来就不该变。
            try:
                index: str | int = int(self.tree.index(iid))
            except tk.TclError:
                index = "end"
            self.tree.delete(iid)
            self._photos.pop(iid, None)
            self._rows.pop(iid, None)
        else:
            index = 0            # 新记录的 added_at 最新，排在第一位

        self._insert_record("", rec, index)
        self.lbl_count.configure(text=f"共 {len(self._rows)} 条")
        self.lbl_path.configure(text="")
        self._load_thumbs([rec])

    def _load_thumbs(self, records: list[dict]) -> None:
        """批量补缩略图。

        命中磁盘缓存的那些也一并交给 fetch_thumbs——它会自己查缓存，然后在
        工作线程里解码。以前这里是在主线程直接解码所有命中的缓存，历史记录一
        多，切到历史页就会卡住一下。
        """
        items = []
        for rec in records:
            iid = str(rec.get("id") or "")
            if not iid:
                continue
            items.append({"id": iid, "thumbs": {"large": str(rec.get("thumb_url") or "")}})
        if not items:
            return

        def _job() -> None:
            fetch_thumbs(
                items, self.app.downloader,
                on_done=lambda iid, img, err: self.ui.post("hist_thumb", iid, img, err),
                decode=lambda p: open_scaled(p, THUMB_W, THUMB_H),
            )

        self.ui.run_bg(_job, on_error="hist_batch_err")

    def _on_thumb(self, image_id: str, img: Any, err: Exception | None) -> None:
        """收到一张**已经解码好**的缩略图（解码在 fetch_thumbs 的工作线程里做）。"""
        if err is not None or img is None or not self.tree.exists(str(image_id)):
            return
        photo = self.app.photo(img)
        self._photos[str(image_id)] = photo
        try:
            self.tree.item(str(image_id), image=photo)
        except tk.TclError:
            pass

    def _on_batch_err(self, exc: Exception) -> None:
        """整批缩略图任务失败了（单张失败走 _on_thumb 的 err 分支，只记日志）。"""
        log.warning("历史缩略图批量任务失败：%s", exc)
        self.app.set_status(f"历史缩略图加载失败：{exc}", "warn")

    # ================================================================= 选择
    def _on_group_toggle(self) -> None:
        self._grouped = bool(self.var_grouped.get())
        self.cfg.set("history_grouped", self._grouped)
        self.cfg.save()
        self.refresh()

    def _selected_record(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._rows.get(str(sel[0]))

    def _on_select(self, _event: tk.Event) -> None:
        rec = self._selected_record()
        state = "normal" if rec else "disabled"
        for btn in (self.btn_apply, self.btn_open, self.btn_delete, self.btn_redownload):
            btn.configure(state=state)
        if not rec:
            return
        path = Path(str(rec.get("path") or ""))
        if path.is_file():
            size = path.stat().st_size / 1024 / 1024
            self.lbl_path.configure(text=f"{path}　（{size:.2f} MB）")
        else:
            self.lbl_path.configure(
                text=f"{path}　— 文件已丢失，点「重新下载」可以找回")

    def _on_double_click(self, _event: tk.Event) -> None:
        rec = self._selected_record()
        if not rec:
            return
        if Path(str(rec.get("path") or "")).is_file():
            self.apply_selected()
        else:
            self.redownload_selected(apply_after=True)

    # ================================================================= 操作
    def apply_selected(self) -> None:
        rec = self._selected_record()
        if not rec:
            return
        path = Path(str(rec.get("path") or ""))
        if not path.is_file():
            if messagebox.askyesno(
                "文件已丢失",
                f"这个文件已经不在了：\n{path}\n\n要重新下载回来吗？",
                parent=self,
            ):
                self.redownload_selected(apply_after=True)
            return
        try:
            set_wallpaper(path, self.cfg.get("fill_mode") or "fill")
        except OSError as exc:
            self.app.report_error(WallpaperError(f"设置壁纸失败：{exc}"))
            return
        self.app.set_status(f"已设为壁纸：{path.name}", "ok")

    def open_selected(self) -> None:
        rec = self._selected_record()
        if not rec:
            return
        path = Path(str(rec.get("path") or ""))
        try:
            open_in_explorer(path, select_file=path.is_file())
        except OSError as exc:
            self.app.report_error(WallpaperError(f"打开资源管理器失败：{exc}"))

    def redownload_selected(self, apply_after: bool = False) -> None:
        rec = self._selected_record()
        if not rec:
            return
        path = Path(str(rec.get("path") or ""))
        if path.is_file() and not apply_after:
            if not messagebox.askyesno(
                "文件还在",
                f"本地已经有这个文件了：\n{path}\n\n要重新下载覆盖它吗？",
                parent=self,
            ):
                return

        iid = str(rec.get("id") or "")
        self.btn_redownload.configure(state="disabled")
        self.app.set_status(f"正在重新下载 {rec.get('filename') or iid} …")
        self.app.set_progress(0.1)

        def _job() -> dict:
            return redownload_wallpaper(
                rec, self.cfg, self.app.client, self.app.downloader, self.store,
                on_progress=lambda done, total: self.ui.post("bytes", done, total),
                on_stage=lambda text: self.ui.post("stage", text),
            )

        self._pending_apply = apply_after
        self.ui.run_bg(_job, on_ok="redownload_ok", on_error="redownload_err")

    def _on_redownload_ok(self, rec: dict) -> None:
        self.app.set_progress(None)
        self.btn_redownload.configure(state="normal")
        path = Path(str(rec.get("path") or ""))
        self.app.set_status(f"已重新下载：{path.name}", "ok")
        self.refresh()
        if getattr(self, "_pending_apply", False):
            self._pending_apply = False
            try:
                set_wallpaper(path, self.cfg.get("fill_mode") or "fill")
                self.app.set_status(f"已重新下载并设为壁纸：{path.name}", "ok")
            except OSError as exc:
                self.app.report_error(WallpaperError(f"设置壁纸失败：{exc}"))

    def _on_redownload_err(self, exc: Exception) -> None:
        self.app.set_progress(None)
        self._pending_apply = False
        self.btn_redownload.configure(state="normal")
        self.app.report_error(exc if isinstance(exc, WallpaperError)
                              else WallpaperError(f"重新下载失败：{exc}"))

    def delete_selected(self) -> None:
        rec = self._selected_record()
        if not rec:
            return
        path = Path(str(rec.get("path") or ""))
        if not messagebox.askyesno(
            "确认删除",
            f"将删除本地文件并从历史记录中移除：\n\n{path}\n\n"
            "这个操作不可撤销（不进回收站）。确定继续吗？",
            parent=self,
        ):
            return
        try:
            delete_wallpaper(rec, self.cfg)
        except OSError as exc:
            self.app.report_error(WallpaperError(f"删除文件失败：{exc}"))
            return
        self.store.remove(str(rec.get("id") or ""))
        self.app.set_status(f"已删除：{path.name}", "ok")
        self.refresh()

    def _prune(self) -> None:
        removed = self.store.prune_missing()
        self.app.set_status(f"已清理 {removed} 条失效记录" if removed else "没有失效记录")
        self.refresh()

    # ================================================================= 工具
    @staticmethod
    def _fmt_time(raw: Any) -> str:
        try:
            return datetime.fromisoformat(str(raw)).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return str(raw or "—")
