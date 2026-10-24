"""Window dragging in both subtitle modes without stealing control interactions."""
from PySide6.QtCore import QEvent, QObject, QRect, QTimer, Qt
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSlider,
                              QAbstractSpinBox, QApplication, QComboBox, QLineEdit,
                              QMenu, QSizeGrip, QWidget)


class SubtitleGestures(QObject):
    HOLD_MS = 350
    EDGE_PIXELS = 6

    def __init__(self, window, single_mode, show_menu):
        super().__init__(window)
        self.window = window
        self.single_mode, self.show_menu = single_mode, show_menu
        self.pressed = False
        self.dragging = False
        self.manual = False
        self.resize_edges = Qt.Edge(0)
        self.resize_geometry = None
        self.platform = QApplication.platformName()
        self.origin = self.latest = self.offset = None
        self.hold = QTimer(self)
        self.hold.setSingleShot(True)
        self.hold.setInterval(self.HOLD_MS)
        self.hold.timeout.connect(self.begin_move)
        # Filtering the application's events covers newly appended labels too.
        # Qt removes this filter when its window-owned QObject is destroyed.
        QApplication.instance().installEventFilter(self)

    def cancel(self):
        self.hold.stop()
        self.pressed = self.dragging = self.manual = False
        self.resize_edges = Qt.Edge(0)
        self.resize_geometry = None
        self.origin = self.latest = self.offset = None
        if QWidget.mouseGrabber() is self.window:
            self.window.releaseMouse()
        self.window.unsetCursor()

    def begin_move(self):
        if not self.pressed or self.window.stopping or not self.window.isVisible(): return
        self.dragging = True
        self.offset = self.origin - self.window.pos()
        self.window.setCursor(Qt.CursorShape.ClosedHandCursor)
        handle = self.window.windowHandle()
        # XWayland may return success without the compositor moving a window
        # after a held press. X11 permits direct positioning and pointer capture.
        self.manual = self.platform == "xcb" or not (handle and handle.startSystemMove())
        if self.manual:
            self.window.grabMouse()

    def resize(self, position):
        delta = position - self.origin
        rect = QRect(self.resize_geometry)
        if self.resize_edges & Qt.Edge.LeftEdge:
            rect.setLeft(min(rect.left() + delta.x(), rect.right() - self.window.minimumWidth() + 1))
        if self.resize_edges & Qt.Edge.RightEdge:
            rect.setRight(max(rect.right() + delta.x(), rect.left() + self.window.minimumWidth() - 1))
        if self.resize_edges & Qt.Edge.TopEdge:
            rect.setTop(min(rect.top() + delta.y(), rect.bottom() - self.window.minimumHeight() + 1))
        if self.resize_edges & Qt.Edge.BottomEdge:
            rect.setBottom(max(rect.bottom() + delta.y(), rect.top() + self.window.minimumHeight() - 1))
        self.window.setGeometry(rect)

    def _edges(self, point):
        local = self.window.mapFromGlobal(point)
        edges = Qt.Edge(0)
        if local.x() < self.EDGE_PIXELS: edges |= Qt.Edge.LeftEdge
        elif local.x() >= self.window.width() - self.EDGE_PIXELS: edges |= Qt.Edge.RightEdge
        if local.y() < self.EDGE_PIXELS: edges |= Qt.Edge.TopEdge
        elif local.y() >= self.window.height() - self.EDGE_PIXELS: edges |= Qt.Edge.BottomEdge
        return edges

    def _interactive(self, widget):
        while widget is not None and widget is not self.window:
            if isinstance(widget, (QAbstractButton, QAbstractSlider, QAbstractSpinBox,
                                   QComboBox, QLineEdit, QAbstractItemView, QMenu, QSizeGrip)):
                return True
            widget = widget.parentWidget()
        return False

    def _control_at(self, watched, position):
        # An ignored mouse event can bubble from a control to its parent. Check
        # both the receiver and the actual hit target, including disabled controls.
        target = self.window.childAt(self.window.mapFromGlobal(position))
        return self._interactive(watched) or self._interactive(target)

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.window and kind in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
            self.cancel()
            return False
        if kind not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove,
                        QEvent.Type.MouseButtonRelease, QEvent.Type.ContextMenu, QEvent.Type.Wheel):
            return False
        if self.window.stopping or not isinstance(watched, QWidget): return False
        if watched.window() is not self.window:
            return False
        if kind == QEvent.Type.ContextMenu:
            if not self.single_mode() or self._control_at(watched, event.globalPos()): return False
            self.cancel()
            self.show_menu(event.globalPos())
            return True
        if kind == QEvent.Type.Wheel:
            self.cancel()
            return False  # QScrollArea keeps wheel/trackpad history scrolling.
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.cancel()
            position = event.globalPosition().toPoint()
            if self._control_at(watched, position): return False
            handle = self.window.windowHandle()
            edges = self._edges(position)
            if edges and self.platform == "xcb":
                self.pressed = True
                self.origin = self.latest = position
                self.resize_edges = edges
                self.resize_geometry = self.window.geometry()
                self.window.grabMouse()
                return True
            if edges:
                # Window edges are exclusively for resizing, even if the
                # compositor cannot handle a resize request on this platform.
                return bool(handle and handle.startSystemResize(edges))
            self.origin = self.latest = position
            self.pressed = True
            self.hold.start()
            return True
        if kind == QEvent.Type.MouseMove and self.pressed:
            if not event.buttons() & Qt.MouseButton.LeftButton:
                self.cancel()
                return False
            self.latest = event.globalPosition().toPoint()
            if self.resize_edges:
                self.resize(self.latest)
            else:
                if not self.dragging and (self.latest - self.origin).manhattanLength() >= QApplication.startDragDistance():
                    self.hold.stop()
                    self.begin_move()
                if self.dragging and self.manual:
                    self.window.move(self.latest - self.offset)
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            pressed = self.pressed
            self.cancel()
            return pressed
        return False
