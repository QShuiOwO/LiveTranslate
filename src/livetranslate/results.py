from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
import os
import tempfile


@dataclass
class AudioInfo:
    duration_ms: float
    frames: int
    sample_rate: int
    channels: int
    sample_width: int
    rms_dbfs: float
    peak: float


@dataclass
class CaptureResult:
    audio_file: str = ""
    source_device: str | None = None
    elapsed_ms: float = 0.0
    audio: AudioInfo | None = None
    success: bool = False
    error: str | None = None
    error_code: str | None = None


@dataclass
class ASRResult:
    transcript: str = ""
    elapsed_ms: float = 0.0
    model_name: str = ""
    backend: str = "unverified"
    success: bool = False
    error: str | None = None
    error_code: str | None = None
    source_language: str = "en"
    gpu_evidence: list[str] = field(default_factory=list)
    log_file: str | None = None
    metrics: dict = field(default_factory=dict)


@dataclass
class TranslationResult:
    translated_text: str = ""
    elapsed_ms: float = 0.0
    model_name: str = ""
    success: bool = False
    error: str | None = None
    error_code: str | None = None
    metrics: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    audio_file: str = ""
    audio_duration_ms: float = 0.0
    source_device: str | None = None
    source_language: str = "en"
    target_language: str = "zh-CN"
    original_text: str = ""
    translated_text: str = ""
    asr_model: str = ""
    translation_model: str = ""
    capture_elapsed_ms: float = 0.0
    asr_elapsed_ms: float = 0.0
    translation_elapsed_ms: float = 0.0
    total_elapsed_ms: float = 0.0
    backend: str = "unverified"
    success: bool = False
    error: str | None = None
    error_code: str | None = None
    metrics: dict = field(default_factory=dict)


def save_json(path: Path, data) -> None:
    """Atomically replace one result; no partial success JSON on interrupted writes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(data) if hasattr(data, "__dataclass_fields__") else data
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, suffix=".tmp", delete=False,
                                         encoding="utf-8") as f:
            name = f.name
            json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)
