"""功能回归测试：保存目录迁移 / 重新下载 / 历史分组 / 缩略图 / 后台任务。

直接跑就行，脚本会把自己重开到一个**临时 APPDATA** 里，绝不碰你真实的配置：

    .venv/Scripts/python.exe app/tools/feature_test.py
    .venv/Scripts/python.exe app/tools/feature_test.py --no-net    # 跳过真联网的两节

`--no-net` 只跳过「真联网重新下载」和「实跑搜索」——这两节是**额外的**真网络
验证。主干用例（含「重新下载」的全部分支）都走 `app/tools/fakes.py` 里的假客户端，
没网也照跑。

退出码 0 表示全部通过。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 控制台编码兜底：Windows 上 stdout 跟着系统代码页走，英文机器是 cp1252，
# 打中文会抛 UnicodeEncodeError 把脚本带崩（CI 上踩过）。只降级 errors，不改 encoding。
for _s in (sys.stdout, sys.stderr):
    if _s is not None and hasattr(_s, "reconfigure"):
        _s.reconfigure(errors="replace")

REEXEC_FLAG = "WP_FEATURE_TEST_ISOLATED"

FAILS: list[str] = []
SKIPS: list[str] = []
# 被 _patch_messagebox 拦下来的错误弹窗。断言"该弹的时候弹了"要用它——
# 只看界面状态证明不了错误提示真的出现过。
ERRORS_SHOWN: list[str] = []
CATCHER: _ErrorCatcher | None = None


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'OK ' if ok else '!! '}{label}: {got!r}"
          + ("" if ok else f"  期望 {want!r}"))
    if not ok:
        FAILS.append(label)


def skip(label: str, why: str) -> None:
    print(f"  -- {label}：跳过（{why}）")
    SKIPS.append(label)


def raises(fn, exc_type) -> bool:
    """跑 `fn`，判断抛的是不是 `exc_type`（子类也算）。抛别的会打出来，方便定位。"""
    try:
        fn()
    except exc_type:
        return True
    except Exception as exc:  # noqa: BLE001 - 就是要看实际抛了什么
        print(f"  ~~ 期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}")
        return False
    print(f"  ~~ 期望 {exc_type.__name__}，实际没抛")
    return False


def _reopen_in_temp_appdata() -> None:
    """把自己重新拉起，APPDATA 指向一个临时目录。"""
    tmp = Path(tempfile.mkdtemp(prefix="wp_feature_test_"))
    env = dict(os.environ)
    env["APPDATA"] = str(tmp)
    env[REEXEC_FLAG] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    print(f"临时 APPDATA：{tmp}\n")
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                           *sys.argv[1:]], env=env)
    shutil.rmtree(tmp, ignore_errors=True)
    raise SystemExit(proc.returncode)


if os.environ.get(REEXEC_FLAG) != "1":
    _reopen_in_temp_appdata()

# 下面这些导入必须在 APPDATA 设好之后才发生
from app.config import APP_NAME, CONFIG_DIR, STORE_PATH, get_config  # noqa: E402
from app.service import (  # noqa: E402
    _record_source_url,
    migrate_wallpapers,
    redownload_wallpaper,
)
from app.store import DownloadStore  # noqa: E402


class _ErrorCatcher(logging.Handler):
    """把 ERROR 级日志全部收集起来。

    **为什么必须有这个**：`UiQueue._poll` 会捕获事件处理器抛出的所有异常，
    只写一条 `log.error("UI 事件处理失败 ...")` 就算完。于是"某个功能实际炸了"
    完全可以不体现在任何界面断言上——界面照常显示、测试全绿，而那段代码每次
    执行都在抛 `NameError`（真发生过：`EVICT_MIN_INTERVAL` 漏了定义，
    导致缩略图缓存清理从来没跑过，测试却一直是绿的）。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.lines: list[str] = []
        self.allowed: list[str] = []

    def expect(self, snippet: str) -> None:
        """声明"接下来会出现一条含 snippet 的 ERROR，是测试故意造的"。

        没有这个机制的话，那些**故意**注入失败的用例（假断网、假磁盘错误）
        会被下面那条"运行期间出现 ERROR 就判失败"的兜底规则误伤。
        """
        self.allowed.append(snippet)

    def emit(self, record: logging.LogRecord) -> None:
        line = f"[{record.name}] {record.getMessage()}"
        for i, snip in enumerate(self.allowed):
            if snip in line:
                del self.allowed[i]
                print(f"  ~~ 预期内的 ERROR（已放行）：{line}")
                return
        self.lines.append(line)


def _watch_errors() -> _ErrorCatcher:
    """盯住程序的 ERROR 级日志。注意要挂在 APP_NAME 那个 logger 上：
    它 `propagate = False`，挂到 root 上收不到。"""
    global CATCHER
    catcher = _ErrorCatcher()
    logging.getLogger(APP_NAME).addHandler(catcher)
    CATCHER = catcher
    return catcher


def expect_error(snippet: str) -> None:
    """在**故意**注入失败的用例之前调用，声明那条 ERROR 是预期的。"""
    if CATCHER is None:
        raise AssertionError("ERROR 监听还没装好，expect_error 调早了")
    CATCHER.expect(snippet)


def _patch_messagebox() -> None:
    """测试里不能弹出任何模态对话框，否则会一直卡在那里等点击。"""
    from tkinter import messagebox

    def _note(kind: str):
        def _fn(title="", message="", **_kw):
            print(f"  [弹窗·{kind}] {title}")
            if kind == "error":
                ERRORS_SHOWN.append(f"{title}：{message}")
            return True
        return _fn

    messagebox.showinfo = _note("info")
    messagebox.showwarning = _note("warning")
    messagebox.showerror = _note("error")
    messagebox.askyesno = _note("askyesno")
    messagebox.askokcancel = _note("askokcancel")


