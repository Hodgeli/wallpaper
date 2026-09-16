"""测试用的假客户端 / 假下载器（`feature_test.py` 与 `unit_test.py` 共用）。

**为什么需要**：真联网的用例在离线环境（或 CI）里只能整段跳过，而「重新下载」
恰好是全项目分支最多的一条路径——有 url / 无 url 回查 / 原目录不可用 /
回查失败 / 下载失败。最该测的地方反而没覆盖，说不过去。

换掉真实现不需要改产品代码：`redownload_wallpaper` 本来就把 client / downloader
当参数收，UI 层也只是 `app.client` / `app.downloader` 两个普通属性。

**假实现的签名照着真实现抄，故意不写 `**kwargs` 吞参数。** 真实现改了签名而这里
没跟上时会直接 `TypeError` 炸出来，比"静默不覆盖"好得多。
"""
from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------- 造数据
def make_item(iid: str = "abc12345", *, resolution: str = "3840x2160",
              file_size: int = 1_234_567, file_type: str = "image/jpeg",
              url: str | None = None, ext: str = "jpg", category: str = "nature",
              colors: list[str] | None = None,
              favorites: int = 100) -> dict[str, Any]:
    """造一条 wallhaven 搜索结果，字段照官方 API 的形状来。

    `url=None` 表示用派生的假直链；想看"记录里没 url"的分支就别往 store 里塞 url。
    `colors=[]` 表示"这张图没有配色"（跟 `None` 不同——`None` 是"没指定，给个默认的"）。
    """
    prefix = iid[:2]
    try:
        width, height = (int(v) for v in resolution.split("x")[:2])
    except ValueError:      # resolution 传了 "auto" 之类的东西，别在这里炸
        width = height = 0
    return {
        "id": iid,
        "url": f"https://wallhaven.cc/w/{iid}",
        "short_url": f"https://whvn.cc/{iid}",
        "views": 1000,
        "favorites": favorites,
        "source": "",
        "purity": "sfw",
        "category": category,
        "dimension_x": width,
        "dimension_y": height,
        "resolution": resolution,
        "ratio": "16x9",
        "file_size": file_size,
        "file_type": file_type,
        "created_at": "2026-01-01 00:00:00",
        "colors": list(colors) if colors is not None else ["#1a2b3c", "#ffffff"],
        "path": url or f"https://w.wallhaven.cc/full/{prefix}/wallhaven-{iid}.{ext}",
        "thumbs": {
            "large": f"https://th.wallhaven.cc/lg/{prefix}/{iid}.jpg",
            "original": f"https://th.wallhaven.cc/orig/{prefix}/{iid}.jpg",
            "small": f"https://th.wallhaven.cc/small/{prefix}/{iid}.jpg",
        },
    }


def jpeg_bytes(width: int = 64, height: int = 64, color=(20, 40, 60),
               quality: int = 80) -> bytes:
    """一段**真的能解码**的 JPEG。

    别用 `b"fake"` 之类的占位字节：有些路径会真去 `Image.open`，
    假字节会抛 UnidentifiedImageError，测试就变成在测"假数据坏得对不对"了。
    """
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def png_bytes(width: int = 64, height: int = 64, color=(200, 10, 10)) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------- 假客户端
class FakeClient:
    """假的 `WallhavenClient`。

    只实现被调用到的两个方法。没配 `single` 却有人调 `fetch_json` 时直接
    AssertionError——这类"测试没预料到的调用"应该吵出来，不该静默返回空。
    """

    def __init__(self, items: list[dict[str, Any]] | None = None, *,
                 single: dict[str, Any] | None = None,
                 search_error: Exception | None = None,
                 fetch_error: Exception | None = None) -> None:
        self.items = [dict(it) for it in (items or [])]
        self.single = dict(single) if single is not None else None
        self.search_error = search_error
        self.fetch_error = fetch_error
        self.search_calls: list[dict[str, Any]] = []
        self.fetch_calls: list[str] = []

    def search(self, keyword: str = "", page: int = 1, *, categories: str | None = None,
               atleast: str | None = None, ratios: str | None = None,
               sorting: str | None = None, order: str | None = None,
               color: str | None = None, seed: str | None = None,
               nsfw: bool | None = None, api_key: str | None = None,
               on_wait: Callable[[float, str], None] | None = None) -> dict[str, Any]:
        self.search_calls.append({
            "keyword": keyword, "page": page, "categories": categories,
            "atleast": atleast, "ratios": ratios, "sorting": sorting,
            "order": order, "color": color, "seed": seed, "nsfw": nsfw,
        })
        if self.search_error is not None:
            raise self.search_error
        return {
            "items": [dict(it) for it in self.items],
            "meta": {"current_page": page, "last_page": 1,
                     "per_page": len(self.items), "total": len(self.items)},
        }

    def fetch_json(self, url: str, params: dict[str, Any] | None = None,
                   timeout: Any = None) -> dict[str, Any]:
        self.fetch_calls.append(url)
        if self.fetch_error is not None:
            raise self.fetch_error
        if self.single is None:
            raise AssertionError(
                f"FakeClient 没配 single，却被要求 fetch_json({url})——"
                "要么用例该给 single，要么被测代码不该走回查分支")
        return {"data": dict(self.single)}


# ------------------------------------------------------------------- 假下载器
class FakeDownloader:
    """假的 `Downloader`。不碰网络，往目标路径写固定字节。"""

    def __init__(self, payload: bytes | None = None, *,
                 error: Exception | None = None) -> None:
        self.payload = payload if payload is not None else jpeg_bytes()
        self.error = error
        self.file_calls: list[tuple[str, Path]] = []
        self.byte_calls: list[str] = []

    def download_to_file(self, url: str, dest: Path,
                         on_progress: Callable[[int, int], None] | None = None,
                         timeout: int = 60) -> Path:
        dest = Path(dest)
        self.file_calls.append((url, dest))
        if self.error is not None:
            raise self.error
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.payload)
        if on_progress:
            on_progress(len(self.payload), len(self.payload))
        return dest

    def get_bytes(self, url: str, timeout: Any = None, *,
                  should_abort: Callable[[], bool] | None = None) -> bytes:
        self.byte_calls.append(url)
        # 开连接前先问一次，跟真实现的顺序保持一致
        if should_abort is not None and should_abort():
            from app.images import DownloadAborted
            raise DownloadAborted()
        if self.error is not None:
            raise self.error
        return self.payload


@contextlib.contextmanager
def use_fakes(app: Any, client: FakeClient | None = None,
              downloader: FakeDownloader | None = None
              ) -> Iterator[tuple[FakeClient | None, FakeDownloader | None]]:
    """临时把 UI 层的真客户端换成假的，退出时还原。

    `app.client` / `app.downloader` 是普通属性，`history_tab` 通过
    `self.app.client` 取用，所以这里换掉就够了，不用 patch 任何方法。
    """
    old = (app.client, app.downloader)
    if client is not None:
        app.client = client
    if downloader is not None:
        app.downloader = downloader
    try:
        yield client, downloader
    finally:
        app.client, app.downloader = old
