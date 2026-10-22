from dataclasses import asdict
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile

from .audio.linux import LinuxAudioCapture, select_monitor
from .config import Config
from .errors import LiveTranslateError
from .process import run_command
from .telemetry import gpu_snapshot


def audit(config: Config, hip_smoke: bool = False) -> dict:
    checks = []

    def command(name, args, *, required=True, env=None, timeout=20):
        item = {"name": name, "required": required, "command": args, "success": False}
        try:
            p = run_command(args, timeout=timeout, check=False, env=env)
            item.update(success=p.returncode == 0, returncode=p.returncode,
                        stdout=p.stdout[:64000], stderr=p.stderr[:8000], elapsed_ms=p.elapsed_ms)
            if p.returncode:
                item["error"] = (p.stderr or p.stdout)[-3000:] or f"exit code {p.returncode}"
        except LiveTranslateError as exc:
            item.update(error=str(exc), error_code=exc.code)
        checks.append(item)
        return item

    def read(name, path):
        try:
            value = Path(path).read_text().strip()
            checks.append({"name": name, "required": True, "success": True, "stdout": value})
        except OSError as exc:
            checks.append({"name": name, "required": True, "success": False, "error": str(exc)})

    read("os_release", "/etc/os-release")
    command("kernel", ["uname", "-srvm"])
    command("cpu", ["lscpu"])
    read("memory", "/proc/meminfo")
    command("pci_gpu_driver", ["lspci", "-nnk", "-d", "1002:"])
    headless = dict(os.environ)
    headless.pop("DISPLAY", None)
    headless.pop("WAYLAND_DISPLAY", None)
    command("vulkan_devices", ["vulkaninfo", "--summary"], env=headless, timeout=40)
    command("rocm_version", ["/opt/rocm/bin/hipconfig", "--version"], required=False)
    command("rocm_agents", ["/opt/rocm/bin/rocminfo"], required=False)
    command("rocm_smi", ["/opt/rocm/bin/rocm-smi", "--showproductname", "--showmeminfo", "vram", "--json"], required=False)
    command("pipewire", ["pipewire", "--version"])
    command("pipewire_pulse", ["pipewire-pulse", "--version"])
    command("audio_services", ["systemctl", "--user", "is-active", "pipewire", "pipewire-pulse", "wireplumber"])
    command("pactl_version", [config.audio.pactl, "--version"])
    command("default_sink", [config.audio.pactl, "get-default-sink"])
    command("default_source", [config.audio.pactl, "get-default-source"])
    try:
        monitors = LinuxAudioCapture(config.audio).enumerate_monitors()
        checks.append({"name": "monitors", "required": True, "success": bool(monitors),
                       "devices": [asdict(m) for m in monitors], "stdout": str(len(monitors)) + " system monitors",
                       "error": None if monitors else "No monitor source is available"})
        try:
            chosen = select_monitor(monitors, config.audio.source or None)
            checks.append({"name": "selected_monitor", "required": True, "success": True, "stdout": chosen.name})
        except LiveTranslateError as exc:
            checks.append({"name": "selected_monitor", "required": True, "success": False, "error": str(exc)})
    except LiveTranslateError as exc:
        checks.append({"name": "monitors", "required": True, "success": False, "error": str(exc)})
    command("ffmpeg_version", [config.audio.ffmpeg, "-version"])
    ffmpeg = command("ffmpeg_devices", [config.audio.ffmpeg, "-hide_banner", "-devices"])
    if ffmpeg["success"] and not re.search(r"^\s*D[E. ]*\s+pulse\s", ffmpeg["stdout"] + ffmpeg["stderr"], re.M):
        ffmpeg.update(success=False, error="FFmpeg was not built with PulseAudio input support")
    command("python", [sys.executable, "--version"])
    command("cmake", ["cmake", "--version"])
    command("compiler", ["c++", "--version"])
    command("git", ["git", "--version"])
    command("glslc", ["glslc", "--version"])
    for name, path in [("whisper_binary", config.asr.binary), ("whisper_model", config.asr.model),
                       ("llama_binary", config.server.binary), ("llama_model", config.server.model)]:
        p = Path(path)
        exists = p.is_file() or ("binary" in name and shutil.which(path) is not None)
        checks.append({"name": name, "required": True, "success": exists,
                       "stdout": f"{path}: {p.stat().st_size if p.is_file() else 'PATH'} bytes" if exists else "",
                       "error": None if exists else f"Not found: {path}"})
    if hip_smoke:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="livetranslate-hip-") as tmp:
            binary = str(Path(tmp) / "hip-smoke")
            compile_result = command("hip_probe_build", ["/opt/rocm/bin/hipcc", "--offload-arch=gfx1201", "-O2",
                                                         str(root / "scripts/hip_smoke.cpp"), "-o", binary], required=False, timeout=120)
            if compile_result["success"]:
                command("hip_compute", [binary], required=False, timeout=30)
    required_ok = all(c["success"] for c in checks if c["required"])
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
            "environment_ready": required_ok, "inference_verified": False,
            "inference_note": "Device enumeration and library probes do not verify Whisper/LLM GPU inference; run real validation separately.",
            "gpu_snapshot": gpu_snapshot(), "checks": checks}


def redact(value: str) -> str:
    value = value.replace(str(Path.home()), "~")
    return re.sub(r"(?:[0-9A-Fa-f]{2}[_:]){5}[0-9A-Fa-f]{2}", "<device-id>", value)


def markdown(report: dict) -> str:
    lines = ["# M0 环境审计", "", f"生成时间（UTC）：{report['generated_at']}", "",
             f"环境就绪：{report['environment_ready']}。此报告不代表模型已实际使用 GPU 推理。", "",
             "机器路径与蓝牙地址已脱敏。完整动态 JSON 保存在被 Git 忽略的 artifacts/。", ""]
    for check in report["checks"]:
        status = "PASS" if check["success"] else ("FAIL" if check["required"] else "OPTIONAL FAIL")
        lines += [f"## {check['name']} — {status}", "", "```text"]
        detail = check.get("stdout", "") + "\n" + check.get("stderr", "")
        if check.get("devices"):
            detail += "\n" + "\n".join(str(d) for d in check["devices"])
        if check.get("error"):
            detail += "\n" + check["error"]
        lines += [redact(detail.strip()) or "No output", "```", ""]
    lines += ["## GPU sysfs 快照", "", "```text", str(report["gpu_snapshot"]), "```", "",
              "这是整张设备的显存快照，包含桌面与其他进程。真实推理日志、峰值显存和耗时见 benchmarks.md。", ""]
    return "\n".join(lines)
