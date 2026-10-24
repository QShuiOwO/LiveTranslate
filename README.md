# LiveTranslate

Ubuntu 24.04 优先的本地多语言识别与简体中文翻译工具。捕获系统播放音频，默认通过 speech.cpp / Qwen3-ASR-1.7B Q8_0 识别，再通过本地 llama.cpp / Hy-MT2-7B Q4_K_M 翻译。新版 whisper.cpp / Small.en 保留为可显式选择的回退配置。M2 提供连续监听、VAD 分段、临时/最终字幕和原生 Qt 双语窗口，保留 M1 文件与批次 demo。Python 3.11+，使用项目独立 Conda 环境；不提供麦克风模式。

真实验收状态见 [M0/M1 报告](docs/M0_M1_REPORT.md)，实测数据见 [性能报告](docs/benchmarks.md)，硬件与依赖见 [环境审计](docs/environment.md)。不要把 doctor 的设备枚举当成 GPU 推理验证。

原 M1 已通过真实 HDMI monitor 端到端验收；当时 pytest 69 项通过，包含真实模型测试。当时默认蓝牙输出已断开，该路径保留为失败。逐项结果见 [测试报告](docs/test-results.md)。

原模型的 M2 HDMI + xcb / XWayland + Vulkan 路径已完成超过 30 分钟真实验收。当前切换为 Qwen3-ASR + HY-MT2 后，160 项测试通过（含 3 项真实模型测试），另通过真实 HDMI 回采及一分钟可见字幕短测，8/8 最终双语字幕完成绘制；新组合按要求未做长测。当前结果见 [模型切换报告](docs/QWEN_HY_UPGRADE.md)。历史结果与测量方法见 [M2 报告](docs/M2_REPORT.md)、[M2 性能](docs/M2_BENCHMARKS.md) 与 [原生引擎升级报告](docs/NATIVE_UPGRADE.md)。

透明双区域界面初版通过 **167 项测试**，并完成 35 秒真实 HDMI 窗口回归（4/4 完整双语绘制）。界面布局与验收说明见 [M2 报告](docs/M2_REPORT.md)。

当前支持英语、日语、韩语、法语、德语、西班牙语、意大利语、俄语 → 简体中文，默认背景不透明度为 85%。语言切换、真实测试及质量限制见 [语言切换报告](docs/LANGUAGE_SWITCH.md)。

面向接手开发者的设计说明、模块地图和 Windows 11 26H2 移植清单见 [项目交接文档](docs/HANDOFF_WINDOWS_11_26H2.md)。Windows 适配尚未实现，文档中的 Windows 命令为待实机验证的实施示例。

## M2 连续双语字幕

本机已完成环境搭建，启动字幕窗口只需一条命令，可在任意目录执行：

```bash
livetranslate-start
```

该命令链接到项目的 `start.sh`，自动使用 `.conda/bin/python`，优先加载 `config/local.toml`（不存在时使用 `config/default.toml`），以 xcb / XWayland 打开窗口，并加载已有的项目内 Qt 库缓存。无需手动激活 Conda 或设置环境变量；Qwen3-ASR 和 HY-MT2 服务自动启动，关闭窗口即释放程序拥有的服务。本机配置会跟随系统当前选中的声音输出。

窗口顶部语言下拉框提供 **英语、日语、韩语、法语、德语、西班牙语、意大利语、俄语 → 中文**。纯字幕模式可右键选择“返回双框与设置”后切换语言。选择后立即用于后续系统音频，无需重载模型；切换会清空当前字幕、字幕历史及翻译上下文，正在处理的旧语言结果会被丢弃，建议在语音停顿时切换。默认仍为英语，选项在本次运行有效；不会修改系统声音输出。当前 Qwen3-ASR + HY-MT2 共接入上述八个方向，Whisper Small.en 回退配置只能选英语。

可直接以日语模式启动：

```bash
livetranslate-start --language ja
# 英语：livetranslate-start --language en
```

