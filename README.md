# LiveTranslate

Ubuntu 24.04 优先的本地英语识别与简体中文翻译工具。捕获系统播放音频，以 whisper.cpp Small.en 识别，再通过本地 llama.cpp / Qwen3-4B-Instruct-2507 Q4_K_M 翻译。M2 提供连续监听、VAD 分段、临时/最终字幕和原生 Qt 双语窗口，保留 M1 文件与批次 demo。Python 3.11+，使用项目独立 Conda 环境；不提供麦克风模式。

真实验收状态见 [M0/M1 报告](docs/M0_M1_REPORT.md)，实测数据见 [性能报告](docs/benchmarks.md)，硬件与依赖见 [环境审计](docs/environment.md)。不要把 doctor 的设备枚举当成 GPU 推理验证。

本次 M1 已通过真实 HDMI monitor 端到端验收；最终 pytest 69 项通过，包含真实模型测试。默认蓝牙输出已断开，该默认路径保留为失败。逐项结果见 [测试报告](docs/test-results.md)。

M2 已在 HDMI + xcb / XWayland + Vulkan 路径完成超过 30 分钟真实验收，107 项测试通过；原生 Wayland 曝光限制保留在报告中。M1 信息是当时的验收快照。M2 设计、30 分钟运行结果和显示延迟见 [M2 报告](docs/M2_REPORT.md) 与 [M2 性能](docs/M2_BENCHMARKS.md)。

## M2 连续双语字幕

已有 M1 环境可直接安装附加依赖并构建同一提交的常驻 Whisper 服务，不更换模型或驱动：

```bash
conda activate "$PWD/.conda"
env -u PYTHONPATH python -m pip install -r config/live-requirements.lock
env -u PYTHONPATH python -m pip install --no-build-isolation -e .
python scripts/build_native.py --jobs 8
python -m livetranslate devices
python -m livetranslate live --config config/local.toml
```

也可安装 `python -m pip install -e '.[live]'`；固定版本见 `config/live-requirements.lock`。GUI 使用 PySide6-Essentials，不需要 Qt WebEngine、CUDA、额外 ASR/翻译模型或麦克风权限。Wayland 桌面可直接运行；X11 若 Qt 报缺少 xcb-cursor，安装 Ubuntu 的 `libxcb-cursor0`，无需修改显卡驱动。

启动后自动管理项目自己的本地 Whisper 服务（127.0.0.1:8082）与 Qwen 服务（127.0.0.1:8080）。已存在、健康的本地 Qwen 可复用，模型别名仍会核对；外部服务不会被本程序终止或擅自重启。程序自己启动的服务会在退出时释放。Whisper 服务必须在上述构建后可用；M1 的 whisper-cli 不受影响。

窗口上部显示临时字幕，下部显示已确认英文及中文。英文确认后不再改写；中文只在完成翻译后确认。稳定的临时译文在英文继续增长时保留。字号、深浅主题、字幕历史和置顶提示可在窗口调整；历史最多保留配置指定的条数。Wayland 不支持窗口透明度时禁用该控件，置顶行为由桌面合成器决定。窗口可见期间使用临时桌面 idle inhibitor，退出即释放，不改变系统锁屏设置，也不解除手动锁屏。

本机 GNOME 原生 Wayland 长测出现窗口未最小化但 `exposed=false` 的区间，影响可见字幕测量。需要稳定置顶时使用同一 GUI 的 XWayland / xcb 模式。系统已安装 `libxcb-cursor0` 时只需 `QT_QPA_PLATFORM=xcb python -m livetranslate live --config config/local.toml`。本机为避免全局安装，已将该库解包到项目缓存，可运行：

```bash
env -u PYTHONPATH QT_QPA_PLATFORM=xcb \
  LD_LIBRARY_PATH="$PWD/.cache/qt-xcb/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  python -m livetranslate live --config config/local.toml
```

缓存重建步骤和实际验证结果见 [M2 报告](docs/M2_REPORT.md)。这里只切换 Qt 显示平台，两类模型仍在同一 RX 9070 XT 上以 Vulkan 推理。

```bash
# 指定输出的 monitor；不会设置系统默认设备。
python -m livetranslate live --config config/local.toml --source MONITOR_NAME
# 定时运行、保存真实窗口截图及测量数据（音频须实际播放）。
python -m livetranslate live --config config/local.toml --duration 60 \
  --output-dir artifacts/my-live-session --screenshot artifacts/my-live-session/window.png
# 无界面模式可用于排查音频与模型；不作为 GUI 延迟验收。
python -m livetranslate live --config config/local.toml --headless --duration 60
```

