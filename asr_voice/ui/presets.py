"""引擎与主题预设。

i18n（A6）：本模块的引擎档位名与模型标签是**导入期常量**（模块级列表），
不能在定义处调 t() —— import 时界面语言还没定（见 i18n 模块 docstring 的
「导入期求值禁令」）。故表里只存 **i18n 键**，由取值函数在调用点 t()：

- 档位分类名：`engine.cat.<provider>.<slot>`（slot 在 provider 间会重名，
  如 tencent 的 "general" 与 xfyun 的 "general" 文案不同，键必须带 provider）
- 模型标签：`engine.model.<归一化 engine_id>`，见 `_model_key()`
- 主题显示名：`theme.<键>`（由 `theme_registry.theme_display_name` 解析，
  本模块 THEMES 的 "name" 仅是主题包自带原文的兜底）

「每个模型都有词条」由 tests/test_engine_preset_i18n.py 守着：新增引擎
模型却忘记补词条，测试立刻红（而不是等到切英文时菜单裸奔出 key）。
"""
from ..config.loader import funasr_available
from ..i18n import t

# 腾讯云引擎三档分类：右键菜单只显示三个分类名，点击分类名即切换到该档
# 配置的模型；每档实际调用哪个模型在设置页「引擎参数」的下拉菜单里配置
# （写入 tencent.menu_model_<slot>，候选即下表各档模型列表）。
TENCENT_CATEGORY_SLOTS = ("large_2_0", "large_1_0", "general")

# 表元素：(slot, 分类名 i18n 键, [(engine_id, 标签 i18n 键), ...])
TENCENT_CATEGORIES = [
    ("large_2_0", "engine.cat.tencent.large_2_0", [
        ("16k_zh_en_2.0",         "engine.model.16k_zh_en_2_0"),
        ("16k_zh_en_speaker_2.0", "engine.model.16k_zh_en_speaker_2_0"),
        ("Hy-ASR-3.0-preview",    "engine.model.hy_asr_3_0_preview"),
    ]),
    ("large_1_0", "engine.cat.tencent.large_1_0", [
        ("16k_zh_en",      "engine.model.16k_zh_en"),
        ("16k_multi_lang", "engine.model.16k_multi_lang"),
        ("16k_en_large",   "engine.model.16k_en_large"),
    ]),
    ("general", "engine.cat.tencent.general", [
        ("16k_zh",         "engine.model.16k_zh"),
        ("16k_zh-TW",      "engine.model.16k_zh_tw"),
        ("16k_zh_edu",     "engine.model.16k_zh_edu"),
        ("16k_zh_medical", "engine.model.16k_zh_medical"),
        ("16k_yue",        "engine.model.16k_yue"),
        ("16k_zh_dialect", "engine.model.16k_zh_dialect"),
    ]),
]

# 阿里云百炼两档分类：官方无 1.0/2.0 版本线，按「大模型版/通用引擎」分。
# 仅收录实时语音识别流式输出模型（8k 电话场景等非流式用途不列出）。
# 每档单一模型（qwen3-asr-flash-realtime / fun-asr-realtime 即将下线，已移除），
# 均走 run-task 协议。
ALIYUN_CATEGORIES = [
    ("llm", "engine.cat.aliyun.llm", [
        ("aliyun:qwen-audio-3.0-asr-flash-streaming",
         "engine.model.aliyun_qwen_audio_3_0_asr_flash_streaming"),
    ]),
    ("general", "engine.cat.aliyun.general", [
        ("aliyun:paraformer-realtime-v2",
         "engine.model.aliyun_paraformer_realtime_v2"),
    ]),
]

# 讯飞两档分类：大模型版仅 autodialect（37 语种 autominor 需工单开通，不接入）；
# 标准版语言由 xfyun.std_lang 决定（cn/en），engine_id 形如 xfyun:std:cn。
XFYUN_CATEGORIES = [
    ("llm", "engine.cat.xfyun.llm", [
        ("xfyun:autodialect", "engine.model.xfyun_autodialect"),
    ]),
    ("general", "engine.cat.xfyun.general", [
        ("xfyun:std:cn", "engine.model.xfyun_std_cn"),
        ("xfyun:std:en", "engine.model.xfyun_std_en"),
    ]),
]

# 火山两档分类：官方即分 1.0/2.0（bigasr/seedasr），每档仅一个模型，
# 计费方式由设置页 volcengine.<family>_billing 单独控制。
VOLCENGINE_CATEGORIES = [
    ("large_2_0", "engine.cat.volcengine.large_2_0", [
        ("volcengine:seedasr", "engine.model.volcengine_seedasr"),
    ]),
    ("large_1_0", "engine.cat.volcengine.large_1_0", [
        ("volcengine:bigasr", "engine.model.volcengine_bigasr"),
    ]),
]

