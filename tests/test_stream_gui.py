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


def settle(app):
    for _ in range(6): app.processEvents()


def timeline_sentence(window, uid, final=True, text=None):
    w, e, app = window
    job = SpeechJob(uid, 1, b'', 1, 2, 3, monotonic_ns(), final)
    text = text or f'Sentence {uid}: the next subtitle arrives at the bottom.'
    w.caption(e.book.asr(job, text))
    w.caption(e.book.translation(TextJob(job, text, monotonic_ns()), f'第 {uid} 条字幕出现在底部。'))
    settle(app)
    return job


def test_single_mode_has_one_frame_and_appends_new_speech_at_bottom(window):
    from PySide6.QtCore import QPoint
    w, e, app = window
    w.single_mode.setChecked(True); settle(app)
    assert w.timeline.isVisible() and not w.draft.isVisible() and not w.final.isVisible()
    assert not w.show_history.isEnabled()
    assert w.timeline.parentWidget() is w.subtitle_container
    assert w.timeline.geometry() == w.subtitle_container.rect()
    timeline_sentence(window, 1)
    first = w.timeline.cards[1]
    before = first.mapTo(w.timeline.scroll.viewport(), QPoint(0, 0)).y()
    timeline_sentence(window, 2, final=False)
    second = w.timeline.cards[2]
    assert list(w.timeline.cards) == [1, 2]
    assert first.mapTo(w.timeline.scroll.viewport(), QPoint(0, 0)).y() < before
    assert first.geometry().bottom() < second.geometry().top()
    content = w.timeline.scroll.widget()
    assert abs(second.geometry().bottom() - content.height() + 1) <= 2
    assert not second.en.visibleRegion().isEmpty() and not second.zh.visibleRegion().isEmpty()


def test_single_mode_updates_draft_to_final_in_place_and_keeps_order(window):
    w, e, app = window
    w.single_mode.setChecked(True)
    j = timeline_sentence(window, 1, final=False)
    card = w.timeline.cards[1]
    final = replace(j, revision=2, final=True)
    text = 'The first sentence is now complete.'
    w.caption(e.book.asr(final, text))
    # New speech can arrive while the previous final translation is still running.
    timeline_sentence(window, 2, final=False)
    w.caption(e.book.translation(TextJob(final, text, monotonic_ns()), '第一句已完成。'))
    settle(app)
    assert list(w.timeline.cards) == [1, 2] and w.timeline.cards[1] is card
    assert card.en.text() == text and card.zh.text() == '第一句已完成。'
    assert not card.badge.isVisible()
    assert card.geometry().bottom() < w.timeline.cards[2].geometry().top()
    assert not w.timeline.cards[2].badge.isVisible()
    row = dict(card.snapshot)
    w.caption({**row, 'text': 'Late incorrect overwrite', 'translated': '错误覆盖'})
    assert card.en.text() == text and card.zh.text() == '第一句已完成。'


def test_single_mode_follows_latest_until_user_scrolls_and_resumes_on_click(window):
    w, e, app = window
    w.resize(700, 420); w.single_mode.setChecked(True)
    for uid in range(1, 7): timeline_sentence(window, uid)
    bar = w.timeline.scroll.verticalScrollBar()
    assert bar.maximum() > 0 and bar.value() == bar.maximum()
    bar.setValue(0); settle(app)
    assert not w.timeline.follow_latest
    timeline_sentence(window, 7)
    assert bar.value() == 0, 'A new subtitle must not interrupt reading earlier subtitles'
    w.latest_action.trigger(); settle(app)
    assert w.timeline.follow_latest and bar.value() == bar.maximum()
    timeline_sentence(window, 8)
    assert bar.value() == bar.maximum()
    assert not w.timeline.scroll.verticalScrollBar().isVisible()


