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
    assert w.final.isAncestorOf(w.scroll)
    assert abs(w.draft.height() - w.final.height()) <= 2
    w.theme.setCurrentIndex(2)
    app.processEvents()
    w.show_history.setChecked(False)
    app.processEvents()
    assert not w.scroll.isVisible()


def test_history_follows_new_captions_until_the_user_scrolls_up(window):
    w, e, app = window
    w.show_history.setChecked(True)

    def settle_layout():
        for _ in range(5):
            app.processEvents()

    def complete(uid):
        job = SpeechJob(uid, 1, b'', 1, 2, 3, monotonic_ns(), True)
        text = f"Sentence {uid} has finished speaking."
        w.caption(e.book.asr(job, text))
        w.caption(e.book.translation(TextJob(job, text, monotonic_ns()), f"第 {uid} 句已经说完。"))
        settle_layout()

    for uid in range(1, 5):
        complete(uid)
        bar = w.scroll.verticalScrollBar()
        assert bar.value() == bar.maximum()
    assert bar.maximum() > 0
    bar.setValue(0)
    settle_layout()
    previous_maximum = bar.maximum()
    complete(5)
    assert bar.maximum() > previous_maximum
    assert bar.value() == 0, "Reading earlier subtitles must not jump to the newest caption"
    w.show_history.setChecked(False)
    w.show_history.setChecked(True)
    settle_layout()
    assert bar.value() == bar.maximum()


def test_short_bilingual_captions_fit_default_regions_without_scrollbars(window):
    w, e, app = window
    w.resize(980, 620)
    for uid, final in ((1, True), (2, False)):
        job = SpeechJob(uid, 1, b'', 1, 2, 3, monotonic_ns(), final)
        text = "Artificial intelligence helps us understand the world and communicate across different languages."
        w.caption(e.book.asr(job, text))
        w.caption(e.book.translation(TextJob(job, text, monotonic_ns()), "人工智能帮助我们理解世界，并跨越语言交流。"))
    for _ in range(5):
        app.processEvents()
    for card in (w.draft, w.final):
        assert card.scroll.verticalScrollBar().maximum() == 0
        assert not card.scroll.verticalScrollBar().isVisible()
        assert not card.en.visibleRegion().isEmpty()
        assert not card.zh.visibleRegion().isEmpty()


def rendered_window(w):
    # Rendering for assertions must not affect actual presentation measurements.
    w.setProperty("screenshot_render", True)
    try:
        return w.grab().toImage()
    finally:
        w.setProperty("screenshot_render", False)


def pixel_at(image, point):
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(point.x() * ratio), round(point.y() * ratio))


def test_default_appearance_is_light_with_85_percent_background_and_16_point_text(window):
    from PySide6.QtGui import QPalette
    w, e, app = window
    assert w.theme.currentText() == "浅色"
    assert w.font_size.value() == 16
    assert w.opacity.value() == 85
    assert w.opacity.minimum() == 0 and w.opacity.maximum() == 100
    assert w.opacity.isEnabled()
    assert w.opacity_value.text() == "85%"
    assert w.windowOpacity() == 1
    for card in (w.draft, w.final):
        for label in (card.en, card.zh):
            assert label.font().pointSize() == 16
            assert label.palette().color(QPalette.ColorRole.WindowText).name() == "#000000"


