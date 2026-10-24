import io
from dataclasses import replace
from pathlib import Path
import pytest

from livetranslate.config import Config
from livetranslate.errors import LiveTranslateError
from livetranslate.results import TranslationResult
from livetranslate.streaming.services import ManagedTranslator, NativeService, PersistentWhisper


def client(tmp_path, monkeypatch):
    c = PersistentWhisper(Config(), tmp_path, lambda *a: None)
    monkeypatch.setattr(c.service, "ensure", lambda: None)
    return c


def test_persistent_asr_sends_wav_and_greedy_parameters(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    requests = []
    def open_request(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b'{"text": " Hello world. "}')
    monkeypatch.setattr(c.opener, "open", open_request)
    assert c.recognize(b'\1\0' * 16000) == "Hello world."
    body = requests[0].data
    assert b"RIFF" in body and b"audio/wav" in body and b'temperature_inc' in body
    assert requests[0].full_url.startswith("http://127.0.0.1:8082/")
    assert "--convert" not in c.service.args and "-nc" not in c.service.args


@pytest.mark.parametrize("raw", [b'not JSON', b'{"text":null}', b'{}'])
def test_broken_asr_response_invalidates_owned_service(tmp_path, monkeypatch, raw):
    c = client(tmp_path, monkeypatch)
    invalidated = []
    monkeypatch.setattr(c.service, "invalidate", lambda: invalidated.append(True))
    monkeypatch.setattr(c.opener, "open", lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(LiveTranslateError): c.recognize(b'\1\0' * 16000)
    assert invalidated == [True]


def test_asr_network_failure_is_recoverable(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    monkeypatch.setattr(c.service, "invalidate", lambda: None)
    def fail(*args, **kwargs): raise TimeoutError("service stopped")
    monkeypatch.setattr(c.opener, "open", fail)
    with pytest.raises(LiveTranslateError, match="service stopped"): c.recognize(b'\0' * 640)


def test_external_service_is_never_terminated_on_shutdown(tmp_path):
    class ExternalProcess:
        def terminate(self): raise AssertionError("Must not terminate a user-owned service")
        def kill(self): raise AssertionError("Must not kill a user-owned service")
    service = NativeService("llama", [], 8080, tmp_path, lambda log: [], lambda *a: None)
    service.external = True
    service.process = ExternalProcess()
    service.close()
    assert service.closed


def test_managed_translator_passes_the_frozen_english_context(tmp_path, monkeypatch):
    monkeypatch.setattr("livetranslate.streaming.services.server_command", lambda config: ["mock-server"])
    translator = ManagedTranslator(Config(), tmp_path, lambda *a: None)
    events = []
    monkeypatch.setattr(translator.service, "ensure", lambda: events.append("ready"))
    def translate(text, *, context=()):
        events.append((text, context))
        return TranslationResult(translated_text="要是我这么做会怎样？", success=True)
    monkeypatch.setattr(translator.client, "translate", translate)
    context = ("I am trying to make a tool.", "The last attempt did not work.")
    assert translator.translate("What if I do like this?", context=context) == "要是我这么做会怎样？"
    assert events == ["ready", ("What if I do like this?", context)]
    assert translator.client.config.timeout == Config().stream.inference_timeout_s


@pytest.mark.parametrize("code,restart", [("translation_context_long", False), ("translation_unavailable", True)])
def test_context_requests_preserve_service_error_recovery(tmp_path, monkeypatch, code, restart):
    monkeypatch.setattr("livetranslate.streaming.services.server_command", lambda config: ["mock-server"])
    translator = ManagedTranslator(Config(), tmp_path, lambda *a: None)
    monkeypatch.setattr(translator.service, "ensure", lambda: None)
    invalidated = []
    monkeypatch.setattr(translator.service, "invalidate", lambda: invalidated.append(True))
    received = []
    def translate(text, *, context=()):
        received.append(context)
        return TranslationResult(error="request failed", error_code=code)
    monkeypatch.setattr(translator.client, "translate", translate)
    context = ("Previous English sentence.",)
    with pytest.raises(LiveTranslateError) as error:
        translator.translate("Current sentence.", context=context)
    assert error.value.code == code and received == [context]
    assert invalidated == ([True] if restart else [])
