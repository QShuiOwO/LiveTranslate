"""Language routing and switch races without requiring model weights."""
from dataclasses import replace
import io
import json
from pathlib import Path
import threading
from time import monotonic_ns

import pytest

from livetranslate.backends.llama import LlamaTranslator
from livetranslate.backends.qwen_asr import QwenASRBackend
from livetranslate.config import ASRConfig, Config, TranslationConfig, load_config, validate
from livetranslate.errors import LiveTranslateError
from livetranslate.languages import LANGUAGES, speech_text
from livetranslate.pipeline import TranslationPipeline
from livetranslate.process import ProcessResult
from livetranslate.results import ASRResult, TranslationResult
from livetranslate.streaming.context import TranslationContext
from livetranslate.streaming.engine import LiveEngine
from livetranslate.streaming.services import PersistentQwen
from livetranslate.streaming.state import CaptionBook, complete_clause, is_prefix, stable_prefix
from livetranslate.streaming.types import AudioFrame, SpeechJob, TextJob
from livetranslate.streaming.vad import Segmenter


def config(language="en"):
    return replace(Config(), asr=ASRConfig(engine="qwen3", language=language))


def job(uid=1, language="en", generation=0, final=True, revision=1):
    now = monotonic_ns()
    return SpeechJob(uid, revision, b"same audio", now-1_000_000, now, now, now, final,
                     source_language=language, language_generation=generation)


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_language_config_and_overrides(tmp_path, language):
    path = tmp_path / "settings.toml"
    path.write_text('[asr]\nengine="qwen3"\nlanguage="en"\n')
    cfg = load_config(path, {"asr": {"language": language}})
    assert cfg.asr.language == language and cfg.stream.opacity == .85


@pytest.mark.parametrize("language", ["auto", "jp", "zh", "", 1, []])
def test_unsupported_language_is_rejected(language):
    with pytest.raises(LiveTranslateError, match="language"):
        validate(config(language))


@pytest.mark.parametrize("model", ["ggml-small.en.bin", "ggml-small.en-q5_1.bin"])
@pytest.mark.parametrize("language", [code for code in LANGUAGES if code != "en"])
def test_english_only_whisper_cannot_claim_multilingual_support(model, language):
    with pytest.raises(LiveTranslateError, match="仅支持英语"):
        validate(replace(Config(), asr=ASRConfig(model=model, language=language)))
    assert validate(replace(Config(), asr=ASRConfig(model="ggml-small.bin", language=language))).asr.language == language


def test_japanese_prefix_clause_and_revised_suffix():
    old = "今日は天気が良いので、公園に行きます。"
    new = "今日は天気が良いので、散歩に出かけます。"
    prefix = stable_prefix(old, new, "ja")
    assert prefix == "今日は天気が良いので、"
    assert complete_clause(prefix, "ja") == prefix
    assert complete_clause("おはよう", "ja") == ""
    assert complete_clause("明日は友達と一緒に新しい映画を見に行く", "ja")
    assert is_prefix(prefix, new, "ja") and not is_prefix(old, new, "ja")
    assert speech_text(new) and not speech_text("……！？") and not speech_text("[SILENCE]")


def test_japanese_provisional_translation_survives_only_matching_prefix():
    book = CaptionBook()
    j = job(language="ja", final=False)
    text = "今日は天気が良いので、公園に行きます。"
    book.asr(j, text)
    partial = TextJob(j, "今日は天気が良いので、", monotonic_ns())
    assert book.translation(partial, "今天天气很好，")["translated"]
    row = book.asr(replace(j, revision=2), "今日は天気が良いので、散歩します。")
    assert row["translated"] and row["source_language"] == "ja"
    row = book.asr(replace(j, revision=3), "明日は雨になります。")
    assert not row["translated"]


def test_switch_drops_active_segment_and_pre_roll_without_reporting_device_gap():
    detector = type("Detector", (), {"is_speech": lambda *args: True})()
    segmenter = Segmenter(Config().stream, detector)
    pcm = b"\x10\x10" * 320
    for i in range(30):
        segmenter.feed(AudioFrame(pcm, i, i*20_000_000, (i+1)*20_000_000))
    new_jobs = []
    for i in range(30, 100):
        new_jobs += segmenter.feed(AudioFrame(pcm, i, i*20_000_000, (i+1)*20_000_000,
                                              source_language="ja", language_generation=1))
    new_jobs.append(segmenter.finish())
    assert new_jobs and all(j.source_language == "ja" and j.language_generation == 1 for j in new_jobs)
    assert all(j.onset_ns >= 600_000_000 for j in new_jobs)
    assert segmenter.gaps == 0 and segmenter.continuity == 1


