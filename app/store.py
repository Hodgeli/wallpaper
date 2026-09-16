"""下载记录：既用于「已下载去重」，也是历史记录面板的数据源。"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import STORE_PATH, ensure_dirs
from .logsetup import get_logger

log = get_logger("store")

STORE_VERSION = 1


class DownloadStore:
    """线程安全的 JSON 记录表，写入用原子替换。"""

    def __init__(self, path: Path = STORE_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}
        self.load()

    # ------------------------------------------------------------ 读写
    def load(self) -> None:
        with self._lock:
            self._items = {}
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("下载记录读取失败，将重新开始：%s", exc)
                return
            items = raw.get("items") if isinstance(raw, dict) else raw
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and it.get("id"):
                        self._items[str(it["id"])] = it

    def save(self) -> None:
        with self._lock:
            ensure_dirs()
            payload = {
                "version": STORE_VERSION,
                "items": sorted(
                    self._items.values(),
                    key=lambda x: str(x.get("added_at") or ""),
                    reverse=True,
                ),
            }
            tmp = self.path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as exc:
                log.error("下载记录写入失败：%s", exc)
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------ 查询
    def has(self, image_id: str) -> bool:
        with self._lock:
            return str(image_id) in self._items

    def get(self, image_id: str) -> dict[str, Any] | None:
        with self._lock:
            it = self._items.get(str(image_id))
            return dict(it) if it else None

    def all(self) -> list[dict[str, Any]]:
        """按加入时间倒序返回全部记录。"""
        with self._lock:
            return sorted(
                (dict(v) for v in self._items.values()),
                key=lambda x: str(x.get("added_at") or ""),
                reverse=True,
            )

    def recent_ids(self, limit: int = 200) -> set[str]:
        with self._lock:
            ordered = sorted(
                self._items.values(),
                key=lambda x: str(x.get("added_at") or ""),
                reverse=True,
            )
            return {str(x["id"]) for x in ordered[:limit]}

    def existing_file(self, image_id: str) -> Path | None:
        """如果这张图已经下载过且文件还在，返回它的路径。"""
        rec = self.get(image_id)
        if not rec:
            return None
        p = Path(str(rec.get("path") or ""))
        return p if p.is_file() else None

    # ------------------------------------------------------------ 修改
    def upsert(self, record: dict[str, Any]) -> None:
        """写入或更新一条记录。已存在时保留首次加入时间并累加使用次数。"""
        iid = str(record.get("id") or "").strip()
        if not iid:
            return
        with self._lock:
            old = self._items.get(iid) or {}
            merged = dict(old)
            merged.update(record)
            merged["id"] = iid
            merged.setdefault("added_at", datetime.now().isoformat(timespec="seconds"))
            if old:
                merged["added_at"] = old.get("added_at") or merged["added_at"]
                merged["use_count"] = int(old.get("use_count") or 1) + 1
            else:
                merged["use_count"] = 1
            merged["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self._items[iid] = merged
        self.save()

    def remove(self, image_id: str) -> None:
        with self._lock:
            self._items.pop(str(image_id), None)
        self.save()

    def update_fields(self, image_id: str, fields: dict[str, Any],
                      save: bool = True) -> bool:
        """只覆盖指定字段，不动 added_at / use_count。

        「迁移保存目录」「重新下载」这类维护性改动走这里——它们不是一次"使用"，
        不该把 use_count 顶上去，也不该改首次加入时间。
        批量调用时把 save 设为 False，最后统一 save() 一次。
        """
        iid = str(image_id)
        with self._lock:
            rec = self._items.get(iid)
            if not rec:
                return False
            rec.update(fields)
        if save:
            self.save()
        return True

    def prune_missing(self) -> int:
        """剔除文件已被删除的记录，返回剔除条数。"""
        with self._lock:
            gone = [k for k, v in self._items.items()
                    if not Path(str(v.get("path") or "")).is_file()]
            for k in gone:
                self._items.pop(k, None)
        if gone:
            self.save()
        return len(gone)


_store: DownloadStore | None = None
_store_lock = threading.RLock()


def get_store() -> DownloadStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = DownloadStore()
        return _store
