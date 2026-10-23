from dataclasses import dataclass, field, fields, replace
from pathlib import Path
import math
import tomllib
from urllib.parse import urlsplit

from .errors import LiveTranslateError


@dataclass(frozen=True)
class AudioConfig:
    ffmpeg: str = "ffmpeg"
    pactl: str = "pactl"
    duration: float = 15.0
    source: str = ""
    silence_dbfs: float = -60.0


@dataclass(frozen=True)
class ASRConfig:
    engine: str = "whisper"
    binary: str = "build/whisper-vulkan-d1be6fde11ac/bin/whisper-cli"
    model: str = "models/ggml-small.en.bin"
    backend: str = "vulkan"
    device: int = 0
    threads: int = 8
    timeout: float = 180.0
    server_port: int = 8083


@dataclass(frozen=True)
class TranslationConfig:
    base_url: str = "http://127.0.0.1:8080"
    model: str = "Qwen3-4B-Instruct-2507-Q4_K_M"
    timeout: float = 120.0
    max_tokens: int = 256
    seed: int = 42
    prompt_style: str = "subtitle"


@dataclass(frozen=True)
class ServerConfig:
    binary: str = "build/llama-vulkan-f5027fb21307/bin/llama-server"
    model: str = "models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    port: int = 8080
    backend: str = "vulkan"
    device: str = "Vulkan0"
    context_size: int = 4096
    gpu_layers: int = 99
    threads: int = 8


@dataclass(frozen=True)
class OutputConfig:
    directory: str = "artifacts"


@dataclass(frozen=True)
class StreamConfig:
    whisper_server: str = "build/whisper-vulkan-d1be6fde11ac/bin/whisper-server"
    whisper_port: int = 8082
    vad_mode: int = 2
    vad_floor_dbfs: float = -52.0
    min_speech_ms: int = 240
    end_silence_ms: int = 560
    pre_roll_ms: int = 200
    partial_interval_ms: int = 1000
    min_partial_ms: int = 1200
    max_segment_ms: int = 10000
    frame_queue_size: int = 100
    final_queue_size: int = 3
    max_job_age_s: float = 6.0
    inference_timeout_s: float = 4.0
    history_limit: int = 80
    font_size: int = 16
    opacity: float = 0.4
    theme: str = "light"


@dataclass(frozen=True)
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    stream: StreamConfig = field(default_factory=StreamConfig)


def local_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise LiveTranslateError("config", f"Invalid local URL: {value}") from exc
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not port
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/", "/v1", "/v1/")):
        raise LiveTranslateError("config", "Translation URL must be http://127.0.0.1:PORT (optionally /v1); cloud endpoints are forbidden")
    return f"http://127.0.0.1:{port}"


