# Qwen3-ASR + HY-MT2 模型切换与短测

日期：2026-10-23。结论：RX 9070 XT 16GB 可同时运行 Qwen3-ASR-1.7B Q8_0 和 Hy-MT2-7B Q4_K_M；真实文件、HDMI 回采及一分钟可见双语字幕测试通过。默认配置和一键启动已切换。按用户要求仅短测，本组合未做 30 分钟长测。

## 运行组合与兼容边界

| 部分 | 当前版本 / 模型 | 执行方式 |
|---|---|---|
| ASR | Qwen3-ASR-1.7B，Q8_0，2,176,109,216 bytes | speech.cpp 0.8.11，Vulkan0 |
| 翻译 | Hy-MT2-7B，Q4_K_M，4,624,648,896 bytes | llama.cpp，Vulkan0 |
| speech.cpp | `d074e1b9d84fe5ec530c959292ffe3d94ffd288b` | 社区 C++ / ggml 实现；本地 Release 构建 |
| speech.cpp ggml | `cbdeccfd49ec9c702c175c273d36c47520cf4710` | 固定子模块，Vulkan ON，CUDA/HIP OFF |
| llama.cpp | `f5027fb21307eb264aed483242603d68c87b0fe3` | 复用已更新的 Vulkan 构建 |
| whisper.cpp | v1.9.5，`d1be6fde11ac6e0407606b4e42fe72d34add8037` | 复用已更新的 Vulkan 构建，仅供 Small.en 回退 |

Qwen3-ASR 不能由 whisper.cpp 加载。当前链路是系统音频 → speech.cpp / Qwen3-ASR → llama.cpp / HY-MT2 → Qt 双语字幕。模型参数指定英语；多语言字幕逻辑本次未扩展。

