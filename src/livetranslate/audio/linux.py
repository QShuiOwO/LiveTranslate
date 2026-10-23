"""PipeWire/PulseAudio monitor capture; never falls back to a microphone."""

from dataclasses import dataclass
import json
import math
import os
import re
from pathlib import Path
import tempfile
from time import perf_counter

from ..config import AudioConfig
from ..errors import LiveTranslateError
from ..process import run_command
from ..results import CaptureResult
from .wav import inspect_wav


@dataclass(frozen=True)
class MonitorSource:
    name: str
    description: str
    sink: str | None
    state: str
    is_default: bool
    connected: bool


@dataclass(frozen=True)
class BluetoothDeviceState:
    connected: bool
    alias: str


def bluetooth_device_state(path: str) -> BluetoothDeviceState | None:
    """Read BlueZ directly: PipeWire may retain stale Bluetooth device properties."""
    if not isinstance(path, str) or not re.fullmatch(r"/org/bluez/hci\d+/dev_(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2}", path):
        return None
    try:
        raw = json.loads(run_command([
            "busctl", "--system", "--json=short", "call", "org.bluez", path,
            "org.freedesktop.DBus.Properties", "GetAll", "s", "org.bluez.Device1",
        ], timeout=2).stdout)
        if not isinstance(raw, dict) or raw.get("type") != "a{sv}": return None
        data = raw.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict): return None
        connected, alias = data[0].get("Connected"), data[0].get("Alias", {})
        if not isinstance(connected, dict) or connected.get("type") != "b" or not isinstance(connected.get("data"), bool):
            return None
        name = alias.get("data", "") if isinstance(alias, dict) else ""
        return BluetoothDeviceState(connected["data"], name if isinstance(name, str) else "")
    except (LiveTranslateError, json.JSONDecodeError):
        # Missing busctl/BlueZ or a transient D-Bus failure preserves the pactl result.
        return None


def parse_monitors(sources: list[dict], sinks: list[dict], default_sink: str,
                   bluetooth_states: dict[str, BluetoothDeviceState] | None = None) -> list[MonitorSource]:
    if (not isinstance(sources, list) or not isinstance(sinks, list)
            or any(not isinstance(d, dict) or not isinstance(d.get("properties", {}), dict) for d in [*sources, *sinks])):
        raise LiveTranslateError("audio_devices", "pactl returned an invalid device list")
    by_monitor = {}
    by_sink = {str(s.get("index")): s for s in sinks}
    by_sink.update({s.get("name"): s for s in sinks})
    for sink in sinks:
        monitor = sink.get("monitor_source")
        if isinstance(monitor, str) and monitor:
            by_monitor[monitor] = sink
    result = []
    for source in sources:
        name = source.get("name", "")
        props = source.get("properties", {})
        sink_ref = source.get("monitor_of_sink")
        valid_ref = not isinstance(sink_ref, bool) and sink_ref is not None and str(sink_ref) not in {"", "-1", "4294967295", "n/a"}
        sink = by_monitor.get(name)
        if sink is None and valid_ref:
            sink = by_sink.get(str(sink_ref))
        if not name or not (name in by_monitor or props.get("device.class") == "monitor" or sink is not None):
            continue
        # A .monitor suffix alone is not proof that this is an output monitor.
        sink_name = sink.get("name") if sink else None
        sink_props = (sink or {}).get("properties", {})
        connection = sink_props.get("api.bluez5.connection")
        device = (bluetooth_states or {}).get(sink_props.get("api.bluez5.path"))
        connected = (device.connected if device else connection != "disconnected")
        connected = connected and source.get("state", "").upper() != "UNAVAILABLE"
        active = (sink or {}).get("active_port")
        ports = (sink or {}).get("ports", [])
        if any(p.get("name") == active and p.get("availability") in {"not available", "unavailable", "no"} for p in ports):
            connected = False
        description = f"Monitor of {device.alias}" if device and device.alias else source.get("description", "")
        result.append(MonitorSource(name, description, sink_name,
                                    source.get("state", "UNKNOWN"), sink_name == default_sink, connected))
    return result


def select_monitor(monitors: list[MonitorSource], explicit: str | None = None) -> MonitorSource:
    if explicit:
        chosen = next((m for m in monitors if m.name == explicit), None)
        if chosen is None:
            raise LiveTranslateError("audio_source_missing", f"Not an available system monitor: {explicit}")
    else:
        chosen = next((m for m in monitors if m.is_default), None)
        if chosen is None:
            available = [m for m in monitors if m.connected]
            if len(available) == 1:
                chosen = available[0]
            elif not available:
                raise LiveTranslateError("audio_no_monitor", "No connected output monitor found; check your PipeWire session")
            else:
                raise LiveTranslateError("audio_source_ambiguous", "Default output has no monitor; specify --source from the monitor list")
    if not chosen.connected:
        raise LiveTranslateError("audio_device_disconnected", f"Output device is disconnected: {chosen.name}")
    return chosen


class LinuxAudioCapture:
    def __init__(self, config: AudioConfig):
        self.config = config

    def enumerate_monitors(self) -> list[MonitorSource]:
        try:
            default = run_command([self.config.pactl, "get-default-sink"], timeout=8).stdout.strip()
            sources = json.loads(run_command([self.config.pactl, "--format=json", "list", "sources"], timeout=8).stdout)
            sinks = json.loads(run_command([self.config.pactl, "--format=json", "list", "sinks"], timeout=8).stdout)
            # Validate pactl structures before inspecting device properties.
            monitors = parse_monitors(sources, sinks, default)
            paths = {path for s in sinks if isinstance(path := s.get("properties", {}).get("api.bluez5.path"), str)}
            states = {path: state for path in paths if path and (state := bluetooth_device_state(path)) is not None}
            return parse_monitors(sources, sinks, default, states) if states else monitors
        except json.JSONDecodeError as exc:
            raise LiveTranslateError("audio_devices", f"Invalid pactl JSON: {exc}") from exc

    def capture(self, duration: float, output: Path, source: str | None = None) -> CaptureResult:
        start = perf_counter()
        result = CaptureResult(audio_file=str(output))
        temporary = None
        try:
            if not math.isfinite(duration) or not 10 <= duration <= 30:
                raise LiveTranslateError("audio_duration", "Capture duration must be between 10 and 30 seconds")
            monitor = select_monitor(self.enumerate_monitors(), source or self.config.source or None)
            result.source_device = monitor.name
            output = Path(output).resolve()
            result.audio_file = str(output)
            if output.exists():
                raise LiveTranslateError("audio_output_exists", f"Refusing to overwrite existing recording: {output}")
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".wav", delete=False) as f:
                temporary = Path(f.name)
            run_command([
                self.config.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-y",
                "-f", "pulse", "-i", monitor.name, "-t", str(duration), "-vn",
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", str(temporary),
            ], timeout=duration + 15)
            result.audio = inspect_wav(temporary, self.config.silence_dbfs)
            if abs(result.audio.duration_ms - duration * 1000) > max(1000, duration * 100):
                raise LiveTranslateError("audio_short_capture", "Captured audio is shorter than requested; device may have disconnected")
            # Re-enumerate after capture to catch a device disappearing mid-recording.
            select_monitor(self.enumerate_monitors(), monitor.name)
            # Hard-link gives no-overwrite semantics even if another capture won a race.
            os.link(temporary, output)
            result.success = True
        except LiveTranslateError as exc:
            result.error, result.error_code = str(exc), exc.code
        except OSError as exc:
            result.error, result.error_code = str(exc), "audio_io"
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            result.elapsed_ms = (perf_counter() - start) * 1000
        return result