需持久保存启动语言时，在 `config/local.toml` 已有的 `[asr]` 表中设置 `language = "ja"`（不要重复创建表）。`demo`、`transcribe`、`translate` 也支持 `--language en|ja|ko|fr|de|es|it|ru`。参数对应英语、日语、韩语、法语、德语、西班牙语、意大利语、俄语；例如 `livetranslate-start --language ko` 直接启动韩语模式。适配与真实短测的结果、限制见 [语言切换验收](docs/LANGUAGE_SWITCH.md)。

项目目录内也可直接执行 `./start.sh`。支持透传 `live` 的参数，例如 `livetranslate-start --duration 60` 或 `livetranslate-start --help`。相对路径按项目根目录解析；外部路径可传绝对路径。已有 `QT_QPA_PLATFORM` 设置时保留，可用于显式选择其他 Qt 平台。

全新检出完成下方环境搭建后，可在项目根目录添加同名用户命令（`~/.local/bin` 需在 PATH 中；已有同名文件时先检查，不覆盖）：

```bash
mkdir -p "$HOME/.local/bin"
ln -s "$PWD/start.sh" "$HOME/.local/bin/livetranslate-start"
```

已有环境可安装附加依赖、构建固定版本的三个引擎，并下载当前模型：

```bash
conda activate "$PWD/.conda"
env -u PYTHONPATH python -m pip install -r config/live-requirements.lock
env -u PYTHONPATH python -m pip install --no-build-isolation -e .
python scripts/build_native.py --jobs 8 --no-cpu-fallback
python scripts/download_models.py
python -m livetranslate devices
python -m livetranslate live --config config/local.toml
```

也可安装 `python -m pip install -e '.[live]'`；固定版本见 `config/live-requirements.lock`。GUI 使用 PySide6-Essentials，不需要 Qt WebEngine、CUDA 或麦克风权限。Wayland 桌面可直接运行；X11 若 Qt 报缺少 xcb-cursor，安装 Ubuntu 的 `libxcb-cursor0`，无需修改显卡驱动。

启动后自动管理本地 Qwen3-ASR 服务（127.0.0.1:8083）与 HY-MT2 翻译服务（127.0.0.1:8080）。已存在、健康的本地 llama-server 可复用，模型别名仍会核对；外部服务不会被本程序终止或擅自重启。程序自己启动的服务会在退出时释放。Qwen3-ASR 由 speech.cpp 加载，不能由 whisper.cpp 加载；回退配置的 Whisper 服务使用 8082。

窗口默认采用浅色主题、85% 背景不透明度、16 号黑色文字和上下两个圆角边框。上框显示识别及翻译中的实时双语字幕，下框保留最近完成的双语字幕；原文识别已完成、中文仍在生成时继续留在上框。两个区域位于同一容器中，随窗口等比例缩放；字号较大或文本较长时在区域内滚动，避免撑开布局。原文确认后不再改写，稳定的临时译文在原文继续增长时保留。

语言、字号、背景不透明度、主题、置顶、字幕历史及停止按钮集中在顶部。“背景”滑块在 0–100% 之间仅调节背景填充，旁边显示百分比；文字、边框及控件保持清晰。上下字幕框共用一层背景，85% 不会因重叠绘制变成更深的颜色。保留浅色、深色和透明黑字主题；透明黑字模式固定无填充，返回浅色或深色时恢复原背景百分比。在标题、字幕或空白处按住左键拖动即可移动窗口，双击标题放大或还原，拖动右下角手柄缩放；右上角可最小化或关闭。开启字幕历史时，历史列表显示在下框内，自动跟随最新字幕；手动上滚后保持阅读位置，重新开启历史恢复跟随。历史条数受配置限制。

点击顶部 **单框滚动** 后，窗口只显示一个纯字幕框：隐藏工具栏、标题、状态标签、滚动条及等待提示，没有语音时框内保持空白。同一段原文和译文在底部持续更新，完成后保留原位置，接下来的字幕从底部追加并把旧字幕推向上方；迟到的译文更新自己的原行。单框与双框均支持**按住左键直接拖动**，无需先停住等待；字幕文字、状态文字、标题和其他空白区域都可拖动。按钮、复选框、输入框、滑块、下拉框、滚动条及缩放手柄保持原有操作，不触发窗口移动；窗口外缘用于缩放。

