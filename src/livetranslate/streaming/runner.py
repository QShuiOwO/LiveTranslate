from dataclasses import replace
from pathlib import Path
import signal
import sys
from time import monotonic, sleep

from ..errors import LiveTranslateError
from .engine import LiveEngine


def run_live(config, *, source=None, duration=None, headless=False, output_dir=None, screenshot=None):
    if sys.platform != "linux":
        raise LiveTranslateError("platform_unsupported", "M2 system capture currently supports Linux; no microphone mode is provided")
    if config.asr.backend != "vulkan" or config.server.backend != "vulkan":
        raise LiveTranslateError("stream_backend", "M2 uses the existing Vulkan models; M1 CPU commands remain available")
    asr_port = config.asr.server_port if config.asr.engine == "qwen3" else config.stream.whisper_port
    if asr_port == config.server.port:
        raise LiveTranslateError("config", "ASR/LLM ports must differ for live mode")
    if duration is not None and not 1 <= duration <= 86400:
        raise LiveTranslateError("config", "Live duration must be between 1 and 86400 seconds")
    # Fail before opening session logs or starting threads when optional extras are absent.
    try:
        import psutil  # noqa: F401
        import webrtcvad  # noqa: F401
        if not headless:
            from .gui import run_gui
    except ImportError as exc:
        raise LiveTranslateError("live_dependency", "Install live extras: python -m pip install -e '.[live]'") from exc
    if source: config = replace(config, audio=replace(config.audio, source=source))
    from ..cli import new_name
    directory = output_dir or Path(config.output.directory) / new_name("live")
    engine = LiveEngine(config, directory)
    if not headless:
        return run_gui(engine, duration, screenshot)
    engine.presentation = "headless; not GUI latency validation"
    old_int = signal.signal(signal.SIGINT, lambda *args: engine.request_stop())
    old_term = signal.signal(signal.SIGTERM, lambda *args: engine.request_stop())
    try:
        engine.start(); start = monotonic()
        while not engine.finished.is_set():
            for event in engine.bus.drain():
                if event["kind"] == "caption" and event["zh_final"]:
                    print(event["text"] + "\n" + event["translated"], flush=True)
            if duration and monotonic()-start >= duration: engine.request_stop()
            sleep(0.03)
        report = engine.report()
        return 1 if engine.start_error or report["workers_alive"] else 0
    finally:
        engine.request_stop(); engine.finished.wait(timeout=45)
        engine.metrics.close()
        signal.signal(signal.SIGINT, old_int); signal.signal(signal.SIGTERM, old_term)
