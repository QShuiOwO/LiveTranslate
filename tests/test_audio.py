from dataclasses import replace
import json
import wave
import pytest

from conftest import write_wav
from livetranslate.audio.linux import (BluetoothDeviceState, LinuxAudioCapture, MonitorSource,
                                      bluetooth_device_state, parse_monitors, select_monitor)
from livetranslate.audio.wav import inspect_wav
from livetranslate.config import AudioConfig
from livetranslate.errors import LiveTranslateError
from livetranslate.process import ProcessResult


def monitor(name="speaker.monitor", default=True, connected=True):
    return MonitorSource(name, "speaker", "speaker", "SUSPENDED", default, connected)


def test_pipewire_monitor_never_selects_microphone():
    sources = [{"name": "mic", "monitor_of_sink": 4294967295},
               {"name": "fake.monitor"},
               {"name": "speaker.monitor", "properties": {"device.class": "monitor"}}]
    sinks = [{"name": "speaker", "index": 3, "monitor_source": "speaker.monitor"}]
    parsed = parse_monitors(sources, sinks, "speaker")
    assert [m.name for m in parsed] == ["speaker.monitor"]
    assert select_monitor(parsed).name == "speaker.monitor"
    with pytest.raises(LiveTranslateError):
        select_monitor(parsed, "mic")


def test_pulseaudio_numeric_monitor_of_sink():
    parsed = parse_monitors([{"name": "loop", "monitor_of_sink": 7}], [{"name": "out", "index": 7}], "out")
    assert parsed[0].is_default


def test_invalid_monitor_reference_and_unplugged_port():
    assert parse_monitors([{"name": "mic", "monitor_of_sink": "made-up"}], [], "out") == []
    sinks = [{"name": "out", "index": 7, "active_port": "hdmi", "ports": [{"name": "hdmi", "availability": "not available"}]}]
    parsed = parse_monitors([{"name": "loop", "monitor_of_sink": 7}], sinks, "out")
    assert not parsed[0].connected


@pytest.mark.parametrize("devices,explicit,code", [
    ([], None, "audio_no_monitor"), ([monitor(connected=False)], None, "audio_device_disconnected"),
    ([monitor()], "gone.monitor", "audio_source_missing"),
    ([monitor("a", False), monitor("b", False)], None, "audio_source_ambiguous"),
])
def test_device_failure_reasons(devices, explicit, code):
    with pytest.raises(LiveTranslateError) as error:
        select_monitor(devices, explicit)
    assert error.value.code == code


def test_audio_metadata(audio):
    info = inspect_wav(audio)
    assert info.duration_ms == 250
    assert info.channels == 1 and info.sample_rate == 16000
    assert -20 < info.rms_dbfs < -5


@pytest.mark.parametrize("kind,code", [("silent", "audio_silent"), ("empty", "audio_empty"),
                                      ("stereo", "audio_format"), ("bad", "audio_invalid"),
                                      ("truncated", "audio_truncated")])
def test_bad_audio_rejected(tmp_path, kind, code):
    p = tmp_path / "bad.wav"
    if kind == "bad":
        p.write_bytes(b"garbage")
    else:
        write_wav(p, seconds=0 if kind == "empty" else 0.25,
                  silent=kind == "silent", channels=2 if kind == "stereo" else 1)
        if kind == "truncated":
            p.write_bytes(p.read_bytes()[:-100])
    with pytest.raises(LiveTranslateError) as error:
        inspect_wav(p)
    assert error.value.code == code


def test_capture_no_overwrite_and_failed_process(tmp_path, monkeypatch):
    capture = LinuxAudioCapture(AudioConfig())
    monkeypatch.setattr(capture, "enumerate_monitors", lambda: [monitor()])
    p = tmp_path / "existing.wav"
    p.write_bytes(b"keep me")
    result = capture.capture(10, p)
    assert result.error_code == "audio_output_exists" and p.read_bytes() == b"keep me"
    def fail(*args, **kwargs):
        raise LiveTranslateError("process_failed", "device disconnected; ffmpeg exit 1")
    monkeypatch.setattr("livetranslate.audio.linux.run_command", fail)
    result = capture.capture(10, tmp_path / "new.wav")
    assert not result.success and "disconnected" in result.error
    assert not (tmp_path / "new.wav").exists()


def test_device_removed_during_capture(tmp_path, monkeypatch):
    capture = LinuxAudioCapture(AudioConfig())
    device_lists = iter([[monitor()], []])
    monkeypatch.setattr(capture, "enumerate_monitors", lambda: next(device_lists))
    def record(args, **kwargs):
        write_wav(__import__("pathlib").Path(args[-1]), seconds=10)
        return ProcessResult(0, "", "", 10000)
    monkeypatch.setattr("livetranslate.audio.linux.run_command", record)
    result = capture.capture(10, tmp_path / "gone.wav")
    assert result.error_code == "audio_source_missing"
    assert not (tmp_path / "gone.wav").exists()


def test_pactl_json_failure(monkeypatch):
    monkeypatch.setattr("livetranslate.audio.linux.run_command", lambda *a, **kw: ProcessResult(0, "not json", "", 1))
    with pytest.raises(LiveTranslateError) as error:
        LinuxAudioCapture(AudioConfig()).enumerate_monitors()
    assert error.value.code == "audio_devices"


