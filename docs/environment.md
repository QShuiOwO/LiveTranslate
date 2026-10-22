# M0 环境审计

审计日期：2026-10-22T08:36:25.734551+00:00。所有信息来自本次主机命令；用户名、主机名和蓝牙地址未纳入本报告。

默认配置就绪检查为 **FAIL**，唯一原因是默认蓝牙输出已断开。通过项目内 `config/local.toml` 显式选择已连接 HDMI monitor 后，就绪检查 **PASS**。未修改系统默认输出或音频服务配置。

## 硬件、驱动与系统

| 项目 | 实测 |
| --- | --- |
| 系统 | Ubuntu 24.04.5 LTS (Noble Numbat) |
| 内核 | 7.0.0-38-generic，x86_64 |
| CPU | AMD Ryzen 7 9700X 8-Core Processor，8 核 / 16 线程 |
| 系统内存 | 30.46 GiB（MemTotal 31,941,032 KiB） |
| 独显 | AMD Radeon RX 9070 XT，PCI 1002:7550，gfx1201；amdgpu 驱动 |
| 独显 VRAM | 17,095,983,104 bytes，约 15.92 GiB |
| 其他设备 | Radeon 集显 gfx1036；Vulkan 另可枚举 llvmpipe CPU |
| Vulkan | Instance 1.3.275；独显 API 1.4.318；Mesa RADV 25.2.8-0ubuntu0.24.04.4 |
| ROCm / HIP | ROCm 7.2.4；HIP 编译器 7.2.53211-97f5574fe2；rocminfo 枚举 gfx1201 |
| HIP 运算 | hipcc 编译 gfx1201 小内核，设备加一运算 1024/1024 结果正确 |

Vulkan 保留原始警告：`radv is not a conformant Vulkan implementation, testing use only.` 本次随后完成了真实 ASR 和 LLM Vulkan 推理、GPU 权重分配日志及基准，未因警告修改驱动。HIP 小内核通过仅代表 HIP 基本计算可用，未编译或验收 Whisper/LLM HIP 后端。

## 音频会话

| 项目 | 实测 |
| --- | --- |
| PipeWire / pipewire-pulse / WirePlumber | 用户服务 active，PipeWire 1.0.5 |
| PulseAudio 兼容服务 | PulseAudio on PipeWire，协议 35；pactl 16.1 |
| 默认输出 | bluez_output.<device-id>.1，设备报告 disconnected |
| 默认输入 | bluez_input.<device-id>.0，麦克风，不用于系统采集 |
| 默认 monitor | bluez_output.<device-id>.1.monitor，选择时报 audio_device_disconnected |
| 可枚举 monitor | SPDIF、HDMI 3、蓝牙，共 3 个 |
| 真实验收源 | alsa_output.pci-0000_03_00.1.hdmi-stereo-extra2.monitor |
| FFmpeg | 6.1.1-3ubuntu5，devices 输出 DE pulse，确认支持 pulse 输入 |
| 真实回采 | 官方人声通过 paplay 定向播放至 HDMI，再由 FFmpeg 采集对应 monitor |

## 依赖版本

| 依赖 | 版本 |
| --- | --- |
| 系统 Python | 3.12.3 |
| 独立 Conda 环境 | 项目 .conda，Python 3.12.15；未改变系统 Python |
| Conda | 26.3.2 |
| pytest | 9.1.1 |
| pip / setuptools | 26.2.2 / 84.2.1 |
| CMake | cmake version 3.28.3 |
| C++ | c++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0 |
| Git | git version 2.43.0 |
| glslc | shaderc 2023.8-1build1 |
| libvulkan-dev | 1.3.275.0-1build1 |
| libshaderc-dev / glslc 包 | 2023.8-1build1 |

完整 Conda 公共包 URL 固定在 `config/conda-linux-64.lock`。项目运行时仅使用 Python 标准库；pytest 为开发依赖。

## 已有 Whisper 检查与复用

已有 `~/AI/whisper.cpp/` 的 origin 是官方 ggml-org 仓库。原工作树有两个已删除的跟踪文件，本次保留原样；没有 git reset、checkout 或覆盖原工作树。已有 build 的 GGML_VULKAN/HIP/CUDA 均为 OFF，只有 ggml-base.bin，缺少 Small.en，因此需要在项目内构建和部署要求的模型。

- Whisper 源码 commit：`200b1197907545a88c5a00fb15f52e2cf88af6f5`。
- llama.cpp 源码 commit：`d88040c704c8855c497d46c20675a3d492f70f94`。

先尝试外部源码配合项目构建目录时，CMake 要写外部 bindings/javascript/package.json.tmp，被只读边界拒绝。最小修复是从已有官方提交导出 Git archive 到项目 vendor/ 后构建，未改动原源码；未重复下载 Whisper 源码。两个项目的 Vulkan 构建均完成。

## 模型部署

| 模型 | 下载字节数 | 仓库 revision | 校验 |
| --- | ---: | --- | --- |
| ggml-small.en.bin | 487,614,201 | `5359861c739e955e79d9a303bcbc70fb988958b1` | SHA-256 通过 |
| Qwen3-4B-Instruct-2507-Q4_K_M.gguf | 2,497,281,120 | `a06e946bb6b655725eafa393f4a9745d460374c9` | SHA-256 通过 |

仓库、不可变下载 URL、完整 SHA-256 见 `config/model-lock.json`。匿名下载，无登录；模型只存 models/，不纳入 Git。

## 安全边界与复现

未卸载、替换或升级 ROCm、驱动、内核；未改变音频全局设置或默认输出。项目 Conda 环境、权重、构建、缓存及运行数据与原环境分离。

当前执行沙箱隔离了 /dev/dri、/dev/kfd 与桌面 Pulse socket；真实 GPU、音频、localhost 服务测试通过获准的主机执行完成。早先 bwrap 启动失败已解决；新的设备隔离问题不能归因为模型不支持 GPU。Git 元数据写入也通过获准的项目 Git 操作完成。

复现审计：`python scripts/doctor.py --hip-smoke --output artifacts/doctor.json`。使用已验证源：`python scripts/doctor.py --config config/local.toml`。默认源断开的 FAIL 保留，不掩盖为通过。原始检查 stdout/stderr、状态和耗时保存在 artifacts/doctor-final.json，显式源检查在 artifacts/doctor-selected-source.json；动态报告不提交 Git。

真实推理证据和测量边界见 [benchmarks.md](benchmarks.md)。
