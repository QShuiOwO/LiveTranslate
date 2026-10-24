"""Owned local native services, bounded logs, restart on failure, no global service edits."""
from collections import deque
from dataclasses import replace
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
from time import monotonic, sleep
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4
import wave

from ..backends.llama import LlamaTranslator, NoRedirect
from ..backends.whisper import backend_evidence
from ..errors import LiveTranslateError
from ..languages import speech_text, validate_asr_language
from ..results import save_json
from ..server import server_command, verify_server_backend


class NativeService:
    def __init__(self, name, args, port, directory, verify, status, health_path="/health"):
        self.name, self.args, self.port = name, args, port
        self.directory, self.verify, self.status = Path(directory), verify, status
        self.health_path = health_path
        self.process = None
        self.reader = None
        self.tail = deque(maxlen=256)
        self.restarts = 0
        self.starts = 0
        self.external = False
        self.closed = False
        self.lock = threading.RLock()
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.handler = None
        self.evidence = []

    def _healthy(self):
        try:
            with self.opener.open(f"http://127.0.0.1:{self.port}{self.health_path}", timeout=0.4) as r:
                return r.status == 200
        except OSError:
            return False

    def _read(self, process):
        try:
            for line in iter(process.stdout.readline, b""):
                value = line.decode("utf-8", errors="replace").rstrip()
                self.tail.append(value[:8000])
                self.handler.emit(logging.LogRecord(self.name, logging.INFO, "", 0, value, (), None))
        finally:
            process.stdout.close()

    def ensure(self):
        with self.lock:
            if self.closed:
                raise LiveTranslateError("service_stopped", f"{self.name} is shutting down")
            if self.process is not None and self.process.poll() is None:
                return
            if self.external:
                if not self._healthy():
                    raise LiveTranslateError("service_unavailable", f"External {self.name} is unavailable; not owned, will not terminate it")
                return
            if self.starts == 0:
                with socket.socket() as probe:
                    used = probe.connect_ex(("127.0.0.1", self.port)) == 0
                if used:
                    if self.name == "llama" and self._healthy():
                        self.external = True
                        self.status("service_external", f"Reusing local llama-server on {self.port}; will not stop/restart externally owned server")
                        return
                    raise LiveTranslateError("service_port_in_use", f"Port {self.port} already used; not replacing an unknown service")
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.handler is None:
                self.handler = RotatingFileHandler(self.directory / f"{self.name}.log", maxBytes=8*1024*1024,
                                                   backupCount=2, encoding="utf-8")
                self.handler.setFormatter(logging.Formatter("%(message)s"))
            if self.reader: self.reader.join(timeout=1)
            self.tail.clear()
            env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_ARG_")}
            env.update(LC_ALL="C.UTF-8", LANG="C.UTF-8", HF_HUB_OFFLINE="1", SPEECH_NO_UPDATE_CHECK="1", NO_COLOR="1")
            try:
                self.process = subprocess.Popen(self.args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                env=env, bufsize=0)
            except OSError as exc:
                raise LiveTranslateError("service_start", f"Cannot start {self.name}: {exc}") from exc
            self.starts += 1
            self.restarts = self.starts - 1
            self.reader = threading.Thread(target=self._read, args=(self.process,), name=f"{self.name}-log", daemon=True)
            self.reader.start()
            deadline = monotonic() + 30
            while monotonic() < deadline and not self.closed:
                if self.process.poll() is not None:
                    raise LiveTranslateError("service_exited", f"{self.name} exited {self.process.returncode}: {' '.join(self.tail)[-2000:]}")
                # Initialization logs are parsed before accepting inference readiness.
                try:
                    evidence = self.verify("\n".join(self.tail))
                except LiveTranslateError:
                    evidence = None
                if evidence and self._healthy():
                    self.evidence = evidence
                    save_json(self.directory / f"{self.name}-ready.json", {
                        "pid": self.process.pid, "command": self.args, "port": self.port,
                        "backend_evidence": evidence, "starts": self.starts, "owned": True})
                    self.status("service_ready", f"{self.name} ready on 127.0.0.1:{self.port}")
                    return
                sleep(0.1)
            self.invalidate()
            raise LiveTranslateError("service_start_timeout", f"{self.name} did not initialize with verified GPU logs")

    def invalidate(self):
        with self.lock:
            if self.external: return
            if self.process:
                if self.process.poll() is None:
                    self.process.terminate()
                    try: self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill(); self.process.wait()
                else:
                    self.process.wait()
            if self.reader: self.reader.join(timeout=1)
            self.process = None

    def close(self):
        with self.lock:
            self.closed = True
            self.invalidate()
            if self.handler:
                self.handler.close()


