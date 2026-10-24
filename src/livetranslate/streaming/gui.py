"""Native Qt presentation; model workers never touch widgets."""
from collections import OrderedDict
from pathlib import Path
import json
import signal
import sys
import os
from time import monotonic_ns

from ..errors import LiveTranslateError
from ..languages import LANGUAGES, validate_asr_language

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QFont, QPainter, QPen, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QPushButton, QScrollArea, QSlider,
                               QSizeGrip, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)


class ScreenInhibitor:
    """Keep captions visible using an owned, temporary desktop idle inhibitor."""
    def __init__(self):
        self.active = False
        self.connection_name = None
        if sys.platform != "linux": return
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
            self.connection_name = f"livetranslate-idle-{os.getpid()}"
            connection = QDBusConnection.connectToBus(QDBusConnection.BusType.SessionBus, self.connection_name)
            interface = QDBusInterface("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
                                       "org.freedesktop.ScreenSaver", connection)
            reply = interface.call("Inhibit", "LiveTranslate", "Continuous system-audio subtitles")
            self.active = reply.type() == QDBusMessage.MessageType.ReplyMessage
        except (ImportError, RuntimeError): pass

    def close(self):
        if self.connection_name:
            from PySide6.QtDBus import QDBusConnection
            # Inhibitors are scoped to the sender's connection and released on disconnect.
            QDBusConnection.disconnectFromBus(self.connection_name)
            self.connection_name = None


class PaintedLabel(QLabel):
    def __init__(self, callback, field):
        super().__init__()
        self.callback, self.field, self.snapshot = callback, field, None
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def caption(self, snapshot, text):
        self.snapshot = dict(snapshot)
        if self.text() != text: self.setText(text)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        window = self.window()
        handle = window.windowHandle()
        if (self.snapshot and self.text() and window.isVisible() and not window.isMinimized()
                and handle and handle.isExposed()
                and not window.property("screenshot_render")):
            self.callback(self.snapshot, self.field, monotonic_ns())


class WindowHandle(QLabel):
    """Move a frameless window with the compositor; keep a fallback for X11."""
    def __init__(self):
        super().__init__("LiveTranslate")
        self.setObjectName("brand")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("拖动移动窗口；双击放大或还原")
        self.offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if not handle or not handle.startSystemMove():
                self.offset = event.globalPosition().toPoint() - self.window().pos()
            event.accept()
        else: super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self.offset)
        else: super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().showNormal() if self.window().isMaximized() else self.window().showMaximized()
            event.accept()
        else: super().mouseDoubleClickEvent(event)


class ResizeGrip(QSizeGrip):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.palette().color(QPalette.ColorRole.WindowText), 1.2))
        for length in (5, 10, 15):
            painter.drawLine(self.width() - length - 3, self.height() - 4,
                             self.width() - 4, self.height() - length - 3)


def transparent_scroll():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setAutoFillBackground(False)
    scroll.viewport().setAutoFillBackground(False)
    return scroll


