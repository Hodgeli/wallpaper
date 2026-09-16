"""业务服务层：下载壁纸、设为桌面、随机换一张。GUI 与 --random 共用。"""
from __future__ import annotations

import random
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .api import (
    WALLPAPER_API,
    NoResult,
    ParseError,
    WallhavenClient,
    WallpaperError,
)
from .config import FALLBACK_KEYWORDS, Config, get_config
from .images import Downloader, cleanup_empty_dirs
from .logsetup import get_logger
from .naming import guess_ext, target_path
from .store import DownloadStore, get_store
from .winwall import auto_resolution_text, set_wallpaper

log = get_logger("service")

ProgressCb = Callable[[str], None]
BytesCb = Callable[[int, int], None]

# 「默认关键词」的分隔符：中英文逗号、顿号、分号、换行都认
KEYWORD_SPLIT = re.compile(r"[,，、;；\r\n]")


# --------------------------------------------------------------------- 工具
def resolve_atleast(cfg: Config) -> str | None:
    """把配置里的分辨率设置翻译成 API 的 atleast 参数。"""
    value = (cfg.get("resolution") or "auto").strip()
    if not value or value == "auto":
        return auto_resolution_text()
    return value


def split_keywords(raw: Any) -> list[str]:
    """把「默认关键词」拆成列表。

    支持逗号分隔（中英文逗号、顿号、分号、换行都认），去空白、去空项，
    并把项内部的连续空白压成单个空格。单个关键词原样返回成一项，
    所以不带分隔符时行为跟以前完全一样。
    """
    if not raw:
        return []
    items: list[str] = []
    for seg in KEYWORD_SPLIT.split(str(raw)):
        seg = re.sub(r"\s+", " ", seg).strip()
        if seg:
            items.append(seg)
    return items


def keyword_pool(cfg: Config) -> tuple[list[str], str]:
    """返回 (候选关键词列表, 来源)。

    来源 "config" 表示来自「默认关键词」配置；"fallback" 表示配置为空、
    用的是内置分类列表。注意内置列表只在配置为空时兜底，不会被拆分配置影响。
    """
    items = split_keywords(cfg.get("default_keyword"))
    if items:
        return items, "config"
    return list(FALLBACK_KEYWORDS), "fallback"


def pick_keyword(cfg: Config) -> tuple[str, str, int]:
    """从候选里随机挑一个关键词。返回 (关键词, 来源, 候选总数)。"""
    pool, source = keyword_pool(cfg)
    return random.choice(pool), source, len(pool)


# --------------------------------------------------------------------- 搜索
def search(client: WallhavenClient, keyword: str, page: int, *,
           atleast: str | None = None, ratios: str | None = None,
           sorting: str | None = None, color: str | None = None,
           seed: str | None = None, on_wait: Callable[[float, str], None] | None = None,
           ) -> dict[str, Any]:
    """一次搜索。atleast/ratios 传 None 表示用配置里的值。"""
    return client.search(
        keyword=keyword,
        page=page,
        atleast=atleast,
        ratios=ratios,
        sorting=sorting,
        color=color,
        seed=seed,
        on_wait=on_wait,
    )


def pick_random_item(items: list[dict[str, Any]], store: DownloadStore,
                     skip_downloaded: bool = True) -> dict[str, Any] | None:
    """从一批结果里随机挑一张，默认跳过已经下载过的。"""
    if not items:
        return None
    pool = items
    if skip_downloaded:
        fresh = [it for it in items if not store.has(str(it.get("id")))]
        if fresh:
            pool = fresh
    return random.choice(pool)


