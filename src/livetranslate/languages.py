"""Source-language rules shared by adapters, configuration and presentation."""
from pathlib import Path
import re

from .errors import LiveTranslateError

LANGUAGES = {"en": "英语", "ja": "日语"}


def validate_language(language):
    if not isinstance(language, str) or language not in LANGUAGES:
        raise LiveTranslateError("language", "Source language must be en or ja")
    return language


def validate_asr_language(config, language):
    validate_language(language)
    if (language != "en" and config.engine == "whisper"
            and re.search(r"\.en(?:[.-]|$)", Path(config.model).name)):
        raise LiveTranslateError("language_model", "当前 Whisper .en 模型仅支持英语；日语请选择 Qwen3-ASR 或多语言 Whisper 模型")
    return language


def speech_text(text):
    return bool(text and any(char.isalpha() for char in text)
                and text.upper() not in {"[BLANK_AUDIO]", "[SILENCE]"})


def text_units(text, language):
    return sum(not char.isspace() for char in text) if language == "ja" else len(text.split())
