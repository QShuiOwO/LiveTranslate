# M0/M1 完整测试结果

当前 UI 更新：167 tests，0 failures，0 errors，0 skipped（含 3 项真实模型测试、12 项 GUI 测试）。新增透明 alpha/黑字、双区域缩放、框内滚动、历史跟随及临时/完成分区验证；另通过 35 秒真实 HDMI 可见字幕测试，4/4 完成双语绘制。证据目录：`artifacts/ui-transparent/`。

本页保留 M1 的 69 项验收快照。原始 M2 验收为 107 项，后续系统输出跟随与蓝牙检测修复后为 120 项完整通过；真实连续字幕和异常恢复结果见 [M2_REPORT.md](M2_REPORT.md)。

当前 Qwen3-ASR + HY-MT2 更新：160 tests，0 failures，0 errors，0 skipped（含 3 项真实模型测试）；另有 HDMI 回采和一分钟可见字幕验收。证据：`artifacts/qwen-hy-upgrade/pytest-final.xml`，详见 [模型切换报告](QWEN_HY_UPGRADE.md)。

原 M1 最终运行：69 tests，0 failures，0 errors，0 skipped。

命令：`LIVETRANSLATE_REAL=1 env -u PYTHONPATH python -m pytest -q --junitxml=artifacts/pytest-all.xml`。最终运行 69 项全部通过，其中 66 项单元/Fake 集成测试、3 项真实本地模型测试。未启用真实测试的早期运行明确跳过 real 用例，不作为最终验收。

| 模块 | 通过数 | 验证内容 |
| --- | ---: | --- |
| tests.test_audio | 16 | monitor 解析、麦克风排除、断开/消失、静音/空/截断、FFmpeg 失败与不覆盖录音 |
| tests.test_backends | 14 | 缺失模型、无 GPU 证据、翻译请求/异常、无效 JSON、重定向、子进程退出码/超时/stderr |
| tests.test_config | 25 | TOML、路径、CLI 覆盖、未知字段、类型/范围、禁止云端 URL |
| tests.test_pipeline | 6 | Fake 数据流、输出字段、原文保留、失败短路、原子 JSON |
| tests.test_real | 3 | 真实 Small.en 英文识别、Qwen 中文翻译、已有 WAV 全管线 |
| tests.test_server | 5 | 缺失 GGUF、loopback/offline/CPU 参数、拒绝未证明的 GPU |

## pytest 逐项结果

