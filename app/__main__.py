"""支持 `python -m app` 直接启动。"""
import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