# 云引擎分类注册表：右键菜单对注册的组只显示分类名（点击即切到该档
# 配置的模型）；funasr 单项引擎不注册，保持平铺。
ENGINE_CATEGORIES = {
    "tencent": TENCENT_CATEGORIES,
    "aliyun": ALIYUN_CATEGORIES,
    "xfyun": XFYUN_CATEGORIES,
    "volcengine": VOLCENGINE_CATEGORIES,
}

# 模型标签的键名由 engine_id 归一化派生（非字母数字一律换下划线）。
# 派生而非手工起名：新增模型时键名不可能写歪，且 i18n 对称性测试能直接
# 用同一函数反查「哪些 engine_id 还没词条」。
_KEEP = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def _model_key(engine_id: str) -> str:
    """engine_id -> `engine.model.<归一化>` i18n 键。

    `16k_zh_en_2.0` -> `engine.model.16k_zh_en_2_0`；
    `aliyun:qwen-audio-3.0-asr-flash-streaming`
        -> `engine.model.aliyun_qwen_audio_3_0_asr_flash_streaming`

    **测试依赖，勿删**：生产路径读表里的键字面量，本函数只被
    `tests/test_engine_preset_i18n.py` 用来反查「表里的键确实按规则派生」。
    """
    norm = "".join(ch if ch in _KEEP else "_" for ch in engine_id.lower())
    return f"engine.model.{norm}"


def _cat_key(provider: str, slot: str) -> str:
    """档位分类名的 i18n 键。

    **测试依赖，勿删**：生产路径（`provider_menu_items`）读表里的键字面量，
    本函数只被 `tests/test_engine_preset_i18n.py` 用来反查键名规则。
    键必须带 provider：slot 在 provider 之间会重名（如 general/llm），
    只写 `engine.cat.<slot>` 会撞键。
    """
    return f"engine.cat.{provider}.{slot}"


def provider_menu_items(group: str, cfg) -> list[tuple[str, str, str]]:
    """某云引擎组右键菜单分类项：[(slot, 分类名, engine_id), ...]。

    档位值读取：
    - tencent：menu_model_<slot> 配置键（设置页「调用」下拉可改）；
    - aliyun：每档单一模型，无配置键，回落该档首项；
    - xfyun：general 档复用 std_lang（cn/en -> "xfyun:std:cn"/"xfyun:std:en"），
      llm 档固定 autodialect；
    - volcengine：每档单模型，无需配置。
    缺失/非法一律回落该档首项，保证配置写坏时菜单仍可用。
    """
    cats = ENGINE_CATEGORIES.get(group)
    if not cats:
        return []
    gcfg = getattr(cfg, group, None)
    result = []
    for slot, title_key, models in cats:
        val = ""
        if group in ("tencent", "aliyun"):
            val = str(getattr(gcfg, f"menu_model_{slot}", "") or "").strip()
        elif group == "xfyun" and slot == "general":
            lang = str(getattr(gcfg, "std_lang", "") or "cn").strip()
            val = f"xfyun:std:{lang}" if lang in ("cn", "en") else "xfyun:std:cn"
        valid = {model_id for model_id, _ in models}
        if val not in valid:
            val = models[0][0]
        # 分类名后带当前绑定模型：腾讯用英文模型 ID（够短）；阿里完整 ID
        # 太长，用中文短名（engine_display_name，完整 ID 对照见 User_Doc）；
        # 讯飞/火山档与模型一一对应，不带
        title = t(title_key)
        if group == "tencent":
            title = f"{title} · {val}"
        elif group == "aliyun":
            title = f"{title} · {engine_display_name(val)}"
        result.append((slot, title, val))
    return result


def get_engines(order: str | None = None):
    """引擎菜单项列表：按 provider 分组，order 里排前面的组在前，没列出的追加到末尾。

    组内枚举必须和设置界面可选项一一对应，否则配置里的 engine_id 在菜单里没有勾选项。
    """
    # 组内项与设置界面可选项一一对应，否则选中后菜单无勾选项。
    # 云引擎组为各分类下全部叶子模型的平铺（右键菜单按分类名渲染，
    # 此处供设置页「引擎排序」显隐过滤与显示名解析使用）
    by_group = {
        group: [(model_id, t(label_key))
                for _, _, models in cats
                for model_id, label_key in models]
        for group, cats in ENGINE_CATEGORIES.items()
    }
    # 「（需安装）」是状态后缀而非模型名的一部分，故单独拼（未装依赖时提示用户去装）。
    # 标签本身带「· 中文」（spec §8「仅中文」三处标注之一：引擎菜单）。
    funasr_label = t("engine.funasr.label")
    if not funasr_available():
        funasr_label += t("engine.funasr.needs_install")
    by_group["funasr"] = [("funasr:paraformer-zh-streaming", funasr_label)]

    # 默认顺序；order 非空时按其重排，缺失项追加
    default_order = ["tencent", "aliyun", "xfyun", "volcengine", "funasr"]
    if order:
        seen = set()
        custom = [p.strip() for p in str(order).split(",") if p.strip()]
        ordered = [p for p in custom if p in by_group and p not in seen and not seen.add(p)]
        ordered += [p for p in default_order if p not in seen]
    else:
        ordered = default_order
    engines = []
    for g in ordered:
        engines.extend(by_group.get(g, []))
    return engines


