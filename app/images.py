"""缩略图磁盘缓存 + 原图下载。网络部分跑在线程池里，UI 侧只负责取结果。"""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

from .api import (
    REQUEST_TIMEOUT,
    USER_AGENT,
    DiskError,
    NetworkUnreachable,
    RequestTimeout,
    WallpaperError,
    resolve_proxies,
)
from .config import THUMB_CACHE_DIR, Config, as_int, ensure_dirs, get_config
from .logsetup import get_logger

log = get_logger("images")

THUMB_WORKERS = 6        # 缩略图并发
ORIGINAL_WORKERS = 3     # 原图并发（大文件，别太贪）
CHUNK = 64 * 1024
EVICT_TARGET_RATIO = 0.9  # 清理到上限的 90%


class DownloadAborted(Exception):
    """调用方主动放弃的下载。

    故意**不继承** `WallpaperError`：它不是错误，不该出现在状态栏或弹窗里，
    也不该被 `report_error` 当成故障上报。上层要么静默丢掉，要么只记 debug 日志。
    """


def _check_status(resp: requests.Response, url: str) -> None:
    if resp.status_code != 200:
        raise WallpaperError(f"下载图片失败：HTTP {resp.status_code}（{_short(url)}）")


class Downloader:
    """带代理、UA、错误分类的下载器（线程安全，内部用 Session 池）。"""

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or get_config()
        self._local = threading.local()

    # -------------------------------------------------------------- 内部
    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.trust_env = False
            s.headers.update({
                "User-Agent": USER_AGENT,
                "Referer": "https://wallhaven.cc/",
                "Accept": "image/avif,image/webp,image/jpeg,image/png,*/*;q=0.8",
            })
            self._local.session = s
        return s

    def _proxies(self) -> dict[str, str] | None:
        return resolve_proxies(self.cfg)

    # -------------------------------------------------------------- 对外
    def get_bytes(self, url: str, timeout: int | tuple[int, int] = REQUEST_TIMEOUT,
                  *, should_abort: Callable[[], bool] | None = None) -> bytes:
        """下载到内存，失败抛分类好的 WallpaperError。

        `should_abort` 给了就改用流式下载，每读一块问一次"还要不要"，返回 True
        立刻抛 `DownloadAborted` 放弃。原图预览靠它避免用户已经切走了、还在傻下
        几十 MB（见 `search_tab._fetch_full_image`）。
        """
        # 开连接之前先问一次：任务可能在队列里排了一会儿，用户早就切走了
        if should_abort is not None and should_abort():
            raise DownloadAborted()
        try:
            if should_abort is None:
                resp = self.session.get(url, timeout=timeout, proxies=self._proxies())
                _check_status(resp, url)
                return resp.content
            with self.session.get(url, stream=True, timeout=timeout,
                                  proxies=self._proxies()) as resp:
                _check_status(resp, url)
                buf = bytearray()
                for chunk in resp.iter_content(CHUNK):
                    if not chunk:
                        continue
                    if should_abort():
                        raise DownloadAborted()
                    buf += chunk
                return bytes(buf)
        except DownloadAborted:
            raise                      # 主动放弃，不是网络错误，别被下面的分支包装
        except requests.exceptions.ProxyError as exc:
            raise NetworkUnreachable("代理不可用，请检查代理软件或在设置里切换代理模式",
                                     detail=str(exc)) from exc
        except requests.exceptions.SSLError as exc:
            raise NetworkUnreachable("HTTPS 证书校验失败（通常是代理拦截）",
                                     detail=str(exc)) from exc
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as exc:
            raise RequestTimeout(f"下载超时：{_short(url)}", detail=str(exc)) from exc
        except requests.exceptions.ConnectionError as exc:
            raise NetworkUnreachable(f"下载失败，网络不可达：{_short(url)}",
                                     detail=str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise NetworkUnreachable(f"下载失败：{exc}", detail=str(exc)) from exc
        except OSError as exc:
            # RequestException 是 OSError 的子类，这一支必须排在它后面
            raise NetworkUnreachable(
                "HTTPS 证书校验失败，无法建立安全连接。"
                "若这是打包版程序，说明打包时漏掉了 certifi 的证书文件，请重新打包。",
                detail=str(exc),
            ) from exc

    def download_to_file(
        self,
        url: str,
        dest: Path,
        on_progress: Callable[[int, int], None] | None = None,
        timeout: int = 60,
    ) -> Path:
        """流式下载到文件：先写 `.part`，下完再原子改名。

        **中断不会丢掉已经下到的部分。** `.part` 留在原地，下次对同一个目标文件
        再来一遍时带上 `Range` 从断点接着下。wallhaven 的原图常有 10–30MB，
        "每次从 0 开始"在弱网下代价太大——下到 95% 断一次就要全部重来。

        `.part` 的文件名里带了 URL 指纹（`xxx.jpg.<8位十六进制>.part`），所以换了
        下载地址绝不会把两段不同的数据拼成一个坏文件；同一个目标下别的地址留下的
        `.part` 会被顺手清掉，不会越积越多。
        """
        dest = Path(dest)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DiskError(f"无法创建目录：{dest.parent}（{exc.strerror or exc}）") from exc

        if not _is_writable(dest.parent):
            raise DiskError(f"目录没有写入权限：{dest.parent}")

        part = _part_path(dest, url)
        _prune_stale_parts(dest, keep=part)

        try:
            self._fetch_to_part(url, part, on_progress, timeout)
            if part.stat().st_size == 0:
                raise WallpaperError("下载到的文件是空的")
            os.replace(part, dest)
            return dest
        except DiskError as exc:
            # 写盘出错多半是"没空间了"，留着那半截只会占地方
            _safe_unlink(part)
            raise DiskError(f"写入文件失败：{dest}（{exc.strerror or exc}）") from exc
        except requests.exceptions.RequestException as exc:
            # **保留 .part**：网络抖一下而已，下次带上 Range 接着下。
            # 这一支必须排在 OSError 之前——RequestException 本身就是 OSError 的子类，
            # 顺序反了会把网络错误误报成"写入文件失败"。
            raise NetworkUnreachable(f"下载中断：{exc}", detail=str(exc)) from exc
        except OSError as exc:
            _safe_unlink(part)
            raise DiskError(f"写入文件失败：{dest}（{exc.strerror or exc}）") from exc
        # WallpaperError（含 HTTP 状态异常）往上抛，.part 留着——
        # 5xx 之类的服务端故障是暂时的，等它好了还能接着下。

    def _fetch_to_part(
        self,
        url: str,
        part: Path,
        on_progress: Callable[[int, int], None] | None,
        timeout: int,
    ) -> None:
        """把 `url` 的内容写进 `part`，能续就续。

        最多两趟：第一趟带 `Range`；服务器说"这个范围不认"（416）就清掉重来一趟，
        第二趟不带 Range。只重来一次，避免和服务器来回扯皮。
        """
        for restart in (False, True):
            start = part.stat().st_size if part.is_file() else 0
            headers = {"Range": f"bytes={start}-"} if start else {}
            with self.session.get(url, stream=True, timeout=timeout,
                                  headers=headers, proxies=self._proxies()) as resp:
                if resp.status_code == 416:
                    # 我们手里的 .part 不比资源短。常见的原因是"上次其实已经下完了，
                    # 只差最后那步改名就断电了"——用 Content-Range 里的总长度确认。
                    total = _total_from_content_range(resp.headers.get("Content-Range"))
                    if start and total == start:
                        return                      # 已经是完整的，交给上层改名
                    if restart:
                        raise WallpaperError(
                            f"下载原图失败：服务器拒绝了断点续传（{_short(url)}）")
                    _safe_unlink(part)
                    continue                        # 第二趟不带 Range

                if resp.status_code == 206:
                    pass                            # 服务器接受了 Range，从 start 往后写
                elif resp.status_code == 200:
                    # 服务器无视了 Range（或者本来就是第一次下）。从头写，
                    # 不能把新数据追加到旧数据后面。
                    start = 0
                    _safe_unlink(part)
                else:
                    raise WallpaperError(
                        f"下载原图失败：HTTP {resp.status_code}（{_short(url)}）")

                total = _expected_total(resp, start)
                done = start
                if on_progress:
                    on_progress(done, total)
                with open(part, "ab" if start else "wb") as fh:
                    for chunk in resp.iter_content(CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        done += len(chunk)
                        if on_progress:
                            on_progress(done, total)
                return


# --------------------------------------------------------------------- 缩略图缓存
def thumb_cache_path(image_id: str) -> Path:
    return THUMB_CACHE_DIR / f"{image_id}.jpg"


def _deliver(iid: str, path: Path, decode: Callable[[Path], Any] | None,
             on_done: Callable[..., None]) -> None:
    """把一张缩略图交给回调。有 decode 就先在工作线程里解码/缩放。"""
    if decode is None:
        on_done(iid, path, None)
        return
    try:
        on_done(iid, decode(path), None)
    except Exception as exc:  # noqa: BLE001 - 单张解码失败不该拖垮整批
        log.warning("缩略图解码失败 %s：%s", iid, exc)
        on_done(iid, None, exc)


def fetch_thumbs(
    items: Iterable[dict[str, Any]],
    downloader: Downloader,
    on_done: Callable[..., None],
    workers: int = THUMB_WORKERS,
    decode: Callable[[Path], Any] | None = None,
) -> None:
    """并发下载一批缩略图，每张完成就回调一次。

    - 命中磁盘缓存的直接回调，不发网络请求（这种情况连 url 都不需要）；
    - `decode` 不为 None 时，在工作线程里对文件做一次解码/缩放，
      回调拿到的是它的返回值；否则回调拿到的是路径。

    `decode` 存在的意义：解码 + 重采样是纯 CPU 活，放在主线程（UI 事件里）
    会让界面明显卡住——一页 24 张缩略图几乎同时到达时尤其明显。
    调用方应该把 `decode` 传成自己的"打开并缩放"函数。

    注意 `decode` 是在**调用本函数的那个线程**里串行执行的（缩略图线程池只负责
    下载）。这是有意的：一张 640x400 的缩略图解码加缩放只要几毫秒，24 张加起来
    也就 200ms 上下，而界面全程不卡、图是一张张"流"出来的。把解码塞进池子里能
    再快一点，但会让下载线程同时干 CPU 活，收益不值这个复杂度。
    """
    ensure_dirs()
    cached: list[tuple[str, Path]] = []
    todo: list[tuple[str, str, Path]] = []
    for it in items:
        iid = str(it.get("id") or "")
        if not iid:
            continue
        path = thumb_cache_path(iid)
        if path.is_file() and path.stat().st_size > 0:
            cached.append((iid, path))
            continue
        url = ((it.get("thumbs") or {}).get("large")
               or (it.get("thumbs") or {}).get("small") or "")
        if not url:
            continue
        todo.append((iid, url, path))

    if not todo:
        for iid, path in cached:
            _deliver(iid, path, decode, on_done)
        return

    with ThreadPoolExecutor(max_workers=max(1, workers),
                            thread_name_prefix="thumb") as pool:
        futures = {
            pool.submit(_fetch_one_thumb, downloader, iid, url, path): iid
            for iid, url, path in todo
        }
        # 下载已经跑起来了，这时候再解码命中的缓存，两边并行
        for iid, path in cached:
            _deliver(iid, path, decode, on_done)
        for fut in as_completed(futures):
            iid = futures[fut]
            try:
                path = fut.result()
            except Exception as exc:  # noqa: BLE001 - 单张失败不影响整批
                log.warning("缩略图 %s 下载失败：%s", iid, exc)
                on_done(iid, None, exc)
            else:
                _deliver(iid, path, decode, on_done)


def _fetch_one_thumb(downloader: Downloader, image_id: str, url: str, path: Path) -> Path:
    data = downloader.get_bytes(url)
    if not data:
        raise WallpaperError("缩略图内容为空")
    ensure_dirs()
    tmp = path.with_suffix(".jpg.part")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def evict_cache(max_mb: int | None = None) -> int:
    """把缓存压到上限以内，返回删除的文件数。"""
    # 注意不能用 `max_mb or 默认值`：0 是合法上限（清空缓存），会被当成假值
    limit_mb = (as_int(get_config().get("thumb_cache_mb") or 200, 200)
                if max_mb is None else int(max_mb))
    limit = max(0, limit_mb) * 1024 * 1024
    try:
        files = [p for p in THUMB_CACHE_DIR.glob("*.jpg") if p.is_file()]
    except OSError:
        return 0
    total = sum(p.stat().st_size for p in files)
    if total <= limit:
        return 0

    target = int(limit * EVICT_TARGET_RATIO)
    files.sort(key=lambda p: p.stat().st_mtime)
    removed = 0
    for p in files:
        if total <= target:
            break
        try:
            size = p.stat().st_size
            p.unlink()
            total -= size
            removed += 1
        except OSError:
            continue
    if removed:
        log.info("缩略图缓存清理：删除 %d 个文件，剩余 %.1f MB", removed, total / 1024 / 1024)
    return removed


def cache_size_mb() -> float:
    try:
        return sum(p.stat().st_size for p in THUMB_CACHE_DIR.glob("*.jpg")
                   if p.is_file()) / 1024 / 1024
    except OSError:
        return 0.0


def clear_cache() -> None:
    try:
        shutil.rmtree(THUMB_CACHE_DIR, ignore_errors=True)
    except OSError:
        pass
    ensure_dirs()


# --------------------------------------------------------------------- 小工具
def _part_path(dest: Path, url: str) -> Path:
    """下载中的临时文件名，带 URL 指纹。

    **指纹是为了防止把两个不同地址的数据拼在一起**：下载地址变了（换了 CDN、
    或者记录里的 url 被回查改过）时，旧的那半截数据跟新地址对不上，续传拼出来的
    就是个坏文件——而且它长得跟正常文件一样，用户要等设成壁纸才发现。
    带上指纹，旧数据天然是另一个文件名，用不上。
    """
    tag = hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()[:8]
    return dest.with_name(f"{dest.name}.{tag}.part")


def _prune_stale_parts(dest: Path, keep: Path) -> None:
    """清掉同一个目标文件下、别的地址留下的 `.part`。

    它们永远不会再被用上（指纹对不上），只会占地方。只匹配
    `<dest.name>.*.part` 这个形状，不会误伤同目录里的其他文件。
    """
    prefix = f"{dest.name}."
    try:
        entries = list(dest.parent.iterdir())
    except OSError:
        return
    for p in entries:
        if p.name == keep.name or not p.name.startswith(prefix):
            continue
        if p.name.endswith(".part") and p.is_file():
            _safe_unlink(p)


def _total_from_content_range(value: str | None) -> int | None:
    """从 `Content-Range: bytes 100-999/1000` 里取出总长度 1000。

    416 的响应头里总长度写在 `*` 的位置（`bytes */1000`），所以不能用
    `start-end/total` 这种固定格式去切。取不到就返回 None。
    """
    if not value or "/" not in value:
        return None
    tail = value.rsplit("/", 1)[1].strip()
    return int(tail) if tail.isdigit() else None


def _expected_total(resp: requests.Response, start: int) -> int:
    """这次下载完成后，文件最终应该有多大。拿不到就返回 0。

    续传时 `Content-Length` 是**剩余**的字节数，得加上已经下到的 `start`；
    `Content-Range` 里则是完整长度，直接可用。
    """
    if resp.status_code == 206:
        total = _total_from_content_range(resp.headers.get("Content-Range"))
        if total is not None:
            return total
    length = resp.headers.get("Content-Length")
    if length and str(length).isdigit():
        return start + int(length)
    return 0


def _short(url: str, n: int = 60) -> str:
    return url if len(url) <= n else url[:n] + "…"


def _is_writable(folder: Path) -> bool:
    probe = folder / f".wp_write_test_{int(time.time() * 1000)}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def cleanup_empty_dirs(root: Path, subdir: Path) -> bool:
    """删除壁纸后顺手清掉变空的子目录。返回是否删掉了目录。"""
    try:
        root = Path(root).resolve()
        target = Path(subdir).resolve()
        if target == root or root not in target.parents:
            return False          # 只允许删 root 底下的子目录
        if any(target.iterdir()):
            return False
        target.rmdir()
        return True
    except OSError:
        return False
