"""冒烟测试：启动打包好的 exe，确认进程活着、主窗口真的创建出来了、日志无异常。

    python app/tools/smoke_test.py                      # 测 dist/WallpaperPicker.exe
    python app/tools/smoke_test.py build/stage/X.exe    # 测指定路径

退出码：0 正常 / 1 进程提前退出 / 2 没找到可见窗口 / 3 日志里有 ERROR
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_EXE = ROOT / "dist" / "WallpaperPicker.exe"
LOG = Path.home() / "AppData" / "Roaming" / "auto_wallpaper" / "logs" / "wallpaper.log"

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_of(pids: set[int] | None) -> list[tuple[int, str, int]]:
    """注意：PyInstaller --onefile 的 exe 会 fork 出一个子进程，
    真正持有窗口的是子进程。这里直接按标题全局找，避免依赖进程树查询。"""
    found: list[tuple[int, str, int]] = []

    def cb(hwnd, _):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if not user32.IsWindowVisible(hwnd):
            return True
        if pids is not None and wpid.value not in pids:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value:
            found.append((hwnd, buf.value, wpid.value))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found


def main() -> int:
    exe = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_EXE
    if not exe.exists():
        print(f"!! 找不到 exe：{exe}")
        return 4
    print(f"被测 exe：{exe}")

    log_before = LOG.read_text("utf-8", "replace") if LOG.exists() else ""
    # 先记下启动前已有的同名窗口。用户可能正开着程序，
    # 如果只按标题找窗口，会把人家那个算成"启动成功"——假通过。
    before = {hwnd for hwnd, title, _pid in windows_of(None)
              if "WallpaperPicker" in title}

    proc = subprocess.Popen([str(exe)])
    print(f"已启动 pid={proc.pid}")
    try:
        deadline = time.time() + 30
        wins: list[tuple[int, str, int]] = []
        while time.time() < deadline:
            time.sleep(1.0)
            if proc.poll() is not None:
                print(f"!! 进程提前退出，returncode={proc.returncode}")
                return 1
            wins = [w for w in windows_of(None)
                    if "WallpaperPicker" in w[1] and w[0] not in before]
            if wins:
                break
        print(f"存活进程 pid={proc.pid}  poll={proc.poll()}")
        for hwnd, title, wpid in wins:
            print(f"  窗口 hwnd={hwnd} pid={wpid} title={title!r}")
        if not wins:
            print("!! 没找到新出现的可见窗口"
                  f"（启动前已有 {len(before)} 个同名窗口，可能是用户自己开着的）")
            return 2
    finally:
        # 只杀本次启动的进程树。--onefile 会 fork 子进程，子进程才持有窗口，
        # terminate() 打不到它，得用 taskkill /T 连子进程一起收掉。
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()

    log_after = LOG.read_text("utf-8", "replace") if LOG.exists() else ""
    new = log_after[len(log_before):].strip()
    print("--- 本次运行新增日志 ---")
    print(new or "(无)")
    bad = [ln for ln in new.splitlines() if " ERROR " in ln or "Traceback" in ln or " CRITICAL " in ln]
    if bad:
        print("!! 发现错误行：")
        for b in bad:
            print("   ", b)
        return 3
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
