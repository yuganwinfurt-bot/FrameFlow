"""Controls and image surfaces used by the media preview."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSlider,
                               QStyle, QStyleOptionSlider, QVBoxLayout)

from . import icons
from .motion import WheelSlider, hide_vertical_scroll_bar


class TransportButton(QPushButton):
    """播放器控制按钮，使用单独的深色外观和清晰的无障碍名称。"""

    def __init__(self, icon_name: str, title: str, callback, *, primary: bool = False):
        super().__init__()
        self.setIcon(icons.icon(icon_name, 18, "#FFFFFF", 2))
        self.setIconSize(QSize(18, 18))
        self.setAutoDefault(False)
        self.setProperty("transport", True)
        self.setProperty("transportPrimary", primary)
        self.setAccessibleName(title)
        self.setToolTip(title)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 32)
        self.clicked.connect(callback)


class SeekSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            control = QStyle.ComplexControl.CC_Slider
            handle = self.style().subControlRect(control, option, QStyle.SubControl.SC_SliderHandle, self)
            if not handle.contains(event.position().toPoint()):
                groove = self.style().subControlRect(control, option, QStyle.SubControl.SC_SliderGroove, self)
                self.setValue(QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(),
                    round(event.position().x()) - groove.left() - handle.width() // 2,
                    max(1, groove.width() - handle.width()), option.upsideDown))
                event.accept()
                return
        super().mousePressEvent(event)


class FrameNumberEdit(QLineEdit):
    """直接输入帧号，回车或离开输入框时确认。"""

    valueChanged = Signal(int)

    def __init__(self):
        super().__init__("1")
        self.setObjectName("frameField")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setValidator(QIntValidator(1, 2_147_483_647, self))
        self._minimum = self._maximum = self._value = 1
        self.returnPressed.connect(self._commit)

    def setRange(self, minimum, maximum):
        self._minimum, self._maximum = minimum, maximum
        self.setValue(self._value)

    def value(self):
        return self._value

    def setValue(self, value):
        value = min(self._maximum, max(self._minimum, value))
        self.setText(str(value))
        if value != self._value:
            self._value = value
            self.valueChanged.emit(value)

    def _commit(self):
        text = self.text()
        self.setValue(int(text) if text.isdecimal() else self._value)

    def focusOutEvent(self, event):
        self._commit()
        super().focusOutEvent(event)


class PreviewLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.original = None

    def setText(self, text):
        self.original = None
        super().setText(text)

    def set_image(self, data):
        picture = QPixmap()
        if data and picture.loadFromData(data):
            self.set_picture(picture)
        else:
            self.setText("暂无预览")

    def set_picture(self, picture):
        self.original = picture
        self._fit()

    def _fit(self):
        if self.original is not None:
            self.setPixmap(self.original.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def sizeHint(self):
        return QSize(480, 300)

    def minimumSizeHint(self):
        return QSize(0, 120)


class InfoChip(QLabel):
    """媒体信息里的一格字：按内容排版，但最小宽度为 0，窄窗口时可以被压缩。"""

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())


class RunStatus(QPlainTextEdit):
    setText = QPlainTextEdit.setPlainText
    text = QPlainTextEdit.toPlainText

    def __init__(self):
        super().__init__()
        self.setObjectName("runStatus")
        self.setAccessibleName("运行信息")
        self.setReadOnly(True)
        self.setTabChangesFocus(True)
        self.setPlaceholderText("等待操作")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(self)
        self.setMinimumHeight(42)

    def sizeHint(self):
        return QSize(0, 56)


class PictureView(PreviewLabel):
    """图片查看器：滚轮以光标为中心缩放、放大后可拖动、双击复位。"""

    MIN_SCALE = 0.25
    MAX_SCALE = 6.0

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._scale = 1.0          # 相对“适应窗口”的倍率
        self._offset = QPointF(0, 0)
        self._drag_from = None
        self._drag_start = None

    # 基础：适应窗口时的缩放比
    def _fit_scale(self) -> float:
        if self.original is None or self.original.isNull():
            return 1.0
        area = self.size()
        if area.width() <= 0 or area.height() <= 0:
            return 1.0
        return min(area.width() / self.original.width(), area.height() / self.original.height())

    def _paint(self) -> None:
        if self.original is None or self.original.isNull():
            return
        base = self._fit_scale()
        scale = max(self.MIN_SCALE, min(self.MAX_SCALE, base * self._scale))
        width = max(1, round(self.original.width() * scale))
        height = max(1, round(self.original.height() * scale))
        canvas = QPixmap(self.size())
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        x = (self.width() - width) / 2 + self._offset.x()
        y = (self.height() - height) / 2 + self._offset.y()
        painter.drawPixmap(QRectF(x, y, width, height), self.original,
                           QRectF(self.original.rect()))
        painter.end()
        self.setPixmap(canvas)

    def _clamp_offset(self) -> None:
        base = self._fit_scale()
        scale = max(self.MIN_SCALE, min(self.MAX_SCALE, base * self._scale))
        width = self.original.width() * scale if self.original else 0
        height = self.original.height() * scale if self.original else 0
        limit_x = max(0.0, (width - self.width()) / 2)
        limit_y = max(0.0, (height - self.height()) / 2)
        self._offset = QPointF(max(-limit_x, min(limit_x, self._offset.x())),
                               max(-limit_y, min(limit_y, self._offset.y())))

    def set_picture(self, picture):
        self.original = picture
        self._scale = 1.0
        self._offset = QPointF(0, 0)
        self._paint()

    def _fit(self):
        self._paint()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._paint()

    def wheelEvent(self, event):
        if self.original is None or self.original.isNull():
            return super().wheelEvent(event)
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta:
            return
        factor = 1.1 ** (delta / 120.0)
        before = self._scale
        after = max(self.MIN_SCALE, min(self.MAX_SCALE, before * factor))
        if after == before:
            event.accept()
            return
        # 以光标位置为缩放中心：算出光标相对中心的世界坐标，按比例缩放后回推偏移
        centre = QPointF(self.width() / 2, self.height() / 2)
        cursor = event.position()
        anchor = (cursor - centre - self._offset) / before
        self._scale = after
        self._offset = cursor - centre - anchor * after
        self._clamp_offset()
        self._paint()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._scale > 1.0:
            self._drag_from = event.position()
            self._drag_start = QPointF(self._offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            self._offset = self._drag_start + (event.position() - self._drag_from)
            self._clamp_offset()
            self._paint()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_from is not None:
            self._drag_from = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._scale = 1.0
        self._offset = QPointF(0, 0)
        self._paint()
        event.accept()


class VideoPreview(PreviewLabel):
    def __init__(self):
        super().__init__()
        self.zoom = 1.0
        self.offset = QPointF()
        self.drag_position = None
        self.setToolTip("滚轮缩放 · 放大后拖动画面 · 双击复位")

    def set_picture(self, picture):
        if self.original is None:
            self.setText("")
        super().set_picture(picture)

    def _image_rect(self):
        view = QRectF(self.contentsRect())
        width, height = self.original.width(), self.original.height()
        scale = min(view.width() / width, view.height() / height) * self.zoom
        image = QRectF(0, 0, width * scale, height * scale)
        image.moveCenter(view.center() + self.offset)
        return image

    def _fit(self):
        if self.original is None:
            return
        view = self.contentsRect()
        image = self._image_rect()
        limit_x = max(0.0, (image.width() - view.width()) / 2)
        limit_y = max(0.0, (image.height() - view.height()) / 2)
        self.offset = QPointF(min(limit_x, max(-limit_x, self.offset.x())),
                              min(limit_y, max(-limit_y, self.offset.y())))
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.original is not None:
            painter = QPainter(self)
            painter.setClipRect(self.contentsRect())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self._image_rect(), self.original, QRectF(self.original.rect()))

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if self.original is None or not delta:
            return super().wheelEvent(event)
        steps = min(3.0, max(-3.0, delta / 120))
        zoom = min(8.0, max(.25, self.zoom * 1.25 ** steps))
        center = QRectF(self.contentsRect()).center()
        anchor = event.position() if self._image_rect().contains(event.position()) else center
        self.offset = anchor - center + (center + self.offset - anchor) * (zoom / self.zoom)
        self.zoom = zoom
        self._fit()
        event.accept()

    def mousePressEvent(self, event):
        if self.zoom > 1 and self.original is not None and event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.position()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.offset += event.position() - self.drag_position
            self.drag_position = event.position()
            self._fit()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag_position is not None and event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.zoom = 1.0
            self.offset = QPointF()
            self.drag_position = None
            self._fit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class VolumeSlider(WheelSlider):
    """竖直音量滑轨：从底部按数值向上填充，避免 QSS 在垂直方向上的反向问题。"""

    TRACK_WIDTH = 4
    HANDLE = 12

    def __init__(self, orientation=Qt.Orientation.Vertical):
        super().__init__(orientation, wheel_step=5)
        self.setRange(0, 100)
        self.setFixedWidth(24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _track(self) -> QRectF:
        top = self.HANDLE / 2
        bottom = self.height() - self.HANDLE / 2
        return QRectF((self.width() - self.TRACK_WIDTH) / 2, top,
                      self.TRACK_WIDTH, max(1.0, bottom - top))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        track = self._track()
        radius = self.TRACK_WIDTH / 2
        painter.setBrush(QColor("#DEE7F5"))
        painter.drawRoundedRect(track, radius, radius)
        ratio = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        filled = track.height() * ratio
        if filled > 0:
            fill = QRectF(track.left(), track.bottom() - filled, track.width(), filled)
            painter.setBrush(QColor("#1677FF"))
            painter.drawRoundedRect(fill, radius, radius)
        handle_y = track.bottom() - filled
        painter.setBrush(QColor("#FFFFFF"))
        pen = QPen(QColor("#1677FF"))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.drawEllipse(QPointF(track.center().x(), handle_y),
                            self.HANDLE / 2, self.HANDLE / 2)
        painter.end()


class VolumePopup(QFrame):
    """B 站式音量浮层：竖直滑轨 + 百分比，点击轨道即定位。"""

    changed = Signal(int)

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setObjectName("volumePopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(64, 190)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(4)
        self.slider = VolumeSlider(Qt.Orientation.Vertical)
        self.slider.setObjectName("volumeSlider")
        self.slider.setAccessibleName("播放音量")
        layout.addWidget(self.slider, 1, Qt.AlignmentFlag.AlignHCenter)
        self.label = QLabel("100%")
        self.label.setObjectName("volumePercent")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)
        self.slider.valueChanged.connect(self._on_value)
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        from PySide6.QtGui import QColor
        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(26)
        glow.setOffset(0, 5)
        glow.setColor(QColor(30, 60, 110, 44))
        self.setGraphicsEffect(glow)

    def _on_value(self, value: int) -> None:
        self.label.setText(f"{value}%")
        self.changed.emit(value)

    def set_level(self, value: int) -> None:
        if self.slider.value() != value:
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)
        self.label.setText(f"{value}%")
