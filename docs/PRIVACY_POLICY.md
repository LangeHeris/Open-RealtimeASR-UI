# 隐私政策 / Privacy Policy

最后更新 / Last updated: 2026-10-11

本政策适用于 Open-RealtimeASR-UI（ORI），包括渠道发行版与 GitHub 开源版。

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
- 默认只同步**脱敏配置**（不含任何密钥）与历史记录；密钥只留在本机。
- 「云存档同步密钥」是自选开关，**双向**生效：开启后含密钥的完整配置副本会上传，任何登录该平台账号的设备都能读取；在另一台设备上首次启动时，这份副本会被自动恢复回来，无需重新填写密钥。
- 关闭该开关时，云端那份含密钥的副本会被**删除**，密钥不再上传。若删除因平台原因失败，程序会记录警告并提示您在平台的云同步设置中手动清除。
- 无论开关如何，脱敏配置与历史记录始终会同步。
- By default only a **sanitized** config (no API keys) and history are synced; keys stay on this machine.
- "Sync API keys via cloud save" is an opt-in toggle and works in **both directions**: when on, a full config copy including keys is uploaded and any device signed into that platform account can read it; on a new device that copy is restored automatically, so keys need not be re-entered.
- When you turn the toggle off, the key-bearing copy in the cloud is **deleted** and no keys are uploaded. If the platform rejects the deletion, the app logs a warning and asks you to clear it manually in the platform's cloud settings.
- The sanitized config and history are always synced regardless of the toggle.

## 5. 统计 / Statistics
- 仅累计数字：录音秒数、识别字数、识别会话数。不上传任何文本与音频。
- Only aggregates: recorded seconds, recognized characters, session count. No text or audio is uploaded.

## 6. 其他 / Other
- 无广告、无第三方追踪。日志仅存本机（logs/ 目录）。
- No ads, no third-party tracking. Logs are stored locally.
