"""把「随机换一张壁纸」注册到桌面右键菜单里。

用法（**以管理员身份**运行，否则 HKEY_CLASSES_ROOT 写不进去）：

    .venv/Scripts/python.exe app/tools/install_context_menu.py
    .venv/Scripts/python.exe app/tools/install_context_menu.py --exe D:\\path\\a.exe
    .venv/Scripts/python.exe app/tools/install_context_menu.py --uninstall

改的是 `HKEY_CLASSES_ROOT\\DesktopBackground\\shell` 下的一个子键。只碰这一个键，
卸载就是把整棵子树删掉，不留残渣。

**注册了不等于生效**：改完要重启 explorer.exe，菜单才会刷新。
脚本默认不替你重启（那会关掉你所有文件夹窗口），只打印提示；
加 `--restart-explorer` 才会重启。

`Icon` 那行：exe 里嵌的是**单张图标**，所以后面不能跟 `,0`。
写了 `,N`（N>0）时 Windows 会去找资源组里第 N 个图标，找不到就不显示图标。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

import winreg  # noqa: E402

# 桌面背景右键菜单的子项都挂在这个键下面，和系统自带的「查看」「排序方式」并列
MENU_PARENT = r"DesktopBackground\shell"
KEY_NAME = "WallhavenRandom"
KEY_PATH = rf"{MENU_PARENT}\{KEY_NAME}"
MENU_TEXT = "随机换一张壁纸"
ROOT = Path(__file__).resolve().parents[2]


def find_exe(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"!! 指定的 exe 不存在：{p}")
        return p.resolve()
    cand = ROOT / "dist" / "WallpaperPicker.exe"
    if cand.exists():
        return cand
    raise SystemExit(
        f"!! 找不到 {cand}\n"
        "   先打包（见 README「重新打包」），或用 --exe 指定 exe 的路径。"
    )


def install(exe: Path) -> None:
    # command：实际执行的命令行。不需要 %1 之类——这是桌面空白区域菜单，
    # 没有"选中的文件"。
    cmd = f'"{exe}" --random --silent'

    with winreg.CreateKeyEx(winreg.HKEY_CLASSES_ROOT, KEY_PATH, 0,
                            winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, MENU_TEXT)
        winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, str(exe))
    with winreg.CreateKeyEx(winreg.HKEY_CLASSES_ROOT, KEY_PATH + r"\command", 0,
                            winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, cmd)

    print(f"已注册：{MENU_TEXT}")
    print(f"  exe     {exe}")
    print(f"  命令行  {cmd}")
    print(f"  注册表  HKEY_CLASSES_ROOT\\{KEY_PATH}")
    print()
    print("要重启一次 explorer.exe 才会出现在菜单里：")
    print("  任务管理器 → 找到「Windows 资源管理器」→ 右键「重新启动」")
    print("  或者重跑本脚本并加 --restart-explorer")


def uninstall() -> None:
    def del_tree(path: str) -> None:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, path, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                del_tree(rf"{path}\{sub}")
        winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, path)

    try:
        del_tree(KEY_PATH)
    except FileNotFoundError:
        print("菜单项本来就没注册，无需卸载。")
        return
    print(f"已删除 HKEY_CLASSES_ROOT\\{KEY_PATH}")
    print("同样要重启 explorer.exe 才会从菜单里消失。")


def restart_explorer() -> None:
    """重启资源管理器。会关掉所有文件夹窗口，所以只在你明确要求时做。"""
    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                   capture_output=True, check=False)
    # 用 explorer.exe 自己启动（不带参数）来恢复 shell，比 start 稳
    subprocess.Popen(["explorer.exe"])
    print("已重启 explorer.exe。")


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="install_context_menu",
        description="把「随机换一张壁纸」加到桌面右键菜单（需要管理员权限）。",
    )
    ap.add_argument("--exe", default=None, help="指定 WallpaperPicker.exe 的路径")
    ap.add_argument("--uninstall", action="store_true", help="从右键菜单里移除")
    ap.add_argument("--restart-explorer", action="store_true",
                    help="改完顺手重启 explorer.exe（会关掉所有文件夹窗口）")
    args = ap.parse_args()

    try:
        if args.uninstall:
            uninstall()
        else:
            install(find_exe(args.exe))
    except PermissionError:
        # 不包一层的话，用户看到的是一屏 traceback，最后一行才是
        # `PermissionError: [WinError 5] 拒绝访问。` —— 看不出是权限问题。
        print("!! 拒绝访问：注册表 HKEY_CLASSES_ROOT 需要管理员权限。")
        print("   用管理员身份重新打开一个终端，再跑一次。")
        return 5

    if args.restart_explorer:
        restart_explorer()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
