"""Explicit real model tests. Not used as a replacement for playback/monitor E2E."""
import os
from pathlib import Path
import pytest

from livetranslate.backends.llama import LlamaTranslator
from livetranslate.backends.factory import create_asr
from livetranslate.config import load_config
from livetranslate.pipeline import TranslationPipeline

pytestmark = [pytest.mark.real, pytest.mark.skipif(os.environ.get("LIVETRANSLATE_REAL") != "1", reason="real local model tests require LIVETRANSLATE_REAL=1")]


@pytest.fixture
def real_config():
    return load_config(Path(os.environ.get("LIVETRANSLATE_CONFIG", "config/default.toml")))


def test_real_asr_english(real_config):
    audio = Path(os.environ.get("LIVETRANSLATE_TEST_WAV", "artifacts/jfk.wav"))
    result = create_asr(real_config.asr, Path(real_config.output.directory)).transcribe(audio)
    assert result.success, result.error
    assert "country" in result.transcript.lower()
    assert result.backend == real_config.asr.backend


def test_real_translation_chinese(real_config):
    result = LlamaTranslator(real_config.translation).translate("Artificial intelligence is changing the world.")
    assert result.success, result.error
    assert "人工智能" in result.translated_text and "世界" in result.translated_text


def test_real_file_pipeline(real_config):
    pipeline = TranslationPipeline(None, create_asr(real_config.asr, Path(real_config.output.directory)),
                                   LlamaTranslator(real_config.translation))
    result = pipeline.run(input_file=Path(os.environ.get("LIVETRANSLATE_TEST_WAV", "artifacts/jfk.wav")))
    assert result.success, result.error
    assert "country" in result.original_text.lower() and "国家" in result.translated_text
