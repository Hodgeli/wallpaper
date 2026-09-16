"""PyInstaller 打包入口。

源码入口是 `python -m app`；打包时需要这个顶层脚本，
否则 app 包内的相对导入在冻结环境下会失效。
"""
import multiprocessing
import sys

from app.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
