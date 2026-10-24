from dataclasses import replace
from pathlib import Path
import threading
from time import monotonic, monotonic_ns, sleep

import pytest

from livetranslate.config import Config, StreamConfig, load_config
from livetranslate.errors import LiveTranslateError
from livetranslate.streaming.engine import LiveEngine
from livetranslate.streaming.metrics import percentile
from livetranslate.streaming.queues import BoundedFrames, WorkQueue
from livetranslate.streaming.state import CaptionBook, complete_clause, stable_prefix
from livetranslate.streaming.types import AudioFrame, SpeechJob, TextJob
from livetranslate.streaming.vad import Segmenter


SPEECH = (1200).to_bytes(2, "little", signed=True) * 320
SILENCE = b"\0" * 640


class Detector:
    def is_speech(self, pcm, rate): return pcm != SILENCE


def frame(i, speech=True, epoch=1):
    return AudioFrame(SPEECH if speech else SILENCE, i, (i-1)*20_000_000, i*20_000_000, epoch)


def job(uid=1, revision=1, final=False, pcm=SPEECH):
    return SpeechJob(uid, revision, pcm, 1_000_000_000, 2_000_000_000, 2_200_000_000, monotonic_ns(), final)


def test_frame_overflow_drops_old_audio_and_exposes_gap():
    q = BoundedFrames(2)
    for i in range(4): q.put(frame(i))
    assert q.stats() == {"capacity": 2, "depth": 2, "dropped": 2, "high_water": 2}
    q.close()
    assert [q.get().sequence, q.get().sequence] == [2, 3]
    assert q.get() is None


def test_work_queue_prioritizes_final_and_coalesces_drafts():
    q = WorkQueue(2)
    q.put(job(revision=1)); q.put(job(revision=2))
    q.put(job(uid=2, final=True)); q.put(job(final=True, revision=3))
    assert q.get().uid == 2
    assert q.get().final
    q.close(); assert q.get() is None
    assert q.coalesced == 1


def test_final_backlog_is_finite_and_returns_lost_ids():
    q = WorkQueue(2)
    lost = []
    for uid in range(20): lost += q.put(job(uid=uid, final=True))
    assert [j.uid for j in lost] == list(range(18))
    assert q.stats()["high_water"] == 2
    q.close(); assert q.get().uid == 18 and q.get().uid == 19 and q.get() is None


def test_queue_close_unblocks_waiting_worker():
    q = WorkQueue(2)
    results = []
    t = threading.Thread(target=lambda: results.append(q.get())); t.start()
    q.close(); t.join(timeout=1)
    assert not t.is_alive() and results == [None]


def test_vad_has_partial_before_end_and_silence_final():
    config = replace(StreamConfig(), end_silence_ms=400)
    vad = Segmenter(config, Detector())
    jobs = []
    for i in range(1, 151): jobs += vad.feed(frame(i))
    assert any(not j.final for j in jobs) and not any(j.final for j in jobs)
    for i in range(151, 171): jobs += vad.feed(frame(i, False))
    final = [j for j in jobs if j.final]
    assert len(final) == 1 and final[0].reason == "pause"
    assert final[0].speech_end_ns == 3_000_000_000
    assert len(final[0].pcm) <= (150 + 8) * 640
    assert vad.finish() is None


def test_vad_short_noise_and_silence_do_not_reach_models():
    vad = Segmenter(StreamConfig(), Detector())
    jobs = []
    for i in range(1, 80): jobs += vad.feed(frame(i, i <= 3))
    assert jobs == []


def test_vad_caps_continuous_speech_without_overlapping_audio():
    vad = Segmenter(replace(StreamConfig(), max_segment_ms=3000), Detector())
    finals = []
    for i in range(1, 311):
        finals += [j for j in vad.feed(frame(i)) if j.final]
    assert len(finals) == 2
    assert finals[0].audio_end_ns <= finals[1].onset_ns
    assert all(len(j.pcm) <= 150 * 640 for j in finals)


def test_capture_discontinuity_never_joins_audio_across_gap():
    vad = Segmenter(StreamConfig(), Detector())
    for i in range(1, 81): vad.feed(frame(i))
    final = [j for j in vad.feed(frame(100)) if j.final]
    assert final[0].reason == "capture_gap" and vad.gaps == 1
    assert vad.uid == 2


def test_draft_agreement_and_meaningful_translation_trigger():
    assert stable_prefix("The world is changing", "The world is changing rapidly.") == "The world is changing"
    assert complete_clause("Hello") == ""
    assert complete_clause("My fellow citizens, we are here today.") == "My fellow citizens, we are here today."
    assert not complete_clause("This is the")
    assert complete_clause("The future of artificial intelligence affects everyone in the").endswith("everyone")


def test_confirmed_english_and_chinese_cannot_be_rewritten():
    b = CaptionBook(5)
    final = job(final=True, revision=2)
    b.asr(job(), "Hello")
    b.asr(final, "Hello world.")
    assert b.asr(job(revision=3), "Changed draft") is None
    assert b.asr(replace(final, revision=4), "Changed final") is None
    translated = TextJob(final, "Hello world.", monotonic_ns())
    assert b.translation(translated, "你好，世界。")["zh_final"]
    assert b.translation(translated, "不能改写") is None
    assert b.rows[1]["text"] == "Hello world." and b.rows[1]["translated"] == "你好，世界。"