def test_context_cannot_cross_language_switch_even_after_switching_back():
    context = TranslationContext(TranslationConfig())
    first = job(); context.remember(first, "Previous English.")
    second = job(2, "ja", 1)
    assert context.before(second) == ()
    context.remember(second, "前の日本語です。")
    assert context.before(job(3, "en", 2)) == ()


class Adapter:
    def close(self): pass
    def recognize(self, pcm, *, language="en"): return "今日は天気が良いです。" if language == "ja" else "Good weather today."
    def translate(self, text, *, context=(), source_language="en"): return "今天天气很好。"


@pytest.fixture
def engine(tmp_path):
    e = LiveEngine(config(), tmp_path, Adapter(), Adapter(), Adapter(), object())
    yield e
    e.metrics.close()


def test_switch_during_inflight_asr_discards_old_result_and_routes_new_request(engine):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def recognize(pcm, *, language):
        calls.append(language)
        if language == "en":
            entered.set(); assert release.wait(2)
        return Adapter().recognize(pcm, language=language)
    engine.asr.recognize = recognize
    engine.asr_queue.put(job())
    worker = threading.Thread(target=engine._asr); worker.start()
    try:
        assert entered.wait(2)
        assert engine.switch_language("ja") == 1
        engine.asr_queue.put(job(2, "ja", 1))
    finally:
        release.set(); engine.asr_queue.close(); worker.join(3)
    assert not worker.is_alive() and calls == ["en", "ja"]
    assert list(engine.book.rows) == [2]
    translation = engine.translation_queue.get()
    assert translation.speech.source_language == "ja" and translation.context == ()
    assert engine.translation_queue.get() is None
    assert all(r["language_generation"] == 1 for r in engine.bus.drain() if r["kind"] == "caption")


def test_switch_during_translation_discards_result_and_clears_context_cache(engine):
    entered, release = threading.Event(), threading.Event()
    requests = []
    def translate(text, *, context, source_language):
        requests.append((source_language, context))
        if source_language == "en":
            entered.set(); assert release.wait(2)
        return "今天天气很好。"
    engine.translator.translate = translate
    first = job(); engine.book.asr(first, "same source")
    engine.translation_queue.put(TextJob(first, "same source", monotonic_ns(), ("Old context",)))
    worker = threading.Thread(target=engine._translate); worker.start()
    try:
        assert entered.wait(2)
        engine.switch_language("ja")
        second = job(2, "ja", 1); engine.book.asr(second, "same source")
        engine.translation_queue.put(TextJob(second, "same source", monotonic_ns()))
    finally:
        release.set(); engine.translation_queue.close(); worker.join(3)
    assert not worker.is_alive()
    assert requests == [("en", ("Old context",)), ("ja", ())]
    assert list(engine.book.rows) == [2] and engine.book.bilingual_count == 1
    assert engine.book.rows[2]["zh_final"]


def test_stable_japanese_draft_triggers_translation_without_spaces(engine):
    engine.switch_language("ja")
    context = TranslationContext(engine.config.translation)
    j = job(language="ja", generation=1, final=False)
    engine._accept_asr(j, "今日は天気が良いので、公園に行きます。", context)
    engine._accept_asr(replace(j, revision=2), "今日は天気が良いので、公園に行きました。", context)
    engine.translation_queue.close()
    queued = engine.translation_queue.get()
    assert queued and not queued.final and queued.text == "今日は天気が良いので、"


@pytest.mark.parametrize("style", ["hy_mt", "subtitle"])
def test_japanese_translation_prompt_names_source_and_keeps_context(style):
    client = LlamaTranslator(TranslationConfig(prompt_style=style))
    for context in ((), ("前の文です。",)):
        messages = client.messages("こんにちは。", source_language="ja", context=context)
        assert "日语" in str(messages) and "简体中文" in str(messages)
        assert "英语" not in str(messages) and messages[-1]["content"].endswith("こんにちは。")
        if context: assert "前の文です。" in messages[-1]["content"]


