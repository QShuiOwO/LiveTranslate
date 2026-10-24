# LiveTranslate 项目交接与 Windows 11 26H2 移植指南

> 文档日期：2026-10-24  
> 实现基线：`a83e9e19e8d12df72edb1b98447bb44d1ffefb0f`，应用版本 `0.2.0`  
> 已验证平台：Ubuntu 24.04、AMD RX 9070 XT、Vulkan、Qt xcb / XWayland  
> 移植目标：同一硬件上的 Windows 11 26H2 x64  
> **Windows 版本尚未实现和实机验收。本文中的 Windows 方案、示例配置和实施阶段均为交接建议，不能视为已有功能。**

## 1. 接手时先了解的结论

LiveTranslate 捕获电脑正在播放的声音，将语音识别为原文，再使用本地大模型翻译成简体中文，最后显示双语字幕。它不调用云端推理 API，目前不提供麦克风模式。

当前默认链路是：

```text
系统播放音频
  → 输出设备回采
  → 16 kHz / 单声道 / PCM16 音频帧
  → VAD 分段
  → speech.cpp + Qwen3-ASR-1.7B Q8_0
  → llama.cpp + HY-MT2-7B Q4_K_M
  → Qt 双语字幕窗口
```

必须区分三个概念：

- **模型**是 GGUF 权重；**推理引擎**是 speech.cpp / llama.cpp；**LiveTranslate**负责音频、调度、语言配置、字幕状态和界面。
- Qwen3-ASR 当前由 **speech.cpp** 加载，不是 whisper.cpp。whisper.cpp 保留给 Small.en 回退方案。
- 当前实时功能是应用层“连续采集 + 有界语音片段 + 重复临时识别 + 最终确认”，不等同于 Qwen 官方原生流式解码。

Windows 移植的可行性较高：现有模型权重和主要调度逻辑可以保留，主要新增工作是 WASAPI 回采、平台工厂、Windows 原生程序配置、启动与资源监控。不要只删除 Linux 平台检查就宣称支持 Windows。

接手阅读顺序建议：本文 → [README](../README.md) → [M2 架构](M2_ARCHITECTURE.md) → [语言切换与实测](LANGUAGE_SWITCH.md) → 对应源码和测试。早期 M0/M1、M2 报告包含历史版本，不能把其中的旧模型、默认值或长测结论套用于当前实现。

## 2. 已实现范围与验证边界

| 能力 | 当前状态 |
| --- | --- |
| Linux 系统播放音频回采 | 已实现，默认跟随系统当前输出，也可显式指定 monitor |
| 文件与批次翻译 | 已实现，保留 M1 `demo --input` 和定时采集流程 |
| 连续采集、VAD、异步推理、有界队列 | 已实现 |
| 临时/最终字幕、上下文翻译、历史与语言切换 | 已实现 |
| 双框界面、单框纯字幕、拖动与缩放 | 已实现，Ubuntu 桌面已验证 |
| 源语言 | 英语、日语、韩语、法语、德语、西班牙语、意大利语、俄语 |
| 目标语言 | 当前固定简体中文 `zh-CN` |
| GPU | 当前连续模式要求 Vulkan；没有新增 HIP 适配 |
| Windows 音频回采、启动器、诊断与打包 | 尚未实现 |
| 麦克风、自动切换模型、无限上下文 | 不在当前范围 |

最近一次完整测试为 **344 项通过，包含 3 项真实本地模型回归**。六种新增语言各用一段真人录音验证，并完成约 **97.08 秒**的真实 HDMI 连续切换，**7/7** 最终双语字幕有可见窗口绘制。

只有早期 **Whisper Small.en + Qwen3-4B** 组合完成过 30 分钟验收。当前 **Qwen3-ASR + HY-MT2**、新语言和后续界面版本以短测为主，不能宣称已经通过同等长期验证。识别成功也不代表逐字正确：韩语样本有近音字误识别，日语曾出现专名误识别。详细证据和失败记录见 [语言报告](LANGUAGE_SWITCH.md) 与 [M2 报告](M2_REPORT.md)。

## 3. 技术栈、版本和模型资产

### 3.1 当前开发环境

| 项目 | 基线 |
| --- | --- |
| CPU / GPU / RAM | Ryzen 7 9700X / Radeon RX 9070 XT 16GB，gfx1201 / 32GB |
| Python | 项目要求 3.11+；交接时项目 Conda 环境为 3.12.15 |
| GUI | PySide6-Essentials 6.12.0、shiboken6 6.12.0 |
| VAD | webrtcvad-wheels 2.0.14.post1 |
| 进程资源统计 | psutil 7.2.2 |
| Linux 音频 | PipeWire / PulseAudio 兼容接口、pactl、FFmpeg |
| 配置与测试 | TOML、pytest、Git |

精确 Python 依赖见 [live-requirements.lock](../config/live-requirements.lock)。[conda-linux-64.lock](../config/conda-linux-64.lock) 仅用于 Linux，不能用于创建 Windows 环境。

### 3.2 原生引擎

| 引擎 | 用途 | 固定提交 |
| --- | --- | --- |
| speech.cpp | 当前 Qwen3-ASR 文件识别与常驻 HTTP 服务 | `d074e1b9d84fe5ec530c959292ffe3d94ffd288b` |
| speech.cpp 的 ggml 子模块 | 当前 ASR 张量计算与 Vulkan 后端 | `cbdeccfd49ec9c702c175c273d36c47520cf4710` |
| llama.cpp | 当前 HY-MT2 翻译服务，也支持旧翻译模型 | `f5027fb21307eb264aed483242603d68c87b0fe3` |
| whisper.cpp | Small.en 回退；版本 v1.9.5 | `d1be6fde11ac6e0407606b4e42fe72d34add8037` |

