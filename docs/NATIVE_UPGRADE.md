# 原生引擎 Vulkan 升级与回归

日期：2026-10-23。Ubuntu 24.04、Ryzen 7 9700X、RX 9070 XT，Python 3.12.15、GCC 13.3.0、CMake 3.28.3、Mesa RADV 25.2.8。

**两个新版已完成 Vulkan 编译、真实模型验证并接入默认配置。HDMI 系统音频端到端通过；本轮没有重做新版的 30 分钟验收。追加蓝牙试验的样本内容核对未通过，单独保留失败记录。**

## 版本与构建

| 组件 | 原版本 | 当前版本 |
| --- | --- | --- |
| whisper.cpp | `200b1197907545a88c5a00fb15f52e2cf88af6f5`，2026-06-18 | [v1.9.5](https://github.com/ggml-org/whisper.cpp/releases/tag/v1.9.5)，`d1be6fde11ac6e0407606b4e42fe72d34add8037` |
| llama.cpp | `d88040c704c8855c497d46c20675a3d492f70f94`，2026-10-22 | [2026-10-23 开发提交](https://github.com/ggml-org/llama.cpp/commit/f5027fb21307eb264aed483242603d68c87b0fe3)，`f5027fb21307eb264aed483242603d68c87b0fe3` |
| ASR 权重 | Whisper Small.en | 同一文件及参数，见 config/model-lock.json |
| 翻译权重 | Qwen3-4B-Instruct-2507 Q4_K_M | 同一文件及参数，见 config/model-lock.json |

复用用户已下载、origin 为官方 ggml-org 的两个 Git 仓库。构建脚本复制固定提交的 Git 对象到项目中，保留外部工作树和本地修改；源码与产物按提交分别保存，避免覆盖旧运行版本。保留 Git 元数据也让原生程序报告正确的源码提交。

本次使用 `GGML_VULKAN=ON`、`GGML_CUDA=OFF`、`GGML_HIP=OFF`、Release、8 个编译任务，未启用 CPU 构建后备。Whisper 的配置和编译耗时 57.60 秒，llama.cpp 为 88.93 秒；两者均成功。源码未做上游代码修改，未安装驱动或修改系统音频配置。

在项目根目录、项目 Python 环境中可复现：

```bash
python scripts/build_native.py --jobs 8 --backend vulkan --no-cpu-fallback \
  --whisper-source "$HOME/AI/whisper.cpp-v1.9.5" \
  --llama-source "$HOME/AI/llama.cpp-latest"
```

没有本地源码时按 config/native-lock.json 从官方仓库取固定提交。当前产物目录为：

- build/whisper-vulkan-d1be6fde11ac/：whisper-cli、whisper-server 及其库。
- build/llama-vulkan-f5027fb21307/：llama-server、llama-bench 及其库。

默认 TOML、Python 默认配置和被忽略的 config/local.toml 已切换到上述产物；单命令启动入口继续使用这些配置。Whisper 监听 127.0.0.1:8082，Qwen 监听 127.0.0.1:8080，均显式指定设备和端口。

本轮构建清单与完整日志位于 artifacts/native-upgrade-20261023/build/。所有运行证据均在被忽略的 artifacts/native-upgrade-20261023/ 中，未提交权重、录音、日志或本机配置。

## GPU 证据

- Whisper 日志识别出 RX 9070 XT / RADV GFX1201，记录 Vulkan0 模型分配 487.00 MB，以及 `whisper_backend_init_gpu: using Vulkan0 backend`；真实识别 JFK 英语样本，结果包含完整的 country 问句。
- llama-server 日志明确选择同一 Vulkan0 / RX 9070 XT，模型缓冲 2375.91 MiB，37/37 层 offload；真实本地 Chat Completions 将测试句译为“人工智能正在改变世界。”。
- 同一 11 秒人声样本在新版 GPU 路径的批次中位数为 292.34 ms，CPU 路径为 1211.98 ms。两者均产生真实识别结果；实测 CPU/GPU 比值约 4.15。

这些证据同时包含设备选择、模型分配、真实输出和 CPU/GPU 对照，没有用健康检查或设备枚举代替实际推理验证。

## 自动化与真实运行

| 验证 | 实际结果 |
| --- | --- |
| 完整 pytest，显式启用真实模型 | **137 passed，0 skipped**；含 3 项真实模型测试，JUnit：pytest-final.xml |
| 新增构建保护测试 | 9 项：固定提交、官方来源、保留下载目录修改和旧源码、拒绝覆盖未知目录、失败日志分别保留、默认配置与构建路径一致 |
| 新增活动蓝牙后备测试 | 8 项：RUNNING 状态与端口条件、BlueZ 明确断开优先、非活动源、不可用或未知端口仍拒绝 |
| M1 真实 HDMI 15 秒录音到英中结果 | PASS；原文包含 country、译文包含“国家”；m1-system-e2e.acceptance.json |
| 原引擎 HDMI / JFK / GUI 35 秒 | PASS；4 条最终双语，4/4 实际绘制；baseline-live/ |
| 新引擎 HDMI / JFK / GUI 35 秒 | PASS；4 条最终双语，4/4 实际绘制；candidate-live/ |
| 切换后 135 秒连续系统音频 | PCM 134.24 秒，26 条最终双语、0 推理错误、0 丢帧/任务、0 重连、0 最终改写，退出无遗留线程；integrated-live/ |
| 上述 135 秒的完整显示覆盖 | **未通过**；窗口约第 40 秒最小化，只有前 7/26 条有有效绘制事件，不能把这 7 条统计当作完整运行时延 |
| 从 /tmp 执行 livetranslate-start，当前默认蓝牙输出 | 后备修复后采集 34.24 秒，5 条最终双语、5/5 实际绘制，0 推理错误/丢帧/重连，无遗留线程；launcher-live-fixed/ |
| 当前蓝牙输出的定向 JFK 内容验收 | **未通过**；播放进程正常、捕获非静音、模型返回非空，但识别内容未匹配 country、译文未匹配“国家”；m1-bluetooth-e2e.acceptance.json |
| 新引擎 30 分钟长测 | **未重跑**；原 M2 的 30 分钟结果对应旧提交，不能作为新版长测通过的依据 |

原始自动验收器对非长测只要求至少一次真实双语绘制，因此 integrated-live/acceptance.json 中的 success=true 不代表全部 26 条都可见。本报告另核对最终字幕 ID 的实际绘制覆盖，保留未达到完整显示覆盖的结论。

## 前后性能对比

顺序执行旧版和新版各 5 次，统一使用官方 11 秒 JFK WAV、同一 Small.en 和 Qwen 文件、8 个线程。下表为中位数；不是实时字幕的语音到显示延迟。

| 指标 | 原引擎 | 新引擎 |
| --- | ---: | ---: |
| ASR Vulkan 批次耗时 | 275.96 ms | 292.34 ms |
| ASR CPU 批次耗时 | 1196.94 ms | 1211.98 ms |
| ASR Vulkan 音频秒/处理秒 | 39.86 | 37.63 |
| 翻译 HTTP 完整调用 | 121.11 ms | 119.77 ms |
| 翻译后端生成吞吐 | 134.11 token/s | 134.24 token/s |

ASR 耗时含 WAV 检查、进程/模型初始化、推理与解析，文件与着色器缓存可能已热。翻译使用已加载的本地服务，含健康检查、模型检查与生成，cache_prompt=false；后端吞吐来自响应 timings，测试输出只有 6 个 token，不能代表长输出吞吐。

本轮小样本没有证明整体提速：ASR 批次约增加 5.94%，翻译完整调用接近；不从 5 次顺序采样推导普遍性能结论。

同一 HDMI / JFK 的短测，所有显示指标 n=4：

| 语音到真实 Qt 绘制 | 原引擎 P50 / P95 | 新引擎 P50 / P95 |
| --- | ---: | ---: |
| 英文首条 | 1093.84 / 1197.98 ms | 1131.74 / 1199.65 ms |
| 中文首条 | 2769.29 / 3510.50 ms | 2749.92 / 3500.00 ms |
| 语音结束到最终双语 | 775.88 / 793.41 ms | 767.22 / 783.46 ms |

使用 monotonic_ns，起点为 VAD 语音帧进入应用，终点为 visible、exposed 且未最小化的 QLabel 完成绘制；排除截图重绘。未包括初始 Pulse/HDMI/蓝牙缓冲、合成器与物理面板延迟。n=4 仅是短测，无法替代长时间尾延迟统计。

135 秒运行中应用 RSS 峰值 136.45 MiB、拥有的进程树 RSS 峰值 819.29 MiB；5 秒采样的整卡显存峰值 5.61 GiB、GPU busy 峰值 11%、进程树 CPU 峰值 17.78%（100%=一个逻辑核）。整卡显存包含桌面与其他进程，稀疏采样可能漏掉短峰值；不将它标为模型专用显存，也不把 135 秒的内存观察标为长期无泄漏。

## 蓝牙现场问题与边界

测试期间系统默认输出从 HDMI 切换到另一副蓝牙耳机。PipeWire 输出与 monitor 均为 RUNNING、活动端口 available，但 api.bluez5.connection 残留 disconnected；BlueZ GetAll 调用退出码为 1，无有效属性。原逻辑拒绝该默认源，首次单命令测试没有采到 PCM，明确失败，保留 launcher-live/ 与 acceptance.json。

直接 FFmpeg 探测采到 15.013 秒、16 kHz 单声道 PCM，RMS -20.03 dBFS，证明存在有效回采数据。增加受限的后备条件：只有 BlueZ 查询不可用、确认为输出 monitor、输出及源都为 RUNNING、活动端口明确可用时才尝试采集；BlueZ 明确断开或端口不可用仍拒绝。静止/未知状态不会仅凭这项后备被当作已连接。没有改变默认设备、profile、系统音量或系统音频服务。

修复后的默认源能产生并实际绘制双语字幕，但额外定向播放的 JFK 内容没有匹配，因此当前蓝牙路径只验证了采集、推理数据流和显示，不能声称通过了 JFK 语义内容验收。HDMI 的独立内容验收通过。默认源现场与诊断记录均保留，未用 Mock 或替换文本把失败改成通过。

将该次蓝牙录音分别交给旧版和新版 Whisper，二者均成功运行，但都没有识别出 country，且转写完全相同；这不是本次升级独有的识别变化。录音与 JFK 参考的短时能量包络最大相关约 0.19，仅作为输入差异的诊断，不作为识别评分。未确定样本未匹配的完整原因，保留 bluetooth-content-diagnostic.json；本轮不会据此宣称蓝牙受控内容验收通过。

随后只读检查还记录到一条未静音的浏览器播放流，因此没有获得隔离的蓝牙音源条件；不能把存在其他播放内容的采集等同于独立 JFK 录音。本轮没有停止用户的其他播放流来改变这一条件。各项通过、失败及未验证状态汇总于 upgrade-result.json。

## 启动与回退

正常启动继续使用：

```bash
livetranslate-start
```

保持字幕窗口可见以获得实际显示时延；最小化时继续处理音频，但不会伪造可见绘制时间戳。

本机已保留旧源码、旧 build/whisper-vulkan 与 build/llama-vulkan，以及原运行配置的绝对路径快照。可从任意目录临时回退：

```bash
livetranslate-start --config artifacts/native-upgrade-20261023/rollback.local.toml
```

该快照在被忽略的 artifacts/ 中，仅适用于本机。应用提交可通过 git log -1 --format=%H 获取；原生源码提交固定在 config/native-lock.json。Windows、HIP、多语言模型替换与新版 30 分钟验收不在本轮已完成项目中。