每次运行使用新的目录，保存 events.jsonl、模型日志、资源数据及 summary.json；默认目录自动命名。日志轮转，音频只在内存的有限帧/分段中处理，不连续写录音。状态栏会提示断开、恢复或旧任务丢弃。停止按钮、关闭窗口和 Ctrl+C 都会结束采集、排空有限队列并释放拥有的子进程。

配置 `[stream]` 控制 VAD、停顿、临时识别间隔、最长分段、队列、超时和外观，见 [架构说明](docs/M2_ARCHITECTURE.md)。不含 `[stream]` 的旧 M1 配置继续可用，常驻 ASR 可执行文件默认从 whisper-cli 同目录推导。字幕延迟从 VAD 语音帧进入应用算到可见文字控件实际完成绘制；截图重绘和隐藏窗口不计入，详细测量边界见性能文档。

真实 HDMI 自动验收会播放所给的英语文件：

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

构建固定在 [native-lock.json](config/native-lock.json) 的官方提交。已有 Whisper 也可通过 `--whisper-source /path/to/whisper.cpp` 指定。原工作树及其本地修改不会被覆盖。Vulkan 构建失败时保存错误并尝试 CPU，实际后端写入 `artifacts/native-runtime.toml`。只有 CPU 构建通过不代表 GPU 通过；需要使用该配置运行并记录真实识别。可用 `--no-cpu-fallback` 要求构建直接失败。

下载脚本匿名访问 Hugging Face，固定 [model-lock.json](config/model-lock.json) 的 revision，并核对远端公布的大小与 SHA-256。无需 Hugging Face 登录或 Token。下载失败保留 `.part`，重跑续传；最终权重仅在校验通过后发布到 models/，不提交 Git。已有损坏的最终文件会报错并保留。

本次环境的可复现 Conda 包集合为 [conda-linux-64.lock](config/conda-linux-64.lock)。同平台可用它替代环境创建命令：

```bash
CONDA_PKGS_DIRS="$PWD/.cache/conda-pkgs" conda create -p "$PWD/.conda" --file config/conda-linux-64.lock -y
```

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

本机已生成被 Git 忽略的 `config/local.toml`，选择本次验证的 HDMI 源，可直接用 `python -m livetranslate demo --config config/local.toml --duration 15`。全新检出时需自行选择可用源。

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

主要覆盖参数：`--source`、`--duration`、`--asr-model`、`--asr-binary`、`--backend`、`--device`、`--base-url`、`--translation-model`、`--max-tokens`。服务端另支持 `--model`、`--binary`、`--port`。API 默认 max_tokens=256、temperature=0、seed=42；达到 token 上限会报告不完整翻译。

## 测试和真实验收

```bash
# 清除外部 ROS 等工具链注入的 PYTHONPATH；只影响这个命令。
env -u PYTHONPATH python -m pytest -q --junitxml=artifacts/pytest-unit.xml

mkdir -p artifacts
cp vendor/whisper.cpp/samples/jfk.wav artifacts/jfk.wav
LIVETRANSLATE_REAL=1 env -u PYTHONPATH python -m pytest -q -m real --junitxml=artifacts/pytest-real.xml

# 真正播放官方英语样本，捕获系统 monitor，再经过真实 Small.en 和 Qwen。
# 该命令会向所选设备播放约 11 秒英语；不改变系统默认输出。
env -u PYTHONPATH python scripts/validate_real.py --source MONITOR_NAME
env -u PYTHONPATH python scripts/benchmark.py --iterations 3
python scripts/doctor.py --hip-smoke --output artifacts/doctor.json
```

未启用真实测试时 `real` 测试明确跳过，不代表通过。`validate_real.py` 必须有健康本地服务和可播放输出；没有这些条件时报告失败。测试默认使用官方 JFK 英文语音（真实人声），单元测试的正弦波不用于真实验收。HIP probe 只验证一个内核的运算，不能代表已验证 Whisper/LLM HIP 后端。

Vulkan、PulseAudio socket 或 `/dev/dri` 在隔离执行沙箱内不可见时，需要在拥有当前桌面会话设备权限的本机终端运行。不要通过修改驱动、设为全局音频服务器或放宽网络监听来处理沙箱隔离。

## 交付范围

接口与边界见 [M1 架构文档](docs/architecture.md) 和 [M2 架构文档](docs/M2_ARCHITECTURE.md)。权重、音频样本、原生源码/产物、日志、动态 JSON 和本机配置被 `.gitignore` 排除。Windows 采集适配器暂未实现，已有文件的数据流和字幕状态机不绑定 Linux；没有麦克风输入、HIP 适配或模型替换。
