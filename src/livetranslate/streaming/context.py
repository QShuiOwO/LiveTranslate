"""Bounded source-language history, owned exclusively by the ASR worker."""
from collections import deque
from dataclasses import dataclass

from ..config import TranslationConfig
from .types import SpeechJob


@dataclass(frozen=True)
class ContextEntry:
    uid: int
    text: str
    end_ns: int


class TranslationContext:
    def __init__(self, config: TranslationConfig):
        self.config = config
        self.entries: deque[ContextEntry] = deque(maxlen=config.context_segments)
        self.boundary = None
        self.highest_uid = -1

    def _advance(self, job: SpeechJob) -> bool:
        if job.uid < self.highest_uid:
            return False
        self.highest_uid = job.uid
        boundary = (job.epoch, job.continuity)
        if boundary != self.boundary:
            self.entries.clear()
            self.boundary = boundary
        max_age_ns = self.config.context_max_age_s * 1e9
        # Capture ingress timestamps can slightly overlap at adjacent read boundaries.
        self.entries = deque((entry for entry in self.entries
                              if max(0, job.onset_ns - entry.end_ns) <= max_age_ns),
                             maxlen=self.config.context_segments)
        return True

    def before(self, job: SpeechJob) -> tuple[str, ...]:
        if not self._advance(job):
            return ()
        return tuple(entry.text for entry in self.entries if entry.uid < job.uid)

    def remember(self, job: SpeechJob, text: str) -> None:
        if not job.final or not self._advance(job):
            return
        text = text.strip()
        if (not text or not self.config.context_segments or len(text) > self.config.context_max_chars
                or any(entry.uid == job.uid for entry in self.entries)):
            return
        self.entries.append(ContextEntry(job.uid, text, job.speech_end_ns))
        while len("\n".join(entry.text for entry in self.entries)) > self.config.context_max_chars:
            self.entries.popleft()
