# 隐私政策 / Privacy Policy

最后更新 / Last updated: 2026-09-22

本政策适用于 Open-RealtimeASR-UI（ORI），包括商店版与 GitHub 开源版。

## 1. 音频处理 / Audio processing
- 默认内置离线识别引擎（FunASR）：音频在本机处理，不上传任何服务器，无需联网。
- 云端识别引擎（阿里云/腾讯云/讯飞/火山引擎，可选）：启用时音频直接发送至您自选的厂商服务器；本软件不存储、不中转这些音频。
- The built-in offline engine processes audio locally and uploads nothing. Optional cloud engines (Alibaba Cloud / Tencent Cloud / iFlytek / Volcengine) send audio directly to the vendor you choose; this app does not store or relay it.

## 2. AI 文本修正 / AI correction
- 可选功能：识别文本发送至您自行配置的 OpenAI 兼容接口（自备地址与密钥）；本软件不存储发送内容。
- Optional: transcripts are sent to your self-hosted OpenAI-compatible endpoint. Nothing is stored by this app.

## 3. 剪贴板 / Clipboard
- 文本注入临时读写剪贴板，并在注入后按设置恢复；本软件不记录剪贴板历史。
- Text injection temporarily reads/writes the clipboard and restores it per settings. No clipboard history is kept.

## 4. 云端存档 / Cloud Save
- 默认同步脱敏配置（不含任何密钥）与历史记录；「云存档同步密钥」为自选开关，开启后含密钥的完整配置副本才会上传，任何登录该平台账号的设备可获取。
- By default only a sanitized config (no API keys) plus history is synced. Uploading a full config including keys requires the opt-in toggle; any device signed into that platform account can then read them.

## 5. 统计 / Statistics
- 仅累计数字：录音秒数、识别字数、识别会话数。不上传任何文本与音频。
- Only aggregates: recorded seconds, recognized characters, session count. No text or audio is uploaded.

## 6. 其他 / Other
- 无广告、无第三方追踪。日志仅存本机（logs/ 目录）。
- No ads, no third-party tracking. Logs are stored locally.