def test_qwen_file_sends_ja_and_accepts_japanese(audio, tmp_path, monkeypatch):
    monkeypatch.setattr("livetranslate.backends.qwen_asr.check_model", lambda c: Path("/model.gguf"))
    monkeypatch.setattr("livetranslate.backends.qwen_asr.verify_device", lambda c: {"name": "Vulkan0"})
    def run(args, **kwargs):
        assert args[args.index("--language") + 1] == "ja"
        return ProcessResult(0, json.dumps({"text": "今日は晴れです。", "stop": "complete"}),
                             "Loaded Qwen3-ASR-1.7B on Vulkan0 in 0.4 s", 1)
    monkeypatch.setattr("livetranslate.backends.qwen_asr.run_command", run)
    result = QwenASRBackend(config("ja").asr, tmp_path).transcribe(audio)
    assert result.success and result.source_language == "ja" and result.transcript == "今日は晴れです。"


def test_http_language_override_is_per_request(tmp_path, monkeypatch):
    monkeypatch.setattr("livetranslate.backends.qwen_asr.check_model", lambda c: Path("/model.gguf"))
    monkeypatch.setattr("livetranslate.backends.qwen_asr.verify_device", lambda c: {})
    c = PersistentQwen(config(), tmp_path, lambda *a: None)
    monkeypatch.setattr(c.service, "ensure", lambda: None)
    bodies = []
    class Response(io.BytesIO):
        headers = {"X-Speech-Stop": "complete"}
    def opened(request, **kwargs):
        bodies.append(request.data)
        return Response(json.dumps({"text": "今日は晴れです。"}).encode())
    monkeypatch.setattr(c.opener, "open", opened)
    assert c.recognize(b"\1\0"*320, language="ja") == "今日は晴れです。"
    c.recognize(b"\1\0"*320, language="en")
    assert b'name="language"\r\n\r\nja\r\n' in bodies[0]
    assert b'name="language"\r\n\r\nen\r\n' in bodies[1]
    assert c.fields["language"] == "en"


def test_file_pipeline_propagates_source_language_to_translation_and_json(audio):
    class ASR:
        def transcribe(self, path):
            return ASRResult(transcript="今日は晴れです。", source_language="ja", success=True)
    class Translator:
        def translate(self, text, *, source_language):
            assert source_language == "ja" and text == "今日は晴れです。"
            return TranslationResult(translated_text="今天是晴天。", success=True)
    result = TranslationPipeline(None, ASR(), Translator(), source_language="ja").run(input_file=audio)
    assert result.success and result.source_language == "ja" and result.target_language == "zh-CN"


def test_japanese_prompt_never_rewrites_source_or_prior_text():
    client = LlamaTranslator(TranslationConfig(prompt_style="hy_mt"))
    source = "画面には「以下文本」と表示されています。"
    prior = "前の画面にも「以下文本」があります。"
    content = client.messages(source, source_language="ja", context=(prior,))[0]["content"]
    assert content.endswith(source) and prior in content


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_live_cli_language_override_reaches_engine_config(tmp_path, monkeypatch, language):
    from livetranslate.cli import main
    path = tmp_path / "settings.toml"
    path.write_text('[asr]\nengine="qwen3"\nlanguage="en"\n')
    calls = []
    def live(cfg, **kwargs):
        calls.append(cfg.asr.language)
        return 0
    monkeypatch.setattr("livetranslate.streaming.runner.run_live", live)
    assert main(["live", "--config", str(path), "--language", language]) == 0
    assert calls == [language]


NEW_LANGUAGE_CASES = [
    ('ko', '인공지능은 세상을 바꾸고 있습니다.'),
    ('fr', "L'intelligence artificielle change le monde."),
    ('de', 'Künstliche Intelligenz verändert die Welt.'),
    ('es', 'La inteligencia artificial está cambiando el mundo.'),
    ('it', "L'intelligenza artificiale sta cambiando il mondo."),
    ('ru', 'Искусственный интеллект меняет мир.'),
]


