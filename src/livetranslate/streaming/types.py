from dataclasses import dataclass
from typing import Protocol, Iterator
import threading

RATE = 16000
FRAME_SAMPLES = 320
FRAME_BYTES = 640


@dataclass(frozen=True)
class AudioFrame:
    pcm: bytes
    sequence: int
    start_ns: int
    end_ns: int
    epoch: int = 0


@dataclass(frozen=True)
class SpeechJob:
    uid: int
    revision: int
    pcm: bytes
    onset_ns: int
    speech_end_ns: int
    audio_end_ns: int
    queued_ns: int
    final: bool
    reason: str = "partial"


@dataclass(frozen=True)
class TextJob:
    speech: SpeechJob
    text: str
    queued_ns: int

    @property
    def uid(self): return self.speech.uid

    @property
    def final(self): return self.speech.final


class ContinuousAudioSource(Protocol):
    def frames(self, stop: threading.Event) -> Iterator[AudioFrame]: ...
    def close(self) -> None: ...


class StreamingASR(Protocol):
    def recognize(self, pcm: bytes) -> str: ...
    def close(self) -> None: ...
