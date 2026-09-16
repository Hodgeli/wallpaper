"""界面配色：跟随系统浅色/深色，统一配置 ttk 样式。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

LIGHT: dict[str, str] = {
    "bg": "#f4f5f7",
    "panel": "#ffffff",
    "panel_alt": "#eceef1",
    "fg": "#1f2328",
    "muted": "#6b7280",
    "border": "#d5d9e0",
    "accent": "#2563eb",
    "accent_fg": "#ffffff",
    "accent_soft": "#dbeafe",
    "entry_bg": "#ffffff",
    "entry_fg": "#1f2328",
    "select_bg": "#dbeafe",
    "select_fg": "#1f2328",
    "ok": "#15803d",
    "warn": "#b45309",
    "error": "#b91c1c",
    "thumb_bg": "#e3e6ea",
    "status_bg": "#e9ebef",
}

DARK: dict[str, str] = {
    "bg": "#1b1d21",
    "panel": "#24272b",
    "panel_alt": "#2b2f34",
    "fg": "#e7e9ec",
    "muted": "#9aa2ad",
    "border": "#3a3f45",
    "accent": "#3b82f6",
    "accent_fg": "#ffffff",
    "accent_soft": "#2f4463",
    "entry_bg": "#2c3035",
    "entry_fg": "#e7e9ec",
    "select_bg": "#334155",
    "select_fg": "#ffffff",
    "ok": "#4ade80",
    "warn": "#fbbf24",
    "error": "#f87171",
    "thumb_bg": "#33373c",
    "status_bg": "#202327",
}


def detect_system_theme() -> str:
    """读注册表判断系统是浅色还是深色（AppsUseLightTheme: 0=深色, 1=浅色）。"""
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(value) else "dark"
    except (OSError, ImportError, FileNotFoundError, ValueError, TypeError):
        return "light"


def palette(mode: str | None = None) -> dict[str, str]:
    """mode 是 "light" / "dark" 就强制用；其余（None、"auto"、写错的值）一律跟随系统。

    注意别写成 `DARK if mode == "dark" else LIGHT`——那样 "auto" 会掉进 else
    分支被当成浅色，等于把"跟随系统"变成了"强制浅色"。
    """
    if mode not in ("light", "dark"):
        mode = detect_system_theme()
    return DARK if mode == "dark" else LIGHT


def apply_theme(root: tk.Misc, mode: str | None = None) -> dict[str, str]:
    """给整个应用套上配色，返回所用色板。"""
    pal = palette(mode)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=pal["bg"])

    style.configure(".", background=pal["bg"], foreground=pal["fg"],
                    fieldbackground=pal["entry_bg"], bordercolor=pal["border"],
                    lightcolor=pal["panel"], darkcolor=pal["panel"])
    style.configure("TFrame", background=pal["bg"])
    style.configure("Panel.TFrame", background=pal["panel"])
    style.configure("TLabel", background=pal["bg"], foreground=pal["fg"])
    style.configure("Panel.TLabel", background=pal["panel"], foreground=pal["fg"])
    style.configure("Muted.TLabel", background=pal["bg"], foreground=pal["muted"])
    style.configure("PanelMuted.TLabel", background=pal["panel"], foreground=pal["muted"])
    style.configure("Title.TLabel", background=pal["bg"], foreground=pal["fg"],
                    font=("Microsoft YaHei UI", 14, "bold"))
    style.configure("Hint.TLabel", background=pal["bg"], foreground=pal["muted"],
                    font=("Microsoft YaHei UI", 9))
    style.configure("Error.TLabel", background=pal["status_bg"], foreground=pal["error"])
    style.configure("Ok.TLabel", background=pal["status_bg"], foreground=pal["ok"])
    style.configure("Status.TLabel", background=pal["status_bg"], foreground=pal["fg"])

    style.configure("TLabelframe", background=pal["bg"], bordercolor=pal["border"],
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=pal["bg"], foreground=pal["muted"],
                    font=("Microsoft YaHei UI", 9))

    style.configure("TButton", background=pal["panel_alt"], foreground=pal["fg"],
                    bordercolor=pal["border"], focuscolor=pal["accent"],
                    padding=(10, 5), relief="flat")
    style.map("TButton",
              background=[("active", pal["accent_soft"]), ("disabled", pal["panel_alt"])],
              foreground=[("disabled", pal["muted"])])
    style.configure("Accent.TButton", background=pal["accent"], foreground=pal["accent_fg"],
                    padding=(12, 6), relief="flat")
    style.map("Accent.TButton",
              background=[("active", pal["accent"]), ("disabled", pal["border"])],
              foreground=[("disabled", pal["muted"])])

    style.configure("TEntry", fieldbackground=pal["entry_bg"], foreground=pal["entry_fg"],
                    bordercolor=pal["border"], insertcolor=pal["fg"], padding=4)
    style.map("TEntry", bordercolor=[("focus", pal["accent"])])

    style.configure("TCombobox", fieldbackground=pal["entry_bg"], background=pal["panel_alt"],
                    foreground=pal["entry_fg"], arrowcolor=pal["fg"],
                    bordercolor=pal["border"], padding=4)
    style.map("TCombobox",
              fieldbackground=[("readonly", pal["entry_bg"])],
              foreground=[("readonly", pal["entry_fg"])],
              bordercolor=[("focus", pal["accent"])])

    style.configure("TSpinbox", fieldbackground=pal["entry_bg"], foreground=pal["entry_fg"],
                    background=pal["panel_alt"], arrowcolor=pal["fg"],
                    bordercolor=pal["border"], padding=4)

    style.configure("TCheckbutton", background=pal["bg"], foreground=pal["fg"],
                    focuscolor=pal["accent"])
    style.map("TCheckbutton", background=[("active", pal["bg"])])

    style.configure("TNotebook", background=pal["bg"], bordercolor=pal["border"],
                    tabmargins=(6, 6, 6, 0))
    style.configure("TNotebook.Tab", background=pal["panel_alt"], foreground=pal["muted"],
                    padding=(18, 8), bordercolor=pal["border"])
    style.map("TNotebook.Tab",
              background=[("selected", pal["panel"])],
              foreground=[("selected", pal["fg"])])

    style.configure("Treeview", background=pal["panel"], fieldbackground=pal["panel"],
                    foreground=pal["fg"], bordercolor=pal["border"], rowheight=46)
    style.map("Treeview",
              background=[("selected", pal["accent_soft"])],
              foreground=[("selected", pal["select_fg"])])
    style.configure("Treeview.Heading", background=pal["panel_alt"], foreground=pal["muted"],
                    relief="flat", padding=(6, 6))
    style.map("Treeview.Heading", background=[("active", pal["accent_soft"])])

    style.configure("TScrollbar", background=pal["panel_alt"], troughcolor=pal["bg"],
                    bordercolor=pal["bg"], arrowcolor=pal["muted"], relief="flat")
    style.map("TScrollbar", background=[("active", pal["accent_soft"])])

    style.configure("TProgressbar", background=pal["accent"], troughcolor=pal["panel_alt"],
                    bordercolor=pal["border"], lightcolor=pal["accent"],
                    darkcolor=pal["accent"])

    style.configure("TSeparator", background=pal["border"])

    # Combobox 下拉列表是 tk 控件，得单独设置
    root.option_add("*TCombobox*Listbox.background", pal["entry_bg"])
    root.option_add("*TCombobox*Listbox.foreground", pal["entry_fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", pal["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", pal["accent_fg"])
    root.option_add("*TCombobox*Listbox.font", "{Microsoft YaHei UI} 9")

    return pal


def font(size: int = 9, bold: bool = False) -> tuple:
    return ("Microsoft YaHei UI", size, "bold") if bold else ("Microsoft YaHei UI", size)
