from dataclasses import replace
import pytest

from livetranslate.config import Config
from livetranslate.errors import LiveTranslateError
from livetranslate.server import server_command, verify_server_backend


def test_missing_gguf_does_not_start_service(tmp_path):
    config = Config()
    config = replace(config, server=replace(config.server, model=str(tmp_path / "missing.gguf")))
    with pytest.raises(LiveTranslateError) as error:
        server_command(config)
    assert error.value.code == "translation_model_missing"


def test_server_binds_loopback_and_disables_cloud_downloads(tmp_path):
    model = tmp_path / "test.gguf"
    model.write_bytes(b"GGUFplaceholder")
    config = Config()
    config = replace(config, server=replace(config.server, model=str(model)))
    args = server_command(config)
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert "--offline" in args and "--parallel" in args
    cpu = server_command(replace(config, server=replace(config.server, backend="cpu")))
    assert cpu[cpu.index("--n-gpu-layers") + 1] == "0"
    assert cpu[cpu.index("--device") + 1] == "none"


@pytest.mark.parametrize("log", ["Found Vulkan GPU", "offloaded 0/37 layers to GPU\nVulkan0 model buffer size = 0 MiB",
    "using device Vulkan0 (llvmpipe)\noffloaded 37/37 layers to GPU\nVulkan0 model buffer size = 2000 MiB"])
def test_server_startup_does_not_claim_unproven_gpu(log):
    with pytest.raises(LiveTranslateError):
        verify_server_backend(log, "vulkan", "Vulkan0")
