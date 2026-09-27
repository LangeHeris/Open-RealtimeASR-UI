# Open-RealtimeASR-UI User Manual

> [简体中文](User_Doc_zh.md) | **English**

## Contents

- [1. Introduction](#1-introduction)
  - [1.1 Software Overview](#11-software-overview)
  - [1.2 Supported Environments and Compatibility](#12-supported-environments-and-compatibility)
  - [1.3 Deployment Forms and Usage](#13-deployment-forms-and-usage)
  - [1.4 Feature Overview](#14-feature-overview)
- [2. Before You Begin](#2-before-you-begin)
  - [2.1 Runtime Environment Requirements](#21-runtime-environment-requirements)
  - [2.2 Preparation Before Use](#22-preparation-before-use)
  - [2.3 Risk Notices](#23-risk-notices)
- [3. Installation and First Use](#3-installation-and-first-use)
  - [3.1 Running from Source](#31-running-from-source)
  - [3.2 Running the Packaged Build (exe)](#32-running-the-packaged-build-exe)
  - [3.3 Installing the Local FunASR Engine (Optional)](#33-installing-the-local-funasr-engine-optional)
  - [3.4 First Launch and Initialization](#34-first-launch-and-initialization)
  - [3.5 Configuring Recognition Engine Keys](#35-configuring-recognition-engine-keys)
  - [3.6 Verifying Your First Recording](#36-verifying-your-first-recording)
- [4. Main Interface](#4-main-interface)
  - [4.1 Floating Bar Layout](#41-floating-bar-layout)
  - [4.2 Status Indicators](#42-status-indicators)
  - [4.3 Right-Click Menu](#43-right-click-menu)
  - [4.4 System Tray](#44-system-tray)
  - [4.5 Settings Overview](#45-settings-overview)
- [5. Basic Voice Input Usage](#5-basic-voice-input-usage)
  - [5.1 Starting and Stopping Recording](#51-starting-and-stopping-recording)
  - [5.2 Push-to-talk](#52-push-to-talk)
  - [5.3 Real-time Text Insertion and Live Typing](#53-real-time-text-insertion-and-live-typing)
  - [5.4 Text Injection Methods](#54-text-injection-methods)
  - [5.5 Interrupting and Canceling](#55-interrupting-and-canceling)
  - [5.6 VAD Voice Detection and Noise-floor Calibration](#56-vad-voice-detection-and-noise-floor-calibration)
  - [5.7 Always-on Mic Warm-up](#57-always-on-mic-warm-up)
  - [5.8 Pause Media and Global Mute](#58-pause-media-and-global-mute)
- [6. Recognition Engines](#6-recognition-engines)
  - [6.1 Engine Overview](#61-engine-overview)
  - [6.2 Engine Switching](#62-engine-switching)
  - [6.3 Alibaba Cloud Model Studio](#63-alibaba-cloud-model-studio)
  - [6.4 Tencent Cloud](#64-tencent-cloud)
  - [6.5 iFLYTEK](#65-iflytek)
  - [6.6 Volcengine Doubao](#66-volcengine-doubao)
  - [6.7 Local FunASR](#67-local-funasr)
- [7. AI Correction](#7-ai-correction)
  - [7.1 Overview](#71-overview)
  - [7.2 Enabling and Endpoint Setup](#72-enabling-and-endpoint-setup)
  - [7.3 The Two Typing Modes](#73-the-two-typing-modes)
  - [7.4 Batch Correction](#74-batch-correction)
  - [7.5 Custom Prompts](#75-custom-prompts)
  - [7.6 How to Confirm AI Correction Is Working](#76-how-to-confirm-ai-correction-is-working)
- [8. Voice Commands](#8-voice-commands)
  - [8.1 Overview](#81-overview)
  - [8.2 Trigger Methods](#82-trigger-methods)
  - [8.3 Local Command Reference](#83-local-command-reference)
  - [8.4 AI Commands](#84-ai-commands)
  - [8.5 Custom Phrases](#85-custom-phrases)
  - [8.6 Mutual Exclusion with Live Typing](#86-mutual-exclusion-with-live-typing)
- [9. Real-Time Voice Conversation](#9-real-time-voice-conversation)
  - [9.1 Overview](#91-overview)
  - [9.2 Activation and Keys](#92-activation-and-keys)
  - [9.3 Entering and Exiting](#93-entering-and-exiting)
  - [9.4 Conversation States and Interruption](#94-conversation-states-and-interruption)
  - [9.5 Model and Persona](#95-model-and-persona)
  - [9.6 Voice and Playback](#96-voice-and-playback)
  - [9.7 Session Behavior](#97-session-behavior)
  - [9.8 FAQ](#98-faq)
  - [9.9 Alibaba Cloud Qwen-Audio Realtime (Provider #2)](#99-alibaba-cloud-qwen-audio-realtime-provider-2)
  - [9.10 Hermes Agent (Provider #3)](#910-hermes-agent-provider-3)
- [10. Settings Reference](#10-settings-reference)
  - [10.1 General](#101-general)
  - [10.2 Engine Keys](#102-engine-keys)
  - [10.3 Engine Parameters](#103-engine-parameters)
  - [10.4 AI Correction](#104-ai-correction)
  - [10.5 Voice Commands](#105-voice-commands)
  - [10.6 Recording](#106-recording)
  - [10.7 Voice Mode](#107-voice-mode)
  - [10.8 About](#108-about)
- [11. Themes and Personalization](#11-themes-and-personalization)
  - [11.1 Built-in Themes](#111-built-in-themes)
  - [11.2 Extension Theme Packs](#112-extension-theme-packs)
  - [11.3 Font Settings](#113-font-settings)
  - [11.4 Floating Bar Appearance](#114-floating-bar-appearance)
- [12. Configuration Reference](#12-configuration-reference)
  - [12.1 Config File Lookup Order](#121-config-file-lookup-order)
  - [12.2 Common Config Fields](#122-common-config-fields)
- [13. FAQ and Troubleshooting](#13-faq-and-troubleshooting)
  - [13.1 The app won't start](#131-the-app-wont-start)
  - [13.2 Hotkeys don't work or conflict](#132-hotkeys-dont-work-or-conflict)
  - [13.3 Recognition produces no text output](#133-recognition-produces-no-text-output)
  - [13.4 The first word is swallowed / words are missing](#134-the-first-word-is-swallowed--words-are-missing)
  - [13.5 AI correction doesn't respond](#135-ai-correction-doesnt-respond)
  - [13.6 Voice commands don't work](#136-voice-commands-dont-work)
  - [13.7 The clipboard gets overwritten](#137-the-clipboard-gets-overwritten)
  - [13.8 FunASR-related issues](#138-funasr-related-issues)
  - [13.9 Text cannot be typed in games](#139-text-cannot-be-typed-in-games)
- [14. Appendix](#14-appendix)
  - [14.1 Glossary of common terms](#141-glossary-of-common-terms)
  - [14.2 Quick reference of default values](#142-quick-reference-of-default-values)
  - [14.3 Exit codes](#143-exit-codes)
  - [14.4 Reference links](#144-reference-links)

## 1. Introduction

### 1.1 Software Overview

> **Open-RealtimeASR-UI (ORI for short) is a Windows desktop voice dictation tool for Chinese-speaking users, positioned as an open-source alternative to the native Windows 11 `Win+H` voice input**: press a global hotkey to start talking, your speech is transcribed into text in real time by a cloud or local recognition engine, and the text is injected directly at the current cursor position.

- **Five recognition engines, hot-swappable via the right-click menu** — four cloud providers (Alibaba Cloud Model Studio / Tencent Cloud / iFLYTEK / Volcengine Doubao) with bring-your-own keys + fully offline local FunASR; switching requires no restart;
- **Every stage of the dictation chain is adjustable** — injection method, sentence-segmentation pacing, live typing, interrupt method, and noise-floor calibration are all configurable, with dedicated adaptations for scenarios such as games and windows running with administrator privileges;
- **AI capabilities** — AI correction, voice commands, and end-to-end voice conversation are all optional features, off by default; once enabled, they connect only to the services you configure yourself.

### 1.2 Supported Environments and Compatibility

| Item | Requirement |
| --- | --- |
| Operating system | Windows 10 / 11 (desktop) |
| Runtime | The packaged build (exe) runs directly; the source build requires Python 3.10+ |
| Audio input | Any usable microphone (a headset microphone is recommended to block out speaker interference) |
| Network | Cloud engines require access to the corresponding provider's services; the local FunASR engine is fully offline |

Notes:

- Engine availability depends on **API key configuration** and **network connectivity**; engines without configured keys are hidden from the menu by default
- The packaged build (exe) **does not include the local FunASR engine** (funasr + torch are too large to bundle); install it yourself if you need offline recognition.

### 1.3 Deployment Forms and Usage

ORI can be launched in two ways:

- **Packaged build (zip)**: download the Release zip and extract it; double-click `ORI.exe` (keep the `_internal\` folder next to it).
- **Source build**: run `pip install -r requirements.txt`, then start via `python -m asr_voice.main` or `start.bat`.

### 1.4 Feature Overview

| Core feature | One-line description | Details |
| --- | --- | --- |
| Real-time dictation | Start/stop with a global hotkey, live typing, does not swallow the first word (warm-up + pre-roll buffer + connection-setup resend); VAD auto sentence segmentation and noise-floor calibration; Delete-key / left-click interrupt to take it back; optional Push-to-talk | Chapter 5 |
| Five recognition engines | Alibaba Cloud Model Studio (default) / Tencent Cloud / iFLYTEK / Volcengine Doubao — four cloud providers, bring your own keys + local FunASR offline; hot-switch via the right-click menu; per-slot model binding and menu visibility are configurable | Chapter 6 |
| Text injection | Dual channels of simulated keystrokes / clipboard paste, with automatic clipboard restore; game mode + elevation to break through UIPI; auto pause media / global mute while recording | §5.4, §5.8 |
| AI correction (optional) | Cleans up homophone typos, punctuation, and filler words; works with any OpenAI-compatible LLM; custom prompts and batch correction to save API calls | Chapter 7 |
| Voice commands (optional) | Triggered by "听我说" ("listen to me"): local commands with zero API calls, "帮我…" ("help me…") has the AI process the selected text, custom phrases | Chapter 8 |
| Real-time voice conversation (optional) | End-to-end speech large models (Doubao S2S / Alibaba Cloud Qwen-Audio / self-hosted Hermes), full-duplex barge-in interruption, bubble subtitles, dedicated recognition engine for dialogs | Chapter 9 |
| Interface and themes | A single always-on-top floating bar is the entire interface and never steals focus; 3 built-in themes + theme-pack extensions, custom fonts; history quick-pick from the right-click menu and a separate search window | Chapters 4, 11 |

## 2. Before You Begin

### 2.1 Runtime Environment Requirements

Before using ORI, it is recommended to confirm that your current environment meets the following basic conditions:

- The operating system is Windows 10 / 11 (desktop)
- A usable microphone device is available and system microphone permissions are granted
- When using cloud engines, you have network access to the corresponding provider's services (Alibaba Cloud / Tencent Cloud / iFLYTEK / Volcengine)
- The packaged build requires no Python environment; the source build requires Python 3.10+

The recommended usage environment is as follows:

- A quiet or moderately noisy environment; wear headphones while dictating to block out sound played through speakers and avoid misrecognition
- When using global hotkeys, avoid conflicts with global hotkeys of the system or other software
- When using LLM-dependent features such as AI correction and voice commands, you have network access to the corresponding API endpoints

Notes:

- Recognition accuracy, latency, and cost vary by engine; see Chapter 6 for details
- Some features can only be used after an API key is configured or the corresponding switch is turned on

### 2.2 Preparation Before Use

To ensure the installation and first use go smoothly, complete the following preparations before installing:

1. **Check the system and microphone**  
   Confirm your Windows version and microphone availability, and verify in System → Privacy → Microphone that desktop-app microphone access is turned on.
2. **Prepare recognition engine API keys (cloud engines)**  
   For cloud engines you must activate the corresponding service at your own expense in each provider's console and obtain the keys; **most providers offer some trial or free quota**:
   - Alibaba Cloud Model Studio: `api_key` (starts with sk-), obtained from the [Alibaba Cloud Model Studio console](https://bailian.console.aliyun.com)
   - Tencent Cloud: `secret_id` + `secret_key` + `app_id`, obtained from the [Tencent Cloud console](https://console.cloud.tencent.com/asr)
   - iFLYTEK: [iFLYTEK Open Platform - Real-time Speech Transcription LLM Edition](https://console.xfyun.cn/services/new_rta) `app_id + api_key + api_secret`; [iFLYTEK Open Platform - Real-time Speech Transcription Standard Edition](https://console.xfyun.cn/services/rta) `std_app_id + std_api_key` 
   - Volcengine: new edition `api_key`; legacy edition `app_id + access_token ` [Volcengine Ark console](https://console.volcengine.com/ark/region:cn-beijing/openManagement?advancedActiveKey=model&projectName=ark&tab=TTS)
3. **Prepare headphones (recommended)**  
   Playing audio through speakers creates echo interference with the microphone; it is recommended to use headphones to listen to device audio, but it is fine if you do not have any.
4. **Back up important configuration**  
   Before modifying `config.yaml`, back up the current configuration file so you can quickly restore it if problems arise.

### 2.3 Risk Notices

- **Cloud engines**: When using cloud engines such as Alibaba Cloud Model Studio / Tencent Cloud / iFLYTEK / Volcengine, fees are charged by the corresponding provider under its own billing rules; before use, confirm that the service is activated and that you understand the billing terms
- **Clipboard operations**: With "Keep clipboard" on, the recognition result overwrites images / files and other content on the clipboard; when it is off, the program tries to restore the original clipboard content, but images / files cannot be safely restored, so it automatically falls back to typing input; with "game mode" on, the clipboard is overwritten directly and the original content is not restored
- **Typing input simulates the keyboard**: The `simulate_keys` method simulates keyboard key presses, which may fail or be intercepted in some programs (such as games or apps protected by security software)
- **Global hotkeys**: ORI relies on global hotkey listening, which some security software may intercept; if a hotkey is already occupied by another program, you need to change it
- **Persistent microphone warm-up**: With `audio.warmup_capture` on, the program keeps the microphone occupied (the taskbar microphone icon stays lit), which may affect other recording apps on the system
- **AI correction / voice commands**: The recognized text is sent to the configured LLM service (OpenAI-compatible API); assess the privacy and compliance risks yourself

## 3. Installation and First Use

### 3.1 Running from Source

Steps:

1. Clone the repository and enter the project directory
2. Install dependencies: `pip install -r requirements.txt`
3. Launch the program: `python -m asr_voice.main`, or double-click `start.bat`

Notes:

- `start.bat` automatically looks for a Python interpreter in the order `python.local.bat` → `.venv\Scripts\python.exe` → `py -3` → `python`, and gives installation guidance if none is found
- `python.local.bat` lets you configure the absolute path to a local Python (gitignored, never committed)
- On first run, a configuration file is generated automatically at `config\config.yaml` (based on the `config\config.example.yaml` template)

### 3.2 Running the Packaged Build (exe)

Steps:

1. Download the Release zip (or build it yourself, see below) and extract it anywhere
2. Double-click `ORI.exe` inside the extracted folder (keep the `_internal\` folder next to it)
3. On first launch, `config.yaml` is generated automatically in the same directory as the exe (if the directory is not writable, it falls back to `~/.asr_voice\config.yaml`)

To build it yourself:

```bat
build.bat  
```

Note: the packaged build does not currently include the local FunASR engine

### 3.3 Installing the Local FunASR Engine (Optional)

```bash
# CPU
pip install funasr torch torchaudio

# GPU (CUDA 11.8 example)
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
pip install funasr
```

Notes:

- On first use, models are downloaded automatically from ModelScope to `~/.cache/modelscope/`
- The program warms up in the background as soon as it starts (about 3–8 seconds on CPU / about 1–3 seconds on GPU); the floating bar shows a notification when it is ready
- For offline use, download the `paraformer-zh-streaming` model in advance and place it in that cache directory
- The local engine is only available in the source build
- The repository does not bundle or distribute model weights — you need to download them yourself. Three ways:
  1. Auto download: leave `funasr.local_dir` empty, and the program downloads automatically from the selected channel on first use
  2. Local directory: after downloading the weights yourself, set `funasr.local_dir` to the absolute path of the weights directory (loaded directly from local disk, no network access)

### 3.4 First Launch and Initialization

On first launch, focus on confirming the following:

- The program starts normally and the floating bar displays properly (except when started with the silent-start parameter `--hidden`)
- On first run, `config.yaml` is generated automatically; see §12.1 for its location
- When no keys are configured, the program still starts normally and prompts you to configure a key
- The recording hotkey (default `Ctrl+G`) is registered successfully

After the first launch, you typically need to perform the following basic steps:

1. **Configure recognition engine keys**  
   The default engine is Alibaba Cloud Model Studio; fill in the key for at least one engine (or switch to local FunASR).

2. **Confirm the microphone device**  
   In Settings → Recording, select the correct microphone device (the system default input device is used by default).

3. **Verify a recording**  
   Press `Ctrl+G` and speak, press it again to stop, and confirm that the text is successfully injected into the target input box.

### 3.5 Configuring Recognition Engine Keys

> Cloud engine keys in ORI can be entered in two ways: through the Settings UI or by editing the configuration file directly.

**Method 1: Settings UI (recommended)**

1. Right-click the floating bar → Settings
2. Go to the "Engine Keys" page
3. Expand the corresponding engine card and fill in the key
4. Click Save

**Method 2: Edit config.yaml directly**

1. Click the "Open config file" pill button in the top-right corner of the Settings window (opens the file with the system default editor)
2. Fill in the key fields and save; the program hot-reloads the changes automatically

Notes:

- Engine groups without configured keys are hidden from the right-click menu by default (except the engine group currently in use)
- For each engine's key fields and how to obtain them, see §6.3–§6.6

### 3.6 Verifying Your First Recording

After the first successful configuration, it is recommended to verify the dictation workflow end to end once with the following steps:

1. Open any window that accepts text input (such as Notepad) and focus it
2. Press `Ctrl+G` to start recording; the floating bar appears and the status dot changes to the listening/recognizing state
3. Speak a few sentences at normal speed and watch the text preview on the floating bar and the text being typed into the input box in real time
4. Press `Ctrl+G` again to stop and confirm that the final text is fully injected
5. Check that the floating bar shows no error (orange status dot + error message)

If any problems occur during the process, see Chapter 13 (Troubleshooting).

## 4. Main Interface

### 4.1 Floating Bar Layout

The floating bar consists of:

| Area | Contents |
| --- | --- |
| Start/stop button | Round button on the left (▶/■); click to start/stop recording |
| Status dot | Idle = dim dot in the theme color; listening/recognizing = red dot with a breathing glow; error = orange |
| Text preview | Intermediate results are shown semi-transparently, final results at full brightness; also used for transient hints and error messages (errors stay for about 5 seconds) |
| Close button | ✕ on the right; click to minimize to the tray (the app keeps running in the background) |

Floating bar interactions:

- **Left-click drag**: move the window (persisted; kept across restarts)
- **Bottom-right drag handle**: resize the window (persisted)
- **Right-click**: open the full menu
- **Minimal theme**: square floating window + centered voiceprint waveform + bubble text above; the entire window acts as the start/stop button

### 4.2 Status Indicators

The floating bar reports the app state through the status dot and the text preview:

| State | Appearance |
| --- | --- |
| Idle | Status dot is a dim dot in the theme color |
| Listening / recognizing | Red dot with a breathing glow; the text preview scrolls intermediate results in real time |
| Error | Orange status dot; the text preview shows the error message for about 5 seconds |
| Transient hint | The text preview shows a short-lived hint (e.g. AI correction results, engine switched successfully), disappearing after 1–2 seconds |

With AI correction enabled, each corrected sentence shows a three-state hint (see §7.6).

### 4.3 Right-Click Menu

> The right-click menu is ORI's main control entry; the floating bar and the tray share the same menu.

Menu structure, top to bottom:

| Section | Contents |
| --- | --- |
| Recording | Start recording / Stop recording (renamed dynamically by the current state) |
| Voice chat | Checkable item: enter/exit the real-time voice conversation (governed by the `dialog.enable` master switch; this item is hidden when the switch is off — see §9.3) |
| Recognition engine | Engine list grouped in your custom order (Tencent Cloud 3 tiers / Alibaba Cloud 2 tiers / iFLYTEK 2 tiers / Volcengine 2 tiers / local FunASR; the actual model and visibility of each tier: see §6.2) |
| Theme | All available themes (built-in + theme packs) |
| Injection method | Clipboard paste / simulated keystrokes (mutually exclusive), Keep clipboard, Live typing, AI correction, Voice commands (checkable items) |
| History | Open window (full browsing, search, double-click to copy the whole record), Enable recording, recent entries (up to 15, click to copy), clear history |
| Font | Choose font file (.ttf/.otf), Reset to default font |
| Settings | Open the settings dialog |
| Behavior | Auto-hide / Always on top / Launch at startup / Pause media (pause background media playback while recording) / Run as administrator (one-off restart elevated and take over; grayed out when already running as administrator) (multi-select) |
| How to interrupt | Left-click interrupt / Delete-key interrupt (multi-select) |
| Other | Exit (quit the app completely) |

Notes:

- The engine menu's group order can be adjusted under Settings → General → Engine order
- Engine groups with no key configured are hidden by default; the current engine group is always visible

### 4.4 System Tray

| Action | Effect |
| --- | --- |
| Single-click the tray icon | Show the floating bar when hidden; toggle recording when visible |
| Double-click the tray icon | Show / hide the floating bar |
| Right-click the tray icon | Open the full menu (same as right-clicking the floating bar) |

### 4.5 Settings Overview

The settings dialog has 8 pages:

| Page | Contents |
| --- | --- |
| General | Hotkeys, output (paste delay), interface (auto-hide delay, opacity, settings theme), shared AI endpoint (API address, key, model, connectivity test), engine order |
| Engine Keys | Collapsible key cards for each cloud engine (Tencent / Alibaba / iFLYTEK LLM & Standard editions / Volcengine new & legacy); the current engine is expanded by default; local FunASR has no key and no card |
| Engine Parameters | Collapsible recognition-parameter cards per engine (Tencent tier models & filtering, Alibaba segmentation & filler words, iFLYTEK parameters for both editions, Volcengine billing & smoothing, FunASR model & device) |
| AI Correction | Enable switch, type after correction, include history context, request timeout, disable thinking mode, batch correction, custom prompt |
| Voice Commands | Trigger word, command hotkey, AI command prefix, AI command thinking mode, AI command prompt, custom phrase list, standby timeout, command reference |
| Recording | Recording (always-on mic warm-up, device selection, silence auto-stop, max recording length), Voice Activity Detection (VAD) (speech threshold, sentence-break wait, minimum speech) |
| Voice Mode | Dialog provider switch (Doubao / Alibaba Cloud Qwen / self-hosted Hermes), dialog hotkey, keys & test connection, model & persona, voice & playback, turn detection & half-duplex, session behavior, dialog-specific recognition (see Chapter 9) |
| About | About the app, open-source components, contact, logs & troubleshooting |

Details:

- The "Open config file" button in the title bar opens `config.yaml` with the system default editor; changes are hot-reloaded automatically after saving
- The "Restore defaults" button in the title bar resets `config.yaml` to factory defaults (history, custom phrases, and prompt versions are unaffected); after confirmation, the hot reload takes effect and the settings window closes
- Some settings (such as hotkeys and themes) only take effect after restarting the app; the UI shows corresponding hints
- Saving works by field-level line write-back (comments preserved), with read-back verification after writing and automatic rollback on failure

## 5. Basic Voice Input Usage

### 5.1 Starting and Stopping Recording

> Press the global hotkey (default `Ctrl+G`) to start recording, press it again to stop, and the final text is inserted at the current cursor position.

A complete dictation workflow:

1. Focus the target input location (any program that accepts text input)
2. Press `Ctrl+G` → the floating bar appears and recording starts
3. Speak at a normal pace → intermediate results are typed at the cursor in real time
4. Press `Ctrl+G` again (or let the silence timeout / maximum recording length trigger) → recording stops and the final text is inserted

Additional notes:

- The hotkey can be changed under Settings → General → Hotkeys (a restart is required); avoid conflicts with the system or other software
- Recording stops automatically after no speech for the configured number of seconds (default 10 s, configurable; 0 = never stop automatically)
- A single recording is force-wrapped after 60 seconds by default; change this under Settings → Recording → Max recording length (0 = no limit)
- If establishing the ASR connection fails momentarily, it retries automatically 3 times (at 2-second intervals)

### 5.2 Push-to-talk

Off by default. To enable: right-click the floating bar → Behavior → Hold to talk, or Settings → General → Hotkeys → Push-to-talk.

- **Recording**: hold the recording hotkey and speak; release to stop immediately and have the text typed. While the key is held,
  recording never stops automatically on silence (only the 60-second hard limit still applies).
- **Voice Mode**: the microphone opens only while the dialog hotkey is held, and closes on release. When the key is not held, the microphone transmits nothing at all
  (Hermes is the exception, see below), so when playing through speakers the AI's voice is not picked up by your own microphone,
  and you no longer need to enable "half-duplex". Pressing the hotkey while the AI is speaking interrupts it (Doubao / Alibaba Cloud Model Studio support interruption;
  Hermes "commits only after you finish speaking", so this does not apply).
- **A tap does not count as recording**: a press shorter than the threshold (default 300 ms, adjustable on the settings page) is
  discarded entirely — no server connection, nothing typed — so accidental touches incur no cost whatsoever.
- The floating bar shows "Preparing" → "Recording", which tells you whether the press registered. Built-in themes (Light/Dark/Minimal)
  express this with the status dot: dark red = preparing, bright red = recording.
- In a Hermes dialog, the result may take a few hundred milliseconds longer to appear after you release the key (local turn detection needs a bit of silence).
  Hermes recognition runs on your machine, so text is typed live as usual while the key is held; this sentence is simply not sent to the AI
  until after you release.
- With push-to-talk enabled, the "half-duplex" switch no longer takes effect (its role is already covered by the push-to-talk gate).

### 5.3 Real-time Text Insertion and Live Typing

> "The words appear as you speak them" is ORI's core experience: intermediate results are written into the input box in real time, new content is appended directly, and backspacing plus retyping happens only when ASR revises earlier text.

Related configuration items:

| Item | Default | Description |
| --- | --- | --- |
| `recording.live_intermediate` | `true` | Live typing: intermediate results are typed at the cursor in real time; `false` = text is inserted all at once after each sentence break |
| Live typing (right-click menu) | On | Checkbox, kept in sync with the config field |

Logic-lock note: **when AI correction is enabled, the injection method is clipboard paste, game mode is enabled, or voice commands are enabled, "Live typing" is forcibly treated as off** (it is restored automatically once the corresponding lock is released). Reasons:

- Clipboard paste overwrites the clipboard on every injection, so incremental appending of intermediate results is impossible
- AI correction needs to correct complete sentences in the background; typing intermediate results would cause visible jumping from backspacing and retyping
- Intermediate frames of a voice-command sentence may linger in the input box due to homophone misrecognition
- Game mode forces whole-segment paste injection, which is incompatible with incremental appending of intermediate results

### 5.4 Text Injection Methods

> ORI provides two ways to deliver recognition results into the target program; switch between them via "Injection method" in the right-click menu.

| Method | Config value | Characteristics |
| --- | --- | --- |
| Clipboard paste | `clipboard_paste` | The result is written to the clipboard and pasted with Ctrl+V — fast |
| Simulated keystrokes | `simulate_keys` | Simulates keyboard input character by character; compatible with programs that forbid pasting (default) |

Related behaviors:

- **Game mode**: after enabling it via the right-click menu "Injection method ▸ Game mode", recognition results are always injected through clipboard paste, with no fallback of any kind and no clipboard restoration after pasting — intended for games and other custom-drawn input boxes where simulated keystrokes do not work (such input boxes ignore synthetic keystrokes that carry no scan code). While it is on, the injection method is locked to clipboard paste and "Live typing" is locked off (the previous states are restored automatically when game mode is turned off); it has no effect on target programs that do not even support pasting. If the target game runs as administrator (common for clients with anti-cheat), this app must also run as administrator in order to inject text; otherwise Windows silently discards the synthetic keystrokes — when this is detected, the floating bar shows a hint automatically. When game mode is turned on while the app is not running as administrator, it also proactively asks whether to restart the app as administrator (choosing "Elevate now" brings up the UAC prompt; after confirmation the elevated instance takes over and game mode stays on; choosing "Not now" or ignoring the prompt for 15 seconds simply dismisses it). Game mode persists in the configuration until the next launch, whereas elevation lives only for one process lifetime — so after a restart, if game mode is still on and this instance is not elevated, the same prompt appears again at startup (it does not pop up during a silent start). To avoid being asked every time, enable "Always start as administrator" under General in Settings
- **Keep clipboard = On**: the recognition result stays on the clipboard so you can paste it again with Ctrl+V at any time; however, it overwrites any image / file currently on the clipboard
- **Keep clipboard = Off (default)**: the original clipboard content is restored automatically after pasting; when the clipboard holds an image / file (which cannot be restored safely), injection falls back to simulated keystrokes automatically
- **Paste delay**: how long to wait after writing to the clipboard before simulating the paste (default 50 ms, adjustable 10–1000 ms); increase it somewhat for programs that respond slowly

### 5.5 Interrupting and Canceling

> Said something wrong? You can take it back: trigger an interrupt action while recording and everything from this session that has not yet been typed is discarded.

Two interrupt methods (right-click menu "How to interrupt"; both can be enabled at the same time):

| Method | Default | Behavior |
| --- | --- | --- |
| Delete-key interrupt | On | Press Backspace / Delete while recording to interrupt the current recognition |
| Left-click interrupt | Off | Click anywhere on the floating bar to interrupt the current recognition |

### 5.6 VAD Voice Detection and Noise-floor Calibration

> ORI uses energy-based voice activity detection (VAD) to segment speech automatically: a speaking pause longer than the configured duration counts as the end of a sentence, and no speech for more than the configured number of seconds stops recording automatically.

VAD parameters (Settings → Recording → Voice Activity Detection (VAD)):

| Parameter | Default | Description |
| --- | --- | --- |
| Speech threshold | 500 | Energy below this value counts as silence (adjust to your microphone sensitivity) |
| Sentence-break wait | 600 ms | Continuous silence longer than this marks the end of a sentence |
| Minimum speech | 300 ms | Speech segments shorter than this are discarded (avoids false triggers from noise) |

**Automatic noise-floor calibration**: at the start of every recording, ORI spends about 1 second calibrating the ambient noise floor; when the noise floor is high, the silence threshold is raised automatically to keep the VAD from getting stuck — especially useful in noisy environments, with no need to adjust the threshold by hand over and over.

### 5.7 Always-on Mic Warm-up

> With the always-on mic enabled, the microphone stays "warm" — roughly the 400 ms of audio before you press the hotkey is included as well, so the first word you speak is not swallowed.

Related mechanisms:

- **Always-on mic warm-up** (`audio.warmup_capture`, off by default): pre-opens the capture stream in the background plus a pre-roll buffer (keeps about 400 ms of audio from before the hotkey press)
- **Background ASR connection**: audio captured during the handshake is buffered first and sent once the connection is established
- **Cost**: the taskbar microphone icon stays lit (the app continuously holds the microphone); turning it off saves more power but may swallow the first word

### 5.8 Pause Media and Global Mute

During recording, background audio can be interrupted automatically to prevent the microphone from picking up background sound that interferes with your speech; it resumes automatically when recording stops. Both switches are off by default and can be used separately or together.

- **Pause media**: enable it via the right-click menu "Behavior ▸ Pause media" or on the "Recording" settings page. When recording starts, playing media is truly paused (stopped at the current position) and resumes from that point when recording stops. Covers apps with media controls (browsers, mainstream music/video players); apps without media controls, such as games, cannot be paused — this is a system limitation.
- **Global mute**: provided only on the "Recording" settings page. While recording, all background sound is muted (including the game itself) and restored after recording stops. It can be used alone or stacked with "Pause media" (pause handles resume-from-position; mute handles total silence); for in-game recording, pairing it with "Game mode" is recommended. Note: during the few seconds of recording you hear no background sound at all (sudden silence in a game can affect sound-based positioning); if the app is force-killed (process killed) during recording, the mute state may persist — reopen the app and record once, or manually turn the affected programs' volume back up, to recover. You can also check "Mute only in Game Mode": with it on, global mute is applied only when game mode is also on, preventing everyday recording (writing/chatting) from accidentally silencing music and videos; pause media is not affected by this restriction.

Both switches take effect at the start of each recording according to their current state; toggling them mid-recording does not affect the current session, and when recording stops the app still undoes exactly the actions it actually performed for that session.

## 6. Recognition Engines

### 6.1 Engine Overview

| Engine | Model / Version | Key(s) | Notes |
| --- | --- | --- | --- |
| Alibaba Cloud Model Studio (default) | `paraformer-realtime-v2` (default) / `qwen-audio-3.0-asr-flash-streaming` (Qwen Audio large model, more accurate) | `api_key` | Default is Paraformer v2; the Qwen Audio large model recognizes more accurately and supports filler-word removal |
| Tencent Cloud | `Hy-ASR-3.0-preview` / `16k_zh` / `16k_zh_en` / `16k_zh_dialect`, etc. | `secret_id` + `secret_key` + `app_id` | Real-time streaming; direct WebSocket connection with signature authentication |
| iFLYTEK | Large-model edition (`autodialect`/`autominor`) / Standard edition (ZH/EN) | Large-model edition `app_id+api_key+api_secret`; Standard edition `std_app_id+std_api_key` | `edition` switches between the two independent key sets |
| Volcengine | Doubao streaming 1.0 (`bigasr`) / 2.0 (`seedasr`), billing configured independently | New version `api_key`; legacy `app_id`+`access_token` | Binary streaming protocol; supports secondary recognition + disfluency removal (DDC) |
| Local FunASR | `paraformer-zh-streaming` | **No key required** | Fully offline, CPU/GPU, background warm-up at startup, model auto-downloaded on first use |

> Cloud engines must be activated at your own expense in each vendor's console; the local FunASR engine is completely free and offline.
>
> **The packaged build (exe) currently does not include the local FunASR engine**; choosing FunASR prompts you to use a cloud engine or run from source instead.

### 6.2 Engine Switching

How to switch: right-click the floating bar → Recognition engine → pick the target category; the switch takes effect the moment you click.

Cloud engines are rendered in the menu by **category tier** (each group uses a grayed-out disabled item as its group header; group order follows `ui.engine_order`):

| Vendor | Menu category tiers | Model actually called by each tier |
| --- | --- | --- |
| Tencent Cloud | Large Model 2.0 / Large Model 1.0 / General Engines | Each of the three tiers binds one model, defaulting to 16k_zh_en_2.0 / 16k_zh_en / 16k_zh; each tier can be changed under Settings → Engine Parameters → Tencent Cloud (candidates in the table below) |
| Alibaba Cloud Model Studio | Large Model / General Engines | Large Model = qwen-audio-3.0-asr-flash-streaming; General Engines = paraformer-realtime-v2 |
| iFLYTEK | Realtime ASR Large Model / Realtime ASR Standard | Large-model edition = autodialect; Standard edition follows `xfyun.std_lang` (cn/en) |
| Volcengine | Doubao Large Model 2.0 / Doubao Large Model 1.0 | 2.0 = seedasr; 1.0 = bigasr (each generation's billing mode is configured independently) |
| FunASR Local | A single flat entry "Paraformer-ZH Streaming" | The only model; no category tiers |

Related notes:

- Each category name is followed by the model currently bound to it: Alibaba Cloud uses short Chinese names (e.g. "大模型版 · Qwen Audio"), while Tencent Cloud uses English model IDs (e.g. "大模型2.0 · 16k_zh_en_2.0")
- Engine groups with no keys configured are hidden by default (except the group of the current engine); FunASR has no notion of keys and instead shows "(not installed)" depending on whether its dependencies are installed
- Three-level visibility control (Settings → General → Engine order): `ui.engine_show_<provider>` hides an entire vendor group; `ui.engine_hidden_models` hides individual models by model ID (comma-separated, empty = show all); groups without configured keys remain hidden as before
- Engine group order and visibility can be adjusted under Settings → General → Engine order (each group can be expanded to per-model switches)

**Model ID reference** (the candidates for Tencent Cloud's three tiers can be chosen from the dropdown on the Settings "Engine Parameters" page; Alibaba Cloud's two tiers are fixed mappings):

| Vendor | Model ID | Display name |
| --- | --- | --- |
| Alibaba Cloud | `qwen-audio-3.0-asr-flash-streaming` | Qwen Audio · Large model (more accurate) |
| Alibaba Cloud | `paraformer-realtime-v2` | Paraformer v2 · General (ZH/EN/JA/KO + dialects, supports filler-word filtering, default) |
| Tencent Cloud | `16k_zh_en_2.0` | ZH-EN Large 2.0 · ZH/EN + 31 dialects |
| Tencent Cloud | `16k_zh_en_speaker_2.0` | ZH-EN Large 2.0 · Speaker diarization |
| Tencent Cloud | `Hy-ASR-3.0-preview` | Hy-ASR 3.0 Preview · Beta, client-side segmentation |
| Tencent Cloud | `16k_zh_en` | ZH-EN Large 1.0 · ZH/EN + 31 dialects |
| Tencent Cloud | `16k_multi_lang` | Multilingual Large · 15 languages |
| Tencent Cloud | `16k_en_large` | English Large |
| Tencent Cloud | `16k_zh` | Chinese General · ZH/EN |
| Tencent Cloud | `16k_zh-TW` | Traditional Chinese |
| Tencent Cloud | `16k_zh_edu` | Chinese Education |
| Tencent Cloud | `16k_zh_medical` | Chinese Medical |
| Tencent Cloud | `16k_yue` | Cantonese |
| Tencent Cloud | `16k_zh_dialect` | Multi-dialect · 23 types |

For iFLYTEK/Volcengine, the menu category names are the tiers themselves (see table above); no separate mapping is needed.

### 6.3 Alibaba Cloud Model Studio

The default engine. Related configuration (`config.yaml` → `aliyun`):

| Field | Default | Description |
| --- | --- | --- |
| `api_key` | empty | Model Studio API key (starts with sk-), obtained from https://bailian.console.aliyun.com |
| `model` | `paraformer-realtime-v2` | Paraformer v2 (Chinese including dialects + multiple languages, default); alternative `qwen-audio-3.0-asr-flash-streaming` (Qwen Audio large model, more accurate) |
| `disfluency_removal` | `true` | Filter filler words (verbal tics such as "ah" and "um") |
| `max_sentence_silence` | `800` | Sentence-break silence threshold (milliseconds, 200–6000) |
| `vocabulary_id` | empty | Hotword list ID (optional, created in the Model Studio console) |
| `language_hints` | empty | Language hints (optional), e.g. `zh` / `zh,en`; leave empty for automatic detection |

### 6.4 Tencent Cloud

Related configuration (`config.yaml` → `tencent`):

| Field | Default | Description |
| --- | --- | --- |
| `secret_id` / `secret_key` | empty | Obtain from https://console.cloud.tencent.com/cam/capi |
| `app_id` | empty | Obtain from https://console.cloud.tencent.com/asr |
| `engine_model_type` | `Hy-ASR-3.0-preview` | The model called directly by right-click menu entries other than "General Engines": default Hy-ASR 3.0 (large model, ZH/EN + dialects; in this beta the server does no sentence segmentation — the client splits sentences by speech pauses, and the splitting cadence is affected by `vad.silence_duration_ms`; when recording stops, the last sentence is typed immediately, so even a short two-character sentence is not lost); for candidates see the model reference table in §6.2 |
| `menu_model_large_2_0` | `16k_zh_en_2.0` | The model actually called by the right-click menu "Large Model 2.0" tier (changeable from the dropdown on the Settings "Engine Parameters" page) |
| `menu_model_large_1_0` | `16k_zh_en` | The model actually called by the right-click menu "Large Model 1.0" tier |
| `menu_model_general` | `16k_zh` | The model actually called by the right-click menu "General Engines" tier |
| `filter_punc` | `1` | Punctuation filtering: 0 no filtering / 1 strip trailing punctuation / 2 strip all punctuation |
| `convert_num_mode` | `1` | Number conversion: 0 Chinese numerals / 1 smart-convert to Arabic numerals |
| `filter_dirty` | `0` | Sensitive-word filtering: 0 no filtering / 1 filter / 2 replace with * |
| `filter_modal` | `2` | Filler-word filtering: 0 no filtering / 1 partial / 2 strict, filter all |
| `hotword_id` / `customization_id` | empty | Hotword list / self-learning model ID (optional) |

### 6.5 iFLYTEK

The [iFLYTEK Open Platform](https://console.xfyun.cn/) provides two independent editions with separate keys (`config.yaml` → `xfyun`):

| Field | Description |
| --- | --- |
| `edition` | `llm` = Realtime ASR Large Model / `std` = Realtime ASR Standard; switched by the two right-click menu categories (written automatically), so manual editing is normally unnecessary |
| `app_id` / `api_key` / `api_secret` | Large-model edition keys, obtained after activating the "实时语音转写大模型" (Realtime ASR Large Model) service |
| `std_app_id` / `std_api_key` | Standard-edition keys, obtained after activating the "实时语音转写" (Realtime ASR, standard edition) service; not interchangeable with the large-model edition keys |
| `lang` | Large-model edition language: `autodialect` = ZH/EN + 202 dialects / `autominor` = 37 languages (requires activation via a support ticket) |
| `std_lang` | Standard-edition language: `cn` = Chinese / mixed Chinese-English / `en` = English |
| `pd` | Standard-edition vertical domain: court / edu / finance / medical / tech; leave empty for general |
| `std_punc` | Standard-edition punctuation filtering (punc=0) |
| `filter_modal` / `std_filter_modal` | Filler-word filtering, independent for each edition (the client discards wp=s smoothed filler words) |

### 6.6 Volcengine Doubao

Get your keys from the [Volcengine Ark console](https://console.volcengine.com/ark/region:cn-beijing/openManagement?advancedActiveKey=model&projectName=ark&tab=TTS). Volcengine Doubao streaming ASR has two generations, 1.0 (`bigasr`) and 2.0 (`seedasr`), and each generation's billing mode is configured independently (`config.yaml` → `volcengine`):

| Field | Description |
| --- | --- |
| `api_key` | New-version authentication API Key (X-Api-Key); leave empty for legacy-version users |
| `access_token` + `app_id` | Legacy authentication (X-Api-Access-Key + X-Api-App-Key); leave empty for new-version users |
| `bigasr_billing` | Doubao 1.0 billing mode: `duration` (billed by duration) / `concurrent` (billed by concurrency) |
| `seedasr_billing` | Doubao 2.0 billing mode, same as above |
| `resource_id` | Synthesized automatically by the program (volc.{version}.sauc.{billing}); manual modification is normally unnecessary |
| `enable_ddc` | Disfluency removal (strips filler words/repeated words), on by default |
| `enable_nonstream` | Secondary recognition: after ~800 ms of silence, produces a more accurate final result; on by default |

Note: each billing mode must match the resource package purchased in the console; switching versions in the right-click menu automatically applies the corresponding version's billing mode.

### 6.7 Local FunASR

> A fully offline local engine that requires no keys. All recognition happens on your machine — suited to scenarios with no network, sensitive content, or no desire to pay.

Related configuration (`config.yaml` → `funasr`):

| Field | Default | Description |
| --- | --- | --- |
| `model` | `paraformer-zh-streaming` | Streaming model; supports Live typing |
| `device` | `cuda` | `auto` / `cuda` / `cpu` (cuda is faster if you have a GPU) |
| `chunk_preset` | `默认` | Recognition window preset: `默认` (Default) 600 ms / `低延迟` (Low latency) 480 ms / `高准确` (High accuracy) 720 ms (changing it triggers automatic re-warm-up) |
| `hotword` | empty | Hotwords, separated by spaces |
| `preheat` | `true` | Warm up the model in the background at startup (false = load only on first recording) |
| `local_dir` | empty | Local model directory (absolute path to weights you downloaded yourself); when non-empty, loads locally without downloading over the network |
| `model_hub` | `ms` | Download channel: `ms` = ModelScope / `hf` = HuggingFace (only takes effect during automatic download) |
| `model_revision` | empty | Pin the model version (e.g. `apache-2.0-20260804`); leave empty to use the latest |

Notes:

- Install dependencies: `pip install funasr torch torchaudio` (for GPU versions see §3.3)
- On first use the model is automatically downloaded from ModelScope to `~/.cache/modelscope/`; for offline use, download it in advance
- The packaged build does not include this engine; run from source to use it

## 7. AI Correction

### 7.1 Overview

> AI correction (off by default) calls a large model to polish the text after recognition completes: fixing homophone typos, completing punctuation, and removing verbal tics.

Compatible with the OpenAI protocol — OpenAI / DeepSeek / Moonshot / Zhipu / Tongyi / local Ollama / vLLM and more; just change `base_url` to connect.

[DeepSeek](https://api-docs.deepseek.com/zh-cn/) is recommended; follow its documentation to obtain the endpoint and API key directly.

### 7.2 Enabling and Endpoint Setup

How to enable:

1. Right-click menu → Injection method → check "AI correction"
2. Or Settings → AI Correction → Enable AI Correction → Save

Endpoint configuration (Settings → General → Shared AI Endpoint, or `config.yaml` → `llm`):

| Field | Default | Description |
| --- | --- | --- |
| `llm.enable` | `false` | Master switch (off by default, opt-in) |
| `llm.base_url` | `https://api.deepseek.com/v1` | OpenAI-compatible endpoint URL; OpenAI: `https://api.openai.com/v1`; Ollama: `http://localhost:11434/v1` |
| `llm.api_key` | empty | Bearer authentication key (any value works for a local Ollama) |
| `llm.model` | `deepseek-flash` | Correction model |
| `llm.timeout_ms` | `3000` | On timeout the original text is used directly; no blocking |
| `llm.disable_thinking` | `true` | Disable thinking mode (supported by DeepSeek V4 / Zhipu / Qwen3 / OpenAI o-series; the older R1 does not support it — switch models) |

### 7.3 The Two Typing Modes

| Mode | Behavior | Characteristics |
| --- | --- | --- |
| Default (`llm.wait_for_correction=false`) | The original text is typed first; once the background LLM finishes polishing, it is backspaced and replaced in place | Real time, but with the visual jumping of backspacing and retyping |
| Type after correction (`true`) | The final result is not typed immediately; the corrected version is injected directly once ready | No jumping; eliminates swallowed sentences at the root; 1–3 seconds of latency per sentence |

Recommendation: use the default mode for everyday chatting; turn on "Type after correction" for scenarios like document writing where the quality of the final text matters more than speed.

### 7.4 Batch Correction

> Accumulate several sentences and send them for correction together to save API calls.

| Field | Default | Description |
| --- | --- | --- |
| `llm.batch_sentences` | `3` | Accumulated sentence count: `1` = correct each sentence immediately; `2+` = collect N sentences and send them for correction in one batch (saves API calls) |
| `llm.batch_idle_ms` | `1000` | In accumulation mode, correction is also triggered when the silence since the previous sentence exceeds this duration |

How it works:

- The silence trigger and the no-speech timeout **work independently**: the silence trigger governs when an LLM batch is sent for correction; the no-speech timeout governs when the recording session stops
- When recording stops (including via the no-speech timeout), sentences still in the accumulation queue are **force-sent for correction** (`flush_pending`), so no sentence is lost
- For batch correction the program automatically appends a "return one line per sentence" requirement to the end of the prompt and verifies that the number of returned lines matches the number of sentences; if the LLM merges or swallows sentences, the entire batch replacement is abandoned and the originals are kept — better to skip correction than to lose a sentence
- `batch_idle_ms` only takes effect in accumulation mode (`batch_sentences ≥ 2`); natural pauses between spoken Chinese sentences are about 0.3–1 second, so 1–2 seconds is recommended

### 7.5 Custom Prompts

- **Must include the `{text}` placeholder** — otherwise the LLM never receives the text to correct and will reply "please provide the text to be corrected"
- The Settings UI only accepts a single line; for multiple lines, edit `config.yaml` manually with the YAML block scalar `|`:

```yaml
llm:
  prompt: |
    你是语音识别文本修正器。
    - 修正同音错别字
    - 补全标点
    文本：
    {text}
```

- Leave empty to use the built-in template (which already contains the `{text}` placeholder)
- The Settings UI supports **version management** for custom prompts (version dropdown + New/Save/Delete); built-in presets cannot be modified

### 7.6 How to Confirm AI Correction Is Working

Once enabled, every sentence you speak makes the floating bar show one of three indicators (disappearing after 1–2 seconds):

| Floating bar hint | Meaning |
| --- | --- |
| `AI corrected N->M chars` | The call succeeded, corrections were made, and the text was replaced |
| `AI correction: original already correct` | The call succeeded and the original was already fine, so nothing was changed (proof that the AI is actually running) |
| `AI correction failed (original kept)` | The call failed (wrong key / timeout / network); the original text is used as a fallback |

**No indicator at all** = AI correction is not actually enabled; check the `llm.enable` switch and whether `api_key` has been filled in.

## 8. Voice Commands

### 8.1 Overview

> Voice commands (off by default) let you "use your voice instead of your hands": while recording, say the trigger word (default 「听我说」, "listen to me") at the start of a sentence to enter command mode — that sentence is not typed, not saved to history, and not sent for AI correction; it is executed directly as an instruction.

How to enable: right-click menu "Injection method ▸ Voice commands".

### 8.2 Trigger Methods

| Method | Action | Description |
| --- | --- | --- |
| One-shot | 「听我说 发送」 | Trigger word + command in a single step |
| Trigger word only (standby) | 「听我说」 | Enters standby mode (8 seconds by default); the next sentence is the command; automatically cancelled on timeout |
| Command hotkey | `Ctrl+Shift+G` (default, changeable) | Pressing it immediately starts recording and enters standby, no trigger word needed; leave empty to disable |

Fault tolerance: with AI correction on, if the trigger word is recognized as homophones (e.g. "之嘛开们"), it first goes through the correction pipeline; once the corrected version spells back the trigger word, command detection runs a second time and the command still executes.

### 8.3 Local Command Reference

> Local commands are fast to recognize and free; both Chinese and English variants can be spoken, case-insensitively. The full command reference table is under Settings → Voice Commands → Command reference (expandable/collapsible).

| Category | Phrase (example variants) | Effect |
| --- | --- | --- |
| Editing | 删除那句 / 删掉那句 / 删除刚听写 / delete that | Delete the text just dictated |
| Editing | 全选 / 选择全部 / select all | Select all text in the current input box (combine with "帮我" to process a whole passage) |
| Editing | 撤销 / 恢复上一步 / undo | Undo the last action |
| Editing | 退格 / 删一个字 / backspace | Backspace-delete one character |
| Sending | 发送 / 回车 / press enter | Press Enter (send the message) |
| Sending | 停止录音 / 停止 / 停止听写 / stop listening | Stop recording |
| Switching | 逐字输入 / 切逐字 | Switch to simulated keystrokes (typing input) |
| Switching | 剪贴输入 / 剪贴板输入 / 切剪贴 | Switch to clipboard paste (clipboard input) |
| Toggles | 保留剪贴 (+关/停用/取消 = turn off) | Toggle for leaving the result on the clipboard |
| Toggles | 逐字同步 (+关/停用/取消 = turn off) | Live typing toggle |
| Toggles | AI 修正 / 智能修正 (+关/停用/取消 = turn off) | AI auto-correction toggle |
| Toggles | 删除打断 / 删除键打断 (+关/停用/取消 = turn off) | Delete-key interrupt toggle |

Matching priority: local command table > custom phrases > AI commands. On no match, the hint reads: "Not understood. Say a key/setting directly, or \"帮我\" + command for AI".

### 8.4 AI Commands

> AI commands (require `llm.api_key` to be configured): 「听我说 帮我 + instruction」 processes the currently selected text — read the selection → large-model transformation → replace in place; both the selected text and the clipboard are properly restored.

| Phrase | Effect |
| --- | --- |
| 帮我润色 / 帮我改通顺 | AI polish |
| 帮我加标点 | Automatic punctuation |
| 帮我翻译成英文（中文/日文…） | Translation |
| 帮我扩写 / 帮我缩写 | Expand / condense |
| 帮我改正式 / 改口语 / 改客气 | Tone adjustment |
| 帮我居中 / 帮我加粗 / 转Markdown | Formatting tags |
| Any instruction | Free-form passthrough (the template contains the `{selection}`/`{command}` placeholders and requires the model to output the result body directly) |

Related configuration (`config.yaml` → `commands`):

| Field | Default | Description |
| --- | --- | --- |
| `ai_prefix` | `帮我` | AI command prefix |
| `ai_thinking` | `true` | Thinking mode (on by default — slower but more accurate; independent of AI correction's `disable_thinking`) |
| `ai_prompt_template` | empty | Custom prompt template; empty = built-in default passthrough template; when customizing, use the `{selection}`/`{command}` placeholders |

### 8.5 Custom Phrases

> Say "trigger word + phrase name" (e.g. "听我说 我的邮箱") to insert preset text.

- Requires the Settings → Voice Commands → Custom phrases switch to be on
- The list is a two-column table (phrase name / phrase content) with Add/Delete, stored in `phrases.json` in the state directory
- Exact matching, no pinyin fault tolerance; command-table lookup takes priority over phrases

### 8.6 Mutual Exclusion with Live Typing

Intermediate frames of the command sentence can remain in the input box due to homophone misrecognition (displacing the text you had selected), so **voice commands and Live typing cannot be on at the same time**: enabling either one asks for confirmation and then automatically turns off the other (your original preference is remembered and restored when the feature is turned off).

## 9. Real-Time Voice Conversation

### 9.1 Overview

> Press `Ctrl+Shift+H` to chat with the AI by voice directly: Doubao and Qwen are end-to-end speech large models that understand your speech and reply with audio — you can interrupt the AI's playback (barge-in) at any time just by starting to speak; Hermes uses local recognition and replies in text (see §9.10). All three support multi-turn continuous conversation.

The voice conversation supports three service providers — pick one of the three (`dialog.provider`, Settings → Voice Mode → Dialog provider):

| Provider | Config value | Description |
| --- | --- | --- |
| Doubao S2S (default) | `doubao` | Volcengine's Doubao end-to-end speech large model (a single WebSocket handles segmentation/recognition/synthesis); for setup and usage see §9.2–§9.8 |
| Alibaba Cloud Qwen-Audio | `aliyun` | Alibaba Cloud Model Studio's Qwen-Audio Realtime end-to-end speech large model; for setup and usage see §9.9 |
| Hermes Agent (self-hosted) | `hermes` | A self-hosted hermes-agent API server: a pure-text agent; segmentation and recognition are done on the local machine (dialog-specific ASR) and replies are text (no AI speaking); for setup and usage see §9.10 |

The three are mutually exclusive: after switching providers, the field groups on the Settings → Voice Mode page show or hide accordingly; they share the four settings `enable` / `output_device` / `half_duplex` / `auto_reconnect` (Hermes keeps half-duplex always on and auto-reconnect does not apply; see §9.10).

How it differs from dictation mode (Chapter 5):

| | Dictation mode | Voice conversation |
| --- | --- | --- |
| Purpose | Converts speech into text and types it into the input field | Continuous Q&A chat with the AI |
| Pipeline | ASR recognition → (optional AI correction) → text typed at the cursor | Doubao/Qwen: end-to-end speech large model (recognition, understanding, and reply in one); Hermes: local ASR recognition → Hermes text reply |
| Reply | None | Doubao/Qwen reply with AI speech; Hermes replies in plain text (TTS is reserved in the architecture but not yet wired up) |
| Interruption | Esc / Delete key | Simply start speaking to interrupt the AI's playback |

- The conversation is shown in a bubble card on the floating bar, with "You / AI" role color bands distinguishing the two parties (teal = you, purple = AI); AI replies stream in line by line and become final when the AI finishes speaking — click the bubble to expand/collapse the full text
- The conversation and recording are mutually exclusive: pressing the dialog hotkey while recording is ignored with a notice; pressing the recording hotkey during a conversation is likewise ignored with a notice — neither side preempts the other
- Billing follows the server-side rules: one complete conversation (from entering to exiting) counts as one query — Doubao is billed per the Volcengine console invoice, Qwen per the Alibaba Cloud Model Studio invoice; Hermes is self-hosted and not billed

### 9.2 Activation and Keys

> This section covers setup for the **Doubao provider**; for Alibaba Cloud Qwen-Audio Realtime (`dialog.provider: "aliyun"`), see §9.9.

1. Enable the "Doubao end-to-end real-time speech large model" in the Volcengine speech console (console.volcengine.com/speech)
2. Two key authentication methods (the same Volcengine keys used by the recognition engine work here):
   - **New single key**: `api_key` (generated under "API Key Management" in the console)
   - **Legacy dual keys**: `app_id` + `access_token` (from the app details page; both must come from the same app)
3. Leaving the three key fields **empty = automatically reuse the Volcengine configuration from the Engine Keys page** — users who have already set up Volcengine recognition work out of the box, with no need to enter the keys twice

The Settings → Voice Mode → Connection group has a "Test connection" button: it completes a full handshake with the currently entered keys (opens a session and closes it right away). On success it displays the `X-Tt-Logid` you need for support tickets; on failure it gives the specific reason (service not enabled / invalid keys / network unreachable).

### 9.3 Entering and Exiting

Enter a conversation (either entry point):

- Press the dialog hotkey, `Ctrl+Shift+H` by default (changeable under Settings → Voice Mode → Dialog hotkey; **empty = the hotkey is simply not registered** — the menu and floating-bar entries remain; changes require a restart to take effect)
- Check "Voice chat" in the floating bar's right-click menu

Exit the conversation (any of these):

- Press the dialog hotkey again
- Uncheck "Voice chat" in the right-click menu
- Say "exit" or "goodbye" to the AI (can be turned off under session behavior; for Hermes the match happens after local ASR recognition, and works the same way)
- Click the floating bar's start/stop button (during a conversation the button icon becomes a stop symbol, i.e. "exit the conversation")
- Hermes only: while thinking (while the AI is generating a reply), press the hotkey once = **cancel the current turn** and return to listening (the server truly stops via /stop and does not keep running in the background); press it twice = exit the conversation

Turn off the voice conversation feature entirely: Settings → Voice Mode → Connection → **Enable Voice Mode** (or `dialog.enable: false` in `config.yaml`). Once off, the "Voice chat" right-click menu item, the dialog hotkey, and the floating-bar conversation entry are all removed — as if the feature did not exist; this settings page stays available so you can re-enable it anytime. Changes take effect immediately (the menu is rebuilt on each right-click and hotkeys are re-registered on config hot reload); no restart needed.

> Difference from "leaving the dialog hotkey empty": empty only removes the hotkey as an entry point — the menu and floating bar can still enter a conversation; the master switch takes the whole feature offline.
>
> Turning off the master switch mid-conversation will not trap you: the start/stop button and the hotkey still exit the current conversation normally — you just cannot enter a new one afterward.

### 9.4 Conversation States and Interruption

The floating bar's status dot has four conversation states; thinking and AI speaking deliberately use different colors (the same color would make it look stuck):

| State | Status dot color | Visual | Meaning |
| --- | --- | --- | --- |
| Connecting | Blue | Spinning spinner | Establishing a session with the server |
| You speaking | Teal | Voiceprint bouncing with the audio level | Microphone is capturing audio |
| Thinking | Blue | Spinning spinner | AI is understanding and generating a reply |
| AI speaking | Purple | Breathing pulse | AI is replying by voice |

Interruption (barge-in): full-duplex by default — while the AI is speaking, simply start talking to interrupt and take over the conversation; no need to wait for it to finish.

| Setting | Default | Description |
| --- | --- | --- |
| `dialog.end_smooth_window_ms` | `800` | Turn-detection window: how long a silence counts as the end of a sentence. Smaller values make the AI pick up the conversation faster, but too short may mistake a mid-sentence pause for the end of speech (settings slider 500–5000; larger values can be edited by hand in `config.yaml`, up to 50000) |
| `dialog.half_duplex` | `false` | Half-duplex: pauses audio capture while the AI is speaking. Recommended for **users on speakers** (prevents the microphone from picking up the speaker output and self-interrupting); keep it off with headphones to enjoy full-duplex |

Hermes has no voice reply, so the "AI speaking" state never appears: once thinking completes and the reply text becomes final, it goes straight back to listening (the state machine is shared — Doubao/Qwen use all four states, Hermes only three of them).

> §9.5–§9.7 cover **Doubao S2S**-only fields; for the corresponding Alibaba Cloud Qwen fields see §9.9, and for Hermes see §9.10.

### 9.5 Model and Persona

| Model | Version | Best for |
| --- | --- | --- |
| O2.0 general chat | `1.2.1.1` | Everyday chat and Q&A (default) |
| SC2.0 role-play | `2.2.0.0` | Human-like role-play; supports cloned voices and action/expression descriptions |

Persona fields take effect per version — one set or the other:

- **O2.0**: `bot_name` (the name the AI calls itself, ≤20 characters), `system_role` (description of role/duties/tone), `speaking_style` (style preference, e.g. "relaxed and humorous, use short sentences")
- **SC2.0**: `character_manifest` (character description, e.g. "You are an ancient-style book boy, speaking half-classical half-vernacular"; may include action and expression descriptions), mutually exclusive with the three fields above

### 9.6 Voice and Playback

- **Voice** (`dialog.speaker`): the dropdown contains 7 official voices (vv / 小何 (Xiaohe) / 云舟 (Yunzhou) / 小天 (Xiaotian) — 4 Chinese; Tim / Dacey / Stokie — 3 English); for SC2.0 cloned voices (names starting with `saturn_` or `S_`), type the name directly into the input box
- **Speech rate / Volume** (`dialog.speech_rate` / `dialog.loudness_rate`): range [-50, 100]; positive values speed up / brighten; only applies to 2.0-version models
- **Playback device** (`dialog.output_device`): which device the AI voice plays from; follows the system output by default; the list refreshes automatically when the dropdown is opened (hot-plug supported)

### 9.7 Session Behavior

| Setting | Default | Description |
| --- | --- | --- |
| `dialog.enable_user_query_exit` | `true` | Saying "exit" / "goodbye" ends the conversation automatically |
| `dialog.keep_context` | `true` | The next conversation continues from the most recent 20 turns of context (the server remembers by dialog_id) |
| `dialog.auto_reconnect` | `true` | Automatically rebuilds the session after long idle periods or service errors; at most 1 reconnect per conversation |
| `dialog.strict_audit` | `true` | Server-side safety review level (the default value in the Volcengine docs) |

### 9.8 FAQ

- **Entering a conversation shows "Please fill in the voice mode key first"**: all three key fields are empty and the Engine Keys page has no usable Volcengine key either; fill them in under Settings → Voice Mode, or configure the Volcengine recognition key first
- **Shows "Recording in progress, stop it first" / "Dialog in progress, exit the dialog first"**: the normal behavior of the two mutually exclusive modes — exit the current mode first
- **The AI keeps "interrupting itself"**: caused by speaker output being picked up by the mic; enable half-duplex or switch to headphones (see §9.4)
- **The AI responds too fast/too slowly**: adjust the turn-detection window `end_smooth_window_ms` (see §9.4)
- **Connection failures / frequent errors**: run "Test connection" first to self-check and note the logid; match against the error message (e.g. "dialog configuration error" → check the model and persona; "no interaction for a long time" is a normal release and auto-reconnect takes over)
- **Voice/persona changes not taking effect**: some parameters are sent when the session is created; exit the conversation and re-enter
- **Settings don't line up after switching providers**: after switching provider, the field groups on the Settings → Voice Mode page show or hide accordingly (the Doubao / Qwen / Hermes groups are mutually exclusive) — this is normal; some parameters are sent at session creation, so re-enter the conversation for them to take effect
- **Qwen context not continued**: Qwen has no cross-connection continuation (reconnecting starts a new session); cross-conversation memory is supported only by Doubao (`keep_context`); within a single session the number of history turns is controlled by `max_history_turns`
- **Hermes says not configured when entering a conversation**: `dialog.hermes.base_url` / `api_key` not filled in (api_key is the `API_SERVER_KEY` in `~/.hermes/.env` on the server); an authentication failure shows "Hermes authentication failed: check the API Key"
- **No sound from Hermes**: normal — Hermes provides no audio (audio_api=false); replies are shown as text in the floating-bar bubble; TTS is reserved for later
- **Hermes answers slowly**: model generation time on your self-hosted server (measured 5.5–7.9 s for a plain-text single turn); you can adjust `turn_timeout_ms`, but do not set it too low; to cancel the current turn while thinking, press the dialog hotkey once
- **Sessions piling up on the Hermes server**: Hermes sessions only accumulate on the server side — the client never closes or deletes them (each conversation creates a new session, with a unique suffix added to the title automatically to avoid duplicates); this is a server-side characteristic, and you can clean them up on the Hermes side yourself

### 9.9 Alibaba Cloud Qwen-Audio Realtime (Provider #2)

> The second provider for the voice conversation: Alibaba Cloud Model Studio's Qwen-Audio Realtime end-to-end speech large model. Switch to "Alibaba Cloud" under Settings → Voice Mode → Dialog provider (`dialog.provider: "aliyun"`); the field groups lower on the settings page switch to the Qwen-specific configuration accordingly.

**Activation and keys**

1. Enable the "Qwen-Audio Realtime" service in the [Alibaba Cloud Model Studio console](https://bailian.console.aliyun.com)
2. API Key (`dialog.qwen.api_key`): enter your Model Studio API Key (starts with sk-); **leaving it empty automatically reuses the Alibaba Cloud configuration from the Engine Keys page** — the same key is shared with Alibaba Cloud recognition, so users who have already set that up work out of the box
3. Region (`dialog.qwen.region`, Settings → Voice Mode → Qwen connection → Region):
   - `legacy` (default): public endpoint under the old domain; no WorkspaceId needed
   - `beijing` / `singapore`: dedicated endpoints under the new domain; **WorkspaceId is required** (find it under "Business Space" in the top-right of the Model Studio console)
4. "Test connection": completes a full handshake with the currently entered key; on success it shows `session=<id>` (the counterpart of Doubao's X-Tt-Logid, for use when reporting issues); on failure it gives the specific reason

**Model and persona**

| Setting | Default | Description |
| --- | --- | --- |
| `dialog.qwen.model` | `qwen-audio-3.0-realtime-plus` | `plus` = more capable / `flash` = faster and cheaper |
| `dialog.qwen.voice` | `longanqian` | 5 official dragon-series voices: longanqian / longanlingxin / longanlingxi / longanxiaoxin / longanlufeng |
| `dialog.qwen.instructions` | empty | System instructions (persona/style/duties — the counterpart of Doubao's persona description); empty uses the server default |

**Audio capture and turn detection**

| Setting | Default | Description |
| --- | --- | --- |
| `dialog.qwen.turn_detection` | `server_vad` | `server_vad` = server-side VAD turn detection (tunable); `smart_turn` = model-driven automatic turn detection (more natural, not tunable, and it may retract valid speech — the client already handles this without getting stuck) |
| `dialog.qwen.vad_threshold` | `0.5` | [-1.0, 1.0]; higher values trigger less easily (server_vad only) |
| `dialog.qwen.silence_duration_ms` | `800` | How long a silence counts as the end of a sentence, 200–6000 ms (server_vad only) |

**Session behavior**

| Setting | Default | Description |
| --- | --- | --- |
| `dialog.qwen.enable_speech_emotion` | `true` | Detects emotion in the user's speech and lets it influence replies |
| `dialog.qwen.max_history_turns` | `20` | Number of conversation turns carried within a single session (1–50); more turns means better continuity but higher token consumption |
| `dialog.qwen.enable_search` | `false` | Web search when replying (plus/flash models only) |

**Key differences from Doubao**

| Item | Doubao S2S | Alibaba Cloud Qwen |
| --- | --- | --- |
| Key fallback | Reuses the Volcengine config from the Engine Keys page | Reuses the Alibaba Cloud config from the Engine Keys page |
| Turn detection | Client-side silence detection (`end_smooth_window_ms`) | Server-side VAD / model-driven automatic turn detection (`turn_detection`) |
| Cross-conversation memory | `keep_context` continues 20 turns by dialog_id | **No cross-connection continuation**: reconnecting starts a new session; history turns apply within a single session only |
| Persona | Doubao field group (O / SC version, one or the other) | A single "System instructions" field |
| Voice | 7 official voices + SC2.0 cloned voices | 5 official dragon-series voices |
| Test connection | Shows `logid=<value>` | Shows `session=<id>` |
| Billing | One complete conversation counts as one query (per the Volcengine console invoice) | Per the Model Studio console invoice |

All three providers share the `dialog.enable` master switch; Doubao and Qwen share `output_device` / `half_duplex` / `auto_reconnect`; Hermes keeps half-duplex always on, has no audio output, and auto-reconnect does not apply. The four conversation states and the interruption behavior (§9.4) apply to all three providers alike (Hermes has no "AI speaking" state).

### 9.10 Hermes Agent (Provider #3)

> The third provider for the voice conversation: a **self-hosted** hermes-agent API server (NousResearch's open-source agent, OpenAI-compatible HTTP). It differs in nature from the first two — Hermes is a **pure-text agent** (with a full toolset, memory, and persona) and provides no audio capabilities at all (measured capabilities `audio_api=false` / `realtime_voice=false`), so segmentation and recognition are filled in locally by ORI: **local VAD segmentation + dialog-specific ASR recognition → text sent to Hermes → text reply**. Self-hosted and self-used, with no billing whatsoever.

**Activation and keys**

1. Deploy hermes-agent on your server and start the API server (see its official documentation)
2. Switch to "Hermes Agent (self-hosted)" under Settings → Voice Mode → Dialog provider (`dialog.provider: "hermes"`)
3. Fill in the configuration (`dialog.hermes`, Settings → Voice Mode → Hermes connection):
   - `base_url`: the server address, e.g. `http://192.168.0.242:8642` (without `/v1`; a mistakenly included suffix is stripped automatically)
   - `api_key`: the `API_SERVER_KEY` in `~/.hermes/.env` on the server (can be left empty for a self-hosted service without authentication)
4. "Test connection" does not apply to Hermes (the button only shows a neutral notice): use the diagnostic script `tests/diag_hermes_dialog.py` or simply press the dialog hotkey for a real connection

**Dialog-specific recognition**

The input text for Hermes comes from ORI's own ASR engine. "Dialog-specific recognition" (`dialog.asr`, Settings → Voice Mode → Dialog-specific recognition) lets you assign a separate engine for conversations without affecting dictation:

| Setting | Default | Description |
| --- | --- | --- |
| `dialog.asr.engine` | empty | Empty = follow the top-level "Recognition Engine"; options are `tencent` / `aliyun` (other engines are not supported yet — choosing one reports a clear error and prompts you to switch back) |
| `dialog.asr.model` | empty | Empty = the engine's default tier; Tencent accepts `16k_zh_en_2.0` / `16k_multi_lang` |
| `dialog.asr.lang` | empty | Language hint, e.g. `zh,en`; empty = engine default |
| `dialog.asr.hotword` | empty | Hotwords: Tencent takes a hotword list ID; Alibaba Cloud takes the hotword list ID from the Model Studio console |

Typical usage: dictation with a Chinese engine, conversation with a mixed Chinese-English engine — neither has to compromise.

**Session behavior**

| Item | Behavior |
| --- | --- |
| Session | Each time you enter a conversation, a new session is created on the server (title = `session_title` + a unique suffix, to avoid the server-side "title already taken" 400); the conversation log is visible on the Hermes side and can be shared with the console / CLI as the same session record |
| Turns | Serial, turn by turn: while a request is in flight, **no audio is captured** (the "Half-duplex" switch has no effect on Hermes — it is always on); reply text streams into the floating-bar bubble, and after the final result it returns to listening |
| Cancellation | While thinking, press the dialog hotkey once to cancel the current turn (the server truly stops via `/stop` and does not keep running in the background); press it twice to exit the conversation |
| Tolerance | `system_hint` declares with every turn that "the input comes from speech recognition and may contain homophone typos or missing punctuation", leaving the LLM to tolerate it itself — saving one round trip compared with adding another layer of AI correction |
| Timeout | `connect_timeout_ms` (health check and session creation, default 5000 ms) / `turn_timeout_ms` (upper limit for a single-turn reply, default 60000 ms; the agent may run tools — measured plain-text turns still take 5.5–7.9 s, so do not set it too low) |
| Memory | Multiple turns are continuous within the same session (the server remembers by session); after exiting, the session is not reused — cross-conversation memory (Doubao's `keep_context`) does not apply |

## 10. Settings Reference

### 10.1 General

| Group | Setting | Description |
| --- | --- | --- |
| Behavior | Always start as administrator | Action button pinned at the top of the page (UAC shield icon): creates a logon scheduled task so the app starts as administrator at boot without a UAC prompt |
| Hotkeys | Recording hotkey | Default `Ctrl+G`; recorded in the hotkey capture box (Esc cancels, Backspace clears). A restart is required for changes to take effect |
| Output | Paste delay | 10–1000 ms, default 50 |
| Interface | Auto-hide delay | 0–3600 seconds, 0 = never hide |
| Interface | Floating bar opacity | 0–100%, 0 = follow the theme default, previewed live |
| Interface | Settings theme | Card selection: Minimal Dark / Fluent Light, previewed instantly on selection |
| Shared AI Endpoint | API address / API Key / Model | Shared by all voice AI features (both the "帮我" voice command and AI correction read from here) |
| Shared AI Endpoint | Test connectivity | Sends a minimal request with the address/key/model currently entered and immediately reports success or the failure reason |
| Engine order | Visibility switches + ↑↓ reordering | 5 groups (Tencent Cloud / Alibaba Cloud Model Studio / iFLYTEK / Volcengine / FunASR local); each group expands to per-model switches (hide a whole vendor group or individual models). Controls the group order and visibility in the right-click Recognition Engine menu |

### 10.2 Engine Keys

Accordion card layout; the card of the current engine is expanded by default, and the order follows the engine order setting:

- **Tencent Cloud**: SecretId / SecretKey / AppID (password fields)
- **Alibaba Cloud Model Studio**: API Key (starts with sk-)
- **iFLYTEK** (two subsections: LLM version / standard version): LLM version AppID / APIKey / APISecret; standard version AppID / APIKey
- **Volcengine** (two subsections: new / legacy authentication): new authentication API Key; legacy authentication App ID / Access Token

### 10.3 Engine Parameters

Accordion card layout; the order follows the engine order setting:

- **Tencent Cloud**: models for the three menu categories (Large Model 2.0 / Large Model 1.0 / General Engines, each with its own model dropdown), filler-word filtering (No filtering / Partial filtering / Full filtering), punctuation filtering (No filtering / Strip trailing punctuation / Strip all punctuation), number format (Output Chinese numerals / Smart-convert to Arabic numerals), sensitive-word filtering (No filtering / Filter sensitive words / Replace with *), hotword list, self-learning model
- **Alibaba Cloud Model Studio**: filler-word filtering (switch), sentence-break silence (200–6000 ms), hotword list, language hints (e.g. zh / zh,en)
- **iFLYTEK** (LLM version / standard version): engine version switch; filler-word filtering for the LLM version; recognition language (Chinese / Chinese-English, English), punctuation filtering, filler-word filtering and vertical domain (court/edu/finance/medical/tech) for the standard version
- **Volcengine** (Doubao 1.0 / 2.0): billing mode for each generation (Billed by duration / Billed by concurrency), disfluency removal, secondary recognition
- **FunASR local**: model, device (Auto / CPU / GPU CUDA), recognition window (Default 600 ms / Low latency 480 ms / High accuracy 720 ms), hotwords, local model directory (path to weights you downloaded yourself; the advanced fields `funasr.model_hub` / `funasr.model_revision` are only available in the config file)

> The packaged build (exe) does not include the local engine dependencies, so this card only shows a single "unavailable" note; run from source for local recognition.

### 10.4 AI Correction

| Setting | Description |
| --- | --- |
| Enable AI Correction | Master switch |
| Type after correction | `wait_for_correction`, see §7.3 |
| Include history context | Sends the last 15 history entries to the AI along with the correction request as context |
| Request timeout | 1000–10000 ms, default 3000 |
| Disable thinking mode | `disable_thinking` |
| Batch size | 1–10, default 3 |
| Batch idle trigger | 500–10000 ms, default 1000 |
| Custom prompt | Version dropdown + multi-line editor + New/Save/Delete versions; built-in versions cannot be modified |

The API address / key / model and the connectivity test are configured under "General → Shared AI Endpoint" (shared with the "帮我" voice command); this page only covers correction behavior.

### 10.5 Voice Commands

| Group | Setting | Description |
| --- | --- | --- |
| Trigger and Commands | Trigger word | Default "听我说" |
| Trigger and Commands | Command hotkey | Default `Ctrl+Shift+G`; leave empty to disable |
| Trigger and Commands | AI command prefix | Default "帮我" |
| Trigger and Commands | AI command thinking mode | `ai_thinking` |
| Trigger and Commands | AI command prompt | Multi-line editor with the `{selection}` / `{command}` placeholders |
| Trigger and Commands | Custom phrases | Switch that shows or hides the phrase list editor |
| Trigger and Commands | Standby timeout | 3–30 seconds, default 8 |
| Command reference | Expandable / collapsible | Phrase-to-effect table (generated automatically from the command table, in sync with the matching logic) |
| Custom phrase list | Table + Add / Delete | Phrase name / phrase content |

### 10.6 Recording

| Group | Setting | Description |
| --- | --- | --- |
| Recording | Always-on mic | `audio.warmup_capture` switch; does not swallow the first word |
| Recording | Microphone | Dropdown that enumerates WASAPI devices live, supports hot-plug, with "System default" pinned at the top |
| Recording | Auto-stop | 0–300 seconds, default 10 (0 = never stop automatically) |
| Recording | Max recording length | 0–3600 seconds, default 60 (0 = no limit) |
| Voice Activity Detection (VAD) | Speech threshold | 50–5000, default 500 |
| Voice Activity Detection (VAD) | Sentence-break wait | 200–3000 ms, default 600 |
| Voice Activity Detection (VAD) | Minimum speech | 100–2000 ms, default 300 |

### 10.7 Voice Mode

Settings grouped by section (see Chapter 9 for detailed usage and examples). The default provider is **Doubao S2S**; switching the "Dialog provider" swaps the whole field group below (the Doubao / Qwen / Hermes groups show and hide mutually exclusively):

| Group | Setting | Description |
| --- | --- | --- |
| Connection | Enable Voice Mode | Master switch: when off, the menu item, the dialog hotkey and the floating bar's dialog entry are all removed (this page stays available so you can turn it back on); takes effect immediately |
| Connection | Dialog provider | Doubao S2S (default) / Alibaba Cloud Model Studio Qwen-Audio / Hermes Agent (self-hosted); the field groups below show or hide accordingly |
| Connection | Dialog hotkey | Default `Ctrl+Shift+H`; empty = the hotkey is simply not registered (the menu and floating bar entries remain). A restart is required |
| Connection | API Key / App ID / Access Token | Doubao: leave empty to automatically reuse the Volcengine config from the "Engine Keys" page |
| Connection | Test connection | One full handshake that verifies the service is enabled and the key is valid; on success Doubao shows `logid=<value>` and Qwen shows `session=<id>`, while Hermes only gives a neutral message (for the diagnostic script see §9.10) |
| Model and persona | Model version | O2.0 general chat / SC2.0 role-play (Doubao) |
| Model and persona | Bot nickname / Persona description / Speaking style | O version only (Doubao) |
| Model and persona | Character description | SC version only (mutually exclusive with the three fields above), multi-line editor (Doubao) |
| Voice and playback | Voice | Dropdown with 7 official voices; cloned voices can be typed in directly (Doubao) |
| Voice and playback | Speech rate / Volume | [-50,100], 2.0 version only (Doubao) |
| Voice and playback | Playback device | Dropdown that enumerates output devices live (which device the AI voice plays from); shared by Doubao and Qwen |
| Audio in and turn detection | Turn-detection window | 500–5000 ms, default 800 (larger values can be set by editing the config file directly) (Doubao) |
| Audio in and turn detection | Half-duplex mode | Pauses audio input while the AI is speaking (prevents self-interruption on speakers); shared by Doubao and Qwen (Hermes is always half-duplex) |
| Session behavior | Voice exit / Cross-session memory / Auto-reconnect / Strict review | 4 switches, all on by default (Cross-session memory applies to Doubao only) |

**Field group when provider = Alibaba Cloud (Qwen)** (`dialog.qwen`; shows or hides mutually exclusively with the Doubao group above):

- **Qwen connection**: region (legacy public endpoint / beijing / singapore dedicated endpoints), WorkspaceId (required under the new domain), API Key (leave empty to automatically reuse the Alibaba Cloud config from the "Engine Keys" page)
- **Qwen model and persona**: model (Plus / Flash), voice (5 official dragon-series voices), system instructions (persona / style)
- **Qwen audio in and turn detection**: turn detection (server_vad with tunable threshold and silence duration / smart_turn with automatic model-based turn detection), VAD threshold (-1.0 to 1.0), turn-detection silence (200–6000 ms)
- **Qwen session behavior**: speech emotion, history turns (1–50), web search (Plus/Flash only)

**Field group when provider = Hermes (self-hosted)** (`dialog.hermes` + `dialog.asr`; shows or hides mutually exclusively with the two groups above):

- **Hermes connection**: server address (`http://host:port`; a /v1 suffix entered by mistake is stripped automatically), API Key (the `API_SERVER_KEY` in `~/.hermes/.env` on the server), model name (reserved field, not sent with the request in the current version), session title (empty = the server session has no title), recognition tolerance hint (system_hint), connection timeout, turn timeout
- **Dialog-specific recognition**: dialog recognition engine (empty = follow the top-level "Recognition Engine"; Tencent Cloud / Alibaba Cloud available), dialog model tier, language, hotwords (see §9.10 for what these fields mean)

### 10.8 About

- Header card: rounded icon + "Open-RealtimeASR-UI" + version number
- Collapsible cards:
  - **About**: what the app is and how it works
  - **Open-source components**: CPython / PySide6 / sounddevice / PortAudio / CFFI / numpy / keyboard / pyperclip / websockets / PyYAML / FunASR / PyTorch and their licenses
  - **Contact**: Bilibili space updates (opens the link)
  - **Logs & troubleshooting**: log retention in days (1–365), open log folder, clear logs

## 11. Themes and Personalization

### 11.1 Built-in Themes

| Theme | Colors | Characteristics |
| --- | --- | --- |
| Light | bg `#f7f7f8`, text `#1d1d1f`, accent `#007aff` | Bright and clean |
| Dark | bg `#1c1c20`, text `#eeeeee`, accent `#5696e8` | Dark color scheme |
| Minimal | — | Square floating window + centered voiceprint waveform + bubble text above; the whole window is the start/stop button (default) |

How to switch: right-click menu → Theme, or `config.yaml` → `ui.theme_name` (takes effect after a restart).

### 11.2 Extension Theme Packs

> Beyond the built-in themes, external theme packs are supported: drop a pack into the `themes/` directory and it takes effect after a restart — no code changes needed.

| Theme pack | Characteristics |
| --- | --- |
| Cyberpunk (赛博朋克风) | Deep purple-black background `#0d0d1a`, neon cyan text `#00f0ff`, alert-red accent `#ff2a5f`, bright yellow top line; thin neon border + cut-off bottom-left corner |
| Celtic (凯尔特风格) | White text on a black background; the start/stop button is a Celtic knot (with a rotating animation while recording), chain-pattern border |

How theme packs work:

- Structure: `themes/<pack name>/theme.yaml` (bg / text / accent are required); a pack takes effect as soon as it is dropped in
- When a feature module is missing, that theme is skipped automatically and app startup is unaffected
- A theme whose name collides with a built-in one is skipped
- In the packaged build the themes directory sits next to the exe; in the source build it is at the project root
- The lite build (`build.bat lite`) physically excludes the DLC themes

### 11.3 Font Settings

Three ways to set the font (in descending priority; you can also switch at any time while running via the right-click menu "Choose font file"):

1. `font_file`: absolute path to any `.ttf` / `.otf` file (a font that is not installed in the system is recommended)
2. `font_family`: the family name of a font already installed in the system (e.g. `Microsoft YaHei UI`)
3. Neither filled in: the built-in default font is used (Alibaba PuHuiTi 3.0 Medium; falls back to the Qt default font when the font file is missing)

### 11.4 Floating Bar Appearance

| Item | Description |
| --- | --- |
| Position and size | Drag with the left button to move, resize with the bottom-right handle; persisted (saved to `state.json` in the state directory, not written to config.yaml) |
| Opacity | `ui.bar_opacity`: 0 = theme default (Light 82% / Dark 92%), 1–100 = percentage; the slider on the settings page previews it live |
| Always on top | `ui.always_on_top`, on by default; when off, other windows may cover the floating bar |
| Follow cursor | `ui.follow_cursor`, off by default |
| Auto-hide | Hides to the system tray after the idle timeout (`ui.auto_hide_seconds`); default 0 = never hide |
| Text size | `ui.font_size_bar` (floating bar) / `ui.font_size_app` (app default for menus and similar) |

## 12. Configuration Reference

### 12.1 Config File Lookup Order

The config file is in YAML format. Lookup order:

**Packaged build (frozen)**:

1. `config.yaml` next to the exe
2. `config/config.yaml` next to the exe

**Source build**:

1. `config/config.yaml` at the project root
2. `config.yaml` at the project root

**Common fallbacks**:

3. `config/config.yaml` in the current working directory
4. `config.yaml` in the current working directory
5. `~/.asr_voice/config.yaml` in the user home directory

Generated automatically on first run: when no config file exists, the built-in template is copied — the packaged build generates it next to the exe first (falling back to `~/.asr_voice/config.yaml` when that is not writable), and the source build generates it at `config/config.yaml` (already gitignored).

Loading and merging: `defaults (defaults.py) ← config.yaml (deep merge + type normalization)`.

State directory (`paths.state_dir`): `<exe directory>/.asr_voice/` for the packaged build, `~/.asr_voice/` for the source build — it holds `history.json`, `state.json`, `phrases.json`, `prompts.json` and `menu_icons/`. The packaged build and the source build do not share state.

### 12.2 Common Config Fields

For the complete field list and comments see [config/config.example.yaml](../config/config.example.yaml). Common settings:

| Field | Default | Description |
| --- | --- | --- |
| `engine` | `aliyun` | Current engine: `aliyun` / `tencent` / `xfyun` / `volcengine` / `funasr` |
| `aliyun.model` | `paraformer-realtime-v2` | General engine tier (default); the "Large Model" tier is fixed to `qwen-audio-3.0-asr-flash-streaming` (more accurate; switch it from the right-click menu) |
| `funasr.chunk_preset` | `默认` | Local recognition window: `默认` (Default) 600 ms / `低延迟` (Low latency) 480 ms / `高准确` (High accuracy) 720 ms |
| `funasr.local_dir` | empty | Local model directory (path to weights you downloaded yourself); when non-empty, models load locally without downloading anything over the network |
| `funasr.model_hub` | `ms` | Download source: `ms` = ModelScope / `hf` = HuggingFace (only applies when downloading automatically) |
| `funasr.model_revision` | empty | Pins the model version (e.g. `apache-2.0-20260804`); leave empty to use the latest |
| `audio.sample_rate` | `16000` | Sample rate; all engines require 16 kHz |
| `audio.warmup_capture` | `false` | Always-on mic warm-up: does not swallow the first word (the taskbar microphone icon stays lit) |
| `audio.input_device` | empty | Microphone device name (substring match by sounddevice); empty = the system default input device |
| `vad.energy_threshold` | `500` | Energy threshold; anything below this value counts as silence |
| `vad.silence_duration_ms` | `600` | Continuous silence longer than this marks the end of a sentence |
| `hotkey.toggle` | `ctrl+g` | Global hotkey that starts recording |
| `recording.auto_stop_seconds` | `10` | Stops automatically when no speech is detected (0 = never stop) |
| `recording.max_session_seconds` | `60` | Hard timeout for a single recording (0 = no limit) |
| `recording.live_intermediate` | `true` | Intermediate results are typed live (see §5.3 for the logic locks) |
| `injection.method` | `simulate_keys` | Injection method: `simulate_keys` (simulated keystrokes) / `clipboard_paste` (clipboard paste) |
| `injection.keep_clipboard` | `false` | `true` = the recognized result stays on the clipboard; `false` = the previous clipboard content is restored after pasting |
| `injection.game_mode` | `false` | Game mode: forces clipboard paste injection (never falls back to simulated keystrokes, and the clipboard is not restored after pasting) |
| `ui.theme_name` | `极简` | Built-in Light / Dark / Minimal; for extension themes see the theme packs in `themes/` |
| `ui.engine_order` | `aliyun,volcengine,xfyun,tencent,funasr` | Group order in the right-click Recognition Engine menu (adjustable in the settings window) |
| `ui.auto_hide_seconds` | `0` | How long the bar may stay idle before hiding to the system tray (0 = never auto-hide) |
| `history.enable` | `true` | History switch: when on, results are saved to the local history and can be copied from there |
| `llm.enable` | `false` | AI correction switch (off by default, opt-in) |
| `llm.wait_for_correction` | `false` | `true` = wait for the LLM correction and type the corrected version directly (no jumping, 1–3 seconds of delay per sentence) |
| `commands.enable` | `false` | Master switch for voice commands (mutually exclusive with Live typing) |
| `commands.prefix` | `听我说` | Trigger word |
| `commands.standby_seconds` | `8` | Seconds to stay in standby after saying only the trigger word |
| `dialog.enable` | `true` | Master switch for the voice dialog: when off, the menu item, the dialog hotkey and the floating bar's dialog entry are all removed (the settings page remains so you can turn it back on) |
| `dialog.provider` | `doubao` | Voice dialog provider: `doubao` = Doubao S2S / `aliyun` = Alibaba Cloud Model Studio Qwen-Audio Realtime / `hermes` = Hermes Agent (plain text, see §9.10) |
| `hotkey.dialog` | `ctrl+shift+h` | Hotkey that enters / exits the voice dialog (empty = the hotkey is simply not registered; use `dialog.enable` to turn the whole feature off) |
| `dialog.model` | `1.2.1.1` | Dialog model (Doubao): O2.0 general chat (default) / `2.2.0.0` SC2.0 role-play |
| `dialog.speaker` | `zh_female_vv_jupiter_bigtts` | AI playback voice (Doubao); for SC2.0 cloned voices type the name directly |
| `dialog.end_smooth_window_ms` | `800` | Turn-detection window (Doubao): how long a silence counts as the end of a sentence (see §9.4) |
| `dialog.half_duplex` | `false` | Half-duplex: pauses audio input while the AI is speaking (prevents self-interruption on speakers); shared by Doubao and Qwen (Hermes is always half-duplex) |
| `dialog.qwen.model` | `qwen-audio-3.0-realtime-plus` | Qwen dialog model: `plus` (stronger) / `flash` (faster) |
| `dialog.qwen.turn_detection` | `server_vad` | Qwen turn detection: `server_vad` (tunable threshold / silence) / `smart_turn` (automatic model-based turn detection) |
| `dialog.qwen.enable_search` | `false` | Qwen web search (plus/flash models only) |
| `dialog.hermes.base_url` | empty | Address of the self-hosted Hermes API server (e.g. http://192.168.0.242:8642, without /v1) |
| `dialog.hermes.api_key` | empty | This is the `API_SERVER_KEY` in `~/.hermes/.env` on the server |
| `dialog.asr.engine` | empty | Dialog-specific recognition engine: empty = follow the top-level `engine`; `tencent` / `aliyun` are available (consumed only by the Hermes dialog path) |
| `ui.engine_show_*` | `true` | Shows or hides a whole vendor group in the right-click Recognition Engine menu (e.g. `engine_show_tencent: false` hides the entire Tencent Cloud group) |
| `ui.engine_hidden_models` | empty | Comma-separated engine IDs to hide individually from the right-click menu (empty = show all) |
| `logging.retention_days` | `30` | Log retention in days (rotated daily, cleaned up at startup) |

> Hotkey implementation: combinations with a modifier key (Ctrl/Alt/Shift/Win + a main key) use the native Windows `RegisterHotKey` API — no hook is installed and the modifier state is never altered, so there is no modifier sticking (the "the system still thinks Shift is held after you tapped it once" problem). When a combination is already taken by another program, registration fails and you are prompted to choose another one. Only single plain keys (Backspace/Delete, used by the Delete-key interrupt) go through a keyboard hook, and that hook only listens — it never intercepts.

## 13. FAQ and Troubleshooting

### 13.1 The app won't start

Possible causes and checks:

- **Missing dependencies in the source build**: confirm you have run `pip install -r requirements.txt` and that the Python version is ≥ 3.10
- **Corrupted config file**: delete/back up `config.yaml` and restart — it is regenerated automatically; the packaged build (exe) shows a pop-up at startup reporting that the config failed to load (exit code 3)
- **Core module import failure**: for the source build, confirm the `asr_voice` package structure is complete (exit code 4)
- **Double-clicking the exe does nothing**: check the logs under `logs/` in the same directory as the exe; confirm the directory containing the exe is writable (when it is not, the config falls back to `~/.asr_voice/config.yaml`)
- **Blocked by security software**: some security programs block keyboard hooks and tray applications; add the app to the trust list and try again

### 13.2 Hotkeys don't work or conflict

- Hotkeys with modifier keys are registered through the native Windows `RegisterHotKey` API and **do not require administrator privileges** (combinations including the Win key work as well)
- When a hotkey is already taken by another program (including the system), registration fails and you are prompted to change it; simply pick another combination as prompted (a failed recording hotkey shows a pop-up and the app exits with exit code 3)
- After changing a hotkey, restart the app for it to take effect
- Avoid conflicts with system / common software global hotkeys (such as the system's own Win+H)

### 13.3 Recognition produces no text output

Troubleshoot in the following order:

1. **API keys**: confirm the key for the current engine is filled in correctly (Settings → Engine Keys); when the key is wrong, the floating bar shows an error message
2. **Network**: confirm the vendor's service is reachable (corporate networks/firewalls may block it)
3. **Microphone**: Settings → Recording, select the correct device; confirm the system microphone permission is granted; watch whether the status dot turns red while recording (if it does not, no audio is being captured)
4. **VAD threshold**: speech quieter than the detection threshold is treated as silence; lower the "Speech threshold" as needed
5. **Engine version**: switch to another engine to determine whether it is an engine-specific problem or a general one
6. **Logs**: check `logs/asr_voice.log` for error messages (packaged build: `logs/` in the same directory as the exe; source build: `logs/` at the project root)

### 13.4 The first word is swallowed / words are missing

- Confirm "Always-on mic" (`audio.warmup_capture`) is enabled — the pre-roll buffer keeps about 400 ms of audio from before the hotkey was pressed, so the first word is not swallowed
- If Always-on mic is off, pausing briefly after pressing the hotkey before you start speaking helps
- Simulated keystrokes can drop characters in some programs; switch to clipboard paste (WeChat/WeCom already switch automatically)
- The sentence-break parameters of engines such as iFLYTEK / Alibaba Cloud affect the rhythm of intermediate results; adjust "Sentence-break silence" as needed

### 13.5 AI correction doesn't respond

- **First confirm it is actually enabled**: when enabled, every sentence should show a three-state hint on the floating bar (see §7.6); no hint at all = not enabled — check `llm.enable` and `api_key`
- If it shows "AI correction failed (original kept)": check `base_url` / `model` / `api_key` / network; the timeout (default 3 seconds) can be raised via `timeout_ms`
- A custom prompt must contain the `{text}` placeholder, otherwise the LLM never receives the text to correct
- Local Ollama users: confirm the Ollama service is running and `base_url` is `http://localhost:11434/v1`

### 13.6 Voice commands don't work

- Confirm "Voice commands" is checked in the right-click menu (`commands.enable=true`)
- The trigger word must be at the **start of the sentence**; the continuous-speech form is "trigger word + command" in one step
- If the trigger word is recognized as a homophone, AI correction must be on for the second-pass correction to detect it; otherwise speak more slowly and enunciate more clearly
- Mutually exclusive with "Live typing": enabling voice commands automatically turns Live typing off — this is normal
- AI commands (the "帮我" type) require `llm.api_key` to be configured

### 13.7 The clipboard gets overwritten

- "Keep clipboard" is off by default: the original clipboard content is restored automatically after pasting
- With "Keep clipboard" on, the recognition result occupies the clipboard (overwriting images/files); press Ctrl+V to paste it again at any time
- Copying an image and then immediately using voice input, causing the image to be lost: this is usually because "Keep clipboard" is on — turn it off in the right-click menu to avoid it

### 13.8 FunASR-related issues

- **FunASR cannot be selected in the packaged build**: the packaged build does not include the local engine; run from source instead (see §3.3)
- **Dependencies not installed**: the right-click menu shows "(not installed)"; run `pip install funasr torch torchaudio`
- **First recognition is slow**: the model is downloaded automatically on first use (`~/.cache/modelscope/`); download it in advance in offline environments
- **Slow startup**: `preheat=true` warms up the model at startup (3–8 seconds on CPU); turn it off to load the model only at the first recording instead
- **GPU environment**: setting `device` to `cuda` is faster; set it to `cpu` when there is no GPU
- The local engine's recognition capability trails cloud large models (there is a note under Settings → About → Local offline engine)

### 13.9 Text cannot be typed in games

Simulated keystrokes are based on synthesized key events without scan codes (`KEYEVENTF_UNICODE`); the input pipelines of self-drawn input boxes in games (Raw Input / DirectInput / scan codes) do not read such events — the log shows "injection start/complete" with no failure records, yet no text appears in the game.

- Enable right-click menu "Injection Logic ▸ Game mode" to force clipboard paste; the text is typed as long as the game's chat box supports pasting
- **The target game runs as administrator** (common with anti-cheat clients): Windows UIPI privilege isolation silently discards synthesized input sent by a normal-privilege process; manual key presses are unaffected. Three elevation options inside the app: ① when enabling "Game mode", answer the floating bar prompt with "Elevate now" (if Game mode is kept across restarts, you are asked again after startup); ② right-click menu "Behavior ▸ Run as administrator" (one-time; after the UAC prompt, the elevated instance takes over); ③ "Always start as administrator" at the top of the Settings "General" page (creates a logon scheduled task; every later launch runs as administrator without a UAC prompt). You can also right-click the exe in File Explorer and choose "Run as administrator". To verify, check the "Elevated" column of the target process on the "Details" tab in Task Manager; the "privilege gap" field of the "game mode sending Ctrl+V" log line captures the same fact at that moment (True = the target is elevated, and synthesized input is guaranteed to be discarded)
- If pasting does not work either, the target input box does not even support clipboard paste, and there is no general solution for now
- Note: Backspace is equally ineffective in games, and AI correction replacement is unavailable in game mode

## 14. Appendix

### 14.1 Glossary of common terms

| Term | Description |
| --- | --- |
| ASR | Automatic Speech Recognition |
| VAD | Voice Activity Detection (decides whether there is speech or silence) |
| Live typing | The mechanism that writes intermediate (not yet sentence-broken) results into the input box in real time |
| Injection | The process of writing recognized text into the target input box |
| LLM | Large Language Model (used for AI correction and AI commands) |
| OpenAI-compatible protocol | A common set of LLM HTTP API conventions, supported by OpenAI / DeepSeek / Ollama and others |
| DDC | Semantic smoothing (Volcengine): removes filler words / repeated words |
| Secondary recognition | Volcengine nonstream: after about 800 ms of silence, produces a more accurate final result |
| Hotwords | A preset list of proper nouns that improves recognition accuracy for specific terms |
| Warm-up | Loading the model in the background when the app starts, to shorten first-recognition latency |
| System tray | The notification area on the right side of the Windows taskbar |
| S2S | Speech-to-Speech: end-to-end voice conversation (speak → AI understands → voice reply) |
| barge-in | Interrupting by speaking: start talking while the AI is speaking to interrupt it and take over the conversation |
| Provider | The service backend of a voice conversation, one of three: Doubao S2S / Alibaba Cloud Qwen-Audio / self-hosted Hermes Agent |

### 14.2 Quick reference of default values

| Item | Default |
| --- | --- |
| Default engine | Alibaba Cloud Model Studio (Paraformer v2) |
| Recording hotkey | `Ctrl+G` |
| Push-to-talk / tap threshold | Off / 300 ms (see §5.2) |
| Command hotkey | `Ctrl+Shift+G` |
| Dialog hotkey | `Ctrl+Shift+H` |
| Voice command trigger word | 听我说 |
| AI command prefix | 帮我 |
| Injection method | Simulated keystrokes (keep clipboard off) |
| Auto-stop on no speech | 10 s |
| Max recording length | 60 s (0 = no limit) |
| Standby timeout | 8 s |
| Dialog turn-detection window / half-duplex | 800 ms / off |
| Dialog provider / dialog model | Doubao S2S / General chat (O2.0) (1.2.1.1) |
| VAD threshold / sentence break / minimum speech | 500 / 600 ms / 300 ms |
| History | On, 500 entries |
| AI correction | Off (default LLM: DeepSeek) |
| Theme | Minimal |
| Log retention | 30 days |
| Always-on mic warm-up | Off |
| Backend port/password | None (this app is a pure desktop application and provides no web backend) |

### 14.3 Exit codes

| Exit code | Meaning |
| --- | --- |
| 0 | Normal exit |
| 1 | Uncaught exception |
| 2 | Usage error (command-line arguments) |
| 3 | Config load failure / hotkey registration failure |
| 4 | Core module import failure |
| 5 | Runtime exception |

Command-line arguments: `--config <path>` (specify the config file), `--hidden` (silent start, system tray only), `--replace` (takeover mode: when restarting elevated or toggling autostart, tells the old instance to exit, then takes over once the single-instance lock is released).

### 14.4 Reference links

- Project repository: see [README.md](../README.md)
- Config template: [config/config.example.yaml](../config/config.example.yaml)
- Alibaba Cloud Model Studio console: https://bailian.console.aliyun.com
- Alibaba Cloud Qwen-Audio Realtime documentation: https://help.aliyun.com/zh/model-studio/qwen-asr-realtime-interaction-process
- Tencent Cloud API key management: https://console.cloud.tencent.com/cam/capi
- Tencent Cloud ASR console: https://console.cloud.tencent.com/asr
- iFLYTEK real-time speech transcription: https://console.xfyun.cn
- Volcengine speech console: https://console.volcengine.com/speech

## License and Disclaimer

This project is open-sourced under the [Apache License 2.0](../LICENSE).

- The cloud recognition engines (Alibaba Cloud Model Studio / Tencent Cloud / iFLYTEK / Volcengine Doubao) are the official services of their respective vendors; before use you must obtain your own API keys and **activate them at your own expense** under each vendor's billing rules, and any costs and compliance responsibilities incurred are borne by the user.
- The local FunASR engine is fully offline and free: the toolkit code is MIT and the `paraformer-zh-streaming` model weights are Apache-2.0 (subject to the official model card), with copyright owned by the FunASR / ModelScope parties. This repository does not bundle or distribute the model weights; users download them themselves. To redistribute the weights commercially, pin `funasr.model_revision` and comply with Apache-2.0.
- This project calls the services above through their official APIs; vendor API changes may make the corresponding engine temporarily unavailable — refer to each vendor's latest documentation.
- For the complete license list, copyright attributions, and commercial-compliance notes of third-party components (PySide6 / sounddevice / FunASR, etc.), see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) in the repository.
