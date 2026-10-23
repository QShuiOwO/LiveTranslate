"""Local Qwen3-ASR through the pinned speech.cpp Vulkan/CPU runtime."""
import json
import os
from pathlib import Path
import re
from time import perf_counter
from uuid import uuid4

from ..audio.wav import inspect_wav
from ..errors import LiveTranslateError
from ..process import run_command
from ..results import ASRResult
from ..telemetry import GPUSampler

MODEL_ID = "Qwen3-ASR-1.7B"


def local_environment():
    return {**os.environ, "SPEECH_NO_UPDATE_CHECK": "1", "HF_HUB_OFFLINE": "1", "NO_COLOR": "1"}


def check_model(config):
    path = Path(config.model)
    if not path.is_file():
        raise LiveTranslateError("asr_model_missing", f"Qwen3-ASR GGUF not found: {path}")
    with path.open("rb") as f:
        if f.read(4) != b"GGUF" or path.stat().st_size < 1024 * 1024:
            raise LiveTranslateError("asr_model_invalid", "Qwen3-ASR requires a speech.cpp GGUF, not a Whisper model or safetensors directory")
    return path.resolve()


def device_name(config):
    if config.backend == "cpu":
        return "CPU"
    if config.backend != "vulkan":
        raise LiveTranslateError("asr_backend", "Qwen3-ASR supports Vulkan or CPU")
    return f"Vulkan{config.device}"


def verify_device(config):
    proc = run_command([config.binary, "devices", "--json"], timeout=15, env=local_environment())
    try:
        devices = json.loads(proc.stdout)["devices"]
        selected = next(d for d in devices if d["name"] == device_name(config))
        if config.backend == "vulkan" and (selected["kind"] not in {"gpu", "igpu"}
                or re.search(r"llvmpipe|lavapipe|software", selected["description"], re.I)):
            raise ValueError("selected device is not a hardware GPU")
        if config.backend == "cpu" and selected["kind"] != "cpu":
            raise ValueError("selected device is not CPU")
        return selected
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        raise LiveTranslateError("asr_device_unverified", f"Cannot verify speech.cpp device {device_name(config)}: {exc}") from exc


def backend_evidence(log, config, device):
    name = re.escape(device_name(config))
    # Both lines are emitted from the loaded model's device information, not from device enumeration.
    pattern = rf"(?:Loaded {MODEL_ID} on {name}\b|{MODEL_ID}\s+recognition\s+{name}\b)"
    lines = [line for line in log.splitlines() if re.search(pattern, line)]
    if not lines:
        raise LiveTranslateError("asr_backend_unverified", f"No loaded {MODEL_ID} on {device_name(config)} in speech.cpp log")
    return ["speech.cpp device: " + json.dumps(device, ensure_ascii=False), *lines]


def transcript_text(data, stop):
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise LiveTranslateError("asr_output_invalid", "Qwen3-ASR response has no transcript string")
    if stop != "complete":
        raise LiveTranslateError("asr_incomplete", f"Qwen3-ASR did not complete: {stop}")
    return re.sub(r"\s+", " ", data["text"]).strip()


class QwenASRBackend:
    def __init__(self, config, output_dir=Path("artifacts"), silence_dbfs=-60.0):
        self.config, self.output_dir, self.silence_dbfs = config, Path(output_dir), silence_dbfs

    def transcribe(self, audio_file):
        start = perf_counter()
        result = ASRResult(model_name=Path(self.config.model).name)
        sampler = GPUSampler()
        try:
            audio = inspect_wav(Path(audio_file), self.silence_dbfs)
            model = check_model(self.config)
            selected = verify_device(self.config)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            log = self.output_dir / f"qwen-asr-{uuid4().hex[:12]}.log"
            result.log_file = str(log)
            args = [self.config.binary, "asr", str(model), "--device", device_name(self.config),
                    "--threads", str(self.config.threads), "--language", "en", "--format", "json",
                    str(Path(audio_file).resolve())]
            with sampler:
                try:
                    proc = run_command(args, timeout=self.config.timeout, check=False, env=local_environment())
                except LiveTranslateError as exc:
                    log.write_text(str(exc), encoding="utf-8")
                    raise
            log.write_text(proc.stderr + "\n" + proc.stdout, encoding="utf-8")
            if proc.returncode:
                raise LiveTranslateError("asr_process_failed", f"speech.cpp exited {proc.returncode}: {proc.stderr[-3000:]}")
            try:
                data = json.loads(proc.stdout)
            except ValueError as exc:
                raise LiveTranslateError("asr_output_invalid", f"Invalid speech.cpp JSON: {exc}") from exc
            result.transcript = transcript_text(data, data.get("stop") if isinstance(data, dict) else None)
            if not result.transcript or not re.search(r"[A-Za-z]", result.transcript):
                raise LiveTranslateError("asr_empty", "Qwen3-ASR returned no English speech")
            result.gpu_evidence = backend_evidence(proc.stderr, self.config, selected)
            result.backend = self.config.backend
            result.metrics.update(audio_duration_ms=audio.duration_ms, languages=data.get("languages", []), engine="speech.cpp")
            result.success = True
        except LiveTranslateError as exc:
            result.error, result.error_code = str(exc), exc.code
        except OSError as exc:
            result.error, result.error_code = str(exc), "asr_io"
        finally:
            result.elapsed_ms = (perf_counter() - start) * 1000
            result.metrics["gpu"] = sampler.summary()
        return result
