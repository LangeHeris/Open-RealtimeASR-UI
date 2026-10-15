"""应用级字体管理：用户配置字体 > 内置默认字体（普惠体 Medium）> Qt 默认。

config.yaml 可选 ui.font_file（任意 .ttf/.otf 路径）或 ui.font_family（系统已装 family），
都不填则用内置 fonts/AlibabaPuHuiTi-3-65-Medium.ttf，文件缺失才交给 Qt 默认。
相对路径基于 exe 目录（打包版）或项目根（源码版）解析——PyInstaller onefile 的 _MEIPASS
临时目录每次运行都不同，绝不能作为字体落点或绝对路径基准。

像素主题专用字体（Fusion Pixel Font 10px 点阵，OFL-1.1 可随包内置）**只用于气泡正文**，
右键菜单沿用应用常规字体，与上述用户字体体系互不干扰。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo

logger = logging.getLogger(__name__)

# 启动后缓存：用户指定的 family（最高优先）；加载的 font_file 解析出来的 family
_user_family: str | None = None  # config 中显式写的 family
_loaded_file_family: str | None = None  # font_file 加载后得到的 family
_loaded_file_path: str | None = None  # 最近一次成功加载的文件路径
# 最近一次 apply_default_font 实际生效的 family（含雅黑回退结果；None 表示
# 尚未应用过）。get_font / get_loaded_family 读取它，保证回退后悬浮条
# 绘制与菜单 QSS 不再停留在旧 family
_effective_family: str | None = None
# 已成功注册进 QFontDatabase 的文件（解析后绝对路径 -> family）：
# 同一文件只注册一次，多字重批量加载时据此去重
_registered_fonts: dict[str, str] = {}


def fonts_dir() -> Path:
    """字体目录：exe 同目录（打包版）/ 安装根（发行 depot）/ 项目根（源码版）下 fonts/。

    不能源码锚定：打包版会落到 _MEIPASS（重启即失）；发行 depot 裸跑时 __file__ 上溯两级是
    app/，而随包字体在 <安装根>/fonts/。锚错会静默回退 Qt 默认字体，很难发现。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "fonts"
    from ..paths import install_dir

    depot_root = install_dir()
    if depot_root is not None:
        return depot_root / "fonts"
    return Path(__file__).resolve().parent.parent.parent / "fonts"


def _bundled_fonts_dir() -> Path | None:
    """内置字体随 exe 打包的只读目录（onefile 解包到 _MEIPASS/fonts）。

    spec 用 datas 把内置字重打进 exe，运行时解包在 _MEIPASS 临时目录——
    与可写的用户目录 fonts_dir() 不是同一处，读取时必须单独探测。
    """
    if getattr(sys, "frozen", False):
        p = Path(getattr(sys, "_MEIPASS", "")) / "fonts"
        return p if p.is_dir() else None
    return None


def _font_dirs() -> list[Path]:
    """候选字体目录：用户目录（可写，可继续投放字重）在前，内置目录兜底。

    工坊字体**不走这里**——它不是"目录"而是一批具体文件，由 `_workshop_font_files()` 单独收集。
    """
    dirs = [fonts_dir()]
    b = _bundled_fonts_dir()
    if b is not None and b != dirs[0]:
        dirs.append(b)
    return dirs


def _workshop_font_files() -> list[Path]:
    """已安装工坊物品里的字体文件（无工坊 / 不可用时为空）。"""
    try:
        from ..workshop import font_files
        return font_files()
    except Exception:
        logger.debug("查询工坊字体失败", exc_info=True)
        return []


def load_workshop_fonts() -> list[tuple[str, Path]]:
    """注册全部工坊字体，返回 [(family, 文件路径), ...]（供设置页列出可选字体）。

    注册失败只跳过该文件、记警告——一个坏字体不该让整批都用不了。**不碰 `_loaded_*` 状态**：
    只是"把字体放进数据库"，选不选是用户的事。
    """
    out: list[tuple[str, Path]] = []
    for path in _workshop_font_files():
        family = _register_font(str(path))
        if family is None:
            logger.warning("工坊字体注册失败（跳过）：%s", path)
            continue
        out.append((family, path))
    if out:
        logger.info("工坊字体已注册 %s 个", len(out))
    return out


def resolve_font_path(path: str) -> str:
    """解析 config 中的 font_file：相对路径基于程序数据目录，绝对路径原样。

    兼容旧版存过的绝对路径（含失效的 _MEIxxxx 临时路径）——文件不存在
    时由 load_font_file 走既有的"文件不存在"回退逻辑。
    """
    if not path:
        return path
    p = Path(path)
    if p.is_absolute():
        return path
    return str(fonts_dir().parent / p)


