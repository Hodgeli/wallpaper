# 第三方组件与许可证 / Third-Party Notices

本项目（`auto_wallpaper` / WallpaperPicker）自身以 **MIT 许可证**发布，见
[`LICENSE`](LICENSE)。

它依赖若干开源组件。**这些组件各自适用自己的许可证，与本项目的 MIT 许可证相互
独立**，其著作权归各自的作者所有。本文件列出它们，并说明分发时的义务。

> 各许可证的**完整原文**放在 [`third_party_licenses/`](third_party_licenses/) 目录下。
> 发布打包好的 exe 时，请把这个目录（或其中的文件）一并附上——MIT / BSD /
> Apache-2.0 / HPND 都要求"随分发保留版权与许可声明"，MPL-2.0 与 GPL 另有要求，
> 详见下文。

---

## 运行时依赖（会被打进 exe）

| 组件 | 版本 | 许可证 | 许可证原文 |
| --- | --- | --- | --- |
| [requests](https://github.com/psf/requests) | 2.34.2 | Apache-2.0 | [`requests-LICENSE.txt`](third_party_licenses/requests-LICENSE.txt) |
| [certifi](https://github.com/certifi/python-certifi) | 2026.7.22 | MPL-2.0 | [`certifi-LICENSE.txt`](third_party_licenses/certifi-LICENSE.txt) |
| [urllib3](https://github.com/urllib3/urllib3) | 2.7.0 | MIT | [`urllib3-LICENSE.txt`](third_party_licenses/urllib3-LICENSE.txt) |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) | 3.5.1 | MIT | [`charset-normalizer-LICENSE.txt`](third_party_licenses/charset-normalizer-LICENSE.txt) |
| [idna](https://github.com/kjd/idna) | 3.19 | BSD-3-Clause | [`idna-LICENSE.txt`](third_party_licenses/idna-LICENSE.txt) |
| [Pillow](https://python-pillow.org/) | 12.3.0 | MIT-CMU (HPND) | [`Pillow-LICENSE.txt`](third_party_licenses/Pillow-LICENSE.txt) |
| [PyInstaller](https://pyinstaller.org/)（bootloader 被嵌入 exe） | 6.22.3 | GPL-2.0-or-later **WITH** Bootloader-exception | [`PyInstaller-COPYING.txt`](third_party_licenses/PyInstaller-COPYING.txt) |

## 仅构建/开发期使用（不会进 exe）

| 组件 | 版本 | 许可证 | 许可证原文 |
| --- | --- | --- | --- |
| [pyinstaller-hooks-contrib](https://github.com/pyinstaller/pyinstaller-hooks-contrib) | 2026.7 | Apache-2.0（或 GPL-2.0，二选一） | [`…-LICENSE.txt`](third_party_licenses/pyinstaller-hooks-contrib-LICENSE.txt) |
| [altgraph](https://pypi.org/project/altgraph/) | 0.17.5 | MIT | [`altgraph-LICENSE.txt`](third_party_licenses/altgraph-LICENSE.txt) |
| [pefile](https://github.com/erocarrera/pefile) | 2024.8.26 | MIT | [`pefile-LICENSE.txt`](third_party_licenses/pefile-LICENSE.txt) |
| [pywin32-ctypes](https://github.com/enthought/pywin32-ctypes) | 0.2.3 | BSD-3-Clause | [`pywin32-ctypes-LICENSE.txt`](third_party_licenses/pywin32-ctypes-LICENSE.txt) |
| [ruff](https://github.com/astral-sh/ruff) | 0.16.7 | MIT | 见其[上游仓库](https://github.com/astral-sh/ruff/blob/main/LICENSE) |

> Python 标准库（`tkinter` / `ctypes` / `winreg` / `hashlib` …）随 Python 发行版
> 提供，适用 [PSF License](https://docs.python.org/3/license.html)。

---

## 分发时的几条注意事项

### requests（Apache-2.0）

Apache-2.0 第 4 条要求：随分发提供许可证副本、保留版权与归属声明、**保留 `NOTICE`
文件的内容**。requests 的 NOTICE 很短，原文如下，已同步保存在
[`third_party_licenses/requests-NOTICE.txt`](third_party_licenses/requests-NOTICE.txt)：

```
Requests
Copyright 2019 Kenneth Reitz
```

### certifi（MPL-2.0）

MPL-2.0 是**文件级** copyleft：只要不修改 certifi 的源文件，把它原样打包进 exe
即可，**不需要**把本项目的代码也按 MPL 开源。本项目未修改 certifi，只依赖它提供的
CA 证书包（打包时用 `--collect-data certifi` 带上）。

若你**修改**了 certifi 的代码，则被修改的那些文件需要以 MPL-2.0 公开源码。

### Pillow（MIT-CMU / HPND）

HPND 要求：**不得删除或改动这段许可声明**、不得声称自己写了原软件、修改过的版本
要明确标注为修改版。把 `Pillow-LICENSE.txt` 一起分发即满足要求。

### PyInstaller（GPL-2.0-or-later WITH Bootloader-exception）

**这条最容易被误读，所以单独说清楚**：PyInstaller 主体是 GPLv2+，但它带一个
**Bootloader 例外条款**，原文（`PyInstaller-COPYING.txt` 第 5 段）：

> In addition to the permissions in the GNU General Public License, the authors
> give you **unlimited permission to link or embed compiled bootloader and
> related files into combinations with other programs, and to distribute those
> combinations without any restriction coming from the use of those files.**

也就是说：**用 PyInstaller 打包不会让你的项目变成 GPL，你也不需要把本项目开源成
GPL**。本项目保持 MIT 即可。

（GPL 的约束仍然适用于"修改 PyInstaller 自身的文件"以及"不以组合可执行文件形式
分发它"这两种情形——本项目都不涉及。）

### 本项目自己的代码

- 全部源码（`app/` 包、`run.py`、`app/tools/` 下的脚本）：MIT，见 `LICENSE`。
- 图标（`app/assets/icon.ico`）：由 `app/tools/make_icon.py` 用 Pillow **程序化
  绘制**生成，不含任何第三方素材，随本项目一起按 MIT 授权。
- 本项目**不随仓库分发任何图片素材**：图标是程序化绘制的，界面截图输出到本地
  `docs/`（该目录已被 `.gitignore` 排除），仓库里不存在第三方作品。

---

## 如何重新生成本目录

依赖升级后，许可证文件需要跟着更新。这些文件是**从已安装的包元数据里原样复制**的
（不做任何转写，避免抄错）：

```bash
# 依赖装在 .venv 里之后，逐个从 *.dist-info/licenses/ 复制
ls .venv/Lib/site-packages/*.dist-info/licenses/
```

各包的许可证标识可以用 `pip show <包名>` 或读取
`importlib.metadata.distribution("<包名>").metadata["License"]` 查看。
