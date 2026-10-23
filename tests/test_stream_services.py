import io
from dataclasses import replace
from pathlib import Path
import pytest

from livetranslate.config import Config
from livetranslate.errors import LiveTranslateError
from livetranslate.streaming.services import NativeService, PersistentWhisper


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