Qwen GGUF 来自运行时作者的 [转换仓库](https://huggingface.co/sakasegawa/Qwen3-ASR-1.7B-GGUF)，revision `81e2423d24073cdeeccb5951df16accc89e075a2`，原始 [Qwen 权重](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) revision `7278e1e70fe206f11671096ffdd38061171dd6e5`。SHA-256 为 `5f219b78a1d9c3b9e97da27708b36f8a0bc1bfc1650b541c0a6dbaf87c9a62d0`。用户已有的 safetensors 文件保留，运行时使用经校验的 GGUF；转换布局属于 speech.cpp，不能任意替换成其他实现的同名 GGUF。

HY-MT2 直接复用已下载的文件，与[腾讯官方 GGUF](https://huggingface.co/tencent/Hy-MT2-7B-GGUF) revision `ab8472660ac61fac25f1af43fac2599d52a8a775` 的大小和 SHA-256 完全一致：`9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b`。完整清单见 `config/model-lock.json`。

使用[官方 HY-MT2 提示格式](https://huggingface.co/tencent/Hy-MT2-7B)：单条 user 消息要求译为简体中文，只输出译文。字幕生成选择 temperature=0、top_k=1、seed=42、max_tokens=256；与官方偏质量的随机采样推荐不同。输入、音频及推理均留在本机。

## 实现

- `ASRConfig.engine` 显式选择 `qwen3` 或 `whisper`。旧配置缺省仍为 Whisper；当前 default/local 配置显式选择 Qwen3。
- 文件 CLI、demo、benchmark 和真实 pytest 使用同一 ASR 工厂。Qwen 文件结果保持 transcript、耗时、模型、后端、错误及 GPU 证据结构。
- 连续模式使用常驻 `speech serve`，仅绑定 `127.0.0.1:8083`；翻译仍为 `127.0.0.1:8080`。HTTP 适配器不传 Qwen 不支持的 Whisper 参数，检查模型身份和 `X-Speech-Stop: complete`，达到模型上限不算最终识别成功。
- 复用 VAD、有限队列、临时/最终字幕、超时及服务恢复。此处为应用分段式增量识别；社区运行时不支持官方 vLLM 的原生流式状态，不能将两者混称。
- 启动前拒绝缺失/错误权重、无匹配设备、软件 Vulkan；运行请求不下载模型，禁用更新检查。子进程退出后按需重建，程序退出释放自有服务。
- `build_native.py` 增加固定版本 speech.cpp 及 ggml 子模块构建；源码、产物和模型都不提交 Git。不改变驱动、ROCm、系统默认音频设备。

## 短测验收

完整证据目录：`artifacts/qwen-hy-upgrade/`，不纳入 Git。

| 检查 | 结果 / 证据 |
|---|---|
| 自动化测试 | 160 passed，含 3 项真实模型测试；`pytest-final.xml` |
| 真实 Qwen ASR + HY 翻译 | 官方 JFK 11 秒人声，3 次常驻识别/翻译；英文 country、中文 国家及语义正确；`persistent-model-results.json` |
| CPU / Vulkan 对照 | 各 3 次文件识别成功，显式 CPU 与 Vulkan0；`benchmark.json` |
| 系统回采端到端 | JFK 通过 HDMI 播放、15 秒 monitor 捕获，再真实识别/翻译成功；`system-jfk.acceptance.json` |
| 一键启动与可见窗口 | `start.sh` 启动，60.39 秒会话，58.34 秒 PCM，播放 45 秒 LibriSpeech 真人语音，8 条最终双语字幕全部有有效绘制事件；`live-hdmi-short/acceptance.json` |
| 队列与退出 | 0 错误、0 丢帧、0 任务丢弃、0 过期、0 重新连接、0 最终字幕改写；退出无存活 worker；`live-hdmi-short/summary.json` |
| 识别服务恢复 | 主动终止本程序拥有的 Qwen 服务后，下一请求重启，真实转录一致；`recovery/result.json` |
| Whisper 回退 | 新版 whisper.cpp + 原 Small.en 真实 JFK 识别成功；`whisper-fallback.json` |

音频来源：项目已有 whisper.cpp 官方 `samples/jfk.wav` 和 OpenSLR LibriSpeech test-clean 素材。45 秒片段取自已记录来源的 `artifacts/m2-fixtures/english-30min.wav`；无 TTS、无将参考文本提供给 ASR。截图 `live-hdmi-short/subtitles.png` 已人工检查：中英字幕可见、正常换行。此次测试显式向 HDMI 播放并回采，日常启动仍跟随系统默认输出；当前蓝牙输出本次未重新验收。

## 实际速度与资源

11 秒 JFK，3 次顺序请求，中位数：

| 测量 | 结果 | 边界 |
|---|---:|---|
| Qwen 文件 Vulkan ASR | 929.89 ms | WAV 校验、设备检查、进程/模型加载、推理和解析 |
| Qwen 文件 CPU ASR | 2593.90 ms | 同上；Vulkan 总耗时约快 2.79 倍 |
| Qwen 常驻 ASR | 167.10 ms | 已加载模型，包含 HTTP；约 65.83 音频秒/处理秒 |
| HY 翻译完整 JFK 识别文本 | 307.64 ms | 健康检查、模型检查、HTTP、生成和解析 |
| HY 翻译 AI 示例句 | 120.12 ms | 同上，短句不能代表长句速度 |

文件 Vulkan 吞吐约 11.83 音频秒/处理秒。上述数字均不是实时字幕显示延迟；3 次样本只验证可行性，不足以声称广泛准确率或稳定性能优势。

一分钟 HDMI 实际窗口测量，8 条字幕：

| 时间区间 | P50 | P95 |
|---|---:|---:|
| 第一有声帧 → 首条英文绘制 | 1110.16 ms | 1255.55 ms |
| 第一有声帧 → 首条中文绘制 | 3382.32 ms | 4903.32 ms |
| 最后有声帧 → 最终双语绘制 | 935.29 ms | 1073.85 ms |
| 第一有声帧 → 最终双语绘制 | 4779.35 ms | 10268.66 ms |

采集时刻为 FFmpeg PCM 进入应用的 `monotonic_ns`，批次中的帧按 20 ms 回推。VAD 首/末有声帧定义语音起止；结束时刻是可见、曝光且未最小化窗口的 QLabel `paintEvent` 完成时间。排除截图 grab 的绘制，最终双语取中英文 final 的较晚绘制。未测量应用前的音频缓冲和合成器/物理屏幕延迟。8/8 最终字幕覆盖；P95 是小样本统计。

同场短测 ASR 请求 P50/P95 112.05/179.55 ms（41 次），翻译 224.07/340.02 ms（15 次）。音频/ASR/翻译队列高水位均为 1，无积压。

GPU 证据同时包含：

- speech.cpp 硬件设备信息识别 RX 9070 XT / RADV GFX1201，模型加载日志明确 Qwen3-ASR-1.7B 在 Vulkan0，真实请求成功。
- llama.cpp 日志明确该显卡、33/33 层 GPU offload、4403.24 MiB Vulkan 模型缓冲。
- 真实模型请求期间 `/proc/PID/fdinfo` 的 amdgpu DRM compute 计数非零；去重同一 DRM client 后，Qwen 进程显存约 2.32 GiB、HY 约 4.95 GiB（采样快照，非峰值）。
- 两模型同时驻留的 100 ms sysfs 采样：整卡基线 1.54 GiB，峰值 8.81 GiB，增量约 7.27 GiB，GPU busy 峰值 99%。整卡数字包含桌面及其他程序，不作为单一进程分配值。

GUI 短测的 5 秒资源采样：整卡显存峰值 8.81 GiB，GPU busy 最高 21%（稀疏采样，不能代表推理瞬时峰值）；应用 RSS 峰值 130.85 MiB，拥有的进程树 RSS 峰值 1054.10 MiB。进程树 CPU 最高 8.79%，其中 100% 表示一个逻辑核心；不包含窗口启动前的加载阶段。一分钟不能用于判断长期内存泄漏。

## 失败记录与限制

- GGUF 下载末尾遇到 TLS EOF，保留已下载分块，仅续传剩余约 7 MB，合并后通过完整大小/SHA-256 校验。
- 初次文件测试误传 `speech asr` 不支持的 `--no-warmup`，CLI 明确退出 2；原结果保留在 `first-benchmark.json` 和 `first-model-validation.log`。移除该参数并增加回归检查后，CPU/Vulkan 文件测试及 160 项测试通过。常驻服务保留引擎默认 warmup。
- 按要求未做 30 分钟长测、物理拔插或多语言验收；旧模型的 M2 长测结论不套用于新组合。
- 短测证明可运行，不是广泛准确率评测。HY 在额外 Vulkan 技术示例中将“translate subtitles”译成“渲染字幕”，存在语义偏差；文学短句与分段边界也会影响翻译。未宣称质量全面优于原模型。

## 使用与复现

```bash
# 日常启动：自动使用 config/local.toml，跟随系统当前声音输出。
livetranslate-start
# 明确选择旧模型回退，仍使用更新后的 whisper.cpp / llama.cpp。
livetranslate-start --config config/whisper-qwen.toml

# 新机器按 README 准备环境后：
python scripts/build_native.py --backend vulkan --no-cpu-fallback --jobs 8
python scripts/download_models.py

# 文件 / 批次命令：翻译服务须在另一终端运行。
python -m livetranslate serve
python scripts/transcribe_test.py --input artifacts/jfk.wav
python -m livetranslate demo --input artifacts/jfk.wav
LIVETRANSLATE_REAL=1 python -m pytest -q
python scripts/benchmark.py --iterations 3

# 真实短时播放回采，配置中选择对应 monitor；不会改系统默认输出。
python scripts/validate_stream.py --config config/local.toml --duration 35 \
  --speech artifacts/jfk.wav --launcher ./start.sh --output-dir artifacts/my-short-test
```

本机切换前配置的绝对路径快照为 `artifacts/qwen-hy-upgrade/rollback.local.toml`，只保存在被忽略的目录。此前模型、原始权重及引擎均保留。
