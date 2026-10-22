import io
import json
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
import pytest

from livetranslate.backends.llama import LlamaTranslator, NoRedirect
from livetranslate.backends.whisper import WhisperBackend, backend_evidence
from livetranslate.config import ASRConfig, TranslationConfig
from livetranslate.errors import LiveTranslateError
from livetranslate.process import ProcessResult, run_command


def test_missing_real_model_is_structured(audio, tmp_path):
    result = WhisperBackend(ASRConfig(model=str(tmp_path / "missing.bin")), tmp_path).transcribe(audio)
    assert not result.success and result.error_code == "asr_model_missing"


def test_gpu_flag_or_enumeration_alone_is_not_inference():
    assert backend_evidence("use gpu = 1\nggml_vulkan: 0 = RX 9070 XT", "vulkan")[0] == "unverified"
    log = "ggml_vulkan: 0 = llvmpipe\nwhisper_backend_init_gpu: using Vulkan0 backend\nwhisper_model_load: Vulkan0 total size = 487 MB"
    assert backend_evidence(log, "vulkan")[0] == "unverified"


def mock_llama(monkeypatch, *, content="人工智能正在改变世界。", finish="stop"):
    client = LlamaTranslator(TranslationConfig())
    calls = []
    def request(path, body=None):
        calls.append((path, body))
        if path == "/health":
            return {"status": "ok"}
        if path == "/v1/models":
            return {"data": [{"id": client.config.model}]}
        return {"choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {"completion_tokens": 8}}
    monkeypatch.setattr(client, "request", request)
    return client, calls


def test_translation_request_is_local_bounded_and_deterministic(monkeypatch):
    client, calls = mock_llama(monkeypatch)
    result = client.translate("Artificial intelligence is changing the world.")
    assert result.success and result.translated_text == "人工智能正在改变世界。"
    path, body = calls[-1]
    assert path == "/v1/chat/completions" and body["temperature"] == 0 and body["max_tokens"] == 256
    assert body["seed"] == 42 and not body["stream"]


@pytest.mark.parametrize("content,finish,code", [("", "stop", "translation_empty"),
    ("译文：你好", "stop", "translation_format"), ("<think>analysis</think>你好", "stop", "translation_format"),
    ("未完成", "length", "translation_incomplete"), (None, "stop", "translation_response_invalid")])
def test_bad_translation_is_not_success(monkeypatch, content, finish, code):
    client, _ = mock_llama(monkeypatch, content=content, finish=finish)
    result = client.translate("hello")
    assert not result.success and result.error_code == code


def test_unavailable_service(monkeypatch):
    client = LlamaTranslator(TranslationConfig())
    def fail(*args, **kwargs):
        raise URLError("Connection refused")
    monkeypatch.setattr(client.opener, "open", fail)
    result = client.translate("Hello")
    assert not result.success and result.error_code == "translation_unavailable"


def test_invalid_server_json(monkeypatch):
    client = LlamaTranslator(TranslationConfig())
    monkeypatch.setattr(client.opener, "open", lambda *a, **kw: io.BytesIO(b"not-json"))
    result = client.translate("Hello")
    assert result.error_code == "translation_response_invalid"


def test_malformed_model_list_is_structured(monkeypatch):
    client = LlamaTranslator(TranslationConfig())
    monkeypatch.setattr(client, "request", lambda path, body=None: {"status": "ok"} if path == "/health" else {"data": None})
    assert client.translate("Hello").error_code == "translation_response_invalid"


def test_malformed_whisper_json_is_structured(audio, tmp_path, monkeypatch):
    # Placeholder exists only inside this mocked subprocess unit test.
    model = tmp_path / "placeholder.bin"
    with model.open("wb") as f:
        f.truncate(1024 * 1024)
    def fake_process(args, **kwargs):
        prefix = Path(args[args.index("-of") + 1])
        prefix.with_suffix(".json").write_text(json.dumps({"transcription": [{"text": None}]}))
        return ProcessResult(0, "", "use gpu = 0", 1)
    monkeypatch.setattr("livetranslate.backends.whisper.run_command", fake_process)
    result = WhisperBackend(ASRConfig(model=str(model), backend="cpu"), tmp_path).transcribe(audio)
    assert not result.success and result.error_code == "asr_output_invalid"


def test_redirect_cannot_send_text_elsewhere():
    assert NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com") is None


def test_subprocess_status_stderr_and_timeout():
    with pytest.raises(LiveTranslateError) as error:
        run_command([sys.executable, "-c", "import sys; sys.stderr.write('device gone'); sys.exit(17)"], timeout=2)
    assert error.value.code == "process_failed" and "17" in str(error.value) and "device gone" in str(error.value)
    with pytest.raises(LiveTranslateError) as error:
        run_command([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.03)
    assert error.value.code == "process_timeout"
    with pytest.raises(LiveTranslateError) as error:
        run_command(["/missing/binary"], timeout=2)
    assert error.value.code == "executable_missing"