# ===================================================================== 1. 迁移
def test_migrate(tmp: Path) -> None:
    print("=== 1. migrate_wallpapers ===")
    old, new = tmp / "old", tmp / "new"
    (old / "nature").mkdir(parents=True)
    (old / "cyberpunk").mkdir(parents=True)

    # a 正常搬
    (old / "nature" / "a.jpg").write_bytes(b"a" * 100)
    # b 目标已有同大小文件 -> 只改记录，源文件保留
    (old / "cyberpunk" / "b.jpg").write_bytes(b"b" * 200)
    (new / "cyberpunk").mkdir(parents=True)
    (new / "cyberpunk" / "b.jpg").write_bytes(b"b" * 200)
    # c 目标同名但大小不同 -> 重命名后再搬
    (old / "nature" / "c.jpg").write_bytes(b"c" * 50)
    (new / "nature").mkdir(parents=True)
    (new / "nature" / "c.jpg").write_bytes(b"c" * 9999)
    # d 源文件已丢失（只留记录）
    # e 不在旧目录下（用户自己挪过位置）
    (tmp / "elsewhere").mkdir(parents=True)
    (tmp / "elsewhere" / "e.jpg").write_bytes(b"e" * 10)

    store = DownloadStore(tmp / "store.json")
    for iid, kw, name, src in [
        ("a", "nature", "a.jpg", old / "nature" / "a.jpg"),
        ("b", "cyberpunk", "b.jpg", old / "cyberpunk" / "b.jpg"),
        ("c", "nature", "c.jpg", old / "nature" / "c.jpg"),
        ("d", "nature", "d.jpg", old / "nature" / "d.jpg"),
        ("e", "other", "e.jpg", tmp / "elsewhere" / "e.jpg"),
    ]:
        store.upsert({"id": iid, "keyword": kw, "subdir": kw, "filename": name,
                      "path": str(src), "resolution": "1920x1080", "file_size": 100})
    before = {r["id"]: (r["added_at"], r["use_count"]) for r in store.all()}

    stats = migrate_wallpapers(old, new, store)

    # moved 是"真的搬动了"的文件数，renamed 是其中一个子集（改名后搬）
    check("moved（含改名的那 1 个）", stats["moved"], 2)
    check("renamed", stats["renamed"], 1)
    check("already", stats["already"], 1)
    check("missing", stats["missing"], 1)
    check("outside", stats["outside"], 1)
    check("failed", stats["failed"], 0)

    check("a 落到新目录", (new / "nature" / "a.jpg").is_file(), True)
    check("a 旧位置已空", (old / "nature" / "a.jpg").exists(), False)
    check("c 改名避免覆盖", (new / "nature" / "c_1.jpg").is_file(), True)
    check("c 原有文件没被动", (new / "nature" / "c.jpg").read_bytes()[:1], b"c")
    check("c 原有文件长度不变", (new / "nature" / "c.jpg").stat().st_size, 9999)
    check("e 未动", (tmp / "elsewhere" / "e.jpg").is_file(), True)
    check("nature 空目录已清", (old / "nature").exists(), False)
    # b 只是"目标已存在"，源文件不动，所以这个目录不该被清掉
    check("cyberpunk 目录保留（b 的源文件还在）", (old / "cyberpunk").exists(), True)
    check("b 的源文件保留", (old / "cyberpunk" / "b.jpg").is_file(), True)

    after = {r["id"]: (r["added_at"], r["use_count"]) for r in store.all()}
    check("added_at / use_count 未被扰动", after, before)
    check("记录路径已更新", Path(store.get("a")["path"]).name, "a.jpg")
    check("记录路径在新目录下", Path(store.get("a")["path"]).parent.parent, new)
    check("记录指向改名后的文件", Path(store.get("c")["path"]).name, "c_1.jpg")
    check("b 的记录指向新目录", Path(store.get("b")["path"]).parent.parent, new)
    check("丢失的记录保持原路径", Path(store.get("d")["path"]).name, "d.jpg")


# ===================================================================== 2. 重新下载
def test_redownload(tmp: Path) -> None:
    """「重新下载」的全部分支，用假客户端跑，**不联网**。

    这条路径是全项目分支最多的（有 url / 无 url 回查 / 回查没给地址 /
    回查抛错 / 原目录不可用 / 下载失败），以前只有真联网一条路能跑，
    没网时整段跳过 = 最该测的地方反而零覆盖。
    """
    print("\n=== 2. redownload_wallpaper（假客户端）===")
    from app.api import NetworkUnreachable, WallpaperError
    from app.tools.fakes import FakeClient, FakeDownloader, jpeg_bytes, make_item

    cfg = get_config()
    # 别把测试下载的文件写进用户真实的 Pictures\Wallpapers
    cfg.set("save_dir", str(tmp / "fallback_dir"))
    cfg.save()

    store = DownloadStore(tmp / "store2.json")
    item = make_item("abc12345", file_size=4096)
    url = item["path"]
    dest = tmp / "wallpapers" / "nature" / f"nature_{item['id']}.jpg"
    store.upsert({"id": item["id"], "keyword": "nature", "subdir": "nature",
                  "filename": dest.name, "path": str(dest),
                  "resolution": item.get("resolution"),
                  "url": url, "file_size": 0})

    # (a) 记录里有 url —— 直接用，不该去问 API
    client = FakeClient(single=item)
    check("有 url 时直接用记录里的", _record_source_url(store.get(item["id"]), client), url)
    check("有 url 时没去回查 API", client.fetch_calls, [])

    # (b) 模拟老记录：删掉 url，回查 API
    old_rec = dict(store.get(item["id"]))
    old_rec.pop("url", None)
    check("无 url 时回查 API 拿到同一个地址",
          _record_source_url(old_rec, client), url)
    check("确实回查了一次", len(client.fetch_calls), 1)

    # (c) 文件被删掉后重新下载
    downloader = FakeDownloader(jpeg_bytes(64, 64))
    check("下载前文件不存在", dest.exists(), False)
    rec = redownload_wallpaper(store.get(item["id"]), cfg, client, downloader, store)
    check("重新下载后文件存在", dest.is_file(), True)
    check("记录里的 file_size 已更新", rec.get("file_size"), len(downloader.payload))
    check("实际大小与记录一致", rec["file_size"], dest.stat().st_size)
    check("use_count 没被顶上去", store.get(item["id"])["use_count"], 1)
    check("下载用的就是记录里那个地址", downloader.file_calls[0][0], url)

    # (d) 原目录不可用 -> 退回当前保存目录
    bad = dict(store.get(item["id"]))
    bad["path"] = "Z:/不存在的盘/x.jpg"
    bad["keyword"] = "nature"
    rec2 = redownload_wallpaper(bad, cfg, client, downloader, store)
    check("回退到当前保存目录", Path(rec2["path"]).is_relative_to(cfg.save_dir), True)
    check("回退后文件真的在", Path(rec2["path"]).is_file(), True)

    # (e) 回查的三个失败分支
    check("回查抛错时原样抛出",
          raises(lambda: _record_source_url(
              old_rec, FakeClient(fetch_error=NetworkUnreachable("断网"))),
              NetworkUnreachable), True)
    check("回查结果里没有 path 时报错",
          raises(lambda: _record_source_url(old_rec, FakeClient(single={"id": "x"})),
                 WallpaperError), True)
    check("记录连 id 都没有时报错",
          raises(lambda: _record_source_url({"path": ""}, client), WallpaperError), True)

    # (f) 下载失败 -> 记录不该被写坏
    before = store.get(item["id"])["file_size"]
    check("下载失败会抛出",
          raises(lambda: redownload_wallpaper(
              store.get(item["id"]), cfg, client,
              FakeDownloader(error=NetworkUnreachable("下载中断")), store),
              NetworkUnreachable), True)
    check("失败后 file_size 没被改", store.get(item["id"])["file_size"], before)

    # (g) 状态回调（历史页靠它显示进度）
    stages: list[str] = []
    redownload_wallpaper(store.get(item["id"]), cfg, client, downloader, store,
                         on_stage=stages.append)
    check("进度回调被调过", len(stages) > 0, True)
    print(f"  回调文案：{stages}")


