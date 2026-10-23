from collections import OrderedDict
from dataclasses import asdict
import hashlib
from pathlib import Path
import threading
from time import monotonic_ns

from ..errors import LiveTranslateError
from ..results import save_json
from .metrics import ResourceMonitor, SessionMetrics
from .queues import BoundedFrames, WorkQueue
from .state import CaptionBook, EventBus, complete_clause
from .types import TextJob
from .vad import Segmenter


class LiveEngine:
    def __init__(self, config, directory, source=None, asr=None, translator=None, detector=None):
        self.config = config
        self.directory = Path(directory)
        self.metrics = SessionMetrics(directory)
        self.bus = EventBus()
        self.book = CaptionBook(config.stream.history_limit)
        self.frames = BoundedFrames(config.stream.frame_queue_size)
        self.asr_queue = WorkQueue(config.stream.final_queue_size)
        self.translation_queue = WorkQueue(config.stream.final_queue_size)
        self.segmenter = Segmenter(config.stream, detector)
        if source is None:
            from ..audio.continuous import LinuxContinuousCapture
            source = LinuxContinuousCapture(config.audio, status=self.status)
        self.source = source
        if asr is None or translator is None:
            from .services import ManagedTranslator, persistent_asr
            asr = asr or persistent_asr(config, self.directory, self.status)
            translator = translator or ManagedTranslator(config, self.directory, self.status)
        self.asr, self.translator = asr, translator
        self.stop_event = threading.Event()
        self.finished = threading.Event()
        self.ready = threading.Event()
        self.threads = []
        self.shutdown_thread = None
        self.resource_monitor = None
        self.error_count = 0
        self.stale_jobs = 0
        self.first_frame_ns = None
        self.presentation = "none"
        self.last_partial_translation = {}
        self.start_error = None
        self.presentation_details = {}
        self.vad_finals = 0
        self.empty_asr_finals = 0

    def status(self, code, message):
        self.metrics.event("status", code=code, message=message)
        self.bus.put({"kind": "status", "code": code, "message": message})

    def queue_stats(self):
        return {"audio": self.frames.stats(), "asr": self.asr_queue.stats(), "translation": self.translation_queue.stats(),
                "ui": {"high_water": self.bus.high_water, "dropped": self.bus.dropped, "capacity": self.bus.capacity}}

    def start(self):
        starter = threading.Thread(target=self._start, name="live-start", daemon=True)
        self.threads.append(starter); starter.start()

    def _start(self):
        try:
            for adapter in (self.asr, self.translator):
                if hasattr(adapter, "service"): adapter.service.ensure()
            if self.stop_event.is_set(): return
            self.metrics.event("session_ready", config=asdict(self.config.stream), asr_model=Path(self.config.asr.model).name,
                               translation_model=self.config.translation.model)
            # Consumers start before the producer; native model startup is excluded from speech collection.
            for name, target in [("asr-worker", self._asr), ("translation-worker", self._translate),
                                 ("vad-worker", self._segment), ("capture-worker", self._capture)]:
                t = threading.Thread(target=target, name=name, daemon=True)
                self.threads.append(t); t.start()
            self.resource_monitor = ResourceMonitor(self); self.resource_monitor.start()
            self.ready.set()
            self.status("live_ready", "正在监听系统播放音频")
        except Exception as exc:
            self.start_error = str(exc)
            self.error_count += 1
            self.status("fatal", str(exc))
            self.request_stop()

    def _capture(self):
        try:
            for frame in self.source.frames(self.stop_event):
                if self.first_frame_ns is None:
                    self.first_frame_ns = frame.start_ns
                    self.metrics.event("first_pcm", frame_ns=frame.start_ns)
                    save_json(self.directory / "capture-ready.json", {"frame_ns": frame.start_ns,
                              "source": getattr(self.source, "source_name", None)})
                before = self.frames.dropped
                self.frames.put(frame)
                if self.frames.dropped != before:
                    self.status("audio_backlog", "音频处理积压，已丢弃旧帧；字幕可能有缺口")
        except Exception as exc:
            self.error_count += 1
            self.status("capture_failed", str(exc))
        finally:
            self.frames.close()

    def _offer(self, queue, item):
        for lost in queue.put(item):
            self.metrics.event("job_dropped", uid=lost.uid, final=lost.final)
            self.status("inference_backlog", "推理积压，已丢弃旧任务，保持当前音频进度")
            row = self.book.failure(lost.uid, "处理积压，此段已跳过")
            if row: self._publish(row)

    def _segment(self):
        try:
            while (frame := self.frames.get()) is not None:
                for job in self.segmenter.feed(frame):
                    if job.final:
                        self.vad_finals += 1
                        self.metrics.event("vad_final", uid=job.uid, onset_ns=job.onset_ns, speech_end_ns=job.speech_end_ns,
                                           audio_end_ns=job.audio_end_ns, reason=job.reason, pcm_bytes=len(job.pcm))
                    self._offer(self.asr_queue, job)
            job = self.segmenter.finish()
            if job: self._offer(self.asr_queue, job)
        except Exception as exc:
            self.error_count += 1; self.status("segmentation_failed", str(exc))
        finally:
            self.asr_queue.close()

    def _expired(self, job):
        if (monotonic_ns() - job.queued_ns) / 1e9 <= self.config.stream.max_job_age_s: return False
        self.stale_jobs += 1
        self.metrics.event("job_stale", uid=job.uid, final=job.final)
        self.status("stale_audio", "旧音频任务已过期，继续处理当前播放")
        row = self.book.failure(job.uid, "音频过期，此段已跳过")
        if row: self._publish(row)
        return True

    def _publish(self, row):
        event = {"kind": "caption", **row}
        self.metrics.event("caption", **row)
        self.bus.put(event)

    @staticmethod
    def _cache_put(cache, key, value, size):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > size: cache.popitem(last=False)

    def _request(self, function, job):
        for attempt in range(2 if job.final else 1):
            try:
                return function()
            except LiveTranslateError:
                if not job.final or attempt or (monotonic_ns()-job.queued_ns)/1e9 >= self.config.stream.max_job_age_s:
                    raise
                self.metrics.observe("final_request_retry", 1)
                self.status("service_retry", "本地推理暂不可用，正在重试当前语句")

    def _asr(self):
        cache = OrderedDict()
        try:
            while (job := self.asr_queue.get()) is not None:
                if self._expired(job): continue
                self.metrics.observe("asr_queue_wait_ms", (monotonic_ns() - job.queued_ns) / 1e6)
                key = hashlib.sha256(job.pcm).digest()
                text = cache.get(key)
                try:
                    if text is None:
                        start = monotonic_ns()
                        text = self._request(lambda: self.asr.recognize(job.pcm), job)
                        self.metrics.observe("asr_inference_ms", (monotonic_ns() - start) / 1e6)
                        if text: self._cache_put(cache, key, text, 32)
                    else:
                        self.metrics.observe("asr_cache_hit", 1)
                    if not text:
                        if job.final: self.empty_asr_finals += 1
                        continue
                    row = self.book.asr(job, text)
                    if row is None: continue
                    self._publish(row)
                    translate_text = text if job.final else complete_clause(row["stable"])
                    if translate_text:
                        previous = self.last_partial_translation.get(job.uid)
                        if job.final or (previous != translate_text and (previous is None or len(translate_text.split()) >= len(previous.split())+4)):
                            self._offer(self.translation_queue, TextJob(job, translate_text, monotonic_ns()))
                            self.last_partial_translation[job.uid] = translate_text
                    if job.final:
                        self.last_partial_translation.pop(job.uid, None)
                    while len(self.last_partial_translation) > 8:
                        self.last_partial_translation.pop(next(iter(self.last_partial_translation)))
                except Exception as exc:
                    self.error_count += 1
                    self.status("asr_recovering", str(exc))
                    row = self.book.failure(job.uid, "识别暂不可用，正在恢复")
                    if row: self._publish(row)
        finally:
            self.translation_queue.close()

    def _translate(self):
        cache = OrderedDict()
        while (job := self.translation_queue.get()) is not None:
            if self._expired(job): continue
            self.metrics.observe("translation_queue_wait_ms", (monotonic_ns() - job.queued_ns) / 1e6)
            translated = cache.get(job.text)
            try:
                if translated is None:
                    start = monotonic_ns()
                    translated = self._request(lambda: self.translator.translate(job.text), job)
                    self.metrics.observe("translation_inference_ms", (monotonic_ns() - start) / 1e6)
                    self._cache_put(cache, job.text, translated, 128)
                else:
                    self.metrics.observe("translation_cache_hit", 1)
                row = self.book.translation(job, translated)
                if row: self._publish(row)
            except Exception as exc:
                self.error_count += 1
                self.status("translation_recovering", str(exc))
                row = self.book.failure(job.uid, "翻译暂不可用，后续语句将自动重试")
                if row: self._publish(row)

    def presented(self, snapshot, field, time_ns):
        metrics = self.book.presented(snapshot, field, time_ns)
        for key, value in metrics.items(): self.metrics.observe(key, value)
        if metrics:
            self.metrics.ui_paints += 1
            self.presentation_details["exposed_paint_events"] = self.metrics.ui_paints
            self.metrics.event("paint", uid=snapshot["uid"], field=field, painted_ns=time_ns,
                               onset_ns=snapshot["onset_ns"], speech_end_ns=snapshot["speech_end_ns"], metrics=metrics)

    def request_stop(self):
        if self.shutdown_thread is not None: return
        self.stop_event.set()
        self.shutdown_thread = threading.Thread(target=self._shutdown, name="live-shutdown", daemon=True)
        self.shutdown_thread.start()

    def _shutdown(self):
        try:
            self.source.close()
            # Wait for startup before determining which workers exist.
            for thread in self.threads[:1]:
                if thread is not threading.current_thread(): thread.join(timeout=35)
            self.frames.close()
            for name in ["capture-worker", "vad-worker", "asr-worker", "translation-worker"]:
                for thread in self.threads:
                    if thread.name == name: thread.join(timeout=12)
            self.asr.close(); self.translator.close()
            if self.resource_monitor: self.resource_monitor.close()
        finally:
            self.finished.set()

    def report(self):
        resources = list(self.metrics.resources)
        report = {"session_elapsed_s": (monotonic_ns() - self.metrics.start_ns) / 1e9,
                  "capture_elapsed_s": (monotonic_ns() - self.first_frame_ns) / 1e9 if self.first_frame_ns else 0,
                  "presentation": self.presentation, "start_error": self.start_error,
                  "gui": self.presentation_details,
                  "pcm_duration_s": getattr(self.source, "frames_read", 0) * 0.02,
                  "speech_to_display": self.metrics.snapshot(), "resource_samples": resources,
                  "queues": self.queue_stats(), "final_english": self.book.final_count,
                  "final_bilingual": self.book.bilingual_count, "error_count": self.error_count,
                  "vad_finals": self.vad_finals, "empty_asr_finals": self.empty_asr_finals,
                  "stale_jobs": self.stale_jobs, "capture_gaps": self.segmenter.gaps,
                  "capture_reconnects": getattr(self.source, "reconnects", None),
                  "final_mutation_attempts": self.book.final_mutation_attempts,
                  "workers_alive": [t.name for t in self.threads if t.is_alive()],
                  "timestamp_scope": "monotonic_ns: VAD first/last voiced PCM frame ingress -> QLabel paintEvent completion; excludes unmeasured initial Pulse/HDMI buffering and physical compositor/panel latency"}
        report["services"] = {name: {"starts": adapter.service.starts, "restarts": adapter.service.restarts,
                                      "external": adapter.service.external, "evidence": adapter.service.evidence}
                              for name, adapter in [("asr", self.asr), ("translation", self.translator)] if hasattr(adapter, "service")}
        save_json(self.directory / "summary.json", report)
        return report
