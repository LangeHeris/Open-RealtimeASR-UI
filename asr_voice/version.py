"""版本号单一来源：tag 与代码内版本不一致则 CI 失败（Plan B spec §5）。

全项目只此一处写版本号。其余位置一律从这里读取或由 tools/gen_version_info.py
生成：
- `asr_voice/__init__.py` 转发 `__version__`（兼容既有的 `from .. import __version__`）
- `version_info.txt`（Windows 文件属性资源）由生成器产出，tests/test_version.py
  有「已提交文件 == 生成器输出」的守卫
- `pyproject.toml` 的 version 同值（守卫同上）
"""

__version__ = "0.1.0"
