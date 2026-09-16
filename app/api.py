"""wallhaven API 客户端：错误分类、指数退避重试、限流保护、代理自动探测。"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import requests

from .config import Config, get_config
from .logsetup import get_logger

log = get_logger("api")

API_BASE = "https://wallhaven.cc/api/v1/search"
WALLPAPER_API = "https://wallhaven.cc/api/v1/w"   # 按 id 查单张（重新下载时用）
CONNECT_TIMEOUT = 8            # 建连超时（秒）。国内直连 wallhaven 会卡在 DNS/建连上，
                               # 给 15 秒会让"网络不通"这种场景等 60 秒以上，体验很糟
READ_TIMEOUT = 20              # 读超时（秒）
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
MAX_RETRY = 3                  # 连接类错误重试次数
BACKOFF = (1, 2, 4)            # 指数退避秒数
RATE_LIMIT_FALLBACK_WAIT = 10  # 429 且没给 Retry-After 时的等待秒数

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------- 错误类型
class WallpaperError(Exception):
    """所有业务异常的基类。message 是给人看的中文说明。"""

    kind = "error"
    title = "出错了"

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"{self.message}" + (f" | {self.detail}" if self.detail else "")


class NetworkUnreachable(WallpaperError):
    kind = "network"
    title = "网络不可达"


class RequestTimeout(WallpaperError):
    kind = "timeout"
    title = "请求超时"


class RateLimited(WallpaperError):
    kind = "ratelimit"
    title = "请求过于频繁"


class NotFound(WallpaperError):
    kind = "notfound"
    title = "资源不存在"


class AuthError(WallpaperError):
    kind = "auth"
    title = "鉴权失败"


class ServerError(WallpaperError):
    kind = "server"
    title = "服务端错误"


class ParseError(WallpaperError):
    kind = "parse"
    title = "返回内容无法解析"


class DiskError(WallpaperError):
    kind = "disk"
    title = "写入文件失败"


class NoResult(WallpaperError):
    kind = "empty"
    title = "没有搜索结果"


# --------------------------------------------------------------------- 代理
def system_proxy() -> str | None:
    """读 Windows 系统代理（Clash 等「系统代理」模式写的是这里，不是环境变量）。"""
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except (OSError, ImportError, FileNotFoundError):
        return None

    if not server:
        return None
    server = str(server).strip()
    # 形如 "http=127.0.0.1:7890;https=127.0.0.1:7890" 或 "127.0.0.1:7890"
    if "=" in server:
        parts = {}
        for chunk in server.split(";"):
            if "=" in chunk:
                scheme, addr = chunk.split("=", 1)
                parts[scheme.strip().lower()] = addr.strip()
        server = parts.get("https") or parts.get("http") or ""
    if not server:
        return None
    if not server.startswith(("http://", "https://", "socks")):
        server = "http://" + server
    return server or None


def resolve_proxies(cfg: Config) -> dict[str, str] | None:
    """根据配置决定要不要走代理：auto 跟随系统 / off 直连 / manual 手动地址。"""
    mode = cfg.get("proxy_mode") or "auto"
    if mode == "off":
        return None
    if mode == "manual":
        url = (cfg.get("proxy_url") or "").strip()
        if not url:
            return None
        if not url.startswith(("http://", "https://", "socks")):
            url = "http://" + url
        return {"http": url, "https": url}
    # auto
    auto = system_proxy()
    if not auto:
        return None
    return {"http": auto, "https": auto}


# --------------------------------------------------------------------- 客户端
class WallhavenClient:
    """带重试与错误分类的 wallhaven 搜索客户端。"""

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or get_config()
        self.session = requests.Session()
        # 关掉环境变量推断：代理完全由我们的设置决定，避免被外部 HTTP_PROXY 之类的变量带偏
        self.session.trust_env = False
        self.session.headers.update({
            "User-Agent": USER_AGENT,          # 不带 UA 会被 wallhaven 403
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://wallhaven.cc/",
        })
        self._last_request_at = 0.0
        self._min_interval = 0.6               # 本地限流：最快 0.6s 一次，远低于 45 次/分钟

    # -------------------------------------------------------------- 对外
    def search(
        self,
        keyword: str = "",
        page: int = 1,
        *,
        categories: str | None = None,
        atleast: str | None = None,
        ratios: str | None = None,
        sorting: str | None = None,
        order: str | None = None,
        color: str | None = None,
        seed: str | None = None,
        nsfw: bool | None = None,
        api_key: str | None = None,
        on_wait: Callable[[float, str], None] | None = None,
    ) -> dict[str, Any]:
        """执行一次搜索，返回 {"items": [...], "meta": {...}}。"""
        params: dict[str, Any] = {"page": max(1, int(page))}

        kw = (keyword or "").strip()
        if kw:
            params["q"] = kw

        params["categories"] = categories or self.cfg.get("category") or "111"

        use_nsfw = self.cfg.get("nsfw") if nsfw is None else nsfw
        params["purity"] = "111" if use_nsfw else "100"

        res = atleast if atleast is not None else self.cfg.get("resolution")
        if res and res != "auto":
            params["atleast"] = res

        rat = ratios if ratios is not None else self.cfg.get("ratio")
        if rat:
            params["ratios"] = rat

        params["sorting"] = sorting or self.cfg.get("sorting") or "favorites"
        params["order"] = order or self.cfg.get("order") or "desc"

        col = color if color is not None else self.cfg.get("color")
        if col:
            params["colors"] = str(col).lstrip("#")

        if params["sorting"] == "random":
            if seed:
                params["seed"] = seed

        key = api_key if api_key is not None else self.cfg.get("api_key")
        if key:
            params["apikey"] = str(key).strip()

        payload = self._get_json(params, on_wait=on_wait)
        data = payload.get("data")
        meta = payload.get("meta") or {}
        if not isinstance(data, list):
            raise ParseError("wallhaven 返回的数据结构不符合预期（data 不是列表）",
                             detail=str(payload)[:300])
        return {"items": data, "meta": meta}

    def fetch_json(self, url: str, params: dict[str, Any] | None = None,
                   on_wait: Callable[[float, str], None] | None = None) -> dict[str, Any]:
        return self._get_json(params or {}, url=url, on_wait=on_wait)

    # -------------------------------------------------------------- 内部
    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request_at
        if gap < self._min_interval:
            time.sleep(self._min_interval - gap)

    def _get_json(self, params: dict[str, Any], url: str = API_BASE,
                  on_wait: Callable[[float, str], None] | None = None) -> dict[str, Any]:
        proxies = resolve_proxies(self.cfg)
        last_error: WallpaperError | None = None

        for attempt in range(MAX_RETRY + 1):
            if attempt > 0:
                wait = BACKOFF[min(attempt - 1, len(BACKOFF) - 1)]
                if on_wait:
                    on_wait(wait, last_error.message if last_error else "")
                log.info("第 %d 次重试，等待 %ss（上一次：%s）", attempt, wait, last_error)
                time.sleep(wait)

            self._throttle()
            try:
                resp = self.session.get(
                    url, params=params, timeout=REQUEST_TIMEOUT, proxies=proxies,
                )
                self._last_request_at = time.monotonic()
            except requests.exceptions.ProxyError as exc:
                raise NetworkUnreachable(
                    "代理服务器连接失败。请检查 Clash 等代理软件是否在运行，"
                    "或在「设置 → 网络代理」里改为「不使用代理」。",
                    detail=str(exc),
                ) from exc
            except requests.exceptions.SSLError as exc:
                raise NetworkUnreachable(
                    "HTTPS 证书校验失败，通常是代理软件拦截导致。"
                    "请尝试在「设置 → 网络代理」里切换模式。",
                    detail=str(exc),
                ) from exc
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout) as exc:
                last_error = RequestTimeout(
                    f"连接 wallhaven 超时（建连 {CONNECT_TIMEOUT} 秒 / 读取 {READ_TIMEOUT} 秒无响应）",
                    detail=str(exc),
                )
            except requests.exceptions.ConnectionError as exc:
                last_error = NetworkUnreachable(
                    "无法连接到 wallhaven.cc。可能是网络不通、DNS 解析失败，"
                    "或需要开启代理（wallhaven 在国内通常需要代理才能访问）。",
                    detail=str(exc),
                )
            except requests.exceptions.RequestException as exc:
                raise NetworkUnreachable(f"请求失败：{exc}", detail=str(exc)) from exc
            except OSError as exc:
                # 注意顺序：requests 的 RequestException 本身就是 OSError 的子类，
                # 所以这一支必须放在它后面。这里接的是 certifi 证书缺失之类的裸 OSError。
                raise NetworkUnreachable(
                    "HTTPS 证书校验失败，无法建立安全连接。"
                    "若这是打包版程序，说明打包时漏掉了 certifi 的证书文件，请重新打包。",
                    detail=str(exc),
                ) from exc
            else:
                status = resp.status_code

                if status == 200:
                    try:
                        payload = resp.json()
                    except ValueError as exc:
                        raise ParseError("服务端返回的不是合法 JSON（可能被代理或防火墙劫持）",
                                         detail=resp.text[:300]) from exc
                    if not isinstance(payload, dict):
                        raise ParseError("服务端返回的 JSON 顶层不是对象",
                                         detail=str(payload)[:300])
                    if "error" in payload:
                        raise AuthError(f"wallhaven 拒绝请求：{payload['error']}")
                    return payload

                if status == 404:
                    raise NotFound("请求的资源不存在（HTTP 404）")
                if status in (401, 403):
                    if status == 401:
                        raise AuthError(
                            "API Key 无效或已过期。请到 wallhaven.cc 的账号设置里重新获取。",
                            detail=resp.text[:200],
                        )
                    raise AuthError(
                        "被 wallhaven 拒绝（HTTP 403）。可能是触发了风控或缺少浏览器标识，"
                        "稍后重试；若持续出现请检查代理设置。",
                        detail=resp.text[:200],
                    )
                if status == 429:
                    retry_after = _retry_after_seconds(resp)
                    last_error = RateLimited(
                        f"请求过于频繁被限流（HTTP 429），{retry_after} 秒后自动重试。"
                    )
                    if on_wait:
                        on_wait(retry_after, last_error.message)
                    time.sleep(retry_after)
                    continue
                if 500 <= status < 600:
                    last_error = ServerError(f"wallhaven 服务端错误（HTTP {status}）")
                else:
                    raise WallpaperError(f"wallhaven 返回了未预期的状态码 HTTP {status}",
                                         detail=resp.text[:200])

        raise last_error or WallpaperError("请求失败，且没有可用的错误信息")


def _retry_after_seconds(resp: requests.Response) -> int:
    raw = resp.headers.get("Retry-After")
    if raw:
        try:
            return max(1, min(60, int(float(raw))))
        except (TypeError, ValueError):
            pass
    return RATE_LIMIT_FALLBACK_WAIT


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc
    except ValueError:
        return url
