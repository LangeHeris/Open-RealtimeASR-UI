"""配置加载与校验。

加载顺序：默认值 <- config.yaml（深合并 + 类型归一化）。
密钥不会在 __repr__ 中明文输出。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import yaml

from .defaults import DEFAULTS

# 进程内写锁：update_config_field 是"读-改-写"操作，设置页多个后台写
# 线程（引擎排序/显隐开关等）并发时窗口重叠会产生撕裂文件（曾出现行尾
# 残留"alse"导致 YAML 解析失败、热加载报错），必须串行化。
# 跨进程安全由 _atomic_write 保证（临时文件 + os.replace，读方永远
# 看不到中间态）；锁只负责进程内线程串行。
_CONFIG_WRITE_LOCK = threading.Lock()


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并 override 到 base，返回新 dict。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _to_namespace(d: Any) -> Any:
    """dict 转 SimpleNamespace，支持属性访问。"""
    if isinstance(d, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in d.items()})
    return d


_BOOL_TRUE = {"true", "yes", "on", "1"}
_BOOL_FALSE = {"false", "no", "off", "0", ""}


def _coerce_value(default_val: Any, user_val: Any) -> Any:
    """按默认值类型把用户配置中的字符串转换回正确类型。

    yaml 手写或 UI 写入的值可能带引号变成 str（如 '500' / 'false'），
    消费端做算术/逻辑运算会崩，这里统一在加载时归一化。
    """
    if not isinstance(user_val, str):
        return user_val
    # 注意：bool 是 int 的子类，必须先判 bool
    if isinstance(default_val, bool):
        low = user_val.strip().lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
        return user_val
    if isinstance(default_val, int):
        try:
            return int(float(user_val.strip()))
        except ValueError:
            return user_val
    if isinstance(default_val, float):
        try:
            return float(user_val.strip())
        except ValueError:
            return user_val
    return user_val


def _coerce_types(cfg: dict, defaults: dict) -> dict:
    """遍历默认配置树，把 cfg 中同路径的 str 值归一化为默认值类型。"""
    for key, dval in defaults.items():
        if isinstance(dval, dict):
            sub = cfg.get(key)
            if isinstance(sub, dict):
                _coerce_types(sub, dval)
        elif key in cfg:
            cfg[key] = _coerce_value(dval, cfg[key])
    return cfg


def find_config_path() -> Path | None:
    """按优先级查找配置文件。打包后优先 exe 同目录。"""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后：exe 同目录
        exe_dir = Path(sys.executable).parent
        candidates.append(exe_dir / "config.yaml")
        candidates.append(exe_dir / "config" / "config.yaml")
    else:
        # 源码模式：项目根。开机自启等场景工作目录是 system32，
        # 相对路径候选全部落空，必须锚定源码位置
        src_root = Path(__file__).resolve().parent.parent.parent
        candidates.append(src_root / "config" / "config.yaml")
        candidates.append(src_root / "config.yaml")
    # 当前工作目录
    candidates.append(Path("config/config.yaml"))
    candidates.append(Path("config.yaml"))
    # 用户主目录
    candidates.append(Path.home() / ".asr_voice" / "config.yaml")

    for path in candidates:
        if path.exists():
            return path
    return None


def _default_config_template() -> Path | None:
    """内置配置模板路径：打包后取 exe 内嵌副本，源码模式取仓库 example。"""
    if getattr(sys, "frozen", False):
        # spec 中 datas 打包到解包根目录（onefile: sys._MEIPASS）
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "config.example.yaml"
        return bundled if bundled.exists() else None
    example = Path(__file__).resolve().parent.parent.parent / "config" / "config.example.yaml"
    return example if example.exists() else None


def ensure_default_config(default_engine: str | None = None) -> Path | None:
    """首次运行自动生成 config.yaml（基于 config.example.yaml 模板）。

    没有配置文件时 update_config_field 会静默失败（设置界面填的密钥
    不持久化），必须在启动早期保证文件存在：
    - 打包版：生成到 exe 同目录；目录不可写（如 Program Files）时
      退到 ~/.asr_voice/config.yaml
    - 源码版：生成到仓库 config/config.yaml（已被 .gitignore 排除）

    default_engine 非空时，生成文件里的 engine 行改写为发行默认引擎
    （商店版 funasr，内置离线引擎开箱即用）。

    已有配置文件时为 no-op。返回生成路径，无法生成返回 None。
    """
    import logging

    if find_config_path() is not None:
        return None

    template = _default_config_template()
    if template is None:
        return None

    content = template.read_text(encoding="utf-8")
    logger = logging.getLogger(__name__)
    if default_engine:
        # 模板固定 engine: "aliyun"；发行默认引擎改写首行 engine。
        # 用 subn 取替换计数：0 说明模板形状与预期不符（BOM/缩进/键名不同），
        # 改写会静默落空——写盘后 _load_user_data 会把 aliyun 读成用户显式
        # 选择，读时默认也一并失效，商店版会静默跑错引擎，故必须兜底。
        content, n = re.subn(r'^engine:.*$', f'engine: {default_engine}', content,
                             count=1, flags=re.MULTILINE)
        if n == 0:
            # 回退：前置插入发行默认引擎行（不抛异常，启动流程照常）
            content = f'engine: {default_engine}\n' + content
            logger.warning("配置模板中未找到可改写的 engine 行，已前置插入 "
                           "engine: %s：%s", default_engine, template)
    if getattr(sys, "frozen", False):
        targets = [
            Path(sys.executable).parent / "config.yaml",
            Path.home() / ".asr_voice" / "config.yaml",
        ]
    else:
        targets = [Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"]

    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            logger.info("首次运行，已生成默认配置：%s", target)
            return target
        except OSError:
            logger.warning("配置文件生成失败（目录不可写）：%s", target, exc_info=True)
            continue
    return None


def reset_config_to_defaults() -> Path | None:
    """用内置模板覆盖现有 config.yaml，恢复出厂默认。

    热加载依赖 QFileSystemWatcher，覆盖后由既有监听自动触发重载。
    返回覆盖后的配置路径；无配置文件或模板不可用返回 None。
    """
    import logging
    import shutil

    config_path = find_config_path()
    template = _default_config_template()
    if config_path is None or template is None:
        return None
    shutil.copyfile(template, config_path)
    logging.getLogger(__name__).info("已还原默认配置：%s", config_path)
    return config_path


def load_config(path: str | Path | None = None) -> dict:
    """加载配置：默认值 <- yaml 文件。"""
    cfg = dict(DEFAULTS)

    config_path = Path(path) if path else find_config_path()
    if config_path and config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        cfg = _deep_merge(cfg, user_cfg)
        cfg = _coerce_types(cfg, DEFAULTS)

    # 迁移：旧版 app_key（新版/旧版共用）拆分为独立的 api_key / access_token
    import logging
    _log = logging.getLogger(__name__)
    volc = cfg.get("volcengine", {})
    if isinstance(volc, dict):
        old_key = volc.get("app_key", "")
        if old_key and not volc.get("api_key") and not volc.get("access_token"):
            if volc.get("app_id"):
                volc["access_token"] = old_key
            else:
                volc["api_key"] = old_key
            _log.info("已迁移 volcengine.app_key -> %s",
                      "access_token" if volc.get("app_id") else "api_key")

    return cfg


def _load_user_data(path: str | Path | None = None) -> dict:
    """读取用户配置文件原始 dict（不合并默认值）；文件不存在返回 {}。

    default_engine 语义需要区分「用户显式写了 engine」与「engine 来自
    默认值合并」，load_config 的合并结果无法区分两者。
    """
    config_path = Path(path) if path else find_config_path()
    if config_path and config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _range_error(val: Any, cast, lo: float, hi: float, label: str) -> str | None:
    """校验数值范围（豆包 / Qwen 的 dialog 数值字段共用）。

    config.yaml 经 _coerce_types 只归一化「能安全转换」的字符串，垃圾值
    （如 speech_rate: "abc"）会原样保留；本函数契约是「返回错误文案而非
    抛异常」——validate_config 承诺返回错误列表，绝不能因用户手改错类型
    崩在启动路径上。非法类型/溢出 → 非法数值；超范围 → 超出范围；合法 → None。
    """
    try:
        num = cast(val)
    except (TypeError, ValueError, OverflowError):
        return f"{label} 非法数值：{val!r}"
    if not lo <= num <= hi:
        return f"{label} 超出范围 [{lo},{hi}]：{val}"
    return None


def validate_config(cfg: dict) -> list[str]:
    """返回错误信息列表，空列表表示通过。"""
    errors = []

    engine = cfg.get("engine", "tencent")
    if engine not in ("tencent", "funasr", "aliyun", "xfyun", "volcengine"):
        errors.append(
            f"engine 无效：{engine}（可选：tencent / funasr / aliyun / xfyun / volcengine）"
        )

    # 腾讯云引擎需要密钥；FunASR 引擎不需要；阿里云需要百炼 API Key
    if engine == "tencent":
        tencent = cfg.get("tencent", {})
        if not tencent.get("secret_id"):
            errors.append("tencent.secret_id 未配置")
        if not tencent.get("secret_key"):
            errors.append("tencent.secret_key 未配置")
        if not tencent.get("app_id"):
            errors.append("tencent.app_id 未配置")

        engine_model = tencent.get("engine_model_type", "")
        valid_engines = {
            "16k_zh", "16k_zh-PY", "16k_zh_medical", "16k_en", "16k_yue",
            "16k_ja", "16k_ko", "16k_vi", "16k_ms", "16k_id", "16k_fil",
            "16k_th", "16k_pt", "16k_tr", "16k_ar", "16k_es", "16k_hi",
            "16k_fr", "16k_de", "16k_zh_dialect",
            "16k_zh_en", "16k_multi_lang", "16k_en_large", "8k_zh_large",
            "16k_zh_en_2.0", "16k_zh_en_speaker_2.0",
            "Hy-ASR-3.0-preview",
            "8k_zh", "8k_en",
        }
        if engine_model not in valid_engines:
            errors.append(f"tencent.engine_model_type 无效：{engine_model}")

    if engine == "aliyun":
        if not cfg.get("aliyun", {}).get("api_key"):
            errors.append("aliyun.api_key 未配置")

    if engine == "xfyun":
        xfyun = cfg.get("xfyun", {})
        std = xfyun.get("edition", "llm") == "std"
        # 两服务密钥互不通用：标准版填 std_* 两键，大模型版填原三键
        fields = ("std_app_id", "std_api_key") if std else ("app_id", "api_key", "api_secret")
        for field in fields:
            if not xfyun.get(field):
                errors.append(f"xfyun.{field} 未配置")
        if std:
            if xfyun.get("std_lang", "cn") not in ("cn", "en"):
                errors.append("xfyun.std_lang 无效（可选：cn / en）")
        else:
            lang = xfyun.get("lang", "")
            if lang not in ("autodialect", "autominor"):
                errors.append(
                    f"xfyun.lang 无效：{lang}"
                    "（可选：autodialect=中英+方言 | autominor=37语种，需工单开通）"
                )

    if engine == "volcengine":
        v = cfg.get("volcengine", {})
        has_new = bool(v.get("api_key"))
        has_old = bool(v.get("access_token") and v.get("app_id"))
        if not has_new and not has_old:
            errors.append(
                "volcengine 密钥未配置（新版填 api_key；旧版填 app_id + access_token）"
            )
        # 计费方式（duration/concurrent）两版本各自独立；resource_id 由程序
        # 自动合成，不在此校验（缺失时默认 duration 兜底）
        for fld in ("bigasr_billing", "seedasr_billing"):
            b = v.get(fld)
            if b not in (None, "", "duration", "concurrent"):
                errors.append(f"volcengine.{fld} 无效（可选：duration / concurrent）")

    sample_rate = cfg.get("audio", {}).get("sample_rate")
    if sample_rate != 16000:
        errors.append(
            f"audio.sample_rate 必须为 16000，当前为 {sample_rate}"
        )

    # 长按说话：阈值与采样间隔都要有合理范围，否则 0 或负数会让状态机行为异常
    hk = cfg.get("hotkey", {}) or {}
    for fld, default, lo, hi in (
        ("hold_threshold_ms", 300, 100, 3000),
        ("hold_poll_ms", 20, 5, 200),
    ):
        err = _range_error(hk.get(fld, default), int, lo, hi, f"hotkey.{fld}")
        if err:
            errors.append(err)

    # 语音对话（豆包 S2S）：只查范围，不查密钥（dialog 节密钥留空时回退
    # volcengine 节，回退后是否就绪由 dialog_configured 判定，不阻塞启动）
    dialog = cfg.get("dialog", {})
    model = dialog.get("model", "1.2.1.1")
    if model not in ("1.2.1.1", "2.2.0.0"):
        errors.append(
            f"dialog.model 无效：{model}（可选：1.2.1.1=O2.0 通用对话 | 2.2.0.0=SC2.0 角色扮演）"
        )
    for fld in ("speech_rate", "loudness_rate"):
        err = _range_error(dialog.get(fld, 0), int, -50, 100, f"dialog.{fld}")
        if err:
            errors.append(err)
    err = _range_error(dialog.get("end_smooth_window_ms", 800), int,
                       500, 50000, "dialog.end_smooth_window_ms")
    if err:
        errors.append(err)

    # 语音对话（阿里云 Qwen-Audio Realtime）：provider/region/turn_detection 枚举
    # + 数值范围。qwen 子节缺失时全走默认值（向后兼容旧配置），零报错。
    provider = str(dialog.get("provider", "doubao") or "doubao").strip().lower()
    if provider not in ("doubao", "aliyun", "hermes"):
        errors.append(
            f"dialog.provider 无效：{provider}（可选：doubao | aliyun | hermes）")
    qwen = dialog.get("qwen", {}) or {}
    region = str(qwen.get("region", "legacy") or "legacy").strip().lower()
    if region not in ("legacy", "beijing", "singapore"):
        errors.append(f"dialog.qwen.region 无效：{region}"
                      f"（可选：legacy | beijing | singapore）")
    elif region != "legacy" and not str(qwen.get("workspace_id", "") or "").strip():
        errors.append(f"dialog.qwen.region={region} 时 workspace_id 必填")
    td = str(qwen.get("turn_detection", "server_vad") or "server_vad")
    if td not in ("server_vad", "smart_turn"):
        errors.append(f"dialog.qwen.turn_detection 无效：{td}"
                      f"（可选：server_vad | smart_turn）")
    # 数值字段：畸形值（非数字/溢出）记为校验错误而非抛异常，统一走
    # _range_error（与豆包字段同一防线；_coerce_types 只归一化能安全转换
    # 的字符串，垃圾值仍会直达这里）。
    for fld, default, cast, lo, hi in (
        ("vad_threshold", 0.5, float, -1.0, 1.0),
        ("silence_duration_ms", 800, int, 200, 6000),
        ("max_history_turns", 20, int, 1, 50),
    ):
        err = _range_error(qwen.get(fld, default), cast, lo, hi,
                           f"dialog.qwen.{fld}")
        if err:
            errors.append(err)
    if bool(qwen.get("enable_search", False)):
        # model 留空 = 走默认（plus），此时 enable_search 合法、不报警
        qm = str(qwen.get("model", "") or "")
        if qm and qm not in ("qwen-audio-3.0-realtime-plus",
                             "qwen-audio-3.0-realtime-flash"):
            errors.append(f"dialog.qwen.enable_search 仅在 plus/flash 模型生效，"
                          f"当前 model={qm}")

    return errors


# ui.theme_name 的历史中文显示名 → 内部键（spec A4）。主题键参与持久化、
# 将来还要过 i18n，不能存界面语言相关的名字。含两个已下线的品牌旧名
# （Mabinogi 即今「凯尔特风格」，大小写两种写法都在野外见过）。
LEGACY_THEME_NAMES = {
    "浅色": "light",
    "深色": "dark",
    "极简": "minimal",
    "像素": "pixel",
    "凯尔特风格": "celtic",
    "赛博朋克风": "cyberpunk",
    "赛博朋克2077": "cyberpunk",
    "Mabinogi": "celtic",
    "mabinogi": "celtic",
}


def _migrate_legacy_theme(cfg_ns: SimpleNamespace,
                          config_path: str | Path | None = None) -> None:
    """一次性迁移：旧中文主题名 → 内部键，就地改 namespace 并回写同一份文件。

    只认得出来的旧名才动，认不出来（第三方主题包）原样保留——不替用户做决定。
    config_path 必须与本次加载的来源一致（`--config` 场景见 update_config_field
    注释），否则会把迁移结果写进用户没指定的那份配置。

    回写失败不影响本次运行：内存里已是内部键，下次加载还会再迁；因此写盘异常
    只 warning 一笔（不打扰用户启动），缺少目标文件则 debug（首次运行属正常）。
    """
    ui = getattr(cfg_ns, "ui", None)
    if ui is None:
        return
    old = str(getattr(ui, "theme_name", "") or "").strip()
    new = LEGACY_THEME_NAMES.get(old)
    if not new:
        return
    ui.theme_name = new
    logger = logging.getLogger(__name__)
    try:
        if not update_config_field("theme_name", new, section="ui",
                                   path=config_path):
            logger.debug("主题名迁移未落盘（无配置文件或回读校验失败），下次再试")
    except Exception:
        logger.warning("主题名迁移回写失败（下次启动再试）", exc_info=True)


# ui.settings_style 的历史中文显示名 → 内部键（A6）。
# 与 theme_name 同一种局面：值写进 config、又要承载界面语言，故一并内部键化。
LEGACY_SETTINGS_STYLE_NAMES = {
    "极简暗色": "minimal_dark",
    "流畅浅色": "fluent_light",
}


def _migrate_legacy_settings_style(cfg_ns: SimpleNamespace,
                                   config_path: str | Path | None = None) -> None:
    """一次性迁移：旧中文设置界面主题名 → 内部键，做法同 `_migrate_legacy_theme`。

    认不出的取值原样保留（第三方/手改值不替用户做决定）：后续
    `_theme_palette` 遇到未知键会回退默认皮肤，不影响启动。
    """
    ui = getattr(cfg_ns, "ui", None)
    if ui is None:
        return
    old = str(getattr(ui, "settings_style", "") or "").strip()
    new = LEGACY_SETTINGS_STYLE_NAMES.get(old)
    if not new:
        return
    ui.settings_style = new
    logger = logging.getLogger(__name__)
    try:
        if not update_config_field("settings_style", new, section="ui",
                                   path=config_path):
            logger.debug("设置界面主题迁移未落盘（无配置文件或回读校验失败），下次再试")
    except Exception:
        logger.warning("设置界面主题迁移回写失败（下次启动再试）", exc_info=True)


def _migrate_ui_legacy_values(cfg_ns: SimpleNamespace,
                              config_path: str | Path | None = None) -> None:
    """UI 层持久化键的迁移总入口：加字段时往这里挂一个即可。

    两个字段共用一次加载流程，避免每加一个键就在两处 get_config 里补调用。
    """
    _migrate_legacy_theme(cfg_ns, config_path)
    _migrate_legacy_settings_style(cfg_ns, config_path)


def get_config(path: str | Path | None = None,
               default_engine: str | None = None) -> SimpleNamespace:
    """加载并校验配置，返回 SimpleNamespace。配置不合法时抛出 ValueError。

    default_engine：用户配置文件显式含 engine 键时忽略；否则合并结果
    engine = default_engine（发行层默认引擎，商店版传 funasr）。
    """
    merged = load_config(path)
    data = _load_user_data(path)
    if default_engine and "engine" not in data:
        merged["engine"] = default_engine
    errors = validate_config(merged)
    if errors:
        raise ValueError("配置校验失败：\n  - " + "\n  - ".join(errors))
    ns = _to_namespace(merged)
    _migrate_ui_legacy_values(ns, path)
    return ns


def get_config_unchecked(path: str | Path | None = None,
                         default_engine: str | None = None) -> SimpleNamespace:
    """加载配置但不校验密钥，返回 SimpleNamespace。密钥可能为空，用于先启动 UI 再提示配置。

    default_engine 语义同 get_config。
    """
    merged = load_config(path)
    data = _load_user_data(path)
    if default_engine and "engine" not in data:
        merged["engine"] = default_engine
    ns = _to_namespace(merged)
    _migrate_ui_legacy_values(ns, path)
    return ns


_PLACEHOLDERS = {"", "your_secret_id", "your_secret_key", "your_app_id", "your_api_key", "sk-xxx"}


def is_configured(cfg) -> bool:
    """检查当前引擎是否已配置就绪。

    config 中 engine 存基础名（tencent/aliyun/xfyun/volcengine/funasr），
    按组判断委托给 engine_group_configured。
    """
    if isinstance(cfg, dict):
        engine = cfg.get("engine", "tencent")
    else:
        engine = getattr(cfg, "engine", "tencent")
    # 容错：菜单项带模型后缀（如 "funasr:xxx"）时取基础名
    group = engine.split(":", 1)[0] if engine else "tencent"
    return engine_group_configured(cfg, group)


# funasr 依赖可用性缓存：find_spec 只查搜索路径不执行包代码，微秒级，
# 但没必要每次构建菜单都查
_FUNASR_CHECK_TTL = 60.0  # 菜单每次构建都调用，短缓存避免次次扫 sys.path
_funasr_available_cache: bool | None = None
_funasr_checked_at: float = 0.0


def funasr_available() -> bool:
    """funasr 依赖是否可导入（打包版/未安装环境为 False）。

    结果缓存 60 秒：右键菜单/引擎菜单每次构建都会经 presets 与
    engine_group_configured 调到这里，不能次次扫 sys.path；短缓存
    兼顾“运行中安装 funasr 后能被发现”（永久缓存则永远看不到）。
    本函数是全项目唯一实现（I09 去重，原 presets._funasr_available
    与本模块旧版 _funasr_dependency_available 均已收口至此）。
    """
    global _funasr_available_cache, _funasr_checked_at
    import time

    now = time.time()
    if (_funasr_available_cache is not None
            and now - _funasr_checked_at < _FUNASR_CHECK_TTL):
        return _funasr_available_cache
    try:
        import importlib.util
        _funasr_available_cache = importlib.util.find_spec("funasr") is not None
    except ImportError:
        _funasr_available_cache = False
    _funasr_checked_at = now
    return _funasr_available_cache


def engine_group_configured(cfg, group: str) -> bool:
    """检查指定引擎组的密钥是否已配置（右键菜单过滤用）。

    - tencent：检查密钥是否已填写真实值
    - aliyun：检查百炼 API Key 是否已填写真实值
    - xfyun：标准版检查 std_app_id/std_api_key，大模型版检查三键均为真实值
    - volcengine：检查新版 API Key（api_key）或旧版双密钥（access_token+app_id）已填
    - funasr：无需密钥，返回依赖可用性（打包版不含 funasr/torch）。
      此判断用于录音启动/就绪判定；右键菜单不再据此隐藏 funasr 组，
      显隐由设置的 engine_show_funasr 开关控制（见 ui/context_menu.py）
    """
    if isinstance(cfg, dict):
        t = cfg.get("tencent", {})
        sid = t.get("secret_id", "")
        skey = t.get("secret_key", "")
        appid = t.get("app_id", "")
        aliyun_key = cfg.get("aliyun", {}).get("api_key", "")
        x = cfg.get("xfyun", {})
        xfyun_keys = [x.get("app_id", ""), x.get("api_key", ""), x.get("api_secret", "")]
        xfyun_std_keys = [x.get("std_app_id", ""), x.get("std_api_key", "")]
        xfyun_std = x.get("edition", "llm") == "std"
        v = cfg.get("volcengine", {})
        volc_new_key = v.get("api_key", "")
        volc_access_token = v.get("access_token", "")
        volc_app_id = v.get("app_id", "")
    else:
        t = getattr(cfg, "tencent", None)
        sid = getattr(t, "secret_id", "") if t else ""
        skey = getattr(t, "secret_key", "") if t else ""
        appid = getattr(t, "app_id", "") if t else ""
        a = getattr(cfg, "aliyun", None)
        aliyun_key = getattr(a, "api_key", "") if a else ""
        x = getattr(cfg, "xfyun", None)
        xfyun_keys = [
            (getattr(x, "app_id", "") if x else ""),
            (getattr(x, "api_key", "") if x else ""),
            (getattr(x, "api_secret", "") if x else ""),
        ]
        xfyun_std_keys = [
            (getattr(x, "std_app_id", "") if x else ""),
            (getattr(x, "std_api_key", "") if x else ""),
        ]
        xfyun_std = (getattr(x, "edition", "llm") if x else "llm") == "std"
        v = getattr(cfg, "volcengine", None)
        volc_new_key = (getattr(v, "api_key", "") if v else "")
        volc_access_token = (getattr(v, "access_token", "") if v else "")
        volc_app_id = (getattr(v, "app_id", "") if v else "")

    def _real(s: str) -> bool:
        return s.strip().lower() not in _PLACEHOLDERS

    if group == "funasr":
        return funasr_available()

    if group == "aliyun":
        return _real(aliyun_key)

    if group == "xfyun":
        # 标准版：std_app_id+std_api_key；大模型版：三键齐全
        if xfyun_std:
            return all(_real(k) for k in xfyun_std_keys)
        return all(_real(k) for k in xfyun_keys)

    if group == "volcengine":
        return _real(volc_new_key) or (
            _real(volc_access_token) and _real(volc_app_id)
        )

    return (
        sid.strip().lower() not in _PLACEHOLDERS
        and skey.strip().lower() not in _PLACEHOLDERS
        and appid.strip().lower() not in _PLACEHOLDERS
    )


def resolve_dialog_credentials(cfg) -> tuple[str, str, str]:
    """对话模式密钥：`dialog` 节优先，逐字段留空则回退 `volcengine` 节。

    用户通常已在「引擎密钥」页填过火山新版 API Key（volcengine.api_key），
    对话模式复用同一把即可开箱可用，不必填两遍。回退是**逐字段**的：
    dialog 只填了 api_key 时，app_id / access_token 仍可从 volcengine 节取。

    返回 (api_key, app_id, access_token)，均为 str（yaml 空值 None 归一为 ""）。
    同时支持 dict（load_config 返回值）与 SimpleNamespace（get_config 返回值）。
    """
    def _get(section: str, key: str) -> str:
        if isinstance(cfg, dict):
            sec = cfg.get(section) or {}
            return str(sec.get(key, "") or "") if isinstance(sec, dict) else ""
        sec = getattr(cfg, section, None)
        return str(getattr(sec, key, "") or "") if sec is not None else ""

    return (
        _get("dialog", "api_key") or _get("volcengine", "api_key"),
        _get("dialog", "app_id") or _get("volcengine", "app_id"),
        _get("dialog", "access_token") or _get("volcengine", "access_token"),
    )


_QWEN_WS_HOSTS = {
    "beijing": "cn-beijing.maas.aliyuncs.com",
    "singapore": "ap-southeast-1.maas.aliyuncs.com",
}


def build_qwen_ws_url(region: str, workspace_id: str, model: str) -> str:
    """按 region 路由 Qwen-Audio Realtime 的 WebSocket 端点（纯函数，可单测）。

    legacy（旧域名，忽略 workspace_id）用 dashscope 公共端点；beijing/singapore
    （新域名，workspace_id 必填）用 {ws}.{host} 独享端点。model 作 ?model= 查询
    参——query 值做百分号转义（含 & / # / 空格等字符时不破坏 URL 语义）；
    workspace_id 进 host 段无转义语义，非法字符由建连失败错误提示上报。
    """
    region = (region or "legacy").strip().lower()
    model = quote((model or "qwen-audio-3.0-realtime-plus").strip(), safe="")
    if region in _QWEN_WS_HOSTS:
        ws = (workspace_id or "").strip()
        return (f"wss://{ws}.{_QWEN_WS_HOSTS[region]}"
                f"/api-ws/v1/realtime?model={model}")
    return f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={model}"


def dialog_provider(cfg) -> str:
    """读 dialog.provider，归一为 "doubao" | "aliyun" | "hermes"
    （缺失/None/非法 → doubao，保持向后兼容）。"""
    if isinstance(cfg, dict):
        sec = cfg.get("dialog") or {}
        val = sec.get("provider", "doubao") if isinstance(sec, dict) else "doubao"
    else:
        sec = getattr(cfg, "dialog", None)
        val = getattr(sec, "provider", "doubao") if sec is not None else "doubao"
    val = str(val or "doubao").strip().lower()
    return val if val in ("doubao", "aliyun", "hermes") else "doubao"


def resolve_qwen_credentials(cfg) -> tuple[str, str]:
    """Qwen 对话密钥：dialog.qwen.api_key 留空回退复用 aliyun.api_key（同豆包
    回退 volcengine 模式）。返回 (api_key, workspace_id)，均为 str（None 归一 ""）。
    同时支持 dict（load_config）与 SimpleNamespace（get_config）。
    """
    def _qwen(key: str) -> str:
        if isinstance(cfg, dict):
            d = cfg.get("dialog") or {}
            q = (d.get("qwen") or {}) if isinstance(d, dict) else {}
            return str(q.get(key, "") or "") if isinstance(q, dict) else ""
        d = getattr(cfg, "dialog", None)
        q = getattr(d, "qwen", None) if d is not None else None
        return str(getattr(q, key, "") or "") if q is not None else ""

    def _aliyun(key: str) -> str:
        if isinstance(cfg, dict):
            sec = cfg.get("aliyun") or {}
            return str(sec.get(key, "") or "") if isinstance(sec, dict) else ""
        sec = getattr(cfg, "aliyun", None)
        return str(getattr(sec, key, "") or "") if sec is not None else ""

    return _qwen("api_key") or _aliyun("api_key"), _qwen("workspace_id")


def qwen_dialog_configured(cfg) -> bool:
    """Qwen 对话密钥是否就绪：api_key 真实非占位（沿用 _PLACEHOLDERS 过滤）。"""
    api_key, _ = resolve_qwen_credentials(cfg)
    return api_key.strip().lower() not in _PLACEHOLDERS


def _dialog_get(cfg, key: str, default=None):
    """读 cfg.dialog.<key>，dict 与 SimpleNamespace 两种 cfg 通用。

    loader 里所有 dialog 相关读取都要同时支持 `load_config`（dict）与
    `get_config`（SimpleNamespace）两种返回，故统一走这里。
    """
    if isinstance(cfg, dict):
        d = cfg.get("dialog") or {}
        return d.get(key, default) if isinstance(d, dict) else default
    d = getattr(cfg, "dialog", None)
    return getattr(d, key, default) if d is not None else default


def _nested_get(sub, key: str, default=None):
    """读子节（dict 或 SimpleNamespace）里的字段；子节为 None 时给默认值。"""
    if sub is None:
        return default
    if isinstance(sub, dict):
        return sub.get(key, default)
    return getattr(sub, key, default)


def resolve_hermes_credentials(cfg) -> tuple[str, str]:
    """Hermes 连接参数 (base_url, api_key)，均为 str（None 归一 ""）。

    base_url 去掉尾斜杠与误填的 "/v1" 后缀：Hermes 官方文档的 curl 示例
    写的是 "http://host:8642/v1"，用户很可能连后缀一起抄进来，而内部统一
    按 "<base_url>/v1/..." 拼路径，不剥就会拼出 "/v1/v1/..."。
    """
    sub = _dialog_get(cfg, "hermes")
    base = str(_nested_get(sub, "base_url", "") or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    key = str(_nested_get(sub, "api_key", "") or "").strip()
    return base, key


def hermes_dialog_configured(cfg) -> bool:
    """Hermes 连接参数是否就绪：base_url 与 api_key 都非空且非占位。"""
    base, key = resolve_hermes_credentials(cfg)
    return bool(base) and key.strip().lower() not in _PLACEHOLDERS


def dialog_asr_engine(cfg) -> str:
    """对话专属 ASR 引擎名；dialog.asr.engine 留空则跟随顶层 engine。"""
    sub = _dialog_get(cfg, "asr")
    want = str(_nested_get(sub, "engine", "") or "").strip().lower()
    if want:
        return want
    if isinstance(cfg, dict):
        return str(cfg.get("engine", "aliyun") or "aliyun")
    return str(getattr(cfg, "engine", "aliyun") or "aliyun")


def dialog_asr_overrides(cfg) -> dict:
    """对话专属 ASR 的覆盖项，只含非空字段。

    返回空 dict 表示「全部跟随该引擎的常规配置」。空字符串一律视为未设置
    ——设置界面留空即回到跟随，不做「空串覆盖成空」这种反直觉行为。
    """
    sub = _dialog_get(cfg, "asr")
    out = {}
    for k in ("model", "lang", "hotword"):
        v = str(_nested_get(sub, k, "") or "").strip()
        if v:
            out[k] = v
    return out


def dialog_configured(cfg) -> bool:
    """对话模式连接参数是否就绪（provider 感知）。

    provider=="aliyun" 走 Qwen 判定；"hermes" 走 Hermes 判定；
    否则走豆包判定。右键菜单/悬浮条/热键四处入口共用本判定。
    """
    prov = dialog_provider(cfg)
    if prov == "aliyun":
        return qwen_dialog_configured(cfg)
    if prov == "hermes":
        return hermes_dialog_configured(cfg)
    api_key, app_id, access_token = resolve_dialog_credentials(cfg)

    def _real(s: str) -> bool:
        return s.strip().lower() not in _PLACEHOLDERS

    return _real(api_key) or (_real(app_id) and _real(access_token))


def dialog_enabled(cfg) -> bool:
    """语音对话功能总开关（`dialog.enable`）。

    关掉后右键菜单「语音模式」项、对话热键与悬浮条的对话态入口全部下线，
    仅设置页保留以便重新开启。四处入口共用本判定，不各写一遍。

    **缺失与 None 一律当启用**：升级后的旧配置没有这个键、或 yaml 里写
    `enable:` 留空解析成 None，都不应该让功能凭空消失（消失比存在更难排查）；
    只有显式假值（False / 0 / 空串）才算关闭。同时支持 dict（load_config
    返回值）与 SimpleNamespace（get_config 返回值）。
    """
    if isinstance(cfg, dict):
        sec = cfg.get("dialog") or {}
        val = sec.get("enable", True) if isinstance(sec, dict) else True
    else:
        sec = getattr(cfg, "dialog", None)
        val = getattr(sec, "enable", True) if sec is not None else True
    return True if val is None else bool(val)


# os.replace 的瞬时失败重试：总预算（秒）、次数上限、起始退避。
# 见 _replace_with_retry——总预算是硬上限，避免长期占用时把界面拖死。
_REPLACE_DEADLINE = 0.20
_REPLACE_MAX_ATTEMPTS = 12
_REPLACE_BASE_DELAY = 0.008


def _replace_with_retry(tmp: Path, path: Path) -> None:
    """os.replace 带退避重试：Windows 上它会被并发读句柄顶掉。

    Windows 的 Python open() 建句柄时不带 FILE_SHARE_DELETE，因此**任何**
    正在读该文件的句柄都会让 os.replace 以 PermissionError [WinError 5]
    失败——不只是别的进程，本进程自己的热加载读者也算：
    设置页逐字段保存 -> 每次写入触发 QFileSystemWatcher -> 应用热加载
    read_text() -> 恰好撞上下一个字段的 os.replace。压测（并发读 + 200
    次写）失败率约 24%，表现为设置保存随机报错、界面卡在旧值。

    这类占用都是毫秒级的（热加载只读几 KB、杀软扫完就放），实测第一次
    退避（8ms）内即可恢复。但重试是在**持有 _CONFIG_WRITE_LOCK 的调用
    线程**上睡的，而设置页 _save 在主线程逐字段同步调用——若只按次数
    退避，持续占用时单字段要耗 0.63s，30 个字段就是近 20s 界面冻结。
    故以总预算为硬上限：瞬时竞争几毫秒就过，真被长期占用则 0.2s 内
    放弃，由 _write_and_verify 转成"保存失败"而不是卡死界面。
    """
    deadline = time.monotonic() + _REPLACE_DEADLINE
    last: Exception | None = None
    for i in range(_REPLACE_MAX_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0 or i == _REPLACE_MAX_ATTEMPTS - 1:
                break  # 预算耗尽或是最后一次，不再白睡
            time.sleep(min(_REPLACE_BASE_DELAY * (i + 1), remaining))
    raise last  # type: ignore[misc]


def _atomic_write(path: Path, text: str) -> None:
    """原子写入：先写同目录临时文件，再 os.replace 一步替换。

    直接 write_text 是"truncate → 写入"两步操作，其他进程（如正在
    运行的主程序的热加载）恰好在该窗口读文件会拿到空/半个文件；
    若这个读者也是写者（读-改-写），残缺内容会被写回，config.yaml
    曾因此被整个清空过（密钥全丢，靠 dist 旧副本才找回）。
    os.replace 在同一卷上是原子操作，读方要么见到旧文件要么见到
    新文件，永远读不到中间态。覆盖前另存一份 .bak（单份滚动），
    万一仍出问题可手工恢复。

    失败时清理残留 tmp：留着会让下一次写入复用同一个名字，且用户
    在目录里看到 config.yaml.tmp 会以为配置坏了。
    """
    try:
        if path.exists() and path.stat().st_size > 0:
            path.with_suffix(path.suffix + ".bak").write_bytes(path.read_bytes())
    except OSError:
        pass  # 备份失败不阻塞写入
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _write_and_verify(path: Path, original: str, new_content: str,
                      key: str, expected: Any, section: str | None) -> bool:
    """写入新内容并回读校验目标键值确实生效；不符则回滚原文。

    行级正则替换在用户手改过缩进、值含 # 等特殊字符时可能写错位置：
    写入后用 yaml 重新解析整个文件，确认目标键（含节路径）的当前值
    与期望一致；不一致或解析失败均视为写入未生效——恢复原文并返回
    False，避免错误值静默落盘（设置界面表现为保存失败，可重试）。
    """
    import logging

    log = logging.getLogger(__name__)

    # 第一段：写入。失败意味着文件根本没被动过（os.replace 是原子的），
    # 此时原文件仍是 original，**不需要也不应该回滚**——旧实现在这里
    # 统一走回滚，而回滚用的是同一条 os.replace，必然以同样的
    # PermissionError 再次失败并把异常抛给调用方，把一次"保存失败"
    # 放大成设置页崩溃（热加载竞争下几乎必现）。
    try:
        _atomic_write(path, new_content)
    except Exception:
        log.warning("配置写入失败（原文件未改动）：%s%s",
                    f"{section}." if section else "", key, exc_info=True)
        return False

    # 第二段：回读校验。写进去了但值不对才需要回滚原文。
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        node = loaded
        if section:
            for part in section.split("."):
                node = node.get(part) if isinstance(node, dict) else None
        actual = node.get(key) if isinstance(node, dict) else None
        if actual == expected:
            return True
        log.warning("配置写入回读不符（%s%s：期望 %r，实得 %r），尝试回滚",
                    f"{section}." if section else "", key, expected, actual)
    except Exception:
        log.warning("配置写入后回读校验异常，尝试回滚：%s", key, exc_info=True)

    # 回滚也只是尽力：同样可能撞 WinError 5。失败只能记日志，
    # 绝不能再抛出——调用方（设置页）要的是"保存失败、可重试"。
    try:
        _atomic_write(path, original)
    except Exception:
        log.error("配置回滚失败，请检查 %s 是否被占用：%s%s",
                  path, f"{section}." if section else "", key, exc_info=True)
    return False


def update_config_field(key: str, value: str, section: str | None = None,
                        value_type: str = "str",
                        path: str | Path | None = None) -> bool:
    """更新 config.yaml 中某个字段（行级处理，保留注释和格式）。

    进程内以 _CONFIG_WRITE_LOCK 串行化，防止并发后台写线程撕裂文件；
    具体实现见 _update_config_field_locked。

    - section=None：顶层字段（只匹配无缩进行）；文件中不存在时追加到末尾
    - section 给定：限定在该顶层节内；节不存在时追加整个节，
      节内无此字段时追加到节尾
    - path：写到哪份文件。省略则写 `find_config_path()`（默认配置）。
      **调用方若是从某个指定路径读的配置（如 `--config <file>`），必须传同
      一个 path**——否则会「从 A 读、往 B 写」，B 被凭空追加字段、A 永远不生效。
    - value_type：str=单引号标量（Windows 路径含反斜杠，双引号会被 YAML 转义破坏）；
      int=裸数字；bool=裸 true/false。数值/布尔必须写裸标量，
      否则 yaml 加载后是字符串，消费端算术运算会崩
    成功返回 True；写入后回读校验不符（正则写入写错位置的兜底防线）
    会回滚原文并返回 False。
    """
    with _CONFIG_WRITE_LOCK:
        return _update_config_field_locked(key, value, section, value_type, path)


def _update_config_field_locked(key: str, value: str, section: str | None,
                                value_type: str,
                                path: str | Path | None = None) -> bool:
    import re

    path = Path(path) if path else find_config_path()
    if path is None or not path.exists():
        return False

    content = path.read_text(encoding="utf-8")
    if value_type == "bool":
        line_value = "true" if str(value).strip().lower() in ("true", "1", "yes") else "false"
        expected: Any = line_value == "true"
    elif value_type == "int":
        try:
            line_value = str(int(float(str(value).strip())))
            expected = int(line_value)
        except ValueError:
            line_value = "'" + str(value).replace("'", "''") + "'"
            expected = str(value)
    elif value_type == "float":
        try:
            f = float(str(value).strip())
            line_value = repr(f)
            expected = f
        except ValueError:
            line_value = "'" + str(value).replace("'", "''") + "'"
            expected = str(value)
    elif value_type == "json":
        # JSON 字符串是合法 YAML 双引号标量：\n 等转义在加载后还原为真实字符，
        # 用于多行文本（如 LLM 自定义 Prompt）——单引号标量会把 \n 当字面量
        import json
        line_value = json.dumps(str(value), ensure_ascii=False)
        expected = str(value)
    else:
        line_value = "'" + str(value).replace("'", "''") + "'"
        expected = str(value)

    if section is not None and "." in section:
        # 嵌套节（如 dialog.qwen）：走缩进敏感路径，避免扁平正则误伤同名兄弟键
        return _update_nested_field(path, content, key, line_value, expected,
                                    section.split("."))

    if section is None:
        pattern = rf'(^{re.escape(key)}:[ \t]*)[^#\n]*'
        new_content, count = re.subn(
            pattern, rf"\g<1>{line_value}", content, count=1, flags=re.MULTILINE
        )
        if count:
            return _write_and_verify(path, content, new_content, key, expected, None)
        new_content = content.rstrip("\n") + f"\n\n{key}: {line_value}\n"
        return _write_and_verify(path, content, new_content, key, expected, None)

    lines = content.splitlines(keepends=True)
    sec_pat = re.compile(rf"^{re.escape(section)}:[ \t]*(#.*)?$")

    sec_start = None
    for i, ln in enumerate(lines):
        if sec_pat.match(ln.rstrip("\n")):
            sec_start = i
            break
    if sec_start is None:
        new_content = content.rstrip("\n") + f"\n\n{section}:\n  {key}: {line_value}\n"
        return _write_and_verify(path, content, new_content, key, expected, section)

    # 找节边界 + 直接子键缩进（节内首个非注释非空行的缩进；合法 YAML 同层键
    # 缩进一致，嵌套子节的键缩进更深）。锚定该缩进后，扁平写 dialog.model
    # 不会误命中 dialog.qwen.model——不再依赖「扁平键排在嵌套子节之前」的
    # 隐式顺序（用户手改调序也安全）。
    sec_end = len(lines)
    direct_ind = None
    for i in range(sec_start + 1, len(lines)):
        stripped = lines[i].rstrip("\n")
        # 节边界：任何无缩进的非空行（含顶层注释）
        if stripped and not stripped[0].isspace():
            sec_end = i
            break
        st = stripped.strip()
        if not st or st.startswith("#"):
            continue
        if direct_ind is None:
            direct_ind = len(stripped) - len(stripped.lstrip(" \t"))

    key_idx = None
    if direct_ind is not None:
        # 只认直接子键缩进（合法 YAML 同层键缩进一致，direct_ind 即该缩进）。
        # 不设「任意缩进」兜底：更深缩进的首个同名键属于嵌套子节（如
        # dialog.qwen.model），命中后写入必被 _write_and_verify 回读拦下
        # 回滚，反而把「直接子键缺失时应插入」退化为静默保存失败。
        key_pat = re.compile(
            rf"^([ \t]{{{direct_ind}}}{re.escape(key)}:[ \t]*)")
        for i in range(sec_start + 1, sec_end):
            if key_pat.match(lines[i].rstrip("\n")):
                key_idx = i
                break

    if key_idx is not None:
        m = re.match(rf"^([ \t]*{re.escape(key)}:[ \t]*)",
                     lines[key_idx].rstrip("\n"))
        prefix = m.group(1)
        if not prefix.endswith((" ", "\t")):
            prefix += " "        # 空值行（`key:` 冒号后无空格）：补空格保 YAML 合法
        lines[key_idx] = f"{prefix}{line_value}\n"
        return _write_and_verify(path, content, "".join(lines), key, expected, section)

    # 节内无此字段：插到节内最后一个有效行之后（缩进对齐直接子键）
    insert_at = sec_start + 1
    for j in range(sec_start + 1, sec_end):
        if lines[j].strip() and not lines[j].lstrip().startswith("#"):
            insert_at = j + 1
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"   # 末行缺尾换行：先补齐，防插入后与上行粘连
    pad = " " * (direct_ind if direct_ind is not None else 2)
    lines.insert(insert_at, f"{pad}{key}: {line_value}\n")
    return _write_and_verify(path, content, "".join(lines), key, expected, section)


def _update_nested_field(path: Path, content: str, key: str, line_value: str,
                         expected: Any, parts: list[str]) -> bool:
    """缩进敏感地在嵌套节（如 dialog.qwen）里改/插 key（section 含点号时走此路）。

    parts 是从顶层到目标父节的路径（如 ["dialog","qwen"]）。逐层用缩进定位子节：
    每层在当前父块 [lo,hi) 内找缩进 > 父缩进的首个裸 `name:` 行（跳过注释/空行），
    其内容范围为 [该行+1, 下一个缩进 <= 该行的非空行)。定位到最内层节后，在其
    范围内找直接子键 `key:`（缩进 == 该节首个非注释子键缩进）——命中则整行换值，
    缺失则插到节内最后一个非空行之后。任一层节缺失则从缺失处按剩余路径整体追加。
    写后走 _write_and_verify 回读校验（已支持点号 section），不符则回滚。
    """
    import re

    lines = content.splitlines(keepends=True)

    def indent_of(s: str) -> int:
        return len(s) - len(s.lstrip(" \t"))

    def block_end(idx: int, ind: int) -> int:
        for i in range(idx + 1, len(lines)):
            s = lines[i].rstrip("\n")
            if not s.strip():
                continue
            if indent_of(s) <= ind:
                return i
        return len(lines)

    def find_child(lo: int, hi: int, parent_ind: int, name: str):
        """在 [lo,hi) 内找缩进 > parent_ind 的裸 `name:` 节头，返回 (idx, ind)。"""
        child_ind = None
        for i in range(lo, hi):
            s = lines[i].rstrip("\n")
            st = s.strip()
            if not st:
                continue
            ind = indent_of(s)
            if ind <= parent_ind:
                break
            if st.startswith("#"):
                continue
            if child_ind is None:
                child_ind = ind
            if ind == child_ind and re.match(
                    rf"^{re.escape(name)}:[ \t]*(#.*)?$", st):
                return i, ind
        return None, None

    sec_path = ".".join(parts)
    lo, hi, parent_ind = 0, len(lines), -1
    for depth, part in enumerate(parts):
        idx, ind = find_child(lo, hi, parent_ind, part)
        if idx is None:
            # 节缺失（罕见：用户删了子节）：在当前块末尾按剩余路径 + key 追加
            insert_at = lo
            for j in range(lo, hi):
                if lines[j].strip():
                    insert_at = j + 1
            pad = parent_ind + 2 if parent_ind >= 0 else 0
            chunk = []
            for name in parts[depth:]:
                chunk.append(" " * pad + f"{name}:\n")
                pad += 2
            chunk.append(" " * pad + f"{key}: {line_value}\n")
            if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
                lines[insert_at - 1] += "\n"   # 末行缺尾换行：先补齐防粘连
            lines[insert_at:insert_at] = chunk
            return _write_and_verify(path, content, "".join(lines), key,
                                     expected, sec_path)
        lo, hi, parent_ind = idx + 1, block_end(idx, ind), ind

    # 最内层节范围 [lo, hi)：找直接子键 key（跳过注释/空行，锁首个子键缩进）
    key_ind, key_idx = None, None
    for i in range(lo, hi):
        s = lines[i].rstrip("\n")
        st = s.strip()
        if not st:
            continue
        ind = indent_of(s)
        if ind <= parent_ind or st.startswith("#"):
            continue
        if key_ind is None:
            key_ind = ind
        if ind == key_ind and re.match(rf"^{re.escape(key)}:([ \t]|$)", st):
            key_idx = i
            break

    if key_idx is not None:
        m = re.match(rf"^([ \t]*{re.escape(key)}:[ \t]*)",
                     lines[key_idx].rstrip("\n"))
        prefix = m.group(1)
        if not prefix.endswith((" ", "\t")):
            prefix += " "        # 空值行（`key:` 冒号后无空格）：补空格保 YAML 合法
        lines[key_idx] = f"{prefix}{line_value}\n"
        return _write_and_verify(path, content, "".join(lines), key, expected, sec_path)

    # 节内无此键：插到节内最后一个非空行之后
    insert_at = lo
    for j in range(lo, hi):
        if lines[j].strip():
            insert_at = j + 1
    if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"   # 末行缺尾换行：先补齐，防插入后与上行粘连
    pad = " " * (key_ind if key_ind is not None else parent_ind + 2)
    lines.insert(insert_at, f"{pad}{key}: {line_value}\n")
    return _write_and_verify(path, content, "".join(lines), key, expected, sec_path)


def mask_secret(value: str, visible: int = 4) -> str:
    """密钥脱敏，仅保留前 visible 位，其余用 * 替换。"""
    if not value:
        return "<空>"
    if len(value) <= visible:
        return "*" * len(value)
    return value[:visible] + "*" * (len(value) - visible)


def describe_config(cfg: SimpleNamespace) -> str:
    """返回脱敏的配置摘要，用于启动日志。"""
    engine = getattr(cfg, "engine", "tencent")
    parts = [f"engine_type={engine}"]
    if engine == "funasr":
        parts.append(f"model={cfg.funasr.model}")
        parts.append(f"device={cfg.funasr.device}")
    elif engine == "xfyun":
        x = getattr(cfg, "xfyun", None)
        edition = getattr(x, "edition", "llm")
        parts.append(f"edition={edition}")
        if edition == "std":
            parts.append(f"lang={getattr(x, 'std_lang', 'cn')}")
        else:
            parts.append(f"lang={getattr(x, 'lang', 'autodialect')}")
        parts.append(f"app_id={mask_secret(getattr(x, 'app_id', ''))}")
        parts.append(f"api_key={mask_secret(getattr(x, 'api_key', ''))}")
    elif engine == "volcengine":
        v = getattr(cfg, "volcengine", None)
        parts.append(f"resource_id={getattr(v, 'resource_id', '')}")
        parts.append(f"api_key={mask_secret(getattr(v, 'api_key', ''))}")
        parts.append(f"access_token={mask_secret(getattr(v, 'access_token', ''))}")
    else:
        parts.append(f"model={cfg.tencent.engine_model_type}")
        parts.append(f"app_id={mask_secret(cfg.tencent.app_id)}")
        parts.append(f"secret_id={mask_secret(cfg.tencent.secret_id)}")
    parts.append(f"hotkey={cfg.hotkey.toggle}")
    parts.append(f"sample_rate={cfg.audio.sample_rate}")
    return ", ".join(parts)