def _register_font(path: str) -> str | None:
    """把字体文件注册进 QFontDatabase，返回其 family 名（None 表示失败）。

    无状态助手：不触碰 _loaded_* / _effective_family（批量注册多字重时不顶替用户所选字体）；
    同一文件经 _registered_fonts 去重，重复调用返回缓存 family。
    """
    p = Path(path)
    if not p.is_file():
        logger.warning("字体文件不存在，注册跳过：%s", path)
        return None
    try:
        key = str(p.resolve())
    except OSError:
        key = str(p)
    if key in _registered_fonts:
        return _registered_fonts[key]
    font_id = QFontDatabase.addApplicationFont(str(p))
    if font_id < 0:
        logger.warning(
            "字体文件注册失败（addApplicationFont 返回 -1，文件可能损坏或格式不受支持）：%s",
            path)
        return None
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        logger.warning("字体文件不包含可识别 family：%s", path)
        return None
    _registered_fonts[key] = families[0]
    logger.info("已注册字体文件：%s -> family=%s", path, families[0])
    return families[0]


def load_font_file(path: str) -> str | None:
    """加载指定 .ttf / .otf 文件，返回其 family 名（None 表示失败）。

    用户配置入口：经 _register_font 注册并维护"最近加载"状态供诊断；文件不存在或注册
    失败时清空该状态，避免残留无效路径。
    """
    global _loaded_file_family, _loaded_file_path
    p = Path(path)
    if not p.is_file():
        logger.warning("字体文件不存在：%s", path)
        _loaded_file_family = None
        _loaded_file_path = None
        return None
    family = _register_font(path)
    if family is None:
        _loaded_file_family = None
        _loaded_file_path = None
        return None
    _loaded_file_family = family
    _loaded_file_path = str(p)
    return family


def set_user_family(family: str) -> None:
    """记录 config 中显式写的 family（不一定对应已加载文件，由 Qt 自行查找）。"""
    global _user_family
    _user_family = family.strip() or None
    logger.info("用户指定 font_family: %s", _user_family or "<空>")


# fonts/ 目录下普惠体 3.0 各字重文件的匹配模式：
# 55 Regular / 65 Medium / 75 SemiBold / 85 Bold / 115 Black ...
_PUHUITI_GLOBS = ("AlibabaPuHuiTi*.ttf", "AlibabaPuHuiTi*.otf")

# 内置默认字体：未配置 font_file/font_family 时使用（普惠体 65 Medium
# 观感比系统默认雅黑 Regular 饱满）。相对路径走 resolve_font_path 锚定
_BUILTIN_FONT_FILE = "fonts/AlibabaPuHuiTi-3-65-Medium.ttf"


def load_puhuiti_weights() -> str | None:
    """把候选目录下全部 AlibabaPuHuiTi 字重文件注册进 QFontDatabase。

    各字重 TTF 共用同一 family，逐个 addApplicationFont 后 Qt 自动合并 styles——Normal/Bold
    请求各自命中对应字面。只做注册不触碰 _loaded_*，单文件失败仅警告；返回解析出的 family。
    """
    family: str | None = None
    found_any = False
    for d in _font_dirs():
        if not d.is_dir():
            continue
        for pat in _PUHUITI_GLOBS:
            for p in sorted(d.glob(pat)):
                found_any = True
                fam = _register_font(str(p))
                if fam is None:
                    logger.warning("普惠体字重文件注册失败（跳过）：%s", p)
                    continue
                if family is None:
                    family = fam
    if not found_any:
        return None
    if family:
        logger.info("普惠体多字重注册完成：family=%s styles=%s",
                    family, QFontDatabase.styles(family))
    return family


# 视作常规字重（Normal 请求可命中）的 styleName 关键字：各厂商对
# Regular 的叫法不一（Regular/Normal/Book/Roman），做子串匹配兜底
_REGULAR_STYLE_HINTS = ("regular", "normal", "book", "roman")


def _family_has_regular(family: str) -> bool:
    """判断 family 是否含 Regular（Normal 请求可命中）的字面。

    styleName 含常规字重关键字（Regular/Normal/Book/Roman，不区分大小写）即视为有；查不到
    任何 style 时返回 True，不强行回退，交给 Qt 自行解析。
    """
    try:
        styles = QFontDatabase.styles(family)
    except Exception:
        return True
    if not styles:
        return True
    return any(any(h in s.lower() for h in _REGULAR_STYLE_HINTS)
               for s in styles)


