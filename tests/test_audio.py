from dataclasses import replace
import wave
import pytest

from conftest import write_wav
from livetranslate.audio.linux import LinuxAudioCapture, MonitorSource, parse_monitors, select_monitor
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