# =================================================================== 2b. 真联网
def test_redownload_live(tmp: Path) -> None:
    """真去 wallhaven 搜一张最小的下下来——验证真实现的接口没被假实现掩盖。

    分支覆盖交给上面那节，这里只负责"真网络 + 真磁盘"这一条。
    """
    print("\n=== 2b. redownload_wallpaper（真联网，可选）===")
    cfg = get_config()
    cfg.set("save_dir", str(tmp / "fallback_dir"))
    cfg.save()
    from app.api import WallhavenClient
    from app.images import Downloader

    client = WallhavenClient(cfg)
    downloader = Downloader(cfg)
    store = DownloadStore(tmp / "store2b.json")

    try:
        result = client.search(keyword="nature", page=1, atleast="1920x1080",
                               ratios="16x9", sorting="favorites")
    except Exception as exc:  # noqa: BLE001
        skip("真联网重新下载", f"搜索失败（网络/代理）：{exc}")
        return
    items = result.get("items") or []
    if not items:
        skip("真联网重新下载", "搜索没有结果")
        return
    item = min(items, key=lambda it: int(it.get("file_size") or 0))
    print(f"  选了一张 {item['id']}  {item.get('resolution')}  "
          f"{int(item.get('file_size') or 0) / 1024:.0f} KB")

    dest = tmp / "wallpapers_live" / "nature" / f"nature_{item['id']}.jpg"
    store.upsert({"id": item["id"], "keyword": "nature", "subdir": "nature",
                  "filename": dest.name, "path": str(dest),
                  "resolution": item.get("resolution"),
                  "url": item.get("path"), "file_size": 0})

    # 假客户端的 fetch_json 是编的，真客户端这一支只有联网才跑得到
    old_rec = dict(store.get(item["id"]))
    old_rec.pop("url", None)
    check("真客户端回查 API 也拿到同一个地址",
          _record_source_url(old_rec, client), item.get("path"))

    rec = redownload_wallpaper(store.get(item["id"]), cfg, client, downloader, store)
    check("真下载后文件存在", dest.is_file(), True)
    check("实际大小与记录一致", rec["file_size"], dest.stat().st_size)
    check("下载到的不是空文件", rec["file_size"] > 0, True)


# ===================================================================== 3. 界面
def seed_store(base_dir: Path) -> None:
    """造 4 条记录：h1 的文件真实存在，其余都是"已丢失"。"""
    base_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {"id": "h1", "keyword": "cyberpunk", "subdir": "cyberpunk",
         "filename": "cyberpunk_1.jpg", "path": str(base_dir / "cyberpunk_1.jpg"),
         "resolution": "3840x2160", "file_size": 3 * 1024 * 1024,
         "added_at": "2026-09-15T10:00:00"},
        {"id": "h2", "keyword": "cyberpunk", "subdir": "cyberpunk",
         "filename": "cyberpunk_2.jpg", "path": str(base_dir / "cyberpunk_2.jpg"),
         "resolution": "1920x1080", "file_size": 1 * 1024 * 1024,
         "added_at": "2026-09-15T11:00:00"},
        {"id": "h3", "keyword": "nature", "subdir": "nature",
         "filename": "nature_1.jpg", "path": str(base_dir / "nature_1.jpg"),
         "resolution": "2560x1440", "file_size": 2 * 1024 * 1024,
         "added_at": "2026-09-15T12:00:00"},
        {"id": "h4", "keyword": "", "subdir": "wallpaper",
         "filename": "wallpaper_x.jpg", "path": str(base_dir / "wallpaper_x.jpg"),
         "resolution": "1920x1080", "file_size": 1 * 1024 * 1024,
         "added_at": "2026-09-15T09:00:00"},
    ]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps({"version": 1, "items": records},
                                     ensure_ascii=False), encoding="utf-8")
    (base_dir / "cyberpunk_1.jpg").write_bytes(b"j" * 4096)


