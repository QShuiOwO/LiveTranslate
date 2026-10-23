"""Native Qt presentation; model workers never touch widgets."""
from collections import OrderedDict
from pathlib import Path
import json
import signal
import sys
import os
from time import monotonic_ns

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QPushButton, QScrollArea, QSlider,
                               QSpinBox, QVBoxLayout, QWidget)


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


class CaptionCard(QFrame):
    def __init__(self, engine, draft=False):
        super().__init__()
        self.setObjectName("draftCard" if draft else "finalCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        self.badge = QLabel("识别中 · 临时" if draft else "已确认")
        self.badge.setObjectName("badge")
        self.en = PaintedLabel(engine.presented, "en")
        self.zh = PaintedLabel(engine.presented, "zh")
        self.zh.setObjectName("chinese")
        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        layout.addWidget(self.badge); layout.addWidget(self.en); layout.addWidget(self.zh); layout.addWidget(self.notice)
        self.snapshot = None

    def apply(self, row):
        self.snapshot = row
        self.en.caption(row, row["text"])
        self.zh.caption(row, row["translated"])
        self.badge.setText("已确认 · 双语" if row["zh_final"] else ("英文已确认 · 翻译中" if row["final"] else "识别中 · 临时"))
        self.notice.setText(row.get("error") or "")
        self.notice.setVisible(bool(row.get("error")))


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
        self.inhibitor = None
        self._visibility = None
        self.stop_deadline_ns = monotonic_ns() + int(duration * 1e9) if duration else None
        self.setWindowTitle("LiveTranslate · 实时双语字幕")
        self.resize(980, 660)
        self.setMinimumSize(700, 440)
        main = QWidget(); self.setCentralWidget(main)
        layout = QVBoxLayout(main); layout.setContentsMargins(20, 16, 20, 16); layout.setSpacing(10)
        header = QHBoxLayout()
        brand = QLabel("LiveTranslate"); brand.setObjectName("brand")
        local = QLabel("系统播放音频  ·  本地推理"); local.setObjectName("muted")
        self.stop_button = QPushButton("停止监听")
        self.stop_button.clicked.connect(lambda: self.request_stop("button"))
        header.addWidget(brand); header.addWidget(local); header.addStretch(); header.addWidget(self.stop_button)
        layout.addLayout(header)
        self.status_label = QLabel("正在加载本地模型…"); self.status_label.setWordWrap(True)
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)
        self.draft = CaptionCard(engine, True)
        self.draft.en.setText("等待系统音频中的英语语音…")
        self.draft.setMinimumHeight(115)
        self.final = CaptionCard(engine)
        self.final.en.setText("已确认的字幕会保留在这里")
        self.final.setMinimumHeight(130)
        layout.addWidget(self.draft); layout.addWidget(self.final)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("字号"))
        self.font_size = QSpinBox(); self.font_size.setRange(14, 36); self.font_size.setValue(engine.config.stream.font_size)
        controls.addWidget(self.font_size)
        controls.addWidget(QLabel("透明度"))
        self.opacity = QSlider(Qt.Orientation.Horizontal); self.opacity.setRange(40, 100)
        self.opacity.setMaximumWidth(100); self.opacity.setValue(int(engine.config.stream.opacity * 100))
        if QApplication.instance().platformName().startswith("wayland"):
            self.opacity.setEnabled(False)
            self.opacity.setToolTip("当前 Wayland 合成器不支持窗口透明度；可调整主题与字号")
        controls.addWidget(self.opacity)
        self.theme = QComboBox(); self.theme.addItems(["深色", "浅色"])
        self.theme.setCurrentIndex(0 if engine.config.stream.theme == "dark" else 1)
        controls.addWidget(self.theme)
        self.on_top = QCheckBox("置顶"); self.on_top.setChecked(True)
        self.show_history = QCheckBox("字幕历史")
        controls.addWidget(self.on_top); controls.addWidget(self.show_history); controls.addStretch()
        layout.addLayout(controls)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(130)
        history_widget = QWidget(); self.history_layout = QVBoxLayout(history_widget)
        self.history_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(history_widget); self.scroll.setVisible(False)
        layout.addWidget(self.scroll, 1)
        self.resource_label = QLabel("采集与推理异步运行 · 识别中的字幕可能变化，已确认英文不会改写")
        self.resource_label.setObjectName("muted"); self.resource_label.setWordWrap(True)
        layout.addWidget(self.resource_label)
        layout.addStretch()
        self.font_size.valueChanged.connect(self.appearance)
        self.opacity.valueChanged.connect(self.appearance)
        self.theme.currentIndexChanged.connect(self.appearance)
        self.on_top.toggled.connect(self._top)
        self.show_history.toggled.connect(self.scroll.setVisible)
        self.appearance()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.timer = QTimer(self); self.timer.setInterval(33); self.timer.timeout.connect(self.poll); self.timer.start()
        self._old_sigint = signal.signal(signal.SIGINT, lambda *args: self.request_stop("SIGINT"))
        self._old_sigterm = signal.signal(signal.SIGTERM, lambda *args: self.request_stop("SIGTERM"))

    def _top(self, enabled):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()

    def appearance(self, *args):
        dark = self.theme.currentIndex() == 0
        background, card, text, muted = ("#111827", "#1e293b", "#f1f5f9", "#94a3b8") if dark else ("#eef2f6", "#ffffff", "#172033", "#596579")
        self.setStyleSheet(f"""
        QMainWindow, QWidget {{ background: {background}; color: {text}; }}
        QFrame#draftCard, QFrame#finalCard {{ background: {card}; border-radius: 12px; }}
        QFrame#draftCard QLabel, QFrame#finalCard QLabel {{ background: transparent; }}
        QLabel#brand {{ font-size: 24px; font-weight: 700; }}
        QLabel#badge, QLabel#muted {{ color: {muted}; font-size: 12px; }}
        QLabel#status {{ color: #60a5fa; font-size: 13px; }}
        QLabel#notice {{ color: #f59e0b; font-size: 13px; }}
        QLabel#chinese {{ font-weight: 600; }}
        QPushButton {{ border: 1px solid {muted}; border-radius: 6px; padding: 7px 12px; }}
        QScrollArea {{ border: none; }}
        """)
        size = self.font_size.value()
        for card_widget in [self.draft, self.final, *self.history.values()]:
            card_widget.en.setFont(QFont("Noto Sans CJK SC", size))
            card_widget.zh.setFont(QFont("Noto Sans CJK SC", size + 1))
        if self.opacity.isEnabled(): self.setWindowOpacity(self.opacity.value() / 100)

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
        uid = row["uid"]
        previous = self.rows.get(uid)
        if previous is None and row["final"] and uid <= self.latest_final: return
        if previous and previous["final"]:
            if not row["final"] or previous["text"] != row["text"]: return
            if previous["zh_final"] and previous["translated"] != row["translated"]: return
        self.rows[uid] = row
        while len(self.rows) > self.engine.config.stream.history_limit + 4: self.rows.popitem(last=False)
        if not row["final"]:
            if uid >= self.latest_draft and uid > self.latest_final:
                self.latest_draft = uid; self.draft.apply(row)
        else:
            if uid >= self.latest_final:
                self.latest_final = uid; self.final.apply(row)
                if uid >= self.latest_draft:
                    self.draft.en.snapshot = self.draft.zh.snapshot = None
                    self.draft.en.setText("继续监听…"); self.draft.zh.setText("")
            if uid not in self.history:
                card = CaptionCard(self.engine)
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
            card.en.setFont(QFont("Noto Sans CJK SC", size)); card.zh.setFont(QFont("Noto Sans CJK SC", size+1))

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
