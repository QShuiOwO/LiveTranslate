from dataclasses import replace
from time import monotonic_ns

import pytest

from livetranslate.config import Config, StreamConfig, TranslationConfig, load_config
from livetranslate.errors import LiveTranslateError
from livetranslate.streaming.context import TranslationContext
from livetranslate.streaming.engine import LiveEngine
from livetranslate.streaming.types import AudioFrame, SpeechJob, TextJob
from livetranslate.streaming.vad import Segmenter


SPEECH = (1200).to_bytes(2, "little", signed=True) * 320
SILENCE = b"\0" * 640


def speech(uid, *, final=True, revision=1, onset=None, end=None, epoch=1, continuity=0, text_key=None):
    start = uid * 2 if onset is None else onset
    finish = start + 1 if end is None else end
    pcm = (text_key or f"{uid}:{revision}").encode()
    return SpeechJob(uid, revision, pcm, int(start * 1e9), int(finish * 1e9),
                     int((finish + .16) * 1e9), monotonic_ns(), final,
                     epoch=epoch, continuity=continuity)


def test_context_uses_only_previous_confirmed_english():
    history = TranslationContext(TranslationConfig())
    first = speech(1)
    assert history.before(first) == ()
    history.remember(replace(first, final=False), "An unfinished draft.")
    assert history.before(first) == ()
    history.remember(first, "The previous English sentence.")
    assert history.before(first) == ()
    assert history.before(speech(2)) == ("The previous English sentence.",)
    history.remember(speech(3), "A future final must not leak backward.")
    assert "A future final must not leak backward." not in history.before(speech(2))


def test_context_retains_only_two_recent_segments_and_freezes_each_job_snapshot():
    history = TranslationContext(TranslationConfig())
    history.remember(speech(1), "First final.")
    history.remember(speech(2), "Second final.")
    third = speech(3)
    snapshot = history.before(third)
    pending = TextJob(third, "Current sentence.", monotonic_ns(), context=snapshot)
    history.remember(third, pending.text)
    assert pending.context == ("First final.", "Second final.")
    assert history.before(speech(4)) == ("Second final.", "Current sentence.")
    for uid in range(4, 100):
        history.remember(speech(uid), f"Final {uid}.")
    assert history.before(speech(100)) == ("Final 98.", "Final 99.")


def test_character_budget_drops_oldest_whole_segment_without_truncating():
    history = TranslationContext(TranslationConfig(context_max_chars=128))
    older, newer = "Older sentence " + "a" * 56, "Newer sentence " + "b" * 56
    history.remember(speech(1), older)
    history.remember(speech(2), newer)
    context = history.before(speech(3))
    assert context == (newer,)
    assert len("\n".join(context)) <= 128


def test_single_oversized_segment_is_skipped_and_not_rewritten_as_a_fragment():
    history = TranslationContext(TranslationConfig(context_max_chars=128))
    history.remember(speech(1), "x" * 129)
    assert history.before(speech(2)) == ()


def test_context_expires_by_audio_time_not_queue_or_inference_time():
    history = TranslationContext(TranslationConfig(context_max_age_s=30))
    history.remember(speech(1, onset=1, end=2), "Recent spoken context.")
    assert history.before(speech(2, onset=31, end=32)) == ("Recent spoken context.",)
    assert history.before(speech(3, onset=33, end=34)) == ()


@pytest.mark.parametrize("changed", [{"epoch": 2}, {"continuity": 1}])
def test_source_or_sequence_boundary_clears_context(changed):
    history = TranslationContext(TranslationConfig())
    history.remember(speech(1), "Old source context.")
    next_job = speech(2, **changed)
    assert history.before(next_job) == ()
    history.remember(next_job, "New source context.")
    assert history.before(speech(3, **changed)) == ("New source context.",)


def test_context_can_be_disabled():
    history = TranslationContext(TranslationConfig(context_segments=0))
    history.remember(speech(1), "This must not be passed when context is disabled.")
    assert history.before(speech(2)) == ()


class Detector:
    def is_speech(self, pcm, rate): return pcm != SILENCE


def frame(sequence, *, voiced=False, epoch=1):
    return AudioFrame(SPEECH if voiced else SILENCE, sequence,
                      (sequence - 1) * 20_000_000, sequence * 20_000_000, epoch)


@pytest.mark.parametrize("changed_epoch,skip", [(2, 0), (1, 5)])
def test_silence_between_utterances_still_propagates_capture_boundary(changed_epoch, skip):
    vad = Segmenter(StreamConfig(), Detector())
    first = []
    for sequence in range(1, 45):
        first += vad.feed(frame(sequence, voiced=sequence <= 16))
    previous = next(j for j in first if j.final)
    start = 45 + skip
    # The boundary occurs in silence, so there is no capture_gap speech job to signal it.
    assert vad.feed(frame(start, epoch=changed_epoch)) == []
    following = []
    for sequence in range(start + 1, start + 45):
        following += vad.feed(frame(sequence, voiced=sequence <= start + 16, epoch=changed_epoch))
    current = next(j for j in following if j.final)
    assert previous.epoch == 1 and current.epoch == changed_epoch
    assert current.continuity > previous.continuity
    history = TranslationContext(TranslationConfig())
    history.remember(previous, "Do not bring this into another capture interval.")
    assert history.before(current) == ()


def test_active_gap_final_keeps_old_source_identity():
    vad = Segmenter(StreamConfig(), Detector())
    for sequence in range(1, 17): vad.feed(frame(sequence, voiced=True))
    gap = vad.feed(frame(30, voiced=True, epoch=2))
    old = next(j for j in gap if j.final)
    assert old.reason == "capture_gap" and old.epoch == 1
    following = []
    for sequence in range(31, 74):
        following += vad.feed(frame(sequence, voiced=sequence <= 45, epoch=2))
    new = next(j for j in following if j.final)
    assert new.epoch == 2 and new.continuity > old.continuity