def test_ui(tmp: Path) -> None:
    print("\n=== 3. 历史分组 ===")
    from app.ui.app import WallpaperPickerApp

    cfg = get_config()
    old_dir = tmp / "wp_old"
    cfg.set("save_dir", str(old_dir))
    cfg.set("history_grouped", False)
    cfg.save()
    seed_store(old_dir)

    app = WallpaperPickerApp(cfg)

    def pump(sec: float) -> None:
        end = time.time() + sec
        while time.time() < end:
            app.update()
            time.sleep(0.03)

    pump(1.0)
    ht = app.history_tab

    roots = ht.tree.get_children()
    check("默认不分组，4 条平铺", len(roots), 4)
    check("分组开关默认关", ht.var_grouped.get(), False)

    ht.var_grouped.set(True)
    ht._on_group_toggle()
    pump(0.5)

    roots = ht.tree.get_children()
    labels = [ht.tree.item(r, "values")[1] for r in roots]
    print(f"  分组节点：{labels}")
    check("分成 3 组", len(roots), 3)
    check("cyberpunk 组在前", labels[0].startswith("▾ cyberpunk"), True)
    check("未记录关键词排最后", labels[-1].startswith("▾ （未记录关键词）"), True)
    check("组标题带丢失数", "1 个已丢失" in labels[0], True)
    check("cyberpunk 组有 2 个孩子", len(ht.tree.get_children(roots[0])), 2)
    check("组行不计入 _rows", "grp::cyberpunk" in ht._rows, False)
    check("图片行计入 _rows", len(ht._rows), 4)
    check("计数文案含关键词数", "3 个关键词" in ht.lbl_count.cget("text"), True)
    check("计数文案含丢失数", "3 个文件已丢失" in ht.lbl_count.cget("text"), True)

    # 选中分组行 -> 按钮应禁用
    ht.tree.selection_set(roots[0])
    pump(0.2)
    check("选中分组行时按钮禁用", str(ht.btn_redownload["state"]), "disabled")
    check("选中分组行无记录", ht._selected_record(), None)

    # cyberpunk 组里第一条是 h2（added_at 倒序），文件已丢失
    child = ht.tree.get_children(roots[0])[0]
    check("组内第一条是已丢失的 h2", child, "h2")
    ht.tree.selection_set(child)
    pump(0.2)
    check("选中丢失记录时按钮可用", str(ht.btn_redownload["state"]), "normal")
    check("路径提示提到已丢失", "已丢失" in ht.lbl_path.cget("text"), True)

    # 选中存在的记录 -> 显示大小
    cy = [r for r in ht.tree.get_children()
          if ht.tree.item(r, "values")[1].startswith("▾ cyberpunk")][0]
    ht.tree.selection_set("h1")
    pump(0.2)
    check("h1 挂在 cyberpunk 组下", ht.tree.parent("h1"), cy)
    check("存在的文件显示大小", "MB）" in ht.lbl_path.cget("text"), True)

    # 关掉分组应回到平铺
    ht.var_grouped.set(False)
    ht._on_group_toggle()
    pump(0.5)
    check("关掉分组回到 4 条", len(ht.tree.get_children()), 4)
    check("配置已持久化", cfg.get("history_grouped"), False)

    # ---------------------------------------------------- 增量新增（不重建整表）
    print("\n=== 3b. 历史页增量新增 ===")
    fresh = tmp / "wp_old" / "space_9.jpg"
    fresh.write_bytes(b"x" * 2048)
    new_rec = {"id": "h9", "keyword": "space", "subdir": "space",
               "filename": "space_9.jpg", "path": str(fresh),
               "added_at": "2026-09-15T22:00:00", "file_size": 2048}
    # 平铺视图按 added_at 倒序：h3(12:00) h2(11:00) h1(10:00) h4(09:00)
    check("增量前的顺序", list(ht.tree.get_children()), ["h3", "h2", "h1", "h4"])

    app.store.upsert(new_rec)
    ht.add_record(app.store.get("h9"))
    pump(0.4)
    check("新增一条后是 5 行", len(ht.tree.get_children()), 5)
    check("新记录插在最前面（added_at 最新）",
          list(ht.tree.get_children()), ["h9", "h3", "h2", "h1", "h4"])
    check("新记录进了 _rows", "h9" in ht._rows, True)
    check("计数文案已更新", ht.lbl_count.cget("text"), "共 5 条")
    check("空列表提示已清掉", ht.lbl_path.cget("text"), "")

    # 已存在的记录原地替换：不该新增行，也不该被挪到最前面
    # （store 里 added_at 保留的是首次加入时间，排序位置本来就不该变）
    ht.add_record(app.store.get("h2"))
    pump(0.3)
    check("重复记录不新增行",
          list(ht.tree.get_children()), ["h9", "h3", "h2", "h1", "h4"])
    check("重复记录仍是 5 条", ht.lbl_count.cget("text"), "共 5 条")

    app.store.remove("h9")
    fresh.unlink()
    ht.refresh()
    pump(0.3)
    check("清掉临时记录后回到 4 条", len(ht.tree.get_children()), 4)

    # 这个处理器只在"整批缩略图任务失败"时才跑，平时永远不会被触发——
    # 直接调一次，至少保证它本身不炸（写错名字在这里就会暴露）
    ht._on_batch_err(RuntimeError("测试用的假失败"))
    check("批次失败处理器没抛异常", True, True)

    # ================================================ 3c. 界面里的重新下载（假客户端）
    print("\n=== 3c. 历史页「重新下载」全流程（假客户端，不联网）===")
    from app.api import NetworkUnreachable
    from app.tools.fakes import FakeClient, FakeDownloader, jpeg_bytes, make_item, use_fakes

    lost = app.store.get("h2")
    lost_path = Path(str(lost["path"]))
    check("h2 的文件当前不在", lost_path.exists(), False)
    check("h2 记录里没有 url（会走回查分支）", bool(lost.get("url")), False)

    fake_client = FakeClient(single=make_item("h2", url="https://example.invalid/h2.jpg"))
    fake_dl = FakeDownloader(jpeg_bytes(48, 48))
    ht.tree.selection_set("h2")
    pump(0.2)

    # 换掉 app.client / app.downloader 就够了：history_tab 是在工作线程里
    # 现取 self.app.client，没有缓存副本
    with use_fakes(app, fake_client, fake_dl):
        ht.redownload_selected()
        done = False
        for _ in range(80):
            pump(0.1)
            if lost_path.is_file():
                done = True
                break
        pump(0.4)

    check("重新下载把文件放回了原位", done, True)
    check("确实走了 API 回查", len(fake_client.fetch_calls), 1)
    check("下载用的是回查回来的地址",
          fake_dl.file_calls[0][0], "https://example.invalid/h2.jpg")
    check("记录里的 file_size 已更新",
          app.store.get("h2")["file_size"], len(fake_dl.payload))
    # 成功后 _on_redownload_ok 会整表 refresh()，选中行被清掉 -> 按钮随之禁用。
    # 这不是 bug（按钮本来就只对选中行生效），但确实少了一步：想接着点
    # 「设为壁纸」得重新选一次。记在清单 P3 里，这轮不动。
    check("刷新后没有选中行", ht.tree.selection(), ())
    check("没选中时按钮禁用", str(ht.btn_redownload["state"]), "disabled")
    check("状态栏说了已重新下载", "已重新下载" in app.lbl_status.cget("text"), True)
    check("失败弹窗一次都没弹", ERRORS_SHOWN, [])

    # 假下载器抛错 -> 走 _on_redownload_err，弹窗被 _patch_messagebox 记下来
    lost_path.unlink()
    expect_error("测试用的假断网")      # 这条 ERROR 是我们故意造的
    with use_fakes(app, fake_client,
                   FakeDownloader(error=NetworkUnreachable("测试用的假断网"))):
        ht.tree.selection_set("h2")
        pump(0.2)
        ht.redownload_selected()
        for _ in range(40):
            pump(0.1)
            if ERRORS_SHOWN:
                break
        pump(0.2)

    check("下载失败时弹了错误框", len(ERRORS_SHOWN), 1)
    check("按钮也被放回可用", str(ht.btn_redownload["state"]), "normal")
    ERRORS_SHOWN.clear()

    # ============================================ 3d. 预览面板：迟到的结果不覆盖
    print("\n=== 3d. 预览面板：迟到的缩略图不该顶掉当前选中 ===")
    from app.tools.fakes import FakeDownloader, jpeg_bytes, make_item, use_fakes

    pv = app.search_tab.preview
    # 两张图**尺寸不同**——"画布上现在是谁"才一眼能看出来（40×40 vs 60×60）
    raw_a = jpeg_bytes(40, 40, (255, 0, 0))
    raw_b = jpeg_bytes(60, 60, (0, 0, 255))
    item_a = make_item("pva")
    item_b = make_item("pvb")

    down = FakeDownloader(raw_b)
    with use_fakes(app, downloader=down):
        check("预览面板用的是注入的假下载器（不是 __init__ 里存的那份）",
              pv.downloader is down, True)
        pv.show_item(item_b)
        pv.cancel_pending_timers()      # 别让它真去下"原图"
        for _ in range(30):
            pump(0.1)
            if pv.canvas.has_image():
                break
        check("B 的缩略图显示出来了", pv.canvas.image_size(), (60, 60))

        # A 的缩略图任务迟到了（用户在它回来之前就点了 B）——必须丢掉。
        # 这里**真把 A 的 job 跑一遍**，而不是手搓一个假结果：顺带验证了
        # job 闭包里取的是 A 自己的 url（40×40 那张，不是 B 的 60×60）。
        down.payload = raw_a
        late_iid, late_img = pv._thumb_job("pva", item_a["thumbs"]["large"])()
        check("迟到结果带着 A 的 id 和 A 的图",
              (late_iid, late_img.size), ("pva", (40, 40)))
        pv._on_thumb_loaded((late_iid, late_img))
        check("迟到的 A 被忽略，画布还是 B", pv.canvas.image_size(), (60, 60))

        # 闭包要在**提交时**把 id 绑好。这一条是回归：原来闭包里读的是
        # self._selected，任务在池子里排队时用户已经切走了，回来就会
        # "新的图位显示旧的图"。
        check("闭包里的 id 是提交时那个（不是当前选中的）",
              pv._thumb_job("pva", item_a["thumbs"]["large"])()[0], "pva")
        check("此刻面板上确实是另一张", pv._iid, "pvb")

    pv.reset()
    check("reset 之后画布空了", pv.canvas.has_image(), False)

    # ============================================================ 4. 换目录触发迁移
    print("\n=== 4. 设置页换保存目录 -> 自动迁移 ===")
    ht.var_grouped.set(True)
    ht._on_group_toggle()
    pump(0.3)

    new_dir = tmp / "wp_moved"
    before_use = app.store.get("h1").get("use_count")
    st = app.settings_tab
    st.var_save_dir.set(str(new_dir))
    st.save_to_config()

    for _ in range(60):
        pump(0.25)
        if (new_dir / "cyberpunk_1.jpg").is_file():
            break
    pump(0.5)

    check("配置里的保存目录已更新", Path(cfg.save_dir), new_dir)
    check("文件已搬到新目录", (new_dir / "cyberpunk_1.jpg").is_file(), True)
    check("旧位置已清空", (old_dir / "cyberpunk_1.jpg").exists(), False)
    moved_rec = app.store.get("h1")
    check("历史记录路径已更新", Path(moved_rec["path"]), new_dir / "cyberpunk_1.jpg")
    check("迁移后 added_at 未变", moved_rec["added_at"], "2026-09-15T10:00:00")
    check("迁移后 use_count 未变", moved_rec.get("use_count"), before_use)

    # 关窗时要把还挂着的 after 定时器取消掉。不取消的话窗口 destroy() 之后
    # 它们照样触发，Tcl 打一串 `invalid command name "..."`——纯噪音，
    # 但每次跑测试都会看到，很容易被当成"哪里坏了"。
    st.save_to_config()          # 会排一个 2.5 秒后清掉"已保存 ✓"的定时器
    check("保存后排了清理定时器", st._saved_job is not None, True)
    check("事件轮询定时器在跑", app.ui._job is not None, True)
    app._on_close()
    check("关窗后设置页的定时器已取消", st._saved_job, None)
    check("关窗后事件轮询定时器已取消", app.ui._job, None)


