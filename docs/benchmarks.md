# M0/M1 性能测量

本文保留 M0/M1 的批次测量。M2 的真实 HDMI 连续采集、QLabel 绘制时延、30 分钟内存与队列结果见 [M2_BENCHMARKS.md](M2_BENCHMARKS.md)，两种时延口径不可互换。

测量日期：2026-10-22。Ryzen 7 9700X / RX 9070 XT (gfx1201) / Ubuntu 24.04。ASR Small.en，LLM Qwen3-4B-Instruct-2507 Q4_K_M；两个模型均真实本地推理，选择 Vulkan0 独显。

## 测量口径

- Python 阶段耗时使用 perf_counter；单次 ASR 包括 WAV 校验、子进程与模型初始化、推理和输出解析。没有清空系统文件/着色器缓存，不能称作严格冷启动。
- 翻译服务常驻，短句客户端耗时包含 health、模型别名检查、HTTP 和生成；temperature=0、top_k=1、seed=42、max_tokens=256、cache_prompt=false。
- 最终基准与其他测试、HIP 编译/探针分开顺序运行。桌面会话仍在运行；数字是单机小样本，不是长期实时字幕延迟保证。
- GPU 显存采样周期 100 ms，读 AMD sysfs；记录全设备占用，包含桌面、缓存和其他进程。短任务峰值可能漏采，利用率也可能滞后。不能把设备总显存或单个快照当作模型占用。

## 真实 ASR 和短句翻译：各 3 次

| 项目 | 中位耗时 ms | 最小–最大 ms | 吞吐 / RTF |
| --- | ---: | ---: | --- |
| Small.en Vulkan / 11 秒英文 | 269.28 | 268.32–282.72 | 40.85 音频秒/秒；RTF 0.0245 |
| 同一 Small.en，明确 -ng CPU / 11 秒英文 | 1223.29 | 1203.06–1237.30 | 8.99 音频秒/秒；RTF 0.1112 |
| Qwen 本地中文翻译 / 标准短句 | 121.01 | 120.64–121.11 | 120 prompt tokens，6 completion tokens |

本组样本 Vulkan ASR 耗时约为 CPU 的 1/4.54。CPU 比较使用同一 Vulkan 可执行文件加 -ng，日志确认 use_gpu=0 和 CPU 权重分配；另复用了原有纯 CPU 构建做独立验证。
首次记录的 Vulkan ASR 619.50 ms，原有 CPU 构建 1188.53 ms；这是早期单次样本，最终比较以三次测量为准。

输入：`Artificial intelligence is changing the world.`
输出：`人工智能正在改变世界。`。首次客户端调用 90.51 ms。最终三次短句请求的 cached_tokens/cache_n 均为 0，服务报告生成速率约 132–135 tokens/s；6 token 的短输出会受固定开销影响。

另用独立 127.0.0.1:8081 临时 CPU 服务验证同一 Qwen 模型，明确 GPU layers=0、device=none，得到相同中文，客户端单次耗时 818.72 ms，启动 1257.25 ms。该项是 CPU 后备可用性验证，不是三次中位基准；测试后已关闭临时服务。原始证据在 artifacts/translation-cpu.json 与 artifacts/cpu-server/。

## llama-bench 原生基准

GPU 权重、Vulkan0，threads=8，pp128 与 tg64 各 3 次，默认 warmup。工作负载是原生 token 基准，使用真实 Qwen 模型；它不能代替有语义的翻译验收。

| 工作负载 | 平均 tokens/s | 标准差 tokens/s |
| --- | ---: | ---: |
| pp128 | 2523.79 | 58.62 |
| tg64 | 164.47 | 12.92 |

复现命令：

```bash
build/llama-vulkan/bin/llama-bench -m models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  -p 128 -n 64 -r 3 -ngl 99 -dev Vulkan0 -sm none --offline -o json
python scripts/benchmark.py --iterations 3
```

## 实际系统音频端到端

统一 demo CLI 启动 15 秒 monitor 采集，约 1 秒后 paplay 向已连接 HDMI 输出播放官方 JFK 英文人声。最终 CLI exit code=0，录音经实际音频格式/响度检查。

| 项目 | 实测 |
| --- | ---: |
| 实际录音时长 | 15011.06 ms |
| 捕获阶段 | 15216.61 ms |
| Small.en Vulkan ASR | 293.37 ms |
| 本地 Qwen 翻译 | 252.99 ms |
| 总耗时 | 15772.38 ms |
| 识别+翻译 RTF | 0.0364 |
| 处理吞吐 | 27.47 录音秒/处理秒 |
| 已有 11 秒 WAV 的 CLI 总耗时 | 543.14 ms |

端到端原文：
> And so, my fellow Americans, ask not what your country can do for you. Ask what you can do for your country.

端到端中文：
> 因此，我的同胞们，不要问你的国家能为你做些什么。要问你自己能为国家做些什么。

总耗时包含等待录音；这仍是 15 秒批次原型，不能解释为 253 ms 流式字幕延迟。默认蓝牙输出断开导致默认路径失败，本次通过 --source 指定 HDMI；未修改系统默认输出。

## GPU 使用与显存证据

ASR 日志同时出现独显识别、`using Vulkan0 backend` 和 `Vulkan0 total size = 487.00 MB`。LLM 日志出现 RX 9070 XT、`offloaded 37/37 layers to GPU` 与 `Vulkan0 model buffer size = 2375.91 MiB`；KV buffer 为 576.00 MiB，启动 compute buffer 为 81.01 MiB。服务监听通过 ss 确认为 127.0.0.1:8080。

本地服务本次启动耗时 505.45 ms（已缓存文件与着色器），健康检查 status=ok。首次默认日志级别不足以证明 GPU，启动器主动拒绝；增加 verbosity=4 后取得完整 offload 证据，没有放宽校验。

| 阶段 | 设备基线 GiB | 采样峰值 GiB | 峰值增量 GiB | GPU busy 峰值 |
| --- | ---: | ---: | ---: | ---: |
| 首次 ASR（LLM 未运行） | 1.823 | 2.638 | 0.815 | 55% |
| llama-bench（LLM 服务未运行） | 1.760 | 4.212 | 2.452 | 99% |
| LLM 服务启动 | 1.754 | 4.729 | 2.974 | 35% |
| 最终 ASR 第一次（LLM 常驻） | 4.863 | 5.678 | 0.815 | 76% |
| 真实系统音频 ASR（LLM 常驻） | 5.668 | 6.474 | 0.807 | 83% |

Vulkan0 与 RX 9070 XT 的对应关系来自推理日志，集显未承担权重分配。CPU 运行日志明确禁用 GPU；其 sysfs busy 偶尔仍显示前一 GPU 任务的滞后利用率，因此不以 busy 数字独立判断后端。
整卡总量约 15.922 GiB。LLM 权重缓冲、KV/计算缓冲与全设备峰值是不同统计口径；不把桌面占用扣除后的近似增量称为精确进程显存。

原始数据保存在 artifacts/benchmark.json、llama-bench.json、llama-bench-telemetry.json、server-ready.json、real-system-e2e.json 及推理日志；这些运行数据不纳入 Git。
