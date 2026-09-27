"""豆包端到端实时语音大模型（S2S RealtimeAPI）—— 帧编解码层。

接口文档：火山引擎《豆包端到端实时语音大模型 API》（修订记录最新 26.04.29）。

本模块只含编解码（纯函数、零 IO、零线程，唯一例外是 DialogFrame dataclass）：
build_event_frame / build_audio_frame / parse_frame / build_auth_headers /
build_start_session_payload，可拿文档给出的十进制字节示例逐字节断言。

DoubaoDialogClient（独立线程 + asyncio loop + 收发循环）在 Plan 2 的
doubao_dialog.py 里，会 import 本模块。

与现有 volcengine_realtime.py（ASR 流式识别）的协议差异（不可复用其打包/解析）：
- **不用 sequence**：ASR 音频包必须带严格连续序号（负包 -(seq+1)），S2S 完全不用。
  文档 StartConnection 示例 byte1=0x14 → flags=0b0100，低 2 bit 为 0 = 无 sequence
- **客户端生成 session id**：uuid4()，StartSession 及之后所有 Session 级事件都要带
- **不压缩**：文档明确推荐 0b0000（ASR 首包必须 gzip）
- **音频响应 msg_type=0b1011**：ASR 只有 0b1001
- **旧版鉴权头布局不同**：S2S 用 X-Api-App-ID 装 App ID，X-Api-App-Key 是固定值；
  ASR 用 X-Api-App-Key 装 App ID
"""

from __future__ import annotations

import gzip
import json
import logging
import struct
import uuid
import zlib
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ---- 二进制协议常量（文档 2.2）----
PROTOCOL_VERSION = 0b0001
HEADER_SIZE = 0b0001          # 值 1 表示头长 4 字节

MSG_FULL_CLIENT_REQUEST = 0b0001    # 客户端文本事件
MSG_AUDIO_ONLY_REQUEST = 0b0010     # 客户端音频
MSG_FULL_SERVER_RESPONSE = 0b1001   # 服务端文本事件
MSG_AUDIO_ONLY_RESPONSE = 0b1011    # 服务端音频
MSG_ERROR_RESPONSE = 0b1111         # 服务端错误

FLAG_SEQ_MASK = 0b0011        # 低 2 bit：sequence 标志（本项目不用，恒为 0）
FLAG_EVENT = 0b0100           # 携带事件 ID

SERIAL_RAW = 0b0000
SERIAL_JSON = 0b0001
COMPRESS_NONE = 0b0000
COMPRESS_GZIP = 0b0001

# ---- 连接信息（文档 2.1）----
WS_URL = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
RESOURCE_ID = "volc.speech.dialog"
FIXED_APP_KEY = "PlgvMymc7f3tQnJ6"     # 文档给的固定值，不是密钥

# ---- 客户端事件 ID（文档 2.3）----
EV_START_CONNECTION = 1
EV_FINISH_CONNECTION = 2
EV_START_SESSION = 100
EV_FINISH_SESSION = 102
EV_TASK_REQUEST = 200
EV_UPDATE_CONFIG = 201
EV_END_ASR = 400
EV_CLIENT_INTERRUPT = 515

# ---- 模型版本与音频参数 ----
DEFAULT_MODEL_O2 = "1.2.1.1"        # O2.0 通用对话（文档标「规范版本号」）
DEFAULT_MODEL_SC2 = "2.2.0.0"       # SC2.0 角色扮演
DEFAULT_SPEAKER = "zh_female_vv_jupiter_bigtts"   # vv 音色（文档默认）

INPUT_SAMPLE_RATE = 16000           # 上行：PCM 单声道 int16 小端
OUTPUT_SAMPLE_RATE = 24000          # 下行：指定 pcm_s16le 后的采样率
OUTPUT_FORMAT = "pcm_s16le"         # 关键：改掉默认 OGG/Opus，零新依赖
DIALOG_BLOCK_FRAMES = 320           # 20ms@16kHz（文档 1.2 强烈推荐，= 640 字节/包）

# ---- P0 联调定案（2026-09-03，诊断脚本实测，结论见设计文档「联调待验证清单」）----
# #4 speech_rate / loudness_rate 归属层级：文档 tts 层级字段说明与 tts.audio_config
# 示例自相矛盾；两处服务端均接受，但语速对比（--speech-rate 40 ± --rate-in-tts）
# 仅 audio_config 层听感更快，故固定写 audio_config（ 原 RATE_IN_AUDIO_CONFIG=True，
# 临时开关已随定案删除，不再留可翻转的全局态）。
# #7 asr.audio_info：实测不声明也正常识别（PCM 16k 是默认格式），固定不声明
#（ 原 DECLARE_ASR_AUDIO_INFO=False，同样删除）。
# #2 音频帧 session id：必需，见 build_audio_frame。

