"""程序入口。

用法：
    WallpaperPicker.exe                  启动图形界面
    WallpaperPicker.exe --random         静默抓一张随机壁纸并设为桌面（可挂计划任务）
    WallpaperPicker.exe --random --silent  失败时也不弹窗
"""
from __future__ import annotations

import argparse
import ctypes
import logging
import sys

from .config import APP_NAME, APP_VERSION, ensure_dirs, get_config
from .logsetup import setup_logging
from .store import get_store

MB_ICONERROR = 0x10
MB_TOPMOST = 0x40000


def _message_box(text: str, title: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, MB_ICONERROR | MB_TOPMOST)
    except Exception:
        pass


def run_random(silent: bool = False, keyword: str | None = None) -> int:
    """--random：抓一张随机壁纸并设为桌面，不弹界面。"""
    from .api import WallpaperError
    from .service import random_wallpaper

    cfg = get_config()
    if keyword:
        cfg.set("default_keyword", keyword)

    log = logging.getLogger(f"{APP_NAME}.random")
    try:
        record = random_wallpaper(cfg, store=get_store(),
                                  on_stage=lambda text: log.info("%s", text))
    except WallpaperError as exc:
        log.error("随机换壁纸失败：%s", exc)
        if not silent:
            _message_box(f"{exc.message}\n\n日志：%APPDATA%\\auto_wallpaper\\logs",
                         "随机换壁纸失败")
        return 1
    except Exception as exc:  # noqa: BLE001 - 计划任务里绝不能崩栈
        log.exception("随机换壁纸出现未预期错误")
        if not silent:
            _message_box(f"未预期的错误：{exc}\n\n日志：%APPDATA%\\auto_wallpaper\\logs",
                         "随机换壁纸失败")
        return 1

    log.info("完成：%s", record.get("path"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="从 wallhaven 搜索、下载并设置 Windows 桌面壁纸。",
    )
    parser.add_argument("--random", action="store_true",
                        help="不打开界面，直接抓一张随机壁纸并设为桌面（适合计划任务）")
    parser.add_argument("--keyword", default=None,
                        help="配合 --random 使用，临时指定搜索关键词")
    parser.add_argument("--silent", action="store_true",
                        help="配合 --random 使用，失败时也不弹窗")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    parser.add_argument("--version", action="version",
                        version=f"{APP_NAME} {APP_VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(raw)
    except SystemExit as exc:
        # argparse 报错时只往 stderr 打一行用法。打包成 --noconsole 的 exe 之后
        # stderr 是空的，用户什么都看不到——右键菜单里点一下完全没反应、
        # 日志里也一个字没有，根本无从查起。真发生过：注册表里的命令行末尾
        # 粘进了一个换行，`--silent\n` 被当成未知参数。
        # 所以这里往日志里留一条，并且用 %r 打出原样参数（能看出空白字符）。
        if exc.code not in (0, None):   # -h / --version 是 0，不用记
            setup_logging(level=logging.INFO, to_stderr=False).error(
                "无法识别的命令行参数：%r。参数是按原样传入的，"
                "注册表 / 快捷方式 / 计划任务里的尾随空格或换行同样会导致失败。",
                raw,
            )
        raise

    setup_logging(
        level=logging.DEBUG if args.debug else logging.INFO,
        to_stderr=not args.random,
    )
    ensure_dirs()

    if args.random:
        return run_random(silent=args.silent, keyword=args.keyword)

    # 延迟导入：--random 模式不需要加载 tkinter / Pillow
    from .ui.app import run_gui

    return run_gui(get_config())


if __name__ == "__main__":
    sys.exit(main())