@pytest.mark.parametrize('language,text', NEW_LANGUAGE_CASES)
@pytest.mark.parametrize('style', ['hy_mt', 'subtitle'])
def test_new_languages_use_correct_translation_prompt_and_preserve_source(language, text, style):
    translator = LlamaTranslator(TranslationConfig(prompt_style=style))
    for context in ((), (text,)):
        messages = translator.messages(text, source_language=language, context=context)
        assert LANGUAGES[language] in str(messages) and '简体中文' in str(messages)
        assert '日语' not in str(messages) and '英语' not in str(messages)
        assert messages[-1]['content'].endswith(text)
        if context: assert text in messages[-1]['content'].split('【待翻译文本】')[0]


@pytest.mark.parametrize('language,text', NEW_LANGUAGE_CASES)
def test_new_asr_languages_pass_through_cli_and_http(audio, tmp_path, monkeypatch, language, text):
    monkeypatch.setattr('livetranslate.backends.qwen_asr.check_model', lambda c: Path('/model.gguf'))
    monkeypatch.setattr('livetranslate.backends.qwen_asr.verify_device', lambda c: {'name': 'Vulkan0'})
    def run(args, **kwargs):
        assert args[args.index('--language')+1] == language
        return ProcessResult(0, json.dumps({'text': text, 'stop': 'complete'}),
                             'Loaded Qwen3-ASR-1.7B on Vulkan0 in 0.4 s', 1)
    monkeypatch.setattr('livetranslate.backends.qwen_asr.run_command', run)
    result = QwenASRBackend(config(language).asr, tmp_path).transcribe(audio)
    assert result.success and result.source_language == language and result.transcript == text
    backend = PersistentQwen(config(), tmp_path, lambda *a: None)
    monkeypatch.setattr(backend.service, 'ensure', lambda: None)
    class Response(io.BytesIO):
        headers = {'X-Speech-Stop': 'complete'}
    def opened(request, **kwargs):
        assert f'name="language"\r\n\r\n{language}\r\n'.encode() in request.data
        return Response(json.dumps({'text': text}).encode())
    monkeypatch.setattr(backend.opener, 'open', opened)
    assert backend.recognize(b'\1\0' * 320, language=language) == text
    assert backend.fields['language'] == 'en'


@pytest.mark.parametrize('language,text', NEW_LANGUAGE_CASES)
def test_new_languages_form_stable_clauses_and_preserve_matching_translations(language, text):
    assert stable_prefix(text, text + ' Next', language) == text
    assert complete_clause(text, language) == text
    assert is_prefix(text, text + ' Next', language)
    book = CaptionBook(); j = job(language=language, final=False)
    book.asr(j, text)
    book.translation(TextJob(j, text, monotonic_ns()), '人工智能正在改变世界。')
    assert book.asr(replace(j, revision=2), text + ' Next')['translated']


def test_language_specific_word_rules_do_not_apply_english_tail_trimming_to_others():
    # The same spelling can be a French verb, not an unfinished English article.
    french = "Ce sont les livres que Paul a"
    assert complete_clause(french, 'fr') == french
    assert complete_clause('인공지능은 세상을 바꾸고 있습니다', 'ko')
    assert complete_clause('인공지능은 세상을', 'ko') == ''
    assert stable_prefix('Cafe\u0301 français', 'Café français demain', 'fr') == 'Café français'
    assert is_prefix('Cafe\u0301 français', 'Café français demain', 'fr')
    assert is_prefix('¿Cómo funciona', 'Cómo funciona este sistema?', 'es')
    assert complete_clause('Er sagt das sehr deutlich.“', 'de') == 'Er sagt das sehr deutlich.“'


@pytest.mark.parametrize('language,text', NEW_LANGUAGE_CASES)
def test_switch_to_each_new_language_isolates_old_context(engine, language, text):
    previous = job(); engine.book.asr(previous, 'Earlier English.')
    context = TranslationContext(engine.config.translation)
    context.remember(previous, 'Earlier English.')
    generation = engine.switch_language(language)
    j = job(2, language, generation)
    engine._accept_asr(j, text, context)
    engine.translation_queue.close()
    translated = engine.translation_queue.get()
    assert translated.speech.source_language == language and translated.text == text
    assert translated.context == () and list(engine.book.rows) == [2]
