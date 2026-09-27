# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_submodules

# exe 文件名用短名 ORI.exe（好记好敲）；产品全名 Open-RealtimeASR-UI
# 由 version_info.txt 的 ProductName 呈现。改动请同步更新
# version_info.txt 里的 OriginalFilename / InternalName。
APP_NAME = 'ORI'

# 精简构建（build.bat lite）：物理排除 DLC 主题的特性模块，
# 且构建后不投放 themes/ 主题包目录——本体不含赛博朋克/凯尔特的任何内容。
LITE_BUILD = os.environ.get('ORI_BUILD_MODE') == 'lite'

datas = []
binaries = []
hiddenimports = []
hiddenimports += collect_submodules('sounddevice')
# 暂停播放/全局静音：media_pause.py 延迟导入，静态分析收集不到；
# winrt 为命名空间包、comtypes 动态生成绑定，均需显式声明
hiddenimports += collect_submodules('winrt')
# winrt 为命名空间包，collect_submodules 可能遗漏，显式声明实际用到的子模块
hiddenimports += [
    'winrt.windows.media.control',
    'winrt.windows.foundation',
    'winrt.windows.foundation.collections',
]
hiddenimports += collect_submodules('pycaw')
hiddenimports += collect_submodules('comtypes')
# 本项目只 import 了 QtCore/QtGui/QtWidgets/QtNetwork，声明这四个即可：
# PyInstaller 内置 hook 会自动收集对应的 DLL 和平台/样式/图片格式插件。
# 不要用 collect_all('PySide6') —— 它会把 WebEngine（Chromium，~195MB）、
# QtQuick/Qml、Designer、FFmpeg 等整个 Qt 生态全部塞进 exe（255MB 的由来）。
hiddenimports += [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtNetwork',
]
# 配置模板：首次运行时复制到 exe 同目录生成 config.yaml
datas += [('config/config.example.yaml', '.')]
# 悬浮条图标资源（程序图标 + 声纹主题图案）：
# icons.py 用 Path(__file__).parent.parent / "assets" 定位，frozen 下
# 解析到 _MEIPASS/asr_voice/assets/，必须按相同相对结构打包。
# 凯尔特结素材已迁出至 themes/凯尔特风格/assets/（外部主题包，不进 exe）。
datas += [('asr_voice/assets', 'asr_voice/assets')]
# 内置默认字体（阿里巴巴普惠体 3.0，免费商用，授权条款见随包
# THIRD_PARTY_NOTICES.md）：
# 白名单只投放代码实际引用的三个字重（55 Regular / 65 Medium / 85 Bold，
# 对应 font-weight 400/500/700 命中字面），不用 glob 通配：
# fonts/ 顶层其余字重（45 Light / 75 SemiBold / 95 ExtraBold 等）留给
# 本地运行使用，不进安装包（每个约 8MB）。新增字重需同时在此白名单登记。
# 打包后解包到 _MEIPASS/fonts/，fonts.py 的 _bundled_fonts_dir() 从此
# 读取；用户自行投放的字重走 exe 同目录 fonts/（fonts_dir()）。
# 字体文件本身不随仓库分发（EULA 约束，见 README 致谢节）：干净克隆/CI 没有
# fonts/ 时跳过打包并提示，程序回退系统字体（fonts.py 已有容错），构建不失败。
_BUNDLED_FONTS = []
for _w in ('55-Regular', '65-Medium', '85-Bold'):
    _fp = f'fonts/AlibabaPuHuiTi-3-{_w}.ttf'
    if os.path.exists(_fp):
        _BUNDLED_FONTS.append((_fp, 'fonts'))
    else:
        print(f'[spec] font not found (not shipped in repo): {_fp} - exe falls back to system fonts')
datas += _BUNDLED_FONTS
# 像素主题专用字体（缝合像素字体 Fusion Pixel 10px 点阵规格，SIL OFL-1.1）
# **不进 GitHub 版**：像素主题是 商店版独占，GitHub 版连主题包与特性模块
# 一起排除，字体跟着主题走。发行包由组装脚本直接从源码树投放
# 字体，不经本 spec（该脚本属 渠道私有资产，不随本仓库分发）。
# 第三方许可清单随 exe 分发（LGPL / Apache 等许可声明与字体授权依据）；
# 本项目自身的 Apache-2.0 LICENSE 与 NOTICE 一并随包分发（Apache-2.0 §4 要求）
datas += [('THIRD_PARTY_NOTICES.md', '.'), ('LICENSE', '.'), ('NOTICE', '.')]