上游地址和版本以 [native-lock.json](../config/native-lock.json) 为准。迁移时优先保持这些提交；若使用不同的 Windows 发布包，需要记录其实际提交、依赖和校验和，并重新验证模型兼容性，不能仅凭版本名称相同就视为相同产物。

### 3.3 默认权重

| 用途 | 文件 | 大小（字节） | 来源 |
| --- | --- | ---: | --- |
| ASR | `Qwen3-ASR-1.7B-Q8_0.gguf` | 2,176,109,216 | `sakasegawa/Qwen3-ASR-1.7B-GGUF`，speech.cpp 专用 layout 1 转换件 |
| 翻译 | `Hy-MT2-7B-Q4_K_M.gguf` | 4,624,648,896 | `tencent/Hy-MT2-7B-GGUF` |

SHA-256：

```text
Qwen3-ASR-1.7B-Q8_0.gguf
5f219b78a1d9c3b9e97da27708b36f8a0bc1bfc1650b541c0a6dbaf87c9a62d0

Hy-MT2-7B-Q4_K_M.gguf
9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b
```

下载 revision、URL 和上游模型信息以 [model-lock.json](../config/model-lock.json) 为准。权重可以跨操作系统复制，但必须核对校验和；Linux 可执行文件、动态库和 Conda 环境不能这样复用。

手动回退配置为 [whisper-qwen.toml](../config/whisper-qwen.toml)，使用 Small.en + Qwen3-4B-Instruct-2507 Q4_K_M。Small.en 仅支持英语；失败时不会静默切换模型，也不会自动回退 CPU。M1 可显式指定 CPU，当前 M2 连续模式仍要求 Vulkan。

## 4. 仓库组成和职责

采用 `src` 布局，入口为 `python -m livetranslate`；安装后也提供 `livetranslate` 命令。

| 位置 | 职责 |
| --- | --- |
| [cli.py](../src/livetranslate/cli.py) | 命令解析、配置覆盖；`demo/capture/transcribe/translate/devices/serve/live` |
| [config.py](../src/livetranslate/config.py) | 配置数据类、TOML 加载、取值校验、路径处理和本地服务地址限制 |
| [languages.py](../src/livetranslate/languages.py) | 八种源语言注册表、语言校验、英语专用权重限制 |
| [interfaces.py](../src/livetranslate/interfaces.py) | M1 音频、ASR、翻译协议接口 |
| [results.py](../src/livetranslate/results.py)、[errors.py](../src/livetranslate/errors.py) | 结构化结果、错误代码、JSON 原子保存 |
| [audio/linux.py](../src/livetranslate/audio/linux.py) | monitor 枚举、默认输出选择、蓝牙连接状态、批次采集 |
| [audio/continuous.py](../src/livetranslate/audio/continuous.py) | Linux 连续 FFmpeg 采集、管道读取、重连和帧时间戳 |
| [audio/wav.py](../src/livetranslate/audio/wav.py) | WAV 格式、时长、完整性、静音检查 |
| [backends/factory.py](../src/livetranslate/backends/factory.py) | 按配置选择文件 ASR 后端 |
| [backends/qwen_asr.py](../src/livetranslate/backends/qwen_asr.py)、[backends/whisper.py](../src/livetranslate/backends/whisper.py) | 真实文件识别、结果解析、GPU 日志证据 |
| [backends/llama.py](../src/livetranslate/backends/llama.py) | 本地 Chat Completions、翻译提示、上下文与输出校验 |
| [pipeline.py](../src/livetranslate/pipeline.py) | M1 批次/文件 `TranslationPipeline` |
| [streaming/types.py](../src/livetranslate/streaming/types.py) | 音频帧、语音任务、文本任务及连续接口 |
| [streaming/queues.py](../src/livetranslate/streaming/queues.py)、[streaming/vad.py](../src/livetranslate/streaming/vad.py) | 有界队列、临时任务合并、语音分段 |
| [streaming/engine.py](../src/livetranslate/streaming/engine.py) | 采集/VAD/ASR/翻译线程、任务调度、语言代次和退出 |
| [streaming/services.py](../src/livetranslate/streaming/services.py) | 自有原生服务管理、健康检查、日志、常驻 ASR 和翻译适配 |
| [streaming/context.py](../src/livetranslate/streaming/context.py)、[streaming/state.py](../src/livetranslate/streaming/state.py) | 翻译上下文、稳定前缀、字幕冻结、UI 事件合并 |
| [streaming/gui.py](../src/livetranslate/streaming/gui.py)、[streaming/gestures.py](../src/livetranslate/streaming/gestures.py) | Qt 呈现、两种布局、历史、语言选择、拖动与缩放 |
| [streaming/runner.py](../src/livetranslate/streaming/runner.py) | 连续模式平台限制、依赖检查、GUI/headless 启动 |
| [streaming/metrics.py](../src/livetranslate/streaming/metrics.py)、[telemetry.py](../src/livetranslate/telemetry.py) | 事件、时延、进程资源和 Linux AMD 显存采样 |
| [process.py](../src/livetranslate/process.py)、[server.py](../src/livetranslate/server.py) | 有超时的子进程调用、批次翻译服务启动和 GPU 校验 |
| [diagnostics.py](../src/livetranslate/diagnostics.py) | 当前 Linux doctor 审计逻辑 |
| `scripts/` | 构建、下载、诊断、批次/连续验证和性能分析工具 |
| `config/`、`tests/`、`docs/` | 版本锁与配置、自动化测试、设计和验收记录 |

