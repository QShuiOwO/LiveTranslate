import json
from pathlib import Path
import re
import tempfile
from time import perf_counter
from uuid import uuid4

from ..audio.wav import inspect_wav
from ..config import ASRConfig
from ..errors import LiveTranslateError
from ..process import run_command
from ..results import ASRResult
from ..telemetry import GPUSampler


def backend_evidence(log: str, requested: str) -> tuple[str, list[str]]:
    lines = log.splitlines()
    if requested == "cpu":
        if re.search(r"use gpu\s*=\s*0", log):
            return "cpu", [line for line in lines if "use gpu" in line or "CPU total size" in line]
        return "unverified", []
    names = r"Vulkan\d+" if requested == "vulkan" else r"(?:ROCm|HIP)\d+"
    selected = re.findall(r"whisper_backend_init_gpu: using (" + names + r") backend", log)
    if not selected:
        return "unverified", []
    device = selected[0]
    weight = [line for line in lines if re.search(re.escape(device) + r"\s+total size\s*=\s*[1-9]", line)]
    evidence = [line for line in lines if device in line or ("ggml_vulkan:" in line and " = " in line)]
    # Shader-capable CPU drivers do not count as GPU inference.
    if requested == "vulkan":
        index = re.search(r"\d+", device).group()
        chosen = [line for line in lines if re.search(r"ggml_vulkan:\s*" + index + r"\s*=", line)]
        if not chosen or any(re.search(r"llvmpipe|lavapipe|software", line, re.I) for line in chosen):
            return "unverified", evidence
    return (requested if weight else "unverified"), evidence


class WhisperBackend:
    def __init__(self, config: ASRConfig, output_dir: Path = Path("artifacts"), silence_dbfs: float = -60.0):
        self.config = config
        self.output_dir = Path(output_dir)
        self.silence_dbfs = silence_dbfs

    def transcribe(self, audio_file: Path) -> ASRResult:
        start = perf_counter()
        result = ASRResult(model_name=Path(self.config.model).name)
        sampler = GPUSampler()
        try:
            audio = inspect_wav(Path(audio_file), self.silence_dbfs)
            model = Path(self.config.model)
            if not model.is_file():
                raise LiveTranslateError("asr_model_missing", f"Whisper model not found: {model}")
            if model.stat().st_size < 1024 * 1024:
                raise LiveTranslateError("asr_model_invalid", "Whisper model is too small; test placeholder weights are not supported")
            self.output_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.output_dir / f"whisper-{uuid4().hex[:12]}.log"
            result.log_file = str(log_path)
            with tempfile.TemporaryDirectory(prefix="whisper-", dir=self.output_dir) as tmp:
                prefix = Path(tmp) / "transcript"
                args = [self.config.binary, "-m", str(model), "-f", str(Path(audio_file).resolve()),
                        "-l", "en", "-t", str(self.config.threads), "-oj", "-of", str(prefix),
                        "-tp", "0", "-tpi", "0", "-nt"]
                args += ["-ng"] if self.config.backend == "cpu" else ["-dev", str(self.config.device)]
                with sampler:
                    proc = run_command(args, timeout=self.config.timeout, check=False)
                log = proc.stderr + "\n" + proc.stdout
                log_path.write_text(log, encoding="utf-8")
                if proc.returncode:
                    raise LiveTranslateError("asr_process_failed", f"whisper-cli exited {proc.returncode}: {proc.stderr[-3000:]}")
                result.backend, result.gpu_evidence = backend_evidence(log, self.config.backend)
                if result.backend != self.config.backend:
                    raise LiveTranslateError("asr_backend_unverified", f"Requested {self.config.backend}, but logs do not prove model weights used that backend; see {log_path}")
                try:
                    data = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
                    if not isinstance(data, dict) or not isinstance(data.get("transcription"), list):
                        raise ValueError("transcription must be an array")
                    if any(not isinstance(s, dict) or not isinstance(s.get("text"), str) for s in data["transcription"]):
                        raise ValueError("each transcription segment must contain text")
                    result.transcript = " ".join(segment["text"].strip() for segment in data["transcription"]).strip()
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise LiveTranslateError("asr_output_invalid", f"Cannot parse whisper-cli JSON: {exc}") from exc
                if not result.transcript or not re.search(r"[a-zA-Z]", result.transcript) or result.transcript in {"[BLANK_AUDIO]", "[Silence]"}:
                    raise LiveTranslateError("asr_empty", "Whisper produced no English speech transcript")
                result.metrics["process_elapsed_ms"] = proc.elapsed_ms
                result.metrics["audio_duration_ms"] = audio.duration_ms
                result.success = True
        except LiveTranslateError as exc:
            result.error, result.error_code = str(exc), exc.code
            if result.log_file and not Path(result.log_file).exists():
                try:
                    Path(result.log_file).write_text(str(exc), encoding="utf-8")
                except OSError:
                    pass
        except OSError as exc:
            result.error, result.error_code = str(exc), "asr_io"
        finally:
            result.elapsed_ms = (perf_counter() - start) * 1000
            result.metrics["gpu"] = sampler.summary()
            if result.success:
                result.metrics["real_time_factor"] = result.elapsed_ms / audio.duration_ms
                result.metrics["audio_seconds_per_second"] = audio.duration_ms / result.elapsed_ms
        return result