def test_late_draft_translation_cannot_replace_final():
    b = CaptionBook()
    partial = job(revision=1)
    b.asr(partial, "Hello world.")
    b.asr(job(final=True, revision=2), "Hello world again.")
    assert b.translation(TextJob(partial, "Hello world.", monotonic_ns()), "旧译文") is None


def test_chinese_draft_remains_visible_when_english_grows():
    b = CaptionBook()
    first = job(revision=1)
    b.asr(first, "The world is changing very quickly.")
    translated = b.translation(TextJob(first, "The world is changing very quickly.", monotonic_ns()), "世界正在迅速变化。")
    assert translated["translated"]
    longer = b.asr(job(revision=2), "The world is changing very quickly in many ways.")
    assert longer["translated"] == "世界正在迅速变化。" and not longer["zh_final"]
    changed = b.asr(job(revision=3), "A completely different observation.")
    assert not changed["translated"]


def test_history_and_dedup_state_are_bounded():
    b = CaptionBook(5)
    for uid in range(100): b.asr(job(uid=uid, final=True), f"Line {uid}")
    assert len(b.rows) == 9
    assert b.asr(job(uid=1, final=True), "Old event") is None
    assert len(b.rows) == 9


def test_latency_uses_speech_clock_and_separate_paint_times():
    b = CaptionBook()
    partial = b.asr(job(), "Hello")
    assert b.presented(partial, "en", 2_100_000_000) == {"first_en_ms": 1100.0}
    final_job = job(final=True, revision=2)
    final = b.asr(final_job, "Hello world.")
    assert b.presented(final, "en", 2_700_000_000) == {}
    final = b.translation(TextJob(final_job, "Hello world.", monotonic_ns()), "你好，世界。")
    observed = b.presented(final, "zh", 2_900_000_000)
    assert observed["first_zh_ms"] == 1900
    assert observed["final_from_speech_end_ms"] == 900
    assert b.presented(final, "zh", 3_000_000_000) == {}
    assert percentile([100, 200, 300], 50) == 200
    assert percentile([100, 200, 300], 95) == 290


@pytest.mark.parametrize("value", ["[stream]\nframe_queue_size=0", "[stream]\nend_silence_ms=30",
                                     "[stream]\nopacity=nan", "[stream]\nfont_size=true",
                                     "[stream]\ntheme='unknown'"])
def test_stream_config_rejects_invalid_values(tmp_path, value):
    p = tmp_path / "settings.toml"; p.write_text(value)
    with pytest.raises(LiveTranslateError): load_config(p)


def test_m1_config_without_stream_uses_sibling_whisper_server(tmp_path):
    p = tmp_path / "old.toml"
    p.write_text('[asr]\nbinary="../build/bin/whisper-cli"\n')
    cfg = load_config(p)
    assert Path(cfg.stream.whisper_server).parent == Path(cfg.asr.binary).parent


def test_m1_custom_server_port_is_not_reserved_by_unused_stream_service(tmp_path):
    p = tmp_path / "m1.toml"
    p.write_text('[server]\nport=8082\n')
    assert load_config(p).server.port == 8082


def test_live_rejects_colliding_ports_before_creating_session(tmp_path):
    from livetranslate.streaming.runner import run_live
    cfg = Config(); cfg = replace(cfg, server=replace(cfg.server, port=cfg.stream.whisper_port))
    with pytest.raises(LiveTranslateError, match="ports must differ"):
        run_live(cfg, headless=True, output_dir=tmp_path / "unused")
    assert not (tmp_path / "unused").exists()


def test_engine_fake_data_flow_failure_recovery_and_shutdown(tmp_path):
    class Source:
        frames_read = 0
        reconnects = 0
        source_name = "fake-output.monitor"
        def frames(self, stop):
            for i in range(1, 201):
                if stop.is_set(): break
                self.frames_read += 1
                yield frame(i, i <= 150)
            stop.wait(0.1)
        def close(self): pass
    class ASR:
        closed = False
        def recognize(self, pcm): return "The world is changing rapidly."
        def close(self): self.closed = True
    class Translator:
        closed = False
        def translate(self, text, *, context=()): return "世界正在迅速变化。"
        def close(self): self.closed = True
    a, t = ASR(), Translator()
    engine = LiveEngine(Config(), tmp_path / "session", Source(), a, t, Detector())
    engine.start()
    deadline = monotonic() + 3
    while engine.book.bilingual_count == 0 and monotonic() < deadline: sleep(0.01)
    engine.request_stop(); assert engine.finished.wait(4)
    assert engine.book.bilingual_count == 1
    report = engine.report(); engine.metrics.close()
    assert not report["workers_alive"] and a.closed and t.closed
    assert report["queues"]["audio"]["high_water"] <= 100
    assert report["presentation"] == "none" and "first_en_ms" not in report["speech_to_display"]