def test_mode_switch_retains_existing_content_without_duplicates(window):
    w, e, app = window
    timeline_sentence(window, 1)
    timeline_sentence(window, 2, final=False)
    w.show_history.setChecked(True)
    for _ in range(3):
        w.single_mode.setChecked(True); settle(app)
        assert list(w.timeline.cards) == [1, 2]
        assert w.timeline.cards[1].snapshot['zh_final']
        assert not w.timeline.cards[2].snapshot['final']
        assert w.history_insertions == 1
        w.single_mode.setChecked(False); settle(app)
        assert w.draft.isVisible() and w.final.isVisible() and not w.timeline.isVisible()
        assert w.scroll.isVisible() and w.show_history.isEnabled()
        assert not w.timeline.cards
        assert w.draft.snapshot['uid'] == 2 and w.final.snapshot['uid'] == 1


def test_single_mode_has_bounded_rows_and_never_resurrects_evicted_finals(window):
    w, e, app = window
    w.single_mode.setChecked(True)
    for uid in range(1, 26): timeline_sentence(window, uid)
    assert len(w.timeline.cards) == e.config.stream.history_limit + 4
    assert list(w.timeline.cards) == list(range(17, 26))
    latest = dict(w.timeline.cards[25].snapshot)
    w.caption({**latest, 'uid': 1})
    assert list(w.timeline.cards) == list(range(17, 26))
    assert w.timeline.body.count() == len(w.timeline.cards) + 1
    assert w.timeline.scroll.verticalScrollBar().value() == w.timeline.scroll.verticalScrollBar().maximum()


def test_single_mode_language_reset_clears_all_rows_and_keeps_mode(window):
    w, e, app = window
    w.single_mode.setChecked(True)
    timeline_sentence(window, 1)
    old = dict(w.timeline.cards[1].snapshot)
    e.source_language = 'ja'; e.language_generation = 1; e.book.clear()
    w._sync_language(); settle(app)
    assert not w.timeline.cards
    assert w.single_mode.isChecked() and w.timeline.isVisible()
    w.caption(old)
    assert not w.timeline.cards
    j = SpeechJob(2, 1, b'', 1, 2, 3, monotonic_ns(), False, source_language='ja', language_generation=1)
    w.caption(e.book.asr(j, '新しい字幕は下に表示されます。'))
    settle(app)
    assert list(w.timeline.cards) == [2] and '新しい' in w.timeline.cards[2].en.text()


