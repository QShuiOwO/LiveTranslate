from dataclasses import asdict
import json
from pathlib import Path

from livetranslate.pipeline import TranslationPipeline
from livetranslate.results import ASRResult, CaptureResult, TranslationResult, save_json


class FakeASR:
    def __init__(self, success=True):
        self.calls = []
        self.success = success

    def transcribe(self, path):
        self.calls.append(path)
        return ASRResult(transcript="Hello world.", elapsed_ms=12, model_name="fake-asr", backend="fake",
                         success=self.success, error=None if self.success else "model missing",
                         error_code=None if self.success else "asr_model_missing")


class FakeTranslator:
    def __init__(self, success=True):
        self.calls = []
        self.success = success

    def translate(self, text, *, source_language="en"):
        self.calls.append(text)
        return TranslationResult(translated_text="你好，世界。", elapsed_ms=20, model_name="fake-translator",
                                 success=self.success, error=None if self.success else "service unavailable",
                                 error_code=None if self.success else "translation_unavailable")


def test_file_pipeline_contract_and_atomic_json(audio, tmp_path):
    asr, translator = FakeASR(), FakeTranslator()
    result = TranslationPipeline(None, asr, translator).run(input_file=audio)
    assert result.success and translator.calls == ["Hello world."] and asr.calls == [audio]
    assert result.original_text == "Hello world." and result.translated_text == "你好，世界。"
    assert result.source_device is None and result.capture_elapsed_ms == 0
    assert result.asr_elapsed_ms == 12 and result.translation_elapsed_ms == 20
    p = tmp_path / "results" / "result.json"
    save_json(p, result)
    data = json.loads(p.read_text())
    required = {"audio_file", "audio_duration_ms", "source_device", "source_language", "target_language",
                "original_text", "translated_text", "asr_model", "translation_model", "capture_elapsed_ms",
                "asr_elapsed_ms", "translation_elapsed_ms", "total_elapsed_ms", "backend", "success", "error"}
    assert required <= data.keys()
    assert not list(p.parent.glob("*.tmp"))


def test_live_pipeline_preserves_capture_device(audio):
    class FakeCapture:
        def capture(self, duration, output, source=None):
            return CaptureResult(str(audio), "speaker.monitor", 10001, success=True)
    result = TranslationPipeline(FakeCapture(), FakeASR(), FakeTranslator()).run(duration=10)
    assert result.success and result.source_device == "speaker.monitor"
    assert result.capture_elapsed_ms == 10001


def test_asr_failure_short_circuits_translation(audio):
    translator = FakeTranslator()
    result = TranslationPipeline(None, FakeASR(False), translator).run(input_file=audio)
    assert not result.success and result.error_code == "asr_model_missing" and translator.calls == []


def test_translation_failure_preserves_original(audio):
    result = TranslationPipeline(None, FakeASR(), FakeTranslator(False)).run(input_file=audio)
    assert not result.success and result.original_text == "Hello world."
    assert result.error_code == "translation_unavailable"


def test_capture_failure_short_circuits_models():
    class FailedCapture:
        def capture(self, *args):
            return CaptureResult(elapsed_ms=8, error="No source", error_code="audio_no_monitor")
    asr = FakeASR()
    result = TranslationPipeline(FailedCapture(), asr, FakeTranslator()).run()
    assert not result.success and result.error_code == "audio_no_monitor" and not asr.calls


def test_missing_input_never_invokes_models(tmp_path):
    asr = FakeASR()
    result = TranslationPipeline(None, asr, FakeTranslator()).run(input_file=tmp_path / "missing.wav")
    assert result.error_code == "audio_invalid" and not asr.calls