# --------------------------------------------------------------------- 下载
def download_wallpaper(item: dict[str, Any], keyword: str, cfg: Config,
                       downloader: Downloader, store: DownloadStore,
                       *, apply: bool = True,
                       on_progress: BytesCb | None = None,
                       on_stage: ProgressCb | None = None) -> dict[str, Any]:
    """下载一张图（已存在则复用），可选立即设为壁纸，返回记录字典。"""
    iid = str(item.get("id") or "")
    if not iid:
        raise WallpaperError("这条结果缺少 id，无法下载")

    resolution = str(item.get("resolution") or "unknown")
    url = str(item.get("path") or "")
    if not url:
        raise WallpaperError("这条结果没有原图地址")

    save_dir = cfg.save_dir
    naming = cfg.get("naming") or "keyword_res_id"
    ext = guess_ext(str(item.get("file_type") or ""), url)
    dest, subdir, filename = target_path(save_dir, keyword, resolution, iid, ext, naming)

    # 去重：同一张图已经有本地文件就直接复用
    existing = store.existing_file(iid)
    if existing:
        dest = existing
        subdir = dest.parent.name
        filename = dest.name
        if on_stage:
            on_stage(f"已存在，直接复用：{filename}")
    else:
        if on_stage:
            on_stage(f"正在下载 {filename} …")
        downloader.download_to_file(url, dest, on_progress=on_progress)

    if apply:
        if on_stage:
            on_stage("正在设为桌面壁纸 …")
        set_wallpaper(dest, cfg.get("fill_mode") or "fill")

    record = {
        "id": iid,
        "keyword": keyword,
        "subdir": subdir,
        "filename": filename,
        "path": str(dest),
        "url": url,                    # 原图直链，本地文件被删后靠它重新下载
        "resolution": resolution,
        "file_size": _safe_size(dest) or int(item.get("file_size") or 0),
        "page_url": str(item.get("url") or ""),
        "thumb_url": str(((item.get("thumbs") or {}).get("large"))
                         or ((item.get("thumbs") or {}).get("small")) or ""),
        "category": str(item.get("category") or ""),
        "favorites": int(item.get("favorites") or 0),
        "colors": item.get("colors") or [],
    }
    store.upsert(record)
    log.info("下载并处理完成：%s（%s）", dest, "已设为壁纸" if apply else "仅下载")
    return record


# --------------------------------------------------------------------- 随机换一张
def random_wallpaper(cfg: Config | None = None, *,
                     client: WallhavenClient | None = None,
                     downloader: Downloader | None = None,
                     store: DownloadStore | None = None,
                     on_stage: ProgressCb | None = None) -> dict[str, Any]:
    """抓一张随机壁纸并设为桌面。读设置页的默认配置。"""
    cfg = cfg or get_config()
    client = client or WallhavenClient(cfg)
    downloader = downloader or Downloader(cfg)
    store = store or get_store()

    keyword, source, pool_size = pick_keyword(cfg)
    if on_stage:
        if source == "fallback":
            on_stage(f"使用关键词「{keyword}」（默认关键词为空，已回落到内置分类）")
        elif pool_size > 1:
            on_stage(f"从 {pool_size} 个关键词中随机选了「{keyword}」")
        else:
            on_stage(f"使用关键词「{keyword}」")

    atleast = resolve_atleast(cfg)
    last_error: Exception | None = None

    # 多试几页，尽量避开已经下载过的图
    for attempt in range(4):
        page = random.randint(1, 5)
        try:
            result = client.search(
                keyword=keyword, page=page, atleast=atleast,
                ratios=cfg.get("ratio"), sorting=cfg.get("sorting"),
                color=cfg.get("color"),
            )
        except WallpaperError as exc:
            last_error = exc
            log.warning("第 %d 次搜索失败：%s", attempt + 1, exc)
            continue

        items = result.get("items") or []
        item = pick_random_item(items, store)
        if item is None:
            last_error = NoResult(f"关键词「{keyword}」没有搜到结果")
            continue

        try:
            return download_wallpaper(item, keyword, cfg, downloader, store,
                                      apply=True, on_stage=on_stage)
        except WallpaperError as exc:
            last_error = exc
            log.warning("下载失败，换一张重试：%s", exc)
            continue

    if isinstance(last_error, WallpaperError):
        raise last_error
    raise WallpaperError("随机换壁纸失败，重试多次仍未成功")


def delete_wallpaper(record: dict[str, Any], cfg: Config) -> None:
    """删除本地文件并清理空目录（历史面板用）。"""
    path = Path(str(record.get("path") or ""))
    if path.is_file():
        path.unlink()
    cleanup_empty_dirs(cfg.save_dir, path.parent)


# --------------------------------------------------------------------- 迁移保存目录
def _path_fields(dest: Path) -> dict[str, Any]:
    return {"path": str(dest), "subdir": dest.parent.name, "filename": dest.name}


def _unique_target(dest: Path) -> Path:
    """目标已存在时换个带序号的名字，避免覆盖别人的文件。"""
    if not dest.exists():
        return dest
    for i in range(1, 1000):
        cand = dest.with_name(f"{dest.stem}_{i}{dest.suffix}")
        if not cand.exists():
            return cand
    raise OSError(f"目标目录里同名文件太多：{dest.name}")