def _is_puhuiti(family: str, path: str) -> bool:
    """判断本次 font_file 是否普惠体系列：family 或文件名含 puhuiti。"""
    marker = "puhuiti"
    return marker in family.lower() or marker in Path(path).name.lower()


def apply_default_font(app, family: str = "", font_file: str = "",
                       app_point_size: int = 9) -> str:
    """设置 QApplication 默认字体，返回最终选用的 family 名。

    优先级：font_file 加载结果 > family（Qt 查系统库）> 内置普惠体 65 Medium，文件缺失才保持
    Qt 默认。生效 family 写入 _effective_family（get_font 同源）；无 Regular 字面则回退雅黑。
    """
    global _effective_family
    set_user_family(family)

    chosen: str | None = None
    if font_file:
        # config 里的相对路径（fonts/xxx.ttf）在此统一解析：
        # 打包版基于 exe 目录、源码版基于项目根（见 resolve_font_path）
        chosen = load_font_file(resolve_font_path(font_file))
        if not chosen:
            logger.warning("font_file 加载失败，回退到 font_family 或默认")
        elif _is_puhuiti(chosen, font_file):
            # 普惠体系列：同族其余字重一并注册（同 family 自动合并 styles）；
            # 非普惠体不批量扫描，避免无谓的注册开销
            load_puhuiti_weights()
    if not chosen and _user_family:
        chosen = _user_family
    if not chosen:
        # 内置默认字体：打包版从解包目录（_MEIPASS/fonts）、源码版从
        # 项目根 fonts/ 查找；文件缺失（构建未投放 fonts/）返回 None
        # 继续落 Qt 默认
        for d in _font_dirs():
            p = d / Path(_BUILTIN_FONT_FILE).name
            if not p.is_file():
                continue
            chosen = _register_font(str(p))
            if chosen:
                break
        if chosen:
            load_puhuiti_weights()

    if chosen:
        if not _family_has_regular(chosen):
            logger.warning(
                "family %r 无 Regular 字面（styles=%s），正文回退 Microsoft YaHei UI",
                chosen, QFontDatabase.styles(chosen))
            chosen = "Microsoft YaHei UI"
        f = QFont(chosen, app_point_size)
        f.setWeight(QFont.Normal)
        f.setStyleStrategy(QFont.PreferAntialias)
        app.setFont(f)
        # 记录实际命中的字面，便于诊断"请求 Normal 却渲染成 Bold"类问题
        info = QFontInfo(f)
        logger.info("QApplication 默认字体：%s %dpt -> 命中 family=%s styleName=%s",
                    chosen, app_point_size, info.family(), info.styleName())
        _effective_family = chosen
        return chosen

    # 完全不设字体，交给 Qt 默认
    logger.info("未指定字体，使用 Qt 默认")
    _effective_family = ""
    return ""


def get_font(point_size: int = 9, weight: QFont.Weight = QFont.Weight.Medium) -> QFont:
    """取一个 QFont 实例，应用于 paintEvent 显式指定字体（默认 Medium 字重）。

    优先级：_effective_family > font_file family > 用户 family > QApplication 默认 > 雅黑。
    空 family 绝不用 QFont("")（Windows 上会解析为无 CJK、发糊的 MS Sans Serif 位图字体）。
    """
    family = (_effective_family if _effective_family is not None
              else (_loaded_file_family or _user_family)) or ""
    if not family:
        # 函数内延迟导入，避免模块加载期引入 QtWidgets 依赖
        from PySide6.QtWidgets import QApplication
        inst = QApplication.instance()
        app_family = inst.font().family() if inst is not None else ""
        family = app_family or "Microsoft YaHei UI"
    f = QFont(family, point_size)
    f.setWeight(weight)
    f.setStyleStrategy(QFont.PreferAntialias)
    return f


def get_loaded_family() -> str:
    """返回当前生效的 family 名（用于菜单 QSS font-family 声明）。

    与 get_font 同源：apply_default_font 实际生效结果（含雅黑回退）。
    """
    return (_effective_family if _effective_family is not None
            else (_loaded_file_family or _user_family)) or ""


# ---------------------------------------------------------------------------
# 像素主题专用字体（缝合像素字体 Fusion Pixel Font）
# 授权：SIL Open Font License 1.1（OFL 全文随文件投放于 fonts/），个人/企业均可
# 免费商用与捆绑再分发。不要改用 Zpix：商用收费且禁止修改/再分发，不可随包内置。

