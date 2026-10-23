"""Select an explicitly configured recognizer; never silently switch models."""
from pathlib import Path

from ..errors import LiveTranslateError
from .whisper import WhisperBackend


def create_asr(config, output_dir=Path("artifacts"), silence_dbfs=-60.0):
    if config.engine == "whisper":
        return WhisperBackend(config, output_dir, silence_dbfs)
    if config.engine == "qwen3":
        from .qwen_asr import QwenASRBackend
        return QwenASRBackend(config, output_dir, silence_dbfs)
    raise LiveTranslateError("config", f"Unknown ASR engine: {config.engine}")
