import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from livetranslate.backends.factory import create_asr
from livetranslate.backends.llama import LlamaTranslator
from livetranslate.backends.qwen_asr import QwenASRBackend, backend_evidence, transcript_text, verify_device
from livetranslate.backends.whisper import WhisperBackend
from livetranslate.config import ASRConfig, Config, TranslationConfig, load_config
from livetranslate.errors import LiveTranslateError
from livetranslate.process import ProcessResult
from livetranslate.streaming.services import PersistentQwen


def test_factory_selects_explicit_engine():
    assert isinstance(create_asr(ASRConfig()), WhisperBackend)
    assert isinstance(create_asr(ASRConfig(engine="qwen3")), QwenASRBackend)
    with pytest.raises(LiveTranslateError):
        create_asr(ASRConfig(engine="unknown"))


def test_missing_model_and_safetensors_directory_are_structured(audio, tmp_path):
    for path in (tmp_path / "missing.gguf", tmp_path):
        r = QwenASRBackend(ASRConfig(engine="qwen3", model=str(path)), tmp_path).transcribe(audio)
        assert not r.success and r.error_code == "asr_model_missing"


@pytest.mark.parametrize("backend,name", [("vulkan", "Vulkan0"), ("cpu", "CPU")])
def test_qwen_file_result_and_explicit_device(audio, tmp_path, monkeypatch, backend, name):
    monkeypatch.setattr("livetranslate.backends.qwen_asr.check_model", lambda c: Path("/real-model.gguf"))
    monkeypatch.setattr("livetranslate.backends.qwen_asr.verify_device", lambda c: {"name": name})
    def run(args, **kwargs):
        assert args[args.index("--device")+1] == name
        assert kwargs["env"]["SPEECH_NO_UPDATE_CHECK"] == "1"
        assert "--no-warmup" not in args
        assert str(audio.resolve()) == args[-1]
        return ProcessResult(0, '{"text":"Ask what you can do for your country.","stop":"complete","languages":["en"]}',
                             f"Loaded Qwen3-ASR-1.7B on {name} in 0.4 s", 2)
    monkeypatch.setattr("livetranslate.backends.qwen_asr.run_command", run)
    r = QwenASRBackend(ASRConfig(engine="qwen3", backend=backend), tmp_path).transcribe(audio)
    assert r.success and r.backend == backend and r.elapsed_ms > 0 and r.gpu_evidence
    assert "country" in r.transcript and Path(r.log_file).is_file()


def test_qwen_timeout_retains_structured_error_and_log(audio, tmp_path, monkeypatch):
    monkeypatch.setattr("livetranslate.backends.qwen_asr.check_model", lambda c: Path("/real-model.gguf"))
    monkeypatch.setattr("livetranslate.backends.qwen_asr.verify_device", lambda c: {})
    def timeout(*a, **kw): raise LiveTranslateError("process_timeout", "Qwen request exceeded its deadline")
    monkeypatch.setattr("livetranslate.backends.qwen_asr.run_command", timeout)
    r = QwenASRBackend(ASRConfig(engine="qwen3"), tmp_path).transcribe(audio)
    assert not r.success and r.error_code == "process_timeout"
    assert "deadline" in Path(r.log_file).read_text()


@pytest.mark.parametrize("device", [
    {"name": "Vulkan0", "description": "llvmpipe", "kind": "gpu"},
    {"name": "Vulkan0", "description": "CPU", "kind": "cpu"},
    {"name": "Vulkan1", "description": "AMD Radeon", "kind": "gpu"},
])
def test_qwen_refuses_wrong_or_software_gpu(monkeypatch, device):
    monkeypatch.setattr("livetranslate.backends.qwen_asr.run_command",
                        lambda *a, **kw: ProcessResult(0, json.dumps({"devices": [device]}), "", 1))
    with pytest.raises(LiveTranslateError, match="Cannot verify"):
        verify_device(ASRConfig(engine="qwen3"))


def test_qwen_enumeration_alone_does_not_prove_model_device():
    c = ASRConfig(engine="qwen3")
    d = {"name": "Vulkan0", "kind": "gpu", "description": "AMD Radeon RX 9070 XT"}
    for log in ("Vulkan0 available", "Loaded Qwen3-ASR-1.7B on CPU in 1 s",
                "Loaded Qwen3-ASR-0.6B on Vulkan0 in 1 s", "Loaded Qwen3-ASR-1.7B on Vulkan01 in 1 s"):
        with pytest.raises(LiveTranslateError): backend_evidence(log, c, d)
    assert backend_evidence("Loaded Qwen3-ASR-1.7B on Vulkan0 in 0.4 s", c, d)
    assert backend_evidence("  Qwen3-ASR-1.7B  recognition  Vulkan0  0.4 s", c, d)