class CaptionCard(QFrame):
    def __init__(self, engine, draft=False, history=False):
        super().__init__()
        self.setObjectName("historyCard" if history else ("draftCard" if draft else "finalCard"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 10, 2, 16) if history else layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(10)
        self.badge = QLabel("正在识别 · 实时双语" if draft else "识别完成 · 双语字幕")
        self.badge.setObjectName("badge")
        layout.addWidget(self.badge)
        content = QWidget()
        content.setAutoFillBackground(False)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.en = PaintedLabel(engine.presented, "en")
        self.zh = PaintedLabel(engine.presented, "zh")
        self.zh.setObjectName("chinese")
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        for label in (self.en, self.zh, self.notice):
            # Let height-for-width determine wrapping, rather than reserving the narrow size hint's height.
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            body.addWidget(label)
        if history:
            layout.addWidget(content)
        else:
            self.scroll = transparent_scroll()
            self.scroll.setWidget(content)
            content.setAutoFillBackground(False)
            layout.addWidget(self.scroll, 1)
            self.setMinimumHeight(112)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.snapshot = None

    def apply(self, row):
        self.snapshot = row
        self.en.caption(row, row["text"])
        self.zh.caption(row, row["translated"])
        self.zh.setVisible(bool(row["translated"]))
        self.badge.setText("处理暂不可用" if row.get("error") else ("识别完成 · 双语字幕" if row["zh_final"]
                           else ("原文已确认 · 翻译中" if row["final"] else "正在识别 · 实时双语")))
        self.notice.setText(row.get("error") or "")
        self.notice.setVisible(bool(row.get("error")))

    def clear_draft(self):
        self.snapshot = self.en.snapshot = self.zh.snapshot = None
        self.badge.setText("正在识别 · 实时双语")
        self.en.setText("继续监听系统播放音频…")
        self.zh.clear(); self.zh.hide(); self.notice.clear(); self.notice.hide()


class SubtitleWindow(QMainWindow):
    def __init__(self, engine, duration=None, screenshot=None):
        super().__init__()
        self.engine = engine
        # Destroy the Qt object tree in Qt's event loop, before QApplication teardown.
        # Signal/lambda cycles otherwise leave widgets to Python's final GC pass.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.engine.presentation = "qt_label_paint"
        self.screenshot = Path(screenshot) if screenshot else None
        self.screenshot_saved = False
        self.rows = OrderedDict()
        self.history = OrderedDict()
        self.latest_final = 0
        self.latest_draft = 0
        self.stopping = False
        self.allow_close = False
        self.history_insertions = 0
        self.language_generation = getattr(engine, "language_generation", 0)
        self.inhibitor = None
        self._visibility = None
        self.stop_deadline_ns = monotonic_ns() + int(duration * 1e9) if duration else None
        self.setWindowTitle("LiveTranslate · 实时双语字幕")
        self.setObjectName("subtitleWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(980, 620)
        self.setMinimumSize(700, 420)
        main = QWidget(); main.setObjectName("mainSurface"); self.setCentralWidget(main)
        layout = QVBoxLayout(main); layout.setContentsMargins(16, 12, 16, 10); layout.setSpacing(12)
        self.toolbar = QWidget()
        toolbar_layout = QVBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0); toolbar_layout.setSpacing(8)
        header = QHBoxLayout(); header.setSpacing(12)
        self.drag_handle = WindowHandle()
        self.status_label = QLabel("正在加载本地模型…")
        self.status_label.setObjectName("status"); self.status_label.setWordWrap(True)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.minimize_button = QPushButton("−"); self.minimize_button.setObjectName("windowButton")
        self.minimize_button.setFixedSize(28, 28); self.minimize_button.setToolTip("最小化")
        self.minimize_button.setAccessibleName("最小化窗口")
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button = QPushButton("×"); self.close_button.setObjectName("windowButton")
        self.close_button.setFixedSize(28, 28); self.close_button.setToolTip("关闭并释放资源")
        self.close_button.setAccessibleName("关闭字幕窗口")
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.drag_handle); header.addWidget(self.status_label, 1)
        self.language = QComboBox()
        self.language.setAccessibleName("识别与翻译语言")
        self.language.setToolTip("切换后从新音频开始识别，并清空当前字幕和历史")
        for code, name in LANGUAGES.items():
            self.language.addItem(f"{name} → 中文", code)
            try:
                validate_asr_language(engine.config.asr, code)
            except LiveTranslateError as exc:
                item = self.language.model().item(self.language.count() - 1)
                item.setEnabled(False)
                item.setToolTip(str(exc))
        self.language.setCurrentIndex(self.language.findData(engine.config.asr.language))
        header.addWidget(self.language)
        header.addWidget(self.minimize_button); header.addWidget(self.close_button)
        toolbar_layout.addLayout(header)
        controls = QHBoxLayout(); controls.setSpacing(8)
        controls.addWidget(QLabel("字号"))
        self.font_size = QSpinBox(); self.font_size.setRange(14, 36); self.font_size.setValue(engine.config.stream.font_size)
        self.font_size.setAccessibleName("字幕字号"); self.font_size.setFixedWidth(62)
        controls.addWidget(self.font_size)
        controls.addWidget(QLabel("背景"))
        self.opacity = QSlider(Qt.Orientation.Horizontal); self.opacity.setRange(0, 100)
        self.opacity.setFixedWidth(76); self.opacity.setValue(int(engine.config.stream.opacity * 100))
        self.opacity.setAccessibleName("背景不透明度")
        self.opacity.setToolTip("仅调整背景不透明度，文字和边框保持清晰；透明主题始终无填充")
        controls.addWidget(self.opacity)
        self.opacity_value = QLabel()
        self.opacity_value.setFixedWidth(36)
        self.opacity_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.opacity_value.setAccessibleName("背景不透明度百分比")
        controls.addWidget(self.opacity_value)
        self.theme = QComboBox(); self.theme.addItems(["透明黑字", "浅色", "深色"])
        self.theme.setCurrentIndex({"transparent": 0, "light": 1, "dark": 2}[engine.config.stream.theme])
        self.theme.setAccessibleName("字幕主题"); self.theme.setMinimumWidth(96)
        controls.addWidget(self.theme)
        self.on_top = QCheckBox("置顶"); self.on_top.setChecked(True)
        self.show_history = QCheckBox("字幕历史")
        controls.addWidget(self.on_top); controls.addWidget(self.show_history); controls.addStretch()
        self.stop_button = QPushButton("停止监听")
        self.stop_button.clicked.connect(lambda: self.request_stop("button"))
        controls.addWidget(self.stop_button)
        toolbar_layout.addLayout(controls)
        layout.addWidget(self.toolbar)
        self.subtitle_container = QWidget()
        self.subtitle_container.setObjectName("subtitleContainer")
        subtitles = QVBoxLayout(self.subtitle_container)
        subtitles.setContentsMargins(0, 0, 0, 0); subtitles.setSpacing(12)
        self.draft = CaptionCard(engine, True)
        self.draft.en.setText(f"等待系统音频中的{LANGUAGES[engine.config.asr.language]}语音…")
        self.draft.zh.hide()
        self.final = CaptionCard(engine)
        self.final.en.setText("完成的原文和中文译文将显示在这里")
        self.final.zh.hide()
        subtitles.addWidget(self.draft, 1); subtitles.addWidget(self.final, 1)
        layout.addWidget(self.subtitle_container, 1)
        self.scroll = transparent_scroll()
        history_widget = QWidget(); self.history_layout = QVBoxLayout(history_widget)
        self.history_layout.setContentsMargins(0, 0, 0, 0); self.history_layout.setSpacing(8)
        self.history_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(history_widget); self.scroll.setVisible(False)
        history_widget.setAutoFillBackground(False)
        self.follow_history = True
        self._adjusting_history = False
        self.scroll.verticalScrollBar().rangeChanged.connect(self._history_range)
        self.scroll.verticalScrollBar().valueChanged.connect(self._history_position)
        self.final.layout().addWidget(self.scroll, 1)
        self.resource_label = QLabel("上方实时更新 · 下方保留已完成双语字幕")
        self.resource_label.setObjectName("muted"); self.resource_label.setWordWrap(True)
        footer = QHBoxLayout()
        footer.addWidget(self.resource_label, 1)
        self.size_grip = ResizeGrip(self); self.size_grip.setFixedSize(22, 22)
        self.size_grip.setToolTip("拖动右下角调整窗口大小")
        self.size_grip.setAccessibleName("调整字幕窗口大小")
        footer.addWidget(self.size_grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        layout.addLayout(footer)
        self.font_size.valueChanged.connect(self.appearance)
        self.opacity.valueChanged.connect(self.appearance)
        self.theme.currentIndexChanged.connect(self.appearance)
        self.on_top.toggled.connect(self._top)
        self.show_history.toggled.connect(self._history)
        self.language.currentIndexChanged.connect(self._language_changed)
        self.appearance()
        self.timer = QTimer(self); self.timer.setInterval(33); self.timer.timeout.connect(self.poll); self.timer.start()
        self._old_sigint = signal.signal(signal.SIGINT, lambda *args: self.request_stop("SIGINT"))
        self._old_sigterm = signal.signal(signal.SIGTERM, lambda *args: self.request_stop("SIGTERM"))

    def _language_changed(self, index):
        try:
            self.engine.switch_language(self.language.itemData(index))
            self._sync_language()
        except LiveTranslateError as exc:
            self.language.blockSignals(True)
            self.language.setCurrentIndex(self.language.findData(self.engine.source_language))
            self.language.blockSignals(False)
            self.status_label.setText(str(exc))

    def _sync_language(self):
        generation = getattr(self.engine, "language_generation", 0)
        if generation == self.language_generation: return
        self.language_generation = generation
        language = self.engine.source_language
        self.language.blockSignals(True)
        self.language.setCurrentIndex(self.language.findData(language))
        self.language.blockSignals(False)
        self.rows.clear()
        for card in self.history.values():
            self.history_layout.removeWidget(card)
            card.hide(); card.deleteLater()
        self.history.clear()
        self.latest_draft = self.latest_final = 0
        self.follow_history = True
        self.draft.clear_draft(); self.final.clear_draft()
        self.draft.en.setText(f"等待系统音频中的{LANGUAGES[language]}语音…")
        self.final.badge.setText("识别完成 · 双语字幕")
        self.final.en.setText("完成的原文和中文译文将显示在这里")
        self.status_label.setText(f"● 已切换为{LANGUAGES[language]} → 中文，正在监听新音频")

    def _top(self, enabled):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()

    def _history(self, enabled):
        if enabled: self.follow_history = True
        self.final.scroll.setVisible(not enabled)
        self.scroll.setVisible(enabled)
        if enabled: self._history_bottom()

    def _history_bottom(self):
        self.follow_history = True
        bar = self.scroll.verticalScrollBar()
        self._adjusting_history = True
        try: bar.setValue(bar.maximum())
        finally: self._adjusting_history = False

    def _history_range(self, minimum, maximum):
        if self.follow_history: self._history_bottom()

    def _history_position(self, value):
        if not self._adjusting_history:
            self.follow_history = value >= self.scroll.verticalScrollBar().maximum() - 4

    def appearance(self, *args):
        transparent = self.theme.currentIndex() == 0
        dark = self.theme.currentIndex() == 2
        alpha = round(self.opacity.value() * 255 / 100)
        self.opacity.setEnabled(not transparent)
        self.opacity_value.setText("0%" if transparent else f"{self.opacity.value()}%")
        if transparent:
            background, text, border = "transparent", "#000000", "rgba(0, 0, 0, 110)"
        elif dark:
            background, text, border = f"rgba(17, 24, 39, {alpha})", "#f1f5f9", "#94a3b8"
        else:
            background, text, border = f"rgba(238, 242, 246, {alpha})", "#000000", "#596579"
        # Paint the background once so overlapping cards do not compound its opacity.
        self.setStyleSheet(f"""
        QWidget {{ background: transparent; color: {text}; }}
        QWidget#mainSurface {{ background: {background}; border-radius: 16px; }}
        QFrame#draftCard, QFrame#finalCard {{ background: transparent; border: 1px solid {border}; border-radius: 20px; }}
        QFrame#historyCard {{ background: transparent; border: none; border-bottom: 1px solid {border}; }}
        QLabel#brand {{ font-size: 15px; font-weight: 700; }}
        QLabel#badge {{ color: {text}; font-size: 12px; font-weight: 600; }}
        QLabel#muted, QLabel#status, QLabel#notice {{ color: {text}; font-size: 12px; }}
        QLabel#chinese {{ font-weight: 600; }}
        QPushButton, QSpinBox, QComboBox {{ border: 1px solid {border}; border-radius: 6px; padding: 5px 8px; }}
        QPushButton:hover, QComboBox:hover {{ border-color: {text}; }}
        QPushButton#windowButton {{ padding: 0; font-size: 18px; border: none; }}
        QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid {border}; border-radius: 3px; background: transparent; }}
        QCheckBox::indicator:checked {{ background: {text}; }}
        QComboBox QAbstractItemView {{ background: {"#1e293b" if dark else "#ffffff"}; color: {text}; selection-background-color: {"#334155" if dark else "#e2e8f0"}; }}
        QSlider::groove:horizontal {{ height: 3px; background: {border}; border-radius: 1px; }}
        QSlider::handle:horizontal {{ background: {text}; width: 10px; margin: -4px 0; border-radius: 5px; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{ background: transparent; width: 7px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {border}; border-radius: 3px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        size = self.font_size.value()
        for card_widget in [self.draft, self.final, *self.history.values()]:
            card_widget.en.setFont(QFont("Noto Sans CJK SC", size))
            card_widget.zh.setFont(QFont("Noto Sans CJK SC", size))

    def poll(self):
        handle = self.windowHandle()
        visibility = (self.isVisible(), bool(handle and handle.isExposed()), self.isMinimized())
        if visibility != self._visibility:
            self._visibility = visibility
            self.engine.presentation_details["last_visibility"] = dict(zip(("visible", "exposed", "minimized"), visibility))
            self.engine.metrics.event("window_visibility", **self.engine.presentation_details["last_visibility"])
        if self.stop_deadline_ns and monotonic_ns() >= self.stop_deadline_ns and not self.stopping:
            self.request_stop("duration")
        for event in self.engine.bus.drain():
            if event["kind"] == "caption": self.caption(event)
            elif event["kind"] == "language": self._sync_language()
            elif event["kind"] == "status":
                self.status_label.setText(self._status(event["code"], event["message"]))
            elif event["kind"] == "resources":
                data = event["data"]
                self.resource_label.setText(f"已确认 {self.engine.book.bilingual_count} 条双语字幕  ·  "
                                           f"应用内存 {data['app_rss_bytes']/2**20:.0f} MiB  ·  "
                                           f"队列丢弃 {sum(q.get('dropped',0) for q in data['queues'].values())}")
        if self.stopping and self.engine.finished.is_set():
            # Give final labels at least one event-loop paint before saving presentation metrics.
            self.allow_close = True
            QTimer.singleShot(150, self.close)
            self.timer.stop()

    @staticmethod
    def _status(code, message):
        if code in {"capture_ready", "live_ready", "service_ready"}: return "● 正在监听系统播放音频 · 本地识别与翻译"
        if code == "service_external": return "● 使用已有本地翻译服务"
        if code == "fatal": return "启动失败：" + message
        return "正在恢复：" + message

    def caption(self, row):
        self._sync_language()
        if row.get("language_generation", 0) != self.language_generation: return
        uid = row["uid"]
        previous = self.rows.get(uid)
        if previous is None and row["final"] and uid <= self.latest_final: return
        if previous and previous["final"]:
            if not row["final"] or previous["text"] != row["text"]: return
            if previous["zh_final"] and previous["translated"] != row["translated"]: return
        self.rows[uid] = row
        while len(self.rows) > self.engine.config.stream.history_limit + 4: self.rows.popitem(last=False)
        if not row["zh_final"]:
            if uid >= self.latest_draft and uid > self.latest_final:
                self.latest_draft = uid; self.draft.apply(row)
        else:
            if uid >= self.latest_final:
                self.latest_final = uid; self.final.apply(row)
                if uid >= self.latest_draft:
                    self.draft.clear_draft()
            if uid not in self.history:
                card = CaptionCard(self.engine, history=True)
                self.history[uid] = card; self.history_layout.addWidget(card)
                self.history_insertions += 1
                while len(self.history) > self.engine.config.stream.history_limit:
                    _, old = self.history.popitem(last=False)
                    self.history_layout.removeWidget(old); old.deleteLater()
            self.history[uid].apply(row)
            if row["zh_final"] and self.screenshot and not self.screenshot_saved:
                self.screenshot_saved = True
                QTimer.singleShot(150, self.save_screenshot)
        # Set font only on changed/new content; text updates do not rebuild the window.
        size = self.font_size.value()
        for card in ([self.history[uid]] if uid in self.history else []):
            card.en.setFont(QFont("Noto Sans CJK SC", size)); card.zh.setFont(QFont("Noto Sans CJK SC", size))

    def save_screenshot(self):
        if self.screenshot:
            self.screenshot.parent.mkdir(parents=True, exist_ok=True)
            self.engine.presentation_details["visible_at_screenshot"] = self.isVisible() and self.windowHandle().isExposed()
            self.setProperty("screenshot_render", True)
            try: self.grab().save(str(self.screenshot))
            finally: self.setProperty("screenshot_render", False)

    def request_stop(self, reason="user"):
        if self.stopping: return
        self.engine.metrics.event("stop_requested", reason=reason, deadline_ns=self.stop_deadline_ns,
                                  remaining_s=(self.stop_deadline_ns-monotonic_ns())/1e9 if self.stop_deadline_ns else None)
        self.stopping = True; self.stop_button.setEnabled(False)
        self.language.setEnabled(False)
        self.status_label.setText("正在结束当前字幕并释放音频与模型资源…")
        self.engine.request_stop()

    def closeEvent(self, event: QCloseEvent):
        if not self.allow_close:
            event.ignore(); self.request_stop("window_close"); return
        signal.signal(signal.SIGINT, self._old_sigint)
        signal.signal(signal.SIGTERM, self._old_sigterm)
        if self.inhibitor: self.inhibitor.close()
        self.engine.presentation_details.update(history_insertions=self.history_insertions,
                                                retained_history=len(self.history), retained_rows=len(self.rows))
        self.engine.metrics.event("gui_closed", history_insertions=self.history_insertions,
                                  retained_history=len(self.history), retained_rows=len(self.rows))
        event.accept()


def run_gui(engine, duration=None, screenshot=None):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("LiveTranslate")
    engine.presentation_details = {"qt_platform": app.platformName(),
                                   "screen_width": app.primaryScreen().size().width(),
                                   "screen_height": app.primaryScreen().size().height()}
    window = SubtitleWindow(engine, duration, screenshot)
    window.inhibitor = ScreenInhibitor()
    engine.presentation_details["temporary_idle_inhibit"] = window.inhibitor.active
    window.show(); window.raise_(); window.activateWindow(); engine.start()
    app.exec()
    report = engine.report()
    engine.metrics.close()
    return 1 if report["start_error"] or report["workers_alive"] else 0
