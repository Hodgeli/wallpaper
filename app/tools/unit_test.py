"""纯逻辑单元测试：naming / config / store / api / 预览面板纯函数。

不联网、不开窗口、几秒跑完：

    .venv/Scripts/python.exe app/tools/unit_test.py

和 `feature_test.py` 的分工：那边测"整条链路串起来能不能跑"（要开窗口、要下载，
跑一遍几分钟）；这边测"纯函数在各种奇怪输入下还对不对"，改完随手就能跑。

退出码 0 表示全部通过。
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# APPDATA 必须在 import app.* 之前设好：`config.CONFIG_DIR` 是模块级算出来的，
# 一旦算成真实用户目录，后面 Config.save() / DownloadStore.save() 里的
# ensure_dirs() 就会往那里建目录。这是本文件唯一需要"隔离"的东西。
_TMP_APPDATA = Path(tempfile.mkdtemp(prefix="wp_unit_test_"))
os.environ["APPDATA"] = str(_TMP_APPDATA)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 控制台编码兜底：Windows 上 Python 的 stdout 跟着系统代码页走，中文机器是 cp936
# （打得出中文，所以本地一直没暴露），英文机器是 cp1252——打中文直接抛
# UnicodeEncodeError 把脚本带崩，CI 的 windows-latest 就是后者。
# 只把编码错误降级成替换字符，不改 encoding，本地中文显示照旧。
for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

from app.api import (  # noqa: E402
    RATE_LIMIT_FALLBACK_WAIT,
    WallpaperError,
    _retry_after_seconds,
    resolve_proxies,
    system_proxy,
)
from app.config import (  # noqa: E402
    APP_NAME,
    APP_VERSION,
    CONFIG_DIR,
    DEFAULT_CONFIG,
    Config,
    _deep_merge,
    as_int,
)
from app.naming import (  # noqa: E402
    build_filename,
    build_subdir,
    clean_segment,
    guess_ext,
    primary_keyword,
    target_path,
)
from app.store import DownloadStore  # noqa: E402

FAILS: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    ok = got == want
    print(f"  {'OK ' if ok else '!! '}{label}: {got!r}"
          + ("" if ok else f"  期望 {want!r}"))
    if not ok:
        FAILS.append(label)


def raises(fn, exc_type) -> bool:
    try:
        fn()
    except exc_type:
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ~~ 期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}")
        return False
    print(f"  ~~ 期望 {exc_type.__name__}，实际没抛")
    return False


# =================================================================== 1. 命名
def test_naming() -> None:
    print("=== 1. naming ===")
    # 空 / 全是垃圾 -> 兜底
    check("空串兜底", clean_segment(""), "wallpaper")
    check("全空白兜底", clean_segment("   "), "wallpaper")
    check("全是点兜底（去完首尾点就空了）", clean_segment(".."), "wallpaper")
    check("None 也兜底", clean_segment(None), "wallpaper")
    check("只有非法字符也兜底", clean_segment('*/:"<>|'), "wallpaper")

    # 清洗
    check("空格转下划线", clean_segment("digital art"), "digital_art")
    check("连续空白合并成一个下划线", clean_segment("a  \t b"), "a_b")
    check("非法字符直接删掉", clean_segment('a:b*c?d"e<f>g|h'), "abcdefgh")
    check("路径分隔符也删（防目录穿越）", clean_segment("a/../b"), "a..b")
    check("控制字符删掉", clean_segment("a\x00b\x1fc"), "abc")
    check("连续下划线合并", clean_segment("__a__b__"), "a_b")
    check("首尾的点/下划线/空格去掉", clean_segment(" ._a_. "), "a")
    check("中文原样保留", clean_segment("风景 壁纸"), "风景_壁纸")

    # 截断
    check("超长截到 max_len", clean_segment("a" * 40), "a" * 30)
    check("max_len 可指定", clean_segment("abcdef", 3), "abc")
    check("截断后落在点上的要削掉", clean_segment("a" * 29 + ".b"), "a" * 29)

    # Windows 保留设备名
    check("CON 加前缀", clean_segment("CON"), "_CON")
    check("小写 con 也认", clean_segment("con"), "_con")
    check("NUL 加前缀", clean_segment("NUL"), "_NUL")
    check("COM1 加前缀", clean_segment("COM1"), "_COM1")
    check("LPT9 加前缀", clean_segment("LPT9"), "_LPT9")
    check("COM10 不是保留名", clean_segment("COM10"), "COM10")
    check("带扩展名的 con 也算（看第一个点之前）", clean_segment("con.txt"), "_con.txt")

    # primary_keyword / build_subdir
    check("关键词只去首尾空白", primary_keyword("  digital art  "), "digital art")
    check("空关键词还是空", primary_keyword(""), "")
    check("子目录名走清洗", build_subdir("digital art"), "digital_art")
    check("子目录名兜底", build_subdir(""), "wallpaper")

    # build_filename
    check("默认命名方式",
          build_filename("nature", "3840x2160", "abc123", ".jpg"),
          "nature_3840x2160_abc123.jpg")
    check("ext 不带点也能处理",
          build_filename("nature", "1920x1080", "abc", "png"),
          "nature_1920x1080_abc.png")
    check("ext 为空退 .jpg",
          build_filename("nature", "1920x1080", "abc", ""),
          "nature_1920x1080_abc.jpg")
    check("仅 ID 模式",
          build_filename("nature", "3840x2160", "abc123", ".jpg", "id"),
          "abc123.jpg")
    check("日期模式",
          build_filename("nature", "3840x2160", "abc123", ".jpg",
                         "date_keyword_res_id", when=datetime(2026, 9, 15)),
          "20260915_nature_3840x2160_abc123.jpg")
    check("命名方式写错时退回默认",
          build_filename("nature", "3840x2160", "abc123", ".jpg", "不存在的模式"),
          "nature_3840x2160_abc123.jpg")
    check("分辨率缺失写 unknown",
          build_filename("nature", "", "abc123", ".jpg"),
          "nature_unknown_abc123.jpg")
    check("id 缺失写 noid",
          build_filename("nature", "1920x1080", "", ".jpg"),
          "nature_1920x1080_noid.jpg")
    check("关键词缺失走兜底",
          build_filename("", "1920x1080", "abc", ".jpg"),
          "wallpaper_1920x1080_abc.jpg")
    check("关键词里的空格不会进文件名",
          build_filename("digital art", "1920x1080", "abc", ".jpg"),
          "digital_art_1920x1080_abc.jpg")

    # guess_ext
    check("file_type 优先", guess_ext("image/jpeg", "http://x/a.png"), ".jpg")
    check("file_type 大小写不敏感", guess_ext("IMAGE/PNG", ""), ".png")
    check("webp 认得", guess_ext("image/webp", ""), ".webp")
    check("file_type 不认识时看 URL", guess_ext("", "http://x/a.webp"), ".webp")
    check("URL 带查询串也能取后缀", guess_ext("", "http://x/a.webp?w=1920"), ".webp")
    check("jpeg 后缀规整成 .jpg", guess_ext("", "http://x/a.jpeg"), ".jpg")
    check("URL 没后缀退 .jpg", guess_ext("", "http://x/a"), ".jpg")
    check("两个都没有退 .jpg", guess_ext("", ""), ".jpg")
    check("URL 后缀不在白名单也退 .jpg", guess_ext("", "http://x/a.exe"), ".jpg")

    # target_path
    p, sub, name = target_path(Path("D:/wp"), "digital art", "3840x2160",
                               "abc123", ".jpg", "keyword_res_id")
    check("子目录", sub, "digital_art")
    check("文件名", name, "digital_art_3840x2160_abc123.jpg")
    check("拼出来的完整路径", p, Path("D:/wp") / "digital_art" /
          "digital_art_3840x2160_abc123.jpg")


# =================================================================== 2. 配置
def test_config(tmp: Path) -> None:
    print("\n=== 2. config ===")

    # _deep_merge
    base = {"a": 1, "b": {"x": 1, "y": 2}, "c": 3}
    merged = _deep_merge(base, {"a": 9, "b": {"y": 8}, "未知键": 1})
    check("已知的标量被覆盖", merged["a"], 9)
    check("嵌套 dict 是逐键合并的", merged["b"], {"x": 1, "y": 8})
    check("没提到的键保持原值", merged["c"], 3)
    check("未知键被丢掉", "未知键" in merged, False)
    check("就地修改（返回的就是 base）", merged is base, True)

    base2 = {"a": {"x": 1}}
    check("类型不一致时直接覆盖",
          _deep_merge(base2, {"a": 5}), {"a": 5})

    # Config 读写
    p = tmp / "config.json"
    cfg = Config(p)
    check("不存在的配置用默认值", cfg.get("fill_mode"), DEFAULT_CONFIG["fill_mode"])
    check("读未知键回 None", cfg.get("根本没有这个键"), None)
    check("读未知键可给默认值", cfg.get("根本没有这个键", "兜底"), "兜底")

    cfg.set("default_keyword", "nature")
    cfg.update(ratio="16x9")
    cfg.save()
    check("存盘后能读回来", Config(p).get("default_keyword"), "nature")
    check("update 也存下来了", Config(p).get("ratio"), "16x9")
    check("存的是合法 JSON", isinstance(json.loads(p.read_text("utf-8")), dict), True)

    cfg.reset()
    check("reset 回到默认", cfg.get("default_keyword"), "")

    # 损坏的配置
    broken = tmp / "broken.json"
    broken.write_text("{这不是 JSON", encoding="utf-8")
    bad = Config(broken)
    check("坏配置不抛异常，用默认值", bad.get("fill_mode"), DEFAULT_CONFIG["fill_mode"])
    check("坏配置不会被覆盖掉", broken.read_text("utf-8"), "{这不是 JSON")

    # 未知键（theme 死键那个 bug 的根因就在这）
    legacy = tmp / "legacy.json"
    legacy.write_text(json.dumps({"theme": "dark", "fill_mode": "tile",
                                  "根本没有这个键": 1}), encoding="utf-8")
    old = Config(legacy)
    check("已知键读得到", old.get("fill_mode"), "tile")
    check("DEFAULT_CONFIG 里声明过的键读得到（P0 补的 theme）",
          old.get("theme"), "dark")
    check("没在 DEFAULT_CONFIG 里声明过的键读不到（老 bug 的根因）",
          "根本没有这个键" in old.as_dict(), False)

    # _migrate：老配置的窗口尺寸
    seq = [0]

    def load_window(raw: dict) -> dict:
        seq[0] += 1
        f = tmp / f"win_{seq[0]}.json"
        f.write_text(json.dumps(raw), encoding="utf-8")
        return Config(f).get("window")

    legacy_win = load_window({"ui_rev": 1, "window": {"w": 1280, "h": 820,
                                                      "x": 100, "y": 50}})
    check("旧默认窗口尺寸升到新默认", (legacy_win["w"], legacy_win["h"]), (1440, 900))
    check("升级时保留原来的位置", (legacy_win["x"], legacy_win["y"]), (100, 50))

    custom_win = load_window({"ui_rev": 1, "window": {"w": 1000, "h": 700}})
    check("用户自己调过的尺寸不动", (custom_win["w"], custom_win["h"]), (1000, 700))

    new_win = load_window({"ui_rev": 2, "window": {"w": 1280, "h": 820}})
    check("已经是新版就不再迁移", (new_win["w"], new_win["h"]), (1280, 820))

    no_rev = load_window({"window": {"w": 1280, "h": 820}})
    check("没有 ui_rev 的老配置按 1 处理", (no_rev["w"], no_rev["h"]), (1440, 900))

    weird = load_window({"ui_rev": "abc", "window": {"w": 1280, "h": 820}})
    check("ui_rev 是垃圾值时按 1 处理", (weird["w"], weird["h"]), (1440, 900))

    # 迁移会写回 ui_rev，下次不再重复迁移
    f = tmp / "win_migrated.json"
    f.write_text(json.dumps({"ui_rev": 1, "window": {"w": 1280, "h": 820}}),
                 encoding="utf-8")
    Config(f)
    check("迁移结果落了盘",
          json.loads(f.read_text("utf-8"))["ui_rev"], DEFAULT_CONFIG["ui_rev"])

    # 窗口尺寸被改坏（手改配置 / 别的版本写脏）时不能让程序起不来。
    # 真踩过：window_geometry() 里直接 int()，抛在 App.__init__ 路径上，
    # 打包版静默退出，用户只看到"双击没反应"。
    def geom(raw: dict):
        f = tmp / f"geom_{len(list(tmp.glob('geom_*.json')))}.json"
        f.write_text(json.dumps(raw), encoding="utf-8")
        return Config(f).window_geometry()

    check("窗口宽是 '1440px' 时只有它退回默认，高照用",
          geom({"window": {"w": "1440px", "h": 900}}), (1280, 900, None, None))
    check("窗口高是 '高' 时退回默认",
          geom({"window": {"w": 1440, "h": "高"}}), (1440, 820, None, None))
    check("窗口尺寸是 null 时退回默认",
          geom({"window": {"w": None, "h": None}}), (1280, 820, None, None))
    check("窗口尺寸是浮点数时取整",
          geom({"window": {"w": 1200.7, "h": 800.2}}), (1200, 800, None, None))
    check("位置是 '左' 时退回 None（居中）",
          geom({"window": {"w": 1200, "h": 800, "x": "左", "y": 30}}),
          (1200, 800, None, 30))
    check("window 整个是字符串时不炸",
          geom({"window": "1440x900"}), (1280, 820, None, None))
    check("没有 window 键时用默认窗口尺寸（DEFAULT_CONFIG 里是 1440x900）",
          geom({}), (1440, 900, None, None))

    # thumb_cache_mb 也是同样的坑（设置页在 App 初始化路径上读它）
    bad_cache = tmp / "bad_cache.json"
    bad_cache.write_text(json.dumps({"thumb_cache_mb": "200MB"}), encoding="utf-8")
    check("缓存上限是 '200MB' 时 as_int 兜底",
          as_int(Config(bad_cache).get("thumb_cache_mb") or 200, 200), 200)
    check("缓存上限是数字字符串时正常转换", as_int("512", 200), 512)
    check("缓存上限是 0 时保留 0（不是假值）", as_int(0, 200), 0)

    # 派生值
    cfg2 = Config(tmp / "derived.json")
    cfg2.set("save_dir", "")
    check("save_dir 为空时退回默认",
          cfg2.save_dir, Path(DEFAULT_CONFIG["save_dir"]))
    cfg2.set("window", {})
    check("窗口几何全空时给兜底值", cfg2.window_geometry(), (1280, 820, None, None))


# =================================================================== 3. 记录表
def test_store(tmp: Path) -> None:
    print("\n=== 3. store ===")
    p = tmp / "store.json"
    st = DownloadStore(p)

    rec = {"id": "a1", "keyword": "nature", "path": str(tmp / "a1.jpg")}
    st.upsert(rec)
    got = st.get("a1")
    check("新记录 use_count 从 1 开始", got["use_count"], 1)
    check("新记录有 added_at", bool(got.get("added_at")), True)
    check("新记录有 updated_at", bool(got.get("updated_at")), True)
    check("has 能查到", st.has("a1"), True)
    check("has 查不存在的", st.has("nope"), False)
    check("get 不存在的回 None", st.get("nope"), None)

    first_added = got["added_at"]

    # get 给的是副本，改它不该影响表里那条
    got["keyword"] = "改过了"
    check("get 返回的是副本（改它不影响表）", st.get("a1")["keyword"], "nature")

    st.upsert({"id": "a1", "keyword": "nature", "path": str(tmp / "a1.jpg")})
    got = st.get("a1")
    check("重复写入累加 use_count", got["use_count"], 2)
    check("重复写入保留首次 added_at", got["added_at"], first_added)

    st.upsert({"id": "a1"})
    check("再写一次变 3", st.get("a1")["use_count"], 3)

    st.upsert({"keyword": "没有 id"})
    check("没有 id 的记录被忽略", len(st.all()), 1)

    # update_fields：维护性改动不该算一次"使用"
    before_added = st.get("a1")["added_at"]
    before_use = st.get("a1")["use_count"]
    check("update_fields 返回 True", st.update_fields("a1", {"path": "新路径"}), True)
    check("字段确实改了", st.get("a1")["path"], "新路径")
    check("update_fields 不动 added_at", st.get("a1")["added_at"], before_added)
    check("update_fields 不动 use_count", st.get("a1")["use_count"], before_use)
    check("update_fields 查不到时回 False", st.update_fields("nope", {"a": 1}), False)

    # save=False 时不落盘
    st.save()
    snapshot = p.read_text("utf-8")
    st.update_fields("a1", {"keyword": "只改内存"}, save=False)
    check("save=False 时没写盘", p.read_text("utf-8"), snapshot)
    check("但内存里已经改了", st.get("a1")["keyword"], "只改内存")
    st.save()
    check("统一 save 之后落盘了", "只改内存" in p.read_text("utf-8"), True)

    # 排序 / 分页
    st2 = DownloadStore(tmp / "store2.json")
    for i, when in enumerate(["2026-01-01T00:00:00", "2026-03-01T00:00:00",
                              "2026-02-01T00:00:00"]):
        f = tmp / f"r{i}.jpg"
        f.write_bytes(b"x")
        st2.upsert({"id": f"r{i}", "added_at": when, "path": str(f)})
    check("all 按 added_at 倒序",
          [r["id"] for r in st2.all()], ["r1", "r2", "r0"])
    check("recent_ids 按倒序截断",
          st2.recent_ids(2), {"r1", "r2"})
    check("recent_ids 超过总数时给全部",
          st2.recent_ids(99), {"r0", "r1", "r2"})

    # 文件存在性
    real = tmp / "real.jpg"
    real.write_bytes(b"x")
    st2.upsert({"id": "has_file", "path": str(real)})
    st2.upsert({"id": "no_file", "path": str(tmp / "没有这个文件.jpg")})
    check("文件在时返回路径", st2.existing_file("has_file"), real)
    check("文件不在时回 None", st2.existing_file("no_file"), None)
    check("记录都没有时回 None", st2.existing_file("nope"), None)
    check("prune_missing 只清掉丢失的", st2.prune_missing(), 1)
    check("存在的记录留下了", st2.has("has_file"), True)
    check("丢失的记录清掉了", st2.has("no_file"), False)
    check("再清一次是 0", st2.prune_missing(), 0)

    # remove
    st2.remove("has_file")
    check("remove 生效", st2.has("has_file"), False)
    st2.remove("根本不存在")
    check("remove 不存在的 id 不炸", True, True)

    # 兼容老的"裸 list"格式
    legacy = tmp / "legacy_store.json"
    legacy.write_text(json.dumps([{"id": "old1"}, {"id": "old2"},
                                  {"没有 id": 1}]), encoding="utf-8")
    lst = DownloadStore(legacy)
    check("裸 list 格式读得进来", sorted(r["id"] for r in lst.all()), ["old1", "old2"])
    check("没有 id 的条目被跳过", len(lst.all()), 2)

    # 损坏的记录表
    broken = tmp / "broken_store.json"
    broken.write_text("不是 JSON", encoding="utf-8")
    check("坏记录表不抛异常，从空开始", DownloadStore(broken).all(), [])

    # 落盘格式
    check("存的是带 version 的对象",
          isinstance(json.loads(p.read_text("utf-8")), dict), True)


# =================================================================== 4. api 纯函数
class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinReg:
    """假的 winreg。

    `system_proxy()` 里是**函数内** `import winreg`，Python 会先查 `sys.modules`，
    所以换掉 `sys.modules["winreg"]` 就够了——不用为了可测性去改产品代码。
    """

    HKEY_CURRENT_USER = object()

    def __init__(self, values: dict, *, broken: bool = False) -> None:
        self.values = values
        self.broken = broken

    def OpenKey(self, root, path):      # noqa: N802 - 照抄 winreg 的命名
        if self.broken:
            raise OSError("测试用的假注册表错误")
        return _FakeKey()

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], 1


class _Resp:
    """`_retry_after_seconds` 只用到 `.headers.get`，不用真 Response。"""

    def __init__(self, headers: dict | None = None) -> None:
        self.headers = headers or {}


@contextlib.contextmanager
def fake_registry(values: dict, *, broken: bool = False):
    """临时把 `sys.modules["winreg"]` 换成假的，退出时还原。"""
    old = sys.modules.get("winreg")
    sys.modules["winreg"] = _FakeWinReg(values, broken=broken)
    try:
        yield
    finally:
        if old is None:
            sys.modules.pop("winreg", None)
        else:
            sys.modules["winreg"] = old


def test_api(tmp: Path) -> None:
    print("\n=== 4. api 纯函数 ===")
    import winreg  # noqa: F401 - 本机没有这个模块的话，下面测的全是假东西

    with fake_registry({"ProxyEnable": 0, "ProxyServer": "127.0.0.1:7890"}):
        check("系统代理关着时回 None", system_proxy(), None)

    with fake_registry({"ProxyEnable": 1, "ProxyServer": "127.0.0.1:7890"}):
        check("裸地址补上 http://", system_proxy(), "http://127.0.0.1:7890")

    with fake_registry({"ProxyEnable": 1, "ProxyServer": "http://127.0.0.1:7890"}):
        check("已有 scheme 就不重复加", system_proxy(), "http://127.0.0.1:7890")

    with fake_registry({"ProxyEnable": 1,
                        "ProxyServer": "http=127.0.0.1:7890;https=127.0.0.1:7891"}):
        check("http=/https= 格式优先取 https",
              system_proxy(), "http://127.0.0.1:7891")

    with fake_registry({"ProxyEnable": 1, "ProxyServer": "http=127.0.0.1:7890"}):
        check("只有 http= 时取 http", system_proxy(), "http://127.0.0.1:7890")

    with fake_registry({"ProxyEnable": 1,
                        "ProxyServer": "ftp=127.0.0.1:21;http=10.0.0.1:8080"}):
        check("跳过不认识的 scheme", system_proxy(), "http://10.0.0.1:8080")

    with fake_registry({"ProxyEnable": 1, "ProxyServer": "socks=127.0.0.1:1080"}):
        check("只有 socks= 时拿不到 http/https，回 None", system_proxy(), None)

    with fake_registry({"ProxyEnable": 1, "ProxyServer": ""}):
        check("ProxyServer 是空串回 None", system_proxy(), None)

    with fake_registry({"ProxyEnable": 1, "ProxyServer": "  127.0.0.1:7890  "}):
        check("首尾空白被 strip 掉", system_proxy(), "http://127.0.0.1:7890")

    with fake_registry({}, broken=True):
        check("注册表读不了时安静回 None", system_proxy(), None)

    with fake_registry({"ProxyEnable": 1}):
        check("没有 ProxyServer 这个值时回 None", system_proxy(), None)

    # resolve_proxies
    c = Config(tmp / "proxy.json")
    c.set("proxy_mode", "off")
    check("off 模式不走代理", resolve_proxies(c), None)
    c.set("proxy_mode", "manual")
    c.set("proxy_url", "")
    check("manual 但没填地址 = 直连", resolve_proxies(c), None)
    c.set("proxy_url", "127.0.0.1:7890")
    check("manual 裸地址补 scheme",
          resolve_proxies(c), {"http": "http://127.0.0.1:7890",
                               "https": "http://127.0.0.1:7890"})
    c.set("proxy_url", "socks5://127.0.0.1:1080")
    check("manual 支持 socks",
          resolve_proxies(c), {"http": "socks5://127.0.0.1:1080",
                               "https": "socks5://127.0.0.1:1080"})
    c.set("proxy_mode", "auto")
    check("auto 模式跟着系统代理（本机是什么就返回什么）",
          resolve_proxies(c), (lambda a: {"http": a, "https": a} if a else None)(
              system_proxy()))

    # _retry_after_seconds
    check("正常秒数", _retry_after_seconds(_Resp({"Retry-After": "5"})), 5)
    check("0 抬到下限 1", _retry_after_seconds(_Resp({"Retry-After": "0"})), 1)
    check("负数也抬到 1", _retry_after_seconds(_Resp({"Retry-After": "-5"})), 1)
    check("超大值压到上限 60", _retry_after_seconds(_Resp({"Retry-After": "999"})), 60)
    check("小数取整", _retry_after_seconds(_Resp({"Retry-After": "2.7"})), 2)
    check("非数字用兜底值",
          _retry_after_seconds(_Resp({"Retry-After": "稍等"})), RATE_LIMIT_FALLBACK_WAIT)
    check("没有这个头用兜底值", _retry_after_seconds(_Resp()), RATE_LIMIT_FALLBACK_WAIT)

    # 异常层级：DownloadAborted 故意不是 WallpaperError
    from app.images import DownloadAborted
    check("DownloadAborted 不是业务错误", issubclass(DownloadAborted, WallpaperError), False)
    check("DownloadAborted 是普通异常", issubclass(DownloadAborted, Exception), True)
    check("WallpaperError 能带 detail",
          WallpaperError("说明", "细节").detail, "细节")


# ============================================================== 5. exe 版本资源
def test_version_file() -> None:
    """打包时写进 exe 的版本资源，内容是照着 APP_VERSION 生成的。"""
    print("\n=== 5. exe 版本资源 ===")
    from app.tools.make_version_file import build_text, version_quad

    check("标准三段版本", version_quad("2.0.0"), (2, 0, 0, 0))
    check("四段版本原样保留", version_quad("1.2.3.4"), (1, 2, 3, 4))
    check("两段补零", version_quad("2.1"), (2, 1, 0, 0))
    check("带后缀的按数字部分算", version_quad("2.0.0-beta"), (2, 0, 0, 0))
    check("空串不炸", version_quad(""), (0, 0, 0, 0))
    check("全是字母不炸", version_quad("a.b.c"), (0, 0, 0, 0))
    check("超过四段只取前四段", version_quad("1.2.3.4.5"), (1, 2, 3, 4))

    text = build_text()
    check("版本号写进去了", f"'{APP_VERSION}'" in text, True)
    check("固定版本号写进去了", "(2, 0, 0, 0)" in text or
          f"({', '.join(str(n) for n in version_quad(APP_VERSION))})" in text, True)
    check("产品名写进去了", f"'{APP_NAME}'" in text, True)
    check("生成的是 VSVersionInfo", text.count("VSVersionInfo("), 1)
    check("括号配平（语法层面的最低保证）",
          text.count("(") - text.count(")"), 0)
    check("没有漏掉的占位符", "{" in text or "}" in text, False)
    for key in ("FileVersion", "ProductVersion", "ProductName", "OriginalFilename"):
        check(f"包含 {key}", key in text, True)


# ========================================================== 6. 预览面板纯函数
def test_preview_pane() -> None:
    """预览面板从 `search_tab.py` 拆出来之后，终于能这样测的两个函数。

    这是拆类的主要收益：`meta_text()` 和 `decode_bytes_obj()` 以前埋在
    `SearchTab` 里，想测就得开窗口、走完整条异步链路；现在它们只是两个
    收 dict / 收 bytes 的纯函数，这里一秒就能覆盖掉各种边角输入。
    """
    print("\n=== 6. 预览面板纯函数 ===")
    import io

    from PIL import Image

    import app.ui.preview_pane as pp
    from app.tools.fakes import jpeg_bytes, make_item, png_bytes

    # ---------------------------------------------------------- meta_text
    item = make_item("abc12345", resolution="3840x2160", category="general",
                     file_size=2 * 1024 * 1024, colors=["#111111", "#222222"],
                     favorites=42)
    lines = pp.meta_text(item).splitlines()
    check("有配色时是三行", len(lines), 3)
    check("分类翻了中文", "分类：通用" in lines[0], True)
    check("纯净度在里面", "纯净度：sfw" in lines[0], True)
    check("比例在里面", "比例：16x9" in lines[0], True)
    check("收藏数在里面", "收藏：42" in lines[1], True)
    check("浏览数在里面", "浏览：1000" in lines[1], True)
    check("体积按 MB 显示", "体积：2.00 MB" in lines[1], True)
    check("配色行", lines[2], "配色：#111111  #222222")

    check("没有配色就两行",
          len(pp.meta_text(make_item("x", colors=[])).splitlines()), 2)
    check("配色最多列 5 个",
          pp.meta_text(make_item("x", colors=["#1", "#2", "#3", "#4", "#5", "#6"])
                       ).splitlines()[2], "配色：#1  #2  #3  #4  #5")
    check("认不出的分类原样显示",
          "分类：weird" in pp.meta_text(make_item("x", category="weird")), True)
    check("没有分类显示破折号",
          "分类：—" in pp.meta_text({"id": "x"}), True)
    check("没有 file_size 不炸",
          "体积：0.00 MB" in pp.meta_text({"id": "x"}), True)
    check("file_size 是 None 也不炸",
          "体积：0.00 MB" in pp.meta_text({"id": "x", "file_size": None}), True)
    check("缺 favorites / views 时按 0 算",
          "收藏：0　浏览：0" in pp.meta_text({"id": "x"}), True)
    check("非字符串的配色值也能拼",
          pp.meta_text({"id": "x", "colors": [1, 2]}).splitlines()[2], "配色：1  2")

    # -------------------------------------------------- decode_bytes_obj
    img, over = pp.decode_bytes_obj(jpeg_bytes(40, 30, (10, 20, 30)))
    check("解码出正确的尺寸", img.size, (40, 30))
    check("没超上限就不标记", over, False)
    check("模式规整成 RGB", img.mode, "RGB")

    img, over = pp.decode_bytes_obj(png_bytes(24, 16))
    check("PNG 也能解", img.size, (24, 16))
    check("PNG 没超上限", over, False)

    buf = io.BytesIO()
    Image.new("L", (12, 12), 128).save(buf, format="PNG")
    img, _ = pp.decode_bytes_obj(buf.getvalue())
    check("单通道（L）被转成 RGB", img.mode, "RGB")   # tkinter 只认 RGB/RGBA

    # 像素上限：把上限临时压到 100，不然得现造一张 16MP 的图（又慢又占内存）。
    # 上限是函数里每次现读的模块级常量，所以改它立刻生效。
    old_limit = pp.FULL_PREVIEW_MAX_PIXELS
    pp.FULL_PREVIEW_MAX_PIXELS = 100
    try:
        img, over = pp.decode_bytes_obj(png_bytes(40, 40))     # 1600 像素
        check("超限的会被标记", over, True)
        check("PNG 缩到上限以内", img.width * img.height <= 100, True)
        check("PNG 宽高比没跑偏", img.size, (10, 10))

        img, over = pp.decode_bytes_obj(jpeg_bytes(40, 40))
        check("JPEG 走 draft 后也在上限内", img.width * img.height <= 100, True)
        check("JPEG 也标记为超限", over, True)

        img, over = pp.decode_bytes_obj(jpeg_bytes(8, 8))      # 64 像素
        check("没超限的图不动它", img.size, (8, 8))
        check("没超限时标记为 False", over, False)
    finally:
        pp.FULL_PREVIEW_MAX_PIXELS = old_limit
    check("上限已还原", pp.FULL_PREVIEW_MAX_PIXELS, old_limit)


# ================================================== 7. 断点续传用到的纯函数
def test_download_helpers(tmp: Path) -> None:
    """断点续传那几个纯函数：临时文件名、清理、从响应头算总长。"""
    print("\n=== 7. 下载续传的纯函数 ===")
    from app.images import (
        _expected_total,
        _part_path,
        _prune_stale_parts,
        _total_from_content_range,
    )

    dest = tmp / "dl" / "wall.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- _part_path
    a = _part_path(dest, "http://img/a.jpg")
    check("同一个地址每次算出来一样", _part_path(dest, "http://img/a.jpg"), a)
    check("换个地址就是另一个文件", a == _part_path(dest, "http://img/b.jpg"), False)
    check("就放在目标文件旁边", a.parent, dest.parent)
    check("保留原文件名（人一眼看得出是谁的）", a.name.startswith("wall.jpg."), True)
    check("以 .part 结尾", a.name.endswith(".part"), True)
    check("后缀是 .part，不会被当成真正的图片", a.suffix, ".part")

    # --------------------------------------------------- _prune_stale_parts
    stale = _part_path(dest, "http://img/old.jpg")
    stale.write_bytes(b"x" * 10)
    keep = _part_path(dest, "http://img/a.jpg")
    keep.write_bytes(b"y" * 10)
    other = dest.parent / "别的图.jpg.abc.part"
    other.write_bytes(b"z")
    _prune_stale_parts(dest, keep=keep)
    check("别的地址留下的 .part 被清掉", stale.exists(), False)
    check("这次要用的那个留着", keep.exists(), True)
    check("别的目标文件的 .part 不误伤", other.exists(), True)

    # --------------------------------------------- _total_from_content_range
    check("从 start-end/total 取总长",
          _total_from_content_range("bytes 100-999/1000"), 1000)
    check("416 的写法（* 占位）也能取",
          _total_from_content_range("bytes */1000"), 1000)
    check("没有斜杠取不到", _total_from_content_range("bytes 0-99"), None)
    check("总长位置是 * 时取不到", _total_from_content_range("bytes 0-99/*"), None)
    check("None 取不到", _total_from_content_range(None), None)
    check("空串取不到", _total_from_content_range(""), None)

    # ------------------------------------------------------- _expected_total
    class _R:
        def __init__(self, status: int, headers: dict) -> None:
            self.status_code = status
            self.headers = headers

    check("206：总长直接来自 Content-Range",
          _expected_total(_R(206, {"Content-Range": "bytes 100-999/1000"}), 100), 1000)
    check("206 没有 Content-Range 时用 start+Content-Length",
          _expected_total(_R(206, {"Content-Length": "900"}), 100), 1000)
    check("200：Content-Length 就是全长",
          _expected_total(_R(200, {"Content-Length": "1000"}), 0), 1000)
    check("拿不到长度就返回 0（进度条只显示已下字节）",
          _expected_total(_R(200, {}), 0), 0)
    check("Content-Length 是垃圾值时也返回 0",
          _expected_total(_R(200, {"Content-Length": "稍等"}), 0), 0)


def main() -> int:
    print(f"配置目录：{CONFIG_DIR}")
    if CONFIG_DIR.parent == Path.home() / "AppData" / "Roaming":
        print("拒绝运行：APPDATA 还是真实用户目录。")
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="wp_unit_tmp_"))
    print(f"临时目录：{tmp}\n")
    try:
        test_naming()
        test_config(tmp)
        test_store(tmp)
        test_api(tmp)
        test_version_file()
        test_preview_pane()
        test_download_helpers(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(_TMP_APPDATA, ignore_errors=True)

    print()
    print(f"共 {CHECKS} 项断言")
    print("全部通过" if not FAILS else f"!! 失败 {len(FAILS)} 项：{FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