# ===================================================================== 5. 缩略图
def test_thumbs(tmp: Path) -> None:
    """缩略图链路：缓存命中 / decode 在工作线程跑 / 单张失败不拖垮整批。

    不联网：全部靠预先塞进缓存目录的文件触发，downloader 直接传 None——
    真发了请求会立刻崩，等于顺手验证了「命中缓存不发网络请求」。
    """
    print("\n=== 5. 缩略图解码 ===")
    from PIL import Image

    from app.config import THUMB_CACHE_DIR
    from app.images import fetch_thumbs, thumb_cache_path
    from app.ui.widgets import open_scaled

    # --- open_scaled 本身
    big = tmp / "big.jpg"
    Image.new("RGB", (3840, 2160), (30, 60, 90)).save(big, quality=90)
    img = open_scaled(big, 240, 135)
    check("open_scaled 结果不超出框", (img.width, img.height), (240, 135))
    check("open_scaled 模式已规整为 RGB", img.mode, "RGB")

    png = tmp / "plain.png"
    Image.new("RGB", (1000, 500), (200, 10, 10)).save(png)
    img2 = open_scaled(png, 240, 135)
    check("open_scaled 对 PNG 也正常（draft 是空操作）",
          (img2.width, img2.height), (240, 120))

    # --- fetch_thumbs 命中缓存 + decode
    THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    iid = "testthumb01"
    dst = thumb_cache_path(iid)
    Image.new("RGB", (640, 400), (10, 200, 10)).save(dst, quality=85)

    got: list[tuple] = []
    fetch_thumbs(
        [{"id": iid}], None,
        on_done=lambda i, val, err: got.append((i, val, err)),
        decode=lambda p: open_scaled(p, 240, 135),
    )
    check("命中缓存也回调（这种情况不需要 url）", len(got), 1)
    check("回调拿到的是 decode 的返回值", isinstance(got[0][1], Image.Image), True)
    # 源图 640x400（8:5）装进 240x135（16:9）的框，只能按高度贴：240/1.6=150 超高，
    # 于是变成 216x135。这不是凑数字，是确认「等比缩放」真的生效
    check("回调里的图已经是解码好的尺寸",
          (got[0][1].width, got[0][1].height), (216, 135))
    check("命中缓存没有错误", got[0][2], None)

    # --- 不传 decode 时保持旧行为：回调拿路径
    raw: list[tuple] = []
    fetch_thumbs([{"id": iid}], None,
                 on_done=lambda i, val, err: raw.append((i, val, err)))
    check("不传 decode 时回调拿到路径", raw[0][1], dst)

    # --- 单张解码失败：抛异常换成 err 回调，不炸整批
    boom: list[tuple] = []

    def _explode(_path):
        raise RuntimeError("解码故意失败")

    fetch_thumbs([{"id": iid}], None,
                 on_done=lambda i, val, err: boom.append((i, val, err)),
                 decode=_explode)
    check("解码失败不往外抛，走 err 回调", len(boom), 1)
    check("解码失败时图是 None", boom[0][1], None)
    check("err 就是那个异常", isinstance(boom[0][2], RuntimeError), True)

    # --- 既没缓存也没 url：静默跳过，不该回调空数据
    nothing: list[tuple] = []
    fetch_thumbs([{"id": "testthumb_missing"}], None,
                 on_done=lambda i, val, err: nothing.append((i, val, err)))
    check("没缓存也没 url 时跳过（不回调）", len(nothing), 0)


