from pathlib import Path
import pytest

from livetranslate.config import load_config, local_base_url
from livetranslate.errors import LiveTranslateError


def test_toml_relative_paths_and_overrides(tmp_path):
    p = tmp_path / "settings.toml"
    p.write_text('[asr]\nmodel="weights/small.bin"\nbackend="cpu"\n[audio]\nduration=12\n')
    config = load_config(p, {"audio": {"duration": 20, "source": None}})
    assert config.asr.model == str(tmp_path / "weights/small.bin")
    assert config.asr.backend == "cpu"
    assert config.audio.duration == 20
    assert config.audio.source == ""


def test_invalid_table_is_not_hidden_by_cli_override(tmp_path):
    p = tmp_path / "settings.toml"
    p.write_text('audio=3')
    with pytest.raises(LiveTranslateError):
        load_config(p, {"audio": {"duration": 15}})


@pytest.mark.parametrize("text", [
    "not TOML = ]", "[unknown]\nx=1", "[asr]\ntimeout=-1", "[asr]\nthreads=1.2",
    "[asr]\nmodel=3", "[audio]\nduration=5", "[audio]\nduration=nan",
    "[translation]\nmax_tokens=100000", "[server]\nport=70000", "[server]\ngpu_layers=0",
    "[asr]\nbackend='cuda'", "[audio]\nmicrophone=true", "audio=3",
])
def test_invalid_configuration_is_explained(tmp_path, text):
    p = tmp_path / "bad.toml"
    p.write_text(text)
    with pytest.raises(LiveTranslateError, match="."):
        load_config(p)


def test_missing_config():
    with pytest.raises(LiveTranslateError) as error:
        load_config(Path("/does-not-exist.toml"))
    assert error.value.code == "config"


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "http://localhost:8080", "http://127.0.0.1.evil:8080",
                                     "http://127.0.0.1:8080@evil.com", "http://127.0.0.1:8080/?x=1",
                                     "http://127.0.0.1:8080/other", "http://127.0.0.1:invalid", "http://0.0.0.0:8080"])
def test_cloud_dns_and_unsafe_urls_rejected(url):
    with pytest.raises(LiveTranslateError):
        local_base_url(url)


def test_local_v1_url_normalizes():
    assert local_base_url("http://127.0.0.1:8080/v1/") == "http://127.0.0.1:8080"
