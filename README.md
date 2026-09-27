<p align="center">
  <img src="docs/ori_banner.jpg" alt="Open-RealtimeASR-UI 横幅" width="100%">
</p>

<div align="center">

# Open-RealtimeASR-UI (ORI)

</div>

<div align="center">

![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)![Platform](https://img.shields.io/badge/Platform-Windows-0078D6.svg)![GUI](https://img.shields.io/badge/GUI-PySide6-41CD52.svg)

</div>

<div align="center">

**简体中文** | [English](README_en.md)

</div>

***

<div align="center">
  <img src="docs/ori_logo.jpg" alt="ORI 凯尔特结徽标" width="220">
</div>

## 项目简介

**Open-RealtimeASR-UI（ORI）是一款面向 Windows 的实时语音听写工具**：按下全局热键开口说话，文字实时出现在光标所在位置——任何输入框都行，游戏里也行；它是 Windows 原生语音输入（Win+H）的开源替代方案。

它的核心设计是**不锁定引擎、自定义听写行为**：云端四家高精度实时流式识别引擎，外加离线本地引擎 FunASR（Paraformer-zh-streaming），右键菜单里自由切换文本注入方式，听写链路上的 AI 功能、语音命令与 AI 实时语音对话，每项都能按需开关。

> 建议在耳机环境下使用，以免扬声器回声干扰识别。完整操作说明见 [用户说明书](docs/User_Doc_zh.md)。

## 快速上手

### 环境要求

- Windows 10 / 11
- 打包版（Releases 的 zip）：解压后双击 `ORI.exe` 即用，无需任何环境（`_internal\` 目录须与 exe 同在）
- 源码版：Python 3.10+

### 三步跑起来

```bash
# 1. 克隆仓库
git clone <repo-url>
cd <repo-dir>

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python run.py
# 或 python -m asr_voice.main（带 _pth 的隔离 Python 下会 ModuleNotFoundError，失败时换上面那条）
# 也可以双击 start.bat，它会自动挑一个可用的 Python
```

### 首次配置

1. 首次启动自动生成 `config.yaml`（打包版在 exe 同目录，源码版在 `config/` 下）
2. 默认引擎为阿里云百炼：在 设置 → 引擎密钥 填入至少一家云厂商的密钥**（各厂商均有免费体验额度）**，或右键切换为本地 FunASR（需先安装 funasr，见下文）
3. 聚焦任意输入框，按 `Ctrl+G` 说话，再按一次停止，文字自动上屏

完整的首次配置流程见 [用户说明书](docs/User_Doc_zh.md) 第 3 章；全部配置字段与注释见 [config/config.example.yaml](config/config.example.yaml)。

## 识别引擎

### 云端引擎

云端四种 + 本地一种，右键菜单按分类档呈现（腾讯云 3 档 / 阿里云 2 档 / 讯飞 2 档 / 火山 2 档），各档绑定的模型与显隐可在设置界面调整：

| 引擎 | 模型 / 版本 | 密钥 | 说明 |
| :-- | :-- | :-- | :-- |
| 阿里云百炼（默认） | 通用引擎 `paraformer-realtime-v2`；大模型版 `qwen-audio-3.0-asr-flash-streaming` | `api_key` | 支持语气词过滤、热词表、语种提示 |
| 腾讯云 | 三档可配：大模型2.0（默认 `16k_zh_en_2.0`）/ 大模型1.0（`16k_zh_en`）/ 通用引擎（`16k_zh`）；直调默认 `Hy-ASR-3.0-preview` | `secret_id` + `secret_key` + `app_id` | 每档实际调用的模型可在设置中改选；支持标点 / 数字 / 脏词 / 语气词过滤 |
| 讯飞 | 大模型版 `autodialect`（中英 + 202 方言）；标准版中文 / 英文 | 大模型版 `app_id`+`api_key`+`api_secret`；标准版 `std_app_id`+`std_api_key` | 两版密钥独立、互不通用 |
| 火山引擎 | 豆包 2.0（`seedasr`）/ 1.0（`bigasr`），两代计费独立配置 | 新版 `api_key`；旧版 `app_id`+`access_token` | 支持二遍识别 + 语义顺滑（DDC） |
| 本地 FunASR | `paraformer-zh-streaming` | **无需密钥** | 完全离线、免费，CPU / GPU 均可 |

> 云端引擎需在各厂商控制台自费开通（均有免费体验额度）；本地 FunASR 完全免费离线。
>
> **打包版（exe）目前不含本地 FunASR 引擎**：funasr + torch 体积过大未打包，选 FunASR 会提示改用云端引擎或源码方式运行。
>
> 各引擎的参数配置见 [用户说明书](docs/User_Doc_zh.md) 第 6 章。

### 本地引擎  FunASR 

```bash
# CPU
pip install funasr torch torchaudio

# GPU（CUDA 11.8 示例）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
pip install funasr
```

- 模型权重本仓库不内置、不分发：首次使用时自动从 ModelScope 下载（约 900MB，缓存到 `~/.cache/modelscope/`），也可用 `funasr.local_dir` 指向自行下载好的本地模型目录；下载渠道 `funasr.model_hub` 可选 `ms`（ModelScope）/ `hf`（HuggingFace）
- 启动即后台预热模型，就绪后悬浮条提示；识别窗口可选默认 600ms / 低延迟 480ms / 高准确 720ms
- 本地引擎仅源码版可用（打包版 exe 不含 funasr + torch）；离线使用请提前下载好模型

详见 [用户说明书](docs/User_Doc_zh.md) 3.3 节。

## 语音模式

听写之外，按 `Ctrl+Shift+H` 进入端到端语音对话（设置 → 语音模式）：

| 服务端 | 配置值 | 说明 |
| --- | --- | --- |
| 豆包 S2S（默认） | `doubao` | 火山引擎端到端语音大模型：识别、理解、语音回复一体；O2.0 通用对话 / SC2.0 角色扮演两档模型，7 个官方音色，AI 播报中随时插话打断 |
| 阿里云百炼 Qwen-Audio | `aliyun` | Qwen-Audio Realtime 端到端语音大模型：plus / flash 两档，5 个官方音色，支持联网搜索 |
| Hermes Agent | `hermes` | 自建 hermes-agent API server：纯文本回复，断句与识别由本机完成（对话专属 ASR），思考中可按热键取消本轮，无计费 |

对话行为：

- **进入 / 退出**：按对话热键，或右键菜单「语音模式」；对 AI 说「退出 / 再见」也能结束
- **打断**：豆包 / Qwen 默认全双工，AI 播报中直接开口即可打断；Hermes 按轮串行（恒半双工）
- **记忆**：豆包支持跨次记忆（续接最近 20 轮）；Qwen 为单会话内记忆；Hermes 服务端按会话记忆
- **计费**：豆包 / Qwen 按厂商规则计费；Hermes 自建自用，无计费

各提供商的密钥、模型、人设与行为差异见 [用户说明书](docs/User_Doc_zh.md) 第 9 章。



## 项目文档

- **[用户说明书](docs/User_Doc_zh.md)**：完整的使用指南——安装与首次配置、界面说明、引擎配置、AI 修正、语音命令、实时语音对话、设置界面、配置字段、常见问题与故障排查

- **[配置模板](config/config.example.yaml)**：完整配置字段及注释；配置文件查找顺序与常用字段速查见用户说明书第 12 章

- **[贡献指南](CONTRIBUTING.md)**：开发环境、代码结构约定与提交规范

- **[第三方声明](THIRD_PARTY_NOTICES.md)**：第三方组件完整清单、版权署名与商用合规说明

## 项目结构

```
asr_voice/
├── asr/            # 识别引擎封装+ 语音对话
├── audio/          # 麦克风采集、VAD、对话播报、录音时暂停播放/全局静音
├── config/         # 配置默认值与加载
├── core/           # 主应用、识别管线、语音对话、语音命令、历史记录、开机自启、程序提权、录音前的底噪校准
├── input/          # 文本注入
├── postprocess/    # AI 修正
└── ui/             # 悬浮条、设置界面、主题系统、字体
config/             # 配置模板 config.example.yaml
themes/             # 扩展主题包库
fonts/              # 内置字体（仅本地/渠道版构建使用，不入公开仓；缺省时自动回退系统字体）
docs/               # 文档
```

## 下载途径

- **源码**：克隆本仓库，按[快速上手](#快速上手)运行；本地 FunASR 模型由用户自行下载

- **打包版（zip）**：见 Releases。下载后解压到任意目录，双击其中的 `ORI.exe` 运行。**当前发布尚未代码签名**（SignPath 开源签名接入中，CI 凭据配置完成后自动签名）：Windows SmartScreen 或杀软可能提示风险——PyInstaller 打包叠加全局热键、文本注入类行为容易触发启发式误报；可用 Release 资产 `SHA256SUMS.txt` 核对 zip 哈希（`certutil -hashfile ORI-<版本>-win64.zip SHA256`），或改用源码方式运行

## 隐私声明

- 云端识别引擎：录音音频与识别结果会发送到您所配置的云端厂商服务（阿里云 / 腾讯云 / 讯飞 / 火山引擎），请自行查阅各厂商隐私政策

- 本地 FunASR 引擎：音频与识别全程在本机完成，不联网上传

- AI 修正 / AI 命令：识别文本会发送到您所配置的 LLM 接口（OpenAI 兼容），请自行评估

- 实时语音对话：豆包 / 阿里云 Qwen 的语音与文本发送到对应云端厂商；Hermes 发送到您自建的服务器（识别仍走您配置的识别引擎）

- 历史记录、自定义短语、提示词版本等数据仅保存在本机状态目录（`~/.asr_voice/` 或 exe 目录 `.asr_voice/`）

## 联系方式

[![哔哩哔哩](https://img.shields.io/badge/哔哩哔哩-空间动态-00A1D6?logo=bilibili\&logoColor=white\&style=for-the-badge)](https://space.bilibili.com/3492318/dynamic)

## 赞助支持

| 支付宝                                                           | 微信                                                         |
| ------------------------------------------------------------- | ---------------------------------------------------------- |
| <img src="docs/alipay.jpg" alt="alipay" style="zoom: 33%;" /> | <img src="docs/wxpay.png" alt="wxpay" style="zoom:50%;" /> |

## License 与免责声明

本项目以 [Apache License 2.0](LICENSE) 开源。

- 云端识别引擎（阿里云百炼 / 腾讯云 / 讯飞 / 火山引擎豆包）均为各厂商官方服务，使用前需自备密钥并按各厂商计费规则**自费开通**，产生的费用与合规责任由使用者自行承担。

- 本项目通过官方接口调用上述服务；厂商接口变动可能导致对应引擎暂时不可用，请以各厂商最新文档为准。

- 商店版与本地自行构建的打包版内置[阿里巴巴普惠体 3.0](https://www.iconfont.cn/fonts/detail?cnid=adI1E7HF7yme)（55 Regular / 65 Medium / 85 Bold 三个字重）作为默认界面字体；GitHub Release 的 CI 产物**不内置字体**（`fonts/` 目录不入仓），界面自动回退系统字体。该字体由阿里巴巴集团发布并保留版权，面向全社会**永久免费商用**（个人与企业均可，无需署名）；本项目仅原样内置分发官方字体文件，未做修改、转换或再分发授权。授权要点与官方声明链接见 [THIRD\_PARTY\_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## Code signing policy

GitHub Releases 的 Windows 构建由 SignPath 开源社区提供的免费代码签名服务签署：

> Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org)

签名角色（个人项目，三种角色为同一人）：

- Authors / Reviewers / Approvers：[@LangeHeris](https://github.com/LangeHeris)

隐私政策：[docs/PRIVACY_POLICY.md](docs/PRIVACY_POLICY.md)

> 注：SignPath 审核通过前，Release 暂为**未签名**包（可用 `SHA256SUMS.txt` 核对哈希，方法见[下载途径](#下载途径)）。

  