# 主题特性模块由 theme_features/__init__.py 用 importlib 动态导入，
# 静态分析收集不到，必须显式声明（lite 构建在 Analysis excludes 中排除）
if not LITE_BUILD:
    hiddenimports += [
        'asr_voice.ui.theme_features.celtic',
        'asr_voice.ui.theme_features.cyberpunk',
    ]
    # DLC 主题包（完整构建）：theme.yaml + 素材随 exe 打进 _MEIPASS/themes/，
    # 单文件即可用，无需 exe 同目录 themes/ 文件夹。theme_registry._themes_roots()
    # 优先读 _MEIPASS/themes，再读 exe 同目录 themes（仍可投放外部主题包扩展）。
    # 按包逐个登记，不用 `datas += [('themes', 'themes')]` 整目录拷贝：整目录会把
    # 商店版独占的像素主题一并打进 GitHub 版（守卫见 tests/test_github_theme_packs.py）。
    for _pack in ('凯尔特风格', '赛博朋克风'):
        datas += [(f'themes/{_pack}', f'themes/{_pack}')]


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 本地 FunASR 引擎依赖（funasr/torch 全家桶约 2GB+）不打包：
    # funasr_realtime.py 的延迟导入也会被静态分析捕获，必须显式排除。
    # 打包版选 FunASR 时运行期会给出明确提示（改用云端或源码运行）。
    # pixel 恒排除：像素主题为 商店版独占，GitHub 版（含 lite）一律物理剔除。
    # 只排模块不够，主题包与字体也要一并断掉，见上面两处 datas 的注释。
    excludes=[
        'funasr', 'torch', 'torchvision', 'torchaudio',
        'scipy', 'matplotlib', 'pandas', 'modelscope',
        'asr_voice.ui.theme_features.pixel',
    ] + ([
        'asr_voice.ui.theme_features.celtic',
        'asr_voice.ui.theme_features.cyberpunk',
    ] if LITE_BUILD else []),
    noarchive=False,
    optimize=0,
)

# --- 瘦身兜底：剔除本项目确定不用的 Qt 组件（无论哪个 hook 带进来的） ---
# 纯 Widgets + Network 应用，以下均为 WebEngine/Quick/Qml/多媒体等未使用组件。
# 保留：platforms(qwindows)、styles、imageformats、iconengines、Qt6Network。
_DROP = (
    'Qt6WebEngine', 'Qt6Pdf', 'Qt6Quick', 'Qt6Qml', 'Qt6Designer', 'Qt6Charts',
    'Qt6DataVisualization', 'Qt6Test', 'Qt6Multimedia', 'Qt6Sql', 'Qt63D',
    'Qt6Bluetooth', 'Qt6Nfc', 'Qt6Positioning', 'Qt6Sensors', 'Qt6SerialPort',
    'Qt6SerialBus', 'Qt6TextToSpeech', 'Qt6WebChannel', 'Qt6WebSockets',
    'Qt6RemoteObjects', 'Qt6Scxml', 'Qt6StateMachine', 'Qt6UiTools', 'Qt6Help',
    'Qt6NetworkAuth', 'Qt6DBus', 'Qt6OpenGL', 'Qt6ShaderTools', 'Qt6Xml',
    'opengl32sw', 'avcodec-', 'avformat-', 'avutil-', 'swresample-', 'swscale-',
    'sqldrivers', 'webview', 'virtualkeyboard',
)
a.binaries = [b for b in a.binaries if not any(p.lower() in b[0].lower() for p in _DROP)]
a.datas = [d for d in a.datas if not any(p.lower() in d[0].lower() for p in _DROP)]
pyz = PYZ(a.pure)

# EXE 公共参数（两种产物形态共用一份，避免复制两遍漂移）
_EXE_KW = dict(
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 压缩 DLL 会增加启动期解压开销，且更易触发杀软逐文件扫描，得不偿失
    upx=False,
    upx_exclude=[],
    console=os.environ.get('ORI_CONSOLE', '') == '1',  # 诊断开关：ORI_CONSOLE=1 出控制台版
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # EXE 图标：Open-RealtimeASR-UI 声浪图标（多尺寸 PNG 内嵌 ICO，同图 assets/app_icon.png）
    icon='asr_voice/assets/app_icon.ico',
    # Windows 文件属性（详细信息）由 version_info.txt 显式控制，
    # 避免 PyInstaller 默认资源与内部命名不一致。
    version='version_info.txt',
)

# 双产物形态（同一份 Analysis，构建命令不变）：
# - 默认 onefile：单个 ORI.exe，运行时自解压到 %TEMP%（GitHub Release 现产物）
# - ORI_PACKAGE=onedir：目录形态 dist/ORI/——EXE 只装引导代码，COLLECT 把依赖
#   平铺进 _internal/。无 %TEMP% 解包（杀软启发式面更小、启动更快）；
#   sys.frozen 仍为 True、sys._MEIPASS 指向 _internal/，主题/字体/配置模板定位
#   与 paths.py 的「exe 同目录」语义和 onefile 完全一致，应用代码零改动。
if os.environ.get('ORI_PACKAGE') == 'onedir':
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_EXE_KW)
    COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[],
            name=APP_NAME)
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None,
              **_EXE_KW)