## 5. 两条运行链路

### 5.1 M1：已有 WAV 或定时采集

```text
已有 WAV / AudioCapture.capture()
  → WAV 校验
  → ASRBackend.transcribe()
  → TranslationBackend.translate()
  → PipelineResult + JSON
```

`TranslationPipeline` 组合三个协议接口，文件输入时不需要音频采集器。它是 Windows 首次验证的合适入口：可以先独立确认 GPU 模型和翻译 API，再开发实时回采。

主要接口：

```python
AudioCapture.capture(duration, output, source=None) -> CaptureResult
ASRBackend.transcribe(audio_file) -> ASRResult
TranslationBackend.translate(text, *, context=(), source_language="en") -> TranslationResult
TranslationPipeline.run(input_file=..., ...) -> PipelineResult
```

结果包含成功/错误、模型、耗时和证据；批次完整结果还包含音频文件、时长、源设备、源/目标语言、原文、译文和各阶段耗时。ASR 的成功状态不能替代人工或参考文本的质量核对。

### 5.2 M2：连续字幕

```text
采集线程 → BoundedFrames → VAD 线程 → ASR WorkQueue → ASR 线程
                                                     │
                                                     ├→ CaptionBook → EventBus → Qt 主线程
                                                     │
                                                     └→ TextJob + 上下文快照
                                                             ↓
                                                    Translation WorkQueue
                                                             ↓
                                                        翻译线程
                                                             ↓
                                                    CaptionBook → EventBus → Qt
```

采集、VAD、ASR、翻译各有工作线程，资源监控另行采样。Qt 主线程约每 33 ms 合并事件，推理线程不操作控件。

连续适配器需要实现：

```python
ContinuousAudioSource.frames(stop_event) -> Iterator[AudioFrame]
ContinuousAudioSource.close() -> None
StreamingASR.recognize(pcm, *, language="en") -> str
StreamingASR.close() -> None
```

`AudioFrame` 是 **16kHz、mono、little-endian PCM16、20 ms、320 samples、640 bytes**。`sequence` 用于检测缺帧；设备重连或输出切换改变 `epoch`；语言切换增加 `language_generation`。`SpeechJob` 固定一段音频及其边界、UID、revision、是否 final 和源语言，`TextJob` 固定待翻译文字及此前上下文快照。

### 5.3 关键默认参数

| 参数 | 默认值与意义 |
| --- | --- |
| 音频帧队列 | 100 帧，约 2 秒；满时丢最旧帧 |
| ASR / 翻译队列 | 各 3 个 final + 1 个最新 partial；优先 final，partial 合并 |
| 任务最大排队年龄 | 6 秒，过期跳过并记录 |
| VAD | WebRTC mode 2，加 -52 dBFS 能量门限 |
| 最小语音 / 前导 | 240 ms / 200 ms |
| 停顿结束一段 | 560 ms；最终音频最多保留末尾 160 ms 静音 |
| 临时识别 | 最小窗口 1200 ms，约每 1000 ms 尝试一次 |
| 最长片段 | 10000 ms；强制结束，不重叠音频 |
| 常驻推理请求超时 | 4 秒；服务启动等待默认 30 秒 |
| 上下文 | 最近最多 2 个已确认原文片段，共 800 字符，30 秒音频时间内 |
| 字幕历史 | 默认 80；活动状态/单框控件最多 `history_limit + 4` |
| 缓存 | ASR 32 项；翻译 128 项，包含上下文与采集/语言边界 |
| 指标与日志 | 时延序列每项 10000 条、资源 1200 条；事件 16 MiB/文件、原生日志 8 MiB/文件，均轮转保留 2 份备份 |

上述阈值是工程启发式参数，不是完整语法理解。长句可能在 10 秒边界被拆开；有界队列也意味着持续过载时会明确跳段，不能用无限缓存掩盖落后。

## 6. 字幕、语言和翻译的一致性

- 原文 final 与译文 final 分别确认后冻结。旧 revision、旧段、迟到结果不能覆盖已确认内容。
- 临时译文只在它对应的原文仍匹配当前稳定前缀时保留，避免每次 ASR 更新都清空中文。
- 英语、韩语及新增欧洲语言以词/空格词组比较稳定前缀；日语按字符。新语种不会套用英语词尾裁剪，重音和组合字符先做 NFC 规范化。
- 切换源语言时，立即增加代次并清空当前字幕、历史和翻译上下文；旧队列项和在途结果均在接受结果前校验代次。
- VAD 在首个新语言帧丢弃旧未完成段与前导音频。设备切换/丢帧边界则通过 `epoch`、`continuity` 阻断旧上下文。
- 翻译只参考此前已确认原文，不把当前段、未来段或旧译文放入上下文。每个任务使用不可变快照。
- 慢推理不持有语言切换锁，避免切换时卡住 GUI。

`languages.py` 是语言选择、CLI、校验和翻译提示的共同来源。当前映射为：`en` 英语、`ja` 日语、`ko` 韩语、`fr` 法语、`de` 德语、`es` 西班牙语、`it` 意大利语、`ru` 俄语。自动识别语种尚未作为应用选项开放。

翻译使用 HY-MT 的单条用户指令，并根据源语言插入对应名称；英文提示保留已有回归结果。参数为 `temperature=0`、`top_p=1`、`top_k=1`、`seed=42`、`max_tokens=256`、`cache_prompt=false`。达到 token 上限、返回空内容或额外思考/解释格式均作为失败处理，不伪造成功。

