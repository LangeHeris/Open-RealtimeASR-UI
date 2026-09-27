r"""管理员提权与「永久为管理员启动」。

右键菜单「程序提权」（即时一次性提权）与 ui.always_admin（永久为
管理员启动）共用这里的实现。

「永久为管理员启动」为什么用计划任务：Windows 不允许普通进程静默
获得管理员权限，runas 提权每次必弹 UAC；而"以最高权限运行"的登录
计划任务（schtasks /RL HIGHEST）由系统在登录时提权拉起，普通权限
进程也能用 schtasks /Run 触发——两条路径都不弹 UAC。因此：

- 首次开启：UAC 确认一次后，由提权实例创建计划任务（登录触发 +
  手动触发共用；命令与开机自启同款 --hidden 静默启动）
- 之后开机：计划任务直接以管理员拉起，不弹 UAC；手动双击等普通
  启动检测到开关开启时触发计划任务（无 UAC）后自身退出
- 关闭开关：删除计划任务（普通权限删失败时弹一次 UAC 提权删）
- 提权实例启动时自动对账自愈：开关开着但任务缺失则补建，开关关着
  但任务残留则清理

「程序提权」（右键菜单）：ShellExecuteW runas 拉起提权实例携带
--replace（单实例接管协议，见 app._acquire_single_instance），
每次弹一次 UAC，属预期行为。

游戏模式开启后的提权询问（should_prompt_elevation）：游戏常以管理员运行，
普通权限的合成输入会被 UIPI 静默丢弃，故开启游戏模式时主动问一次
（仅未提权时，每次都问），确认则走上述「程序提权」同一条路径。与注入
时的事后提示（app._on_inject_requested 里的权限差检测）互补：那里只告知
需提权、不给操作入口。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

# 计划任务名（任务计划程序里显示的名称）
TASK_NAME = "Open-RealtimeASR-UI-Admin"
# 单实例命名管道（与 core.app.VoiceApp._INSTANCE_KEY 保持一致）。
# 此处用原生管道探测而不引 QLocalSocket：启动早期 QApplication 尚未
# 创建，构造 Qt 对象有风险；纯 open() 足够
_INSTANCE_PIPE = r"\\.\pipe\openrealtimeasr-ui-single-instance"

# CREATE_NO_WINDOW：pythonw / 打包 exe 下 subprocess 不闪控制台黑框
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def is_elevated() -> bool:
    """当前进程是否以管理员（高完整性级别）运行。非 Windows 一律 False。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def should_prompt_elevation(enabled: bool, elevated: bool | None = None) -> bool:
    """开启游戏模式后是否该询问「是否以管理员身份重启」。

    三个条件同时成立才问：
    - `enabled`：关闭游戏模式不需提权，问了是干扰；
    - Windows：其余平台没有 UAC 提权路径（`relaunch_elevated` 恒返回 False）；
    - 当前未提权：已是管理员时提问没有意义（`_on_elevate` 只会回一句提示）。

    `elevated` 可注入（None = 实时查 `is_elevated()`），便于离线单测。
    与注入时的事后提示（app._on_inject_requested 里的 UIPI 权限差检测）互补：
    这里是事前主动询问且带一键操作入口，那里是注入真失败后的证据提示。
    """
    if not enabled or sys.platform != "win32":
        return False
    if elevated is None:
        elevated = is_elevated()
    return not elevated


def _quoted_args() -> str:
    """当前命令行参数转成参数串（含 --replace 提权接管标记）。"""
    args = list(sys.argv[1:])
    if "--replace" not in args:
        args.append("--replace")
    return " ".join(f'"{a}"' if (" " in a or a == "") else a for a in args)


def _launch_target() -> tuple[str, str]:
    """提权重启目标 (exe, 参数)：打包版=exe 本身；商店版=launcher.exe；源码版=pythonw + run.py。"""
    params = _quoted_args()
    if getattr(sys, "frozen", False):
        return sys.executable, params
    try:
        from ..paths import install_dir
        root = install_dir()
        if root is not None:
            launcher = root / "launcher.exe"
            if launcher.is_file():
                # 渠道版：提权重启经 launcher（注入发行标记、选依赖环境）；
                # argv 已含发行标记（launcher 再注入一次无害），--replace 照常透传
                return str(launcher), params
    except Exception:
        pass
    exe = Path(sys.executable)
    # 源码模式优先 pythonw：提权重启不弹控制台黑框（与 autostart 同规则）
    pyw = exe.with_name("pythonw.exe")
    python = pyw if pyw.exists() else exe
    project_root = Path(__file__).resolve().parent.parent.parent
    script = project_root / "run.py"
    return str(python), f'"{script}" {params}'.strip()