@pytest.mark.parametrize('opacity', [0, 85])
def test_single_mode_resizes_and_preserves_background_only_opacity(window, opacity):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPalette
    w, e, app = window
    w.single_mode.setChecked(True); w.opacity.setValue(opacity)
    for uid in range(1, 4): timeline_sentence(window, uid)
    w.font_size.setValue(30)
    for width, height in ((700, 420), (1200, 900)):
        w.resize(width, height); settle(app)
        assert w.timeline.geometry() == w.subtitle_container.rect()
        assert w.timeline.scroll.viewport().width() < w.timeline.width()
        for card in w.timeline.cards.values():
            assert card.en.font().pointSize() == 30
            assert card.en.palette().color(QPalette.ColorRole.WindowText).name() == '#000000'
            assert card.en.wordWrap() and card.zh.wordWrap()
        # Both the outer frame and inner entries share the same one background layer.
        image = rendered_window(w)
        point = w.timeline.mapTo(w, QPoint(8, w.timeline.height() // 2))
        assert pixel_at(image, point).alpha() == round(255 * opacity / 100)
        assert w.windowOpacity() == 1
    w.theme.setCurrentIndex(2); settle(app)
    assert w.timeline.cards[3].en.palette().color(QPalette.ColorRole.WindowText).name() == '#f1f5f9'


def test_initial_single_mode_from_config(tmp_path, monkeypatch):
    from livetranslate.config import load_config
    from livetranslate.streaming.gui import SubtitleWindow
    from PySide6.QtWidgets import QApplication
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance() or QApplication([])
    p = tmp_path / 'single.toml'; p.write_text('[stream]\nsubtitle_mode="single"\n')
    e = SimpleNamespace(config=load_config(p), presentation='', bus=EventBus(), book=CaptionBook(),
                        metrics=SimpleNamespace(event=lambda *a, **k: None), presentation_details={},
                        finished=threading.Event(), presented=lambda *a: None, request_stop=lambda: None)
    w = SubtitleWindow(e)
    try:
        w.show(); settle(app)
        assert w.single_mode.isChecked() and w.timeline.isVisible()
        assert not w.draft.isVisible() and not w.final.isVisible()
        assert e.presentation_details['subtitle_mode'] == 'single'
    finally:
        w.timer.stop(); w.allow_close = True; w.close(); app.processEvents()


def test_caption_only_surface_has_no_visible_controls_status_or_placeholder(window):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLabel, QAbstractButton
    w, e, app = window
    w.single_mode.setChecked(True); settle(app)
    assert not w.toolbar.isVisible() and not w.resource_label.isVisible() and not w.size_grip.isVisible()
    assert not any(label.isVisible() for label in w.findChildren(QLabel))
    assert not any(button.isVisible() for button in w.findChildren(QAbstractButton))
    assert w.timeline.mapTo(w, QPoint(0, 0)) == QPoint(0, 0)
    assert w.timeline.size() == w.size()
    timeline_sentence(window, 1)
    w.caption(e.book.failure(1, 'A simulated error must not become a subtitle'))
    settle(app)
    card = w.timeline.cards[1]
    assert {label.text() for label in w.findChildren(QLabel) if label.isVisible()} == {card.en.text(), card.zh.text()}
    assert not card.badge.isVisible() and not card.notice.isVisible()
    w.resize(320, 140); settle(app)
    assert w.width() == 320 and w.height() == 140
    w.restore_action.trigger(); settle(app)
    assert not w.single_mode.isChecked() and w.toolbar.isVisible() and w.resource_label.isVisible()
    assert w.size_grip.isVisible() and w.width() >= 700 and w.height() >= 420


def send_pointer(widget, kind, position, button, buttons):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    event = QMouseEvent(kind, QPointF(widget.mapFromGlobal(position)), QPointF(position),
                        button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def test_long_press_on_new_subtitle_text_moves_window_and_release_stops(window, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtTest import QTest
    w, e, app = window
    w.single_mode.setChecked(True); timeline_sentence(window, 1)
    label = w.timeline.cards[1].en
    gestures = w.surface_gestures
    # Shortened timer keeps the test fast; production uses 350 ms.
    gestures.hold.setInterval(20)
    native_calls = []
    monkeypatch.setattr(type(w.windowHandle()), 'startSystemMove', lambda self: native_calls.append(True) or False)
    monkeypatch.setattr(type(w), 'grabMouse', lambda self: None)
    position = label.mapToGlobal(label.rect().center())
    before = w.pos()
    send_pointer(label, QEvent.Type.MouseButtonPress, position, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    assert gestures.hold.isActive() and not gestures.dragging and w.pos() == before
    QTest.qWait(40)
    assert gestures.dragging and gestures.manual and native_calls == [True]
    delta = QPoint(35, 20)
    send_pointer(label, QEvent.Type.MouseMove, position + delta, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    assert w.pos() == before + delta
    send_pointer(label, QEvent.Type.MouseButtonRelease, position + delta, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    assert not gestures.dragging and not gestures.pressed and not gestures.hold.isActive()
    send_pointer(label, QEvent.Type.MouseMove, position + 2*delta, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
    assert w.pos() == before + delta


def test_short_click_and_early_swipe_do_not_trigger_window_drag(window, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtTest import QTest
    w, e, app = window
    w.single_mode.setChecked(True); settle(app)
    surface = w.timeline.scroll.viewport()
    gesture = w.surface_gestures; gesture.hold.setInterval(20)
    calls = []
    monkeypatch.setattr(type(w.windowHandle()), 'startSystemMove', lambda self: calls.append(True) or True)
    position = surface.mapToGlobal(surface.rect().center())
    before = w.pos()
    send_pointer(surface, QEvent.Type.MouseButtonPress, position, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    send_pointer(surface, QEvent.Type.MouseButtonRelease, position, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    QTest.qWait(35)
    assert not calls and w.pos() == before
    send_pointer(surface, QEvent.Type.MouseButtonPress, position, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    send_pointer(surface, QEvent.Type.MouseMove, position + QPoint(40, 0), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    QTest.qWait(35)
    assert not calls and w.pos() == before
    send_pointer(surface, QEvent.Type.MouseButtonRelease, position, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)


def test_native_move_and_mode_change_cancel_pending_hold(window, monkeypatch):
    from PySide6.QtCore import QEvent, Qt
    w, e, app = window
    w.single_mode.setChecked(True); settle(app)
    surface = w.timeline.scroll.viewport(); g = w.surface_gestures
    position = surface.mapToGlobal(surface.rect().center())
    native = []
    monkeypatch.setattr(type(w.windowHandle()), 'startSystemMove', lambda self: native.append(True) or True)
    monkeypatch.setattr(type(w), 'grabMouse', lambda self: pytest.fail('Native move must not grab mouse a second time'))
    send_pointer(surface, QEvent.Type.MouseButtonPress, position, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    g.hold.stop(); g.begin_move()
    assert native == [True] and g.dragging and not g.manual
    w.restore_action.trigger(); settle(app)
    assert not g.pressed and not g.hold.isActive() and not g.dragging
    g.begin_move()
    assert native == [True]


def test_caption_right_click_menu_and_escape_restore_controls(window):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtTest import QTest
    w, e, app = window
    w.single_mode.setChecked(True); timeline_sentence(window, 1)
    label = w.timeline.cards[1].en; point = label.rect().center()
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, label.mapToGlobal(point))
    app.sendEvent(label, event); settle(app)
    assert w.subtitle_menu.isVisible()
    assert w.restore_action in w.subtitle_menu.actions()
    w.subtitle_menu.hide(); w.restore_action.trigger(); settle(app)
    assert w.toolbar.isVisible() and not w.single_mode.isChecked()
    w.single_mode.setChecked(True); w.activateWindow(); settle(app)
    QTest.keyClick(w, Qt.Key.Key_Escape); settle(app)
    assert not w.single_mode.isChecked() and w.toolbar.isVisible()


def test_x11_long_press_and_border_resize_use_real_window_geometry(window, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint, Qt
    w, e, app = window
    w.single_mode.setChecked(True); w.resize(500, 220); settle(app)
    g = w.surface_gestures; g.platform = 'xcb'
    monkeypatch.setattr(type(w), 'grabMouse', lambda self: None)
    monkeypatch.setattr(type(w.windowHandle()), 'startSystemMove', lambda self: pytest.fail('X11 must not rely on a compositor move acknowledgement'))
    monkeypatch.setattr(type(w.windowHandle()), 'startSystemResize', lambda self, edges: pytest.fail('X11 resize uses direct geometry'))
    point = w.mapToGlobal(QPoint(200, 80)); before = w.pos()
    send_pointer(w.timeline, QEvent.Type.MouseButtonPress, point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    g.hold.stop(); g.begin_move()
    send_pointer(w, QEvent.Type.MouseMove, point + QPoint(25, 15), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    assert w.pos() == before + QPoint(25, 15)
    send_pointer(w, QEvent.Type.MouseButtonRelease, point, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    edge = w.mapToGlobal(QPoint(w.width()-2, w.height()-2)); before = w.size()
    send_pointer(w.timeline, QEvent.Type.MouseButtonPress, edge, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    assert g.resize_edges and not g.hold.isActive()
    send_pointer(w, QEvent.Type.MouseMove, edge + QPoint(40, 30), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    assert w.width() == before.width() + 40 and w.height() == before.height() + 30
    send_pointer(w, QEvent.Type.MouseMove, edge - QPoint(1000, 1000), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    assert w.width() == 240 and w.height() == 100
    send_pointer(w, QEvent.Type.MouseButtonRelease, edge, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    assert not g.resize_edges and not g.pressed