## 7. 服务、GPU 证据和退出行为

| 服务 | 默认地址 | 协议与校验 |
| --- | --- | --- |
| speech.cpp / Qwen3-ASR | `127.0.0.1:8083` | `/v1/audio/transcriptions`，multipart WAV，携带语言与模型；完成标记 `X-Speech-Stop: complete` |
| llama-server / HY-MT2 | `127.0.0.1:8080` | `/health`、`/v1/models`、`/v1/chat/completions`；核对 alias `Hy-MT2-7B-Q4_K_M` |
| whisper-server 回退 | `127.0.0.1:8082` | `/inference`；与 speech.cpp 字段不同，不能混用适配器 |

所有推理都在本机。HTTP 客户端使用数字回环地址、禁用环境代理、拒绝重定向；模型下载是独立的公开仓库下载行为。端口暴露到其他网卡不属于本项目默认配置。

`NativeService` 管理自有模型进程、日志线程、健康检查和按需恢复。只有已有健康的 llama 服务可以按现有逻辑复用；仍要核对模型 alias，不终止或重启用户自行启动的外部服务。外部服务的 GPU 使用情况不能单靠 HTTP 响应证明。

GPU 验证必须同时覆盖：

1. 设备枚举，排除软件 Vulkan，并确认选中了 RX 9070 XT。
2. 模型加载/权重缓冲/层卸载日志，证明模型实际放到了目标后端。
3. 真实输入和输出、阶段耗时及资源观察；不能将“能启动”或“健康检查通过”当作 GPU 推理验收。

连续停止流程为：停止并关闭采集 → 关闭帧队列 → VAD 收尾 → 有限任务排空 → 关闭自有模型服务与日志线程 → GUI 收尾保存报告。最终任务仅在未过期条件下有限重试。Windows 必须保持同样的所有权边界，并验证进程树没有遗留。

## 8. 界面和操作约定

默认浅色、黑字、16 号、85% 背景不透明度。透明度只作用于公共背景，不能改成整个窗口透明度，否则文字和边框也会变淡。

- 双框：上方实时原文与译文，下方已完成双语；历史在下框内部切换。
- 单框：只有源文/译文和一个边框，底部追加、向上滚动；无标题、状态、滚动条或等待提示。没有音频时为空白。
- 单框右键可回到最新、恢复双框设置、关闭；Esc 可恢复设置。上滚暂时停止跟随，滚到底或菜单操作恢复。
- 两种模式的非交互区域均可按住左键直接拖动；按钮、输入框、滑块、下拉框、滚动条和缩放手柄不能被拖动逻辑抢占。外缘负责缩放。
- GUI 模式切换不重载模型、不重启音频；语言切换会清空旧语言字幕与上下文。

Qt 控件树使用 `WA_DeleteOnClose`，避免在 QApplication 已结束后才由 Python 垃圾回收销毁控件。手势过滤器和计时器也由窗口拥有，关闭时清理鼠标捕获。

Windows 需要重点测试 `qwindows` 平台下的透明无边框、置顶、原生移动/缩放、右键菜单、100%/150%/200% 缩放和多显示器。当前 xcb 分支使用直接坐标移动；Windows 会进入其他 Qt 平台路径，不能据 Linux 验证推断原生拖动一定正常。字体 `Noto Sans CJK SC` 可能不存在，需配置字体回退或随部署提供适当字体，检查八语种字符显示。

## 9. 配置、启动和日常排查

### 9.1 配置优先级

优先级为：**数据类默认值 < 本次选中的 TOML < CLI 覆盖**。`local.toml` 不会自动与 `default.toml` 逐项叠加；所选 TOML 未提供的字段回到数据类默认值。例如数据类默认 ASR 引擎仍为 `whisper`，当前默认 TOML 才显式指定 `qwen3`。

- `start.sh` 优先选 `config/local.toml`，不存在时选 `config/default.toml`。
- 直接执行 Python CLI 默认使用 `config/default.toml`；要使用本机配置必须传 `--config`。
- TOML 相对路径以该配置文件所在目录为基准；CLI 输入文件等路径按当前工作目录解析。
- 音频 `source = ""` 表示跟随默认输出；显式 source 表示固定目标。不要修改系统默认音频设备来满足程序选择。
- `stream.subtitle_mode` 可设 `dual` 或 `single`。窗口内选项只影响本次运行，未自动持久化。

### 9.2 已有 Ubuntu 环境

以下命令在项目根目录执行；完整新机搭建见 [README](../README.md)。

```bash
# 已安装的一键启动；项目内也可执行 ./start.sh
livetranslate-start
livetranslate-start --language ko

# 单独运行文件模式需要先启动翻译服务；两个命令分别在两个终端执行。
env -u PYTHONPATH .conda/bin/python -m livetranslate serve --config config/local.toml
env -u PYTHONPATH .conda/bin/python -m livetranslate demo --config config/local.toml --input test.wav

# 当前 doctor 为 Linux 版。
env -u PYTHONPATH .conda/bin/python scripts/doctor.py --output artifacts/doctor.json
```

批次采集为 10–30 秒，WAV 必须是 16kHz mono PCM16；静音、空文件和截断不能进入成功结果。实时模式无需手动启动服务，由程序管理拥有的进程。

### 9.3 测试与诊断入口