# ---- JSON 序列化：必须紧凑 + 不转义中文 ----
# 文档 StartSession 示例 payload 为 60 字节且无空格；json.dumps 默认分隔符
# 是 ', ' / ': ' 会多出空格，默认 ensure_ascii=True 会把中文转成 \uXXXX
_JSON_KWARGS = {"ensure_ascii": False, "separators": (",", ":")}


def _build_header(message_type: int, flags: int, serialization: int,
                  compression: int = COMPRESS_NONE) -> bytes:
    """构造 4 字节协议头：ver|hsize、type|flags、serial|compress、reserved。"""
    return bytes([
        (PROTOCOL_VERSION << 4) | HEADER_SIZE,
        (message_type << 4) | flags,
        (serialization << 4) | compression,
        0x00,
    ])


def build_event_frame(event_id: int, payload: dict | None = None,
                      session_id: str | None = None) -> bytes:
    """构造 JSON 类客户端事件帧（无 sequence、无压缩）。

    布局：header(4) + event(4) + [sid_size(4) + sid] + payload_size(4) + payload

    session_id 传 None 或空串 "" 均等价于不带 id 字段，用于 Connect 类事件
    （1 StartConnection / 2 FinishConnection）—— 文档 StartConnection 示例共
    14 字节，确实不带任何 id 字段；Session 级事件（100/102/201/515 等）
    **必须传非空 session id**，否则服务端会报错。
    payload 为 None 时发 "{}"（文档示例即如此）。
    """
    body = (b"{}" if payload is None
            else json.dumps(payload, **_JSON_KWARGS).encode("utf-8"))
    out = _build_header(MSG_FULL_CLIENT_REQUEST, FLAG_EVENT, SERIAL_JSON)
    out += struct.pack(">I", event_id)
    if session_id:
        sid = session_id.encode("utf-8")
        out += struct.pack(">I", len(sid)) + sid
    out += struct.pack(">I", len(body)) + body
    return out


def build_audio_frame(audio: bytes, session_id: str,
                      include_session_id: bool = True) -> bytes:
    """构造 TaskRequest(200) 音频帧。

    布局：header(4) + event(4) + [sid_size(4) + sid] + payload_size(4) + 裸 PCM

    序列化用 Raw（0b0000）、不压缩：payload 就是 int16 小端单声道 16kHz PCM 字节。
    include_session_id 默认 True，且已由 P0 联调定案（#2）：session id 必需——
    实测不带 sid 时服务端把 payload 首 4 字节误读为 id_size，6 包后回 45000000
    解码错误并 1006 断连（"parse payload size failed: body too short"）。
    每包多 40 字节是必要开销；False 分支仅供诊断脚本对照，正式客户端不要用。
    """
    out = _build_header(MSG_AUDIO_ONLY_REQUEST, FLAG_EVENT, SERIAL_RAW)
    out += struct.pack(">I", EV_TASK_REQUEST)
    if include_session_id and session_id:
        sid = session_id.encode("utf-8")
        out += struct.pack(">I", len(sid)) + sid
    out += struct.pack(">I", len(audio)) + audio
    return out


