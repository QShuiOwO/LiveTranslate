# M2 连续系统音频与字幕

M1 的 AudioCapture、ASRBackend、LlamaTranslator、TranslationPipeline 继续服务批次命令。M2 使用 streaming/ 和 LinuxContinuousCapture；连续接口不依赖 Qt 或具体音频系统。当前默认 Qwen3-ASR-1.7B + HY-MT2-7B，Whisper + Qwen3-4B 作为显式回退配置保留。原始 M2 长测对应旧模型，当前模型切换仅做短测。

```text
LinuxContinuousCapture (monitor only, FFmpeg PCM20ms)
   -> bounded frame queue (100 frames / 2s)
   -> WebRTC VAD + energy floor, bounded utterance (<=10s)
   -> ASR queue (3 finals + 1 latest draft)
   -> persistent local speech.cpp /v1/audio/transcriptions (or whisper-server /inference)
   -> CaptionBook / EventBus -> Qt main-thread widgets
   -> translation queue (3 finals + 1 latest draft)
   -> local HY-MT2 Chat Completions -> same caption ID
```

## 分段、任务和缓冲

音频固定为 16 kHz、单声道 PCM16，20 ms/帧。复用 M1 的 pactl monitor 识别、默认输出优先及显式源覆盖；没有麦克风入口。FFmpeg fragment_size=640，flush_packets=1，独立线程用 selector 读取 PCM 和有界 stderr；无数据 3 秒或源检查失败后重新枚举相同目标，按 0.5–5 秒退避重连。没有全局音频配置写入。

后续输出适配：source 为空时，2 秒设备检查重新选择当前系统默认输出；发现 monitor 改变则回收旧 FFmpeg，重连新源并更新 epoch，由既有缺口处理切断上下文。显式 source 始终固定。蓝牙输出通过 busctl/BlueZ Device1 GetAll 只读取得 Connected 与 Alias，覆盖 PipeWire 可能陈旧的蓝牙字段；每个设备单次枚举只查询一次，超时 2 秒，查询失败时回退 pactl 信息。source UNAVAILABLE 和不可用硬件端口仍会阻止采集。

WebRTC VAD mode=2，附加默认 -52 dBFS 能量门限；所有帧均送入 VAD 状态机。保留 200 ms 前导，至少 240 ms 有声才处理，560 ms 停顿结束一段，最终音频仅留最后有声帧后 160 ms。每 1 秒尝试临时识别，最小窗口 1.2 秒；持续说话最多 10 秒强制分段。强制边界不做音频重叠，因此不会从重叠窗口重复提交同一采样；边界可能拆词，属于当前质量限制。

采集、VAD、ASR、翻译各用一个工作线程；UI 仅在主线程用 33 ms timer 合并事件。帧队列溢出丢最旧帧，sequence/epoch 变化切断上下文。ASR/翻译队列优先最终任务，只保留最新临时任务；最终队列满时明确丢最旧并显示缺口。任务排队超过 6 秒会过期，避免无限追赶旧音频。每个分段、队列、缓存、UI 历史和统计采样都有容量上限。

## 增量与稳定性

相邻识别结果计算词级共同前缀。翻译优先稳定的句读完整片段；没有句读时至少 6 个稳定词并去掉末尾功能词，属于启发式完整度，不是语法模型。临时翻译需比前次增加至少 4 个词；最终结果总会请求翻译，命中文本缓存则复用真实先前结果。ASR 按音频内容摘要复用完全相同的输入；两类缓存按插入顺序淘汰。

CaptionBook 对每段使用单调 ID 与 revision。临时结果可更新，英文 final 一次确认后冻结；中文 final 一次完成后冻结。旧 revision、旧 ID 或已确认行的改写会被拒绝。中文临时译文只有在来源仍是新英文前缀时保留，避免英文每增长一次就清空中文；不再匹配时撤下。UI 按 ID 更新现有行，只有首次 final 插入历史。历史、已确认 ID 水位和事件合并保证不会因迟到消息重复插入已淘汰的行。

保留的字幕行最多 history_limit+4，Qt 历史控件最多 history_limit（默认 80）。翻译文本缓存 128，ASR 音频摘要缓存 32，只有有限在途 PCM。延迟序列每项最多 10,000 条，资源采样最多 1,200 条；超过时报告保留窗口及 total_n。应用事件日志 16 MiB、2 个备份；每个原生服务日志 8 MiB、2 个备份。

## 服务与恢复

默认常驻 Qwen3-ASR 由 speech.cpp 运行，绑定 127.0.0.1:8083；启动时显式选择 Vulkan0、验证硬件设备身份和加载模型的设备日志，并执行引擎默认 warmup。HTTP 上传 WAV，指定模型及英语，只接受 `X-Speech-Stop: complete`。speech.cpp 不接受 Whisper 的 temperature 等字段，两套 HTTP 适配器使用各自的请求协议。原生服务最长启动等待 30 秒，请求仍受 4 秒超时与有限队列约束。无需官方 Python/Torch/vLLM，也不调用远程推理服务；应用分段式临时识别不等同于官方原生流式。

