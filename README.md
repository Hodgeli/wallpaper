# auto_wallpaper

从 [wallhaven.cc](https://wallhaven.cc) 搜索、预览、下载壁纸并设为 Windows 桌面。

用 wallhaven 的**官方 JSON API**（不解析网页），带完整的图形界面、下载历史管理和
命令行模式，可以挂到计划任务里定时换壁纸。

> ## ⚠️ 免责声明（请先读这一段）
>
> - **本项目是非官方的第三方工具，与 wallhaven.cc 及其运营方没有任何隶属、赞助或
>   合作关系**，"wallhaven" 仅用于指明它访问的服务。
> - **本项目不提供、不托管、不缓存任何壁纸图片**。图片的著作权属于各自的作者；
>   wallhaven 的服务条款明确写着上传者保留其内容的一切权利。本程序只负责把你
>   **自己选中**的图下载到**你自己的电脑**上。
> - **下载下来的图用于什么目的（个人当壁纸 / 再分发 / 商用）由你自行判断并承担
>   后果。** 请勿用它批量爬取图库、重新上传到别的站点或绕过速率限制。
> - 程序按 **"原样"** 提供，不附带任何担保；作者不对数据丢失、文件被覆盖、账号
>   被封禁等后果负责。
> - 完整说明见 [`DISCLAIMER.md`](DISCLAIMER.md)。
>
> 许可证：**MIT**（见 [`LICENSE`](LICENSE)）。第三方组件的许可证与分发义务见
> [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)。

---

## 功能

**搜索**
- 关键词 + 分类 + 分辨率 + 比例 + 排序 五个条件一行摆开
- 官方 JSON API，直接返回缩略图直链，不再解析 HTML
- 无限滚动翻页，最多预取 2 页防止撞限流
- 分辨率默认「自适应」＝所有显示器中最大的宽 × 高，双屏不会漏掉大屏
- 比例默认放开 16:9 + 16:10

**浏览与设置**
- 缩略图网格随窗口宽度自适应列数
- 单击选中 → 右侧面板显示大图预览 + 分辨率/收藏/浏览/体积/配色
- 双击缩略图 = 下载并设为桌面壁纸
- 已下载过的图带角标，重复命中直接复用本地文件

**预览与放大**
- 预览面板先秒出缩略图，再后台换成**原图**（超过 25MB 的自动跳过，保留缩略图）
- 预览区本身就能缩放：滚轮以**鼠标位置为锚点**放大局部、左键拖拽平移、双击在「适应窗口 ↔ 1:1」之间切换
- 工具栏：`适应` / `1:1` / `－` / `＋` / 倍率 / 原图尺寸，最高 12×
- 「弹出大图」开独立窗口看，最大 82% 屏幕，键盘 `+` `-` `0` `1` `Esc` 全支持
- 只渲染可视区域再缩放，12× 也不会爆内存；拖拽时用快速重采样，停手 180ms 后自动切回高质量重采样

**历史**
- 记录每次下载：时间、关键词、分辨率、大小、文件
- 可重新设为壁纸、在资源管理器里定位、删除本地文件（顺手清理空目录）
- 本地文件被删了会标成「已丢失」（红字 + 路径栏提示），选中后点「重新下载」就能找回
- 可以「按关键词分组」，组标题上直接标出这一组有几个文件已丢失

**保存目录**
- 在设置页改「保存目录」时，**已经下载的壁纸会自动搬到新目录**，历史记录里的路径同步更新
- 搬的时候不覆盖新目录里的同名文件：内容大小一致就只改记录，不一致就自动改名成 `xxx_1.jpg` 再放
- 不在旧目录里的（你自己挪过位置的）和文件已经丢的会跳过，状态栏会分别告诉你各有多少

**重新下载**
- 历史页选中一条「已丢失」的记录 → 点「重新下载」，优先放回它原来那个位置
- 原目录已经不可用（盘符没了 / 没权限）就按当前保存目录 + 命名规则重新算一个路径
- 老记录里没存原图直链的，会自动回查一次 wallhaven API 拿到地址
- **下载中断了不用从头再来**：原图常有 10–30MB，网络抖一下或者你手快关了窗口，
  已下到的部分会留在 `<文件名>.<8位十六进制>.part` 里；下次对同一张图再下时
  会自动带上 `Range` 从断点接着下，状态栏显示的进度也是从断点开始的。
  那串十六进制是下载地址的指纹，所以换了地址绝不会把两段数据拼成一个坏文件。
  想手动清掉这些半截文件，直接删掉保存目录里的 `*.part` 就行。

**界面主题**
- 设置页 →「显示与内容」→「界面主题」：`自动（跟随系统）` / `浅色` / `深色`，默认自动
- 「自动」读的是 Windows 的「应用模式」设置（`AppsUseLightTheme`）
- 配色是全局 ttk 样式，**改完要重启程序**才生效

**随机换一张**
- 界面上的按钮，或命令行 `WallpaperPicker.exe --random`（静默，适合计划任务）
- 用设置页的「默认搜索配置」；关键词留空时回落到内置分类列表

**默认关键词：可以配多个**

设置页 →「默认搜索配置」→「默认关键词」，**逗号隔开就是多个**：

```
cyberpunk, anime, space
```

分隔符认中英文逗号、顿号、分号、换行。每次用到时**从列表里随机取一个**：

| 场景 | 行为 |
| --- | --- |
| 「随机换一张」按钮 | 每次随机取一个 |
| `WallpaperPicker.exe --random` | 同上，走同一份配置，挂计划任务不用重复写 |
| 搜索页关键词框**留空**时点搜索 | 随机取一个并填进框里，让你看得见搜的是什么；想换一个就清空再搜 |
| 搜索页关键词框**有内容**时 | 按你填的搜，不做随机 |

打开程序时关键词框会自动填上列表里的随机一个；列表只有一项就填那一项。

**整串留空**时：「随机换一张」回落到 26 个内置英文分类（nature、cyberpunk、anime…）；搜索页则不带关键词搜索。

**单个关键词里想表达「同时包含」用空格，别用逗号**——逗号在这里是「或者」：

| 写法 | 含义 |
| --- | --- |
| `nature ocean` | 同时含 nature 和 ocean（一条查询） |
| `nature, ocean` | nature 或 ocean（每次随机取一个） |
| `nature -ocean` | 含 nature、排除 ocean |
| `@someone` · `type:png` · `like:94x38z` | 作者 / 文件类型 / 找相似图 |

临时想换一次关键词，不用改配置——用 `--keyword` 覆盖：

```
WallpaperPicker.exe --random --keyword cyberpunk --silent
```

> `--keyword` **只对这一次运行生效**，不会改动设置页里的「默认关键词」（配置也不会被写盘）。想长期固定某个关键词，请到设置页改。

**错误处理**
- 失败按类型分：网络不可达 / 代理失败 / 超时 / 404 / 401 / 403 / 429 限流 / 解析失败 / 磁盘写入失败 / 目录无权限
- 每种给一句人话说明，严重错误弹窗（同类错误 30 秒内不重复弹）
- 连接类错误指数退避重试 1s / 2s / 4s；429 按 `Retry-After` 等待
- 日志按天切分到 `%APPDATA%\auto_wallpaper\logs\`，保留 7 天

---

## 运行

### 打包好的 exe

```
dist\WallpaperPicker.exe              启动界面
dist\WallpaperPicker.exe --random     静默抓一张随机壁纸并设为桌面
```

`--random` 配合 `--silent` 可以在计划任务里完全静默（失败也不弹窗）：

```
程序:      D:\path\to\WallpaperPicker.exe
参数:      --random --silent
触发器:    每 30 分钟
```

### 加到桌面右键菜单

想右键点一下桌面就换一张，用仓库里的脚本注册（**必须以管理员身份运行**，
否则写不进 `HKEY_CLASSES_ROOT`）：

```bash
python app/tools/install_context_menu.py                    # 用 dist\WallpaperPicker.exe
python app/tools/install_context_menu.py --exe D:\path\a.exe  # 指定别的 exe
python app/tools/install_context_menu.py --uninstall        # 移除
```

它只在 `HKEY_CLASSES_ROOT\DesktopBackground\shell` 下建一个叫 `WallhavenRandom`
的子键，菜单文字「随机换一张壁纸」，命令行是 `"<exe>" --random --silent`。
卸载就是把整棵子树删掉，不留残渣。

> **注册完要重启一次 explorer.exe 才会出现**（任务管理器 → Windows 资源管理器 →
> 重新启动）。或者重跑脚本并加 `--restart-explorer`——那条路会关掉你所有打开的
> 文件夹窗口，所以默认不做。
>
> exe 挪过位置（重新打包、换目录）就得重跑一次，注册表里存的是绝对路径。

### 从源码跑

需要 [uv](https://docs.astral.sh/uv/)（本机已装）。

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt      # 只跑源码装这个就够
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt  # 要打包 exe 再装这个

.venv/Scripts/python.exe -m app              # 启动界面
.venv/Scripts/python.exe -m app --random     # 静默换壁纸
```

运行时依赖只有三条：`requests`、`Pillow`、`certifi`（tkinter / ctypes / winreg 都是标准库）。
`certifi` 本来跟着 `requests` 一起装，单独列出来是因为打包命令依赖它的证书文件，见下面「重新打包」。
`requirements-dev.txt` 只多一个 `pyinstaller`。

### 重新打包

```bash
ROOT=$(cygpath -m "$PWD")      # Git Bash 下必须转一次，见下面的说明
.venv/Scripts/python.exe app/tools/make_icon.py            # 生成图标
.venv/Scripts/python.exe app/tools/make_version_file.py    # 生成 exe 的版本资源
.venv/Scripts/python.exe -m PyInstaller --noconfirm --clean --onefile --noconsole \
  --name WallpaperPicker \
  --icon "$ROOT/app/assets/icon.ico" \
  --version-file "$ROOT/build/version_info.txt" \
  --add-data "$ROOT/app/assets/icon.ico;app/assets" \
  --collect-data certifi \
  --distpath dist --workpath build --specpath build \
  --collect-submodules PIL run.py
```

> 三个必须注意的点：
> - `--icon` 和 `--add-data` 都要用**绝对路径**。加了 `--specpath` 之后相对路径会以 spec 目录（`build/`）为基准解析：`--add-data` 报 "Unable to find"，`--icon` 报 "Icon input file ... not found"。
>   另外在 **Git Bash 里 `$PWD` 不能直接拼**——它是 `/d/new/...` 这种 MSYS 形式，传进去会被拼成 `\d\new\...`，报 `Unable to find '\d\new\...'`。要用 `cygpath -m "$PWD"` 转成 `D:/new/...`。
> - 必须带 `--collect-data certifi`。PyInstaller 6.x 的 certifi 钩子在本机环境里是坏的（`_pyinstaller_hooks_contrib` 版本不匹配），不打进去的话程序一联网就抛 `OSError: Could not find a suitable TLS CA certificate bundle`。
>
> `--version-file` 是可选的，但加上之后 exe 的「属性 → 详细信息」才有版本号。
> 那个文件由 `make_version_file.py` 从 `app/config.py` 的 `APP_VERSION` 生成，
> 版本号仍然只有一个来源。

打包完可以跑一下冒烟测试：

```bash
.venv/Scripts/python.exe app/tools/smoke_test.py                        # 测 dist/ 下那个
.venv/Scripts/python.exe app/tools/smoke_test.py build/stage/Your.exe   # 测指定路径
```

它会启动 exe、确认真的弹出了新窗口（对比启动前后的窗口列表，不会把你自己正开着的实例误认成成功）、再扫一遍日志里有没有 `ERROR`，退出码 0 表示通过。

改过「保存目录迁移 / 重新下载 / 历史分组」这几块的话，另外跑一下功能回归测试：

```bash
.venv/Scripts/python.exe app/tools/feature_test.py            # 全部用例（含真联网的两节）
.venv/Scripts/python.exe app/tools/feature_test.py --no-net   # 跳过真联网的两节
```

它会把自己重开到一个**临时 APPDATA** 里，不会碰你真实的配置和壁纸目录；退出码 0 表示通过。

`--no-net` 只跳过「真联网重新下载」和「实跑搜索」。主干用例（含「重新下载」的全部分支）
走 `app/tools/fakes.py` 里的假客户端，**没网也照跑**。

### 单测与 lint

改完纯逻辑（命名 / 配置 / 记录表 / API 纯函数）随手跑这个，几秒出结果：

```bash
.venv/Scripts/python.exe app/tools/unit_test.py    # 纯函数单测，不联网不开窗口
.venv/Scripts/python.exe -m ruff check .           # lint
.venv/Scripts/python.exe -m ruff check . --fix     # 顺手修能自动修的
```

三个测试的分工：

| 脚本 | 需要 | 跑一遍 | 管什么 |
| --- | --- | --- | --- |
| `unit_test.py` | 无 | 几秒 | 纯函数在奇怪输入下还对不对 |
| `feature_test.py` | 桌面 + 可选网络 | 1–3 分钟 | 整条链路串起来能不能跑 |
| `smoke_test.py` | 打包产物 | 十几秒 | exe 能不能起来 |

CI（`.github/workflows/ci.yml`）只跑 lint + `unit_test.py` + `--version` 冒烟——
`feature_test.py` 要开真窗口，在 runner 上不稳定，目前只能在本地跑。

---

## 数据放在哪

| 内容 | 位置 |
| --- | --- |
| 配置文件 | `%APPDATA%\auto_wallpaper\config.json` |
| 日志 | `%APPDATA%\auto_wallpaper\logs\wallpaper.log`（按天切分，留 7 天） |
| 缩略图缓存 | `%APPDATA%\auto_wallpaper\thumbcache\`（默认上限 200MB，自动清理最旧的） |
| 下载记录 | `%APPDATA%\auto_wallpaper\downloaded.json` |
| 壁纸 | 设置页里的「保存目录」，默认 `%USERPROFILE%\Pictures\Wallpapers` |

壁纸文件按关键词分子目录命名，例如：

```
Wallpapers\
├─ nature\
│   └─ nature_2500x1401_rddgwm.jpg
└─ cyberpunk\
    └─ cyberpunk_3840x2160_7pje5o.png
```

命名方式在设置页可选：`关键词+分辨率+ID`（默认）/ `日期+关键词+分辨率+ID` / `仅 ID`。

---

## 目录结构

```
app/
├─ main.py          入口与参数解析
├─ config.py        常量、路径、配置读写
├─ api.py           wallhaven 客户端（错误分类、重试、代理探测）
├─ images.py        缩略图缓存与原图下载（含断点续传）
├─ service.py       业务逻辑（下载/设为壁纸/随机换一张）
├─ naming.py        文件命名与字符清洗
├─ store.py         下载记录与去重
├─ winwall.py       显示器探测、填充方式、设置壁纸（纯 ctypes，不依赖 pywin32）
├─ logsetup.py      日志
├─ ui/
│  ├─ app.py        主窗口与状态栏
│  ├─ search_tab.py 搜索页
│  ├─ preview_pane.py 右侧预览面板（缩略图 → 原图 → 缩放/弹出）
│  ├─ history_tab.py 历史页
│  ├─ settings_tab.py 设置页
│  ├─ zoomcanvas.py 可缩放/平移的预览画布（鼠标锚点缩放、可视区裁剪渲染）
│  ├─ image_viewer.py 弹出式大图查看窗口
│  ├─ widgets.py    缩略图网格控件
│  ├─ theme.py      跟随系统的浅色/深色配色
│  └─ async_util.py 工作线程 → 主线程的事件队列
├─ assets/icon.ico
└─ tools/
   ├─ make_icon.py          生成图标
   ├─ make_screenshots.py   生成本地界面截图（输出目录不进仓库）
   ├─ make_version_file.py  生成 exe 的版本资源（版本号来自 config.py）
   ├─ install_context_menu.py 把「随机换一张壁纸」注册到桌面右键菜单
   ├─ fakes.py              测试用的假客户端/假下载器/造数据辅助
   ├─ unit_test.py          纯逻辑单测（不联网、不开窗口，几秒跑完）
   ├─ feature_test.py       功能回归测试（开窗口，含可选的联网用例）
   └─ smoke_test.py         打包后冒烟测试（启动 exe、确认窗口出来了、日志无 ERROR）

LICENSE                  本项目许可证（MIT）
DISCLAIMER.md            免责声明（非官方、图片版权、无担保）
THIRD-PARTY-NOTICES.md   第三方组件清单与分发义务
third_party_licenses/    各依赖的许可证原文（逐字复制，未转写）
CHANGELOG.md             版本变更记录
```

---

## 常见问题

**搜不到东西 / 一直转圈**
wallhaven 在国内基本需要代理。程序默认「自动跟随系统代理」，会读 Windows 的代理设置——只要 Clash 开着「系统代理」就能直接用。设置页有「测试连接」按钮可以直接验证。

**关键词搜不出结果**
wallhaven 只认英文关键词。中文关键词会返回空列表。

**想看 NSFW 内容**
设置页打开开关，并填入 API Key（wallhaven.cc → My Account → Settings → API Key，免费）。不开开关时只搜 SFW。

> **API Key 是明文存在 `config.json` 里的**（`%APPDATA%\auto_wallpaper\config.json`），
> 没有加密、也没有走系统凭据管理器。这是本地小工具的常规做法，但你要知道：
> 任何能读到这个文件的程序/人都能看到你的 Key。别把这个文件传到网上、别放进
> 同步盘，也**不要在提 issue 时把它贴出来**。Key 泄露了就到 wallhaven 账号设置里
> 重新生成一个（旧的就立即失效）。

**壁纸被拉变形了**
默认用的是「填充」（等比放大铺满、裁掉溢出），正常不会变形。如果你在设置页选成了「拉伸」，非原生分辨率下就会被拉变形——换回「填充」或「适应」即可。

**缩略图缓存占地方**
设置页可以调整上限或一键清空，默认 200MB、超了自动删最旧的。

**换了保存目录，原来的壁纸会怎样**
会自动搬到新目录，历史记录里的路径跟着更新。目标目录里已经有同名文件时**不会覆盖**：大小一样就只改记录（源文件留在原处，状态栏会提示「N 个目标已有相同文件」），大小不一样就改名成 `xxx_1.jpg` 再放。
文件已经丢了的、或者你手动挪到别处去了的，程序不碰，状态栏会分别报数。

**历史里显示「已丢失」怎么办**
说明记录里的本地文件被删了（或者你换过盘）。选中那一条点「重新下载」就能按原图直链找回来；找不到原图直链的老记录会自动回查一次 API。整批清理用右上角的「清理失效记录」。

---

## 许可证与免责声明

本项目以 **MIT 许可证**发布，见 [`LICENSE`](LICENSE)。

```
Copyright (c) 2018-2026 Hodge
```

> 简单说：你可以随便用、改、再分发（包括商用），但**必须保留版权声明和许可证
> 原文**，而且作者不为任何后果负责。

**使用前请先读 [`DISCLAIMER.md`](DISCLAIMER.md)**，其中说明了：

- 本项目是**非官方**工具，与 wallhaven.cc 无任何隶属关系；
- 本项目**不提供任何图片内容**，图片版权属于各自的作者，下载后怎么用由你负责；
- 软件按"原样"提供，不保证可用性，也不对数据丢失/账号封禁等后果负责。

第三方组件（`requests` / `Pillow` / `certifi` 等）适用各自的许可证，与本项目的
MIT 许可证相互独立；打包成 exe 分发时需要保留它们的许可声明，清单与义务见
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)。

> 顺带说明一个常见误解：本项目用 **PyInstaller** 打包，而 PyInstaller 主体是
> GPLv2+——但它带有 **Bootloader 例外条款**，明确允许把 bootloader 嵌入其他程序
> 并自由分发。所以**本项目保持 MIT 即可，不会因为打包而被迫变成 GPL**。

版本变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。