# ===================================================================== 6. 实跑
def test_live_search(tmp: Path) -> None:
    """联网实跑一次搜索，确认缩略图真的进了格子，而且解码不在主线程。

    这是 P1「缩略图解码搬出主线程」唯一能验证到位的方式：给
    `search_tab.open_scaled` 装个探针，记录每次调用发生在哪个线程。
    """
    print("\n=== 6. 实跑搜索（联网） ===")
    import threading

    import app.ui.search_tab as st
    from app.ui.app import WallpaperPickerApp

    cfg = get_config()
    cfg.set("save_dir", str(tmp / "wp_live"))
    cfg.save()

    real_open_scaled = st.open_scaled
    calls: list[str] = []

    def spy(path, width, height):
        calls.append(threading.current_thread().name)
        return real_open_scaled(path, width, height)

    st.open_scaled = spy
    app = WallpaperPickerApp(cfg)

    def pump_until(cond, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            app.update()
            time.sleep(0.03)
            if cond():
                return True
        return False

    try:
        app.update()
        grid = app.search_tab.grid_view
        if grid._cols == 0:
            skip("实跑搜索", "窗口没拿到宽度，格子没布局（无头环境）")
            return

        app.search_tab.var_keyword.set("nature")
        app.search_tab.do_search(reset=True)

        ok = pump_until(lambda: grid.count() > 0, 30.0)
        check("搜索返回了结果", ok, True)
        if not ok:
            return
        print(f"  结果数：{grid.count()}")

        got_photo = pump_until(lambda: len(grid._photos) > 0, 40.0)
        check("缩略图真的渲染进格子了", got_photo, True)

        # 等这一页出完：连续 2 秒没有新增就算结束
        deadline = time.time() + 60.0
        last, stable_since = -1, time.time()
        while time.time() < deadline:
            app.update()
            time.sleep(0.03)
            if len(grid._photos) != last:
                last, stable_since = len(grid._photos), time.time()
            elif time.time() - stable_since > 2.0:
                break
        total = grid.count()
        print(f"  出图 {len(grid._photos)}/{total}")
        # 容忍个别下载失败（网络抖动），但不接受"只有前几张能出图"
        check("整页缩略图基本都出图了", len(grid._photos) >= total - 3, True)

        # 解码是在 _load_photos 起的那个批次任务里串行做的（缩略图线程池只负责
        # 下载，decode 在收集结果时调用）。关键是它不在主线程。
        check("确实调过解码", len(calls) > 0, True)
        check("解码没发生在主线程", "MainThread" in calls, False)
        # 顺便确认后台任务确实跑在新的线程池里（线程名 bg0..bgN），
        # 而不是以前那种"一次 run_bg 起一个 Thread-N"
        check("后台任务跑在 bg 线程池里",
              all(t.startswith("bg") for t in calls), True)
        print(f"  解码线程：{sorted(set(calls))}，共 {len(calls)} 次")

        # 这两个处理器只在失败路径上跑，平时碰不到，直接调一次保证它们本身没写错
        app.search_tab._on_thumb_batch_err(RuntimeError("测试用的假失败"))
        for handler in app.ui._handlers.get("evict_err", []):
            handler(RuntimeError("测试用的假失败"))
        check("失败路径的处理器都没抛异常", True, True)
    finally:
        st.open_scaled = real_open_scaled
        app._on_close()


# ===================================================================== 7. 后台任务
def test_background(tmp: Path) -> None:
    """后台线程池的上限、下载的中断开关、原图预览的像素上限。全部离线。"""
    print("\n=== 7. 后台任务与预览内存 ===")
    import io
    import threading

    from PIL import Image

    from app.api import WallpaperError
    from app.images import DownloadAborted, Downloader
    from app.ui.async_util import TaskPool

    # ---------------------------------------------------------- 线程池上限
    pool = TaskPool(3)
    lock = threading.Lock()
    running = peak = 0
    release = threading.Event()

    def _job() -> None:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        release.wait(5.0)
        with lock:
            running -= 1

    for _ in range(12):
        pool.submit(_job)
    time.sleep(0.6)
    check("并发数不超过池子大小", peak, 3)
    release.set()
    pool.close()

    # 线程必须是 daemon。非 daemon 的话解释器退出时会 join 它们：用户关窗时
    # 如果还有下载在跑，进程会一直挂到下载超时（最长 60 秒）才退。
    time.sleep(0.2)
    bg = [t for t in threading.enumerate() if t.name.startswith("bg")]
    check("池子里的线程都是 daemon", bool(bg) and all(t.daemon for t in bg), True)

    # 量一下真实的退出耗时。同进程里量不到"解释器退出"这一段，只能开子进程。
    script = (
        "import sys, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from app.config import get_config\n"
        "from app.ui.app import WallpaperPickerApp\n"
        "app = WallpaperPickerApp(get_config())\n"
        "app.ui.run_bg(lambda: time.sleep(30))   # 假装有个 30 秒的下载在跑\n"
        "app.after(400, app._on_close)\n"
        "app.mainloop()\n"
    )
    started = time.time()
    try:
        proc = subprocess.run([sys.executable, "-c", script, str(ROOT)],
                              capture_output=True, timeout=20)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = "超时"
    elapsed = time.time() - started
    print(f"  有长任务在跑时关窗，进程退出耗时 {elapsed:.1f}s")
    check("关窗立刻退出（没被长任务拖住）", code == 0 and elapsed < 10, True)

    # ---------------------------------------------------------- 下载中断开关
    class _Resp:
        def __init__(self, chunks, owner):
            self._chunks = chunks
            self._owner = owner
            self.status_code = 200

        def iter_content(self, _n):
            for chunk in self._chunks:
                self._owner.served += 1
                yield chunk

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    class _Session:
        def __init__(self, chunks):
            self.chunks = chunks
            self.calls = 0
            self.served = 0
            self.kwargs: dict = {}

        def get(self, _url, **kw):
            self.calls += 1
            self.kwargs = kw
            return _Resp(self.chunks, self)

    dl = Downloader(get_config())

    sess = _Session([b"aaa", b"bbb", b"ccc"])
    dl._local.session = sess            # session 是线程内私有的，直接塞进去
    check("不中断时拿全量数据", dl.get_bytes("http://x", should_abort=lambda: False),
          b"aaabbbccc")
    check("带中断开关时走流式下载", sess.kwargs.get("stream"), True)

    # 开连接之前就发现用户已经切走了
    sess2 = _Session([b"aaa"])
    dl._local.session = sess2
    raised: Exception | None = None
    try:
        dl.get_bytes("http://x", should_abort=lambda: True)
    except DownloadAborted as exc:
        raised = exc
    check("切走之后根本不开连接", sess2.calls, 0)
    check("抛的是 DownloadAborted", isinstance(raised, DownloadAborted), True)

    # 下到一半切走
    sess3 = _Session([b"aaa", b"bbb", b"ccc"])
    dl._local.session = sess3
    asked = 0

    def _abort_after_first_chunk() -> bool:
        nonlocal asked
        asked += 1
        return asked > 2                # 第 1 次是开连接前的预检
    raised = None
    try:
        dl.get_bytes("http://x", should_abort=_abort_after_first_chunk)
    except DownloadAborted as exc:
        raised = exc
    # 中断检查只能发生在"收到一块之后"——没法在收到之前判断。所以第 2 块会被
    # 拉下来才发现该停。关键是第 3 块没下：整张图没下完，目的就达到了。
    check("下到一半就停了（3 块只下了 2 块）", sess3.served, 2)
    check("中途放弃也抛 DownloadAborted", isinstance(raised, DownloadAborted), True)
    check("中途放弃没被包装成网络错误",
          isinstance(raised, WallpaperError), False)

    # ---------------------------------------------------------- 预览像素上限
    # 这两个东西现在住在 app/ui/preview_pane.py（第 17 条把预览面板拆出去了）
    import app.ui.preview_pane as pp

    real_cap = pp.FULL_PREVIEW_MAX_PIXELS

    def _png(w: int, h: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
        return buf.getvalue()

    def _jpeg(w: int, h: int) -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="JPEG", quality=80)
        return buf.getvalue()

    pp.FULL_PREVIEW_MAX_PIXELS = 100_000     # 600x600 = 360000 像素，必须缩
    try:
        img, clamped = pp.decode_bytes_obj(_png(600, 600))
        check("超过上限的 PNG 被缩到上限内", img.width * img.height <= 100_000, True)
        check("缩完仍是正方形（宽高比没跑偏）", (img.width, img.height), (316, 316))
        check("返回的标记说明源图超限", clamped, True)

        img, clamped = pp.decode_bytes_obj(_jpeg(600, 600))
        check("JPEG 走 draft 降采样后也落在上限内",
              img.width * img.height <= 100_000, True)
        check("JPEG 也标记为源图超限", clamped, True)
    finally:
        pp.FULL_PREVIEW_MAX_PIXELS = real_cap

    img, clamped = pp.decode_bytes_obj(_jpeg(80, 40))
    check("没超上限的图不动它", (img.width, img.height), (80, 40))
    check("没超上限时标记为 False", clamped, False)


# ===================================================================== 8. 断点续传
class _RangeResp:
    """假的响应对象。按 owner 的设定吐数据，能中途断线。"""

    def __init__(self, payload: bytes, status: int, headers: dict, owner: _Source) -> None:
        self._payload = payload
        self.status_code = status
        self.headers = headers
        self._owner = owner

    def iter_content(self, _n: int):
        served = 0
        step = self._owner.chunk
        for i in range(0, len(self._payload), step):
            served += 1
            if self._owner.break_after is not None and served > self._owner.break_after:
                raise requests.exceptions.ConnectionError("测试用的假断线")
            piece = self._payload[i:i + step]
            self._owner.served += len(piece)
            yield piece

    def __enter__(self) -> _RangeResp:
        return self

    def __exit__(self, *_a) -> bool:
        return False


class _Source:
    """假的原图服务器：认识 Range，能中途断线，也能装作不认识 Range。"""

    def __init__(self, body: bytes, *, break_after: int | None = None,
                 ignore_range: bool = False, force_status: int | None = None,
                 chunk: int = 1024) -> None:
        self.body = body
        self.break_after = break_after
        self.ignore_range = ignore_range
        self.force_status = force_status
        self.chunk = chunk
        self.requests: list[str | None] = []      # 每次请求带的 Range 头
        self.statuses: list[int] = []
        self.served = 0                           # 累计吐出去的字节数

    def get(self, _url: str, **kw):
        rng = (kw.get("headers") or {}).get("Range")
        self.requests.append(rng)
        start = int(rng.split("=", 1)[1].rstrip("-")) if rng else 0
        if self.force_status is not None:
            self.statuses.append(self.force_status)
            return _RangeResp(b"", self.force_status, {}, self)
        if self.ignore_range:
            start = 0
        total = len(self.body)
        if start and start >= total:
            # 资源不比我们手里的 .part 长，只能 416。总长度写在 `*` 的位置。
            self.statuses.append(416)
            return _RangeResp(b"", 416, {"Content-Range": f"bytes */{total}"}, self)
        if start:
            self.statuses.append(206)
            return _RangeResp(self.body[start:], 206, {
                "Content-Range": f"bytes {start}-{total - 1}/{total}",
                "Content-Length": str(total - start),
            }, self)
        self.statuses.append(200)
        return _RangeResp(self.body, 200, {"Content-Length": str(total)}, self)


def test_resume(tmp: Path) -> None:
    """断点续传：中断之后不从头再来。全部离线。"""
    print("\n=== 8. 断点续传 ===")
    from app.api import NetworkUnreachable, WallpaperError
    from app.images import Downloader, _part_path

    dl = Downloader(get_config())
    body = bytes(range(256)) * 40                  # 10240 字节，分 10 块
    home = tmp / "resume"
    home.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- 一次下完
    src = _Source(body)
    dl._local.session = src
    out = home / "wall1.jpg"
    got = dl.download_to_file("http://img/a.jpg", out)
    check("下完的文件内容正确", got.read_bytes() == body, True)
    check("第一次请求没带 Range", src.requests, [None])
    check("没有 .part 残留", [p.name for p in home.glob("*.part")], [])

    # ------------------------------------------------- 中途断线：保留已下的部分
    out2 = home / "wall2.jpg"
    dl._local.session = _Source(body, break_after=2)
    raised = None
    try:
        dl.download_to_file("http://img/b.jpg", out2)
    except NetworkUnreachable as exc:
        raised = exc
    check("断线被分类成网络错误", isinstance(raised, NetworkUnreachable), True)
    part = _part_path(out2, "http://img/b.jpg")
    check("目标文件还没出现", out2.exists(), False)
    check(".part 留下来了", part.is_file(), True)
    kept = part.stat().st_size
    check("留下的正好是下到的那一段（0 < kept < 全长）", 0 < kept < len(body), True)
    check(".part 内容是原文件的前缀", part.read_bytes() == body[:kept], True)

    # ------------------------------------------------- 再来一次：只补剩下的
    src3 = _Source(body)
    dl._local.session = src3
    dl.download_to_file("http://img/b.jpg", out2)
    check("第二次请求带了 Range", src3.requests[0], f"bytes={kept}-")
    check("服务器回的是 206", src3.statuses, [206])
    check("续传后只补了剩下的字节", src3.served, len(body) - kept)
    check("续传后内容完全正确", out2.read_bytes() == body, True)
    check("续传完成后 .part 已消失", part.exists(), False)

    # ------------------------------------------------- 进度从断点处开始
    out3 = home / "wall3.jpg"
    _part_path(out3, "http://img/c.jpg").write_bytes(body[:4096])
    dl._local.session = _Source(body)
    seen: list[tuple[int, int]] = []
    dl.download_to_file("http://img/c.jpg", out3,
                        on_progress=lambda d, t: seen.append((d, t)))
    check("进度第一个回调就是断点位置", seen[0], (4096, len(body)))
    check("进度最后落在总量上", seen[-1], (len(body), len(body)))

    # ------------------------------------- "下完就断电"：.part 已完整，只差改名
    out4 = home / "wall4.jpg"
    _part_path(out4, "http://img/d.jpg").write_bytes(body)
    src5 = _Source(body)
    dl._local.session = src5
    dl.download_to_file("http://img/d.jpg", out4)
    check("已完整的 .part 直接改名", out4.read_bytes() == body, True)
    check("这种情况一个字节都没多下", src5.served, 0)
    check("服务器回的是 416", src5.statuses, [416])

    # --------------------------------- 服务器不认 Range：必须从头下，不能拼坏文件
    out5 = home / "wall5.jpg"
    _part_path(out5, "http://img/e.jpg").write_bytes(body[:4096])
    dl._local.session = _Source(body, ignore_range=True)
    dl.download_to_file("http://img/e.jpg", out5)
    check("服务器无视 Range 时从头下", out5.read_bytes() == body, True)
    check("没把新数据接在旧数据后面", out5.stat().st_size, len(body))

    # ---------------------------- 换了地址：旧 .part 是垃圾，不能拿去续传
    out6 = home / "wall6.jpg"
    stale = _part_path(out6, "http://img/old.jpg")
    stale.write_bytes(b"x" * 4096)
    src7 = _Source(body)
    dl._local.session = src7
    dl.download_to_file("http://img/new.jpg", out6)
    check("换地址后旧 .part 被清掉", stale.exists(), False)
    check("新地址的请求没带 Range（旧数据用不上）", src7.requests, [None])
    check("结果正确", out6.read_bytes() == body, True)

    # ------------------------------- HTTP 错误：.part 留着，等服务端恢复还能接着下
    out7 = home / "wall7.jpg"
    part7 = _part_path(out7, "http://img/f.jpg")
    part7.write_bytes(body[:1024])
    dl._local.session = _Source(body, force_status=503)
    raised = None
    try:
        dl.download_to_file("http://img/f.jpg", out7)
    except WallpaperError as exc:
        raised = exc
    check("5xx 报成 WallpaperError", isinstance(raised, WallpaperError), True)
    check("HTTP 错误时已下的 .part 不会被删", part7.stat().st_size, 1024)


def main() -> int:
    print(f"配置目录：{CONFIG_DIR}")
    if CONFIG_DIR.parent == Path.home() / "AppData" / "Roaming":
        print("拒绝运行：APPDATA 还是真实用户目录，这个测试会清空配置。")
        return 2
    shutil.rmtree(CONFIG_DIR, ignore_errors=True)   # 干净起步，避免上一轮的状态串味

    tmp = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
        else Path(tempfile.mkdtemp(prefix="wp_feature_tmp_"))
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    print(f"临时目录：{tmp}\n")

    _patch_messagebox()
    catcher = _watch_errors()

    test_migrate(tmp)
    test_redownload(tmp)          # 假客户端，离线也跑
    test_ui(tmp)
    test_thumbs(tmp)
    if "--no-net" in sys.argv:
        skip("真联网重新下载", "命令行指定了 --no-net")
        skip("实跑搜索", "命令行指定了 --no-net")
    else:
        test_redownload_live(tmp)
        test_live_search(tmp)
    test_background(tmp)
    test_resume(tmp)

    # 运行期间不该出现任何 ERROR 级日志。事件循环会吞掉事件处理器的异常，
    # 只看界面断言会漏掉"某段代码每次执行都在抛异常"这种情况。
    if catcher.lines:
        print(f"\n!! 运行期间出现 {len(catcher.lines)} 条 ERROR 日志：")
        for line in catcher.lines[:10]:
            print(f"   {line}")
        FAILS.append("运行期间出现 ERROR 日志")

    print()
    if SKIPS:
        print(f"跳过 {len(SKIPS)} 项：{SKIPS}")
    print("全部通过" if not FAILS else f"!! 失败 {len(FAILS)} 项：{FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