# 点阵规格 10px（9px 字身 + 1px 间距）只在点阵整数倍字号下成立——干净档是 15pt/30pt
# （20/40px），其余字号笔画落到半像素、网格错位发虚。故像素主题字号必须吸附到这几档
# （见 snap_pixel_pt），不能用配置里的任意字号。
# ---------------------------------------------------------------------------

# 像素字体文件名（fonts/ 下；随包投放，缺失则气泡退回常规字体）。仅气泡正文使用：
# 10px 点阵规格、2× = 20px。右键菜单不用像素字体（走应用常规字体与字号）。
# 通用铁律：点阵字号只能是点阵规格的整数倍，非整数倍一律发糊。
_PIXEL_FONT_FILE = "fonts/FusionPixel10px-zh_hans.ttf"

# 像素字号档（pt @96dpi -> 汉字宽 px），10px 点阵的整数倍：
# 15pt=20px（2×，气泡正文档） / 30pt=40px（4×，备用）
PIXEL_PT_TIERS: tuple[int, ...] = (15, 30)

# 基准字号：15pt @96dpi = 20px = 10px 点阵的 2×（本主题实际用档）
PIXEL_BASE_PT = 15

# 气泡正文档：15pt = 20px
PIXEL_TEXT_PT = 15
PIXEL_TEXT_PX = 20

# 已解析的像素字体 family 缓存（""=已确认不可用，None=尚未探测）
_pixel_family: str | None = None
_pixel_probed = False


def _probe_pixel_file(font_file: str) -> str:
    """在字体目录里找 font_file 并注册，返回 family；缺失/注册失败返回空串。"""
    for d in _font_dirs():
        p = d / Path(font_file).name
        if not p.is_file():
            continue
        fam = _register_font(str(p))
        if fam:
            logger.info("像素字体可用：%s -> family=%s", p, fam)
            return fam
    logger.info("像素字体不可用（fonts/%s 缺失或注册失败）", Path(font_file).name)
    return ""


def pixel_family() -> str:
    """返回**气泡正文**像素字体 family（10px 规格）；不可用时返回空串。

    懒加载 + 缓存：首次调用才去 fonts/ 找文件并注册（像素主题未被选中时
    零开销）。探测失败记空串，避免每次重绘都重新 glob 目录。
    """
    global _pixel_family, _pixel_probed
    if _pixel_probed:
        return _pixel_family or ""
    _pixel_probed = True
    _pixel_family = _probe_pixel_file(_PIXEL_FONT_FILE)
    return _pixel_family


def _snap_pt(point_size: int, tiers: tuple[int, ...]) -> int:
    """把字号吸附到**不小于它的**最近档；超上限钳到最大档。"""
    for tier in tiers:
        if tier >= point_size:
            return tier
    return tiers[-1]


def snap_pixel_pt(point_size: int) -> int:
    """把任意字号吸附到**不小于它的**最近档（10px 规格：PIXEL_PT_TIERS）。

    像素字体只在点阵整数倍下成立，非整数倍会网格错位。用**向上取整**而非"取最近档"
    （取最近会让"设了 13pt 反而变小"，反直觉）；超过最大档钳到最大档。
    """
    return _snap_pt(point_size, PIXEL_PT_TIERS)


def get_pixel_font(point_size: int = PIXEL_BASE_PT,
                   weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """取像素主题用 QFont（**10px 点阵规格**）；字体缺失时回退常规字体。

    point_size 先经 snap_pixel_pt 吸附。字重固定 Normal——像素字体只有单一 Regular 字面，
    请求 Medium/Bold 会让 Qt 合成加粗、点阵硬边尽失；关掉字形微调与抗锯齿（须按格点落位）。
    """
    fam = pixel_family()
    if not fam:
        return get_font(point_size, weight)
    f = QFont(fam, snap_pixel_pt(point_size))
    f.setWeight(weight)
    # 点阵字体不做抗锯齿（否则硬边被抹成灰阶）；PreferAntialias 会反其道
    f.setStyleStrategy(QFont.NoAntialias)
    # 关闭字距调整/微调，避免半像素定位破坏点阵网格
    f.setKerning(False)
    f.setFixedPitch(True)
    return f


def get_pixel_font_family() -> str:
    """**气泡正文**像素 family 名（供 QSS font-family 声明用）。

    不可用时回退 get_loaded_family()，保证 QSS 里的 family 一定可解析。
    注意：右键菜单不使用像素字体（见 menu_qss），本函数只服务气泡。
    """
    return pixel_family() or get_loaded_family()
