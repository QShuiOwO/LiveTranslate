# M2 验收报告

## 2026-10-24：英语／日语语言切换与默认背景 85%

新增窗口内英语 → 中文 / 日语 → 中文切换，ASR、翻译、VAD、队列与字幕状态同步隔离。默认背景改为 85%，字体仍为 16、浅色黑字。约 60 秒真实 HDMI 测试完成英语→日语→英语，10/10 最终双语可见，零错误／丢弃／最终改写；三个真人日语文件也完成本地识别和翻译，完整测试 246 项通过。存在部分专名与同音词识别错误，明确记录，不作为逐字准确率通过。功能、性能、小样本限制与证据见 [语言切换验收](LANGUAGE_SWITCH.md)。本次未做长测。

日期：2026-10-23；应用版本 0.2.0，基于 M1 `ea9dd8f1`。**M2 在 Ubuntu 24.04、真实 HDMI monitor、Qt xcb / XWayland 与两个 Vulkan 本地模型的条件下通过验收。** 原生 Wayland 全程曝光未通过，作为显示平台限制保留。

## 有限上下文翻译更新（2026-10-24）

实时翻译默认参考最近两段已确认英文，并约束字符数、时间和采集边界；缓存随上下文区分。用户报告的“do like this”误译在真实原完整段及前文回归中已修正，真正表达“喜欢”的对照保留正确词义。218 项测试及 35 秒真实 HDMI 短测通过，详见 [上下文报告](TRANSLATION_CONTEXT.md)。新功能尚未重跑长期验收。

## 当前模型更新

默认已切换为 Qwen3-ASR-1.7B Q8_0 + HY-MT2-7B Q4_K_M，使用 Vulkan；完整 160 项测试、真实 HDMI 回采和一分钟 8/8 可见双语字幕短测通过。仅原 Small.en + Qwen3-4B 组合拥有本文的 30 分钟验收。当前组合的兼容方式、短测性能、失败记录及限制见 [QWEN_HY_UPGRADE.md](QWEN_HY_UPGRADE.md)。

## 透明双区域 UI 更新

后续外观默认值调整为浅色主题、16 号中英文字、40% 背景不透明度。滑块仅影响单层背景，文字与边框保持完整不透明度；19 项 GUI 测试验证浅/深主题在 0/40/100% 的实际像素与历史区域无重复叠加，另有 171 项无需模型测试通过。此次仅调整外观，3 项真实模型测试未重跑；下述真实 HDMI 回归记录对应初版双区域界面。

初版背景改为真正透明无填充，文字黑色，上下圆角框共享一个可缩放容器并等比例分配空间；顶部为字号、不透明度、主题、置顶和历史等控件。上框保留识别/翻译中的双语结果，只有完整双语结果进入下框；历史在下框内部显示。标题可拖动，右下角手柄缩放，保留最小化、关闭与资源释放流程。

初版 167 项测试全部通过，其中 GUI 专项 12 项；实际 Qt xcb 预览检查了默认、700×420 和 1200×800 窗口尺寸。RGBA 像素检查证明窗口与框内空白 alpha=0、文本为黑色。`*-on-white.png` 是便于检查黑字的白底查看图，应用背景本身仍透明。

真实 Qwen3-ASR + HY-MT2 / Vulkan / HDMI 测试：35.42 秒会话、33.30 秒 PCM，4 条完整双语字幕均有有效绘制，0 错误/丢帧/过期/改写，退出无存活 worker。通过 `start.sh` 启动真实窗口，未用预览数据替代模型验收。完整证据在 `artifacts/ui-transparent/live-short/`，测试结果为 `artifacts/ui-transparent/pytest-final.xml`。本次只做短测，未重跑长期稳定性验收。

## 原 M2 范围

在 M1 commit `ea9dd8f1` 上增加连续系统播放音频、WebRTC VAD、常驻 Small.en Vulkan、异步 Qwen 翻译与原生 Qt 双语字幕。未增加麦克风、HIP 推理或其他模型。Windows 采集仍未实现，连续音频与字幕状态机保留平台接口。

