"""Windows 壁纸相关：显示器探测、填充方式、设置/读取当前壁纸。

全部走 ctypes + winreg，不依赖 pywin32，方便 PyInstaller 打包。
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

from .config import FILL_MODES
from .logsetup import get_logger

log = get_logger("winwall")

_user32 = ctypes.windll.user32

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SM_CMONITORS = 80

SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDWININICHANGE = 0x02

_DESKTOP_KEY = r"Control Panel\Desktop"


# --------------------------------------------------------------------- DPI
def enable_dpi_awareness() -> str:
    """开启 Per-Monitor V2 DPI 感知，避免高缩放下界面发糊。返回实际生效的模式。"""
    try:
        # PER_MONITOR_AWARE_V2 = -4
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return "per-monitor"
    except Exception:
        pass
    try:
        _user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


def system_dpi() -> int:
    """当前系统 DPI（96 表示 100% 缩放）。"""
    try:
        return int(_user32.GetDpiForSystem())
    except Exception:
        return 96


# --------------------------------------------------------------------- 显示器
class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
    ctypes.POINTER(_RECT), wintypes.LPARAM,
)


def list_monitors() -> list[dict]:
    """返回每台显示器的设备名、位置、尺寸和是否主屏。"""
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFOEXW)]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    _user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC, ctypes.POINTER(_RECT), _MONITORENUMPROC, wintypes.LPARAM,
    ]

    found: list[dict] = []

    def _cb(hmon, hdc, lprc, data):  # noqa: ANN001
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append({
                "device": info.szDevice,
                "x": r.left,
                "y": r.top,
                "width": r.right - r.left,
                "height": r.bottom - r.top,
                "primary": bool(info.dwFlags & 1),
            })
        return True

    try:
        _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    except Exception as exc:  # pragma: no cover - 极端情况兜底
        log.warning("枚举显示器失败：%s", exc)

    if not found:
        found.append({
            "device": "primary",
            "x": 0, "y": 0,
            "width": _user32.GetSystemMetrics(SM_CXSCREEN),
            "height": _user32.GetSystemMetrics(SM_CYSCREEN),
            "primary": True,
        })
    return found


def max_resolution() -> tuple[int, int]:
    """所有显示器中最大的宽 × 高——用作 atleast 的默认值。"""
    monitors = list_monitors()
    if not monitors:
        return (_user32.GetSystemMetrics(SM_CXSCREEN),
                _user32.GetSystemMetrics(SM_CYSCREEN))
    return (max(m["width"] for m in monitors), max(m["height"] for m in monitors))


def auto_resolution_text() -> str:
    w, h = max_resolution()
    return f"{w}x{h}"


def describe_displays() -> str:
    """给界面用的一行人话描述。"""
    monitors = list_monitors()
    parts = []
    for m in monitors:
        tag = "主屏" if m["primary"] else "副屏"
        parts.append(f"{tag} {m['width']}x{m['height']}")
    w, h = max_resolution()
    return "；".join(parts) + f"　→ 自适应取值 {w}x{h}"


def virtual_screen() -> tuple[int, int]:
    return (_user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


# --------------------------------------------------------------------- 壁纸
def get_current_wallpaper() -> str:
    """读取当前桌面壁纸路径（可能为空字符串）。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _DESKTOP_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "WallPaper")
            return str(value or "")
    except (OSError, ImportError, FileNotFoundError):
        return ""


def _write_style(fill_mode: str) -> None:
    style, tile = FILL_MODES.get(fill_mode, FILL_MODES["fill"])
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _DESKTOP_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, style)
        winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, tile)


def set_wallpaper(path: str | os.PathLike, fill_mode: str = "fill") -> None:
    """把指定图片设为桌面壁纸。失败抛 OSError。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"壁纸文件不存在：{p}")

    _write_style(fill_mode)

    ok = _user32.SystemParametersInfoW(
        SPI_SETDESKWALLPAPER, 0, ctypes.c_wchar_p(str(p)),
        SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE,
    )
    if not ok:
        raise OSError(ctypes.get_last_error() or 0,
                      "SystemParametersInfoW(SPI_SETDESKWALLPAPER) 调用失败")
    log.info("壁纸已设置：%s（填充方式=%s）", p, fill_mode)


def open_in_explorer(path: str | os.PathLike, select_file: bool = False) -> None:
    """打开文件夹；select_file=True 时定位并选中该文件。"""
    target = Path(path)
    if select_file and target.is_file():
        # 不要用 os.system 拼命令串：路径里出现一个引号就能把命令切断，
        # 而且 os.system 会闪一个 cmd 窗口。用列表参数直接交给 CreateProcess。
        #
        # 注意 `/select,` 必须和路径**分成两个参数**。写成 f"/select,{target}" 的话，
        # 路径含空格时 subprocess 会把整个 `/select,C:\...` 括起来变成
        # `"/select,C:\...\nature 3840x2160 abc.jpg"`，explorer 解析不出开关，
        # 实测会退化成打开「文档」目录。分开传才是 `explorer /select, "<路径>"`。
        subprocess.Popen(["explorer", "/select,", str(target)])
    else:
        folder = target if target.is_dir() else target.parent
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # noqa: S606 - Windows 专用
