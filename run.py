"""打包入口脚本。

源码开发时用：python -m asr_voice.main
打包时用此文件作为 PyInstaller 入口，避免相对导入失败。
"""

import sys
from pathlib import Path

# 确保 asr_voice 包目录在搜索路径中（用绝对路径 python.exe 启动时需要）
_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from asr_voice.main import main

if __name__ == "__main__":
    sys.exit(main())