## 已实现

- 20 ms 帧采集；有界音频、最终任务和临时任务队列；超量丢旧、任务过期与可见提示。
- 临时英文与局部一致性、句读/词数触发翻译；最终英文和中文分别冻结，拒绝迟到改写；已有临时译文与英文前缀一致时保留。
- Qt 自动换行、双语临时/最终卡片、有限历史、字号、主题、置顶提示。Wayland 无透明度能力时禁用控件。
- 拥有的 ASR/LLM 自动管理与重启；不接管外部用户服务；同源 monitor 重连；有界收尾和子进程释放。
- 仅真实可见 QLabel 绘制产生显示时延；截图/隐藏窗口和 headless 不填充显示样本。资源、事件、原生日志有界保存。

设计与容量见 [M2_ARCHITECTURE.md](M2_ARCHITECTURE.md)。最终性能见 [M2_BENCHMARKS.md](M2_BENCHMARKS.md)。

## 最终验收结果

| 验收项 | 结果 |
| --- | --- |
| 真实英语连续播放 → HDMI monitor → Small.en → Qwen → 字幕窗口 | PASS；全部本地，两个模型均为 RX 9070 XT / Vulkan0 |
| 连续运行至少 30 分钟 | PASS；会话 1840.36 秒，连续 PCM 1839.28 秒，素材播放自然结束 |
| 异步临时识别与翻译 | PASS；首条英文在段结束前出现，ASR/翻译各自处理，UI 主线程独立更新 |
| 双语字幕绘制与稳定性 | PASS；361 条最终英文、361 条最终双语，361/361 最终双语绘制；历史插入 361 次、保留 80 条；无最终英文改写 |
| 有界队列与退出 | PASS；音频/ASR/翻译/UI high water 为 1/100、1/4、1/4、2/256；零丢帧、丢任务、过期、推理错误；工作线程与拥有的模型子进程均释放 |
| 本次内存观察 | PASS；应用峰值 166.18 MiB，稳态窗口均值增加 4.66 MiB，后 20 分钟斜率 0.311 MiB/分钟；不证明无限时长无泄漏 |
| 真实字幕时延 | PASS；全部 n=361。英文首条 P50/P95 1106.43/1394.21 ms；中文 3349.65/4688.32 ms；语音结束 → 最终双语 906.07/1286.81 ms |
| 原有与新增自动化测试 | PASS；107 项，含 3 项真实模型，0 skipped；JUnit 保存于 artifacts/m2-all-tests.xml |
| M1 真实批次回归 | PASS；真实 HDMI 录音、真实 ASR/翻译，端口 8082 回归通过 |
| 原生 Wayland 全程可见绘制 | 未通过；早期长测仅 35.65% 覆盖，采用 xcb 路径完成本次验收 |

最终验收原始目录为 artifacts/m2-soak-05，acceptance.json 与 analysis.json 均为 true。资源/时延完整统计与测量边界见性能文档。一次约 1.09 秒最小化期间没有 paint 统计；全部最终双语 ID 均有有效绘制事件。长测开始后仅增加了最小化时显式排除统计的防护，未修改采集、VAD、ASR、翻译或队列逻辑；最终 GUI 另行真实短测并经过完整测试。

## 真实验收素材与复现

