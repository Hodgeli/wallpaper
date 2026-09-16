"""日志：按天切分到 %APPDATA%\\auto_wallpaper\\logs\\，保留 7 天。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from .config import APP_NAME, LOG_DIR, ensure_dirs

_configured = False


def setup_logging(level: int = logging.INFO, to_stderr: bool = True) -> logging.Logger:
    """初始化根日志器，重复调用无副作用。"""
    global _configured
    root = logging.getLogger(APP_NAME)
    if _configured:
        return root

    ensure_dirs()
    root.setLevel(level)
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        fh = TimedRotatingFileHandler(
            LOG_DIR / "wallpaper.log",
            when="midnight",
            backupCount=7,
            encoding="utf-8",
            delay=True,
        )
        fh.suffix = "%Y-%m-%d"
        fh.setFormatter(fmt)
        fh.setLevel(level)
        root.addHandler(fh)
    except OSError:
        # 日志文件写不了也不能让程序挂掉
        pass

    if to_stderr:
        try:
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(fmt)
            sh.setLevel(level)
            root.addHandler(sh)
        except Exception:
            pass

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{APP_NAME}.{name}")
