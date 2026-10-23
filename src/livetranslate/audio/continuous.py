"""Linux monitor -> bounded PCM frames. No microphone or default-device mutation."""
from collections import deque
import os
import selectors
import subprocess
import threading
from time import monotonic, monotonic_ns

from .linux import LinuxAudioCapture, select_monitor
from ..errors import LiveTranslateError
from ..streaming.types import AudioFrame, FRAME_BYTES


class LinuxContinuousCapture:
    def __init__(self, config, source=None, status=lambda *args: None):
        self.config = config
        self.source = source or config.source or None
        self.status = status
        self.process = None
        self.source_name = None
        self.reconnects = 0
        self.frames_read = 0
        self.last_frame_ns = None
        self.lock = threading.Lock()

    def close(self):
        with self.lock:
            p = self.process
            if p and p.poll() is None:
                p.terminate()
        if p:
            try: p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()

    def frames(self, stop):
        epoch, sequence, backoff = 0, 0, 0.5
        while not stop.is_set():
            p = None
            try:
                monitor = select_monitor(LinuxAudioCapture(self.config).enumerate_monitors(), self.source)
                self.source_name = monitor.name
                args = [self.config.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning",
                        "-f", "pulse", "-sample_rate", "16000", "-channels", "1", "-fragment_size", "640",
                        "-i", monitor.name, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                        "-flush_packets", "1", "-f", "s16le", "pipe:1"]
                env = {**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}
                p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, env=env)
                with self.lock: self.process = p
                epoch += 1
                pending = bytearray()
                errors = deque(maxlen=8)
                last_read = monotonic()
                next_check = monotonic() + 2
                self.status("capture_ready", monitor.name)
                with selectors.DefaultSelector() as selector:
                    selector.register(p.stdout, selectors.EVENT_READ, "pcm")
                    selector.register(p.stderr, selectors.EVENT_READ, "stderr")
                    while not stop.is_set():
                        for key, _ in selector.select(0.1):
                            data = os.read(key.fd, 8192)
                            if key.data == "stderr":
                                if data: errors.append(data.decode("utf-8", errors="replace"))
                                else: selector.unregister(key.fileobj)
                                continue
                            if not data:
                                raise LiveTranslateError("capture_eof", "FFmpeg audio pipe closed: " + "".join(errors)[-1500:])
                            received = monotonic_ns()
                            pending.extend(data)
                            last_read = monotonic()
                            backoff = 0.5
                            # Stamp each 20ms frame at PCM ingress; backdate frames in the same read.
                            complete = len(pending) // FRAME_BYTES
                            for i in range(complete):
                                pcm = bytes(pending[:FRAME_BYTES]); del pending[:FRAME_BYTES]
                                end = received - (complete - i - 1) * 20_000_000
                                sequence += 1
                                self.frames_read += 1
                                self.last_frame_ns = end
                                yield AudioFrame(pcm, sequence, end - 20_000_000, end, epoch)
                        if monotonic() - last_read > 3:
                            raise LiveTranslateError("capture_stalled", "No PCM frames for 3 seconds")
                        if monotonic() >= next_check:
                            select_monitor(LinuxAudioCapture(self.config).enumerate_monitors(), monitor.name)
                            next_check = monotonic() + 2
                        if p.poll() is not None:
                            raise LiveTranslateError("capture_exited", f"FFmpeg exited {p.returncode}: {''.join(errors)[-1500:]}")
            except (LiveTranslateError, OSError) as exc:
                if not stop.is_set():
                    self.reconnects += 1
                    self.status("capture_reconnecting", str(exc))
            finally:
                self.close()
                if p:
                    p.stdout.close(); p.stderr.close()
                with self.lock: self.process = None
            if not stop.is_set():
                stop.wait(backoff)
                backoff = min(5, backoff * 2)
