"""发行渠道集成：发行层判断 + 平台 SDK 能力封装（两层互不依赖）。

发行层：launcher 透传 --steam → is_steam_build()。GitHub/PyInstaller 版无此参数。
能力层：steamworks import 失败或 SDK 初始化失败 → 静默降级
（仅失去云存档/统计，其余功能照常）。所有公开方法先判可用性，
不可用一律安全空操作（返回 False/None/0，不抛异常）。

AppID：开发期靠工作目录 steam_appid.txt（steamworks 自动读取）；
正式环境由 平台客户端注入。本模块不硬编码 AppID。
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

def _load_dlc_app_id() -> int:
    """取免费显卡加速 DLC 的 AppID；没有构建期产物就回落 0。

    真 ID 不入库（构建期生成、只落 depot，源码树无此文件）；回落 0 是唯一安全缺省：
    DLC 判断整体短路（不显示「安装 DLC」入口），而不是误报「未安装」。
    """
    try:
        from ._steam_ids import STEAM_DLC_GPU_APP_ID as value
    except ImportError:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):     # pragma: no cover - 生成物被手改成垃圾
        return 0


STEAM_DLC_GPU_APP_ID = _load_dlc_app_id()


def _load_main_app_id() -> int:
    """取本应用的主 AppID（拼工坊页链接 + 文件系统兜底发现用）；无构建期产物回落 0。

    真 ID 不入库（同 DLC ID 纪律）；回落 0 时文件系统发现整体短路——宁可什么都不
    发现，也不拿错 ID 去扫别人的目录。
    """
    try:
        from ._steam_ids import STEAM_APP_ID as value
    except ImportError:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):     # pragma: no cover - 生成物被手改成垃圾
        return 0


STEAM_APP_ID = _load_main_app_id()


def is_steam_build() -> bool:
    """发行层判断（全项目收敛点）：进程命令行含 --steam 即发行构建。"""
    return "--steam" in sys.argv[1:]


def external_services_allowed() -> bool:
    """外部 AI 服务能力是否可用。

    源码构建（无 --steam）恒为 True；发行构建下由本机已安装的工坊功能包提供
    （检测在 workshop 侧）。
    """
    if not is_steam_build():
        return True
    return _workshop_feature_pack_present()


def _workshop_feature_pack_present() -> bool:
    """本机是否装了提供该能力的工坊功能包（任何异常一律按未安装处理）。

    懒加载 workshop：它在顶层反向 import 本模块，模块级反向 import 会成环。
    """
    try:
        from .workshop import external_services_pack_installed
        return bool(external_services_pack_installed())
    except Exception:
        logger.debug("工坊功能包检测失败，按未安装处理", exc_info=True)
        return False


def _import_steamworks():
    """延迟导入 steamworks（测试打补丁点）。

    来源是随发行 depot 投放的自研 ctypes 薄封装（不是 PyPI 包），仅发行构建存在；
    源码构建走到这里必然 ImportError，属设计内降级路径。
    """
    import steamworks
    return steamworks


def _optional_accessor(instance, name: str):
    """取可选访问器；不存在/抛异常一律返回 None（该功能静默关闭）。"""
    try:
        accessor = getattr(instance, name)
        return accessor() if callable(accessor) else None
    except Exception:
        logger.debug("Steamworks 访问器 %s 不可用", name, exc_info=True)
        return None


class SteamIntegration:
    """进程级单例：init 一次，其余方法判可用性后空操作。"""

    def __init__(self) -> None:
        self._instance = None   # STEAMWORKS 实例（init 成功才有）
        self._cloud = None      # steamworks.Cloud
        self._stats = None      # steamworks.UserStats
        self._apps = None       # steamworks.Apps
        self._workshop = None   # steamworks.Workshop
        self._waiter = None     # steamworks._CallWaiter
        # 已安装物品目录的缓存（内容层每个入口都要用，不能每次问 Steam）
        self._workshop_dirs_cache: tuple | None = None

    @property
    def available(self) -> bool:
        return self._instance is not None

    def init(self) -> bool:
        if self._instance is not None:
            return True
        try:
            sw = _import_steamworks()
            instance = sw.STEAMWORKS()
            if not instance.initialize():
                logger.info("SteamAPI_Init 失败（非 Steam 环境），静默降级")
                return False
            self._instance = instance
            self._cloud = instance.Cloud()
            self._stats = instance.UserStats()
            self._apps = instance.Apps()
            # 创意工坊访问器必须可选：直接 instance.Workshop() 若遇旧版 depot
            # （无 UGC）抛异常，会顺着 init 的 except 冒出去，把整个能力层判成
            # 不可用，连带云存档/统计/DLC 一起消失。
            self._workshop = _optional_accessor(instance, "Workshop")
            self._waiter = _optional_accessor(instance, "CallWaiter")
            if self._workshop is None:
                logger.info("Steamworks 未提供创意工坊接口（工坊功能关闭，"
                            "云存档/统计不受影响）")
            logger.info("Steamworks 已初始化")
            return True
        except Exception:
            logger.info("Steamworks 初始化失败，静默降级（无云存档/统计）", exc_info=True)
            self._instance = None
            return False

    def run_callbacks(self) -> None:
        if self._instance is not None:
            try:
                self._instance.run_callbacks()
            except Exception:
                pass

    def shutdown(self) -> None:
        """显式收尾 Steamworks（幂等；未初始化时 no-op）。

        全项目仅此一处 SteamAPI_Shutdown：否则退出时要在 loader lock 下跑 DLL_PROCESS_DETACH，
        卡住即进程退不掉。须排在云存档落盘之后；收尾后清空全部子接口引用（防旧句柄）。
        """
        instance, self._instance = self._instance, None
        self._cloud = None
        self._stats = None
        self._apps = None
        self._workshop = None
        self._waiter = None
        self._workshop_dirs_cache = None
        if instance is None:
            return
        try:
            instance.shutdown()
        except Exception:
            # 收尾失败不能把退出路径一起拖垮：咽掉即可。DLL 侧仍会在
            # ExitProcess 时自己收尾（那正是本次要绕开的场景）
            # 真卡住的话看门狗会兜住。
            logger.debug("Steamworks 收尾异常", exc_info=True)

    def cloud_write(self, filename: str, data: bytes) -> bool:
        if self._cloud is None:
            return False
        try:
            return bool(self._cloud.FileWrite(filename, data))
        except Exception:
            logger.debug("云存档写入失败：%s", filename, exc_info=True)
            return False

    def cloud_read(self, filename: str) -> bytes | None:
        if self._cloud is None:
            return None
        try:
            data = self._cloud.FileRead(filename)
            return bytes(data) if data else None
        except Exception:
            logger.debug("云存档读取失败：%s", filename, exc_info=True)
            return None

    def cloud_delete(self, filename: str) -> bool:
        """确保云上**没有**这个文件。

        语义是"目标状态"不是"执行了删除"：本来就没有 → True；能力不可用 → False。**安全相关**：
        关掉「云存档同步密钥」时必须调它删含密钥那份，返回 False 时调用方要记 warn。
        """
        if self._cloud is None:
            return False
        try:
            return bool(self._cloud.FileDelete(filename))
        except Exception:
            logger.debug("云存档删除失败：%s", filename, exc_info=True)
            return False

    def cloud_file_count(self) -> int:
        if self._cloud is None:
            return 0
        try:
            return int(self._cloud.GetFileCount())
        except Exception:
            return 0

    def store_stats(self, recorded_seconds: int, recognized_chars: int, sessions: int) -> bool:
        if self._stats is None:
            return False
        try:
            self._stats.SetStatInt("recorded_seconds", int(recorded_seconds))
            self._stats.SetStatInt("recognized_chars", int(recognized_chars))
            self._stats.SetStatInt("sessions", int(sessions))
            return bool(self._stats.StoreStats())
        except Exception:
            logger.debug("统计写入失败", exc_info=True)
            return False

    def dlc_installed(self, dlc_app_id: int) -> bool:
        if self._apps is None or not dlc_app_id:
            return False
        try:
            return bool(self._apps.BIsDlcInstalled(int(dlc_app_id)))
        except Exception:
            return False

    # ---- 创意工坊 ----
    # 全部遵循能力层契约：不可用时返回空列表 / False / 0，不抛异常。
    # 上层（设置页、内容层）因此不需要到处判可用性——拿不到就是空的。

    def workshop_available(self) -> bool:
        """创意工坊是否真的可用（SDK 已初始化 + UGC 符号齐全）。"""
        if self._workshop is None:
            return False
        try:
            available = getattr(self._workshop, "available", None)
            return bool(available()) if callable(available) else False
        except Exception:
            return False

    def app_id(self) -> int:
        """当前 AppID；拿不到返回 0。

        由平台客户端注入，源码树没有这个值（真 ID 不入库），商店页链接只能运行时拼。
        """
        if self._waiter is None:
            return 0
        try:
            return int(self._waiter.app_id())
        except Exception:
            return 0

    def workshop_items(self) -> list[WorkshopItem]:
        """列出本应用下全部已订阅物品的当前状态。"""
        if not self.workshop_available():
            return []
        try:
            ids = list(self._workshop.subscribed_ids())
        except Exception:
            logger.debug("取订阅物品列表失败", exc_info=True)
            return []
        out: list[WorkshopItem] = []
        for item_id in ids:
            try:
                state = int(self._workshop.item_state(item_id))
                install_dir = (""
                               if not state & _ITEM_STATE_INSTALLED else
                               self._workshop.item_install_dir(item_id))
                done, total = self._workshop.item_download_progress(item_id)
            except Exception:
                logger.debug("读取工坊物品 %s 状态失败", item_id, exc_info=True)
                continue
            out.append(WorkshopItem(item_id, state, downloaded=done,
                                    total=total, install_dir=install_dir))
        return out

    def workshop_installed_dirs(self) -> tuple:
        """已下载到本地、内容可用的物品目录。

        **每次访问都重算**，不做长期缓存：Steam 可能在后台完成下载或按订阅变化
        删目录，过期缓存会让主题/模型"装上了却不出现"。
        """
        return tuple(item.install_dir for item in self.workshop_items()
                     if item.installed and item.install_dir)

    def workshop_download(self, item_id: int) -> bool:
        """把某个已订阅物品的下载提到最高优先级。"""
        if not self.workshop_available() or not item_id:
            return False
        try:
            return bool(self._workshop.download_item(int(item_id), True))
        except Exception:
            logger.debug("请求下载工坊物品 %s 失败", item_id, exc_info=True)
            return False

    def workshop_subscribe(self, item_id: int, timeout_s: float = 30.0) -> bool:
        """订阅物品并等结果。阻塞调用（最多 timeout_s），须在后台线程跑。"""
        return self._workshop_call("subscribe_item", item_id, timeout_s)

    def workshop_unsubscribe(self, item_id: int, timeout_s: float = 30.0) -> bool:
        """退订物品并等结果。阻塞调用，须在后台线程跑。"""
        return self._workshop_call("unsubscribe_item", item_id, timeout_s)

    def _workshop_call(self, method_name: str, item_id: int,
                       timeout_s: float) -> bool:
        """发一个"订阅/退订"这类异步调用并等结果。

        统一走这里：取绑定方法、判可用、等结果任一处漏判都会在无 Steam 环境抛
        AttributeError，而那条降级路径必须安静地什么都不做。
        """
        if not self.workshop_available() or not item_id:
            return False
        try:
            method = getattr(self._workshop, method_name)
            call = int(method(int(item_id)))
            if not call or self._waiter is None:
                return False
            # callback_id 传 0：订阅/退订没有可解的结果结构，只要"成没成"
            # （由 pbFailed 反推）。传错回调编号比不传更糟——会把结果解成垃圾。
            code, _pid = self._waiter.wait(call, timeout_s=timeout_s)
            return code == _RESULT_OK
        except Exception:
            logger.debug("工坊调用 %s(%s) 失败", method_name, item_id,
                         exc_info=True)
            return False

    def workshop_publish(self, folder: str, title: str, description: str = "",
                         tags=(), change_note: str = "",
                         progress=None, timeout_s: float = 900.0) -> int:
        """把本地目录发布成一个工坊物品，返回新物品 ID（失败返回 0）。

        **阻塞直到上传完成**（内容大时可达十几分钟），必须放后台线程；`progress` 收到
        (已处理字节, 总字节)。流程 CreateItem → 等 → StartItemUpdate → 设字段 → 提交 → 等。
        """
        if not self.workshop_available():
            return 0
        try:
            from pathlib import Path

            src = Path(folder)
            if not src.is_dir():
                logger.warning("工坊发布失败：目录不存在 %s", folder)
                return 0
            app_id = int(self._waiter.app_id()) if self._waiter is not None else 0
            if not app_id:
                logger.warning("工坊发布失败：取不到 AppID（平台未注入）")
                return 0

            code, item_id = self._waiter.wait_item_created(
                int(self._workshop.create_item(app_id)), timeout_s=60.0)
            if code != _RESULT_OK or not item_id:
                logger.warning("创建工坊物品失败（result=%s）", code)
                return 0

            handle = int(self._workshop.start_item_update(app_id, item_id))
            if not handle:
                logger.warning("开启工坊物品更新失败：%s", item_id)
                return 0
            # 两个语言槽都要写（英语 + 简体中文）：Steam 不跨语言回退，只填
            # schinese 会让英语用户在物品页看到空标题。同一 handle 写两遍。
            self._workshop.set_item_language(handle, _WS_LANG_ENGLISH)
            if not self._workshop.set_item_title(handle, title):
                logger.warning("设置工坊物品标题失败：%s", item_id)
                return 0
            if description:
                self._workshop.set_item_description(handle, description)
            self._workshop.set_item_language(handle, _WS_LANG_SCHINESE)
            if not self._workshop.set_item_title(handle, title):
                logger.warning("设置工坊物品中文标题失败：%s", item_id)
                return 0
            if description:
                self._workshop.set_item_description(handle, description)
            self._workshop.set_item_content(handle, str(src.resolve()))
            if tags:
                self._workshop.set_item_tags(handle, list(tags))
            # 可见性保持 Public（便于社区浏览）；若需先建后发
            # （开发者自审期），这里改成 _PUBLISHED_VISIBILITY_UNLISTED 即可
            self._workshop.set_item_visibility(handle, _PUBLISHED_VISIBILITY_PUBLIC)

            call = int(self._workshop.submit_item_update(handle, change_note))
            if not call:
                logger.warning("提交工坊物品更新失败：%s", item_id)
                return 0

            def _progress(_elapsed: float) -> None:
                if progress is None:
                    return
                try:
                    progress(*self._workshop.update_progress(handle))
                except Exception:
                    logger.debug("读上传进度失败", exc_info=True)

            code, _pid = self._waiter.wait_item_submitted(
                call, timeout_s, progress=_progress)
            if code != _RESULT_OK:
                logger.warning("工坊上传失败（result=%s，物品 %s）", code, item_id)
                return 0
            logger.info("工坊物品已发布：%s（%s）", title, item_id)
            return item_id
        except Exception:
            logger.debug("工坊发布异常", exc_info=True)
            return 0

    def workshop_update(self, item_id: int, title: str | None = None,
                        description: str | None = None, tags=(),
                        preview: str = "", content: str = "",
                        change_note: str = "", progress=None,
                        timeout_s: float = 900.0) -> bool:
        """更新一个**已存在**的工坊物品（成功返回 True）。

        **没传的字段一律不设**（Steam UGC 只改显式设置的项，未设置保持线上原值）；preview 非图片
        时 Steam 返回 true 但静默不出封面，故先校验存在；**阻塞**须后台线程；改标签/补封面只能走这条。
        """
        if not self.workshop_available():
            return False
        try:
            from pathlib import Path

            if not item_id:
                return False
            app_id = int(self._waiter.app_id()) if self._waiter is not None else 0
            if not app_id:
                logger.warning("工坊更新失败：取不到 AppID（平台未注入）")
                return False
            picture = None
            if preview:
                picture = Path(preview)
                if not picture.is_file():
                    logger.warning("工坊更新失败：预览图不是文件 %s", preview)
                    return False

            handle = int(self._workshop.start_item_update(app_id, item_id))
            if not handle:
                logger.warning("开启工坊物品更新失败：%s", item_id)
                return False
            if title or description:
                for lang in (_WS_LANG_ENGLISH, _WS_LANG_SCHINESE):
                    self._workshop.set_item_language(handle, lang)
                    if title and not self._workshop.set_item_title(handle, title):
                        logger.warning("设置工坊物品标题失败：%s", item_id)
                        return False
                    if description:
                        self._workshop.set_item_description(handle, description)
            if content:
                self._workshop.set_item_content(
                    handle, str(Path(content).resolve()))
            if tags:
                self._workshop.set_item_tags(handle, list(tags))
            if picture is not None:
                if not self._workshop.set_item_preview(
                        handle, str(picture.resolve())):
                    logger.warning("设置工坊物品封面失败：%s", item_id)
                    return False

            call = int(self._workshop.submit_item_update(handle, change_note))
            if not call:
                logger.warning("提交工坊物品更新失败：%s", item_id)
                return False

            def _progress(_elapsed: float) -> None:
                if progress is None:
                    return
                try:
                    progress(*self._workshop.update_progress(handle))
                except Exception:
                    logger.debug("读上传进度失败", exc_info=True)

            code, _pid = self._waiter.wait_item_submitted(
                call, timeout_s, progress=_progress)
            if code != _RESULT_OK:
                logger.warning("工坊更新上传失败（result=%s，物品 %s）",
                               code, item_id)
                return False
            logger.info("工坊物品已更新：%s", item_id)
            return True
        except Exception:
            logger.warning("工坊更新异常：%s", item_id, exc_info=True)
            return False


# EWorkshopFileType / ERemoteStoragePublishedFileVisibility / k_EResult 的
# 取值副本（同样不 import 那个只存在于 depot 的封装，理由见上面的位标志）
_RESULT_OK = 1
_PUBLISHED_VISIBILITY_PUBLIC = 0
# 物品标题/描述要写两个语言槽（Steam 不跨语言回退，只填中文槽会让英语
# 用户看到空标题）。取值与 steam/steamworks 里的常量一致 —— 这里复制而不
# import，理由同上面的位标志：那个模块只存在于 depot。
_WS_LANG_ENGLISH = "english"
_WS_LANG_SCHINESE = "schinese"


_integration: SteamIntegration | None = None


def get_integration() -> SteamIntegration:
    """进程级单例。"""
    global _integration
    if _integration is None:
        _integration = SteamIntegration()
    return _integration


def steam_available() -> bool:
    """能力层：steamworks 可导入且 SDK 初始化成功（懒初始化单例）。"""
    return get_integration().init()


# ---- 云存档纯逻辑（无 steamworks 依赖，可离线单测）----
# 这一段刻意不碰任何 平台 API：云存档最容易出事的地方是「密钥有没有被同步
# 上去」，那是一条纯数据规则，必须能在没有 平台客户端的机器上验证。
SECRET_FIELDS: tuple[tuple[str, str], ...] = (
    ("aliyun", "api_key"),
    ("tencent", "secret_id"), ("tencent", "secret_key"), ("tencent", "app_id"),
    ("xfyun", "app_id"), ("xfyun", "api_key"), ("xfyun", "api_secret"),
    ("xfyun", "std_app_id"), ("xfyun", "std_api_key"),
    ("volcengine", "api_key"), ("volcengine", "access_token"),
    ("volcengine", "app_id"),
    ("dialog", "api_key"), ("dialog", "app_id"), ("dialog", "access_token"),
    ("llm", "api_key"),
)

# dialog 的嵌套子节（qwen/hermes）同样带密钥，扁平清单盖不到
_SECRET_NESTED: tuple[tuple[str, str, str], ...] = (
    ("dialog", "qwen", "api_key"),
    ("dialog", "hermes", "api_key"),
)


def _is_secret(section: str, key: str, nested: str | None) -> bool:
    if nested is None:
        return (section, key) in SECRET_FIELDS
    return (section, nested, key) in _SECRET_NESTED


def sanitize_config_dict(cfg_dict: dict) -> dict:
    """深拷贝并剔除全部密钥字段（云存档默认脱敏）。

    必须深拷贝：原地删等于把调用方手上那份配置直接抹掉。
    """
    import copy

    return strip_secret_fields(cfg_dict, _copy=True)


def strip_secret_fields(cfg_dict: dict, _copy: bool = True) -> dict:
    """递归剔除全部密钥字段（与 `sanitize_config_dict` 区别只有复制与否）。

    **判定按"键名本身"放宽到任意深度、任意父级名**，不按已知 (section, key) 配对：
    工坊片段来自外部来源，父级节名与嵌套层数不受控，只按配对查会被新命名节绕过。
    """
    import copy as _copy_mod

    node = _copy_mod.deepcopy(cfg_dict or {}) if _copy else cfg_dict
    if not isinstance(node, dict):
        return node
    for key in list(node.keys()):
        if key in _SECRET_KEY_NAMES:
            node.pop(key, None)
            continue
        value = node[key]
        if isinstance(value, dict):
            strip_secret_fields(value, _copy=False)
    return node


# 已知密钥的键名集合（跨所有父级通用）。
# 由 SECRET_FIELDS / _SECRET_NESTED 派生，不手写第二份清单 ——
# 两份清单漂移过一次就会出现"某个字段忘了脱敏"的洞，而那种洞在云存档
# 与工坊两条路上都意味着密钥外泄。
_SECRET_KEY_NAMES: frozenset[str] = frozenset(
    {key for _section, key in SECRET_FIELDS}
    | {key for _section, _sub, key in _SECRET_NESTED}
)


# 云存档里的三个文件名。写入侧、吊销侧（关掉开关时删含密钥那份）、
# 恢复侧共用；字面量分散写会漂移，而这里的漂移后果是含密钥的文件没被删掉。
CLOUD_CONFIG_NAME = "config.steamcloud.yaml"
CLOUD_FULL_CONFIG_NAME = "config.full.yaml"
CLOUD_HISTORY_NAME = "history.json"


def build_cloud_files(cfg_dict: dict, history_entries: list,
                      include_keys: bool) -> dict:
    """组装云存档文件：{文件名: 文本}（纯函数，不碰 Steam API）。

    steamcloud 恒脱敏，与 include_keys 无关（开关只决定是否多传一份 full.yaml，默认那份不能变脏）；
    只返回要写入的文件，关掉开关时含密钥旧文件的删除由调用方负责（core/app）。
    """
    import copy
    import json
    from datetime import datetime, timezone

    import yaml

    entries = list(history_entries or [])
    meta = {"saved_at": datetime.now(timezone.utc).isoformat(),
            "entries": len(entries)}
    clean = sanitize_config_dict(cfg_dict)
    clean["_meta"] = meta
    files = {
        CLOUD_CONFIG_NAME: yaml.safe_dump(
            clean, allow_unicode=True, sort_keys=False),
        CLOUD_HISTORY_NAME: json.dumps(entries, ensure_ascii=False),
    }
    if include_keys:
        full = copy.deepcopy(cfg_dict or {})
        full["_meta"] = meta
        files[CLOUD_FULL_CONFIG_NAME] = yaml.safe_dump(
            full, allow_unicode=True, sort_keys=False)
    return files


def _strip_secrets_in_node(node, section: str, nested: str | None = None,
                           allow_secrets: bool = False):
    """就地剔除一个节点里的全部密钥字段（递归到任意深度）。

    用于「整块从云收进来」的路径：_merge_node 只逐键判断，整块落下时会漏嵌套密钥。
    allow_secrets=True 整段跳过：调用方已判定这份云配置来自用户自己开的同步开关。
    """
    if allow_secrets or not isinstance(node, dict):
        return node
    for key in list(node.keys()):
        if _is_secret(section, key, nested):
            node.pop(key, None)
        elif isinstance(node[key], dict):
            _strip_secrets_in_node(node[key], section, nested=key)
    return node


def _merge_node(dst: dict, src: dict, section: str, cloud_newer: bool,
                nested: str | None, allow_secrets: bool = False) -> None:
    """就地合并一节：默认密钥字段永不从云覆盖，其余按新旧与缺失决定。

    allow_secrets=True 时密钥字段按普通字段处理——用户在其他机器上
    自己开了密钥同步的那条路（merge_cloud_config 规则 1）。
    """
    import copy

    for key, value in src.items():
        if _is_secret(section, key, nested) and not allow_secrets:
            continue  # 密钥字段本地优先
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge_node(dst[key], value, section, cloud_newer, nested=key,
                        allow_secrets=allow_secrets)
            continue
        if key not in dst or cloud_newer:
            if isinstance(value, dict):
                # 整块落下前先脱敏：本地没有这一支，云上的嵌套密钥不能跟着进来
                dst[key] = _strip_secrets_in_node(
                    copy.deepcopy(value), section, nested=key,
                    allow_secrets=allow_secrets)
                continue
            dst[key] = copy.deepcopy(value)


def merge_cloud_config(local: dict, cloud: dict,
                       allow_secrets: bool = False) -> dict:
    """云 → 本地的恢复合并（返回副本，不改动入参）。

    密钥字段**默认永不**从云进入本地，只有 `allow_secrets=True` 才放行（默认值必须留 False，
    语义：不确定 → 不放行）；本地缺的从云补（补前脱敏）；两边都有且云更新则云覆盖。
    """
    import copy

    merged = copy.deepcopy(local or {})
    if not cloud:
        return merged
    _local_meta = (local or {}).get("_meta")
    _cloud_meta = cloud.get("_meta")
    local_saved = str(_local_meta.get("saved_at", "")) if isinstance(
        _local_meta, dict) else ""
    cloud_saved = str(_cloud_meta.get("saved_at", "")) if isinstance(
        _cloud_meta, dict) else ""
    # 本地没时间戳时按「云更新」处理：恢复动作本来就是为了把云上的设置拿回来
    # 而真实调用点只在本地 config.yaml 缺失时跑（那时 local 是空字典
    # 全靠这条把云上的字段都收进来）。两边都有时间戳则老实比大小。
    cloud_newer = (not local_saved) or bool(cloud_saved > local_saved)
    for section, value in cloud.items():
        if section == "_meta":
            continue
        if section not in merged:
            # 整节缺失也不能原样收：云上的密钥一样不带进来
            merged[section] = _strip_secrets_in_node(
                copy.deepcopy(value), section, allow_secrets=allow_secrets)
            continue
        if isinstance(value, dict) and isinstance(merged[section], dict):
            _merge_node(merged[section], value, section, cloud_newer,
                        nested=None, allow_secrets=allow_secrets)
        else:
            logger.debug(
                "云存档合并跳过类型不匹配的节 %s（云 %s / 本地 %s）",
                section, type(value).__name__, type(merged[section]).__name__)
    return merged


class WorkshopItem:
    """一个已订阅（或刚创建）的工坊物品的**只读快照**。

    是快照不是活句柄：状态在 Steam 客户端后台推进，UI 上拿到的一定是
    某一刻的样子。刷新靠重新调 `SteamIntegration.workshop_items()`。
    """

    __slots__ = ("item_id", "state", "size_on_disk", "downloaded", "total",
                 "install_dir")

    def __init__(self, item_id: int, state: int, size_on_disk: int = 0,
                 downloaded: int = 0, total: int = 0, install_dir: str = "") -> None:
        self.item_id = int(item_id)
        self.state = int(state)
        self.size_on_disk = int(size_on_disk)
        self.downloaded = int(downloaded)
        self.total = int(total)
        self.install_dir = install_dir or ""

    @property
    def installed(self) -> bool:
        return bool(self.state & _ITEM_STATE_INSTALLED)

    @property
    def subscribed(self) -> bool:
        return bool(self.state & _ITEM_STATE_SUBSCRIBED)

    @property
    def needs_update(self) -> bool:
        return bool(self.state & _ITEM_STATE_NEEDS_UPDATE)

    @property
    def downloading(self) -> bool:
        return bool(self.state & _ITEM_STATE_DOWNLOADING)

    @property
    def download_pending(self) -> bool:
        return bool(self.state & _ITEM_STATE_DOWNLOAD_PENDING)

    @property
    def download_percent(self) -> int:
        """下载百分比 0~100；总量未知（0）时恒 0，绝不除零。"""
        if self.total <= 0:
            return 0
        return max(0, min(100, int(self.downloaded * 100 / self.total)))

    def __repr__(self) -> str:      # pragma: no cover - 调试用
        return (f"WorkshopItem(id={self.item_id}, state={self.state}, "
                f"installed={self.installed}, dir={self.install_dir!r})")


# EItemState 位标志（与 steam/steamworks/__init__.py 的常量同值）。
# 这里复制一份而不是 import：发行层不该依赖随 depot 投放的自研封装
# ——源码构建的模块树里根本没有那个包，import 会直接 ImportError。
_ITEM_STATE_SUBSCRIBED = 1 << 0
_ITEM_STATE_INSTALLED = 1 << 2
_ITEM_STATE_NEEDS_UPDATE = 1 << 3
_ITEM_STATE_DOWNLOADING = 1 << 4
_ITEM_STATE_DOWNLOAD_PENDING = 1 << 5


class StatsTracker:
    """统计累计（只记数字，不记文本）：录音秒数 / 识别字数 / 会话数。"""

    def __init__(self) -> None:
        self.recorded_seconds = 0
        self.recognized_chars = 0
        self.sessions = 0

    def add_final_chars(self, n: int) -> None:
        self.recognized_chars += max(0, int(n))

    def add_session(self, seconds: float) -> None:
        self.sessions += 1
        self.recorded_seconds += max(0, int(seconds))

    def flush(self, integ) -> bool:
        """落盘；返回是否成功（能力层不可用时 integ.store_stats 自会返 False）。"""
        return integ.store_stats(
            self.recorded_seconds, self.recognized_chars, self.sessions)


def nvidia_gpu_present() -> bool:
    """N 卡探测：NVIDIA 驱动在则 nvcuda.dll 可加载（不依赖 CUDA 版 torch）。

    只用来决定要不要提示装显卡加速 DLC，误判的代价很低，所以宁可用最省事的
    探测方式，不要为此拉起 torch/nvidia-smi。
    """
    try:
        import ctypes

        ctypes.WinDLL("nvcuda.dll")
        return True
    except Exception:
        return False
