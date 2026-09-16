"""生成界面截图，输出到本地的 `docs/`（该目录已被 .gitignore 排除，不进仓库）。

截图里会出现 wallhaven 上第三方作者的壁纸作品，所以只在本机留存、不随仓库分发。

Windows 上要抓一个未被前台合成的 tkinter 窗口，`ImageGrab.grab()` 抓不到内容，
必须用 PrintWindow + GetDIBits 直接从窗口句柄取像素。

    python app/tools/make_screenshots.py
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "docs"

# 直接以脚本方式运行时，项目根目录不在 sys.path 上，得手动加进去
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
PW_RENDERFULLCONTENT = 0x02


class _BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wintypes.DWORD * 3)]


def capture(hwnd: int) -> tuple[Image.Image, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top

    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

    bmi = _BMI()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    return img, int(ok)


def main() -> int:
    from app.config import get_config
    from app.ui.app import WallpaperPickerApp

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    app = WallpaperPickerApp(get_config())

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            try:
                app.update()
            except Exception:
                return
            time.sleep(0.03)

    def shoot(name: str, win=None) -> None:
        # 必须先把窗口抬到前台并让它把重绘消息处理完，否则 PrintWindow 会抓到全黑
        win = win or app
        try:
            win.deiconify()
            win.lift()
            win.attributes("-topmost", True)
            win.update()
            pump(0.6)
            win.attributes("-topmost", False)
        except Exception:
            pass
        win.update_idletasks()
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        img, ok = capture(hwnd)
        path = OUT_DIR / f"{name}.png"
        img.save(path)
        print(f"已保存 {path}  size={img.size} PrintWindow={ok} "
              f"灰度范围={img.convert('L').getextrema()}")

    pump(1.5)
    shoot("screenshot-1-search")

    app.search_tab.var_keyword.set("nature")
    app.search_tab.do_search(reset=True)
    for _ in range(80):
        pump(0.25)
        if app.search_tab.grid_view.count() > 0:
            break
    pump(2.5)

    # 挑一张大图，等原图加载完再截图——这样能看出预览是真的原图而不是缩略图
    order = app.search_tab.grid_view._order
    pick = None
    for iid in order:
        if int(app.search_tab.grid_view._items[iid].get("file_size") or 0) > 2 * 1024 * 1024:
            pick = iid
            break
    pick = pick or (order[0] if order else None)
    if pick:
        app.search_tab.grid_view._click(pick)
        pump(0.4)
        for _ in range(40):
            pump(0.3)
            if app.search_tab.preview.has_full_image(str(pick)):
                break
    pump(1.5)
    shoot("screenshot-2-selected")

    # 放大到 1:1 附近，展示局部细节
    if pick:
        app.search_tab.preview.canvas.zoom_by(1.25, anchor=(200, 140))
        app.search_tab.preview.canvas.zoom_by(1.25, anchor=(200, 140))
        pump(1.2)
        shoot("screenshot-3-zoomed")

    app.select_tab("history")
    pump(2.0)
    shoot("screenshot-4-history")

    app.select_tab("settings")
    # 演示「默认关键词」支持逗号分隔的多个关键词（只改控件显示，不写配置）
    app.settings_tab.var_keyword.set("cyberpunk, anime, space")
    pump(1.5)
    shoot("screenshot-5-settings")

    app._on_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