翻译默认 HY-MT2-7B Q4_K_M，由新版 llama.cpp 绑定 127.0.0.1:8080。通过 HY-MT 的用户翻译指令生成简体中文，temperature=0、最多 256 tokens；模型原始推荐采样参数与本项目确定性字幕参数不同。

以下 Whisper 协议适用于回退配置：

使用同一 Whisper 提交新建常驻 Vulkan whisper-server，只绑定 127.0.0.1:8082，接收内存 WAV multipart。固定源码默认 no_context=true，每次请求使用独立文本上下文；该提交不接受 README 里遗留的 -nc 参数，因此不传。temperature 和 temperature_inc 都为 0，禁用非语音 token，不在服务上启用 FFmpeg 上传转换。

两个翻译模型均沿用 llama.cpp、本地 GGUF、单槽与离线模式。HTTP 客户端均禁用代理、拒绝重定向。启动前验证 GPU 权重日志，Whisper 仍拒绝软件 Vulkan。已存在的健康本地 llama-server 可复用，但要校验模型别名；不拥有的服务不终止、不自动重启，GPU 证据需由外部启动者提供。自有进程异常后按需重建，最终任务在未过期时最多重试一次；失败显示状态，不用假结果填补。

停止先关闭采集，VAD 刷新尾段，排空有限 ASR/翻译任务，然后结束拥有的原生进程并回收日志线程。GUI 等收尾结束后再保存报告。外部服务保持运行。GUI 可见时通过专属 D-Bus 连接申请临时 idle inhibitor；关闭连接释放，不改变系统设置、不解锁桌面。

窗口使用 WA_DeleteOnClose，在 Qt 事件循环中销毁控件树，避免信号/闭包引用把销毁推迟到 QApplication 之后的 Python 垃圾回收。关闭语义见 [Qt 官方说明](https://doc.qt.io/qt-6/qwidget.html#close)。窗口 visible/exposed/minimized 状态发生变化时记录事件，用于定位显示测量缺口；不强制抢占其他应用焦点。

## 显示与测量

Qt 使用一个 subtitle_container，内部 QVBoxLayout 将实时区和完成区按 1:1 分配剩余空间，顶部独立工具栏保留字号、不透明度、主题、置顶、历史和停止操作。两个字幕区为圆角 QFrame，内部独立 QScrollArea；PlainText 标签按实际宽度自动换行，超长内容滚动。标签垂直 sizePolicy 为 Preferred，使 heightForWidth 能正确计算高度，避免较窄 sizeHint 在宽窗口制造多余滚动范围。

实时区显示 ASR partial，以及 ASR final 但翻译未完成的结果。仅 zh_final 的完整双语结果进入下框与历史；上一句翻译完成时不清空更新一句的实时内容。错误继续在实时区显示，已完成区保留最后成功的双语结果。开启历史时在下框内部切换到有界列表，不新增第三个字幕区域；rangeChanged 后按显式 follow_history 状态跟到底，用户上滚后停止跟随。

默认 light 主题使用 WA_TranslucentBackground、FramelessWindowHint，背景不透明度 0.4、中英字号均为 16、文字为 #000000。opacity 配置表示背景不透明度（0–1），窗口自身 opacity 始终为 1.0，不调用 setWindowOpacity；mainSurface 单独绘制 rgba 背景，字幕框、滚动区和历史卡不另加填充，避免 40% 叠加成 64%。滑块支持 0–100% 并显示百分比，light/dark 可调，transparent 固定无填充。无系统标题栏时由自有标题支持 startSystemMove（X11 不支持时用位置拖动回退），双击放大/还原，右下角 QSizeGrip 调整窗口大小，最小化与关闭按钮仍进入原有资源释放流程。透明窗口平台要求见 [Qt QWidget 官方文档](https://doc.qt.io/qt-6/qwidget.html#creating-translucent-windows)。此背景 alpha 实现无需整体窗口 opacity 支持，Wayland 也启用滑块；置顶由合成器决定。

Qt 平台遵循 QT_QPA_PLATFORM；Ubuntu GNOME Wayland 会话也可选择 xcb，通过 XWayland 显示。测试中 xprop 确认 xcb 窗口具有 _NET_WM_STATE_ABOVE，原生 Wayland 的未曝光区间另行记录。显示平台不改变 Vulkan 推理后端，也不更改系统桌面设置。

音频的 monotonic_ns 来自 FFmpeg PCM pipe 的读入时刻，同一读批次按 20 ms 回推各帧。VAD 第一/最后有声帧分别定义 onset/end。英文/中文首次显示及 final 双语完成使用 QLabel.paintEvent 完成后的时刻；仅窗口可见且 exposed 时记录，截图 grab 的绘制明确排除。最终双语时间取英文 final 和中文 final 两次绘制的较晚值。headless 只报告模型/队列耗时，不伪造 UI 时延。

这些数据覆盖应用接收到 monitor 音频后的管线，不测量最初 Pulse/HDMI 缓冲、VAD 边界误差和物理屏幕扫描延迟；也不能将整段 WAV 处理时间称为流式字幕延迟。CPU 为应用及拥有的子进程时间增量（100%=1 核）；RSS 分应用与进程树，GPU/VRAM 为设备范围。长测报告单独给出 P50/P95、样本量和内存时间窗。