```bash
# 逻辑回归；排除依赖真实环境的标记。
env -u PYTHONPATH .conda/bin/python -m pytest -q -m 'not real and not audio'

# 先准备官方 JFK 样本并启动相应翻译服务，再启用真实模型回归。
LIVETRANSLATE_REAL=1 LIVETRANSLATE_CONFIG=config/local.toml \
  env -u PYTHONPATH .conda/bin/python -m pytest -q -m real

# Linux 播放 + monitor + 真实模型 + 可见 Qt 窗口；请使用未占用的输出目录。
env -u PYTHONPATH .conda/bin/python scripts/validate_stream.py \
  --config config/local.toml --launcher ./start.sh --duration 35 \
  --speech artifacts/jfk.wav --output-dir artifacts/handoff-live-check
```

`tests/test_real.py` 使用 JFK 样本并检查 `country` / `国家` 等内容。任意自备音频不能直接替换该测试素材后仍期望相同断言。`real` 标记未启用时会跳过，不算真实通过。

排查顺序：设备可用性和有效 WAV → 文件 ASR → 直接文本翻译 → 文件管线 → 连续回采 → GUI 绘制。先分离模型问题与采集问题，再看队列/超时，避免用改提示词掩盖坏音频。

## 10. 测量口径与本机基线

| 当前六语种混合短测，n=7 | P50 | P95 |
| --- | ---: | ---: |
| 起声到首条原文 | 1084.37 ms | 1188.37 ms |
| 起声到首条中文 | 4359.89 ms | 5237.22 ms |
| 语音结束到最终双语 | 991.41 ms | 1291.08 ms |

同一短测主进程 RSS 观测峰值约 152.94 MiB，进程树 RSS 约 1110.11 MiB，整卡显存约 8.56 GiB。GPU 数值包含桌面和其他进程，资源采样可能漏峰值；不是模型独占显存，也不是 Windows 性能预测。

时钟使用 `monotonic_ns`。当前起点是 PCM 进入应用后按帧回推的 VAD 首/末有声帧时间，终点是可见且 exposed 的 QLabel 完成绘制。最终双语取原文 final 和译文 final 绘制的较晚时间。截图重绘、隐藏/最小化和 headless 不生成有效显示延迟。

该口径不覆盖最初的音频系统缓冲、VAD 边界误差和显示器物理扫描延迟。Windows 新增重采样或新的采集时间戳后，要明确缓冲属于测量区间的哪一部分，不能悄悄缩短测量区间来改善数字。

历史字段 `first_en_ms`、`final_english`、标签字段 `en` 现在泛指源文，保留用于兼容；新别名为 `first_source_ms`、`final_source`。分析新语言时使用事件中的 `source_language` 和 `language_generation`，不要把这些旧字段误解释为只处理英语。

## 11. Windows 11 26H2 的可行性依据

