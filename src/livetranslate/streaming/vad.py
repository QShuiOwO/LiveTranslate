from array import array
from collections import deque
import math
import sys
from time import monotonic_ns

from ..config import StreamConfig
from .types import AudioFrame, SpeechJob, FRAME_BYTES


class Segmenter:
    """20 ms VAD, bounded pre-roll/utterances, periodic drafts and silence finals."""
    def __init__(self, config: StreamConfig, detector=None):
        self.config = config
        if detector is None:
            import webrtcvad
            detector = webrtcvad.Vad(config.vad_mode)
        self.detector = detector
        self.pre = deque(maxlen=config.pre_roll_ms // 20)
        self.frames: list[AudioFrame] = []
        self.uid = 0
        self.revision = 0
        self.voiced = 0
        self.silence = 0
        self.last_partial_count = 0
        self.onset_ns = 0
        self.speech_end_ns = 0
        self.previous = None
        self.gaps = 0
        self.continuity = 0

    def is_speech(self, pcm):
        if len(pcm) != FRAME_BYTES:
            raise ValueError("VAD requires 640-byte mono PCM16 frames")
        values = array("h", pcm)
        if sys.byteorder != "little": values.byteswap()
        energy = sum(v * v for v in values) / len(values)
        db = 10 * math.log10(max(energy / 32768**2, 1e-12))
        voice = self.detector.is_speech(pcm, 16000)
        return db >= self.config.vad_floor_dbfs and voice

    def _job(self, final, reason):
        if self.voiced * 20 < self.config.min_speech_ms:
            return None
        self.revision += 1
        # Keep 160 ms after the last voiced frame; omit long closing silence.
        end = min(len(self.frames), len(self.frames) - self.silence + 8)
        frames = self.frames[:end]
        return SpeechJob(self.uid, self.revision, b"".join(f.pcm for f in frames), self.onset_ns,
                         self.speech_end_ns, frames[-1].end_ns, monotonic_ns(), final, reason,
                         epoch=frames[0].epoch, continuity=self.continuity,
                         source_language=frames[0].source_language,
                         language_generation=frames[0].language_generation)

    def _reset(self):
        self.frames = []
        self.voiced = self.silence = self.revision = self.last_partial_count = 0
        self.pre.clear()

    def feed(self, frame):
        jobs = []
        if self.previous and (frame.language_generation != self.previous.language_generation
                              or frame.source_language != self.previous.source_language):
            # Discard the unfinished old-language segment and its pre-roll.
            self._reset()
            self.previous = None
            self.continuity += 1
        if self.previous and (frame.epoch != self.previous.epoch or frame.sequence != self.previous.sequence + 1):
            job = self._job(True, "capture_gap") if self.frames else None
            if job: jobs.append(job)
            self._reset()
            self.gaps += 1
            # Advance even during silence, when there is no old utterance to finalize.
            self.continuity += 1
        self.previous = frame
        speech = self.is_speech(frame.pcm)
        if not self.frames:
            if not speech:
                self.pre.append(frame)
                return jobs
            self.uid += 1
            self.onset_ns = frame.start_ns
            self.frames = list(self.pre)
            self.pre.clear()
        self.frames.append(frame)
        if speech:
            self.voiced += 1
            self.silence = 0
            self.speech_end_ns = frame.end_ns
        else:
            self.silence += 1
        elapsed = len(self.frames) * 20
        reason = None
        if self.silence * 20 >= self.config.end_silence_ms:
            reason = "pause"
        elif elapsed >= self.config.max_segment_ms:
            reason = "duration_limit"
        if reason:
            job = self._job(True, reason)
            if job: jobs.append(job)
            # No audio overlap: a sample belongs to exactly one committed segment.
            self._reset()
        elif (elapsed >= self.config.min_partial_ms and speech
              and (len(self.frames) - self.last_partial_count) * 20 >= self.config.partial_interval_ms):
            job = self._job(False, "periodic")
            if job:
                jobs.append(job)
                self.last_partial_count = len(self.frames)
        return jobs

    def finish(self):
        job = self._job(True, "stop") if self.frames else None
        self._reset()
        return job