def engine_group_of(engine_id: str) -> str:
    """引擎 ID -> 提供商分组（tencent/aliyun/xfyun/volcengine/funasr）。

    腾讯云历史 ID 无前缀（如 16k_zh、Hy-ASR-3.0-preview），默认归入 tencent。
    """
    for prefix in ("funasr", "aliyun", "xfyun", "volcengine"):
        if engine_id.startswith(prefix):
            return prefix
    return "tencent"


def engines_by_group(order: str | None = None) -> dict[str, list[tuple[str, str]]]:
    """按提供商分组的引擎清单：组间顺序与 get_engines 一致（跟随 order）。

    供设置页「引擎排序」折叠分组与右键菜单过滤使用，保证三处看到的
    分组归属完全相同。
    """
    grouped: dict[str, list[tuple[str, str]]] = {}
    for eid, label in get_engines(order):
        grouped.setdefault(engine_group_of(eid), []).append((eid, label))
    return grouped


# 窄场景（悬浮条提示）用的更短显示名：条形主题文本区仅 ~211px，
# 菜单标签「已切换：Paraformer 流式 · 中文」实测超宽，会被 elide_right 截掉尾部，
# 故另配去掉「· 中文」的短名（199px 刚好放下）。只影响提示文案，右键菜单与设置页
# 清单仍用 get_engines 的完整 label（funasr 不属 ENGINE_CATEGORIES，
# 故 provider_menu_items 的分类标题也不受影响）。
_HINT_SHORT_NAME_KEYS = {
    "funasr:paraformer-zh-streaming": "engine.funasr.short",
}


def engine_display_name(engine_id: str) -> str:
    """引擎 id → 短显示名：先查 _HINT_SHORT_NAME_KEYS，否则取菜单标签「 · 」
    前的名词部分，未匹配返回原 id。

    供切换引擎提示等窄场景统一措辞（如「中文通用 · 中英」→「中文通用」）。
    """
    if engine_id in _HINT_SHORT_NAME_KEYS:
        return t(_HINT_SHORT_NAME_KEYS[engine_id])
    for eid, label in get_engines():
        if eid == engine_id:
            return label.split(" · ")[0].replace(
                t("engine.funasr.needs_install"), "").strip()
    return engine_id


# 内置主题预设：light/dark/minimal（任何构建都包含）。
# 键为内部键（语言无关，写进 config 的 ui.theme_name），显示名放 "name"。
# 主题键参与持久化与 i18n，不能跟界面语言绑死（历史上是中文名，见
# loader.LEGACY_THEME_NAMES 的一次性迁移）。
# 扩展主题（如赛博朋克风/凯尔特风格）走外部主题包：themes/ 目录 +
# theme_registry 加载，见 docs/superpowers/specs/2026-08-28-theme-pack-dlc-design.md。
# bg、text、accent 必填；border、top_line 可选，缺省由 _is_light 按明暗派生
#
# i18n（A6）：内置主题的 "name" 存的是 **i18n 键**而非显示名。它存在的意义
# 只是与外部主题包的 theme.yaml 字段同构（`theme_registry._load_pack` 会把
# 包里的 name 原样放进同一位置当兜底），**内置主题的取值永远走不到它**：
# `theme_display_name` 先查 `theme.<键>` 词条，6 个内置键都有词条，兜底分支
# 不会被触达。写中文会让守卫判它硬编；写键则即便某天真被读到，用户看到的
# 也只是一个键名——比露出中文更符合"英文界面不该有中文"的底线。
THEMES = {
    # 浅色：近白底、深灰字、蓝 accent；描边 15% 黑软边（#AARRGGBB），
    # 与右键菜单表面同灰阶（context_menu 浅色分支同一 #f7f7f8）
    "light": {
        "name": "theme.light",
        "bg": "#f7f7f8",
        "text": "#1d1d1f",
        "accent": "#007aff",
        "border": "#26000000",
    },
    "dark": {
        "name": "theme.dark",
        "bg": "#1c1c20",
        "text": "#eeeeee",
        "accent": "#5696e8",
    },
    # 极简：正方形悬浮窗 + 中央声纹波形，整窗即启停按钮；
    # 文本在正上方气泡显示（气泡逻辑见 bar_widgets.SpeechBubble），右上角 ✕ 最小化
    "minimal": {
        "name": "theme.minimal",
        "bg": "#1c1c20",
        "text": "#eeeeee",
        "accent": "#5696e8",      # 录音中声纹波形色
        "voice_print": True,      # 标记：正方形悬浮窗 + 声纹图标 + 气泡文本
    },
}