class Source:
    def close(self): pass


class ASR:
    def __init__(self, texts): self.texts = texts
    def recognize(self, pcm, *, language="en"): return self.texts[pcm]
    def close(self): pass


class Translator:
    def __init__(self, fail_text=None):
        self.calls = []
        self.fail_text = fail_text

    def translate(self, text, *, context=(), source_language="en"):
        self.calls.append((text, context))
        if text == self.fail_text:
            raise LiveTranslateError("translation_unavailable", "Simulated local service outage")
        return f"已翻译第 {len(self.calls)} 次"

    def close(self): pass


class Arrivals:
    """Deliver controlled job arrivals without coalescing all drafts before the consumer runs."""
    def __init__(self, jobs): self.jobs = iter(jobs)
    def get(self): return next(self.jobs, None)


def engine(tmp_path, texts=None, translator=None):
    config = Config()
    config = replace(config, stream=replace(config.stream, final_queue_size=8))
    return LiveEngine(config, tmp_path / "session", Source(), ASR(texts or {}),
                      translator or Translator(), Detector())


def test_asr_attaches_prior_finals_before_remembering_current_and_freezes_snapshots(tmp_path):
    jobs = [speech(1), speech(2, final=False, revision=1),
            speech(2, final=False, revision=2), speech(2, revision=3), speech(3)]
    texts = ["The first segment is already confirmed.",
             "A provisional second sentence has six clear words.",
             "A provisional second sentence has six clear words.",
             "The second segment is now confirmed.", "Third sentence."]
    e = engine(tmp_path, dict(zip((j.pcm for j in jobs), texts)))
    e.asr_queue = Arrivals(jobs)
    offered = []
    original = e._offer
    def offer(queue, item):
        if queue is e.translation_queue: offered.append(item)
        original(queue, item)
    e._offer = offer
    try:
        e._asr()
        second = [j for j in offered if j.uid == 2]
        assert any(not j.final for j in second) and any(j.final for j in second)
        assert all(j.context == (texts[0],) for j in second)
        assert next(j for j in offered if j.uid == 3).context == (texts[0], texts[3])
        assert all("已翻译" not in part for j in offered for part in j.context)
    finally:
        e.metrics.close()


def test_asr_context_resets_even_when_pcm_cache_hits_across_sources(tmp_path):
    jobs = [speech(1, text_key="same"), speech(2, text_key="same"),
            speech(3, epoch=2, text_key="same"), speech(4, epoch=2, continuity=1, text_key="same")]
    e = engine(tmp_path, {b"same": "Identical recognized audio."})
    e.asr_queue = Arrivals(jobs)
    try:
        e._asr()
        queued = []
        while (item := e.translation_queue.get()) is not None: queued.append(item)
        assert [j.context for j in queued] == [(), ("Identical recognized audio.",), (), ()]
    finally:
        e.metrics.close()


def test_translation_cache_is_bound_to_context_and_capture_continuity(tmp_path):
    translator = Translator()
    e = engine(tmp_path, translator=translator)
    text = "What if I do like this?"
    contexts = [(), ("We are demonstrating an action.",), ("We are demonstrating an action.",), (), ()]
    jobs = [speech(1), speech(2), speech(3), speech(4, epoch=2), speech(5, epoch=2, continuity=1)]
    try:
        for job, context in zip(jobs, contexts):
            e.book.asr(job, text)
            e.translation_queue.put(TextJob(job, text, monotonic_ns(), context=context))
        e.translation_queue.close()
        e._translate()
        assert translator.calls == [(text, contexts[i]) for i in (0, 1, 3, 4)]
        assert e.book.bilingual_count == 5
        assert e.book.rows[2]["translated"] == e.book.rows[3]["translated"]
        assert e.book.rows[1]["translated"] != e.book.rows[4]["translated"]
    finally:
        e.metrics.close()


def test_confirmed_english_remains_context_when_its_translation_fails(tmp_path):
    first, second = speech(1), speech(2)
    translator = Translator(fail_text="The first English sentence.")
    e = engine(tmp_path, {first.pcm: translator.fail_text, second.pcm: "The following sentence."}, translator)
    e.asr_queue = Arrivals([first, second])
    try:
        e._asr(); e._translate()
        assert e.error_count == 1
        assert translator.calls[-1] == ("The following sentence.", (translator.fail_text,))
        assert e.book.rows[2]["zh_final"]
    finally:
        e.metrics.close()


@pytest.mark.parametrize("setting", [
    "context_segments=-1", "context_segments=5", "context_segments=1.5", "context_segments=true",
    "context_max_chars=0", "context_max_chars=127", "context_max_chars=2001", "context_max_chars=800.5",
    "context_max_age_s=0", "context_max_age_s=121", "context_max_age_s=nan", "context_max_age_s=inf",
])
def test_invalid_context_configuration_is_rejected(tmp_path, setting):
    path = tmp_path / "settings.toml"
    path.write_text("[translation]\n" + setting + "\n")
    with pytest.raises(LiveTranslateError): load_config(path)


def test_context_configuration_can_be_overridden_or_disabled(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text("[translation]\ncontext_segments=2\ncontext_max_chars=800\ncontext_max_age_s=30\n")
    cfg = load_config(path, {"translation": {"context_segments": 0, "context_max_age_s": 5}})
    assert cfg.translation.context_segments == 0 and cfg.translation.context_max_age_s == 5
