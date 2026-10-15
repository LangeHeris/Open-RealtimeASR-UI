# 第三方组件许可清单（THIRD PARTY NOTICES）

本项目本身以 [Apache License 2.0](LICENSE) 开源。下列第三方组件的版权归其各自所有者，本文件按各许可证要求列明出处与许可。

## 运行时依赖（源码版 pip 安装）

| 组件 | 许可证 | 说明 |
| --- | --- | --- |
| CPython | PSF License | Python 解释器（打包版随附） |
| PySide6 / Shiboken6 | LGPL-3.0（或 GPL-3.0 / 商业授权） | Qt for Python；LGPL 下分发需保持可重新链接能力并附许可文本 |
| sounddevice | MIT | 音频采集 |
| PortAudio | MIT 类许可 | sounddevice 底层音频库 |
| CFFI / pycparser | MIT / BSD | sounddevice 依赖 |
| numpy | BSD-3-Clause | 音频数值计算 |
| keyboard | MIT | 全局热键 |
| pyperclip | BSD | 剪贴板读写 |
| websockets | BSD-3-Clause | 云端 ASR WebSocket 客户端 |
| PyYAML | MIT | 配置解析 |
| pywinrt（winrt-*） | MIT | 暂停播放：Windows 媒体会话（SMTC）控制 |
| pycaw | MIT | 全局静音：逐应用音量（WASAPI）控制 |
| comtypes | MIT | pycaw 的 COM 绑定依赖 |

## 本地 FunASR 引擎相关（可选安装，用户自行 `pip install`）

| 组件 | 许可证 | 说明 |
| --- | --- | --- |
| **FunASR 工具包**（funasr 包） | **MIT** | 代码部分；仓库 https://github.com/modelscope/FunASR 的 LICENSE |
| **paraformer-zh-streaming 模型权重** | **Apache License 2.0** | 权重单独授权，非 funasr 包代码的一部分；本项目不分发权重，用户自行下载。许可取自模型目录 `configuration.json` 的 `license: Apache License 2.0` 字段 |
| ModelScope | Apache-2.0 | 模型下载框架（funasr 依赖） |
| PyTorch / torchaudio | BSD-3-Clause | 推理运行时 |



## 打包发行版（随包内置）

发行 depot 内置便携 CPython 运行时与 venv 依赖环境。上表「运行时依赖」一节的全部组件（sounddevice、keyboard、pyperclip、websockets、PyYAML、numpy、pywinrt、pycaw、comtypes 等）同样随包分发，下表只列发行版新增或与上一节口径不同的条目：

