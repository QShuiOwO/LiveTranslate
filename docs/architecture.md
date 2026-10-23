# M0/M1 架构

本文记录 M0/M1 的同步批次架构，继续兼容。项目使用 Python src 布局。命令行负责加载 TOML、选择平台适配器和保存结果；核心管线只依赖协议，不导入 Linux 实现。M2 的 GUI、VAD 和有界连续处理见 [M2_ARCHITECTURE.md](M2_ARCHITECTURE.md)。

```text
CLI + TOML
  -> TranslationPipeline
       -> AudioCapture          LinuxAudioCapture (pactl + FFmpeg pulse)
       -> ASRBackend            WhisperBackend (local whisper-cli)
       -> TranslationBackend    LlamaTranslator (127.0.0.1 HTTP)
```

`interfaces.py` 中三个 Protocol 分别约定采集、识别和翻译。`TranslationPipeline` 组织阶段、短路失败、保留已完成阶段的原文和耗时。未来添加 WASAPI Loopback 只需实现 `AudioCapture` 并在 CLI 的适配器工厂注册；已有 WAV 的路径不依赖 Linux 采集。

## 音频与错误边界

Linux 适配器读取 pactl JSON，通过 sink 的 monitor 关系或明确的 monitor 元数据识别监听源。不会使用默认输入设备，也不会只凭名称后缀认定 monitor。优先默认输出的 monitor，允许 `--source` 覆盖；默认设备断开时报告错误。未找到默认 monitor 且有多个候选时要求显式选择。

FFmpeg 使用参数列表调用，不经过 shell，捕获 10–30 秒，输出 16 kHz、单声道、PCM16 WAV。临时文件经格式、帧数、截断、响度和设备再次枚举校验后才发布；已有录音不覆盖。静音阈值可配置，默认 -60 dBFS；低于阈值的非常轻声音频也会被拒绝。

已有文件模式允许最多 300 秒 WAV，以限制 M1 的内存和推理规模。文件必须已经规范化，README 提供 FFmpeg 转换指令。失败返回 `success=false`、`error_code`、可读原因及实际耗时；不会生成伪字幕。

`run_command` 处理启动异常、退出码、标准错误和超时。Whisper 使用私有临时输出前缀读取官方 JSON。结果和运行日志保存至被 Git 忽略的 artifacts；JSON 采用临时文件与原子替换，拒绝 NaN/Infinity。

## 本地模型与 GPU 证据

Whisper 默认 Small.en，英文转录，不使用 Whisper 翻译任务。请求 Vulkan/HIP 时，适配器必须在日志中看到 GPU 后端初始化和权重分配；缺少证据即失败，不会把 CPU 回退当作 GPU 成功。Vulkan 软件驱动不算 GPU。CPU 后备由配置或命令行明确选择。

llama-server 只绑定 `127.0.0.1`，显式使用本地 GGUF、固定模型别名、单槽、4096 context 和离线模式。启动器核验健康状态、权重缓冲及 GPU 层 offload 日志，保留 server-ready.json；退出时回收子进程。

固定提交的引擎 offload 日志需要 `--verbosity 4`，启动器明确设置该级别以取得证据。

翻译器仅接受字面地址 `http://127.0.0.1:PORT`，禁用环境代理并拒绝重定向。每次请求检查健康和服务模型别名，通过 `/v1/chat/completions` 发送文本。默认 temperature=0、top_k=1、seed=42、max_tokens=256，不启用 streaming，禁用请求 prompt cache 以使基准可比。输出被截断、含分析标签或翻译前缀时返回失败。简体中文、忠实度和专有名词保留主要依赖提示与真实样本验收，未加入复杂语言质量模型。

GPU sysfs 采样间隔 100 ms，记录全设备显存基线、峰值、结束值和利用率。显存含桌面及其他进程，不能单独证明推理；须结合权重分配、层 offload、真实响应和 CPU 对照。HTTP 响应本身不证明 GPU 使用，单次翻译结果明确保留这一限制。

## 配置与测试

`config/default.toml` 是可提交的便携配置，相对路径按 TOML 所在目录解析。CLI 的路径参数按当前工作目录解析并覆盖配置。`config/local.toml` 与 `*.local.toml` 被忽略，可存本机设备与绝对路径。未知字段和非法数值会报错，避免拼写错误被静默忽略。

`config/native-lock.json` 固定官方原生源码 commit；`config/model-lock.json` 固定模型仓库 revision、大小和 SHA-256。下载不要求登录。构建脚本先检查已有官方 Whisper/llama 仓库，复制固定提交的 Git 对象到项目内构建，保留原工作树及其修改。源码、产物按 commit 分开保存，旧运行版本可回退；每次尝试的日志独立保存。Vulkan 编译失败会保存错误后尝试独立 CPU 构建，生成明确标注后端的候选配置，只有真实推理通过后才切换本机配置。当前升级结果见 [原生引擎升级报告](NATIVE_UPGRADE.md)。

pytest 单元和 Fake 集成测试不加载模型。真实模型测试需显式设置 `LIVETRANSLATE_REAL=1`；播放回采验收由 `scripts/validate_real.py` 完成，使用官方英语语音经真实输出设备播放，再捕获其 monitor。正弦波测试数据只用于单元测试，绝不用于真实模型验收。