def relaunch_elevated() -> tuple[bool, str]:
    """以管理员身份重启程序（弹出 UAC 确认框）。返回 (是否成功发起, 消息)。

    返回 True 只代表提权实例已拉起（UAC 已确认），旧实例应自行退出；
    用户拒绝 UAC 确认框时返回 False。
    """
    if sys.platform != "win32":
        return False, "仅支持 Windows"
    try:
        import ctypes
        exe, params = _launch_target()
        # SW_SHOWNORMAL=1；返回值 >32 表示成功（拒绝 UAC 时返回 5）
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, params, None, 1,
        )
        if ret > 32:
            return True, "提权实例已启动"
        if ret == 5:
            # ERROR_ACCESS_DENIED：用户在 UAC 确认框里点了「否」。与其它失败分开，
            # 调用方据此回滚「永久为管理员启动」开关（拒绝 ≠ 已启用，见
            # settings_dialog 的 admin 行）
            return False, "用户取消了 UAC 确认"
        return False, f"提权失败（ShellExecuteW 返回 {ret}）"
    except Exception as exc:
        return False, f"提权请求异常：{exc}"


# ---- 单实例管道探测（原生命名管道，无 Qt 依赖） ----


def _instance_pipe_alive() -> bool:
    """主实例是否在运行（连接一次单实例管道后立刻断开，空数据无副作用）。"""
    try:
        fd = open(_INSTANCE_PIPE, "r+b", buffering=0)
        fd.close()
        return True
    except OSError:
        return False


def _notify_show(timeout: float = 6.0) -> bool:
    """等单实例服务端起来后发 show 指令：手动双击场景提权实例以 --hidden
    驻留托盘，替双击的用户唤出悬浮条（与双击普通程序的行为一致）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = open(_INSTANCE_PIPE, "r+b", buffering=0)
            fd.write(b"show\n")
            fd.close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def _wait_instance_pipe(timeout: float = 5.0) -> bool:
    """轮询等待主实例出现（触发任务后在途的提权实例冷启动需 1~2 秒）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _instance_pipe_alive():
            return True
        time.sleep(0.2)
    return False


# ---- 计划任务管理（schtasks 封装） ----


def _schtasks(*args: str) -> int:
    """执行 schtasks 命令，返回退出码（只看码不解析输出，避免编码问题）。"""
    try:
        return subprocess.run(
            ["schtasks", *args], capture_output=True,
            creationflags=_NO_WINDOW,
        ).returncode
    except OSError:
        return -1


def admin_task_exists() -> bool:
    """管理员启动计划任务是否存在。"""
    return _schtasks("/Query", "/TN", TASK_NAME) == 0


def create_admin_task() -> tuple[bool, str]:
    """创建"以最高权限运行"的登录计划任务（需在提权状态下调用）。

    任务命令复用开机自启的同款构造（exe / pythonw+run.py，--hidden
    静默启动）：登录触发与 schtasks /Run 手动触发共用同一命令行。
    """
    if sys.platform != "win32":
        return False, "仅支持 Windows"
    from .autostart import _launch_command
    cmd = _launch_command()
    if not cmd:
        return False, "无法确定启动命令"
    rc = _schtasks("/Create", "/TN", TASK_NAME, "/TR", cmd,
                   "/SC", "ONLOGON", "/RL", "HIGHEST", "/F")
    if rc == 0:
        return True, "已创建管理员启动任务"
    return False, f"schtasks 创建失败（退出码 {rc}，需管理员权限）"


def run_admin_task() -> bool:
    """触发计划任务运行（普通权限即可触发自己用户的最高权限任务，无 UAC）。"""
    return _schtasks("/Run", "/TN", TASK_NAME) == 0