BLUETOOTH_PATH = "/org/bluez/hci0/dev_00_11_22_33_44_55"


@pytest.mark.parametrize("connected,pipewire", [(True, "disconnected"), (False, "connected")])
def test_bluez_connection_overrides_stale_pipewire_metadata(connected, pipewire):
    sinks = [{"name": "bt", "monitor_source": "bt.monitor", "properties": {
        "api.bluez5.path": BLUETOOTH_PATH, "api.bluez5.connection": pipewire}}]
    sources = [{"name": "bt.monitor", "state": "SUSPENDED", "description": "(null)"}]
    rows = parse_monitors(sources, sinks, "bt", {BLUETOOTH_PATH: BluetoothDeviceState(connected, "Test headset")})
    assert rows[0].connected is connected and rows[0].is_default
    assert rows[0].description == "Monitor of Test headset"
    if connected: assert select_monitor(rows).name == "bt.monitor"
    else:
        with pytest.raises(LiveTranslateError, match="disconnected"): select_monitor(rows)


@pytest.mark.parametrize("connected", [True, False])
def test_bluez_query_reads_typed_connection_and_unicode_alias(monkeypatch, connected):
    calls = []
    payload = {"type": "a{sv}", "data": [{"Connected": {"type": "b", "data": connected},
                                              "Alias": {"type": "s", "data": "测试耳机"}}]}
    def query(args, **kwargs):
        calls.append((args, kwargs))
        return ProcessResult(0, json.dumps(payload), "", 1)
    monkeypatch.setattr("livetranslate.audio.linux.run_command", query)
    assert bluetooth_device_state(BLUETOOTH_PATH) == BluetoothDeviceState(connected, "测试耳机")
    assert calls[0][0][-3:] == ["GetAll", "s", "org.bluez.Device1"]
    assert calls[0][1]["timeout"] == 2


@pytest.mark.parametrize("raw", ['not json', '[]', '{"type":"a{sv}","data":[]}',
                                  '{"type":"a{sv}","data":[{"Connected":{"type":"b","data":"true"}}]}'])
def test_invalid_bluez_response_preserves_pactl_fallback(monkeypatch, raw):
    monkeypatch.setattr("livetranslate.audio.linux.run_command", lambda *a, **k: ProcessResult(0, raw, "", 1))
    assert bluetooth_device_state(BLUETOOTH_PATH) is None


def test_missing_bluez_or_busctl_is_optional(monkeypatch):
    def fail(*args, **kwargs): raise LiveTranslateError("executable_missing", "busctl unavailable")
    monkeypatch.setattr("livetranslate.audio.linux.run_command", fail)
    assert bluetooth_device_state(BLUETOOTH_PATH) is None


def test_bluez_query_rejects_unexpected_object_paths(monkeypatch):
    monkeypatch.setattr("livetranslate.audio.linux.run_command", lambda *a, **k: pytest.fail("Unexpected D-Bus query"))
    assert bluetooth_device_state("/org/another/service") is None


def test_enumeration_refreshes_bluetooth_before_monitor_selection(monkeypatch):
    sinks = [{"name": "bt", "monitor_source": "bt.monitor", "properties": {
        "api.bluez5.path": BLUETOOTH_PATH, "api.bluez5.connection": "disconnected"}}]
    def query(args, **kwargs):
        value = "bt" if args[-1] == "get-default-sink" else json.dumps(sinks if args[-1] == "sinks" else [{"name": "bt.monitor"}])
        return ProcessResult(0, value, "", 1)
    monkeypatch.setattr("livetranslate.audio.linux.run_command", query)
    monkeypatch.setattr("livetranslate.audio.linux.bluetooth_device_state", lambda path: BluetoothDeviceState(True, "Headset"))
    rows = LinuxAudioCapture(AudioConfig()).enumerate_monitors()
    assert select_monitor(rows).connected


@pytest.mark.parametrize("source_state,sink_state,availability,bluez,expected", [
    ("RUNNING", "RUNNING", "available", None, True),
    ("RUNNING", "RUNNING", "yes", None, True),
    ("RUNNING", "RUNNING", "available", False, False),
    ("RUNNING", "RUNNING", "unavailable", None, False),
    ("RUNNING", "RUNNING", "unknown", None, False),
    ("SUSPENDED", "RUNNING", "available", None, False),
    ("RUNNING", "SUSPENDED", "available", None, False),
    ("UNAVAILABLE", "RUNNING", "available", None, False),
])
def test_active_monitor_fallback_when_bluez_is_unavailable(source_state, sink_state, availability, bluez, expected):
    sinks = [{"name": "bt", "monitor_source": "bt.monitor", "state": sink_state,
              "active_port": "headset", "ports": [{"name": "headset", "availability": availability}],
              "properties": {"api.bluez5.path": BLUETOOTH_PATH, "api.bluez5.connection": "disconnected"}}]
    sources = [{"name": "bt.monitor", "state": source_state}]
    states = {} if bluez is None else {BLUETOOTH_PATH: BluetoothDeviceState(bluez, "Headset")}
    rows = parse_monitors(sources, sinks, "bt", states)
    assert rows[0].connected is expected
