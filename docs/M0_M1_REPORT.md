# LiveTranslate M0/M1 验收报告

日期：2026-10-22。版本：0.1.0。**M0 审计完成；M1 在显式选择已连接 HDMI monitor 的条件下通过。默认蓝牙输出已断开，默认音频路径未通过，已记录为实际环境限制。**

## 完成范围

- 创建 main 分支 Git 仓库、独立项目 Conda 环境、src 布局、TOML 配置与命令行入口。
- 完成 OS/内核/CPU/RAM/显卡/驱动、Vulkan、ROCm/HIP、PipeWire/pulse、monitor、FFmpeg、Python/CMake/C++/Git 审计；doctor 提供可读结果、JSON 和可选 Markdown。
- 检查已有官方 Whisper 仓库及构建后，复用指定提交源码，在项目内编译 Vulkan；保持原工作树及其既有删除不变。部署官方 Small.en 并做真实英文识别，验证 CPU 后备。
- 从官方 llama.cpp 构建 Vulkan 服务，匿名下载指定 Unsloth Q4_K_M GGUF 并核对 SHA-256；仅绑定 127.0.0.1，健康、模型别名、真实中文响应和 GPU offload 均验证。
- 实现 Linux monitor 采集、WAV 有效性/静音检查、结构化识别与翻译、文件和真实采集两种端到端模式；所有推理本地完成。
- 实现 AudioCapture / ASRBackend / TranslationBackend 协议和 TranslationPipeline，保留将来添加 WASAPI 的适配边界；当前未实现 Windows 采集。
- 完成最终 69 项 pytest（66 基础、3 真实模型）以及统一 CLI 的真实系统播放音频端到端；无跳过项。基准各 3 次，保留完整 JSON、日志、JUnit 和原始录音于被忽略的 artifacts/。

## 验收结论

| 验收项 | 结果与边界 |
| --- | --- |
| M0 环境审计 | 完成；默认输出断开可被正确识别；指定可用源后的 doctor environment_ready=true |
| Small.en 真实 ASR | PASS；JFK 人声产生合理英文；日志证明 Vulkan0 权重和推理后端 |
| CPU ASR 后备 | PASS；已有纯 CPU 构建和 -ng 模式均实际识别同一模型与样本 |
| CPU LLM 后备 | PASS；独立临时 127.0.0.1:8081 服务关闭 GPU offload，真实短句产生合理中文，验证后已关闭 |
| 本地 Qwen 翻译 | PASS；人工智能标准句产生“人工智能正在改变世界。” |
| llama-server | PASS；127.0.0.1:8080，health=ok；37/37 层 GPU offload |
| 真实系统音频 E2E | PASS；HDMI 定向播放 + monitor 实录 + 统一 demo CLI + 真实 ASR + 真实 Qwen |
| 默认输出自动路径 | FAIL；默认蓝牙设备报告 disconnected，不会回退到默认麦克风 |
| 设备中途消失 / 无 monitor / 超时 | 确定性单元测试通过；没有人为拔掉物理设备 |
| 实际失败路径 | 断开默认设备、无效源、静音、两类缺失权重、关闭的本地服务均正确返回失败 |
| ROCm/HIP | 枚举和 gfx1201 小内核运算 PASS；Whisper/LLM HIP 推理未验证 |
| Windows、GUI、持续字幕/M2 | 本轮范围外，未实施 |

## 实际模型与版本

- Whisper：`ggml-small.en.bin`，487,614,201 bytes；源码 `200b1197907545a88c5a00fb15f52e2cf88af6f5`；Vulkan0 / RX 9070 XT。
- LLM：`Qwen3-4B-Instruct-2507-Q4_K_M.gguf`，2,497,281,120 bytes；llama.cpp `d88040c704c8855c497d46c20675a3d492f70f94`；Vulkan0 / RX 9070 XT。
- 模型不可变 revision、公开来源和 SHA-256 在 [model-lock.json](../config/model-lock.json)；匿名下载校验通过。
- Python 3.12.15、pytest 9.1.1、FFmpeg 6.1.1、CMake 3.28.3、GCC 13.3.0、Git 2.43.0；Conda 26.3.2。依赖固定在 conda-linux-64.lock。
- 应用 Git commit 为交付仓库的最终 HEAD，哈希在交付消息中提供，可用 `git log -1 --format=%H` 核对；不在报告内嵌自身 commit 哈希。

## 实测性能

- 11 秒真实英语，Small.en Vulkan 三次中位 **269.28 ms**，RTF **0.0245**；CPU 中位 **1223.29 ms**。
- 标准短句 Qwen 翻译中位 **121.01 ms**，三次均为真实本地响应。
- Qwen CPU 后备单次真实翻译 **818.72 ms**；独立临时服务验证后已关闭。
- llama-bench pp128 **2523.79 tokens/s**；tg64 **164.47 tokens/s**，各 3 次。
- 实录 **15.011 秒**；捕获 **15216.61 ms**、识别 **293.37 ms**、翻译 **252.99 ms**、总计 **15772.38 ms**。
- 系统 E2E 的 ASR 阶段整卡显存采样峰值 **6.474 GiB**，包含常驻 LLM 与桌面，不是 ASR 独占显存。

完整测量口径、基线/峰值、日志证据与限制见 [benchmarks.md](benchmarks.md)。完整逐项测试见 [test-results.md](test-results.md)。

## 根因与最小修复

1. 原执行环境 bwrap 失败已由会话环境修复；主机 GPU、音频与 localhost 因隔离在普通沙箱不可见，真实测试使用获准主机执行。未修改设备权限或驱动。
2. CMake 会写已有 Whisper 源码目录；改为导出同一官方提交到项目内构建，保留外部工作树。
3. pytest 继承 ROS 的 PYTHONPATH 导致外部插件依赖缺失；仅对运行命令清除 PYTHONPATH，不改 ROS 或全局环境。
4. 默认蓝牙输出断开；保留该失败，向已连接 HDMI 定向播放并显式捕获其 monitor，不改系统默认设备。
5. llama-server 默认日志级别隐藏引擎 offload 信息；校验器拒绝首次启动。增加 verbosity=4 后重新获取证据，保持严格 GPU 校验。

## 本机运行

项目内已生成不纳入 Git 的 config/local.toml，显式选择本次已验证的 HDMI monitor。当前服务已启动；服务停止后先运行 serve。

```bash
conda activate "$PWD/.conda"
# 服务停止时，在单独终端启动：
env -u PYTHONPATH python -m livetranslate serve
# 系统有音频播放时：
python -m livetranslate demo --config config/local.toml --duration 15
# 使用已有录音：
python -m livetranslate demo --input artifacts/jfk.wav
# 可复现的真实播放回采（会播放官方英文人声）：
python scripts/validate_real.py --config config/local.toml
```

全新环境步骤与 CLI 参数见 [README](../README.md)。录音、权重、原生源码/编译产物、日志、缓存和本机配置均不提交 Git。M2 未开展。