@pytest.mark.parametrize("theme, background, foreground, border", [
    (1, (238, 242, 246), (0, 0, 0), (89, 101, 121)),
    (2, (17, 24, 39), (241, 245, 249), (148, 163, 184)),
])
@pytest.mark.parametrize("opacity", [0, 40, 85, 100])
def test_background_opacity_does_not_stack_or_fade_text_and_borders(
        window, theme, background, foreground, border, opacity):
    from PySide6.QtCore import QPoint
    w, e, app = window
    w.theme.setCurrentIndex(theme)
    w.opacity.setValue(opacity)
    w.draft.en.setText("MMMM Background opacity keeps subtitles readable.")
    for _ in range(3):
        app.processEvents()
    assert w.windowOpacity() == 1
    assert w.opacity_value.text() == f"{opacity}%"
    rendered = rendered_window(w)
    points = [QPoint(8, w.height() // 2), *(
        card.mapTo(w, QPoint(card.width() // 2, card.height() - 18))
        for card in (w.draft, w.final)
    )]
    for point in points:
        color = pixel_at(rendered, point)
        assert color.alpha() == round(255 * opacity / 100)
        if opacity:
            assert all(abs(actual - expected) <= 1
                       for actual, expected in zip(color.getRgb()[:3], background))
    # At zero background opacity the actual glyphs and straight border still paint
    # fully opaque; global window opacity would incorrectly fade both together.
    label_origin = w.draft.en.mapTo(w, QPoint(0, 0))
    solid_text_pixels = [
        pixel_at(rendered, label_origin + QPoint(x, y))
        for x in range(min(w.draft.en.width(), 100))
        for y in range(w.draft.en.height())
    ]
    assert any(color.getRgb() == (*foreground, 255) for color in solid_text_pixels)
    border_colors = [
        pixel_at(rendered, w.draft.mapTo(w, QPoint(x, w.draft.height() // 2)))
        for x in range(3)
    ]
    assert any(color.getRgb() == (*border, 255) for color in border_colors)
    job = SpeechJob(1, 1, b'', 1, 2, 3, monotonic_ns(), True)
    w.caption(e.book.asr(job, "A completed sentence."))
    w.caption(e.book.translation(TextJob(job, "A completed sentence.", monotonic_ns()), "一句完成的字幕。"))
    w.show_history.setChecked(True)
    for _ in range(3):
        app.processEvents()
    rendered = rendered_window(w)
    viewport = w.scroll.viewport()
    history_blank = viewport.mapTo(w, QPoint(viewport.width() // 2, viewport.height() - 12))
    assert pixel_at(rendered, history_blank).alpha() == round(255 * opacity / 100)
    history_card = w.history[1]
    history_padding = history_card.mapTo(w, QPoint(history_card.width() // 2, history_card.height() - 8))
    assert pixel_at(rendered, history_padding).alpha() == round(255 * opacity / 100)


def test_transparent_theme_has_transparent_fill_and_black_subtitle_text(window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QPalette
    w, e, app = window
    w.theme.setCurrentIndex(0)
    w.opacity.setValue(100)
    app.processEvents()
    assert w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert w.windowOpacity() == 1
    for card in (w.draft, w.final):
        for label in (card.en, card.zh):
            assert label.palette().color(QPalette.ColorRole.WindowText).name() == "#000000"
    # Inspect actual rendered alpha, not only a stylesheet containing "transparent".
    rendered = rendered_window(w)
    for point in [QPoint(1, 1), *(
        card.mapTo(w, QPoint(card.width() // 2, card.height() - 18))
        for card in (w.draft, w.final)
    )]:
        assert pixel_at(rendered, point).alpha() == 0


def test_top_controls_and_both_caption_regions_resize_with_the_container(window):
    from PySide6.QtCore import QPoint
    w, e, app = window
    assert w.draft.parentWidget() is w.subtitle_container
    assert w.final.parentWidget() is w.subtitle_container
    for control in (w.font_size, w.opacity, w.theme, w.on_top, w.show_history):
        assert w.toolbar.isAncestorOf(control)
    assert (w.toolbar.mapTo(w, QPoint(0, w.toolbar.height())).y()
            <= w.subtitle_container.mapTo(w, QPoint(0, 0)).y())
    measurements = []
    for width, height in ((800, 600), (1200, 900)):
        w.resize(width, height); app.processEvents()
        assert w.draft.width() == w.final.width()
        assert abs(w.draft.height() - w.final.height()) <= 2
        assert w.draft.geometry().bottom() < w.final.geometry().top()
        assert w.subtitle_container.rect().contains(w.draft.geometry())
        assert w.subtitle_container.rect().contains(w.final.geometry())
        measurements.append([(card.width(), card.height()) for card in (w.draft, w.final)])
    for before, after in zip(*measurements):
        assert after[0] > before[0] + 300
        assert after[1] > before[1] + 100


def test_long_bilingual_text_scrolls_in_each_region_without_expanding_window(window):
    from PySide6.QtWidgets import QScrollArea
    w, e, app = window
    w.resize(800, 600); w.font_size.setValue(36); app.processEvents()
    before = w.size()
    for uid, final in ((1, True), (2, False)):
        text = "Long spoken sentences should wrap and remain available when the window is small. " * 30
        job = SpeechJob(uid, 1, b'', 1, 2, 3, monotonic_ns(), final)
        w.caption(e.book.asr(job, text))
        w.caption(e.book.translation(TextJob(job, text, monotonic_ns()), "较长的中英文字幕应当可以换行并在区域内滚动。" * 30))
    app.processEvents()
    assert w.size() == before
    for card in (w.draft, w.final):
        scroll = card.findChild(QScrollArea)
        assert scroll is not None and scroll.widgetResizable()
        assert scroll.verticalScrollBar().maximum() > 0
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        assert scroll.verticalScrollBar().value() > 0
        assert card.en.wordWrap() and card.zh.wordWrap()


def test_current_bilingual_caption_and_completed_caption_remain_independent(window):
    w, e, app = window
    final_job = SpeechJob(1, 1, b'', 1, 2, 3, monotonic_ns(), True)
    w.caption(e.book.asr(final_job, "The first sentence is complete."))
    w.caption(e.book.translation(TextJob(final_job, "The first sentence is complete.", monotonic_ns()), "第一句已经完成。"))
    draft_job = SpeechJob(2, 1, b'', 1, 2, 3, monotonic_ns(), False)
    w.caption(e.book.asr(draft_job, "The next sentence is still being spoken."))
    w.caption(e.book.translation(TextJob(draft_job, "The next sentence is still being spoken.", monotonic_ns()), "下一句仍在说话中。"))
    assert w.draft.en.text() == "The next sentence is still being spoken."
    assert w.draft.zh.text() == "下一句仍在说话中。"
    assert w.final.en.text() == "The first sentence is complete."
    assert w.final.zh.text() == "第一句已经完成。"
    assert w.history_insertions == 1
    completed = replace(draft_job, revision=2, final=True)
    w.caption(e.book.asr(completed, "The next sentence is now complete."))
    assert w.draft.en.text() == "The next sentence is now complete."
    assert "翻译中" in w.draft.badge.text()
    assert w.final.en.text() == "The first sentence is complete."
    assert w.final.zh.text() == "第一句已经完成。"
    assert w.history_insertions == 1
    w.caption(e.book.translation(TextJob(completed, "The next sentence is now complete.", monotonic_ns()), "下一句现在已经完成。"))
    assert w.final.en.text() == "The next sentence is now complete."
    assert w.final.zh.text() == "下一句现在已经完成。"
    assert not w.draft.zh.text()
    assert w.history_insertions == 2


def test_finishing_previous_translation_does_not_clear_newer_current_caption(window):
    w, e, app = window
    previous = SpeechJob(1, 1, b'', 1, 2, 3, monotonic_ns(), True)
    w.caption(e.book.asr(previous, "The first sentence awaits translation."))
    current = SpeechJob(2, 1, b'', 1, 2, 3, monotonic_ns(), False)
    w.caption(e.book.asr(current, "A second sentence has already begun."))
    w.caption(e.book.translation(TextJob(current, "A second sentence has already begun.", monotonic_ns()), "第二句已经开始。"))
    w.caption(e.book.translation(TextJob(previous, "The first sentence awaits translation.", monotonic_ns()), "第一句等待翻译。"))
    assert w.final.en.text() == "The first sentence awaits translation."
    assert w.final.zh.text() == "第一句等待翻译。"
    assert w.draft.en.text() == "A second sentence has already begun."
    assert w.draft.zh.text() == "第二句已经开始。"


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
    w.caption(e.book.translation(TextJob(j, row["text"], monotonic_ns()), "真实显示测量需要可见窗口。"))
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
    row = e.book.asr(j, "A minimized window is not a displayed subtitle.")
    w.caption(row)
    w.caption(e.book.translation(TextJob(j, row["text"], monotonic_ns()), "最小化窗口不算已显示字幕。"))
    w.showMinimized(); app.processEvents(); observations.clear()
    assert w.isMinimized()
    w.grab(); app.processEvents()
    assert observations == []
    w.showNormal(); app.processEvents(); w.grab()
    assert observations


def test_language_control_resets_visible_history_and_rejects_queued_old_results(window):
    from livetranslate.config import ASRConfig
    w, e, app = window
    e.source_language = 'en'; e.language_generation = 0
    e.config = replace(e.config, asr=ASRConfig(engine='qwen3'))
    w.language.model().item(1).setEnabled(True)
    def switch(language):
        e.source_language = language; e.language_generation += 1; e.book.clear()
        e.bus.put({'kind': 'language', 'generation': e.language_generation, 'source_language': language})
        return e.language_generation
    e.switch_language = switch
    j = SpeechJob(1, 1, b'', 1, 2, 3, monotonic_ns(), True)
    old = e.book.asr(j, 'Old English caption.'); w.caption(old)
    w.caption(e.book.translation(TextJob(j, old['text'], monotonic_ns()), '旧字幕。'))
    assert w.history and w.rows
    w.language.setCurrentIndex(w.language.findData('ja'))
    assert e.source_language == 'ja' and not w.history and not w.rows
    assert '日语' in w.draft.en.text() and not w.final.zh.text()
    w.caption(old)
    assert not w.rows
    new = replace(j, uid=2, source_language='ja', language_generation=1)
    row = e.book.asr(new, '今日は晴れです。'); w.caption(row)
    w.poll()  # delayed switch event must not erase the new caption
    assert w.draft.en.text() == '今日は晴れです。'
    assert w.language.currentText() == '日语 → 中文'
    w.language.setCurrentIndex(0)
    assert e.source_language == 'en' and not w.rows
    assert w.opacity.value() == 85 and w.font_size.value() == 16


def test_english_only_whisper_disables_japanese_choice(window):
    w, e, app = window
    assert w.language.itemData(0) == 'en' and w.language.itemData(1) == 'ja'
    assert not w.language.model().item(1).isEnabled()
    assert 'Qwen3-ASR' in w.language.model().item(1).toolTip()
