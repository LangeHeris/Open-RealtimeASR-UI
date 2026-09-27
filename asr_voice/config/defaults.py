"""默认配置。用户配置（config.yaml）会与此合并。"""

DEFAULTS = {
    "engine": "aliyun",   # "aliyun" 阿里云百炼（默认）
                            # | "volcengine" 火山引擎豆包流式 | "tencent" 腾讯云实时
                            # | "xfyun" 讯飞实时转写 | "funasr" 本地 FunASR（需另行安装 funasr+torch）
                            # 注意别把「模型」与「引擎」记混：默认模型是下方 aliyun.model
                            # 的通用引擎 paraformer-realtime-v2；大模型版
                            # qwen-audio-3.0-asr-flash-streaming 要在右键菜单「大模型」档切换，并非默认值。
    "tencent": {
        "secret_id": "",
        "secret_key": "",
        "app_id": "",
        "engine_model_type": "Hy-ASR-3.0-preview",   # Hy-ASR 3.0（大模型·中英+方言）
        "filter_punc": 1,
        "convert_num_mode": 1,
        "filter_dirty": 0,
        "filter_modal": 2,                  # 严格过滤全部语气词（嗯/啊/吧等）
        "hotword_id": "",
        "customization_id": "",
        # 右键菜单三档分类（大模型2.0/大模型1.0/通用引擎）各自实际调用的
        # 模型，设置页「引擎参数」下拉可改；候选见 ui/presets.py 三档定义
        "menu_model_large_2_0": "16k_zh_en_2.0",
        "menu_model_large_1_0": "16k_zh_en",
        "menu_model_general": "16k_zh",
    },
    "funasr": {
        "model": "paraformer-zh-streaming",  # 流式模型，支持逐字同步上屏
        # device 默认 auto：有 N 卡走 cuda、否则回落 cpu。旧默认值是写死的 cuda，
        # 但没有 cuda 的环境里 resolve 逻辑（_resolve_device 遇到非 auto 一律照用）
        # 不会兜底，纯 CPU 机器上直接加载失败——与「CPU / GPU 均可」的口径不符。
        "device": "auto",                    # "auto" | "cuda" | "cpu"
        # 识别窗口预设："默认"(600ms) | "低延迟"(480ms) | "高准确"(720ms)
        "chunk_preset": "默认",
        "hotword": "",                       # 热词，空格分隔
        "preheat": True,                     # 启动时后台预热模型
        "local_dir": "",                     # 本地模型目录（用户自行下载权重后填绝对路径；非空时优先本地加载，不联网下载）
        "model_hub": "ms",                   # 下载渠道："ms"=ModelScope | "hf"=HuggingFace（仅自动下载时生效）
        "model_revision": "",                # 锁定模型版本（如 HF 的 apache-2.0-20260804）；留空用最新
    },
    "aliyun": {
        # 百炼 API Key，从 https://bailian.console.aliyun.com 获取（sk- 开头）
        "api_key": "",
        "model": "paraformer-realtime-v2",  # 中文（含方言）+多语种；右键「大模型」档用 qwen-audio-3.0-asr-flash-streaming
        "disfluency_removal": True,          # 过滤语气词（去"啊/嗯"等口癖）
        "max_sentence_silence": 800,         # 断句静音阈值 ms，200~6000
        "vocabulary_id": "",                 # 热词列表 ID（可选，百炼控制台创建）
        "language_hints": "",                # 语种提示，如 "zh"；留空自动检测
    },
    "xfyun": {
        # 大模型版（rtasr_llm）密钥：console.xfyun.cn/services/new_rta
        # 开通"实时语音转写大模型"后领取（AppID / APIKey / APISecret）
        "app_id": "",
        "api_key": "",        # APIKey：作为 accessKeyId 参与握手
        "api_secret": "",     # APISecret：HmacSHA1 签名密钥
        # 标准版（rtasr）密钥：console.xfyun.cn/services/rtasr
        # 开通"实时语音转写"（标准版）后领取，与大模型版互不通用
        "std_app_id": "",
        "std_api_key": "",    # APIKey：MD5(appid+ts) 的 HmacSHA1 签名密钥
"edition": "llm",       # llm=大模型版 rtasr_llm | std=标准版 rtasr
        "lang": "autodialect",  # 大模型版：autodialect=中英+202方言 | autominor=37语种（需工单开通）
        "std_lang": "cn",       # 标准版：cn=中文/中英混合 | en=英文
        # 标准版专用参数（大模型版不支持，不发送）：
        "pd": "",               # 垂直领域：court/edu/finance/medical/tech；留空通用
        "std_punc": True,       # 过滤标点（punc=0）
        # 语气词过滤（wp=s 顺滑语气词，客户端丢弃）：两版各自独立
        "filter_modal": True,   # 大模型版
        "std_filter_modal": True,  # 标准版
    },
    "volcengine": {
        # 火山引擎语音控制台 console.volcengine.com/speech 开通
        # "豆包流式语音识别"，支持两种鉴权（新版与旧版密钥分开设置）：
        #   新版单密钥：填 api_key（控制台"API Key 管理"生成的 API Key）
        #   旧版双密钥：填 app_id + access_token（应用详情页的 App ID + Access Token）
        "api_key": "",       # 新版 API Key（X-Api-Key）
        "access_token": "",  # 旧版 Access Token（X-Api-Access-Key）
        "app_id": "",        # 旧版鉴权的 App ID（X-Api-App-Key）
        "bigasr_billing": "duration",   # 豆包1.0 计费方式：duration=按时长 | concurrent=按并发
        "seedasr_billing": "duration",  # 豆包2.0 计费方式：duration=按时长 | concurrent=按并发
        # 当前版本生效的完整资源 ID，由程序自动合成：volc.{当前版本}.sauc.{该版本计费}，
        # 一般无需手工修改（设置界面分别设置两版本的计费方式）
        "resource_id": "volc.seedasr.sauc.duration",
        "enable_ddc": True,        # 语义顺滑（去语气词/重复词）
        "enable_nonstream": True,  # 二遍识别：静音约800ms出更准的 final
    },
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
        "block_size": 1600,
        "dtype": "int16",
        "warmup_capture": False,  # 麦克风常驻预热：热键按下立即有音频，防吞首字（任务栏麦克风图标会常亮）
        "input_device": "",       # 麦克风设备名（sounddevice 按子串匹配）；空=系统默认输入设备
    },
    "vad": {
        "energy_threshold": 500,
        "silence_duration_ms": 600,
        "min_speech_ms": 300,
        "pre_speech_buffer_ms": 200,
    },
    "hotkey": {
        "toggle": "ctrl+g",    # 开始/停止录音热键（默认避开系统 Win 组合，减少冲突）
        "dialog": "ctrl+shift+h",   # 进入/退出语音对话热键；空=只不注册该热键（整体关闭用 dialog.enable）
        # 长按说话（默认关；关闭时全链路行为与既有版本逐字一致）
        "hold_to_talk": False,      # 开=按住热键说话、松手结束；短于阈值的轻点完全不算录音
        "hold_threshold_ms": 300,   # 按下多久才算正式生效；轻点丢弃（也是防误触的省钱闸门）
        "hold_poll_ms": 20,         # 松手判定采样间隔；越小越灵敏，CPU 开销略增
    },
    "ui": {
        "bar_width": 326,
        "bar_height": 78,
        # 默认尺寸跟随当前窗口大小（除极简主题外：极简为正方形，
        # 边长由 floating_bar 的 VOICE_SIDE / VOICE_MIN_SIDE 决定）
        "follow_cursor": False,
        "default_x": -1,   # 首次运行默认位置（-1=主屏正中心；设 0+ 用固定坐标）
        "default_y": -1,
        "bg_color": "#1c1c20",          # 深色主题底色（与 theme_name 配套）
        "text_color": "#eeeeee",
        "accent_color": "#5696e8",
        # 主题预设内部键（light/dark/minimal；扩展主题见 themes/ 主题包的 key 字段；
        # 显示名由 theme_registry 提供，不再是这里的中文值）
        "theme_name": "minimal",
        # 界面语言：""=首次运行按系统语言自动检测；"zh"|"en" 手动固定（无热切换，重启生效）
        "language": "",
        "auto_hide_seconds": 0,          # 空闲多久自动隐藏到托盘（0=不自动隐藏）
        "always_on_top": True,           # 永远置顶（False=允许其他窗口遮挡悬浮条）
        # 设置界面主题内部键（minimal_dark/fluent_light）；显示名走 set.style.* 词条。
        # 老配置里的中文旧值由 loader 一次性迁移（同 ui.theme_name 的做法）
        "settings_style": "minimal_dark",
        "engine_order": "aliyun,volcengine,xfyun,tencent,funasr",  # 识别引擎菜单分组顺序
        "bar_opacity": 0,                # 悬浮条不透明度（0=主题默认；1~100=百分比，100=完全不透明）
        "interrupt_on_click": False,     # 鼠标左键点击悬浮条任意位置时打断当前录音
        "interrupt_on_delete": True,     # 录音中按删除键（Backspace/Delete）打断录音
        "always_admin": False,           # 永久为管理员启动：每次启动自动请求 UAC 提权（拒绝则以普通权限继续；设置页「程序行为」可切换）
        # 各提供商是否在右键"识别引擎"菜单里显示（False=隐藏该提供商整组）
        "engine_show_tencent": True,
        "engine_show_aliyun": True,
        "engine_show_xfyun": True,
        "engine_show_volcengine": True,
        "engine_show_funasr": True,
        # 逐模型显隐：不出现在右键"识别引擎"菜单里的引擎 ID（逗号分隔，空=全部显示）
        "engine_hidden_models": "",
        # 字体：用户配置优先，都未配置用内置默认（优先级从高到低）
        # 1) font_file: 任意 .ttf / .otf 路径（相对路径基于程序目录）
        # 2) font_family: 系统已装字体的 family 名（如 "Microsoft YaHei UI"）
        # 3) 都不填: 内置普惠体 3.0 Medium（fonts/ 目录，文件缺失退回 Qt 默认）
        "font_file": "",
        "font_family": "",
        "font_size_bar": 13,
        "font_size_app": 9,
    },
    "injection": {
        "method": "simulate_keys",
        "paste_delay_ms": 50,
        "keep_clipboard": False,    # 保留剪贴：识别结果留在剪贴板（占用剪贴板，会覆盖图片/文件；Ctrl+V 可随时重复粘贴）
        "game_mode": False,         # 游戏模式：强制剪贴板粘贴注入（不降级逐字、粘贴后不恢复剪贴板）；用于逐字输入无效的游戏等自绘输入框
        "replace_intermediate": True,
    },
    "llm": {
        "enable": False,            # LLM 文本修正开关（默认关，opt-in）
        "base_url": "https://api.deepseek.com/v1",  # OpenAI 兼容接口地址（DeepSeek 默认；OpenAI/Moonshot/智谱/本地 vLLM/Ollama 改此项）
        "api_key": "",              # API Key（Bearer 认证）
        "model": "deepseek-flash",  # DeepSeek Flash（便宜快，支持关闭思考）
        "timeout_ms": 3000,         # 超时直接用原文，不阻塞
        "prompt": "",               # 修正 prompt；留空按界面语言用内置模板（zh/en）
        "disable_thinking": True,   # 关闭思考/推理：请求附加禁用思考参数（能关的模型生效）
        "batch_sentences": 3,       # 累积 3 句修正一次（省 API 调用、上下文更足）
        "batch_idle_ms": 1000,      # 累积模式下距上句静音超过 1 秒也触发修正
        "wait_for_correction": False,  # True=「修正后上屏」：final 不立即上屏，等修正完成直接上屏修正版
                                        # （无视觉跳动、从根本消除吞句；代价是每句延迟 1-3 秒）
        "history_context": False,     # 修正时附带最近 15 条历史记录作为上下文一起发送给 AI
                                        # （帮助理解语境/专有名词；仅随待修正文本一起送出，历史本身不修正）
    },
    "history": {
        "enable": True,             # 历史对话剪贴板：记录识别结果，右键"历史记录"菜单点击复制
    },
    "commands": {
        # 语音命令模式：录音中句首说触发词进入命令态（该句不上屏）
        "enable": False,            # 总开关（默认关；开=右键菜单「注入逻辑 ▸ 语音命令」按需启用）
        "prefix": "听我说",       # 触发词：final 句首命中才有效；单独说进入待命态
        "hotkey": "ctrl+shift+g",   # 命令待命热键：按下即开始录音并进入待命态（免说触发词）；空=未启用
        "ai_prefix": "帮我",        # AI 命令显式前缀：「触发词 帮我润色」走 LLM 处理选中文本
        "ai_thinking": True,        # 「帮我」AI 命令思考模式（默认开，与 AI 修正的关思考开关独立）
        "ai_prompt_template": "用户选中了一段文本，并对它下了一条语音指令。按指令处理选中的文本，直接输出处理结果，不要输出任何解释、开场白或引号。\n\n选中的文本：\n{selection}\n\n语音指令：{command}",  # 「帮我」AI 提示词模板：空=内置默认透传模板；非空按 {selection}/{command} 占位符替换
        "phrases_enable": False,    # 自定义短语（需先开语音命令）：触发词+短语名 -> 插入预设文本，
                                    # 词表存状态目录 phrases.json（打包版 exe 同目录，源码版 ~/.asr_voice；设置界面编辑）
        "standby_seconds": 8,        # 待命态超时：单独说触发词后等待下一句命令的秒数
    },
    "recording": {
        "mode": "realtime",         # "realtime" 实时流式 | "sentence" 一句话识别
        "auto_stop_seconds": 10,    # 无语音超时自动停止，0=不自动停止
        "max_session_seconds": 60,  # 单次录音硬超时（秒），兜底防底噪贴阈值停不下来；0=不限时
        # 逐字同步：中间(未断句)结果实时写入输入框，新增字符增量追加、
        # ASR 回头修正才退格重打（微软/讯飞语音输入行为）。
        # False=仅断句(final)一次性上屏。
        # 逻辑锁：llm.enable=true（AI 修正关闭后恢复）、
        # injection.method=clipboard_paste（切回逐字输入后恢复）、
        # 语音命令或游戏模式启用（对应关闭后恢复）时强制视为关闭
        "live_intermediate": True,
        "pause_background_audio": False,  # 暂停播放：录音时真暂停后台媒体，停止后自动恢复（右键菜单「程序行为」可切换；默认关）
        "mute_background_audio": False,   # 全局静音：录音时静音全部后台声音（含游戏），停止后恢复；可单独使用或与暂停播放叠加
        "mute_only_game_mode": False,     # 仅游戏模式下静音：开启后全局静音只在游戏模式也开着时执行（避免日常录音误静音音乐/视频）
    },
    "dialog": {
        # 总开关：关闭后右键菜单「语音模式」项、对话热键与悬浮条对话入口
        # 全部下线，仅设置页保留以便重开（判定见 loader.dialog_enabled）
        # 默认关（opt-in）：与 config.example.yaml 保持一致。关掉后右键菜单
        # 「语音模式」项、对话热键与悬浮条对话入口全部下线，仅设置页保留可重开。
        "enable": False,

        # 服务提供商：doubao（豆包 S2S，默认，向后兼容）| aliyun（阿里云 Qwen-Audio Realtime）
        # | hermes（自建 hermes-agent API server，纯文本回复）
        # 设置页「语音对话」provider 下拉切换；三家共用 enable/output_device/half_duplex/auto_reconnect
        "provider": "doubao",

        # 实时语音对话（豆包端到端 S2S）。密钥：留空自动回退复用 volcengine 节
        # —— 已填的火山新版 API Key 开箱可用，不用填两遍
        "api_key": "",
        "app_id": "",
        "access_token": "",

        # 模型与人设（bot_name/system_role/speaking_style 仅 O 版本生效）
        "model": "1.2.1.1",          # 1.2.1.1=O2.0 通用对话 | 2.2.0.0=SC2.0 角色扮演
        "bot_name": "小爱",           # ≤20 字符
        "system_role": "",
        "speaking_style": "",
        "character_manifest": "",    # 仅 SC 版本生效（与上面三个互斥）

        # 音色与播报
        "speaker": "zh_female_vv_jupiter_bigtts",
        "speech_rate": 0,            # [-50,100]，仅 2.0 版本生效
        "loudness_rate": 0,          # [-50,100]，仅 2.0 版本生效
        "output_device": "",         # 播放设备名（子串匹配）；空=系统默认输出

        # 收音与判停
        "end_smooth_window_ms": 800,  # [500,50000]，越小打断越快、越易把停顿误判成说完
        "half_duplex": False,         # 半双工闸门：AI 播报时暂停上行（外放防自我打断）

        # 会话行为
        "enable_user_query_exit": True,   # 说"退出/再见"自动挂断（359 带 status_code=20000002）
        "keep_context": True,             # dialog_id 跨进程续接（服务端留最近 20 轮）
        "auto_reconnect": True,           # 45000003/5xx 自动重建会话（每轮最多 1 次）
        "strict_audit": True,             # 安全审核等级（文档默认 true）

        # 阿里云 Qwen-Audio Realtime 专属（provider=aliyun 时生效）。api_key 留空
        # 回退复用 aliyun.api_key（同豆包回退 volcengine 模式，已填的百炼 Key 开箱可用）
        "qwen": {
            "region": "legacy",           # legacy(旧域名) | beijing | singapore（新域名，workspace_id 必填）
            "workspace_id": "",           # 新域名必填；legacy 留空
            "api_key": "",                # 留空回退复用 aliyun.api_key
            "model": "qwen-audio-3.0-realtime-plus",   # plus | flash
            "voice": "longanqian",        # longanqian/longanlingxin/longanlingxi/longanxiaoxin/longanlufeng
            "instructions": "",           # 系统指令（人设/风格，对应豆包 system_role）
            "turn_detection": "server_vad",   # server_vad | smart_turn
            "vad_threshold": 0.5,         # [-1.0,1.0]，仅 server_vad
            "silence_duration_ms": 800,   # [200,6000]，仅 server_vad
            "enable_speech_emotion": True,
            "max_history_turns": 20,      # [1,50]，单会话内历史轮数
            "enable_search": False,       # 联网搜索，仅 plus/flash 生效
        },
        # Hermes Agent（provider=hermes 时生效）：用户自建的 hermes-agent
        # API server（OpenAI 兼容 HTTP）。它只提供文本，不提供任何音频
        # （capabilities 实测 audio_api=false / realtime_voice=false），
        # 所以断句与识别都必须由本端补齐。
        "hermes": {
            "base_url": "",                  # 如 http://192.168.0.242:8642（不含 /v1）
            "api_key": "",                   # 即服务器 ~/.hermes/.env 的 API_SERVER_KEY
            "model": "hermes-agent",
            "session_title": "ORI 语音对话",
            # 识别错误有三个独立来源（引擎档位/口音/语言能力），后两者换引擎
            # 也解决不了。声明输入来源让 LLM 自己容错，比单开一层「AI 修正」
            # 省一次往返（实测单轮已 5.5~7.9 秒，再加一次不可接受）。
            "system_hint": "以下是用户的语音识别结果，可能有同音错别字或缺少标点，"
                           "请结合上下文理解后再回答。",
            "connect_timeout_ms": 5000,
            "turn_timeout_ms": 60000,        # agent 可能跑工具，给足上限
        },
        # 对话专属 ASR。两个字段全留空 = 跟随顶层 engine，行为与现在完全一致
        # （升级不改变既有体验）；显式填写才覆盖 —— 用于「听写用中文引擎 +
        # 对话用中英混说引擎」而不互相牺牲。见 spec §八。
        "asr": {
            "engine": "",                    # 留空 = 跟随 cfg.engine
            "model": "",                     # 留空 = 该引擎的默认档
            "lang": "",                      # 语种，如 "zh,en"
            "hotword": "",                   # 热词，留空 = 不传
        },
    },
    "steam": {
        # 云存档是否同步含密钥的完整配置（默认关 = 只同步脱敏那份）。
        # 开启后任何登录该 平台账号的设备都能拿到你的密钥，设置界面
        # 勾选处已明示风险；上传逻辑见云存档构建函数 build_cloud_files。
        "cloud_sync_keys": False,
        # 显卡加速：需已安装免费扩展包。下次启动生效
        # （launcher 读此键选环境），故改动不在当前进程里热切换。
        "use_gpu": False,
    },
    "logging": {
        "level": "INFO",
        "file": "logs/asr_voice.log",
        "max_size_mb": 5,
        "backup_count": 3,
        "retention_days": 30,
    },
}
