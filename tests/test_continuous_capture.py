import io
import subprocess
import threading

import pytest

from livetranslate.audio.continuous import LinuxContinuousCapture
from livetranslate.audio.linux import MonitorSource
from livetranslate.config import AudioConfig, Config
from livetranslate.errors import LiveTranslateError


def test_missing_monitor_retries_without_starting_microphone(monkeypatch):
    stop = threading.Event()
    statuses = []
    def status(code, message):
        statuses.append((code, message))
        stop.set()
    monkeypatch.setattr("livetranslate.audio.continuous.LinuxAudioCapture.enumerate_monitors", lambda self: [])
    monkeypatch.setattr("livetranslate.audio.continuous.subprocess.Popen",
                        lambda *a, **k: pytest.fail("No monitor must never become a microphone capture"))
    source = LinuxContinuousCapture(AudioConfig(), status=status)
    assert list(source.frames(stop)) == []
    assert source.reconnects == 1 and statuses[0][0] == "capture_reconnecting"
    assert "monitor" in statuses[0][1].lower()


def test_eof_reports_stderr_and_reaps_owned_ffmpeg(monkeypatch):
    stop = threading.Event()
    statuses, commands = [], []
    monitor = MonitorSource("hdmi.monitor", "hdmi", "HDMI", "RUNNING", True, True)
    monkeypatch.setattr("livetranslate.audio.continuous.LinuxAudioCapture.enumerate_monitors", lambda self: [monitor])
    class Process:
        stdout, stderr = io.BytesIO(), io.BytesIO()
        terminated = False
        waited = False
        def poll(self): return None if not self.terminated else 0
        def terminate(self): self.terminated = True
        def wait(self, **kwargs): self.waited = True; return 0
    process = Process()
    def launch(args, **kwargs):
        commands.append(args)
        return process
    monkeypatch.setattr("livetranslate.audio.continuous.subprocess.Popen", launch)
    class Selector:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def register(self, *args): pass
        def select(self, timeout):
            from types import SimpleNamespace
            return [(SimpleNamespace(data="stderr", fd=1), None), (SimpleNamespace(data="pcm", fd=2), None)]
    monkeypatch.setattr("livetranslate.audio.continuous.selectors.DefaultSelector", Selector)
    monkeypatch.setattr("livetranslate.audio.continuous.os.read", lambda fd, size: b"device disconnected" if fd == 1 else b"")
    def status(code, message):
        statuses.append((code, message))
        if code == "capture_reconnecting": stop.set()
    source = LinuxContinuousCapture(AudioConfig(), status=status)
    assert list(source.frames(stop)) == []
    assert process.terminated and process.waited and process.stdout.closed and process.stderr.closed
    assert "device disconnected" in statuses[-1][1]
    assert commands[0][commands[0].index("-i") + 1] == "hdmi.monitor"
    assert "-nostdin" in commands[0] and source.reconnects == 1


def test_stuck_capture_escalates_to_kill_and_wait():
    class Process:
        killed = False
        waited_after_kill = False
        def poll(self): return None
        def terminate(self): pass
        def kill(self): self.killed = True
        def wait(self, timeout=None):
            if timeout is not None: raise subprocess.TimeoutExpired("ffmpeg", timeout)
            self.waited_after_kill = True
    source = LinuxContinuousCapture(AudioConfig())
    source.process = process = Process()
    source.close()
    assert process.killed and process.waited_after_kill


def test_missing_live_extras_fail_before_creating_session(tmp_path, monkeypatch):
    import sys
    from livetranslate.streaming.runner import run_live
    monkeypatch.setitem(sys.modules, "psutil", None)
    session = tmp_path / "must-not-be-created"
    with pytest.raises(LiveTranslateError, match="Install live extras"):
        run_live(Config(), headless=True, output_dir=session)
    assert not session.exists()


def test_auto_source_detects_changed_system_output(monkeypatch):
    old = MonitorSource("old.monitor", "Old", "old", "RUNNING", False, True)
    new = MonitorSource("new.monitor", "New", "new", "RUNNING", True, True)
    monkeypatch.setattr("livetranslate.audio.continuous.LinuxAudioCapture.enumerate_monitors", lambda self: [old, new])
    with pytest.raises(LiveTranslateError, match="System output changed") as error:
        LinuxContinuousCapture(AudioConfig())._check_monitor(old)
    assert error.value.code == "audio_default_changed"


def test_explicit_monitor_stays_selected_when_system_default_changes(monkeypatch):
    old = MonitorSource("old.monitor", "Old", "old", "RUNNING", False, True)
    new = MonitorSource("new.monitor", "New", "new", "RUNNING", True, True)
    monkeypatch.setattr("livetranslate.audio.continuous.LinuxAudioCapture.enumerate_monitors", lambda self: [old, new])
    LinuxContinuousCapture(AudioConfig(source=old.name))._check_monitor(old)
