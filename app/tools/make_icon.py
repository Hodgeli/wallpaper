"""用 Pillow 现画一个应用图标：夜空渐变 + 山峦剪影 + 下载箭头。

    python app/tools/make_icon.py
输出 app/assets/icon.ico（含 16/24/32/48/64/128/256 多尺寸）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]
BASE = 1024


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def draw_icon() -> Image.Image:
    s = BASE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底板
    radius = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=(23, 32, 56, 255))

    # 夜空渐变（上深下浅，偏蓝紫）
    top, bottom = (18, 24, 48), (58, 78, 140)
    for y in range(int(s * 0.62)):
        d.line([(0, y), (s, y)], fill=_lerp(top, bottom, y / (s * 0.62)) + (255,))

    # 星星
    stars = [(0.16, 0.14), (0.30, 0.09), (0.44, 0.17), (0.62, 0.10),
             (0.78, 0.19), (0.88, 0.12), (0.24, 0.26), (0.70, 0.28)]
    for fx, fy in stars:
        x, y = fx * s, fy * s
        r = s * 0.008
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 210))

    # 月亮
    mx, my, mr = s * 0.80, s * 0.20, s * 0.085
    d.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=(245, 240, 210, 255))

    # 山峦剪影（两层）
    back = [(0.0, 0.72), (0.20, 0.50), (0.36, 0.66), (0.52, 0.44),
            (0.72, 0.68), (0.86, 0.56), (1.0, 0.70)]
    d.polygon([(fx * s, fy * s) for fx, fy in back] + [(s, s), (0, s)],
              fill=(38, 48, 78, 255))

    front = [(0.0, 0.86), (0.16, 0.68), (0.34, 0.84), (0.50, 0.64),
             (0.68, 0.82), (0.84, 0.70), (1.0, 0.84)]
    d.polygon([(fx * s, fy * s) for fx, fy in front] + [(s, s), (0, s)],
              fill=(20, 26, 44, 255))

    # 下载箭头
    cx = s * 0.50
    shaft_top, shaft_bottom = s * 0.46, s * 0.66
    half_w = s * 0.055
    d.rounded_rectangle(
        [cx - half_w, shaft_top, cx + half_w, shaft_bottom],
        radius=int(half_w * 0.6), fill=(96, 176, 255, 255),
    )
    head_w, head_top, head_bottom = s * 0.15, s * 0.63, s * 0.80
    d.polygon([(cx - head_w, head_top), (cx + head_w, head_top),
               (cx, head_bottom)], fill=(96, 176, 255, 255))

    # 底部托盘
    tray_y = s * 0.86
    tray_h = s * 0.055
    d.rounded_rectangle([cx - s * 0.19, tray_y, cx + s * 0.19, tray_y + tray_h],
                        radius=int(tray_h * 0.5), fill=(150, 205, 255, 255))

    # 用圆角矩形裁掉四角（渐变和山峦是画满整个方形的，否则圆角看不出来）
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
    img.putalpha(mask)
    return img


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = draw_icon()
    # 注意：ICO 保存时不要传 append_images，否则 PIL 只会写出第一个尺寸
    img.save(OUT, format="ICO", sizes=[(n, n) for n in SIZES])
    print(f"已生成图标：{OUT}（{', '.join(str(n) for n in SIZES)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