纯字幕模式下，**右键字幕框**可选择“回到最新字幕”“返回双框与设置”或“关闭字幕窗口”，按 **Esc** 也可返回双框。滚轮仍可查看历史，向上滚动暂停自动跟随；滚回底部或使用右键菜单恢复跟随。返回双框保留已有字幕，并恢复语言、字号、背景等设置入口。

默认启动仍为双框；若希望每次启动都使用单框，在 `config/local.toml` 已有的 `[stream]` 表中设置 `subtitle_mode = "single"`，恢复为 `"dual"` 即可。窗口内切换仅影响本次运行。两种模式共用字号、背景和语言设置，语言切换仍会清空字幕。

背景不透明度通过背景颜色的 alpha 调整，Wayland 下也可使用；置顶行为由桌面合成器决定。窗口可见期间使用临时桌面 idle inhibitor，退出即释放，不改变系统锁屏设置，也不解除手动锁屏。

本机 GNOME 原生 Wayland 长测出现窗口未最小化但 `exposed=false` 的区间，影响可见字幕测量。需要稳定置顶时使用同一 GUI 的 XWayland / xcb 模式。系统已安装 `libxcb-cursor0` 时只需 `QT_QPA_PLATFORM=xcb python -m livetranslate live --config config/local.toml`。本机为避免全局安装，已将该库解包到项目缓存，可运行：

```bash
env -u PYTHONPATH QT_QPA_PLATFORM=xcb \
  LD_LIBRARY_PATH="$PWD/.cache/qt-xcb/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  python -m livetranslate live --config config/local.toml
```

缓存重建步骤和实际验证结果见 [M2 报告](docs/M2_REPORT.md)。这里只切换 Qt 显示平台，两类模型仍在同一 RX 9070 XT 上以 Vulkan 推理。

`[audio] source = ""` 会监听 Ubuntu“声音输出”当前选中设备的 monitor。连续运行中约每 2 秒检查默认输出，切换输出后结束旧采集、重连新 monitor 并切断旧音频上下文。配置固定的 monitor 名称或传入 `--source` 时保持固定设备。程序只读取系统的输出选择。

