from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
from time import monotonic, sleep

from .backends.llama import LlamaTranslator
from .config import Config
from .errors import LiveTranslateError
from .results import save_json
from .telemetry import GPUSampler


def server_command(config: Config) -> list[str]:
    s = config.server
    model = Path(s.model)
    if not model.is_file():
        raise LiveTranslateError("translation_model_missing", f"LLM model not found: {model}")
    with model.open("rb") as f:
        if f.read(4) != b"GGUF":
            raise LiveTranslateError("translation_model_invalid", f"Expected a GGUF model: {model}")
    args = [s.binary, "-m", str(model), "--host", "127.0.0.1", "--port", str(s.port),
            "--alias", config.translation.model, "--ctx-size", str(s.context_size),
            "--parallel", "1", "--threads", str(s.threads), "--jinja", "--offline",
            "--no-webui", "--split-mode", "none", "--verbosity", "4"]
    args += ["--n-gpu-layers", "0", "--device", "none"] if s.backend == "cpu" else [
        "--n-gpu-layers", str(s.gpu_layers), "--device", s.device]
    return args


def verify_server_backend(log: str, requested: str, device: str) -> list[str]:
    lines = log.splitlines()
    if requested == "cpu":
        if re.search(r"CPU.*model buffer size", log):
            return [line for line in lines if "model buffer size" in line]
        raise LiveTranslateError("server_backend_unverified", "CPU model buffer not found in server log")
    allocations = [line for line in lines if device in line and "model buffer size" in line]
    offload = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", log)
    selected = [line for line in lines if device in line and ("using device" in line or "model buffer size" in line)]
    if (not allocations or not offload or int(offload[-1][0]) <= 0
            or any(re.search(r"llvmpipe|lavapipe|software", line, re.I) for line in selected)):
        raise LiveTranslateError("server_backend_unverified", f"Server log does not prove {requested}/{device} model offload")
    return selected + [line for line in lines if "layers to GPU" in line]


def serve(config: Config, log_path: Path | None = None) -> int:
    args = server_command(config)
    directory = Path(config.output.directory)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = log_path or directory / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    health_config = replace(config.translation, base_url=f"http://127.0.0.1:{config.server.port}", timeout=1.0)
    client = LlamaTranslator(health_config)
    # Do not accidentally validate an already-running unrelated service.
    try:
        client.health()
    except LiveTranslateError:
        pass
    else:
        raise LiveTranslateError("server_port_in_use", f"A service is already healthy on port {config.server.port}")
    env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_ARG_")}
    env.update(LC_ALL="C.UTF-8", LANG="C.UTF-8", HF_HUB_OFFLINE="1")
    proc = None
    start = monotonic()
    try:
        with log_path.open("w", encoding="utf-8") as log, GPUSampler() as sampler:
            try:
                proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env)
            except OSError as exc:
                raise LiveTranslateError("server_start", f"Cannot start llama-server: {exc}") from exc
            deadline = start + 180
            while monotonic() < deadline:
                if proc.poll() is not None:
                    raise LiveTranslateError("server_exited", f"llama-server exited {proc.returncode}: {log_path.read_text()[-4000:]}")
                try:
                    client.health()
                    evidence = verify_server_backend(log_path.read_text(encoding="utf-8"), config.server.backend, config.server.device)
                    break
                except LiveTranslateError as exc:
                    if exc.code == "server_backend_unverified":
                        raise
                    sleep(0.25)
            else:
                raise LiveTranslateError("server_start_timeout", f"llama-server was not healthy after 180 seconds; see {log_path}")
        ready = {"success": True, "pid": proc.pid, "base_url": client.base_url, "backend": config.server.backend,
                 "model": config.translation.model, "model_file": config.server.model,
                 "startup_elapsed_ms": (monotonic() - start) * 1000, "gpu_evidence": evidence,
                 "gpu": sampler.summary(), "log_file": str(log_path), "command": args}
        save_json(directory / "server-ready.json", ready)
        print(f"llama-server ready: {client.base_url}; backend={config.server.backend}; pid={proc.pid}; log={log_path}", flush=True)
        return proc.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
