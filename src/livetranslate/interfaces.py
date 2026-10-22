"""OS-independent contracts; a future WASAPI capture only implements AudioCapture."""

from pathlib import Path
from typing import Protocol

from .results import ASRResult, CaptureResult, TranslationResult


class AudioCapture(Protocol):
    def capture(self, duration: float, output: Path, source: str | None = None) -> CaptureResult: ...


class ASRBackend(Protocol):
    def transcribe(self, audio_file: Path) -> ASRResult: ...


class TranslationBackend(Protocol):
    def translate(self, text: str) -> TranslationResult: ...