短测使用官方 whisper.cpp JFK 英文人声。长测使用 OpenSLR SLR12 LibriSpeech test-clean 的真实人声，CC BY 4.0，作者 Vassil Panayotov 等、朗读来自 LibriVox。来源：[OpenSLR](https://www.openslr.org/12)，公布 MD5 为 `32fa31d27d2e1cad72775fee3f4849a9`。下载后核对 MD5，并记录本机 SHA-256。

```bash
mkdir -p artifacts/m2-fixtures
curl -fL --retry 2 https://www.openslr.org/resources/12/test-clean.tar.gz \
  -o artifacts/m2-fixtures/test-clean.tar.gz
python scripts/prepare_stream_fixture.py
QT_QPA_PLATFORM=xcb python scripts/validate_stream.py --config config/local.toml --duration 1840 --require-soak \
  --speech artifacts/m2-fixtures/english-30min.wav --output-dir artifacts/my-soak
python scripts/analyze_stream.py artifacts/my-soak
```

固定随机种子 20261023，取 228 个片段、40 位说话人，总长 1827.67 秒。按片段调整线性音量至目标 -18 dBFS RMS 并限制峰值，片段间插入 800 ms 静音。没有 TTS，没有在模型外提供识别/翻译答案，参考文本不传给模型。播放通过 paplay 定向到已有 HDMI sink，再从其 monitor 连续采集；没有改变默认设备。

验收运行会打开真实桌面窗口；不要最小化或遮挡它，否则对应片段不具有可见绘制时间戳。桌面会话只获得临时 idle inhibitor，退出释放。完整真实数据、截图、日志、样本及本机配置保留在被 Git 忽略的 artifacts/ 和 config/local.toml。

## Ubuntu 显示平台与用户目录依赖

原生 Wayland 长测 `m2-soak-02` 完成 1839.3 秒 PCM、359 条真实最终双语字幕，但只有 128 条最终可见绘制（35.65%），额外的 98% 覆盖检查失败。该次性能数据保留在性能文档；不将后台生成时间补成显示时间。增加曝光诊断后，`m2-soak-03` 记录到窗口 visible=true、minimized=false、exposed=false，主动停止该次诊断，因此明确未达到 30 分钟。

在相同 GNOME Wayland 桌面选择 xcb / XWayland，xprop 实际确认 _NET_WM_STATE_ABOVE。缺少的 libxcb-cursor0 仅从 Ubuntu noble 软件包源下载并解包到项目缓存，没有使用 sudo apt install，没有替换任何已有系统库。版本 0.1.4-1build1 amd64，Ubuntu apt 索引与文件 SHA-256 都为 `137cf52479b5a9d8c5926d70d311af04be41941a32ee777340d704cc458c06d8`。缓存可复现为：

```bash
mkdir -p .cache/qt-xcb/packages
(cd .cache/qt-xcb/packages && apt download libxcb-cursor0=0.1.4-1build1)
sha256sum --check <<'EOF'
137cf52479b5a9d8c5926d70d311af04be41941a32ee777340d704cc458c06d8  .cache/qt-xcb/packages/libxcb-cursor0_0.1.4-1build1_amd64.deb
EOF
dpkg-deb -x .cache/qt-xcb/packages/libxcb-cursor0_0.1.4-1build1_amd64.deb .cache/qt-xcb/root
env -u PYTHONPATH QT_QPA_PLATFORM=xcb \
  LD_LIBRARY_PATH="$PWD/.cache/qt-xcb/root/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  python -m livetranslate live --config config/local.toml
```

有系统级 libxcb-cursor0 的全新 Ubuntu 安装只需设置 QT_QPA_PLATFORM=xcb；不需要上述解包与 LD_LIBRARY_PATH。本机长测使用两项环境变量，均只传给当前进程树。Vulkan 模型设备与 Qt 显示平台互相独立。

xcb 的 `m2-soak-04` 记录到约 26.1 分钟、312/312 最终双语绘制，但续查时所有测试进程已消失，且没有 summary、正常 stop 或 GUI close 事件。中断原因未确认，明确记录为未完成，不计入 30 分钟验收。保留 interruption-observation.json。后续 `m2-soak-05` 以独立进程会话启动有时限的验收器，并记录 supervisor PID、启动时钟和源文件摘要，持续检查日志新鲜度。

## 失败恢复验证

72 秒真实 HDMI 测试中，在第 8 秒终止拥有的 Whisper、第 22 秒终止拥有的 Qwen、第 38 秒终止本次 FFmpeg 回采子进程。两个推理服务各重启一次，音频重连一次、epoch 变化一次，后续继续生成最终双语字幕，退出无遗留工作线程。没有拔掉物理设备、停止系统 PipeWire 或改变默认 sink。原始证据：artifacts/m2-recovery-01/fault-actions.json、recovery-acceptance.json。

## 自动化与 M1 回归

完整测试运行 **107 passed，0 skipped**，其中 3 项为明确启用的真实模型测试。无模型单元/Mock 运行对应 104 项，3 项真实测试在未显式启用时跳过；不以这些 Fake 测试替代真实验收。

```bash
env -u PYTHONPATH LIVETRANSLATE_REAL=1 \
  LIVETRANSLATE_CONFIG=artifacts/m2-regression-final.local.toml \
  python -m pytest -q --junitxml=artifacts/m2-all-tests.xml
python scripts/validate_real.py --config artifacts/m2-regression-final.local.toml \
  --duration 15 --output artifacts/m2-m1-regression-final/system-jfk.json
```

该命令需先在专用输出配置下启动 M1 llama-server；独立 TOML 改变输出目录，并将 M1 服务与客户端 API 一并设为 8082，实际验证 M1 可以单独使用 M2 默认 ASR 端口；沿用相同模型与 HDMI 源。本机配置不提交。完整 pytest 和真实批次回归在长测之外运行，避免争用 GPU。M1 15 秒 HDMI 真实录音的英文包含完整 JFK 问句，中文为“因此，我的同胞们，不要问你的国家能为你做些什么。要问你自己能为国家做些什么。”捕获至完成总计 16177.97 ms，ASR 448.61 ms、翻译 496.53 ms；这是批次耗时，不是流式显示时延。专用服务在回归后停止。首次临时复用 8082 绑定失败，保留 llama-server.first-bind-failed.log；复查无持续监听者，稍后重试健康检查与所有真实测试通过，具体绑定失败原因未确认。

新增自动化覆盖配置、VAD 临时/停顿/强制边界/静音、队列溢出/合并/关闭、序号缺口、最终字幕不可改写、临时中文保留、有限历史、分离绘制时间戳、服务 HTTP 异常、外部服务不被终止、采集无源/EOF/错误输出/回收升级、缺少可选依赖，以及 GUI 换行、外观、截图排除和关闭对象树生命周期。M1 的设备解析、模型缺失、静音、推理不可用等原有测试全部保留。

## 退出错误与修复验证

补充短测 `m2-short-05` 在字幕生成后退出时报 Qt shared QObject / malloc 错误，因此该次明确失败。GDB 指向 Python 释放闭包时销毁 QWidget 子树，控件销毁被推迟到应用结束阶段；无 idle inhibitor 的最小探针同样复现，排除其为必要触发条件。采用 Qt WA_DeleteOnClose 让控件树在 Qt 事件循环中销毁。相同桌面探针修复前 12/30 次报错，修复后 30/30 次正常退出；这些探针使用 Fake 引擎，仅验证 Qt 生命周期，不作为音频或模型验收。

修复后真实 HDMI 短测 `m2-short-06` 通过：4/4 最终双语可见，英文首条 P50/P95 1087.83/1200.36 ms，中文 2731.22/3483.97 ms，语音结束到最终双语 759.57/778.89 ms，退出正常。原始错误、GDB 与探针保留在 artifacts/m2-short-05、m2-debug-exit-01、m2-exit-probes，没有覆盖失败记录。

最终代码进一步显式排除最小化窗口的绘制统计，并增加回归用例。`m2-short-final` 使用 xcb 与真实 HDMI/模型再次通过：4/4 最终双语绘制，英文首条 P50/P95 1138.18/1210.51 ms，中文 2893.65/3619.52 ms，语音结束到最终双语 895.09/914.56 ms；零推理错误、退出正常、无遗留工作线程。源文件摘要保留于该目录，确认这一短测使用最终界面实现。

## 版本与提交

- Python 3.12.15、pytest 9.1.1；PySide6-Essentials / shiboken6 6.12.0、webrtcvad-wheels 2.0.14.post1、psutil 7.2.2，见 config/live-requirements.lock。
- Whisper 官方提交 `200b1197907545a88c5a00fb15f52e2cf88af6f5`，llama.cpp `d88040c704c8855c497d46c20675a3d492f70f94`。沿用 M1 Small.en 与 Qwen Q4_K_M；权重 revision/hash 见 config/model-lock.json。
- 应用交付 commit 为最终 HEAD，可用 git log -1 --format=%H 核对；不内嵌自身哈希。权重、音频、日志、缓存、构建及本机配置不纳入 Git。

## 测试边界与限制

显示时延仅覆盖应用接收音频到可见 QLabel 绘制，不覆盖最初 Pulse/HDMI 缓冲和物理面板延迟。公开人声的识别、专有名词译法和强制分段边界仍受现有模型与启发式分段限制，未进行完整 WER 或翻译质量打分。恢复测试终止本程序的子进程，没有实际拔掉物理 HDMI；物理热拔插仍未验证。Windows、麦克风、HIP 与模型替换均在本轮范围外。

## 后续修复：跟随系统选中的输出

随后按用户选择适配系统默认输出，本地配置改为 source 为空。连续采集每 2 秒检查当前默认输出，发现变化则重新连接新 monitor，显式指定的源仍固定。没有设置系统默认设备或切换蓝牙 profile。

发现 PipeWire 的蓝牙元数据报告 disconnected，而系统 BlueZ Device1 Connected 实际为 true。先前仅依据 PipeWire 给出的“耳机已断开”判断不充分；历史报告中的断开字段应理解为当时的 PipeWire 报告，不能据此断言物理设备一定断开。修复以 BlueZ 的只读 Connected/Alias 查询更新蓝牙状态与名称；真正断开、源 UNAVAILABLE 和不可用端口仍会被拒绝。

追加验证：artifacts/output-follow-bluetooth-01，真实英语定向播放到当前默认蓝牙耳机，从其 monitor 捕获 34.26 秒 PCM，由 Small.en/Qwen Vulkan 产生 4 条最终双语字幕，真实 xcb 窗口绘制与退出均通过，零推理错误、零音频丢弃、无遗留线程。双模型由本次运行启动，全部本地推理。系统默认输出切换逻辑通过模拟设备列表的自动化用例验证；没有更改全局默认设备来做物理切换测试，也没有为此修复重做 30 分钟长测。

当前完整回归为 **120 passed，0 skipped**（含 3 项真实模型），JUnit 为 artifacts/output-follow-all.xml。新增 13 项覆盖蓝牙状态双向纠正、中文名称、无效/缺失 D-Bus 回退、查询路径、枚举整合、自动输出变化与显式固定源。初次在短测结束后运行真实测试时，受管 llama-server 已退出，2 项报告 connection refused；保留 output-follow-all.service-stopped.xml，使用专用服务重新运行后 120 项通过。本机配置和蓝牙设备标识只保留在被忽略的 config/local.toml、artifacts/ 中。

## 后续原生引擎升级

Whisper 升级到 v1.9.5 / d1be6fde11ac，llama.cpp 升级到 f5027fb21307，均在项目目录以 Vulkan 构建并接入默认配置。沿用 Small.en 与 Qwen 权重，137 项测试通过，包含 3 项真实模型；新增构建目录保护、版本一致性及活动蓝牙后备用例。

升级后真实 HDMI 的 M1 内容验收与 M2 35 秒字幕短测通过。135 秒运行产生 26 条最终双语，但窗口最小化后缺少绘制，只有 7/26 可见事件；默认蓝牙运行的 5/5 字幕实际绘制通过，额外 JFK 内容核对未通过，单独保留失败。本次未重跑新版的 30 分钟验收，本文前面的 30 分钟结果仍对应原提交。完整版本、性能、失败记录和回退方式见 [原生引擎升级报告](NATIVE_UPGRADE.md)。