- PASS `tests.test_audio.test_pipewire_monitor_never_selects_microphone`
- PASS `tests.test_audio.test_pulseaudio_numeric_monitor_of_sink`
- PASS `tests.test_audio.test_invalid_monitor_reference_and_unplugged_port`
- PASS `tests.test_audio.test_device_failure_reasons[devices0-None-audio_no_monitor]`
- PASS `tests.test_audio.test_device_failure_reasons[devices1-None-audio_device_disconnected]`
- PASS `tests.test_audio.test_device_failure_reasons[devices2-gone.monitor-audio_source_missing]`
- PASS `tests.test_audio.test_device_failure_reasons[devices3-None-audio_source_ambiguous]`
- PASS `tests.test_audio.test_audio_metadata`
- PASS `tests.test_audio.test_bad_audio_rejected[silent-audio_silent]`
- PASS `tests.test_audio.test_bad_audio_rejected[empty-audio_empty]`
- PASS `tests.test_audio.test_bad_audio_rejected[stereo-audio_format]`
- PASS `tests.test_audio.test_bad_audio_rejected[bad-audio_invalid]`
- PASS `tests.test_audio.test_bad_audio_rejected[truncated-audio_truncated]`
- PASS `tests.test_audio.test_capture_no_overwrite_and_failed_process`
- PASS `tests.test_audio.test_device_removed_during_capture`
- PASS `tests.test_audio.test_pactl_json_failure`
- PASS `tests.test_backends.test_missing_real_model_is_structured`
- PASS `tests.test_backends.test_gpu_flag_or_enumeration_alone_is_not_inference`
- PASS `tests.test_backends.test_translation_request_is_local_bounded_and_deterministic`
- PASS `tests.test_backends.test_bad_translation_is_not_success[-stop-translation_empty]`
- PASS `tests.test_backends.test_bad_translation_is_not_success[\u8bd1\u6587\uff1a\u4f60\u597d-stop-translation_format]`
- PASS `tests.test_backends.test_bad_translation_is_not_success[<think>analysis</think>\u4f60\u597d-stop-translation_format]`
- PASS `tests.test_backends.test_bad_translation_is_not_success[\u672a\u5b8c\u6210-length-translation_incomplete]`
- PASS `tests.test_backends.test_bad_translation_is_not_success[None-stop-translation_response_invalid]`
- PASS `tests.test_backends.test_unavailable_service`
- PASS `tests.test_backends.test_invalid_server_json`
- PASS `tests.test_backends.test_malformed_model_list_is_structured`
- PASS `tests.test_backends.test_malformed_whisper_json_is_structured`
- PASS `tests.test_backends.test_redirect_cannot_send_text_elsewhere`
- PASS `tests.test_backends.test_subprocess_status_stderr_and_timeout`
- PASS `tests.test_config.test_toml_relative_paths_and_overrides`
- PASS `tests.test_config.test_invalid_table_is_not_hidden_by_cli_override`
- PASS `tests.test_config.test_invalid_configuration_is_explained[not TOML = ]]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[unknown]\nx=1]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[asr]\ntimeout=-1]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[asr]\nthreads=1.2]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[asr]\nmodel=3]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[audio]\nduration=5]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[audio]\nduration=nan]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[translation]\nmax_tokens=100000]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[server]\nport=70000]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[server]\ngpu_layers=0]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[asr]\nbackend='cuda']`
- PASS `tests.test_config.test_invalid_configuration_is_explained[[audio]\nmicrophone=true]`
- PASS `tests.test_config.test_invalid_configuration_is_explained[audio=3]`
- PASS `tests.test_config.test_missing_config`
- PASS `tests.test_config.test_cloud_dns_and_unsafe_urls_rejected[https://api.openai.com/v1]`
- PASS `tests.test_config.test_cloud_dns_and_unsafe_urls_rejected[http://localhost:8080]`
- PASS `tests.test_config.test_cloud_dns_and_unsafe_urls_rejected[http://127.0.0.1.evil:8080]`
- PASS `tests.test_config.test_cloud_dns_and_unsafe_urls_rejected[http://127.0.0.1:8080@evil.com]`
- PASS `tests.test_config.test_cloud_dns_and_unsafe_urls_rejected[http://127.0.0.1:8080/?x=1]`
- PASS `tests.test_config.test_cloud_dns_and_unsafe_urls_rejected[http://127.0.0.1:8080/other]`
- PASS `tests.test_config.test_cloud_dns_and_unsafe_urls_rejected[http://127.0.0.1:invalid]`
- PASS `tests.test_config.test_cloud_dns_and_unsafe_urls_rejected[http://0.0.0.0:8080]`
- PASS `tests.test_config.test_local_v1_url_normalizes`
- PASS `tests.test_pipeline.test_file_pipeline_contract_and_atomic_json`
- PASS `tests.test_pipeline.test_live_pipeline_preserves_capture_device`
- PASS `tests.test_pipeline.test_asr_failure_short_circuits_translation`
- PASS `tests.test_pipeline.test_translation_failure_preserves_original`
- PASS `tests.test_pipeline.test_capture_failure_short_circuits_models`
- PASS `tests.test_pipeline.test_missing_input_never_invokes_models`
- PASS `tests.test_real.test_real_whisper_english`
- PASS `tests.test_real.test_real_qwen_chinese`
- PASS `tests.test_real.test_real_file_pipeline`
- PASS `tests.test_server.test_missing_gguf_does_not_start_service`
- PASS `tests.test_server.test_server_binds_loopback_and_disables_cloud_downloads`
- PASS `tests.test_server.test_server_startup_does_not_claim_unproven_gpu[Found Vulkan GPU]`
- PASS `tests.test_server.test_server_startup_does_not_claim_unproven_gpu[offloaded 0/37 layers to GPU\nVulkan0 model buffer size = 0 MiB]`
- PASS `tests.test_server.test_server_startup_does_not_claim_unproven_gpu[using device Vulkan0 (llvmpipe)\noffloaded 37/37 layers to GPU\nVulkan0 model buffer size = 2000 MiB]`

## pytest 之外的真实验收

- PASS 官方英语人声经 HDMI 实际播放、monitor 回采，再通过 `python -m livetranslate demo` 完成真实识别与翻译；cli_exit_code=0，英文与中文内容校验通过。
- PASS `python -m livetranslate demo --input artifacts/jfk.wav` 独立文件路径。
- PASS Vulkan/CPU ASR、真实短句翻译各 3 次性能测量；llama-bench pp128、tg64 各 3 次。
- PASS HIP gfx1201 运算探针；这不等于 Whisper/LLM HIP 后端通过。
- PASS Qwen CPU 后备：独立 127.0.0.1:8081 服务使用同一 GGUF、GPU layers=0、device=none，真实标准句得到合理中文；验证后已关闭临时服务。
- PASS 实际失败路径返回预期失败：断开默认设备、无效 monitor、静音输入、缺失 ASR 权重、缺失 LLM 权重、关闭的本地服务。
- 无 monitor 枚举、设备在录制中消失、进程超时等通过确定性模拟测试；未人为拔掉用户的物理设备。
- 默认输出的普通 demo 路径因蓝牙已断开而失败；真实成功验收使用显式 HDMI monitor，不改变系统默认设备。

原始结果：artifacts/pytest-all.xml、pytest-all.txt、real-system-e2e.acceptance.json、demo-file.json、failure-smoke.json。日志、JSON 与录音均被 Git 忽略，本文是可提交的脱敏摘要。

首次 pytest 被宿主 ROS PYTHONPATH 注入的 launch_testing 插件阻断，原因是外部插件依赖 yaml 缺失。清除该命令的 PYTHONPATH 后通过，没有修改 ROS 或通过安装全局依赖掩盖问题。