截至文档日期，微软已将 26H2 列为正式版本，AMD 为 RX 9070 XT 提供 Windows 11 x64 驱动；具体 OS build、补丁和驱动版本必须在目标机重新记录。[微软版本资料](https://learn.microsoft.com/en-us/windows/release-health/windows11-release-information)、[AMD 驱动](https://www.amd.com/en/support/downloads/drivers.html/graphics/radeon-rx/radeon-rx-9000-series/amd-radeon-rx-9070-xt.html)

speech.cpp 上游提供 Windows x64 Vulkan 构建，当前锁定源码也包含 Windows CI/MSVC 路径；llama.cpp 文档提供 Windows Vulkan 构建流程。现有 GGUF 原则上可继续使用，但必须在选定 Windows 二进制上实际加载。[speech.cpp 发布](https://github.com/nyosegawa/speech.cpp/releases)、[llama.cpp 构建](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md#vulkan)

Qt 6.12 明确支持 Windows 11 x86_64。已核对当前 PySide6-Essentials、webrtcvad-wheels 和 psutil 固定版本具有 Windows x64 wheel；仍需安装测试与进程退出验证。[Qt 平台支持](https://doc.qt.io/qt-6/supported-platforms.html)

推荐保持 **Windows 原生 Python + WASAPI + speech.cpp/llama.cpp Vulkan + Qt**。同设备的硬件容量没有明显障碍，但 Linux RADV 与 Windows AMD 驱动、WDDM 显存预算和调度不同，不能承诺速度相同或更快。同时运行大型游戏时，应额外测显存竞争和字幕 P95。

## 12. Windows 移植差异清单

以下是待做事项，不是已实现模块。

| 优先级 | 现有位置 / 问题 | 建议改造 |
| --- | --- | --- |
| P0 | `streaming/runner.py` 拒绝非 Linux；`cli.capture_backend` 同样限制 | 改成平台后端选择，只有未实现的平台报不支持 |
| P0 | `LiveEngine` 默认直接创建 LinuxContinuousCapture | 新增采集工厂，保留注入 Fake/真实源的能力 |
| P0 | `audio/linux.py`、`audio/continuous.py` 依赖 pactl、Pulse 和 FFmpeg pulse 输入 | 新增 WASAPI Loopback 批次与连续采集器 |
| P0 | `devices` 命令直接使用 `enumerate_monitors()`，协议未统一设备枚举 | 增加跨平台输出设备描述与枚举入口；Linux monitor 留在 Linux 适配器内部 |
| P0 | 当前连续采集用 selectors 监听子进程管道 | Windows 不照搬该循环；用音频回调/专用读取线程或 Windows 原生事件 |
| P0 | Linux 二进制路径、无 `.exe` 后缀、MSVC 输出目录差异 | 新增 Windows 配置；构建与运行路径按实际产物确定 |
| P0 | 原生构建脚本使用 Linux 产物布局且未传 MSVC `--config Release` | 增加平台/生成器处理，记录 DLL 依赖和 Release 产物；保持版本锁 |
| P1 | `start.sh`、`.conda/bin/python`、强制 xcb、LD_LIBRARY_PATH | 新增 Windows 启动器，使用 `.conda/python.exe` 和 Windows Qt 插件 |
| P1 | subprocess 与 `NativeService` 的退出、日志读取 | 避免弹出原生服务控制台；审计整个自有进程树的退出，可考虑 Job Object；不接管外部服务 |
| P1 | `telemetry.py` 读取 `/sys/bus/pci/devices` | 增加 Windows 采样后端，区分设备、进程和显存预算的统计口径 |
| P1 | `diagnostics.py` 使用 `/proc`、pactl、systemctl、Linux GPU 检查 | Windows doctor 检查 OS build、驱动、设备、DLL、Vulkan、端口与模型 |
| P1 | Qt 字体、DPI、透明/置顶、拖动缩放 | 在 `windows` 平台实测两种模式、多个屏幕和八种语言 |
| P1 | `validate_real.py`、`validate_stream.py` 使用 LinuxAudioCapture / paplay | 新增 Windows 定向播放/回采验证入口，不能只改平台检查 |
| P2 | D-Bus idle inhibitor 只在 Linux 生效 | 如需保持显示活跃，增加可选 Windows 实现；不能修改全局锁屏配置 |
| P2 | 安装包和便携启动 | 原生服务、Qt 插件/DLL、字体策略与模型路径验证完成后再打包 |

Python 官方说明 Windows selectors 支持 socket、不支持 pipe，因此即使换成 Windows FFmpeg，也不能直接复用现有 Linux 管道等待逻辑。[selectors 平台限制](https://docs.python.org/3/library/selectors.html)

建议新增的职责边界可为 `audio/factory.py`、`audio/windows.py`、`audio/windows_continuous.py`、Windows diagnostics/telemetry 后端及 Windows 启动器。名称可以调整，关键是让 VAD、队列、翻译和字幕状态机不依赖 WASAPI 或 PulseAudio 的具体类型。

## 13. WASAPI 回采设计建议

### 13.1 设备和音频流

使用输出端点的 WASAPI Loopback，共享模式，不选择默认麦克风，不要求安装虚拟声卡或开启“立体声混音”。微软说明 loopback 可以读取渲染端点的系统混音；独占模式与受保护内容存在限制。[WASAPI Loopback](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)

初版可评估 [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch)，它提供 loopback 与默认输出枚举。若设备通知、时间戳或底层恢复能力不足，再实现薄的原生 WASAPI 适配层。不要将仅支持普通录音的接口直接当作输出回采。

建议处理顺序：

1. 枚举渲染端点，确定默认角色或用户显式选择的目标。
2. 保留 endpoint ID 和显示名称；持久选择不能依赖易变的列表序号，ID 作为不透明标识使用。
3. 读取设备混音格式，在采集端按实际采样率、声道和样本格式接收音频。
4. 正确下混到单声道，避免丢掉多声道音频中的中心人声或造成削波。
5. 使用有状态重采样转换到 16kHz PCM16；回调块长度可变，在有限缓存中拼成 640-byte 帧。
6. 投递到既有 BoundedFrames，采集回调不运行推理、不更新 Qt、不等待队列腾空。

优先通过 `IMMNotificationClient` 接收默认设备、状态、增删变化，再把重建流操作交给工作线程。该接口提供不透明 endpoint ID，不能解析其内部格式。[设备通知说明](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nn-mmdeviceapi-immnotificationclient)

### 13.2 静音、断开与时间戳

- 依据 `AUDCLNT_BUFFERFLAGS_SILENT` 将明确标记的静音包视为零样本。
- 暂时没有播放数据与设备失效是不同状态；不能机械套用 Linux 的“3 秒无管道数据就重连”。
- 断开/失效不能被无限零帧掩盖；记录错误、释放旧流、有限退避，再按默认跟随或固定设备规则重连。
- 数据不连续与时间戳异常分别处理；设备变化/缺帧要推进 epoch 或连续性边界，不能让旧上下文穿过缺口。
- 需要初始化/释放 COM 的实现，在所属线程按正确生命周期完成，回调不能阻塞设备通知线程。
- `close()` 必须唤醒读取等待，并能重复调用；退出后不留下音频句柄和线程。

微软分别定义了静音、数据不连续和时间戳异常标记，应在适配器中显式处理。[音频缓冲标记](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/ne-audioclient-_audclnt_bufferflags)

优先沿用当前“应用接收音频 → 字幕绘制”的测量定义。若进一步接入设备位置/QPC 时间戳，要记录时钟映射、重采样延迟和缓冲边界，同时保留可与 Linux 比较的应用时延，不能把两种口径混成一列。

## 14. Windows 分阶段实施与命令示例

**以下 PowerShell 命令未在 Windows 实机执行；配置文件和原生程序布局需要先按说明准备。它们不是已经交付的 Windows 一键安装器。**

### 阶段 A：文件模式与模型验证

先安装/复用用户级 Conda、Git、适当的 AMD Windows 驱动。需要源码构建时准备 CMake、MSVC C++ 工具链和 Vulkan SDK；只使用预编译包时，按包要求准备运行时依赖，无需假定终端用户必须安装完整 SDK。

在 Windows 项目根目录新建独立环境，不复制 Ubuntu `.conda`：

```powershell
conda create --prefix .\.conda -c conda-forge python=3.12 pip pytest -y
conda run --prefix .\.conda python -m pip install -r config/live-requirements.lock
conda run --prefix .\.conda python -m pip install -e ".[dev]"
```

从可靠上游获得 Windows Vulkan 版 `speech.exe` 与 `llama-server.exe`，保留配套 DLL，或按锁定提交编译。MSVC 构建应指定 `--config Release`；不混用不同编译器/版本的 DLL。两个模型按锁复制或下载，并核对哈希。

建议将完整 Windows 原生发布包分别放入 `build/windows/speech/`、`build/windows/llama/`，模型仍放 `models/`。先从各引擎的设备枚举确认 9070 XT：`speech devices --json` 与 `llama-server --list-devices`；以对应固定版本的 `--help` 为准。**ASR 的设备序号和 LLM 的设备名称分别核对，不假定两个 `Vulkan0` 或 Linux 的序号必然对应同一块 GPU。**

可手工创建以下 `config/windows.local.toml`。只使用当前已有配置字段；可执行路径需与实际部署一致，示例中的 `0` / `Vulkan0` 必须在枚举确认后保留或替换。

```toml
[audio]
source = ""

[asr]
engine = "qwen3"
language = "en"
binary = "../build/windows/speech/speech.exe"
model = "../models/Qwen3-ASR-1.7B-Q8_0.gguf"
backend = "vulkan"
device = 0
server_port = 8083
threads = 8

[translation]
base_url = "http://127.0.0.1:8080"
model = "Hy-MT2-7B-Q4_K_M"
prompt_style = "hy_mt"
max_tokens = 256

[server]
binary = "../build/windows/llama/llama-server.exe"
model = "../models/Hy-MT2-7B-Q4_K_M.gguf"
port = 8080
backend = "vulkan"
device = "Vulkan0"
context_size = 4096
gpu_layers = 99
threads = 8

[stream]
subtitle_mode = "dual"
font_size = 16
opacity = 0.85
theme = "light"

[output]
directory = "../artifacts"
```

TOML 使用正斜杠可避免反斜杠转义问题。该文件名已匹配 `.gitignore` 的 `*.local.toml`；设备身份和本机路径不应提交。

分别在两个终端运行服务与文件验证：

```powershell
# 终端 1：前台翻译服务，保留日志和 GPU 证据。
conda run --no-capture-output --prefix .\.conda python -m livetranslate serve --config config/windows.local.toml

# 终端 2：先文本翻译，再已有 WAV；测试 WAV 须为 16kHz mono PCM16。
conda run --prefix .\.conda python -m livetranslate translate --config config/windows.local.toml --language en --text "Artificial intelligence is changing the world."
conda run --prefix .\.conda python -m livetranslate transcribe --config config/windows.local.toml --language en --input test.wav
conda run --prefix .\.conda python -m livetranslate demo --config config/windows.local.toml --language en --input test.wav
```

文件链路在设计上可优先移植，但本基线没有 Windows 运行结果。若日志格式、DLL、路径或 GPU 选择失败，应保存原始错误并修正原因，不关闭后端验证来获得 PASS。

### 阶段 B：回采和实时接入

完成采集工厂、Windows 设备枚举、批次/连续 WASAPI 实现以及平台分发后，再运行：

```powershell
# 当前基线直接运行仍会因 Windows 平台限制失败；仅在阶段 B 实现后使用。
conda run --prefix .\.conda python -m livetranslate devices --config config/windows.local.toml
conda run --prefix .\.conda python -m livetranslate capture --config config/windows.local.toml --duration 15 --output test.wav
conda run --no-capture-output --prefix .\.conda python -m livetranslate live --config config/windows.local.toml --language en
```

先让真实系统播放音频生成有效 PCM，再接 VAD/模型/GUI。不要用麦克风、正弦波或 Fake ASR 结果代替系统回采验收。单独启动过翻译服务时，应明确选择保留为外部服务或先关闭后由 live 管理，不能误杀已有进程。

### 阶段 C：应用体验、监控与分发

新增 Windows 启动器，以脚本所在目录定位环境/配置；默认读取 Windows 本机配置，不强制 xcb、不继承 Linux 动态库路径。先保留控制台便于排错，稳定后再提供桌面快捷方式或无控制台入口，并确保错误仍能在日志或界面查看。

GPU 统计需要单独设计。`IDXGIAdapter3::QueryVideoMemoryInfo` 给出的是调用进程的预算和使用量，**在 Python 主进程调用它不能代表另外两个原生推理进程，更不能代表整卡占用**。可评估按 PID/适配器的 Windows 性能计数器、原生进程内采样或 AMD 接口，输出必须标明作用域；暂不可用时明确记 unavailable，不能填零冒充实测。[DXGI 说明](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/nf-dxgi1_4-idxgiadapter3-queryvideomemoryinfo)

打包时验证 Qt `platforms/qwindows.dll`、必要 DLL、字体和原生程序搜索路径。模型保持外置，先提供可检查的目录式部署，再考虑单文件包装；不要把几 GB 模型打入临时解压的单文件程序。参考 [Qt Windows 部署](https://doc.qt.io/qt-6/windows-deployment.html)。

工程量粗估：在熟悉代码且有同设备 Windows 实机的条件下，验证原型约 2–4 个工作日，包含恢复、打包和回归的可用版本约 1–2 周。此为规划估计，若驱动或音频设备出现问题需要重新估算。

## 15. Windows 验收矩阵和通过标准

| 类别 | 必测场景 | 通过依据 |
| --- | --- | --- |
| 系统审计 | OS build、AMD 驱动、GPU 枚举、Python/依赖、原生版本、模型哈希 | 可保存的真实报告，确认使用 9070 XT |
| 模型文件测试 | 固定真人英文及各支持语种、直接文本翻译 | 非空合理内容、正确语言参数、实际 GPU 加载和推理证据 |
| 系统回采 | HDMI、有线/USB/蓝牙输出中实际具备的设备 | 录到所选输出的音频，不误录麦克风；未具备的设备标为未验证 |
| 默认输出变化 | 播放中切换默认设备；固定 source 模式对照 | 自动模式重连新端点，固定模式不擅自切换；旧上下文清空 |
| 异常恢复 | 拔线、蓝牙断开、服务终止、端口占用、缺模型、静音、持续过载 | 可解释错误、有限重试/队列、无假字幕或无限积压 |
| GUI | 双框/纯字幕、八语种、透明背景、拖动、边缘缩放、控件排除、右键/Esc | 实际可见窗口行为正确；最小化/隐藏不计入显示时延 |
| Windows 特性 | 中文/空格路径、不同 DPI、多屏、休眠唤醒 | 无路径/字体/坐标错误，恢复策略明确 |
| 退出 | 停止按钮、关闭、Alt+F4、Ctrl+C、异常终止后的清理 | 释放回采/服务/线程；外部服务不被误终止 |
| 资源与时延 | 首条源文、首条中文、最终双语 P50/P95；CPU/RSS/GPU/显存 | 时间戳和作用域明确；不能用整段处理耗时替代字幕延迟 |
| 稳定性 | 当前模型与代码连续至少 30 分钟 | 队列有界、错误/丢弃可审计、无明显增长趋势或最终改写；输出原始证据 |

先增加 Windows CI 的配置、状态机、协议和构建检查；普通托管 CI 不等于拥有 Radeon GPU，不能用于宣称硬件推理通过。Linux 专用测试应清楚标记/条件跳过，同时继续在 Linux 执行，不能直接删除不通过的用例。

Windows 真实模型回归可以沿用现有测试，在准备 JFK 和健康服务后用 PowerShell 设置 `LIVETRANSLATE_REAL=1`、`LIVETRANSLATE_CONFIG=config/windows.local.toml`。现有 Linux `validate_stream.py` 与 `validate_real.py` 必须先移植播放/采集部分，不能原样拿来当 Windows 验收器。

交付应包含原始配置快照的脱敏版本、测试结果、完整 commit、模型/原生版本、运行命令及未完成项。任何缺设备、权限、音频或下载条件的测试都标为未验证；Fake 只证明逻辑，真实系统音频到真实模型再到可见字幕才证明端到端可用。

## 16. 交接资产、记录与维护规则

### 16.1 仓库与本机资产分开交付

Git 包含源码、测试、默认配置、锁文件和文档。以下为本机资产，默认不提交：

- `.conda/`、`.venv/`、`.cache/`、`vendor/`、`build/`、`models/`。
- GGUF/模型权重、编译产物、临时音频、日志与 `artifacts/`。
- `config/local.toml`、`*.local.toml`、环境变量文件及含个人路径的配置。

新检出无法自动获得旧测试原始证据。需要转交时，从授权本机另行复制对应 artifacts，并保留来源、版本和校验和；不要把私有录音或本机配置混入公开 Git。

最新多语种证据入口是 `artifacts/languages-six-20261024/`，其中 `pytest-final.xml`、`live-six/acceptance.json`、`live-six/summary.json`、逐语言结果和 provenance 对应本基线。此前语言、界面和拖动的证据路径列在 [M2_REPORT.md](M2_REPORT.md)。

### 16.2 接手后的约束

1. 不卸载或替换现有内核、ROCm、显卡驱动来“修复”项目；平台依赖优先隔离在用户/项目环境。
2. 不更改系统默认音频设备、不录入麦克风、不上传音频或文本到云端推理。
3. 不在模型失败时静默换模型或后端，不用假结果掩盖缺陷。
4. 不把队列改成无限缓存，不让 UI 线程执行采集/推理，也不改写已经确认的最终字幕。
5. 更新引擎或模型时保留旧版本，更新对应锁文件，重新验证协议、设备、真实结果和性能。
6. 修改默认值后同时核对代码数据类、默认 TOML、回退配置、测试与文档，避免多个默认值来源漂移。
7. Windows 与 Linux 共用业务层；平台条件尽量收敛到适配器/工厂，避免散落在字幕和翻译代码中。

### 16.3 进一步阅读

| 文档 | 用途 |
| --- | --- |
| [README](../README.md) | 当前运行、环境搭建、常用命令 |
| [M1 架构](architecture.md)、[M0/M1 报告](M0_M1_REPORT.md) | 批次接口与早期验收 |
| [M2 架构](M2_ARCHITECTURE.md)、[M2 报告](M2_REPORT.md) | 连续调度、界面及各次变更记录 |
| [模型切换报告](QWEN_HY_UPGRADE.md)、[原生引擎升级](NATIVE_UPGRADE.md) | 模型/引擎兼容和 GPU 实证 |
| [上下文修正](TRANSLATION_CONTEXT.md) | 有限历史、歧义翻译回归与已知限制 |
| [语言切换](LANGUAGE_SWITCH.md) | 八语种、真人样本和最新短测 |
| [M2 性能](M2_BENCHMARKS.md)、[批次性能](benchmarks.md) | 两种不同测量口径与历史基线 |

完成 Windows 移植后，应更新本文的基线、状态、平台配置与实机证据；在此之前，Windows 的所有实施与性能结论均保持“待验证”。