| 组件 | 许可证 | 说明 |
| --- | --- | --- |
| CPython（python.org 安装器便携化） | PSF License | 运行时本体（venv 基底）。python.org 官方安装器产物；如需 Authenticode 签名，构建时自行对 launcher.exe 签名 |
| **PySide6 / Shiboken6** | **LGPL-3.0（或 GPL-3.0 / 商业授权）** | Qt for Python，发行版 UI 随包分发。**LGPL 下分发须保持可重新链接能力并附许可全文**：depot 以源码 + venv 形态分发（`app\asr_voice`，依赖装在 `env\`），用户可替换 Qt 库重新链接；**许可全文随包另附于 `licenses\LGPL-3.0.txt`（LGPL-3.0 以 GPL-3.0 为基础，故同时附 `licenses\GPL-3.0.txt`）**。上游 wheel（`pyside6_essentials-*.dist-info\licenses\`）只带 `LicenseRef-Qt-Commercial.txt`，不含 LGPL 文本，故由本项目补上 |
| FunASR 工具包（funasr 包） | MIT | 代码部分；发行版默认本地引擎（仅中文） |
| paraformer-zh-streaming 模型权重 | **Apache License 2.0** | 权重单独授权，非 funasr 包代码的一部分；发行版随包预下载（区别于开源版的「用户自行下载」）。许可取自模型目录 `configuration.json` 的 `license: Apache License 2.0` 字段，原始出处：ModelScope `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online` |
| ModelScope | Apache-2.0 | 构建期模型下载框架（随包内置） |
| PyTorch / torchaudio | BSD-3-Clause | 推理运行时；显卡加速 DLC 为 CUDA 构建版 |
| NVIDIA CUDA 运行时库（cuBLAS / cuDNN / cuFFT 等） | NVIDIA CUDA Toolkit 再分发条款 | **仅显卡加速 DLC**：torch `2.12.1+cu130` 构建把 CUDA 运行时 DLL 打包在 `env_gpu\Lib\site-packages\torch\lib\` 内随包分发。属 NVIDIA 再分发许可范围，但**不等于授权使用 CUDA 商标**，也不含 NVIDIA 驱动 |

> 已结项（原 TODO）：模型权重许可已复核——`models\paraformer-zh-streaming\configuration.json` 的 `license` 字段为 **Apache License 2.0**（funasr 代码本身是 MIT，权重另行授权）。若后续新增或换模型，须重新读取该模型目录的 `configuration.json` 并同步本表。

## 界面图标（Lucide，随包内置）

| 项目 | 说明 |
| --- | --- |
| 图标来源 | Lucide v1.55.0（<https://lucide.dev>） |
| 许可证 | **ISC License**（许可全文随包：`licenses\Lucide-ISC.txt`）；其中 `type.svg` 派生自 Feather 项目，另适用 **MIT License**（同一文件内附全文） |
| 改动范围 | **形状未做任何改动**：仅把 `currentColor` 替换为白色后按图标最终尺寸（20px / 40px@2x）光栅化为同目录 `*.png`，运行期再单色着色（跟随应用主题强调色）。 |
| 商用与归属 | ISC/MIT 均允许免费商用与再分发，**不强制要求归属声明**；此处主动声明以致谢上游 |

## 字体（打包版随附，仓库源码不含）

| 组件 | 许可 | 说明 |
| --- | --- | --- |
| 阿里巴巴普惠体 3.0 | [阿里巴巴普惠体 3.0 版法律声明](https://www.alibabafonts.com/#/font)（**全文随包：`fonts\AlibabaPuHuiTi-3-LICENSE.txt`**） | 免费商用，但须保留法律声明、禁止单独售卖/转授权/拆分破解 |
| 缝合像素字体 Fusion Pixel Font（10px 点阵规格） | [SIL Open Font License 1.1](https://github.com/TakWolf/fusion-pixel-font/blob/master/LICENSE-OFL)（全文随包：`fonts/FusionPixel-LICENSE-OFL.txt`） | 像素主题专用，**仅用于气泡正文**（`fonts/FusionPixel10px-zh_hans.ttf`，2× = 20px）；右键菜单沿用应用常规字体。个人/企业均可免费商用、可自由传播、可与本程序捆绑再分发；保留字体名称「缝合像素 / Fusion Pixel」，禁止单独售卖字体文件。字形来自 方舟像素字体（OFL-1.1）、Misaki、MisekiBitmap、BoutiqueBitmap7x7/9x9、Cubic 11、Galmuri 等开源点阵字体 |



## 随包许可全文索引

发行 depot 根目录下这些文件是**许可全文**（不是清单），按对应许可证要求随包分发：

| 路径 | 内容 |
| --- | --- |
| `LICENSE` | 本项目许可证（Apache License 2.0 全文） |
| `NOTICE` | 本项目版权声明 + 随包上游组件的 NOTICE 原文（torch、requests） |
| `THIRD_PARTY_NOTICES.md` | 本文件：第三方组件清单 |
| `licenses\LGPL-3.0.txt` | GNU Lesser General Public License v3 全文（PySide6 / Shiboken6） |
| `licenses\GPL-3.0.txt` | GNU General Public License v3 全文（LGPL-3.0 以其为基础） |
| `fonts\AlibabaPuHuiTi-3-LICENSE.txt` | 《阿里巴巴普惠体 3.0 版》法律声明全文 |
| `fonts\FusionPixel-LICENSE-OFL.txt` | SIL Open Font License 1.1 全文（Fusion Pixel Font） |

其余 MIT / BSD / Apache-2.0 组件的许可全文随各自的 wheel 元数据分发在
`env\Lib\site-packages\<包>.dist-info\licenses\`（`env_gpu` 同理），不另在根目录重复。

