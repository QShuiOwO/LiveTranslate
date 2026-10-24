from pathlib import Path
from time import perf_counter

from .audio.wav import inspect_wav
from .errors import LiveTranslateError
from .interfaces import ASRBackend, AudioCapture, TranslationBackend
from .results import PipelineResult


class TranslationPipeline:
    def __init__(self, capture: AudioCapture | None, asr: ASRBackend, translator: TranslationBackend,
                 silence_dbfs: float = -60.0, source_language: str = "en"):
        self.capture_backend = capture
        self.asr = asr
        self.translator = translator
        self.silence_dbfs = silence_dbfs
        self.source_language = source_language

    def run(self, *, input_file: Path | None = None, duration: float = 15,
            audio_output: Path = Path("artifacts/capture.wav"), source: str | None = None) -> PipelineResult:
        start = perf_counter()
        result = PipelineResult(audio_file=str(input_file or audio_output), source_language=self.source_language)
        try:
            if input_file is None:
                if self.capture_backend is None:
                    raise LiveTranslateError("capture_unavailable", "No AudioCapture implementation for this platform")
                capture = self.capture_backend.capture(duration, audio_output, source)
                result.capture_elapsed_ms = capture.elapsed_ms
                result.source_device = capture.source_device
                result.audio_file = capture.audio_file
                if not capture.success:
                    raise LiveTranslateError(capture.error_code or "capture_failed", capture.error or "Audio capture failed")
            audio = inspect_wav(Path(result.audio_file), self.silence_dbfs)
            result.audio_duration_ms = audio.duration_ms
            asr = self.asr.transcribe(Path(result.audio_file))
            result.asr_elapsed_ms, result.asr_model, result.backend = asr.elapsed_ms, asr.model_name, asr.backend
            result.metrics["asr"] = asr.metrics
            result.metrics["asr_gpu_evidence"] = asr.gpu_evidence
            result.metrics["asr_log"] = asr.log_file
            if not asr.success:
                raise LiveTranslateError(asr.error_code or "asr_failed", asr.error or "ASR failed")
            result.source_language = asr.source_language
            result.original_text = asr.transcript
            if not result.original_text.strip():
                raise LiveTranslateError("asr_empty", "ASR returned empty text")
            translation = self.translator.translate(asr.transcript, source_language=asr.source_language)
            result.translation_elapsed_ms = translation.elapsed_ms
            result.translation_model = translation.model_name
            result.metrics["translation"] = translation.metrics
            if not translation.success:
                raise LiveTranslateError(translation.error_code or "translation_failed", translation.error or "Translation failed")
            result.translated_text = translation.translated_text
            if not result.translated_text.strip():
                raise LiveTranslateError("translation_empty", "Translator returned empty text")
            result.success = True
        except LiveTranslateError as exc:
            result.error, result.error_code = str(exc), exc.code
        finally:
            result.total_elapsed_ms = (perf_counter() - start) * 1000
            if result.audio_duration_ms:
                processing_ms = result.asr_elapsed_ms + result.translation_elapsed_ms
                result.metrics["processing_real_time_factor"] = processing_ms / result.audio_duration_ms
                if processing_ms:
                    result.metrics["audio_seconds_per_processing_second"] = result.audio_duration_ms / processing_ms
            result.metrics["mode"] = "file" if input_file else "system_monitor"
        return result
