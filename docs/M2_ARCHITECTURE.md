# M2 连续系统音频与字幕

M1 的 AudioCapture、WhisperBackend、LlamaTranslator、TranslationPipeline 继续服务批次命令。M2 新增 streaming/ 和 LinuxContinuousCapture；连续接口不依赖 Qt 或具体音频系统。模型、量化、官方原生源码提交沿用 M1。

```text
LinuxContinuousCapture (monitor only, FFmpeg PCM20ms)
   -> bounded frame queue (100 frames / 2s)
   -> WebRTC VAD + energy floor, bounded utterance (<=10s)
   -> ASR queue (3 finals + 1 latest draft)
   -> persistent local whisper-server /inference
   -> CaptionBook / EventBus -> Qt main-thread widgets
   -> translation queue (3 finals + 1 latest draft)
   -> local Qwen Chat Completions -> same caption ID
```

## 分段、任务和缓冲

音频固定为 16 kHz、单声道 PCM16，20 ms/帧。复用 M1 的 pactl monitor 识别、默认输出优先及显式源覆盖；没有麦克风入口。FFmpeg fragment_size=640，flush_packets=1，独立线程用 selector 读取 PCM 和有界 stderr；无数据 3 秒或源检查失败后重新枚举相同目标，按 0.5–5 秒退避重连。没有全局音频配置写入。

WebRTC VAD mode=2，附加默认 -52 dBFS 能量门限；所有帧均送入 VAD 状态机。保留 200 ms 前导，至少 240 ms 有声才处理，560 ms 停顿结束一段，最终音频仅留最后有声帧后 160 ms。每 1 秒尝试临时识别，最小窗口 1.2 秒；持续说话最多 10 秒强制分段。强制边界不做音频重叠，因此不会从重叠窗口重复提交同一采样；边界可能拆词，属于当前质量限制。

采集、VAD、ASR、翻译各用一个工作线程；UI 仅在主线程用 33 ms timer 合并事件。帧队列溢出丢最旧帧，sequence/epoch 变化切断上下文。ASR/翻译队列优先最终任务，只保留最新临时任务；最终队列满时明确丢最旧并显示缺口。任务排队超过 6 秒会过期，避免无限追赶旧音频。每个分段、队列、缓存、UI 历史和统计采样都有容量上限。

## 增量与稳定性

相邻识别结果计算词级共同前缀。翻译优先稳定的句读完整片段；没有句读时至少 6 个稳定词并去掉末尾功能词，属于启发式完整度，不是语法模型。临时翻译需比前次增加至少 4 个词；最终结果总会请求翻译，命中文本缓存则复用真实先前结果。ASR 按音频内容摘要复用完全相同的输入；两类缓存按插入顺序淘汰。

CaptionBook 对每段使用单调 ID 与 revision。临时结果可更新，英文 final 一次确认后冻结；中文 final 一次完成后冻结。旧 revision、旧 ID 或已确认行的改写会被拒绝。中文临时译文只有在来源仍是新英文前缀时保留，避免英文每增长一次就清空中文；不再匹配时撤下。UI 按 ID 更新现有行，只有首次 final 插入历史。历史、已确认 ID 水位和事件合并保证不会因迟到消息重复插入已淘汰的行。

保留的字幕行最多 history_limit+4，Qt 历史控件最多 history_limit（默认 80）。翻译文本缓存 128，ASR 音频摘要缓存 32，只有有限在途 PCM。延迟序列每项最多 10,000 条，资源采样最多 1,200 条；超过时报告保留窗口及 total_n。应用事件日志 16 MiB、2 个备份；每个原生服务日志 8 MiB、2 个备份。

## 服务与恢复

使用同一 Whisper 提交新建常驻 Vulkan whisper-server，只绑定 127.0.0.1:8082，接收内存 WAV multipart。固定源码默认 no_context=true，每次请求使用独立文本上下文；该提交不接受 README 里遗留的 -nc 参数，因此不传。temperature 和 temperature_inc 都为 0，禁用非语音 token，不在服务上启用 FFmpeg 上传转换。

Qwen 仍是 M1 的 llama.cpp、本地 GGUF、单槽与离线模式。HTTP 客户端均禁用代理、拒绝重定向。启动前验证 GPU 权重日志，Whisper 仍拒绝软件 Vulkan。已存在的健康本地 Qwen 可复用，但要校验模型别名；不拥有的服务不终止、不自动重启，GPU 证据需由外部启动者提供。自有进程异常后按需重建，最终任务在未过期时最多重试一次；失败显示状态，不用假结果填补。

停止先关闭采集，VAD 刷新尾段，排空有限 ASR/翻译任务，然后结束拥有的原生进程并回收日志线程。GUI 等收尾结束后再保存报告。外部服务保持运行。GUI 可见时通过专属 D-Bus 连接申请临时 idle inhibitor；关闭连接释放，不改变系统设置、不解锁桌面。

窗口使用 WA_DeleteOnClose，在 Qt 事件循环中销毁控件树，避免信号/闭包引用把销毁推迟到 QApplication 之后的 Python 垃圾回收。关闭语义见 [Qt 官方说明](https://doc.qt.io/qt-6/qwidget.html#close)。窗口 visible/exposed/minimized 状态发生变化时记录事件，用于定位显示测量缺口；不强制抢占其他应用焦点。

## 显示与测量

Qt 显示临时卡片、最终双语卡片和可展开的有限历史；文本按 PlainText 绘制并自动换行。字号、深浅主题和历史可调整。Wayland 的透明度控件禁用，置顶提示由合成器决定。

Qt 平台遵循 QT_QPA_PLATFORM；Ubuntu GNOME Wayland 会话也可选择 xcb，通过 XWayland 显示。测试中 xprop 确认 xcb 窗口具有 _NET_WM_STATE_ABOVE，原生 Wayland 的未曝光区间另行记录。显示平台不改变 Vulkan 推理后端，也不更改系统桌面设置。

音频的 monotonic_ns 来自 FFmpeg PCM pipe 的读入时刻，同一读批次按 20 ms 回推各帧。VAD 第一/最后有声帧分别定义 onset/end。英文/中文首次显示及 final 双语完成使用 QLabel.paintEvent 完成后的时刻；仅窗口可见且 exposed 时记录，截图 grab 的绘制明确排除。最终双语时间取英文 final 和中文 final 两次绘制的较晚值。headless 只报告模型/队列耗时，不伪造 UI 时延。

这些数据覆盖应用接收到 monitor 音频后的管线，不测量最初 Pulse/HDMI 缓冲、VAD 边界误差和物理屏幕扫描延迟；也不能将整段 WAV 处理时间称为流式字幕延迟。CPU 为应用及拥有的子进程时间增量（100%=1 核）；RSS 分应用与进程树，GPU/VRAM 为设备范围。长测报告单独给出 P50/P95、样本量和内存时间窗。