def _run_elevated_wait(exe: str, params: str, timeout_ms: int = 15000) -> bool:
    """弹一次 UAC 运行外部命令并等待结束（用于提权删除计划任务）。"""
    try:
        import ctypes
        from ctypes import wintypes

        class _SEEINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", ctypes.c_ulong),
                ("hwnd", wintypes.HANDLE),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SEE_MASK_NOASYNC = 0x00000100
        info = _SEEINFO()
        info.cbSize = ctypes.sizeof(_SEEINFO)
        info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
        info.lpVerb = "runas"
        info.lpFile = exe
        info.lpParameters = params
        info.nShow = 0  # SW_HIDE
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
            return False
        if info.hProcess:
            WAIT_OBJECT_0 = 0
            r = ctypes.windll.kernel32.WaitForSingleObject(
                info.hProcess, timeout_ms)
            ctypes.windll.kernel32.CloseHandle(info.hProcess)
            return r == WAIT_OBJECT_0
        return True
    except Exception:
        return False


def delete_admin_task() -> tuple[bool, str]:
    """删除管理员启动计划任务。

    先以当前权限删（任务是本用户创建的，owner 通常有权直接删）；
    失败则弹一次 UAC 提权删除（schtasks 挂在 UAC 提权下执行并等待）。
    """
    if sys.platform != "win32":
        return False, "仅支持 Windows"
    if not admin_task_exists():
        return True, "任务不存在，无需清理"
    if _schtasks("/Delete", "/TN", TASK_NAME, "/F") == 0:
        return True, "已删除管理员启动任务"
    # 普通权限删失败：提权删（弹一次 UAC）
    if _run_elevated_wait("schtasks.exe", f'/Delete /TN "{TASK_NAME}" /F'):
        if not admin_task_exists():
            return True, "已删除管理员启动任务"
        return False, "提权删除后任务仍存在，请手动清理"
    return False, f"删除失败（需管理员权限），可在任务计划程序手动删除「{TASK_NAME}」"


def relaunch_if_configured(cfg) -> bool:
    """「永久为管理员启动」启动期判定（main 启动早期调用）。
    返回 True 表示调用方应立即退出当前实例。

    提权实例顺带对账自愈：开关开着但任务缺失则补建（首次开启后的
    落地动作）、开关关着但任务残留则清理（关闭时删除失败的兜底）。
    """
    import logging

    if sys.platform != "win32":
        return False
    log = logging.getLogger("asr_voice")
    admin = bool(getattr(getattr(cfg, "ui", None), "always_admin", False))

    if is_elevated():
        if admin:
            if not admin_task_exists():
                ok, msg = create_admin_task()
                if ok:
                    log.info("已创建管理员启动计划任务（%s）", TASK_NAME)
                else:
                    log.warning("管理员启动计划任务创建失败：%s", msg)
        elif admin_task_exists():
            # 开关已关但任务残留（关闭时删除失败遗留）：自愈清理
            ok, msg = delete_admin_task()
            log.info("自愈清理残留管理员启动任务：%s", "成功" if ok else msg)
        return False

    if not admin:
        return False

    # 未提权 + 开关开启：
    if admin_task_exists():
        # 已有实例在跑：走正常第二实例路径（唤出悬浮条，不动任务）
        if _instance_pipe_alive():
            return False
        # 触发计划任务：提权实例以管理员启动，全程无 UAC
        if run_admin_task():
            log.info("永久管理员启动：已触发计划任务（无 UAC），本进程退出")
            # 手动双击场景：提权实例 --hidden 驻留，替用户唤出悬浮条
            _notify_show()
            return True
        # 触发失败多为"任务已在运行"（提权实例在途/驻留）：等它出现
        if _wait_instance_pipe(5.0):
            log.info("永久管理员启动：提权实例已在运行，本进程退出")
            return True
        log.warning("计划任务触发失败，回退 UAC 提权")
    # 任务不存在（首次开启 / 被删 / 被禁用）：UAC 拉起提权实例，
    # 由它在 relaunch_if_configured 的提权分支补建任务
    ok, msg = relaunch_elevated()
    if ok:
        log.info("永久管理员启动：已拉起提权实例，当前普通权限实例退出")
        return True
    log.warning("永久管理员启动提权失败：%s（本次以普通权限运行）", msg)
    return False