蓝牙设备的连接状态与名称通过可用的 `busctl` 只读查询 [BlueZ Device1](https://github.com/bluez/bluez/blob/master/doc/org.bluez.Device.rst)，避免 PipeWire 缓存的旧 `api.bluez5.connection` 字段把实际连接的耳机误判为断开。BlueZ 查询不可用时，只有输出和 monitor 均为 RUNNING、活动端口明确可用，才允许尝试该监听源；否则保留 pactl 的检测结果。BlueZ 明确报告断开、监听源 UNAVAILABLE 或活动端口不可用时仍拒绝采集。

```bash
# 指定输出的 monitor；不会设置系统默认设备。
python -m livetranslate live --config config/local.toml --source MONITOR_NAME
# 定时运行、保存真实窗口截图及测量数据（音频须实际播放）。
python -m livetranslate live --config config/local.toml --duration 60 \
  --output-dir artifacts/my-live-session --screenshot artifacts/my-live-session/window.png
# 无界面模式可用于排查音频与模型；不作为 GUI 延迟验收。
python -m livetranslate live --config config/local.toml --headless --duration 60
```

实时翻译默认参考最近最多两段已确认原文（总计最多 800 字符、30 秒音频时间以内），帮助理解口语省略和多义词；上下文仅供参考，输出只翻译当前段。每个任务固定自己的上下文，缓存也按上下文区分；语言切换、音源切换、采集重连和丢帧后使用新历史。可在已有 `[translation]` 表内设置 `context_segments`、`context_max_chars`、`context_max_age_s`，其中 `context_segments = 0` 关闭此功能。原文不做词语替换，详见 [上下文修正与真实回归](docs/TRANSLATION_CONTEXT.md)。

每次运行使用新的目录，保存 events.jsonl、模型日志、资源数据及 summary.json；默认目录自动命名。日志轮转，音频只在内存的有限帧/分段中处理，不连续写录音。状态栏会提示断开、恢复或旧任务丢弃。停止按钮、关闭窗口和 Ctrl+C 都会结束采集、排空有限队列并释放拥有的子进程。

配置 `[stream]` 控制 VAD、停顿、临时识别间隔、最长分段、队列、超时和外观，见 [架构说明](docs/M2_ARCHITECTURE.md)。旧 M1 配置继续可用：`[asr] engine` 缺省为 `whisper`，其常驻服务从 whisper-cli 同目录推导。当前默认配置显式设置 `engine = "qwen3"`、`server_port = 8083`，常驻和文件识别共用 speech 可执行文件。字幕延迟从 VAD 语音帧进入应用算到可见文字控件实际完成绘制；截图重绘和隐藏窗口不计入，详细测量边界见性能文档。

真实 HDMI 自动验收会播放所给的语音文件（配置中的 `asr.language` 应与语音一致）：

```bash
python scripts/validate_stream.py --config config/local.toml --duration 35 \
  --speech artifacts/jfk.wav --output-dir artifacts/live-short
# 30 分钟公开人声测试素材准备见 M2 报告；文件及元数据均放在 artifacts。
python scripts/validate_stream.py --config config/local.toml --duration 1840 --require-soak \
  --speech artifacts/m2-fixtures/english-30min.wav --output-dir artifacts/live-soak
```

## 从全新 Ubuntu 24.04 搭建

以下命令在本项目根目录执行。软件包已经存在时直接复用。本次未更改系统驱动、ROCm、内核或全局音频配置。

```bash
sudo apt update
sudo apt install -y build-essential cmake git pkg-config ffmpeg \
  libvulkan-dev vulkan-tools glslc libshaderc-dev libcurl4-openssl-dev \
  pipewire pipewire-pulse wireplumber pulseaudio-utils libxcb-cursor0 fonts-noto-cjk
```

已有桌面 PipeWire 会话应继续使用。需要 Vulkan 能识别真实 AMD GPU；不要为了本项目替换已工作的驱动。系统没有 Conda 时先按 [Miniforge 官方安装说明](https://github.com/conda-forge/miniforge#install) 安装用户级 Miniforge，并核对官方校验和。

```bash
# 环境与包缓存放在项目目录；不升级已有 Conda 环境。
CONDA_PKGS_DIRS="$PWD/.cache/conda-pkgs" conda create -p "$PWD/.conda" -c conda-forge python=3.12 pip pytest -y
conda activate "$PWD/.conda"
env -u PYTHONPATH python -m pip install --no-build-isolation -e .
env -u PYTHONPATH python -m pip install -r config/live-requirements.lock

# 先审计；权重尚未下载时模型检查失败是预期结果。
python scripts/doctor.py --output artifacts/doctor.json

# 先检查 ~/AI/whisper.cpp 的官方提交，能复用则复制源码到项目内。
python scripts/build_native.py --jobs 8
python scripts/download_models.py
```

构建固定在 [native-lock.json](config/native-lock.json) 的上游提交：Whisper v1.9.5 / `d1be6fde11ac`，llama.cpp 开发提交 `f5027fb21307`，社区 speech.cpp v0.8.11 / `d074e1b9d84f`（其 ggml 子模块同样固定）。源码放在 `vendor/NAME.cpp-COMMIT前12位/`，产物放在 `build/NAME-BACKEND-COMMIT前12位/`；新旧版本分别保存。原工作树及其本地修改不会被覆盖，复用已有仓库时只复制固定提交的 Git 对象并保留正确的版本信息。

已下载官方源码时，可直接复用，省去重复下载：

```bash
python scripts/build_native.py --jobs 8 --backend vulkan --no-cpu-fallback \
  --whisper-source "$HOME/AI/whisper.cpp-v1.9.5" \
  --llama-source "$HOME/AI/llama.cpp-latest"
```

每次构建的日志分别保留在 `artifacts/native-build-logs/`，提交、产物和耗时写入 `artifacts/native-build.json`，候选运行配置写入 `artifacts/native-runtime.toml`。`--output-dir` 可将这些报告放到指定目录，`--lock` 可指定另一份固定提交清单。构建不会自动修改 `config/local.toml`；验证新版本后再更新本机配置中的可执行文件路径。

Vulkan 构建失败时构建脚本默认保存错误并尝试独立 CPU 构建，实际后端写入上述运行配置；`--no-cpu-fallback` 要求 Vulkan 失败即停止。只有 CPU 构建通过不代表 GPU 通过；需要使用该配置运行并记录真实识别。M2 连续字幕要求两个服务都使用 Vulkan。

下载脚本匿名访问 Hugging Face，固定 [model-lock.json](config/model-lock.json) 的 revision，并核对远端公布的大小与 SHA-256。无需 Hugging Face 登录或 Token。下载失败保留 `.part`，重跑续传；最终权重仅在校验通过后发布到 models/，不提交 Git。已有损坏的最终文件会报错并保留。

本次环境的可复现 Conda 包集合为 [conda-linux-64.lock](config/conda-linux-64.lock)。同平台可用它替代环境创建命令：

```bash
CONDA_PKGS_DIRS="$PWD/.cache/conda-pkgs" conda create -p "$PWD/.conda" --file config/conda-linux-64.lock -y
```

## 当前模型与回退

Qwen3-ASR 使用 speech.cpp 专用布局的 `Qwen3-ASR-1.7B-Q8_0.gguf`，来源是该运行时作者发布的固定版本转换件；用户已有的官方 safetensors 文件继续保留。HY-MT2 使用腾讯官方 `Hy-MT2-7B-Q4_K_M.gguf`。文件大小约 2.18 GB 和 4.62 GB，运行显存还包含计算缓冲和 KV cache。来源与校验和见 `config/model-lock.json`。

新组合的可行性、真实短测结果及限制见 [模型切换报告](docs/QWEN_HY_UPGRADE.md)。Qwen 的 Vulkan 实现通过应用层 VAD 分段实现临时/最终字幕，不等同于官方 vLLM 原生流式模式。HY-MT2 使用 `prompt_style = "hy_mt"` 的单条用户翻译指令；生成保持 temperature=0 和最多 256 tokens，以便字幕响应可比较。

需要显式回退到新版 whisper.cpp + Small.en、llama.cpp + Qwen3-4B：

```bash
# 缺少旧权重时才下载；校验通过的已有文件直接复用。
python scripts/download_models.py --lock config/whisper-model-lock.json
livetranslate-start --config config/whisper-qwen.toml
```

回退通过配置手动选择，推理失败不会静默换模型。两套配置不要同时启动，以免争用翻译端口。

## 启动本地翻译服务

```bash
conda activate "$PWD/.conda"
env -u PYTHONPATH python -m livetranslate serve
# 如果构建使用了 CPU 后备：
# python -m livetranslate serve --config artifacts/native-runtime.toml
```

该命令保持前台运行，Ctrl+C 关闭子进程。服务仅监听 `127.0.0.1:8080`，显式选择 `Vulkan0`、GPU 层 offload，关闭网页并启用离线模式。启动日志在 `artifacts/llama-server.log`，健康与 GPU 证据在 `artifacts/server-ready.json`。端口已被占用、权重缺失或后端未证实时会失败。

另开终端进入项目目录，激活同一环境：

```bash
curl --noproxy '*' http://127.0.0.1:8080/health
python scripts/translate_test.py --text "Artificial intelligence is changing the world."
```

Python 客户端不使用环境 HTTP 代理，也不跟随重定向；拒绝非 127.0.0.1 地址。模型下载会访问公开仓库，识别与翻译不调用云端推理服务。

## 音频采集、识别与演示

```bash
python -m livetranslate devices
python scripts/capture_test.py --duration 15 --output test.wav
python scripts/transcribe_test.py --input test.wav
python -m livetranslate demo --duration 15
python -m livetranslate demo --input test.wav
```

采集时需有音频正在播放。自动选择默认输出的 monitor，不会选默认麦克风。如果默认输出已断开、候选不明确或想采集其他输出，使用 `devices` 中准确的 monitor 名称：

```bash
python scripts/capture_test.py --duration 15 --output test.wav --source MONITOR_NAME
python -m livetranslate demo --duration 15 --source MONITOR_NAME
```

M1 验收时默认蓝牙输出已断开，真实测试通过显式选择已连接 HDMI 的 monitor 完成。M2 继续使用显式 HDMI 源，开发过程没有设置系统默认设备。当前设备名应重新枚举，不能照抄其他机器的音频源。

本机配置 `config/local.toml` 被 Git 忽略；当前设为 `source = ""`，跟随系统选中的声音输出，可直接用 `python -m livetranslate demo --config config/local.toml --duration 15`。全新检出可使用默认配置的自动选择，或自行指定 monitor。

录音必须为 16 kHz、Mono、PCM16 WAV，拒绝空音频、静音、截断和采集失败。已有文件可先转换：

```bash
ffmpeg -i original.wav -ar 16000 -ac 1 -c:a pcm_s16le normalized.wav
python -m livetranslate demo --input normalized.wav --output artifacts/file-demo.json
```

默认 JSON 保存到 artifacts/。可用 `--output` 指定结果，capture 命令的 `--output` 指 WAV，其结构化结果保存在同名 `.capture.json`。录音不会被覆盖；JSON 中保留错误原因和失败前已完成阶段的信息。`demo` 输出原文、译文、模型、实际 ASR 后端、捕获/识别/翻译/总耗时和 GPU 采样。

## 配置

默认加载 `config/default.toml`，通过 `--config` 显式指定其他 TOML。相对路径相对于配置文件目录；命令行路径相对于当前目录。复制便携配置为 `config/local.toml` 后，可设置本机源、路径、线程、端口和超时，避免将本机信息提交 Git：

```bash
cp config/default.toml config/local.toml
python -m livetranslate demo --config config/local.toml --input normalized.wav
python -m livetranslate demo --input normalized.wav --backend cpu
```

主要覆盖参数：`--source`、`--duration`、`--asr-engine`、`--asr-model`、`--asr-binary`、`--backend`、`--device`、`--base-url`、`--translation-model`、`--max-tokens`。服务端另支持 `--model`、`--binary`、`--port`。API 默认 max_tokens=256、temperature=0、seed=42；达到 token 上限会报告不完整翻译。

## 测试和真实验收

```bash
# 清除外部 ROS 等工具链注入的 PYTHONPATH；只影响这个命令。
env -u PYTHONPATH python -m pytest -q --junitxml=artifacts/pytest-unit.xml

mkdir -p artifacts
cp vendor/whisper.cpp-d1be6fde11ac/samples/jfk.wav artifacts/jfk.wav
LIVETRANSLATE_REAL=1 env -u PYTHONPATH python -m pytest -q -m real --junitxml=artifacts/pytest-real.xml

# 真正播放官方英语样本，捕获系统 monitor，再经过当前配置的真实 ASR 和翻译模型。
# 该命令会向所选设备播放约 11 秒英语；不改变系统默认输出。
env -u PYTHONPATH python scripts/validate_real.py --source MONITOR_NAME
env -u PYTHONPATH python scripts/benchmark.py --iterations 3
python scripts/doctor.py --hip-smoke --output artifacts/doctor.json
```

未启用真实测试时 `real` 测试明确跳过，不代表通过。`validate_real.py` 必须有健康本地服务和可播放输出；没有这些条件时报告失败。测试默认使用官方 JFK 英文语音（真实人声），单元测试的正弦波不用于真实验收。HIP probe 只验证一个内核的运算，不能代表已验证 Whisper/LLM HIP 后端。

Vulkan、PulseAudio socket 或 `/dev/dri` 在隔离执行沙箱内不可见时，需要在拥有当前桌面会话设备权限的本机终端运行。不要通过修改驱动、设为全局音频服务器或放宽网络监听来处理沙箱隔离。

## 交付范围

接口与边界见 [M1 架构文档](docs/architecture.md) 和 [M2 架构文档](docs/M2_ARCHITECTURE.md)。权重、音频样本、原生源码/产物、日志、动态 JSON 和本机配置被 `.gitignore` 排除。Windows 采集适配器暂未实现，已有文件的数据流和字幕状态机不绑定 Linux；没有麦克风输入或 HIP 适配。当前已接入八种源语言到简体中文，并完成多语种短测；当前模型组合尚未进行 30 分钟长期验收。