def migrate_wallpapers(old_dir: Path, new_dir: Path, store: DownloadStore,
                       on_progress: ProgressCb | None = None) -> dict[str, Any]:
    """把已下载的壁纸从旧保存目录搬到新目录，并同步历史记录里的路径。

    只处理历史记录里、且文件确实还在的那些。源文件已经不在的记录原样跳过
    （那是「重新下载」的活）。返回统计字典。
    """
    old_dir = Path(old_dir)
    new_dir = Path(new_dir)
    stats: dict[str, Any] = {
        "total": 0, "moved": 0, "already": 0, "renamed": 0,
        "missing": 0, "outside": 0, "failed": 0, "errors": [],
    }

    records = store.all()
    stats["total"] = len(records)
    old_parents: set[Path] = set()
    touched = False

    for idx, rec in enumerate(records, 1):
        iid = str(rec.get("id") or "")
        src = Path(str(rec.get("path") or ""))
        if not src.is_file():
            stats["missing"] += 1
            continue
        try:
            rel = src.relative_to(old_dir)
        except ValueError:
            # 不在旧目录下（用户自己挪过位置），不擅自搬
            stats["outside"] += 1
            continue

        if on_progress and (idx == 1 or idx % 5 == 0 or idx == len(records)):
            on_progress(f"正在迁移 {idx}/{len(records)}：{src.name}")

        dest = new_dir / rel
        try:
            if dest.is_file() and dest.stat().st_size == src.stat().st_size:
                # 目标目录已经有同一份了（比如用户先手动拷过），只需改记录
                stats["already"] += 1
                store.update_fields(iid, _path_fields(dest), save=False)
                touched = True
                continue

            if dest.exists():
                dest = _unique_target(dest)
                stats["renamed"] += 1

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            stats["moved"] += 1
            store.update_fields(iid, _path_fields(dest), save=False)
            touched = True
            old_parents.add(src.parent)
        except OSError as exc:
            stats["failed"] += 1
            stats["errors"].append(f"{src.name}: {exc}")
            log.warning("迁移失败 %s：%s", src, exc)

    if touched:
        store.save()

    # 旧目录里搬空的子目录顺手清掉（cleanup_empty_dirs 只肯删 root 底下的目录）
    for parent in old_parents:
        cleanup_empty_dirs(old_dir, parent)

    log.info("保存目录迁移完成：%s", {k: v for k, v in stats.items() if k != "errors"})
    return stats


# --------------------------------------------------------------------- 重新下载
def _record_source_url(rec: dict[str, Any], client: WallhavenClient) -> str:
    """拿到重新下载要用的原图直链。老记录没存 url 就回查一次 API。"""
    url = str(rec.get("url") or "").strip()
    if url:
        return url
    iid = str(rec.get("id") or "").strip()
    if not iid:
        raise WallpaperError("这条记录没有 id，无法重新下载")
    payload = client.fetch_json(f"{WALLPAPER_API}/{iid}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ParseError("wallhaven 返回的数据结构不符合预期（data 不是对象）")
    url = str(data.get("path") or "").strip()
    if not url:
        raise WallpaperError(f"wallhaven 没有给出 {iid} 的原图地址")
    return url


def redownload_wallpaper(rec: dict[str, Any], cfg: Config,
                         client: WallhavenClient, downloader: Downloader,
                         store: DownloadStore, *,
                         on_progress: BytesCb | None = None,
                         on_stage: ProgressCb | None = None) -> dict[str, Any]:
    """历史记录里的本地文件被删了，重新下载回来。

    优先放回原来那个位置；原来的目录已经不可用（比如盘符没了）就按当前
    保存目录 + 命名规则重新算一个路径。记录里的 url 缺失时回查一次 API。
    """
    iid = str(rec.get("id") or "")
    if not iid:
        raise WallpaperError("这条记录没有 id，无法重新下载")

    if on_stage:
        on_stage(f"正在获取 {iid} 的原图地址 …")
    url = _record_source_url(rec, client)

    old_path = Path(str(rec.get("path") or ""))
    dest = old_path
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 原目录建不出来（盘符没了 / 没权限），退回当前保存目录
        resolution = str(rec.get("resolution") or "unknown")
        naming = cfg.get("naming") or "keyword_res_id"
        dest, _sub, _name = target_path(
            cfg.save_dir, str(rec.get("keyword") or ""), resolution, iid,
            guess_ext(str(rec.get("file_type") or ""), url), naming,
        )
        if on_stage:
            on_stage(f"原目录不可用，改存到 {dest.parent}")

    if on_stage:
        on_stage(f"正在重新下载 {dest.name} …")
    downloader.download_to_file(url, dest, on_progress=on_progress)

    fields = dict(_path_fields(dest))
    fields["url"] = url
    fields["file_size"] = _safe_size(dest)
    store.update_fields(iid, fields)
    log.info("重新下载完成：%s", dest)
    return store.get(iid) or {**rec, **fields}


# --------------------------------------------------------------------- 小工具
def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
