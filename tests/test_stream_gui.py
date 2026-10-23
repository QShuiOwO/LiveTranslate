from dataclasses import replace
from types import SimpleNamespace
import threading
from time import monotonic_ns
import pytest

from livetranslate.config import Config
from livetranslate.streaming.state import CaptionBook, EventBus
from livetranslate.streaming.types import SpeechJob, TextJob


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from livetranslate.streaming.gui import SubtitleWindow
    app = QApplication.instance() or QApplication([])
    cfg = Config(); cfg = replace(cfg, stream=replace(cfg.stream, history_limit=5))
    e = SimpleNamespace(config=cfg, presentation="", bus=EventBus(), book=CaptionBook(5),
                        metrics=SimpleNamespace(event=lambda *a, **k: None), presentation_details={},
                        finished=threading.Event(), presented=lambda *a: None, request_stop=lambda: None)
    w = SubtitleWindow(e); w.show(); app.processEvents()
    yield w, e, app
    from shiboken6 import isValid
    if isValid(w):
        w.timer.stop(); w.allow_close = True; w.close(); app.processEvents()


def test_history_does_not_duplicate_rows_and_final_is_immutable(window):
    w, e, app = window
    for uid in range(1, 21):
        job = SpeechJob(uid, 1, b'', 1, 2, 3, monotonic_ns(), True)
        row = e.book.asr(job, f"Original {uid}"); w.caption(row)
        row = e.book.translation(TextJob(job, f"Original {uid}", monotonic_ns()), f"中文 {uid}")
        w.caption(row)
    assert w.history_insertions == 20 and len(w.history) == 5
    assert w.final.en.text() == "Original 20" and w.final.zh.text() == "中文 20"
    w.caption({**row, "text": "late overwrite"})
    assert w.final.en.text() == "Original 20"
    w.caption({**row, "uid": 1, "text": "An evicted final"})
    assert w.history_insertions == 20
    assert len(w.rows) <= 9 and w.final.en.wordWrap() and w.final.zh.wordWrap()


def test_appearance_and_history_controls(window):
    w, e, app = window
    w.font_size.setValue(30); w.theme.setCurrentIndex(1); w.show_history.setChecked(True)
    app.processEvents()
    assert w.draft.en.font().pointSize() == 30 and w.scroll.isVisible()
    assert "#eef2f6" in w.styleSheet()


def test_screenshot_render_never_counts_as_a_display_measurement(window):
    w, e, app = window
    observations = []
    e.presented = lambda *args: observations.append(args)
    # Existing labels hold their original callback; replace it explicitly for this test.
    w.final.en.callback = e.presented
    w.final.zh.callback = e.presented
    j = SpeechJob(1, 1, b'', 1, 2, 3, monotonic_ns(), True)
    row = e.book.asr(j, "Real display measurements require a visible window.")
    w.caption(row)
    w.setProperty("screenshot_render", True)
    w.grab(); app.processEvents()
    assert observations == []
    w.setProperty("screenshot_render", False)


def test_closed_window_tree_dies_before_application_teardown(window):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid
    w, e, app = window
    children = [w.draft.en, w.final.zh, w.timer]
    w.allow_close = True
    w.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(w) and all(not isValid(child) for child in children)


def test_minimized_window_never_counts_as_display_even_if_platform_is_exposed(window):
    w, e, app = window
    observations = []
    w.final.en.callback = lambda *args: observations.append(args)
    j = SpeechJob(1, 1, b'', 1, 2, 3, monotonic_ns(), True)
    w.caption(e.book.asr(j, "A minimized window is not a displayed subtitle."))
    w.showMinimized(); app.processEvents(); observations.clear()
    assert w.isMinimized()
    w.grab(); app.processEvents()
    assert observations == []
    w.showNormal(); app.processEvents(); w.grab()
    assert observations
