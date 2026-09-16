"""生成 PyInstaller 用的 Windows 版本资源文件。

    .venv/Scripts/python.exe app/tools/make_version_file.py [输出路径]

默认写到 `build/version_info.txt`，打包时用 `--version-file` 指过去：

    --version-file "$ROOT/build/version_info.txt"

**为什么要这一步**：`APP_VERSION` 只出现在窗口标题、启动日志和 `--version` 里，
exe 本身没有任何版本信息——右键「属性 → 详细信息」全是空白，用户拿到一个 exe
没法确认自己手上是哪一版。这里把版本号写进 exe 的 PE 资源，顺便让
`(Get-Item x.exe).VersionInfo` 可读。

版本号仍然只有 `app/config.py` 一个来源，这里只是把它翻译成 PyInstaller 要的格式。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import APP_NAME, APP_VERSION  # noqa: E402

DEFAULT_OUT = ROOT / "build" / "version_info.txt"

# 语言 0x0409（英语-美国） + 编码 0x04B0（Unicode）——Windows 属性页认这一对
_LANG, _CODEPAGE = 0x0409, 0x04B0
_TRANSLATION = (_LANG << 10) | _CODEPAGE

TEMPLATE = """\
# 由 app/tools/make_version_file.py 生成，别手改——改 app/config.py 里的 APP_VERSION。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={quad},
    prodvers={quad},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('{lang:04x}{cp:04x}', [
        StringStruct('CompanyName', '{name}'),
        StringStruct('FileDescription', '{desc}'),
        StringStruct('FileVersion', '{ver}'),
        StringStruct('InternalName', '{name}'),
        StringStruct('LegalCopyright', ''),
        StringStruct('OriginalFilename', '{name}.exe'),
        StringStruct('ProductName', '{name}'),
        StringStruct('ProductVersion', '{ver}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [{lang}, {cp}])])
  ]
)
"""


def version_quad(version: str) -> tuple[int, int, int, int]:
    """把 "2.1.3" 变成 (2, 1, 3, 0)。解析不了的段落按 0 算，不抛异常。"""
    parts: list[int] = []
    for chunk in (version or "").split(".")[:4]:
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


def build_text() -> str:
    quad = version_quad(APP_VERSION)
    return TEMPLATE.format(
        quad="(" + ", ".join(str(n) for n in quad) + ")",
        lang=_LANG, cp=_CODEPAGE,
        name=APP_NAME,
        desc=f"{APP_NAME} — wallhaven 壁纸助手",
        ver=APP_VERSION,
    )


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    if not out.is_absolute():
        out = (Path.cwd() / out).resolve()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_text(), encoding="utf-8")
    except OSError as exc:
        print(f"写版本资源文件失败：{out}（{exc}）")
        return 1
    print(f"版本 {APP_VERSION}（{version_quad(APP_VERSION)}）-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