@pytest.mark.parametrize("data,stop", [({}, "complete"), ({"text": None}, "complete"),
    ([], "complete"), ({"text": "partial output"}, "model_limit"), ({"text": "partial output"}, None)])
def test_qwen_malformed_or_incomplete_result_is_not_success(data, stop):
    with pytest.raises(LiveTranslateError): transcript_text(data, stop)


def test_qwen_completed_text_preserves_unicode_and_empty_speech():
    assert transcript_text({"text": "  Hello\nworld.  "}, "complete") == "Hello world."
    assert transcript_text({"text": ""}, "complete") == ""
    assert transcript_text({"text": "你好"}, "complete") == "你好"


def qwen_client(tmp_path, monkeypatch):
    monkeypatch.setattr("livetranslate.backends.qwen_asr.check_model", lambda c: Path("/model.gguf"))
    monkeypatch.setattr("livetranslate.backends.qwen_asr.verify_device", lambda c: {})
    c = PersistentQwen(replace(Config(), asr=ASRConfig(engine="qwen3", binary="speech")), tmp_path, lambda *a: None)
    monkeypatch.setattr(c.service, "ensure", lambda: None)
    return c


class Response(io.BytesIO):
    def __init__(self, data, stop="complete"):
        super().__init__(data)
        self.headers = {"X-Speech-Stop": stop}


def test_qwen_http_contract_and_loopback(tmp_path, monkeypatch):
    c = qwen_client(tmp_path, monkeypatch)
    requests = []
    def opened(request, **kwargs):
        requests.append((request, kwargs))
        return Response(b'{"text":"Hello world."}')
    monkeypatch.setattr(c.opener, "open", opened)
    assert c.recognize(b'\1\0' * 16000) == "Hello world."
    req, options = requests[0]
    assert req.full_url == "http://127.0.0.1:8083/v1/audio/transcriptions"
    assert b"Qwen3-ASR-1.7B" in req.data and b"RIFF" in req.data
    assert b"temperature" not in req.data
    assert options["timeout"] == 4
    assert c.service.args[c.service.args.index("--host") + 1] == "127.0.0.1"
    assert "--no-warmup" not in c.service.args


def test_qwen_service_failure_invalidates_owned_process(tmp_path, monkeypatch):
    c = qwen_client(tmp_path, monkeypatch)
    invalidated = []
    monkeypatch.setattr(c.service, "invalidate", lambda: invalidated.append(True))
    def fail(*a, **kw): raise TimeoutError("server unavailable")
    monkeypatch.setattr(c.opener, "open", fail)
    with pytest.raises(LiveTranslateError, match="server unavailable"):
        c.recognize(b'\0' * 640)
    assert invalidated == [True]


def test_qwen_http_limit_is_not_a_final_transcript(tmp_path, monkeypatch):
    c = qwen_client(tmp_path, monkeypatch)
    monkeypatch.setattr(c.opener, "open", lambda *a, **kw: Response(b'{"text":"unfinished"}', "model_limit"))
    with pytest.raises(LiveTranslateError, match="model_limit"):
        c.recognize(b'\1\0' * 16000)


def test_hy_prompt_keeps_source_and_requests_simplified_chinese():
    client = LlamaTranslator(TranslationConfig(prompt_style="hy_mt"))
    source = "Ask not what your country can do for you."
    messages = client.messages(source)
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert "简体中文" in messages[0]["content"]
    assert messages[0]["content"].endswith("\n\n" + source)
    assert len(LlamaTranslator(TranslationConfig()).messages(source)) == 2


@pytest.mark.parametrize("text", ["[asr]\nengine='llama'", "[asr]\nengine='qwen3'\nbackend='hip'",
    "[asr]\nserver_port=8083.5", "[translation]\nprompt_style='unknown'"])
def test_bad_model_configuration_is_rejected(tmp_path, text):
    p = tmp_path / "bad.toml"; p.write_text(text)
    with pytest.raises(LiveTranslateError): load_config(p)
