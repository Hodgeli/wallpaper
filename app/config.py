"""WallpaperPicker —— 应用常量、路径与配置读写。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

APP_NAME = "WallpaperPicker"
APP_DIR_NAME = "auto_wallpaper"
APP_VERSION = "2.0.0"


def _appdata_root() -> Path:
    """配置目录：%APPDATA%\\auto_wallpaper，取不到时退回用户主目录下的隐藏目录。"""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ("." + APP_DIR_NAME)


CONFIG_DIR = _appdata_root()
CONFIG_PATH = CONFIG_DIR / "config.json"
LOG_DIR = CONFIG_DIR / "logs"
THUMB_CACHE_DIR = CONFIG_DIR / "thumbcache"
STORE_PATH = CONFIG_DIR / "downloaded.json"

DEFAULT_SAVE_DIR = Path.home() / "Pictures" / "Wallpapers"

# ---------------------------------------------------------------- 壁纸填充方式
# 对应注册表 HKCU\Control Panel\Desktop 的 WallpaperStyle / TileWallpaper
FILL_MODES: dict[str, tuple[str, str]] = {
    "fill": ("10", "0"),      # 填充：等比放大铺满，裁掉溢出，不变形（Windows 默认）
    "fit": ("6", "0"),        # 适应：完整显示，可能留黑边
    "stretch": ("2", "0"),    # 拉伸：会变形（原脚本的行为）
    "center": ("0", "0"),     # 居中：原始尺寸居中
    "tile": ("0", "1"),       # 平铺
}
FILL_MODE_LABELS = {
    "fill": "填充（推荐）",
    "fit": "适应（可能留黑边）",
    "stretch": "拉伸（会变形）",
    "center": "居中（原始尺寸）",
    "tile": "平铺",
}
FILL_LABEL_TO_KEY = {v: k for k, v in FILL_MODE_LABELS.items()}

# ---------------------------------------------------------------- 界面主题
THEME_MODES = {
    "auto": "自动（跟随系统）",
    "light": "浅色",
    "dark": "深色",
}
THEME_LABEL_TO_KEY = {v: k for k, v in THEME_MODES.items()}

# ---------------------------------------------------------------- 文件命名方式
NAMING_MODES = {
    "keyword_res_id": "关键词 + 分辨率 + ID",
    "date_keyword_res_id": "日期 + 关键词 + 分辨率 + ID",
    "id": "仅 ID",
}
NAMING_LABEL_TO_KEY = {v: k for k, v in NAMING_MODES.items()}

# ---------------------------------------------------------------- 搜索筛选项
CATEGORY_CHOICES = {
    "全部": "111",
    "通用": "100",
    "动漫": "010",
    "人物": "001",
}
CATEGORY_LABEL_TO_KEY = dict(CATEGORY_CHOICES)

RATIO_CHOICES = {
    "16:9 + 16:10（默认）": "16x9,16x10",
    "仅 16:9": "16x9",
    "仅 16:10": "16x10",
    "21:9 带鱼屏": "21x9",
    "不限比例": "",
}
RATIO_LABEL_TO_KEY = dict(RATIO_CHOICES)

SORTING_CHOICES = {
    "收藏数": ("favorites", "desc"),
    "相关度": ("relevance", "desc"),
    "最新": ("date_added", "desc"),
    "浏览量": ("views", "desc"),
    "随机": ("random", "desc"),
    "点赞榜": ("toplist", "desc"),
}
SORTING_LABEL_TO_KEY = dict(SORTING_CHOICES)

RESOLUTION_CHOICES = ["自适应", "1920x1080", "2560x1440", "3840x2160", "2560x1600", "3440x1440", "自定义"]

# 关键词留空时的内置回落分类（沿用原脚本的那份列表，已去掉人物名等过时项）
FALLBACK_KEYWORDS = [
    "nature", "universe", "landscape", "fruit", "animals", "sword", "samurai",
    "digital art", "anime", "artwork", "space", "planet", "spaceship", "futuristic",
    "mountains", "machine", "robot", "cyberpunk", "metal", "cat", "helmet",
    "abstract", "city", "ocean", "forest", "minimalism",
]

# 首屏快捷标签
QUICK_TAGS = ["nature", "cyberpunk", "anime", "space", "mountains", "digital art", "robot", "cat"]

DEFAULT_CONFIG: dict[str, Any] = {
    # 保存位置
    "save_dir": str(DEFAULT_SAVE_DIR),
    # 默认搜索配置：「随机换一张」（含 --random）使用
    # default_keyword 支持逗号分隔的多个关键词，每次随机取一个
    "default_keyword": "",
    "resolution": "auto",          # auto 或 "1920x1080" 这类字面量
    "ratio": "16x9,16x10",
    "sorting": "favorites",
    "order": "desc",
    "category": "111",
    "color": "",                   # 形如 "0066cc"，空表示不筛
    # 界面主题：auto 跟随系统 / light / dark。改完要重启程序才生效
    "theme": "auto",
    # 壁纸
    "fill_mode": "fill",
    "naming": "keyword_res_id",
    # 内容与账号
    "api_key": "",
    "nsfw": False,
    # 网络代理：auto 跟随 Windows 系统代理（Clash 等「系统代理」模式）/ off 直连 / manual 手动
    "proxy_mode": "auto",
    "proxy_url": "",
    # 缓存
    "thumb_cache_mb": 200,
    # 历史页是否按关键词分组
    "history_grouped": False,
    # 窗口几何（首次为 None，由程序决定）
    "window": {"w": 1440, "h": 900, "x": None, "y": None},
    # 界面版本号：用于把老配置里的窗口尺寸一次性升上来
    "ui_rev": 2,
}

# 旧的默认窗口尺寸。老配置里如果是这个值，说明用户没手动调过，可以安全升级。
_LEGACY_WINDOW = (1280, 820)
_CURRENT_WINDOW = (1440, 900)

_lock = threading.RLock()


def ensure_dirs() -> None:
    """确保配置目录、日志目录、缩略图缓存目录存在。"""
    for d in (CONFIG_DIR, LOG_DIR, THUMB_CACHE_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


class Config:
    """JSON 配置。读取时与默认值深合并，写入用原子替换，避免半截文件。"""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = json.loads(json.dumps(DEFAULT_CONFIG))
        self.load()

    # ------------------------------------------------------------ 读写
    def load(self) -> None:
        with _lock:
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # 配置文件损坏时不覆盖它，直接用默认值继续跑
                return
            if isinstance(raw, dict):
                self._data = _deep_merge(json.loads(json.dumps(DEFAULT_CONFIG)), raw)
                self._migrate(raw)

    def _migrate(self, raw: dict[str, Any]) -> None:
        """把老配置升到当前界面版本。

        注意要读**原始**配置里的 ui_rev：_deep_merge 只覆盖 base 里已有的键，
        新加的 ui_rev 会被默认值填上，看 self._data 就永远是新版了。

        只在窗口尺寸**恰好等于旧默认值**时才动它——那说明用户从没手动调过窗口大小，
        可以安全地放大到新默认；用户自己调过的尺寸一律保留。
        """
        try:
            rev = int(raw.get("ui_rev") or 1)
        except (TypeError, ValueError):
            rev = 1
        if rev >= 2:
            return
        win = raw.get("window")
        if not isinstance(win, dict):
            win = {}
        try:
            size = (int(win.get("w") or 0), int(win.get("h") or 0))
        except (TypeError, ValueError):
            size = (0, 0)
        if size == _LEGACY_WINDOW:
            self._data["window"] = {
                "w": _CURRENT_WINDOW[0], "h": _CURRENT_WINDOW[1],
                "x": win.get("x"), "y": win.get("y"),
            }
        self._data["ui_rev"] = DEFAULT_CONFIG["ui_rev"]
        self.save()

    def save(self) -> None:
        with _lock:
            ensure_dirs()
            tmp = self.path.with_suffix(".json.tmp")
            try:
                tmp.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(tmp, self.path)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------ 访问
    def get(self, key: str, default: Any = None) -> Any:
        with _lock:
            return self._data.get(key, DEFAULT_CONFIG.get(key, default))

    def set(self, key: str, value: Any) -> None:
        with _lock:
            self._data[key] = value

    def update(self, **kwargs: Any) -> None:
        with _lock:
            self._data.update(kwargs)

    def as_dict(self) -> dict[str, Any]:
        with _lock:
            return json.loads(json.dumps(self._data))

    def reset(self) -> None:
        with _lock:
            self._data = json.loads(json.dumps(DEFAULT_CONFIG))

    # ------------------------------------------------------------ 派生值
    @property
    def save_dir(self) -> Path:
        return Path(self.get("save_dir") or DEFAULT_SAVE_DIR)

    def window_geometry(self) -> tuple[int, int, int | None, int | None]:
        """窗口几何。任何一项不是数字就退回默认值。

        配置文件是手改得动的：把 w 写成 `"1440px"` 不该让程序起不来。
        （真踩过：原来这里直接 `int(w.get("w"))`，抛出的 ValueError 是在
        `WallpaperPickerApp.__init__` → `_restore_geometry()` 里，没人接，
        程序静默退出——`--noconsole` 的打包版连报错都看不到。）
        """
        w = self.get("window")
        if not isinstance(w, dict):     # 被改成了字符串/数字之类，当没设置
            w = {}
        return (
            as_int(w.get("w"), 1280),
            as_int(w.get("h"), 820),
            as_opt_int(w.get("x")),
            as_opt_int(w.get("y")),
        )


def as_int(value: Any, fallback: int) -> int:
    """能转成 int 就用它，否则用兜底值。不抛异常。

    配置项是"用户手改得动、也可能被别的版本写坏"的，读的时候一律走这里，
    别直接 `int(...)`——`int("1440px")` 抛的 ValueError 如果发生在启动路径上，
    打包版（`--noconsole`）会静默退出，用户只看到"双击没反应"。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def as_opt_int(value: Any) -> int | None:
    """同上，但没值/转不了就回 None（表示"让程序自己决定"）。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """把 override 合并进 base：只覆盖 base 里已有的键，忽略未知键。"""
    for k, v in override.items():
        if k not in base:
            continue
        if isinstance(base[k], dict) and isinstance(v, dict):
            base[k] = _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


_cfg: Config | None = None


def get_config() -> Config:
    """全局单例配置。"""
    global _cfg
    with _lock:
        if _cfg is None:
            _cfg = Config()
        return _cfg