class PersistentASRHTTP:
    def recognize(self, pcm, *, language=None):
        language = validate_asr_language(self.config.asr, self.config.asr.language if language is None else language)
        self.service.ensure()
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
        boundary = "LiveTranslate" + uuid4().hex
        body = bytearray()
        for key, value in {**self.fields, "language": language}.items():
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="segment.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
        body.extend(buffer.getvalue()); body.extend(f"\r\n--{boundary}--\r\n".encode())
        request = Request(f"http://127.0.0.1:{self.service.port}{self.endpoint}", data=bytes(body),
                          headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with self.opener.open(request, timeout=self.config.stream.inference_timeout_s) as response:
                raw = response.read(1024 * 1024 + 1)
                stop = response.headers.get("X-Speech-Stop") if self.config.asr.engine == "qwen3" else None
            if len(raw) > 1024 * 1024: raise ValueError("oversized response")
            data = json.loads(raw)
            if self.config.asr.engine == "qwen3":
                from ..backends.qwen_asr import transcript_text
                text = transcript_text(data, stop)
                return text if speech_text(text) else ""
            if not isinstance(data, dict): raise ValueError("expected JSON object")
            text = data.get("text")
            if not isinstance(text, str): raise ValueError("missing transcript text")
            text = re.sub(r"\s+", " ", text).strip()
            if not speech_text(text): return ""
            return text
        except (OSError, ValueError, TypeError) as exc:
            self.service.invalidate()
            raise LiveTranslateError("stream_asr", f"Local {self.config.asr.engine} request failed: {exc}") from exc

    def close(self): self.service.close()


class PersistentWhisper(PersistentASRHTTP):
    def __init__(self, config, directory, status):
        if config.asr.backend != "vulkan":
            raise LiveTranslateError("stream_backend", "M2 currently validates Vulkan only; M1 CPU mode remains available")
        def verify(log):
            backend, evidence = backend_evidence(log, "vulkan")
            if backend != "vulkan":
                raise LiveTranslateError("asr_backend_unverified", "No verified Vulkan model allocation")
            return evidence
        args = [config.stream.whisper_server, "-m", config.asr.model, "-t", str(config.asr.threads),
                "-l", config.asr.language, "-nf", "-nt", "-sns", "-nlp", "-dev", str(config.asr.device),
                "--host", "127.0.0.1", "--port", str(config.stream.whisper_port)]
        self.service = NativeService("whisper", args, config.stream.whisper_port, directory, verify, status)
        self.config = config
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.endpoint = "/inference"
        self.fields = {"response_format": "json", "temperature": "0", "temperature_inc": "0", "language": config.asr.language}


class PersistentQwen(PersistentASRHTTP):
    def __init__(self, config, directory, status):
        from ..backends.qwen_asr import MODEL_ID, backend_evidence as qwen_evidence, check_model, device_name, verify_device
        model = check_model(config.asr)
        device = verify_device(config.asr)
        args = [config.asr.binary, "serve", str(model), "--device", device_name(config.asr),
                "--threads", str(config.asr.threads), "--language", config.asr.language, "--host", "127.0.0.1",
                "--port", str(config.asr.server_port)]
        self.service = NativeService("qwen-asr", args, config.asr.server_port, directory,
                                    lambda log: qwen_evidence(log, config.asr, device), status)
        self.config = config
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.endpoint = "/v1/audio/transcriptions"
        self.fields = {"response_format": "json", "model": MODEL_ID, "language": config.asr.language}


def persistent_asr(config, directory, status):
    if config.asr.engine == "qwen3":
        return PersistentQwen(config, directory, status)
    return PersistentWhisper(config, directory, status)


class ManagedTranslator:
    def __init__(self, config, directory, status):
        self.service = NativeService("llama", server_command(config), config.server.port, directory,
                                    lambda log: verify_server_backend(log, config.server.backend, config.server.device), status)
        self.client = LlamaTranslator(replace(config.translation, timeout=config.stream.inference_timeout_s))

    def translate(self, text, *, context=(), source_language="en"):
        self.service.ensure()
        result = self.client.translate(text, context=context, source_language=source_language)
        if not result.success:
            if result.error_code in {"translation_http", "translation_unavailable", "translation_not_ready"}:
                self.service.invalidate()
            raise LiveTranslateError(result.error_code or "stream_translation", result.error or "Translation failed")
        return result.translated_text

    def close(self): self.service.close()