def validate(config: Config) -> Config:
    a, w, t, s = config.audio, config.asr, config.translation, config.server
    for name, value, lower, upper in [
        ("audio.duration", a.duration, 10, 30), ("audio.silence_dbfs", a.silence_dbfs, -100, -10),
        ("asr.timeout", w.timeout, 1, 3600), ("translation.timeout", t.timeout, 1, 3600),
        ("asr.device", w.device, 0, 32), ("asr.threads", w.threads, 1, 256),
        ("asr.server_port", w.server_port, 1024, 65535),
        ("translation.max_tokens", t.max_tokens, 1, 1024), ("server.port", s.port, 1, 65535),
        ("server.context_size", s.context_size, 512, 32768), ("server.gpu_layers", s.gpu_layers, 0, 999),
        ("server.threads", s.threads, 1, 256),
    ]:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
            raise LiveTranslateError("config", f"{name} must be between {lower} and {upper}")
    if w.backend not in {"vulkan", "hip", "cpu"} or s.backend not in {"vulkan", "hip", "cpu"}:
        raise LiveTranslateError("config", "Supported backends: vulkan, hip, cpu")
    if w.engine not in {"whisper", "qwen3"}:
        raise LiveTranslateError("config", "asr.engine must be whisper or qwen3")
    if w.engine == "qwen3" and w.backend not in {"vulkan", "cpu"}:
        raise LiveTranslateError("config", "Qwen3-ASR currently supports Vulkan or CPU through speech.cpp")
    if t.prompt_style not in {"subtitle", "hy_mt"}:
        raise LiveTranslateError("config", "translation.prompt_style must be subtitle or hy_mt")
    for value in [w.device, w.threads, w.server_port, t.max_tokens, t.seed, s.port, s.context_size, s.gpu_layers, s.threads]:
        if isinstance(value, bool) or not isinstance(value, int):
            raise LiveTranslateError("config", "Token counts, device indices, seeds, ports and thread counts must be integers")
    if s.backend != "cpu" and s.gpu_layers == 0:
        raise LiveTranslateError("config", "GPU server requires gpu_layers > 0")
    if not 0 <= t.seed <= 4294967295:
        raise LiveTranslateError("config", "translation.seed must be between 0 and 4294967295")
    if ((s.backend == "vulkan" and not s.device.startswith("Vulkan"))
            or (s.backend == "hip" and not s.device.startswith(("ROCm", "HIP")))):
        raise LiveTranslateError("config", "Server device name must match the requested GPU backend")
    stream = config.stream
    limits = {"whisper_port": (1024, 65535), "vad_mode": (0, 3), "vad_floor_dbfs": (-90, -10),
              "min_speech_ms": (80, 1000), "end_silence_ms": (200, 2000), "pre_roll_ms": (0, 500),
              "partial_interval_ms": (400, 5000), "min_partial_ms": (500, 5000),
              "max_segment_ms": (3000, 20000), "frame_queue_size": (10, 250), "final_queue_size": (1, 8),
              "max_job_age_s": (1, 30), "inference_timeout_s": (1, 15), "history_limit": (5, 200),
              "font_size": (14, 36), "opacity": (0, 1)}
    for name, (low, high) in limits.items():
        value = getattr(stream, name)
        default = getattr(StreamConfig(), name)
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                or not low <= value <= high or (isinstance(default, int) and not isinstance(value, int))):
            raise LiveTranslateError("config", f"stream.{name} must be {'an integer ' if isinstance(default, int) else ''}between {low} and {high}")
    if stream.theme not in {"transparent", "dark", "light"}:
        raise LiveTranslateError("config", "Stream theme must be transparent/dark/light")
    if stream.min_partial_ms >= stream.max_segment_ms or stream.min_speech_ms >= stream.end_silence_ms:
        raise LiveTranslateError("config", "Stream speech/partial duration must precede segment boundaries")
    return replace(config, translation=replace(t, base_url=local_base_url(t.base_url)))


def load_config(path: Path | None = None, overrides: dict | None = None) -> Config:
    raw = {}
    base = Path.cwd()
    if path is not None:
        path = Path(path).expanduser().resolve()
        base = path.parent
        try:
            with path.open("rb") as f:
                raw = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise LiveTranslateError("config", f"Cannot load {path}: {exc}") from exc
    types = {"audio": AudioConfig, "asr": ASRConfig, "translation": TranslationConfig,
             "server": ServerConfig, "output": OutputConfig, "stream": StreamConfig}
    if set(raw) - set(types):
        raise LiveTranslateError("config", f"Unknown config sections: {sorted(set(raw) - set(types))}")
    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in raw.items()}
    for section, values in (overrides or {}).items():
        if section not in types:
            raise LiveTranslateError("config", f"Unknown config section: {section}")
        if not isinstance(merged.get(section, {}), dict):
            raise LiveTranslateError("config", f"Invalid table in {section}")
        merged.setdefault(section, {}).update({k: v for k, v in values.items() if v is not None})
    sections = {}
    for section, cls in types.items():
        values = merged.get(section, {})
        defaults = cls()
        if not isinstance(values, dict) or set(values) - {f.name for f in fields(cls)}:
            raise LiveTranslateError("config", f"Unknown keys or invalid table in {section}")
        for key, value in values.items():
            expected = getattr(defaults, key)
            if isinstance(expected, str) and not isinstance(value, str):
                raise LiveTranslateError("config", f"{section}.{key} must be a string")
        # Resolve model/build/output paths; plain executable names use PATH.
        values = {**{f.name: getattr(defaults, f.name) for f in fields(cls)}, **values}
        for key in ("binary", "model", "directory", "whisper_server"):
            if key in values and not (section == "translation" and key == "model"):
                value = values[key]
                if key != "binary" or "/" in value or "\\" in value or value.startswith("~"):
                    p = Path(value).expanduser()
                    values[key] = str((base / p).resolve() if not p.is_absolute() else p)
        sections[section] = cls(**values)
    if sections["asr"].engine == "whisper" and "whisper_server" not in merged.get("stream", {}):
        sections["stream"] = replace(sections["stream"], whisper_server=str(Path(sections["asr"].binary).with_name("whisper-server")))
    return validate(Config(**sections))
