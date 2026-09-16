"""壁纸文件命名：按关键词分子目录，文件名由命名方式决定。"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import NAMING_MODES

# Windows 文件名非法字符 + 控制字符
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_SEGMENT = 30
FALLBACK_KEYWORD = "wallpaper"


def clean_segment(text: str, max_len: int = _MAX_SEGMENT) -> str:
    """把任意关键词清洗成安全的文件名/目录名片段。

    - 空白转下划线
    - 剔除 Windows 非法字符与控制字符
    - 去掉首尾的点、空格、下划线
    - 截断到 max_len；结果为空则用 wallpaper 兜底
    """
    if not text:
        return FALLBACK_KEYWORD
    s = _ILLEGAL.sub("", str(text))
    s = re.sub(r"\s+", "_", s.strip())
    s = re.sub(r"_+", "_", s)
    s = s.strip("._ ")
    if not s:
        return FALLBACK_KEYWORD
    if len(s) > max_len:
        s = s[:max_len].rstrip("._ ")
    # Windows 保留设备名
    if s.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        s = "_" + s
    return s or FALLBACK_KEYWORD


def primary_keyword(keyword: str) -> str:
    """保留完整关键词（含空格），供命名使用。

    早先的规则是「多词只取第一个词」，但内置分类里就有 `digital art`、
    `Tom Hiddleston` 这类词组，取首词会退化成 `digital` / `Tom`，
    既丢信息又容易撞名。改成保留整串、由 clean_segment 统一清洗和截断。
    """
    return (keyword or "").strip()


def build_subdir(keyword: str) -> str:
    """子目录名 = 清洗后的关键词。"""
    return clean_segment(primary_keyword(keyword))


def build_filename(
    keyword: str,
    resolution: str,
    image_id: str,
    ext: str,
    naming: str = "keyword_res_id",
    when: datetime | None = None,
) -> str:
    """按命名方式拼出文件名（不含目录）。"""
    if naming not in NAMING_MODES:
        naming = "keyword_res_id"

    ext = ext if ext.startswith(".") else ("." + ext if ext else ".jpg")
    kw = clean_segment(primary_keyword(keyword))
    res = clean_segment(resolution or "unknown", 20)
    iid = clean_segment(image_id or "noid", 32)

    if naming == "id":
        stem = iid
    elif naming == "date_keyword_res_id":
        stamp = (when or datetime.now()).strftime("%Y%m%d")
        stem = f"{stamp}_{kw}_{res}_{iid}"
    else:
        stem = f"{kw}_{res}_{iid}"

    # 整体长度兜底，避免超过 260 字符的路径限制
    if len(stem) > 120:
        stem = stem[:120].rstrip("._ ")
    return stem + ext


def guess_ext(file_type: str, url: str) -> str:
    """优先用 API 的 file_type，其次从 URL 推断，最后退回 .jpg。"""
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
    }
    ft = (file_type or "").strip().lower()
    if ft in mapping:
        return mapping[ft]
    suffix = Path((url or "").split("?")[0]).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def target_path(save_dir: Path, keyword: str, resolution: str, image_id: str,
                ext: str, naming: str) -> tuple[Path, str, str]:
    """返回 (完整路径, 子目录名, 文件名)。"""
    subdir = build_subdir(keyword)
    filename = build_filename(keyword, resolution, image_id, ext, naming)
    return Path(save_dir) / subdir / filename, subdir, filename