@dataclass
class DialogFrame:
    """解析后的服务端帧。

    event_id 为 0 表示该帧未携带事件 ID；error_code 为 0 表示非错误帧；
    session_id 为 "" 表示未携带 id 字段。payload 保留原始字节
    （音频帧是 PCM/OGG，文本帧是 UTF-8 JSON），按需调 json()。
    """

    message_type: int
    flags: int
    serialization: int
    compression: int
    event_id: int = 0
    error_code: int = 0
    session_id: str = ""
    payload: bytes = b""

    def json(self) -> dict:
        """按 JSON 解析 payload；非 JSON 序列化或解析失败返回 {}（不抛）。"""
        if self.serialization != SERIAL_JSON or not self.payload:
            return {}
        try:
            data = json.loads(self.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("帧 payload JSON 解析失败 event=%s：%s",
                           self.event_id, self.payload[:200])
            return {}
        if not isinstance(data, dict):
            logger.warning("帧 payload 顶层非 dict（%s），已包装为 {'value': ...}",
                           type(data).__name__)
            return {"value": data}
        return data


def _read_payload_and_id(data: bytes, offset: int) -> tuple[bytes, str, bool]:
    """从 offset 起解析「可选 id 字段 + payload size + payload」。

    文档没给「是否携带 id」的显式标志（只说 Connect 类事件带 connect id、
    Session 类带 session id，但 StartConnection 示例里其实没带 connect id），
    所以用长度自洽校验：解析完必须正好用完整个帧。

    解释 A（优先，Session 级是常态）：id_size(4) + id + payload_size(4) + payload
    解释 B（回退）：payload_size(4) + payload
    返回 (payload, id_str, ok)；ok=False 表示两种解释都不自洽。
    """
    total = len(data)
    if offset + 4 > total:
        return b"", "", False

    # 解释 A
    id_size = struct.unpack(">I", data[offset:offset + 4])[0]
    id_end = offset + 4 + id_size
    if id_size <= total and id_end + 4 <= total:
        payload_size = struct.unpack(">I", data[id_end:id_end + 4])[0]
        if id_end + 4 + payload_size == total:
            raw_id = data[offset + 4:id_end]
            return (data[id_end + 4:total],
                    raw_id.decode("utf-8", "replace"), True)

    # 解释 B
    payload_size = struct.unpack(">I", data[offset:offset + 4])[0]
    if offset + 4 + payload_size == total:
        return data[offset + 4:total], "", True

    return b"", "", False


def parse_frame(data: bytes) -> Optional[DialogFrame]:
    """解析服务端二进制帧。无法自洽解析时返回 None（不抛异常）。

    字段顺序严格按文档 2.2 的 flags 表：
    code（仅 msg_type=0b1111）→ sequence（flags 低 2 bit 非 0）→
    event（flags & 0b0100）→ connect/session id → payload size → payload

    注意：不校验 byte0 的 version/header_size nibble —— 当前硬编码 4 字节头；
    若服务端未来发异常头长（如 header_size=2 → 8 字节头），长度自洽校验会兜底
    返回 None 并记 hex 前缀日志，失败模式是安全的。
    """
    if len(data) < 4:
        logger.warning("帧过短（%d 字节），丢弃：%s", len(data), data[:64].hex())
        return None

    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F
    offset = 4

    error_code = 0
    if message_type == MSG_ERROR_RESPONSE:
        if len(data) < offset + 4:
            logger.warning("错误帧缺少 code 字段（%d 字节）：%s", len(data), data[:64].hex())
            return None
        error_code = struct.unpack(">i", data[offset:offset + 4])[0]
        offset += 4

    # sequence：本项目不发送也不消费，但服务端可能带，需按 flags 跳过
    if flags & FLAG_SEQ_MASK:
        offset += 4

    event_id = 0
    if flags & FLAG_EVENT:
        if len(data) < offset + 4:
            logger.warning("帧声明携带 event 但长度不足（%d 字节）：%s", len(data), data[:64].hex())
            return None
        event_id = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4

    payload, session_id, ok = _read_payload_and_id(data, offset)
    if not ok:
        logger.warning("帧长度不自洽，丢弃（%d 字节）：%s", len(data), data[:64].hex())
        return None

    if compression == COMPRESS_GZIP and payload:
        try:
            payload = gzip.decompress(payload)
        # gzip 的异常家族跨三个基类：只有 BadGzipFile 是 OSError 子类（magic 非法），
        # 截断但 magic 合法抛 EOFError、deflate 体损坏抛 zlib.error —— 后两者若不
        # 列出会逃逸出 parse_frame，把接收循环连同整个对话会话一起带崩。
        except (OSError, EOFError, zlib.error) as exc:
            logger.warning("帧 payload 解压失败 event=%s（%s: %s），按原文处理",
                           event_id, type(exc).__name__, exc)

    return DialogFrame(
        message_type=message_type, flags=flags, serialization=serialization,
        compression=compression, event_id=event_id, error_code=error_code,
        session_id=session_id, payload=payload,
    )


def build_auth_headers(api_key: str = "", app_id: str = "", access_token: str = "",
                       connect_id: str | None = None) -> dict:
    """构造 WebSocket 握手鉴权头，两套布局自动选择。

    旧版双密钥（文档 2.1 表格，全部必须）：
        X-Api-App-ID      = 控制台 App ID
        X-Api-Access-Key  = 控制台 Access Token
        X-Api-Resource-Id = volc.speech.dialog
        X-Api-App-Key     = PlgvMymc7f3tQnJ6（文档给的固定值，不是密钥）
        X-Api-Connect-Id  = UUID（建议传，便于排查连接）
    新版单密钥：
        X-Api-Key + X-Api-Resource-Id + X-Api-Connect-Id

    判定与现有 volcengine_realtime._build_headers 一致：access_token 与 app_id
    均非空走旧版，否则走新版。

    注意：S2S 的旧版布局与 ASR 的不同 —— ASR 用 X-Api-App-Key 装 App ID，
    S2S 用 X-Api-App-ID 装 App ID 而 X-Api-App-Key 是固定值，不可互相套用。
    新版 X-Api-Key 是否被 /api/v3/realtime/dialogue 接受是联调待验证项 #1，
    文档只写了旧版四个头。
    """
    cid = connect_id or str(uuid.uuid4())
    if access_token and app_id:
        return {
            "X-Api-App-ID": app_id,
            "X-Api-Access-Key": access_token,
            "X-Api-Resource-Id": RESOURCE_ID,
            "X-Api-App-Key": FIXED_APP_KEY,
            "X-Api-Connect-Id": cid,
        }
    return {
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Connect-Id": cid,
    }


def build_start_session_payload(
    *,
    model: str = DEFAULT_MODEL_O2,
    bot_name: str = "",
    system_role: str = "",
    speaking_style: str = "",
    character_manifest: str = "",
    dialog_id: str = "",
    speaker: str = DEFAULT_SPEAKER,
    speech_rate: int = 0,
    loudness_rate: int = 0,
    explicit_dialect: str = "",
    end_smooth_window_ms: int = 800,
    enable_asr_twopass: bool = False,
    enable_user_query_exit: bool = True,
    strict_audit: bool = True,
    input_mod: str = "keep_alive",
) -> dict:
    """构造 StartSession(100) 的 payload。

    三条硬约束（错误码 42000020 明文）：
    - `dialog.extra.model` **必传**，取值 "1.2.1.1"(O2.0) / "2.2.0.0"(SC2.0)
    - `asr.extra` **不能为 null**，至少是 {}
    - `tts.extra` **不能为 null**，至少是 {}

    版本相关字段按 model 过滤：bot_name/system_role/speaking_style 只对 O 版本生效，
    character_manifest 只对 SC 版本生效（文档 1.1 产品约束表）；填了不匹配版本的
    字段直接不发，避免用户误以为生效。空字符串的可选字段一律不发（省字节，
    也避免服务端把空串当成有效配置）；dialog_id 总是发（空串 = 不续接上下文）。

    end_smooth_window_ms 默认 800（文档默认 1500）：判停快 700ms，对话更跟手。
    enable_asr_twopass 默认关：二遍识别更准但增加延迟，与对话场景的跟手诉求冲突。
    """
    is_sc = model == DEFAULT_MODEL_SC2

    asr_extra: dict = {"end_smooth_window_ms": int(end_smooth_window_ms)}
    if enable_asr_twopass:
        asr_extra["enable_asr_twopass"] = True
    asr: dict = {"extra": asr_extra}
    # 定案 #7：不声明 asr.audio_info（PCM 16k 是默认格式，实测识别正常）。

    dialog: dict = {
        "dialog_id": dialog_id,
        "extra": {
            "model": model,
            "strict_audit": bool(strict_audit),
            "input_mod": input_mod,
            "enable_user_query_exit": bool(enable_user_query_exit),
        },
    }
    if is_sc:
        if character_manifest:
            dialog["character_manifest"] = character_manifest
    else:
        if bot_name:
            dialog["bot_name"] = bot_name
        if system_role:
            dialog["system_role"] = system_role
        if speaking_style:
            dialog["speaking_style"] = speaking_style

    audio_config: dict = {"channel": 1, "format": OUTPUT_FORMAT,
                          "sample_rate": OUTPUT_SAMPLE_RATE}
    tts_extra: dict = {}
    if explicit_dialect:
        tts_extra["explicit_dialect"] = explicit_dialect
    # 定案 #4：speech_rate / loudness_rate 写 audio_config 层（实测该层生效，
    # tts 层级虽被接受但无听感差异）。
    audio_config["speech_rate"] = int(speech_rate)
    audio_config["loudness_rate"] = int(loudness_rate)

    return {
        "asr": asr,
        "dialog": dialog,
        "tts": {"speaker": speaker, "extra": tts_extra, "audio_config": audio_config},
    }
