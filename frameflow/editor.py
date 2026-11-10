"""按媒体类型显示独立的帧、音轨或图片编辑页。"""

from __future__ import annotations

import sys
import tempfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from math import ceil, floor, log10
from pathlib import Path
from stat import S_ISREG
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSignalBlocker, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCursor, QColor, QIntValidator, QKeySequence, QPainter, QPen, QPixmap, QPolygonF, QShortcut
from PySide6.QtWidgets import (QBoxLayout, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter,
                               QStyle, QStyleOptionSlider, QTabBar, QTabWidget, QVBoxLayout, QWidget,
                               QWidgetAction)

from .editing import (Clip, Frames, VideoSource, audio_preview, audio_segment, frame_png, index_frames, length,
                      original_audio, render_edit, render_preview_wav, save_picture, slice_track, splice)
from .details import DetailsView
from .filmstrip import EDIT_KEYS, Filmstrip
from . import icons
from .media import output_extension
from .media import (AUDIO_EXTENSIONS, HARDWARE_BACKENDS, VIDEO_FRAME_RATES, VIDEO_RESOLUTIONS,
                    AudioInfo, Cancelled, Export, ImageInfo, MediaError, Processes, VideoInfo,
                    hardware_acceleration_choices)
from .media import (_AUDIO_ENCODERS, _ENCODERS, _IMAGE_FORMATS, _save_image, estimate_image_size,
                    media_path_missing, probe, thumbnail)
from .motion import (Button, ClickSlider, Combo, DirectoryButton, DirectoryPathEdit, Motion, QualityRow, SpinBox,
                     hide_vertical_scroll_bar, queued_media_key)
from .playback import Playback
from .theme import STUDIO_STYLE, C, shadow


_IMAGE_ENCODERS = {"png", "jpg", "jpeg", "webp", "avif", "tiff", "bmp", "gif_image", "ico", "pdf"}

def _friendly_edge_title(edge: int) -> str:
    """把 3840/1920 这类像素数字翻译成人话，专业信息放在括号里。"""
    names = {3840: "超大尺寸 · 4K", 2560: "超大尺寸 · 1440P",
             1920: "高清 · 1080P", 1280: "高清 · 720P",
             854: "标准 · 480P", 640: "小尺寸 · 360P"}
    return names.get(edge, f"最长边 {edge} px")


_IMAGE_SIZE_CHOICES = (
    ("超大尺寸（适合高分辨率图片）", 2160, "最长边 2160 px"),
    ("高清（适合日常保存）", 1920, "最长边 1920 px"),
    ("标准（适合分享）", 1280, "最长边 1280 px"),
    ("小尺寸（节省空间）", 720, "最长边 720 px"),
    ("超小尺寸（缩略图用）", 256, "最长边 256 px"),
)


_PICTURE_PRESETS = (
    ("画质优先", 95, 6, "保留更多细节，文件通常更大。"),
    ("均衡", 85, 7, "兼顾清晰度和文件大小。"),
    ("文件体积", 72, 9, "优先缩小文件，可填写目标大小。"),
)


def _line(*widgets):
    row = QHBoxLayout()
    for widget in widgets:
        row.addWidget(widget)
    row.addStretch(1)
    return row


def _button(title, callback):
    button = Button(title)
    button.setAutoDefault(False)
    button.setMinimumHeight(32)
    button.clicked.connect(callback)
    return button


def _text_button(title, callback):
    button = QPushButton(title)
    button.setAutoDefault(False)
    button.setProperty("toolLink", True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setMinimumHeight(30)
    button.clicked.connect(callback)
    return button


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


class FullscreenPlayer(QDialog):
    def __init__(self, editor):
        super().__init__(editor, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.editor = editor
        self.setObjectName("fullscreenPlayer")
        self.setWindowTitle(editor.path.name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(editor.player)
        self.hide_timer = QTimer(self, singleShot=True, interval=2500)
        self.hide_timer.timeout.connect(lambda: editor.player_controls.hide() if editor.playback else None)
        for widget in (self, editor.player, editor.frame_view, editor.player_controls,
                       *editor.player_controls.findChildren(QWidget)):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(self.reject)
        self.hide_timer.start()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Enter, QEvent.Type.MouseButtonPress):
            self.editor.player_controls.show()
            self.hide_timer.start()
        return super().eventFilter(obj, event)

    def done(self, result):
        self.hide_timer.stop()
        self.editor.restore_player()
        super().done(result)
        self.deleteLater()


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


def _combo():
    return Combo()


def _text(value, *, name="", muted=False):
    widget = QLabel(value)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    if muted:
        widget.setProperty("muted", True)
    return widget


def _icon_label(name, size=16, color=None):
    label = QLabel()
    label.setObjectName("lineIcon")
    label.setFixedSize(size, size)
    label.setPixmap(icons.pixmap(name, size, color or icons.INK))
    return label


def _icon_button(name, callback, tooltip="", size=17):
    button = Button("")
    button.setProperty("flat", True)
    button.setProperty("iconOnly", True)
    button.setFixedSize(28, 26)
    button.setIcon(icons.icon(name, size))
    button.setIconSize(QSize(size, size))
    button.setToolTip(tooltip)
    button.setAutoDefault(False)
    button.clicked.connect(callback)
    return button


def _timecode(seconds):
    seconds = max(0.0, float(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, remainder = divmod(remainder, 60)
    if hours:
        return f"{int(hours)}:{int(minutes):02d}:{remainder:06.3f}"
    return f"{int(minutes):02d}:{remainder:06.3f}"


def _rate_text(bits):
    if not bits:
        return "—"
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.2f} Mbps"
    return f"{bits / 1_000:.0f} kbps"


def _size_text(size):
    if not size:
        return "—"
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.2f} GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.2f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.1f} KB"
    return f"{size} B"


def _spin(*, seconds=False):
    field = QDoubleSpinBox() if seconds else SpinBox()
    if seconds:
        field.setDecimals(3)
        field.setSingleStep(.1)
        field.setSuffix(" 秒")
    return field


def _field(title: str, widget: QWidget) -> QWidget:
    """给单元格加一行小标题，比一列「标签 + 控件」更紧凑，也便于两列排布。"""
    holder = QWidget()
    holder.setObjectName("fieldCell")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    if title:
        label = QLabel(title)
        label.setObjectName("fieldLabel")
        layout.addWidget(label)
    layout.addWidget(widget)
    holder.field_label = label if title else None
    holder.field_control = widget
    return holder


class _ExportCells:
    """按控件找到它所在的单元格，保持老的 setRowVisible 调用方式。"""

    def __init__(self, cells: dict):
        self._cells = cells

    def cell(self, widget):
        return self._cells.get(widget, widget)

    def setRowVisible(self, widget, visible: bool) -> None:
        self.cell(widget).setVisible(visible)


class EditorEvents(QObject):
    finished = Signal(object, str)
    progress = Signal(int)
    notice = Signal(str)
    frame_ready = Signal(object, object, str)
    picture_estimate = Signal(int, int, str)


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


class WaveView(PreviewLabel):
    range_selected = Signal(float, float)
    seek = Signal(float)
    view_changed = Signal()
    context_requested = Signal(float, object)
    edit_command = Signal(str)
    media_dropped = Signal(str, float)
    trim_changed = Signal()
    window_requested = Signal(float, float)

    def __init__(self, can_drop_media):
        super().__init__()
        self.can_drop_media = can_drop_media
        self.waveform = None
        self.envelope_key = None
        self.bands = []
        self.loaded_interval = None
        self.interval = None
        self.playhead = 0.0
        self.insertion = None
        self.empty_duration = 0.0
        self.drop_active = False
        self.selection = None
        self.anchor = self.cursor = None
        self.overflow = None
        self.trim_mark = None
        self.mark_gesture = None
        self.mark_dragged = False
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

    def _timeline_interval(self):
        return self.interval or ((0.0, self.empty_duration) if self.empty_duration else None)

    def set_waveform(self, waveform, interval):
        self.waveform = waveform
        self.envelope_key = None
        self.bands = []
        self.loaded_interval = self.interval = interval if waveform else None
        self.anchor = self.cursor = None
        self.mark_gesture = None
        self.mark_dragged = False
        self.setText("" if waveform else "暂无可显示的音频")
        self.update()
        self.view_changed.emit()

    def show_interval(self, start, end):
        first, last = self.loaded_interval
        span = min(last - first, max(end - start, self.minimum_span()))
        start = min(max(start, first), last - span)
        self.interval = start, start + span
        self.update()
        self.view_changed.emit()

    def minimum_span(self):
        return max(12, self._bounds().width() / 8) / self.waveform.sample_rate

    def sample_spacing(self):
        if not self.interval:
            return 0
        start, end = self.interval
        return self._bounds().width() / ((end - start) * self.waveform.sample_rate)

    def zoom_to_samples(self, position):
        if self.waveform:
            half_span = max(24, self._bounds().width() / 4) / (2 * self.waveform.sample_rate)
            self.show_interval(position - half_span, position + half_span)

    def zoom(self, factor, fraction=.5):
        if self.interval:
            start, end = self.interval
            span = end - start
            first, last = self.loaded_interval
            width = min(last - first, max(span / factor, self.minimum_span()))
            left = start + (span - width) * fraction
            self.show_interval(left, left + width)

    def reset_zoom(self):
        if self.loaded_interval:
            self.show_interval(*self.loaded_interval)

    def reveal(self, position):
        if self.interval and self.loaded_interval[0] <= position <= self.loaded_interval[1]:
            start, end = self.interval
            if not start <= position <= end:
                self.show_interval(position - (end - start) * .15, position + (end - start) * .85)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not self.interval or not delta:
            super().wheelEvent(event)
            return
        steps = min(8, max(-8, delta / 120))
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.4 ** steps, self._fraction(event.position()))
        else:
            start, end = self.interval
            offset = -(end - start) * steps / 5
            loaded_start, loaded_end = self.loaded_interval
            if self.overflow and (
                (offset < 0 and start <= loaded_start + 1e-6 and loaded_start > 0)
                or (offset > 0 and end >= loaded_end - 1e-6 and loaded_end < self.overflow[1] - 1e-6)
            ):
                self.window_requested.emit(max(0, start + offset), max(end - start, end + offset))
            else:
                self.show_interval(start + offset, end + offset)
        event.accept()

    def _bounds(self):
        return QRectF(self.rect()).adjusted(46, 33, -14, -15)

    def _fraction(self, position):
        bounds = self._bounds()
        return min(1, max(0, (position.x() - bounds.x()) / max(1, bounds.width())))

    def _time_at(self, position):
        if interval := self._timeline_interval():
            start, end = interval
            return start + (end - start) * self._fraction(position)
        return self.playhead

    def set_overflow(self, overflow):
        if overflow != self.overflow:
            self.overflow = overflow
            self.mark_gesture = None
            self.mark_dragged = False
            self.set_trim_mark(overflow)

    def set_trim_mark(self, region):
        self.trim_mark = region
        self.update()
        self.trim_changed.emit()

    def marked_at(self, position):
        return self.trim_mark is not None and self.trim_mark[0] <= position <= self.trim_mark[1]

    def move_trim_mark(self, start):
        if self.trim_mark and self.overflow:
            duration = self.trim_mark[1] - self.trim_mark[0]
            start = max(0.0, min(start, self.overflow[1] - duration))
            self.set_trim_mark((start, start + duration))

    def _overflow_after_mark(self):
        if self.overflow:
            start, end = self.overflow
            if self.trim_mark:
                first, last = self.trim_mark
                end -= max(0.0, min(start, last) - first)
            return start, max(start, end)
        return None

    def _update_mark_gesture(self, position):
        button, origin_x, anchor, original = self.mark_gesture
        if not self.mark_dragged and abs(position.x() - origin_x) < 3:
            return
        self.mark_dragged = True
        current = self._time_at(position)
        start, end = original
        if button == Qt.MouseButton.RightButton:
            duration = end - start
            left = max(0.0, min(start + current - anchor, self.overflow[1] - duration))
            self.set_trim_mark((left, left + duration))
        else:
            left, right = sorted((anchor, max(start, min(end, current))))
            if right - left > .0005:
                self.set_trim_mark((left, right))

    def mousePressEvent(self, event):
        button = event.button()
        time = self._time_at(event.position())
        if (self._timeline_interval() and button in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton)
                and self.marked_at(time)):
            self.setFocus()
            self.anchor = self.cursor = None
            self.mark_gesture = button, event.position().x(), time, self.trim_mark
            self.mark_dragged = False
            event.accept()
        elif button == Qt.MouseButton.LeftButton:
            self.setFocus()
            if self._timeline_interval():
                self.anchor = self.cursor = self._fraction(event.position())
                self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.mark_gesture is not None and event.buttons() & self.mark_gesture[0]:
            self._update_mark_gesture(event.position())
            event.accept()
        elif self.anchor is not None:
            self.cursor = self._fraction(event.position())
            self.update()

    def mouseReleaseEvent(self, event):
        button = event.button()
        position = self._time_at(event.position())
        if self.mark_gesture is not None and button == self.mark_gesture[0]:
            self._update_mark_gesture(event.position())
            moved = self.mark_dragged
            self.mark_gesture = None
            self.mark_dragged = False
            if not moved:
                if button == Qt.MouseButton.RightButton:
                    self.context_requested.emit(position, event.globalPosition().toPoint())
                else:
                    self.seek.emit(position)
            event.accept()
        elif button == Qt.MouseButton.RightButton:
            self.setFocus()
            self.context_requested.emit(position, event.globalPosition().toPoint())
            event.accept()
        elif button == Qt.MouseButton.LeftButton and self.anchor is not None and self._timeline_interval():
            start, end = self._timeline_interval()
            left, right = sorted((self.anchor, self._fraction(event.position())))
            self.anchor, self.cursor = None, None
            if (right - left) * self._bounds().width() >= 3:
                self.range_selected.emit(start + (end - start) * left, start + (end - start) * right)
            else:
                self.seek.emit(start + (end - start) * left)
            self.update()
        else:
            super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        self.drop_active = self.insertion is not None and self.can_drop_media(queued_media_key(event.mimeData()))
        self.update()
        if self.drop_active:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_active = False
        self.update()
        event.accept()

    def dropEvent(self, event):
        self.drop_active = False
        self.update()
        key = queued_media_key(event.mimeData())
        if self.insertion is not None and self.can_drop_media(key):
            self.media_dropped.emit(key, self.insertion)
            event.acceptProposedAction()
        else:
            event.ignore()

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if command := EDIT_KEYS.get(event.key()):
                self.edit_command.emit(command)
                event.accept()
                return
        super().keyPressEvent(event)

    def _selection_fraction(self):
        if self.anchor is not None and self.cursor is not None:
            return sorted((self.anchor, self.cursor))
        if self.selection:
            start, end = self.interval
            return [min(1, max(0, (second - start) / (end - start))) for second in self.selection]
        return 0, 0

    @staticmethod
    def _time_label(second, decimals):
        units = 10 ** decimals
        minutes, remainder = divmod(round(second * units), 60 * units)
        width = decimals + 3 if decimals else 2
        return f"{minutes}:{remainder / units:0{width}.{decimals}f}"

    def _draw_ruler(self, painter, bounds, interval):
        start, end = interval
        duration = end - start
        ideal = duration / max(1, int(bounds.width() / 85))
        unit = 10 ** floor(log10(ideal))
        step = next(unit * factor for factor in (1, 2, 5, 10) if unit * factor >= ideal)
        decimals = min(6, max(1, ceil(-log10(step))))
        text_area = QRectF(bounds.left(), 7, bounds.width(), 20)
        painter.setPen(QColor("#153c62"))
        painter.drawText(text_area, Qt.AlignmentFlag.AlignLeft, self._time_label(start, decimals))
        painter.drawText(text_area, Qt.AlignmentFlag.AlignRight, self._time_label(end, decimals))
        for mark in range(floor(start / step) + 1, ceil(end / step)):
            second = mark * step
            x = bounds.left() + (second - start) * bounds.width() / duration
            painter.setPen(QColor("#d1e0ef"))
            painter.drawLine(QPointF(x, bounds.top() - 5), QPointF(x, bounds.bottom()))
            if bounds.left() + 57 < x < bounds.right() - 57:
                painter.setPen(QColor("#53677e"))
                painter.drawText(QRectF(x - 35, 7, 70, 20), Qt.AlignmentFlag.AlignCenter,
                                 self._time_label(second, decimals))

    def paintEvent(self, event):
        super().paintEvent(event)
        interval = self._timeline_interval()
        if not interval:
            return
        bounds = self._bounds()
        painter = QPainter(self)
        painter.fillRect(bounds, QColor("#f8fafd"))
        painter.fillRect(QRectF(5, bounds.top(), bounds.left() - 5, bounds.height()), QColor("#eff3f8"))
        if self.waveform is None:
            self._draw_ruler(painter, bounds, interval)
            painter.setPen(QColor("#53677e"))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, "暂无音轨 · 单击定位后可拖入音频")
            self._draw_playheads(painter, bounds, interval)
            painter.end()
            return
        left, right = self._selection_fraction()
        selected = QRectF(bounds.left() + left * bounds.width(), bounds.top(),
                          (right - left) * bounds.width(), bounds.height())
        if selected.width() > 0:
            painter.fillRect(selected, QColor("#e0edfd"))
        overflow = self._span_rect(bounds, interval, self._overflow_after_mark())
        marked = self._span_rect(bounds, interval, self.trim_mark)
        if not overflow.isEmpty():
            painter.fillRect(overflow, QColor("#ffe8ea"))
        if not marked.isEmpty():
            painter.fillRect(marked, QColor("#ffcfd4"))
        self._draw_ruler(painter, bounds, interval)
        start, end = self.interval
        waveform = self.waveform
        base_start = self.loaded_interval[0]
        spacing = self.sample_spacing()
        samples_visible = spacing >= 1.0
        if not samples_visible:
            columns = max(1, round(bounds.width() * self.devicePixelRatioF()))
            if (self.interval, columns) != self.envelope_key:
                self.bands = waveform.envelope(start - base_start, end - base_start, columns)
                self.envelope_key = self.interval, columns
        lane_count = len(waveform.samples)
        lane_height = bounds.height() / lane_count
        for index, samples in enumerate(waveform.samples):
            middle = bounds.top() + (index + .5) * lane_height
            gain = lane_height * .41
            name = "波形" if lane_count == 1 else ("左声道" if index == 0 else "右声道")
            painter.setPen(QColor("#62758c"))
            painter.drawText(QRectF(5, middle - lane_height / 2, bounds.left() - 7, 19),
                             Qt.AlignmentFlag.AlignCenter, name)
            for level, label in ((.5, "+0.5"), (0, "0"), (-.5, "−0.5")):
                y = middle - gain * level
                painter.setPen(QColor("#a6b6c8" if not level else "#e4ebf4"))
                painter.drawLine(QPointF(bounds.left(), y), QPointF(bounds.right(), y))
                painter.setPen(QColor("#687d95"))
                painter.drawText(QRectF(5, y - 9, bounds.left() - 12, 18),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, label)
            painter.save()
            painter.setClipRect(QRectF(bounds.left(), middle - lane_height / 2, bounds.width(), lane_height))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#376f9f"), 1))
            if samples_visible:
                first = max(0, floor((start - base_start) * waveform.sample_rate))
                last = min(len(samples), ceil((end - base_start) * waveform.sample_rate) + 1)
                left = bounds.left() + (base_start - start) * waveform.sample_rate * spacing
                points = [QPointF(left + i * spacing, middle - samples[i] / waveform.reference * gain)
                          for i in range(first, last)]
                painter.drawPolyline(QPolygonF(points))
                if spacing >= 3:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor("#376f9f"))
                    for point in points:
                        painter.drawEllipse(point, 1.35, 1.35)
            else:
                band = self.bands[index]
                step = bounds.width() / max(1, len(band) - 1)
                upper = [QPointF(bounds.left() + i * step, middle - high * gain)
                         for i, (_, high) in enumerate(band)]
                lower = [QPointF(bounds.left() + i * step, middle - low * gain)
                         for i, (low, _) in enumerate(band)]
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#c7def2"))
                painter.drawPolygon(QPolygonF(upper + lower[::-1]))
                painter.setPen(QPen(QColor("#4e81ae"), .8))
                painter.drawPolyline(QPolygonF(upper))
                painter.drawPolyline(QPolygonF(lower))
            painter.restore()
        if selected.width() > 0:
            painter.setPen(QColor("#4687d5"))
            painter.drawLine(selected.topLeft(), selected.bottomLeft())
            painter.drawLine(selected.topRight(), selected.bottomRight())
        if not marked.isEmpty():
            painter.setPen(QPen(QColor("#cc444e"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(marked)
        if self.overflow and interval[0] <= self.overflow[0] <= interval[1]:
            x = bounds.left() + (self.overflow[0] - interval[0]) * bounds.width() / (interval[1] - interval[0])
            painter.setPen(QPen(QColor("#c03642"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, bounds.top() - 6), QPointF(x, bounds.bottom()))
            if bounds.right() - x >= 58:
                painter.drawText(QRectF(x + 5, bounds.top() + 4, 54, 19),
                                 Qt.AlignmentFlag.AlignCenter, "视频结尾")
        self._draw_playheads(painter, bounds, interval)
        painter.end()

    @staticmethod
    def _span_rect(bounds, interval, span):
        if span:
            start, end = interval
            left, right = max(start, span[0]), min(end, span[1])
            if right > left:
                scale = bounds.width() / (end - start)
                return QRectF(bounds.left() + (left - start) * scale, bounds.top(),
                              (right - left) * scale, bounds.height())
        return QRectF()

    def _draw_playheads(self, painter, bounds, interval):
        start, end = interval
        for position, color, width in ((self.playhead, "#194f98", 1),
                                       (self.insertion, "#17202b", 3 if self.drop_active else 2)):
            if position is not None and start <= position <= end:
                x = bounds.left() + (position - start) * bounds.width() / (end - start)
                painter.setPen(QPen(QColor(color), width))
                painter.drawLine(QPointF(x, bounds.top() - 6), QPointF(x, bounds.bottom()))


class VolumeSlider(ClickSlider):
    """竖直音量滑轨：从底部按数值向上填充，避免 QSS 在垂直方向上的反向问题。"""

    TRACK_WIDTH = 4
    HANDLE = 12

    def __init__(self, orientation=Qt.Orientation.Vertical):
        super().__init__(orientation)
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


class Editor(QDialog):
    saved = Signal(object)
    missing_source = Signal(object)
    return_requested = Signal()
    task_changed = Signal()
    settings_changed = Signal()
    directory_changed = Signal(object)

    def __init__(self, path, info, tools, directory=None, parent=None, *, embedded=False,
                 lookup_media=None, report_missing=None, defer_media=False, get_output_stem=lambda: ""):
        super().__init__(parent)
        self.embedded = embedded
        self.media_started = False
        self.configuration_only = False
        self._compact = False
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.path, self.info, self.tools = path, info, tools
        self._get_output_stem = get_output_stem
        self.media_format = "mp4" if isinstance(info, VideoInfo) else "mp3"
        self.audio_tab_format = "mp3"
        self.selected_acceleration = "cpu"
        self.lookup_media = lookup_media
        if report_missing is not None:
            self.missing_source.connect(report_missing, Qt.ConnectionType.QueuedConnection)
        self.directory = directory or path.parent
        self.playback_level = 100
        self.last_audible_level = 100
        self.playback_muted = False
        self.fullscreen_player = None
        self.frames = None
        self.filmstrip = None
        self.video = []
        self.video_sources = {}
        self.imported_images = {}
        self.video_range = None
        self.video_overrides = {}
        self.video_selection = ()
        self.video_drag_origin = (None, {})
        self.audio = None if isinstance(info, VideoInfo) else (
            [Clip(path, info.stream, 0, info.duration)] if isinstance(info, AudioInfo) and info.duration else [])
        self.video_clipboard, self.audio_clipboard = [], []
        self.video_clipboard_audio = None
        self.audio_clipboard_video = []
        self.history = []
        self.audio_range_initialized = False
        self.audio_available = False
        self.wave_track = ()
        self.wave_pending = False
        self.wave_cache = OrderedDict()
        self.scope = None
        self.pending_video_insert = None
        self.on_done = None
        self.on_error = None
        self.closing = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frameflow-editor")
        self.frame_pool = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="frameflow-seek")
                           if isinstance(info, VideoInfo) else None)
        self.picture_estimate_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frameflow-image-size")
        self.frame_scope = None
        self.picture_estimate_scope = None
        self.picture_estimate_generation = 0
        self.picture_estimate_timer = QTimer(self)
        self.picture_estimate_timer.setSingleShot(True)
        self.picture_estimate_timer.setInterval(260)
        self.picture_estimate_timer.timeout.connect(self._estimate_picture_size)
        self.frame_inflight = None
        self.frame_cache = OrderedDict()
        self.requested_frame = self.shown_frame = self.failed_frame = None
        self.scratch = tempfile.TemporaryDirectory(prefix="frameflow-preview-")
        self.listen_wav = Path(self.scratch.name) / "listen.wav"
        self.playback = None
        self.playback_ranges = ()
        self.playing = False
        self.listen_requested = False
        self.audio_clock = None
        self.audio_timer = QTimer(self, interval=30)
        self.audio_timer.timeout.connect(self._audio_tick)
        self.wave_reload = QTimer(self, singleShot=True)
        self.wave_reload.timeout.connect(self._reload_audio_wave)
        self.events = EditorEvents(self)
        self.events.finished.connect(self._finished)
        self.events.progress.connect(self._progress)
        self.events.frame_ready.connect(self._frame_ready)
        self.events.picture_estimate.connect(self._picture_estimated)
        self.seek_timer = QTimer(self, singleShot=True, interval=55)
        self.seek_timer.timeout.connect(self._seek)
        self.playback_timer = QTimer(self, interval=12, timerType=Qt.TimerType.PreciseTimer)
        self.playback_timer.timeout.connect(self._playback_tick)
        self.setWindowTitle(f"编辑与预览 · {path.name}")
        self.resize(1010, 740)
        self._build()
        self.events.notice.connect(self._notice)
        if not defer_media:
            self.start_media()

    def start_media(self):
        if self.media_started:
            return
        self.media_started = True
        if isinstance(self.info, VideoInfo):
            self._run("正在读取真实帧数…", self._load_index, self._indexed)
        elif isinstance(self.info, ImageInfo):
            self._schedule_picture_estimate()
            self._run("正在读取图片…", self._load_picture, self._show_image)
        elif self.audio_page is not None:
            self._tab_changed()

    def _build(self):
        self.setObjectName("studio")
        self.setStyleSheet(STUDIO_STYLE)
        base = QVBoxLayout(self)
        base.setContentsMargins(*((0, 0, 0, 0) if self.embedded else (16, 12, 16, 10)))
        base.setSpacing(0)

        self.editor_split = self._splitter(Qt.Orientation.Vertical)
        self.workspace_split = self._splitter(Qt.Orientation.Horizontal)
        self.preview_split = self._splitter(Qt.Orientation.Vertical)
        self.workspace_split.setHandleWidth(0)
        preview = QFrame()
        preview.setObjectName("mediaPreviewPanel")
        shadow(preview, 12, 2, 19)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(11, 9, 11, 10)
        preview_layout.setSpacing(4)
        self.file_title = _text(self.path.name, name="studioTitle")
        self.file_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.file_title.setToolTip(self.path.name)
        preview_layout.addWidget(self.file_title)
        preview_layout.addWidget(self.preview_split, 1)
        self.workspace_split.addWidget(preview)
        self.editor_split.addWidget(self.workspace_split)
        base.addWidget(self.editor_split, 1)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mediaTabs")
        self.tabs.tabBar().setObjectName("mediaTabBar")
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.setIconSize(QSize(18, 18))
        self.preview_split.addWidget(self.tabs)
        self.video_page = self.audio_page = self.image_page = self.details_page = None
        if isinstance(self.info, VideoInfo):
            self.video_page, layout = self._page("视频" if self.embedded else "视频帧", "video")
            layout.setContentsMargins(0, 6, 0, 0)
            self.video_layout = layout
            self.cursor = _spin()
            self.cursor.setSuffix(" 帧")
            self.cursor.setFixedWidth(88)
            self.cursor.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cursor.valueChanged.connect(self._cursor_changed)

            self.player = QFrame()
            self.player.setObjectName("videoPlayer")
            player_layout = QVBoxLayout(self.player)
            player_layout.setContentsMargins(0, 0, 0, 0)
            player_layout.setSpacing(0)
            self.frame_view = self._preview_label(VideoPreview())
            self.frame_view.setObjectName("videoCanvas")
            self.frame_view.setMinimumHeight(150)
            badges = QVBoxLayout(self.frame_view)
            badges.setContentsMargins(10, 9, 10, 0)
            player_header = QHBoxLayout()
            player_header.setSpacing(5)
            container = self.path.suffix.lstrip(".").upper() or "VIDEO"
            player_header.addWidget(_text(container, name="playerBadge"))
            player_header.addWidget(_text(self.info.codec, name="playerBadge"))
            player_header.addWidget(_text(f"{self.info.width} × {self.info.height}", name="playerBadge"))
            player_header.addWidget(_text(f"{self.info.fps:g} FPS", name="playerBadge"))
            player_header.addStretch(1)
            badges.addLayout(player_header)
            badges.addStretch(1)
            player_layout.addWidget(self.frame_view, 1)

            controls = QFrame()
            controls.setObjectName("playerControls")
            self.player_controls = controls
            controls_layout = QHBoxLayout(controls)
            controls_layout.setContentsMargins(10, 7, 10, 8)
            controls_layout.setSpacing(6)
            self.previous_frame_button = TransportButton("previous", "上一帧", self._previous)
            self.play_button = TransportButton("play", "播放", self._toggle_playback, primary=True)
            self.play_button.setEnabled(False)
            self.next_frame_button = TransportButton("next", "下一帧", self._next)
            for widget in (self.previous_frame_button, self.play_button, self.next_frame_button):
                widget.setEnabled(False)
                controls_layout.addWidget(widget)
            self.player_time_current = _text("00:00.000", name="playerTime")
            self.player_time_current.setContentsMargins(5, 0, 0, 0)
            controls_layout.addWidget(self.player_time_current)
            self.player_time_short = _text("00:00", name="playerTime")
            self.player_time_short.hide()
            controls_layout.addWidget(self.player_time_short)
            self.player_time_separator = _text("/", name="playerTime")
            controls_layout.addWidget(self.player_time_separator)
            self.player_time_total = _text("00:00.000", name="playerTime")
            controls_layout.addWidget(self.player_time_total)
            self.player_seek = SeekSlider(Qt.Orientation.Horizontal)
            self.player_seek.setObjectName("playerSeek")
            self.player_seek.setAccessibleName("视频播放位置")
            self.player_seek.setRange(1, 1)
            self.player_seek.setSingleStep(1)
            self.player_seek.setPageStep(1)
            self.player_seek.setMinimumWidth(42)
            self.player_seek.setEnabled(False)
            self.player_seek.valueChanged.connect(self._seek_from_player)
            controls_layout.addWidget(self.player_seek, 1)
            self.volume_button = TransportButton("volume", "静音 / 恢复音量", self._toggle_player_mute)
            controls_layout.addWidget(self.volume_button)
            self.volume_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.volume_button.customContextMenuRequested.connect(self._show_player_volume)
            self.volume_button.installEventFilter(self)
            self.volume_hide = QTimer(self)
            self.volume_hide.setSingleShot(True)
            self.volume_hide.setInterval(260)
            self.volume_hide.timeout.connect(lambda: self.volume_menu.hide()
                                             if self.volume_menu.isVisible() else None)
            self.volume_menu = QMenu(self.volume_button)
            volume_action = QWidgetAction(self.volume_menu)
            volume_panel = QWidget()
            volume_layout = QHBoxLayout(volume_panel)
            volume_layout.addWidget(_text("音量"))
            self.player_volume = QSlider(Qt.Orientation.Horizontal)
            self.player_volume.setRange(0, 100)
            self.player_volume.setValue(self.playback_level)
            self.player_volume.setAccessibleName("播放音量")
            self.player_volume.setFixedWidth(110)
            self.player_volume.valueChanged.connect(self._set_player_volume)
            volume_layout.addWidget(self.player_volume)
            self.volume_value = _text("100%")
            self.volume_value.setMinimumWidth(36)
            volume_layout.addWidget(self.volume_value)
            volume_action.setDefaultWidget(volume_panel)
            self.volume_menu.addAction(volume_action)
            self.volume_options = TransportButton("down", "调整播放音量", self._show_player_volume)
            self.volume_options.setFixedSize(24, 32)
            self.volume_options.hide()
            self.volume_button.setToolTip("")
            self.volume_popup = VolumePopup(self)
            self.volume_popup.changed.connect(self._set_player_volume)
            self.volume_popup.set_level(self.playback_level)
            self.volume_button.installEventFilter(self)
            self.volume_popup.installEventFilter(self)
            self.volume_hide = QTimer(self)
            self.volume_hide.setSingleShot(True)
            self.volume_hide.setInterval(250)
            self.volume_hide.timeout.connect(self._hide_volume_popup)
            self.fullscreen_button = TransportButton("fullscreen", "全屏", self._toggle_fullscreen)
            controls_layout.addWidget(self.fullscreen_button)
            player_layout.addWidget(controls)
            layout.addWidget(self.player, 1)
        if isinstance(self.info, (VideoInfo, AudioInfo)):
            self.audio_page, layout = self._page("音频" if self.embedded and self.video_page else "音轨", "wave")
            self.audio_layout = layout
            layout.setContentsMargins(0, 6, 0, 0)
            self.audio_view = self._preview_label(WaveView(self._can_drop_audio))
            self.audio_view.setStyleSheet("")
            self.audio_view.setObjectName("waveCanvas")
            self.audio_view.media_dropped.connect(self._drop_audio)
            self.audio_view.trim_changed.connect(self._update_audio_overflow)
            self.audio_view.window_requested.connect(self._focus_audio_window)
            self.audio_view.range_selected.connect(self._wave_selected)
            self.audio_view.seek.connect(self._click_audio)
            self.audio_view.view_changed.connect(self._update_audio_toolbar)
            self.audio_view.context_requested.connect(self._show_audio_context_menu)
            self.audio_view.edit_command.connect(self._audio_command)
            self.audio_context_menu = QMenu(self)
            audio_header = QFrame()
            audio_header.setObjectName("waveToolbar")
            transport = QHBoxLayout(audio_header)
            transport.setContentsMargins(9, 3, 7, 3)
            self.audio_play = _button("播放", self._toggle_audio_playback)
            self.audio_play.setObjectName("primary")
            self.audio_play.setProperty("studioAccent", True)
            self.audio_play.setMinimumWidth(64)
            self.audio_play.setEnabled(False)
            self.audio_play.setIcon(icons.icon("play", 16, "#FFFFFF"))
            self.audio_position = _text("0:00.000", muted=True)
            transport.addWidget(_icon_label("wave", 18, C.accent.name()))
            transport.addWidget(_text("波形", name="studioSection"))
            transport.addWidget(self.audio_position)
            transport.addStretch(1)
            self.zoom_out = _button("−", lambda: self.audio_view.zoom(.5))
            self.zoom_out.setToolTip("缩小波形时间范围")
            self.zoom_in = _button("+", lambda: self.audio_view.zoom(2))
            self.zoom_in.setToolTip("放大波形时间范围")
            self.zoom_reset = _button("复位", self._reset_audio_view)
            self.zoom_reset.setToolTip("恢复波形缩放，将播放头和黑色插入线移到开头")
            self.zoom_ratio = _text("1×", muted=True)
            self.zoom_ratio.setToolTip("时间轴缩放比例。放大到采样级时显示当前时间跨度。")
            for widget in (self.zoom_out, self.zoom_ratio, self.zoom_in, self.zoom_reset):
                transport.addWidget(widget)
            transport.addWidget(self.audio_play)
            layout.addWidget(audio_header)
            layout.addWidget(self.audio_view, 1)
            self.audio_label = _text("", muted=True)
            self.audio_label.setWordWrap(True)
            layout.addWidget(self.audio_label)
            self.audio_list = QListWidget()
            self.audio_list.setMaximumHeight(72)
            self.audio_list.setObjectName("audioClips")
            self.audio_list.setToolTip("组成当前音轨的片段")
            self.audio_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            hide_vertical_scroll_bar(self.audio_list)
            layout.addWidget(self.audio_list)
        if isinstance(self.info, (VideoInfo, ImageInfo)):
            self.image_page, layout = self._page("图片", "image")
            layout.setContentsMargins(0, 6, 0, 0)
            self.image_view = PictureView()
            self.image_view.setStyleSheet("")
            self.image_view.setObjectName("imageCanvas")
            layout.addWidget(self.image_view, 1)
            if self.video_page is not None:
                self.tabs.setTabToolTip(self.tabs.indexOf(self.image_page), "预览最靠前选区的首帧，未选帧时预览视频首帧")
        self.details_page, details_layout = self._page("文件详情", "file")
        details_layout.setContentsMargins(12, 12, 12, 12)
        self.details_view = DetailsView(wide=True)
        self.details_view.setAccessibleName("文件详情")
        self.details_view.setToolTip("查看文件原始路径、格式、大小和媒体参数；字段值可以选中，行尾悬停可单独复制。")
        details_layout.addWidget(self.details_view, 1)
        self._update_file_details()

        inspector = QFrame()
        inspector.setObjectName("studioInspector")
        inspector.setMinimumWidth(306)
        inspector.setMaximumWidth(500)
        shadow(inspector, 12, 2, 19)
        self.inspector = inspector
        side = QVBoxLayout(inspector)
        side.setContentsMargins(12, 9, 12, 12)
        side.setSpacing(6)
        self.media_summary = QFrame()
        self.media_summary.setObjectName("mediaInfoCard")
        if self.video_page is None:
            self.media_summary.hide()
        info = self.info
        description = (f"{info.width} × {info.height}   ·   {info.codec}"
                       if isinstance(info, (VideoInfo, ImageInfo)) else
                       f"{info.sample_rate / 1000:g} kHz   ·   {info.codec}")
        if self.video_page is None:
            side.addWidget(_text(description, muted=True))
        body = QWidget()
        self.settings_body = body
        self.settings_scroll = side_scroll = QScrollArea()
        stack = QVBoxLayout(body)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(7)
        side_scroll.setObjectName("studioScroll")
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(side_scroll)
        side_scroll.setWidget(body)
        body.setMinimumWidth(1)
        side_scroll.viewport().setAutoFillBackground(False)
        side_scroll.viewport().setObjectName("exportViewport")
        body.setObjectName("exportBody")
        side.addWidget(side_scroll, 1)

        # 选帧、剪切、另存都在时间线的右键菜单里，侧栏不再重复摆一份。
        # 这两个输入框只是为了保留区间状态的载体（拖选缩略图时会同步），不参与布局。
        self.video_first, self.video_last = FrameNumberEdit(), FrameNumberEdit()
        for field in (self.video_first, self.video_last):
            field.valueChanged.connect(self._range_fields_changed)
        self._selection_note = ""

        self.audio_tools = QFrame()
        self.audio_tools.setObjectName("audioSelectionCard")
        self.audio_tools.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        audio = QVBoxLayout(self.audio_tools)
        audio.setContentsMargins(10, 8, 10, 9)
        audio.setSpacing(6)
        audio_title = QHBoxLayout()
        audio_title.setSpacing(7)
        audio_title.addWidget(_icon_label("wave", 18, C.accent.name()))
        audio_title.addWidget(_text("音频选区", name="cardTitle"))
        audio_title.addStretch(1)
        audio.addLayout(audio_title)
        self.audio_first, self.audio_last, self.audio_cursor = (_spin(seconds=True) for _ in range(3))
        self.audio_selection_note = _text("拖动波形以选择片段", name="audioSelectionStatus")
        self.audio_selection_note.setToolTip("在波形上拖动鼠标即可选择音频片段")
        selection_status = QHBoxLayout()
        selection_status.addWidget(self.audio_selection_note, 1)
        self.edit_audio_range = _button("手动输入选区", self._show_audio_range_fields)
        self.edit_audio_range.setObjectName("secondaryAction")
        self.edit_audio_range.setFixedHeight(27)
        self.edit_audio_range.setCheckable(True)
        selection_status.addWidget(self.edit_audio_range)
        audio.addLayout(selection_status)

        self.audio_range_fields = QWidget()
        range_fields = QGridLayout(self.audio_range_fields)
        range_fields.setContentsMargins(0, 0, 0, 0)
        range_fields.setHorizontalSpacing(8)
        range_fields.setVerticalSpacing(4)
        for column, (title, control) in enumerate((("起点", self.audio_first), ("终点", self.audio_last))):
            field_label = _text(title, name="audioFieldLabel")
            range_fields.addWidget(field_label, 0, column)
            range_fields.addWidget(control, 1, column)
        range_fields.setColumnStretch(0, 1)
        range_fields.setColumnStretch(1, 1)
        self.audio_range_fields.setVisible(False)
        self.audio_first.valueChanged.connect(self._update_audio_marker)
        self.audio_last.valueChanged.connect(self._update_audio_marker)
        audio.addWidget(self.audio_range_fields)
        self.audio_insert_row = QWidget()
        self.audio_insert_row.setObjectName("audioInsertRow")
        insert_line = QHBoxLayout(self.audio_insert_row)
        insert_line.setContentsMargins(0, 0, 0, 0)
        insert_line.setSpacing(8)
        insert_label = _text("插入位置", name="audioFieldLabel")
        insert_line.addWidget(insert_label)
        insert_line.addStretch(1)
        self.audio_cursor.setFixedWidth(128)
        insert_line.addWidget(self.audio_cursor)
        audio.addWidget(self.audio_insert_row)
        self.overflow_label = _text("")
        self.overflow_label.setWordWrap(True)
        self.overflow_label.setStyleSheet("color: #a62c36; font-weight: 650;")
        self.overflow_jump = _text_button("定位", self._focus_audio_overflow)
        self.overflow_jump.setToolTip("跳到标红的待删区域")
        overflow_row = QHBoxLayout()
        overflow_row.addWidget(self.overflow_label, 1)
        overflow_row.addWidget(self.overflow_jump)
        audio.addLayout(overflow_row)
        self.overflow_label.hide()
        self.overflow_jump.hide()
        self.audio_hint = _text("右键波形可剪辑或插入音频。", muted=True)
        self.audio_hint.setToolTip("右键波形可剪辑、删除选区或插入队列中的音频。")
        self.audio_hint.hide()
        self.volume = _spin()
        self.volume.setRange(0, 300)
        self.volume.setValue(100)
        self.volume.setSuffix("%")
        self.volume.setToolTip("仅调整选区。100% 为原音量，50% 为一半，0% 为静音。")
        self.apply_volume = _button("应用到选区", self.set_volume)
        self.apply_volume.setObjectName("secondaryAction")
        self.apply_volume.setFixedHeight(29)
        self.apply_volume.setToolTip(self.volume.toolTip())
        audio.addLayout(_line(_text("选区音量"), self.volume, self.apply_volume))
        self.audio_tools.hide()

        self.picture_group = QFrame()
        self.picture_group.setObjectName("pictureExportCard")
        self.picture_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        picture = QVBoxLayout(self.picture_group)
        picture.setContentsMargins(10, 8, 10, 9)
        picture.setSpacing(6)
        self.picture_frame = _text("导出图片", name="studioSection")
        self.picture_frame.setObjectName("cardTitle")
        picture_heading = QHBoxLayout()
        picture_heading.setSpacing(7)
        picture_heading.addWidget(_icon_label("image", 18, C.accent.name()))
        picture_heading.addWidget(self.picture_frame)
        picture_heading.addStretch(1)
        picture.addLayout(picture_heading)
        self.picture_caption = _text("", muted=True)
        self.picture_caption.setObjectName("pictureCaption")
        picture.addWidget(self.picture_caption)
        picture_fields = QFormLayout()
        picture_fields.setContentsMargins(0, 0, 0, 0)
        picture_fields.setHorizontalSpacing(8)
        picture_fields.setVerticalSpacing(5)
        picture_fields.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        def picture_row(title, control):
            picture_fields.addRow(title, control)
            if label := picture_fields.labelForField(control):
                label.setObjectName("pictureFieldLabel")

        self.image_format = _combo()
        for key, label in _IMAGE_FORMATS.items():
            self.image_format.addItem("GIF · 静态" if key == "gif_image" else label, key)
        picture_row("格式", self.image_format)
        self.picture_preset = QTabBar()
        self.picture_preset.setObjectName("picturePreset")
        self.picture_preset.setAccessibleName("画质与体积")
        self.picture_preset.setDrawBase(False)
        self.picture_preset.setCursor(Qt.CursorShape.PointingHandCursor)
        for title, _, _, hint in _PICTURE_PRESETS:
            self.picture_preset.setTabToolTip(self.picture_preset.addTab(title), hint)
        self.picture_preset.setCurrentIndex(1)
        self.picture_quality_label = _text("画质", name="pictureFieldLabel")
        self.picture_preset.hide()
        self.picture_quality_label.hide()
        self.picture_preset_hint = _text("", name="pictureHint")
        self.picture_preset_hint.setWordWrap(True)
        self.picture_preset_hint.hide()
        self.resolution = _combo()
        self.resolution.addItem("原始尺寸（推荐，不缩放）", 0)
        for title, edge, hint in _IMAGE_SIZE_CHOICES:
            self.resolution.addItem(title, edge)
            self.resolution.setItemData(self.resolution.count() - 1,
                                        f"{hint} · 最长边 {edge} px", Qt.ItemDataRole.ToolTipRole)
        self.resolution.setToolTip("保持画面比例缩放，不会拉伸或压扁；括号内为最长边像素。")
        picture_row("分辨率", self.resolution)
        self.picture_dimensions = _text("正在读取…", name="pictureValue")
        picture_row("输出尺寸", self.picture_dimensions)
        self.size = _spin()
        self.size.setRange(0, 1_000_000)
        self.size.setSpecialValueText("自动")
        self.size.setSuffix(" KB")
        self.size.setToolTip("指定 KB 时会尝试进一步压缩，必要时缩小分辨率。")
        picture_row("目标大小", self.size)
        self.picture_estimate = _text("正在估算…", name="pictureValue")
        self.picture_estimate.hide()

        def update_picture_preset():
            index = self.picture_preset.currentIndex()
            compact = index == 2
            kind = self.image_format.currentData()
            supports_quality = kind in {"jpg", "webp", "avif"}
            lossless_png = kind == "png"
            quality_controls = supports_quality or lossless_png
            self.picture_quality_label.setText("PNG 压缩级别" if lossless_png else "画质")
            names = (("快速", "均衡", "高压缩") if lossless_png else
                     ("画质优先", "均衡", "文件体积"))
            for tab_index, name in enumerate(names):
                self.picture_preset.setTabText(tab_index, name)
            if lossless_png:
                hint = ("PNG 为无损格式，压缩级别不会降低画质。" if not compact else
                        "PNG 保持无损；设定体积上限时可能缩小输出尺寸。")
            elif supports_quality:
                hint = _PICTURE_PRESETS[index][3]
            else:
                hint = "此格式不使用有损画质滑块；可通过分辨率调整文件大小。"
            self.picture_preset_hint.setText(hint)
            self.picture_quality_label.setVisible(False)
            self.picture_preset.setVisible(False)
            self.picture_preset_hint.setVisible(False)
            picture_fields.setRowVisible(self.size, compact)

        self.picture_preset.currentChanged.connect(update_picture_preset)
        self.image_format.currentIndexChanged.connect(update_picture_preset)
        self.image_format.currentIndexChanged.connect(self._update_output_location)
        self.picture_preset.currentChanged.connect(self._schedule_picture_estimate)
        self.image_format.currentIndexChanged.connect(self._schedule_picture_estimate)
        self.image_format.currentIndexChanged.connect(
            lambda *_: self._sync_image_quality())
        self.resolution.currentIndexChanged.connect(self._schedule_picture_estimate)
        self.size.valueChanged.connect(self._schedule_picture_estimate)
        update_picture_preset()
        picture.addLayout(picture_fields)
        if isinstance(self.info, ImageInfo):
            self.picture_caption.setText(f"当前图片 · {self.info.width} × {self.info.height} 像素")
        self.picture_save = _button("保存图片", self.save_image)
        self.picture_save.hide()
        stack.addWidget(self.picture_group)

        self.export_group = QWidget()
        self.export_group.setObjectName("sharedExportBody")
        self.export_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        export = QVBoxLayout(self.export_group)
        export.setContentsMargins(0, 0, 0, 0)
        export.setSpacing(6)
        cells: dict[QWidget, QWidget] = {}

        def row(title: str, widget, width: int = 81) -> QWidget:
            holder = QWidget()
            holder.setObjectName("paramRow")
            holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            holder.setMinimumHeight(30)
            line = QHBoxLayout(holder)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(10)
            label = QLabel(title)
            label.setObjectName("paramLabel")
            label.setFixedWidth(width)
            line.addWidget(label)
            line.addWidget(widget, 1)
            holder.field_label = label
            cells[widget] = holder
            return holder

        def caption(icon: str, title: str, extra=None) -> QWidget:
            holder = QWidget()
            holder.setObjectName("paramCaption")
            holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            line = QHBoxLayout(holder)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(7)
            line.addWidget(_icon_label(icon, 19))
            line.addWidget(_text(title, name="cardTitle"))
            line.addStretch(1)
            if extra is not None:
                line.addWidget(extra)
            return holder

        # ---- 1. 导出模式 -------------------------------------------------
        self.format = _combo()
        self.preset = _combo()
        for title, edge, crf in (("轻量 · 720P", 1280, 28), ("均衡（推荐）", 1920, 23),
                                 ("高画质 · 原始分辨率", 0, 18)):
            self.preset.addItem(title, (edge, crf))
            self.preset.setItemData(self.preset.count() - 1, f"CRF {crf} · {'原始尺寸' if not edge else f'最长边 {edge} px'}",
                                    Qt.ItemDataRole.ToolTipRole)
        self.preset.addItem("自定义", None)
        self.preset.setCurrentIndex(1)
        self.preset.setMinimumWidth(150)
        self.preset.setToolTip("一次套用分辨率与质量；手动改参数后会自动落到「自定义」。")
        self.preset.currentIndexChanged.connect(self._apply_preset)
        self.preset.currentIndexChanged.connect(self._update_mode_hint)

        self.mode_card = QFrame()
        self.mode_card.setObjectName("exportModeCard")
        self.mode_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        mode = QVBoxLayout(self.mode_card)
        mode.setContentsMargins(10, 6, 10, 6)
        mode.setSpacing(3)
        mode_head = QHBoxLayout()
        mode_head.setSpacing(8)
        mode_head.addWidget(_icon_label("layers", 22, C.accent.name()))
        mode_head.addWidget(_text("导出模式", name="cardTitle"))
        mode_head.addStretch(1)
        mode_head.addWidget(self.preset)
        mode.addLayout(mode_head)
        self.mode_hint = _text("快速应用一组分辨率与质量参数", name="cardHint")
        self.mode_hint.setContentsMargins(29, 0, 0, 0)
        self.mode_hint.setWordWrap(True)
        mode.addWidget(self.mode_hint)
        self._update_mode_hint()
        cells[self.preset] = self.mode_card
        export.addWidget(self.mode_card)

        # ---- 2. 基础输出参数 ---------------------------------------------
        basic = QVBoxLayout()
        basic.setContentsMargins(0, 0, 0, 0)
        basic.setSpacing(2)
        self.container = _combo()
        self.container.setToolTip("输出容器：决定文件后缀与兼容性")
        self.codec = _combo()
        self.codec.setToolTip("视频编码器：H.264 兼容性最好，H.265 体积更小")
        self.container.currentIndexChanged.connect(self._pick_container)
        self.codec.currentIndexChanged.connect(self._pick_codec)
        self.format.hide()
        format_holder = QWidget()
        format_holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        format_line = QHBoxLayout(format_holder)
        format_line.setContentsMargins(0, 0, 0, 0)
        format_line.setSpacing(6)
        format_line.addWidget(self.container, 1)
        format_line.addWidget(self.format, 1)
        self.format_row = row("输出格式", format_holder)
        self.codec_row = row("视频编码", self.codec)
        basic.addWidget(self.format_row)
        basic.addWidget(self.codec_row)
        self.acceleration = _combo()
        self.acceleration.setAccessibleName("硬件加速")
        self.acceleration.setToolTip("只列出当前 FFmpeg 构建包含的硬件编码器；设备或驱动不可用时会自动回退 CPU。")
        self.acceleration.currentIndexChanged.connect(self._remember_acceleration)
        self.acceleration_label = _text("硬件加速")
        self.acceleration_row = row("硬件加速", self.acceleration)
        basic.addWidget(self.acceleration_row)
        self.export_edge = _combo()
        for title, edge in (("原始分辨率", 0), *VIDEO_RESOLUTIONS):
            self.export_edge.addItem(f"{_friendly_edge_title(edge)}", edge)
            self.export_edge.setItemData(self.export_edge.count() - 1,
                                          f"最长边 {edge} px，保持比例" if edge else "保留视频的原始像素尺寸",
                                          Qt.ItemDataRole.ToolTipRole)
        for edge in (480, 256):
            self.export_edge.addItem(f"最长边 {edge} px", edge)
        self.export_edge.addItem("自定义最长边…", -1)
        self.export_edge.activated.connect(self._custom_video_edge)
        self.export_edge.setToolTip("按原画面比例缩放，竖屏视频保持竖屏")
        self.export_edge.currentIndexChanged.connect(self._update_export_dimensions)
        self.resolution_row = row("分辨率", self.export_edge)
        basic.addWidget(self.resolution_row)
        self.dimension_note = _text("", name="paramHint", muted=True)
        self.export_dimensions = self.dimension_note  # 兼容旧属性名
        self.resolution_row.layout().addWidget(self.dimension_note)
        self.dimension_note.setVisible(False)
        self.output_fps = _combo()
        fps_label = f"原帧率 · {info.fps:g} FPS" if isinstance(info, VideoInfo) and info.fps else "保持原帧率"
        self.output_fps.addItem(fps_label, 0)
        for rate in VIDEO_FRAME_RATES:
            self.output_fps.addItem(f"{rate} FPS", rate)
        self.output_fps.setToolTip("设置导出视频的每秒帧数，保持播放速度")
        self.output_fps.currentIndexChanged.connect(self._refresh_estimate)
        self.fps_row = row("帧率", self.output_fps)
        basic.addWidget(self.fps_row)
        cells[self.export_edge] = self.resolution_row
        cells[self.dimension_note] = self.dimension_note
        export.addLayout(basic)

        # ---- 3. 质量微调 -------------------------------------------------
        self.quality = QualityRow("质量微调", 18, 34, 23,
                                  hint="CRF 越小画质越高，文件通常越大。",
                                  icon_name="sliders")
        self.quality.setToolTip("CRF 越小越清晰、文件越大；22–26 是日常分享的常用区间。")
        self.estimate_throttle = QTimer(self)
        self.estimate_throttle.setSingleShot(True)
        self.estimate_throttle.setInterval(120)
        self.estimate_throttle.timeout.connect(self._refresh_estimate)
        self.quality.changed.connect(self._schedule_estimate_tick)
        self.quality.settled.connect(self._refresh_estimate)
        self.quality.settled.connect(self._sync_preset)
        self.export_edge.currentIndexChanged.connect(self._sync_preset)
        self.quality_card = QFrame()
        self.quality_card.setObjectName("qualityCard")
        self.quality_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        quality_layout = QVBoxLayout(self.quality_card)
        quality_layout.setContentsMargins(10, 6, 10, 6)
        quality_layout.setSpacing(1)
        quality_layout.addWidget(self.quality)
        cells[self.quality] = self.quality_card
        export.addWidget(self.quality_card)

        # ---- 4. 音频参数 -------------------------------------------------
        audio_box = QVBoxLayout()
        audio_box.setContentsMargins(0, 0, 0, 0)
        audio_box.setSpacing(2)
        self.audio_codec_text = _text("AAC", name="paramValue")
        self.audio_codec_holder = QWidget()
        self.audio_codec_holder.setObjectName("codecDisplay")
        self.audio_codec_holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        codec_line = QHBoxLayout(self.audio_codec_holder)
        codec_line.setContentsMargins(9, 0, 9, 0)
        self.audio_codec_holder.setMinimumHeight(30)
        codec_line.addWidget(self.audio_codec_text)
        codec_line.addStretch(1)
        self.audio_codec_row = row("音频编码", self.audio_codec_holder)
        audio_box.addWidget(self.audio_codec_row)
        self.audio_bitrate = _combo()
        for rate in (96, 128, 192, 256, 320):
            self.audio_bitrate.addItem(f"{rate} kbps", rate)
        self.audio_bitrate.setCurrentIndex(self.audio_bitrate.findData(192))
        self.audio_bitrate.setToolTip("只影响带音轨的视频；无损容器不受影响。")
        self.audio_bitrate.currentIndexChanged.connect(self._refresh_estimate)
        self.audio_row = row("音频码率", self.audio_bitrate)
        audio_box.addWidget(self.audio_row)
        self.audio_sample_rate = _combo()
        self.audio_sample_rate.addItem("保持源采样率", 0)
        for rate in (22050, 44100, 48000):
            self.audio_sample_rate.addItem(f"{rate / 1000:g} kHz", rate)
        self.audio_channels = _combo()
        for title, count in (("保持源声道", 0), ("单声道", 1), ("立体声", 2)):
            self.audio_channels.addItem(title, count)
        self.sample_row = row("采样率", self.audio_sample_rate)
        self.channels_row = row("声道", self.audio_channels)
        self.audio_advanced = QWidget()
        self.audio_advanced.setObjectName("audioAdvancedOptions")
        audio_advanced_layout = QVBoxLayout(self.audio_advanced)
        audio_advanced_layout.setContentsMargins(0, 0, 0, 0)
        audio_advanced_layout.setSpacing(2)
        audio_advanced_layout.addWidget(self.sample_row)
        audio_advanced_layout.addWidget(self.channels_row)
        audio_box.addWidget(self.audio_advanced)
        cells[self.audio_bitrate] = self.audio_row
        export.addLayout(audio_box)

        self.audio_quality = QualityRow(
            "质量微调", values=[320, 256, 192, 128, 96], unit="kbps", value=192,
            hint="码率越高通常音质越好，文件体积越大。", icon_name="wave",
            lossless_hint="WAV / FLAC 为无损格式，不提供有损质量调节。")
        self.audio_quality.changed.connect(self._audio_bitrate_preview)
        self.audio_quality.changed.connect(self._schedule_estimate_tick)
        self.audio_quality.settled.connect(self._audio_quality_changed)
        self.audio_quality_card = QFrame()
        self.audio_quality_card.setObjectName("qualityCard")
        self.audio_quality_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        audio_quality_layout = QVBoxLayout(self.audio_quality_card)
        audio_quality_layout.setContentsMargins(11, 10, 11, 10)
        audio_quality_layout.setSpacing(6)
        audio_quality_layout.addWidget(self.audio_quality)
        self.audio_quality_card.hide()
        export.addWidget(self.audio_quality_card)

        self.image_quality = QualityRow(
            "质量微调", values=[100, 90, 80, 60, 40], unit="%", value=90,
            hint="质量越高通常细节越清晰，文件体积越大。", icon_name="image",
            lossless_hint="PNG 为无损格式，不提供有损质量调节。")
        self.image_quality.changed.connect(self._schedule_estimate_tick)
        self.image_quality.settled.connect(self._refresh_estimate)
        self.image_quality_card = QFrame()
        self.image_quality_card.setObjectName("qualityCard")
        self.image_quality_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        image_quality_layout = QVBoxLayout(self.image_quality_card)
        image_quality_layout.setContentsMargins(11, 10, 11, 10)
        image_quality_layout.setSpacing(6)
        image_quality_layout.addWidget(self.image_quality)
        self.image_quality_card.hide()
        # 图片页展示的是 picture_group，质量卡挂在它内部才看得见
        self.picture_group.layout().addWidget(self.image_quality_card)

        self.export_limit = _spin()
        self.export_limit.setRange(0, 10_000_000)
        self.export_limit.setSingleStep(100)
        self.export_limit.setSuffix(" KB")
        self.export_limit.setSpecialValueText("不限制")
        self.export_limit.valueChanged.connect(self._refresh_estimate)
        self.limit_toggle = QPushButton("目标体积")
        self.limit_toggle.setObjectName("estimateLimitToggle")
        self.limit_toggle.setAutoDefault(False)
        self.limit_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.limit_toggle.setFixedHeight(20)
        self.limit_toggle.setToolTip("可选设置输出体积上限。编码器会尽量接近目标大小，实际结果可能有差异。")
        self.limit_toggle.clicked.connect(self._toggle_video_limit)
        self.audio_limit_toggle = QPushButton("目标体积")
        self.audio_limit_toggle.setObjectName("estimateLimitToggle")
        self.audio_limit_toggle.setAutoDefault(False)
        self.audio_limit_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.audio_limit_toggle.setFixedHeight(20)
        self.audio_limit_toggle.setToolTip(self.limit_toggle.toolTip())
        self.audio_limit_toggle.clicked.connect(self._toggle_video_limit)
        self.audio_limit_toggle.hide()
        audio_box.addWidget(self.audio_limit_toggle)
        self.audio_limit_toggle.hide()
        self.limit_toggle.hide()
        self.limit_row = row("目标体积", self.export_limit)
        self.limit_open = False

        # ---- 5. 预计输出体积 ---------------------------------------------
        self.estimate_card = QFrame()
        self.estimate_card.setObjectName("estimatedSizeCard")
        self.estimate_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        estimate = QVBoxLayout(self.estimate_card)
        estimate.setContentsMargins(0, 0, 0, 0)
        estimate.setSpacing(0)
        estimate_head = QFrame()
        estimate_head.setObjectName("estimatedSizeHeader")
        estimate_head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.estimate_head = estimate_head
        estimate_head.setFixedHeight(24)
        head_line = QHBoxLayout(estimate_head)
        head_line.setContentsMargins(12, 0, 12, 0)
        head_line.setSpacing(7)
        head_line.addWidget(_icon_label("layers", 17))
        head_line.addWidget(_text("预计输出体积", name="cardTitle"))
        head_line.addStretch(1)
        head_line.addWidget(self.limit_toggle)
        estimate.addWidget(estimate_head)
        self.estimate_card.setToolTip(
            "按源文件大小、输出分辨率、质量和音频码率粗略估算；实际大小会随画面内容及编码器变化。")
        self.estimate_rows = {}
        body = QGridLayout()
        body.setContentsMargins(10, 3, 10, 5)
        body.setHorizontalSpacing(8)
        body.setVerticalSpacing(2)
        body.setColumnStretch(0, 1)
        body.setColumnStretch(1, 1)
        for row_index, (key, title) in enumerate((("source", "原始大小"), ("output", "预计输出"),
                                                  ("delta", "预计变化"))):
            label = _text(title, name="estimateLabel")
            value = _text("—", name="estimateValueStrong" if key == "output" else "estimateValue")
            if key == "delta":
                value.setObjectName("estimateDelta")
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.estimate_rows[key] = value
            body.addWidget(label, row_index, 0)
            body.addWidget(value, row_index, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        estimate.addLayout(body)
        cells[self.estimate_card] = self.estimate_card

        # ---- 6. 保存目录 --------------------------------------------------
        self.directory_group = QFrame()
        self.directory_group.setObjectName("saveDirectoryCard")
        self.directory_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        directory = QVBoxLayout(self.directory_group)
        directory.setContentsMargins(0, 0, 0, 0)
        directory.setSpacing(5)
        self.output_file_name = _text("正在准备输出文件名…", name="cardHint")
        self.output_file_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.output_file_name.setToolTip("如果同名文件已存在，程序会自动追加序号，不会覆盖原文件。")
        self.reset_path = _text_button("恢复源目录", self._reset_output_directory)
        self.reset_path.setVisible(self.directory != self.path.parent)
        directory.addWidget(caption("folder", "保存目录", self.reset_path))
        path_card = QFrame()
        path_card.setObjectName("outputPathCard")
        path_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        path_card.setFixedHeight(36)
        card_layout = QHBoxLayout(path_card)
        card_layout.setContentsMargins(10, 0, 6, 0)
        card_layout.setSpacing(8)
        self.output_icon = _icon_label("folder", 18)
        self.output_icon.setFixedSize(28, 28)
        self.output_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.output_icon)
        self.directory_path = DirectoryPathEdit(str(self.directory))
        card_layout.addWidget(self.directory_path, 1)
        self.directory_path.clicked.connect(self.choose_directory)
        self.directory_path.setToolTip("单击选择输出目录")
        directory.addWidget(path_card)
        self.output_file_name.hide()

        cells[self.preset] = self.mode_card
        cells[self.export_edge] = self.resolution_row
        cells[self.quality] = self.quality_card
        cells[self.output_fps] = self.fps_row
        cells[self.acceleration] = self.acceleration_row
        self.export_grid = None
        self.export_fields = _ExportCells(cells)
        self.format.currentIndexChanged.connect(self._refresh_acceleration)
        self.format.currentIndexChanged.connect(self._refresh_audio_codec)
        self.format.currentIndexChanged.connect(self._refresh_estimate)
        self.format.currentIndexChanged.connect(self._update_output_location)
        self.format.currentIndexChanged.connect(self._format_changed)
        self.acceleration.currentIndexChanged.connect(self._sync_quality_encoder)
        self.acceleration.currentIndexChanged.connect(self._refresh_estimate)
        self.format.setMinimumContentsLength(12)
        self.format.setSizeAdjustPolicy(self.format.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.gif_profile = _combo()
        self.gif_profile.addItem("小体积 · 8 FPS", True)
        self.gif_profile.addItem("清晰 · 12 FPS", False)
        self.gif_row = row("GIF 设置", self.gif_profile)
        self.gif_row.hide()
        export.insertWidget(2, self.gif_row)
        export.addWidget(self.limit_row)

        self.panel = QFrame()
        self.panel.setObjectName("exportSettingsPanel")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel_box = QVBoxLayout(self.panel)
        panel_box.setContentsMargins(0, 0, 0, 0)
        panel_box.setSpacing(6)
        self.inspector_title = caption("gear", "输出设置")
        self.inspector_title.setObjectName("exportPanelHeader")
        self.inspector_title.setMinimumHeight(27)
        side.insertWidget(0, self.inspector_title)
        panel_box.addWidget(self.export_group)
        stack.addWidget(self.panel)
        stack.addWidget(self.estimate_card)
        stack.addWidget(self.directory_group)
        stack.addStretch(1)
        self.workspace_split.addWidget(inspector)
        self.workspace_split.setStretchFactor(0, 1)
        self.workspace_split.setSizes([max(420, self.width() - 394), 394])

        self.timeline = QFrame()
        self.timeline.setObjectName("timelineCard")
        timeline = QVBoxLayout(self.timeline)
        timeline.setContentsMargins(9, 5, 9, 7)
        timeline.setSpacing(6)
        if self.video_page is not None:
            self.timeline_header = QBoxLayout(QBoxLayout.Direction.LeftToRight)
            self.timeline_header.setSpacing(3)
            timeline_title = QHBoxLayout()
            timeline_title.addWidget(_icon_label("timeline", 17, C.accent.name()))
            timeline_title.addWidget(_text("时间轴", name="studioSection"))
            self.timeline_hint = _text("左键拖选 · 右键拖动移动帧", name="studioHint")
            self.timeline_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.timeline_hint.setToolTip(self.timeline_hint.text())
            timeline_title.addWidget(self.timeline_hint, 2)
            self.selection_label = _text("", name="studioKicker")
            self.selection_label.setToolTip("拖动或按住 Ctrl 点缩略图选帧；右键可复制、剪切、另存")
            timeline_title.addWidget(self.selection_label, 1)
            self.timeline_header.addLayout(timeline_title, 1)
            locate = QHBoxLayout()
            locate.setSpacing(5)
            self.timeline_zoom = QSlider(Qt.Orientation.Horizontal)
            self.timeline_zoom.setObjectName("timelineZoom")
            self.timeline_zoom.setRange(0, 4)
            self.timeline_zoom.setValue(4)
            self.timeline_zoom.setFixedWidth(61)
            self.timeline_zoom.setAccessibleName("时间轴缩放")
            self.timeline_zoom.setToolTip("左侧全片概览，右侧连续逐帧。也可按住 Ctrl 滚轮缩放。")
            locate.addWidget(_text("缩放", name="studioHint"))
            locate.addWidget(self.timeline_zoom)
            locate.addWidget(_text("定位", name="studioSection"))
            self.locate_input = QLineEdit()
            self.locate_input.setObjectName("frameLocate")
            self.locate_input.setPlaceholderText("输入帧号")
            self.locate_input.setAccessibleName("定位帧号")
            self.locate_input.setValidator(QIntValidator(1, 2_147_483_647, self.locate_input))
            self.locate_input.setFixedWidth(86)
            self.locate_input.setEnabled(False)
            self.locate_button = _button("跳转", self._jump_to_frame)
            self.locate_button.setEnabled(False)
            self.locate_input.returnPressed.connect(self._jump_to_frame)
            self.locate_input.textChanged.connect(self._update_locate_button)
            locate.addWidget(self.locate_input)
            locate.addWidget(self.locate_button)
            self.timeline_header.addLayout(locate)
            timeline.addLayout(self.timeline_header)
            self.filmstrip = Filmstrip(self.tools, self._video_drop_preview, parent=self.timeline)
            self.timeline_zoom.valueChanged.connect(self.filmstrip.set_zoom)
            self.filmstrip.zoom_changed.connect(self.timeline_zoom.setValue)
            self.filmstrip.media_dropped.connect(self._drop_video)
            self.filmstrip.frames_moved.connect(self._move_video_ranges)
            self.frame_context_menu = QMenu(self.filmstrip)
            self.filmstrip.frame_selected.connect(self._locate_frame)
            self.filmstrip.frame_clicked.connect(self._click_frame)
            self.filmstrip.frame_dragged.connect(self._drag_select_frames)
            self.filmstrip.context_requested.connect(self._show_frame_context_menu)
            self.filmstrip.edit_command.connect(self._timeline_command)
            timeline.addWidget(self.filmstrip)
        self.preview_split.addWidget(self.timeline)
        if self.video_page is not None:
            self._build_media_summary()
            self.preview_split.addWidget(self.media_summary)
        self.preview_split.setStretchFactor(0, 1)
        self.preview_split.setStretchFactor(1, 0)
        self.preview_split.setStretchFactor(2, 0)

        self.progress_row = QWidget()
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        progress_layout.addWidget(self.progress, 1)
        self.cancel_button = _button("取消任务", self.cancel_operation)
        self.cancel_button.setEnabled(False)
        progress_layout.addWidget(self.cancel_button)
        self.progress_row.hide()
        # 运行日志不再占一条底栏：默认只留一行提示，进度条只在导出时出现。
        self.status = RunStatus()
        self.status.setReadOnly(True)
        self.status.hide()
        self.notice = _text("", name="editorNotice")
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.notice.hide()
        if self.embedded:
            self.notice.setParent(self)
            self.progress_row.setParent(self)
        else:
            side.addWidget(self.progress_row)
            side.insertWidget(1, self.notice)
        for splitter, tooltip, cursor in (
            (self.workspace_split, "拖动边界调整预览区和右侧设置的宽度", Qt.CursorShape.SplitHCursor),
            (self.preview_split, "拖动边界调整预览区和时间线的高度", Qt.CursorShape.SplitVCursor),
        ):
            handle = splitter.handle(1)
            handle.setToolTip(tooltip)
            handle.setCursor(cursor)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.workspace_split.splitterMoved.connect(self._update_timeline_header)
        self._tab_changed()
        self.preview_split.setSizes([900, 110, 90] if self.embedded else [900, 132, 108])
        self._update_output_location()
        self._refresh_acceleration()
        self._fill_presets()
        self._refresh_audio_codec()
        self._refresh_estimate()
        self._sync_preset()
        for combo in (self.format, self.export_edge, self.output_fps, self.acceleration, self.audio_bitrate,
                      self.image_format, self.resolution, self.gif_profile,
                      self.audio_sample_rate, self.audio_channels):
            combo.currentIndexChanged.connect(self._notify_settings)
        for field in (self.export_limit, self.size, self.picture_preset, self.quality):
            signal = field.currentChanged if field is self.picture_preset else (
                field.changed if field is self.quality else field.valueChanged)
            signal.connect(self._notify_settings)

    def _build_media_summary(self):
        self.media_summary.setObjectName("mediaInfoCard")
        self.media_summary.setAccessibleName("媒体信息")
        layout = QVBoxLayout(self.media_summary)
        layout.setContentsMargins(0, 0, 0, 5)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("mediaInfoHeader")
        header.setFixedHeight(29)
        title = QHBoxLayout(header)
        title.setContentsMargins(10, 0, 10, 0)
        title.setSpacing(6)
        title.addWidget(_icon_label("info", 18))
        title.addWidget(_text("媒体信息", name="cardTitle"))
        title.addStretch(1)
        layout.addWidget(header)

        info = self.info
        audio_codec = (info.audio_codec if info.audio_stream is not None else None) or "无音轨"
        bitrate = _rate_text(info.bitrate)
        sample_rate = f"{info.sample_rate / 1000:g} kHz" if info.sample_rate else "—"
        rows = (
            ("image", "基本信息",
             (f"{info.width} × {info.height}", f"{info.fps:g} FPS" if info.fps else "—",
              f"{info.duration:.3f} 秒", _size_text(info.size))),
            ("gear", "编码信息",
             (info.codec or "—", bitrate, audio_codec, sample_rate)),
        )
        self.media_info_rows = []
        for mark, label, values in rows:
            row = QHBoxLayout()
            row.setContentsMargins(10, 4, 10, 4)
            row.setSpacing(7)
            icon = _icon_label(mark, 16)
            row.addWidget(icon)
            heading = _text(label, name="mediaInfoHeading")
            heading.setFixedWidth(54)
            row.addWidget(heading)
            for index, value in enumerate(values):
                if index:
                    rule = QFrame()
                    rule.setObjectName("mediaInfoRule")
                    rule.setFrameShape(QFrame.Shape.VLine)
                    rule.setFixedSize(1, 13)
                    row.addWidget(rule)
                chip = InfoChip(value)
                chip.setObjectName("mediaInfoValue")
                chip.setToolTip(value if mark == "image" else
                                f"{('视频编码', '视频码率', '音频编码', '采样率')[index]}：{value}")
                chip.setMinimumWidth(0)
                chip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
                chip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                row.addWidget(chip, 1)
            row.addStretch(0)
            self.media_info_rows.append((row, icon, heading))
            layout.addLayout(row)

    def _refresh_acceleration(self, *_):
        if not hasattr(self, "acceleration"):
            return
        kind = self.format.currentData()
        current = self.selected_acceleration
        choices = hardware_acceleration_choices(
            self.tools.ffmpeg if self.tools is not None else "", kind or ""
        )
        with QSignalBlocker(self.acceleration):
            self.acceleration.clear()
            for key, title in choices:
                self.acceleration.addItem(title, key)
            index = self.acceleration.findData(current)
            self.acceleration.setCurrentIndex(index if index >= 0 else 0)
        video_format = kind in _ENCODERS and kind != "gif"
        self.export_fields.setRowVisible(self.acceleration, video_format)
        self.export_fields.setRowVisible(self.output_fps, video_format)
        self.export_fields.setRowVisible(self.export_edge, video_format or kind == "gif")
        self._update_export_dimensions()

    def _remember_acceleration(self, *_):
        self.selected_acceleration = self.acceleration.currentData() or "cpu"

    def _update_export_dimensions(self, *_):
        edge = self.export_edge.currentData()
        kind = self.format.currentData() or self.codec.currentData()
        show = kind in _ENCODERS and bool(edge and edge > 0) and isinstance(self.info, VideoInfo)
        if show:
            width, height = self.info.width, self.info.height
            scale = edge / max(1, width, height)
            out_width = max(2, int(width * scale / 2 + .5) * 2)
            out_height = max(2, int(height * scale / 2 + .5) * 2)
            self.dimension_note.setText(f"实际输出 {out_width} × {out_height}")
        self.export_edge.setAccessibleDescription(self.dimension_note.text() if show else "")
        self.export_fields.setRowVisible(self.export_dimensions, show and self.settings_body.width() >= 337)
        self._refresh_estimate()

    _CONTAINER_TITLES = {"mp4": "MP4", "mp4_hevc": "MP4", "mov": "MOV", "mkv": "MKV", "mkv_hevc": "MKV",
                         "webm": "WEBM", "avi": "AVI"}
    _CODEC_TITLES = {"mp4": "H.264 (AVC)", "mov": "H.264 (AVC)", "mkv": "H.264 (AVC)",
                     "mkv_hevc": "H.265 (HEVC)", "mp4_hevc": "H.265 (HEVC)", "webm": "VP9", "avi": "MPEG-4"}

    def _uses_video_picker(self):
        return isinstance(self.info, VideoInfo) and (
            self.configuration_only or self.tabs.currentWidget() is self.video_page)

    @classmethod
    def _container_for(cls, key):
        return cls._CONTAINER_TITLES.get(key, "GIF" if key == "gif" else "提取音频")

    def _load_formats(self):
        video = self._uses_video_picker()
        audio = not video or self.info.audio_stream is not None
        keys = [*_ENCODERS, "gif"] if video else []
        if audio:
            keys.extend(_AUDIO_ENCODERS)
        key = self.media_format if video or isinstance(self.info, AudioInfo) else self.audio_tab_format
        with QSignalBlocker(self.format):
            self.format.clear()
            for item in keys:
                self.format.addItem({"m4a": "M4A · AAC", "ogg": "OGG · Vorbis"}.get(item, item.upper()), item)
            self.format.setCurrentIndex(max(0, self.format.findData(key)))

    def set_output_format(self, kind):
        if isinstance(self.info, ImageInfo):
            index = self.image_format.findData(kind)
            if index >= 0 and index != self.image_format.currentIndex():
                with QSignalBlocker(self.image_format):
                    self.image_format.setCurrentIndex(index)
                self._update_output_location()
                self._schedule_picture_estimate()
            return
        if kind == self.media_format:
            return
        self.media_format = kind
        self._load_formats()
        self._sync_export_rows()

    def _format_changed(self, *_):
        kind = self.format.currentData()
        if self._uses_video_picker() or isinstance(self.info, AudioInfo):
            self.media_format = kind
        else:
            self.audio_tab_format = kind
        self._sync_export_rows()

    def _sync_format_pickers(self):
        video = self._uses_video_picker()
        self.container.setVisible(video)
        self.format.setVisible(not video)
        if not video:
            self.codec_row.hide()
            return
        key = self.format.currentData()
        groups = dict.fromkeys(self._container_for(self.format.itemData(i)) for i in range(self.format.count()))
        self._syncing_format = True
        try:
            with QSignalBlocker(self.container):
                self.container.clear()
                for title in groups:
                    self.container.addItem(title, title)
                self.container.setCurrentIndex(self.container.findData(self._container_for(key)))
            self._fill_codec()
            with QSignalBlocker(self.codec):
                self.codec.setCurrentIndex(max(0, self.codec.findData(key)))
        finally:
            self._syncing_format = False
        self.codec_row.layout().itemAt(0).widget().setText("音频格式" if key in _AUDIO_ENCODERS else "视频编码")
        self.codec_row.setVisible(key != "gif")

    def _fill_codec(self):
        group = self.container.currentData()
        with QSignalBlocker(self.codec):
            self.codec.clear()
            for index in range(self.format.count()):
                kind = self.format.itemData(index)
                if self._container_for(kind) == group:
                    self.codec.addItem(self._CODEC_TITLES.get(kind, kind.upper()), kind)

    def _pick_container(self, *_):
        if getattr(self, "_syncing_format", False):
            return
        self._fill_codec()
        self._pick_codec()

    def _pick_codec(self, *_):
        if getattr(self, "_syncing_format", False):
            return
        key = self.codec.currentData()
        index = self.format.findData(key) if key else -1
        if index >= 0 and index != self.format.currentIndex():
            self.format.setCurrentIndex(index)

    def _refresh_audio_codec(self, *_):
        kind = self.format.currentData()
        codec = {"webm": "Opus", "avi": "MP3", "gif": "无音轨", "mp3": "MP3",
                 "wav": "PCM", "flac": "FLAC", "opus": "Opus", "ogg": "Vorbis"}.get(kind, "AAC")
        if hasattr(self, "audio_codec_text"):
            self.audio_codec_text.setText(codec)
            self.audio_bitrate.setEnabled(codec != "无音轨")
        if hasattr(self, "audio_sample_rate"):
            previous = self.audio_sample_rate.currentData()
            rates = (24000, 48000) if kind == "opus" else (22050, 44100, 48000)
            with QSignalBlocker(self.audio_sample_rate):
                self.audio_sample_rate.clear()
                self.audio_sample_rate.addItem("保持源采样率", 0)
                for rate in rates:
                    self.audio_sample_rate.addItem(f"{rate / 1000:g} kHz", rate)
                self.audio_sample_rate.setCurrentIndex(max(0, self.audio_sample_rate.findData(previous)))

    _VIDEO_PRESETS = (("轻量 · 720P", 1280, 28), ("均衡（推荐）", 1920, 23),
                      ("高画质 · 原始分辨率", 0, 18))
    _AUDIO_PRESETS = (("小体积 · 128k", 128), ("标准 · 192k", 192), ("高音质 · 320k", 320))

    def _fill_presets(self):
        """按当前媒体类型重建导出模式下拉，避免音频看到视频档位。"""
        if not hasattr(self, "preset"):
            return
        audio = not isinstance(self.info, VideoInfo) and isinstance(self.info, AudioInfo)
        blocker = QSignalBlocker(self.preset)
        self.preset.clear()
        if audio:
            for title, bitrate in self._AUDIO_PRESETS:
                self.preset.addItem(title, ("audio", bitrate))
        else:
            for title, edge, crf in self._VIDEO_PRESETS:
                self.preset.addItem(title, (edge, crf))
        self.preset.addItem("自定义", None)
        self.preset.setCurrentIndex(1 if self.preset.count() > 2 else 0)
        del blocker

    def _apply_preset(self, *_):
        data = self.preset.currentData()
        if isinstance(data, tuple) and data and data[0] == "audio":
            self.audio_bitrate.setCurrentIndex(
                max(0, self.audio_bitrate.findData(data[1])))
            self._refresh_estimate()
            return
        data = self.preset.currentData()
        if not data or not hasattr(self, "quality"):
            return
        edge, crf = data
        with QSignalBlocker(self.export_edge), QSignalBlocker(self.quality), \
                QSignalBlocker(self.preset):
            index = self.export_edge.findData(edge)
            if index >= 0:
                self.export_edge.setCurrentIndex(index)
            self.quality.setValue(crf)
        self._update_export_dimensions()
        self._notify_settings()

    def _update_mode_hint(self, *_):
        if not hasattr(self, "mode_hint"):
            return
        index = self.preset.currentIndex()
        if isinstance(self.info, AudioInfo) and not isinstance(self.info, VideoInfo):
            descriptions = {
                0: "128 kbps，体积最小，适合语音与人声。",
                1: "192 kbps，通用音乐与视频配乐都够用。",
                2: "320 kbps，接近无损听感，文件更大。",
            }
        else:
            descriptions = {
                0: "降低输出分辨率并提高压缩率，通常可减小文件。",
                1: "兼顾画质、文件体积与常用播放器兼容性。",
                2: "保留原始分辨率并优先画面细节，文件通常更大。",
            }
        hint = descriptions.get(index, "手动设置编码、分辨率与帧率")
        if hasattr(self, "acceleration") and self.acceleration.currentData() == "mf":
            hint += " 当前硬件编码器使用固定质量控制。"
        self.mode_hint.setText(hint)

    def _sync_quality_encoder(self, *_):
        if not hasattr(self, "quality"):
            return
        kind = self.format.currentData()
        backend = self.acceleration.currentData() or "cpu"
        measure = {"cpu": "CRF", "nvenc": "CQ", "amf": "QP", "qsv": "质量"}.get(backend, "")
        supported = kind in _ENCODERS and kind != "avi" and bool(measure)
        self.quality.set_measure(measure or "质量")
        if hasattr(self, "quality_card"):
            self.export_fields.setRowVisible(self.quality_card, supported and kind != "avi")
        self._update_mode_hint()

    def _sync_preset(self, *_):
        """手动改分辨率或质量时，预设跟着落到最接近的一项，否则显示「自定义」。"""
        if not hasattr(self, "preset"):
            return
        edge = self.export_edge.currentData() or 0
        crf = self.quality.value()
        for index in range(self.preset.count() - 1):
            data = self.preset.itemData(index)
            if data and data[0] == edge and data[1] == crf:
                if self.preset.currentIndex() != index:
                    with QSignalBlocker(self.preset):
                        self.preset.setCurrentIndex(index)
                self._update_mode_hint()
                return
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(self.preset.count() - 1)
        self._update_mode_hint()

    def _schedule_estimate_tick(self, *_value) -> None:
        """拖动过程中按 120ms 节流重算体积，松手时由 settled 再算一次。"""
        timer = getattr(self, "estimate_throttle", None)
        if timer is not None:
            timer.start()

    def _refresh_estimate(self, *_):
        """按「原文件体积 × CRF 比例 × 像素比」粗估成品体积，明确标注为估算。"""
        if not hasattr(self, "estimate_rows"):
            return
        if isinstance(self.info, AudioInfo):
            duration = self.info.duration or 0
            source = self.info.size or 0
            bitrate = 0
            if hasattr(self, "audio_bitrate"):
                try:
                    bitrate = int(self.audio_bitrate.currentData() or 0)
                except (TypeError, ValueError):
                    bitrate = 0
            if not duration or not bitrate:
                for value in self.estimate_rows.values():
                    value.setText("—")
                return
            self._apply_estimate(source, bitrate * 1000 / 8 * duration)
            return
        if not isinstance(self.info, VideoInfo):
            return
        source = self.info.size or 0
        duration = self.info.duration or 0
        if not source or not duration:
            for value in self.estimate_rows.values():
                value.setText("—")
            return
        quality = self.quality.value() if hasattr(self, "quality") else 23
        quality_ratio = 2 ** ((23 - quality) / 6) if self.format.currentData() != "avi" else 1.0
        edge = self.export_edge.currentData() or 0
        pixel_ratio = 1.0
        if edge and self.info.width and self.info.height:
            longest = max(self.info.width, self.info.height)
            if longest > edge:
                pixel_ratio = (edge / longest) ** 1.4
        has_audio = isinstance(self.info, VideoInfo) and self.info.audio_stream is not None
        bitrate = self.audio_bitrate.currentData() if has_audio and hasattr(self, "audio_bitrate") else 0
        audio_bytes = (bitrate or 0) * 1000 / 8 * duration
        base_audio = 192 * 1000 / 8 * duration if has_audio else 0
        fps = self.output_fps.currentData() if hasattr(self, "output_fps") else 0
        fps_ratio = (min(1.5, fps / self.info.fps) if fps and self.info.fps else 1.0)
        estimate = source * quality_ratio * pixel_ratio * fps_ratio + audio_bytes - base_audio
        limit = self.export_limit.value() * 1000 if hasattr(self, "export_limit") else 0
        if limit:
            estimate = min(estimate, limit)
        estimate = max(16_000, estimate)
        self._apply_estimate(source, estimate)

    def _apply_estimate(self, source: float, estimate: float) -> None:
        """把原始/预计/变化三行写进体积卡，供音频与视频两条路径复用。"""
        source = max(1, int(source or 0))
        estimate = max(1, int(estimate or 0))
        self.estimate_rows["source"].setText(_size_text(source))
        self.estimate_rows["output"].setText(f"约 {_size_text(estimate)}")
        change = (estimate - source) / source * 100
        if abs(change) < .5:
            text, tone = "基本不变", "same"
        elif change < 0:
            text, tone = f"减少约 {-change:.0f}%", "down"
        else:
            text, tone = f"增加约 {change:.0f}%", "up"
        self.estimate_rows["delta"].setText(text)
        self.estimate_rows["delta"].setProperty("tone", tone)
        self.estimate_rows["delta"].style().unpolish(self.estimate_rows["delta"])
        self.estimate_rows["delta"].style().polish(self.estimate_rows["delta"])

    def _update_output_location(self, *_):
        if not hasattr(self, "output_file_name"):
            return
        page = self.tabs.currentWidget() if hasattr(self, "tabs") else None
        kind = self.image_format.currentData() if page is self.image_page else self.format.currentData()
        extension = output_extension(kind or "mp4")
        if page is self.image_page and self.video_page is not None:
            stem = f"{self.path.stem}_帧{self._picture_frame_number():06}"
        elif page is self.image_page:
            stem = f"{self.path.stem}_图片"
        else:
            stem = f"{self.path.stem}_编辑"
        stem = self._get_output_stem() or stem
        if hasattr(self, "output_stem_ext"):
            self.output_stem_ext.setText(f".{extension}")
        name = f"{stem}.{extension}"
        self.output_file_name.setText(name)

    @staticmethod
    def _splitter(orientation):
        splitter = QSplitter(orientation)
        splitter.setHandleWidth(0)
        splitter.setChildrenCollapsible(False)
        return splitter

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "file_title"):
            self.file_title.setText(self.file_title.fontMetrics().elidedText(
                self.path.name, Qt.TextElideMode.ElideMiddle, self.file_title.width()))
        self._update_timeline_header()

    def _update_timeline_header(self, *_):
        compact = self.preview_split.width() < 415
        self._fit_player_controls()
        if hasattr(self, "timeline_header"):
            direction = (QBoxLayout.Direction.TopToBottom if compact
                         else QBoxLayout.Direction.LeftToRight)
            self.timeline_header.setDirection(direction)
            self.timeline_hint.setVisible(not compact)
        tab_bar = self.tabs.tabBar()
        if tab_bar.property("compact") != compact:
            tab_bar.setProperty("compact", compact)
            tab_bar.style().unpolish(tab_bar)
            tab_bar.style().polish(tab_bar)
            tab_bar.updateGeometry()
        if getattr(self, "_compact_media_info", None) != compact:
            self._compact_media_info = compact
            for row, icon, label in getattr(self, "media_info_rows", ()):
                icon.setVisible(not compact)
                label.setFixedWidth(46 if compact else 54)
                row.setSpacing(3 if compact else 7)
                for index in range(row.count()):
                    widget = row.itemAt(index).widget()
                    if isinstance(widget, InfoChip):
                        row.setStretch(index, 0 if compact else 1)
                row.setStretch(row.count() - 1, 1 if compact else 0)
        if hasattr(self, "export_fields"):
            self._update_export_dimensions()

    def _notice(self, message: str) -> None:
        """一行提示替代原来的底部日志栏：只在有话说的时候出现。"""
        self.status.setText(message)
        self.notice.setText(message)
        self.notice.setVisible(bool(message) and not self.embedded)
        self.task_changed.emit()

    def _page(self, title, mark):
        page = QWidget()
        page.setProperty("mediaIcon", mark)
        self.tabs.addTab(page, icons.icon(mark, 18), title)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        return page, layout

    def _preview_label(self, panel=None):
        panel = panel if panel is not None else PreviewLabel()
        panel.setText("等待预览")
        panel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.setMinimumHeight(140)
        panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        if not isinstance(panel, VideoPreview):
            panel.setStyleSheet(f"background:{C.sunken.name()}; color:{C.text2.name()};"
                                f"border:1px solid {C.border.name()}; border-radius:6px")
        return panel

    def _tab_changed(self, *_):
        page = self.tabs.currentWidget()
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            color = C.accent.name() if tab is page else icons.INK
            self.tabs.setTabIcon(index, icons.icon(tab.property("mediaIcon"), 18, color, 1.9))
        self._stop_playback()
        self._sync_panels()
        if page not in (self.video_page, self.image_page):
            self.picture_estimate_timer.stop()
            self.picture_estimate_generation += 1
            if self.picture_estimate_scope is not None:
                self.picture_estimate_scope.cancel()
                self.picture_estimate_scope = None
        if page is self.audio_page:
            if self.frame_scope is not None:
                self.frame_scope.cancel()
            self._refresh_audio(reset=not self.audio_range_initialized)
            if self.media_started and self._audio_track():
                self.preview_audio()
        else:
            self.stop_audio()
            if self.frames:
                self.shown_frame = self.failed_frame = None
                self._sync_frame_preview()
            if page is self.details_page:
                self._update_file_details()
            elif page is self.image_page:
                self._schedule_picture_estimate()
        self._refresh_acceleration()
        self._update_output_location()
        self.task_changed.emit()

    def _show_picture_page(self):
        if self.image_page is not None:
            self.tabs.setCurrentWidget(self.image_page)

    def mount_settings(self, layout=None):
        if layout is None and not self.configuration_only:
            return
        if layout is not None and self.configuration_only:
            return
        self.configuration_only = layout is not None
        # 转换页复用的只是参数区：保存目录与「保存编辑结果」由主窗口自己的底栏承担
        if layout is not None:
            self.settings_scroll.takeWidget()
            self.settings_body.setStyleSheet(STUDIO_STYLE)
            layout.addWidget(self.settings_body)
            self.settings_body.show()
        else:
            self.settings_body.setStyleSheet("")
            self.settings_scroll.setWidget(self.settings_body)
            self.settings_body.show()
        self._sync_panels()
        if isinstance(self.info, ImageInfo):
            self._schedule_picture_estimate()

    def _sync_panels(self):
        if hasattr(self, "settings_scroll"):
            self.settings_scroll.verticalScrollBar().setValue(0)
        page = self.tabs.currentWidget()
        video = page is self.video_page
        audio = page is self.audio_page
        picture = page is self.image_page
        details = page is self.details_page
        self.timeline.setVisible(video)
        self.media_summary.setVisible(video)
        self.audio_tools.hide()
        self.picture_group.setVisible(isinstance(self.info, ImageInfo) if self.configuration_only else picture)
        self.export_group.setVisible(not isinstance(self.info, ImageInfo) if self.configuration_only else video or audio)
        self.directory_group.setVisible(self.configuration_only or not details)
        # 切页时先为右栏保留原有位置，设置控件在被遮挡时才移回。
        self.inspector.setVisible(not details)
        self._panel_picture = picture
        self._load_formats()
        self._sync_export_rows()

    def fit_settings_density(self, compact):
        # 说明始终保留；窗口变矮时先收掉体积估算卡，避免右栏出现滚动条。
        compact_page = getattr(self, "configuration_only", False)
        self.mode_hint.setVisible(not compact_page)
        if hasattr(self, "quality"):
            self.quality.set_compact(compact_page)
        self.quality.set_compact(False)
        self._compact = bool(compact)
        if hasattr(self, "estimate_card"):
            self._sync_export_rows()

    def _toggle_video_limit(self):
        self.limit_open = not self.limit_open
        toggle_text = "收起" if self.limit_open else "目标体积"
        self.limit_toggle.setText(toggle_text)
        self.audio_limit_toggle.setText(toggle_text)
        self._sync_export_rows()

    def _audio_bitrate_preview(self, kbps: int) -> None:
        """拖动时只更新读数，不做体积估算——估算留到松手后。"""
        if not kbps:
            return

    def _audio_quality_changed(self, kbps: int) -> None:
        """质量滑块的码率写回下拉，导出时仍走同一份 audio_bitrate 参数。"""
        if not kbps:
            return
        index = self.audio_bitrate.findData(int(kbps))
        if index >= 0 and index != self.audio_bitrate.currentIndex():
            blocked = self.audio_bitrate.blockSignals(True)
            self.audio_bitrate.setCurrentIndex(index)
            self.audio_bitrate.blockSignals(blocked)
        self._refresh_estimate()

    def _sync_export_rows(self):
        self._sync_format_pickers()
        kind = self.format.currentData()
        video = kind in _ENCODERS
        gif = kind == "gif"
        audio_only = kind in _AUDIO_ENCODERS
        self.export_fields.setRowVisible(self.mode_card, kind != "avi")
        self.export_fields.setRowVisible(self.quality_card, video and kind != "avi")
        picture_tab = hasattr(self, "image_page") and self.tabs.currentWidget() is self.image_page
        is_image = bool(getattr(self, "_panel_picture", False)) or picture_tab or (
            getattr(self, "configuration_only", False) and isinstance(self.info, ImageInfo))
        if hasattr(self, "audio_quality_card"):
            self.audio_quality_card.setVisible(audio_only)
            self.audio_quality.set_lossless(kind in {"wav", "flac", "pcm"})
        if hasattr(self, "image_quality_card"):
            self.image_quality_card.setVisible(is_image)
            picture_kind = self.image_format.currentData()
            self.image_quality.set_lossless(picture_kind in {"png", "gif_image", "bmp", "tiff"})
        self.estimate_card.setVisible(True)
        self.gif_row.setVisible(gif)
        self.audio_advanced.setVisible(audio_only)
        self.sample_row.setVisible(audio_only)
        self.channels_row.setVisible(audio_only)
        has_video_audio = video and isinstance(self.info, VideoInfo) and self.info.audio_stream is not None
        self.audio_codec_row.setVisible(has_video_audio or audio_only)
        self.audio_row.setVisible((has_video_audio or audio_only) and kind not in {"wav", "flac"} and not audio_only)
        self.limit_row.setVisible(False)
        can_limit = kind not in {"wav", "flac"}
        self.limit_toggle.setVisible(False)
        self.audio_limit_toggle.setVisible(False)
        self._refresh_audio_codec()
        self._refresh_acceleration()
        self._sync_quality_encoder()
        if hasattr(self, 'estimate_card'):
            self._refresh_estimate()
        if getattr(self, "configuration_only", False):
            self.mode_hint.hide()
            if hasattr(self, "quality"):
                self.quality.set_compact(True)

    def _notify_settings(self, *_):
        if self.configuration_only or self.tabs.currentWidget() is self.video_page or isinstance(self.info, (AudioInfo, ImageInfo)):
            self.settings_changed.emit()

    def output_options(self):
        if isinstance(self.info, ImageInfo):
            quality, limit, compression = self._picture_encoding()
            return Export(self.image_format.currentData(), self.directory, max_edge=int(self.resolution.currentData() or 0),
                          target_bytes=limit, quality=quality, png_compression=compression,
                          output_stem=self._get_output_stem())
        return Export(self.format.currentData(), self.directory,
                      small_gif=bool(self.gif_profile.currentData()),
                      max_edge=max(0, int(self.export_edge.currentData() or 0)),
                      target_bytes=self.export_limit.value() * 1000,
                      video_acceleration=self.acceleration.currentData() or "cpu",
                      output_fps=int(self.output_fps.currentData() or 0), quality=self.quality.value(),
                      audio_bitrate=int(self.audio_bitrate.currentData() or 0),
                      audio_sample_rate=int(self.audio_sample_rate.currentData() or 0),
                      audio_channels=int(self.audio_channels.currentData() or 0),
                      output_stem=self._get_output_stem())

    def apply_output_options(self, options, *, apply_image_quality=False):
        with QSignalBlocker(self):
            self.set_output_format(options.format)
            image = isinstance(self.info, ImageInfo)
            edge_combo = self.resolution if image else self.export_edge
            edge_index = edge_combo.findData(options.max_edge)
            if edge_index < 0 and options.max_edge > 0:
                edge_index = edge_combo.count() if image else edge_combo.count() - 1
                edge_combo.insertItem(edge_index, f"最长边 {options.max_edge} px", options.max_edge)
            edge_combo.setCurrentIndex(max(0, edge_index))
            if image:
                self.size.setValue(options.target_bytes // 1000)
                if apply_image_quality and options.quality and hasattr(self, "image_quality"):
                    self.image_quality.setValue(options.quality)
                if options.target_bytes:
                    self.picture_preset.setCurrentIndex(2)
            else:
                self.export_limit.setValue(options.target_bytes // 1000)
                for combo, value in ((self.output_fps, options.output_fps), (self.acceleration, options.video_acceleration),
                                     (self.gif_profile, options.small_gif), (self.audio_bitrate, options.audio_bitrate),
                                     (self.audio_sample_rate, options.audio_sample_rate), (self.audio_channels, options.audio_channels)):
                    if (index := combo.findData(value)) >= 0:
                        combo.setCurrentIndex(index)
                if options.quality:
                    self.quality.setValue(options.quality)
            self.directory = options.directory or self.path.parent
            self.directory_path.set_path(str(self.directory))
            self.reset_path.setVisible(self.directory != self.path.parent)
            self._update_output_location()

    def _custom_video_edge(self, index):
        if self.export_edge.itemData(index) != -1:
            self._last_video_edge = index
            return
        edge, accepted = QInputDialog.getInt(self, "输出分辨率", "最长边（像素）", 1280, 16, 8192)
        if accepted:
            target = self.export_edge.findData(edge)
            if target < 0:
                self.export_edge.insertItem(index, f"最长边 {edge} px", edge)
                target = index
            self.export_edge.setCurrentIndex(target)
            self._last_video_edge = target
        else:
            self.export_edge.setCurrentIndex(getattr(self, "_last_video_edge", 0))

    def _update_range_marker(self):
        if self.filmstrip is None:
            return
        spans = [(frame, frame) for frame, selected in self.video_overrides.items() if selected]
        invalid = False
        if self.video_range is not None:
            first, last = self.video_range
            invalid = first > last
            for frame in sorted(self.video_overrides):
                if first <= frame <= last and not self.video_overrides[frame]:
                    if first < frame:
                        spans.append((first, frame - 1))
                    first = frame + 1
            if first <= last:
                spans.append((first, last))
        merged = []
        for first, last in sorted(spans):
            if merged and first <= merged[-1][1] + 1:
                merged[-1] = merged[-1][0], max(merged[-1][1], last)
            else:
                merged.append((first, last))
        self.video_selection = () if invalid else tuple(merged)
        self.filmstrip.set_selection(self.video_selection)
        if not self.frames:
            message = "等待读取帧数"
        elif invalid:
            message = "结束帧应大于或等于起始帧"
        elif not merged:
            message = ""
        else:
            count = sum(last - first + 1 for first, last in merged)
            message = f"已选 {count} 帧，{len(merged)} 段"
        self._selection_note = "" if message == "等待读取帧数" else message
        if hasattr(self, "selection_label"):
            self.selection_label.setText(f"· {self._selection_note}" if self._selection_note else "")
        self._update_timeline_header()

    def _range_fields_changed(self):
        self.video_overrides.clear()
        self.video_range = self.video_first.value(), self.video_last.value()
        self._update_range_marker()

    def _use_current_frame(self, field):
        if self._ready_video():
            with QSignalBlocker(field):
                field.setValue(self.cursor.value())
            self._range_fields_changed()

    def _set_video_range(self, first, last):
        self.video_range = first, last
        with QSignalBlocker(self.video_first), QSignalBlocker(self.video_last):
            self.video_first.setValue(first)
            self.video_last.setValue(last)

    def _click_frame(self, frame, modifiers):
        self.video_drag_origin = self.video_range, dict(self.video_overrides)
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            selected = any(first <= frame <= last for first, last in self.video_selection)
            in_range = self.video_range is not None and self.video_range[0] <= frame <= self.video_range[1]
            if selected != in_range:
                self.video_overrides.pop(frame, None)
            else:
                self.video_overrides[frame] = not selected
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            current = self.cursor.value()
            first, last = self.video_range or (current, current)
            if first > last:
                first = last = current
            self._set_video_range(min(first, frame), max(last, frame))
        else:
            self.video_overrides.clear()
            self._set_video_range(frame, frame)
        self._locate_frame(frame)
        self._update_range_marker()

    def _drag_select_frames(self, anchor, frame, modifiers):
        if not self._ready_video():
            return
        first, last = sorted((anchor, frame))
        base_range, base_overrides = self.video_drag_origin
        self.video_overrides = dict(base_overrides)
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            for selected_frame in range(first, last + 1):
                in_range = base_range is not None and base_range[0] <= selected_frame <= base_range[1]
                was_selected = base_overrides.get(selected_frame, in_range)
                if not was_selected:
                    self.video_overrides[selected_frame] = True
                elif in_range:
                    self.video_overrides[selected_frame] = False
                else:
                    self.video_overrides.pop(selected_frame, None)
            self.video_range = base_range
        elif modifiers & Qt.KeyboardModifier.ShiftModifier and base_range is not None:
            self.video_range = min(base_range[0], first), max(base_range[1], last)
        else:
            self.video_overrides.clear()
            self.video_range = first, last
        with QSignalBlocker(self.video_first), QSignalBlocker(self.video_last):
            self.video_first.setValue(first if self.video_range is None else self.video_range[0])
            self.video_last.setValue(last if self.video_range is None else self.video_range[1])
        self._update_range_marker()
        self._locate_frame(frame)

    def _show_frame_context_menu(self, frame, position):
        if not self._ready_video():
            return
        selected = any(first <= frame <= last for first, last in self.video_selection)
        if not selected:
            self.video_overrides.clear()
            self._set_video_range(frame, frame)
        self._locate_frame(frame)
        self._update_range_marker()
        count = sum(last - first + 1 for first, last in self.video_selection)
        menu = self.frame_context_menu
        menu.clear()
        menu.addAction("播放选区", self.play_video_selection)
        menu.addSeparator()
        menu.addAction("复制\tCtrl+C", self.copy_video)
        menu.addAction("剪切\tCtrl+X", self.cut_video)
        menu.addAction("删除", self.delete_video)
        menu.addSeparator()
        if count == 1:
            menu.addAction("保存为图片…", self.save_selected_frame_as)
        elif count > 1:
            menu.addAction("片段另存为…", self.save_video_selection_as)
        menu.addSeparator()
        menu.addAction("帧前粘贴", self.paste_video_before).setEnabled(bool(self.video_clipboard))
        menu.addAction("帧后粘贴\tCtrl+V", self.paste_video_after).setEnabled(bool(self.video_clipboard))
        menu.addAction("撤销\tCtrl+Z", self.undo).setEnabled(bool(self.history))
        menu.popup(position)

    @staticmethod
    def _target_from_dialog(filename, selected_filter, extensions):
        if not filename:
            return None, None
        target = Path(filename).expanduser()
        suffix = target.suffix.lower().lstrip(".")
        if not suffix:
            suffix = extensions.get(selected_filter)
            if not suffix:
                return None, None
            target = target.with_suffix(f".{suffix}")
        return target, suffix

    def save_selected_frame_as(self):
        if not self._ready_video() or len(self.video_selection) != 1:
            return
        frame, last = self.video_selection[0]
        if frame != last:
            return
        path, index = self._frame_ref(frame)
        filters = {
            "PNG 图片 (*.png)": "png", "JPEG 图片 (*.jpg *.jpeg)": "jpg",
            "WebP 图片 (*.webp)": "webp", "AVIF 图片 (*.avif)": "avif",
            "TIFF 图片 (*.tiff)": "tiff", "BMP 图片 (*.bmp)": "bmp",
            "静态 GIF 图片 (*.gif)": "gif", "ICO 图标 (*.ico)": "ico",
            "PDF 文档 (*.pdf)": "pdf",
        }
        current = self.image_format.currentData() or "png"
        extension = "gif" if current == "gif_image" else current
        selected_filter = next((name for name, suffix in filters.items() if suffix == extension),
                               "PNG 图片 (*.png)")
        default = self.directory / f"{self.path.stem}_帧{frame:06}.{extension}"
        filename, chosen = QFileDialog.getSaveFileName(
            self, "保存当前帧为图片", str(default), ";;".join(filters), selected_filter,
            QFileDialog.Option.DontConfirmOverwrite)
        target, suffix = self._target_from_dialog(filename, chosen or selected_filter, filters)
        if not target:
            return
        kinds = {"jpeg": "jpg", "jpg": "jpg", "gif": "gif_image"}
        kind = kinds.get(suffix, suffix)
        if kind not in _IMAGE_FORMATS:
            self._notice("请选择支持的图片格式。")
            return
        if target.resolve() == self.path.resolve():
            self._notice("不能用保存图片覆盖源视频。")
            return
        quality, limit, png_compression = self._picture_encoding()
        max_edge, video = self.resolution.currentData(), self.video_sources[path].info
        self._run("正在保存所选帧…",
                  lambda scope: save_picture(self.path, video, index, kind, quality, limit,
                                             target.parent, self.tools, scope,
                                             max_edge=max_edge, target=target, frame_path=path,
                                             png_compression=png_compression),
                  lambda path: self._saved(path, limit, frame=frame), exporting=True)

    def save_video_selection_as(self):
        if not self._ready_video() or not self.video_selection:
            return
        video, audio = self._video_range_tracks(self.video_selection)
        fps = self.frames.fps
        formats = {
            "MP4 视频 (*.mp4)": ("mp4", "mp4"),
            "MP4 视频 H.265 (*.mp4)": ("mp4_hevc", "mp4"),
            "MKV 视频 H.264 (*.mkv)": ("mkv", "mkv"),
            "MKV 视频 H.265 (*.mkv)": ("mkv_hevc", "mkv"),
            "MOV 视频 (*.mov)": ("mov", "mov"),
            "WebM 视频 (*.webm)": ("webm", "webm"),
            "AVI 视频 (*.avi)": ("avi", "avi"),
        }
        current = self.format.currentData() or "mp4"
        initial_filter = next((name for name, (kind, _) in formats.items() if kind == current),
                              "MP4 视频 (*.mp4)")
        default_ext = formats[initial_filter][1]
        default = self.directory / f"{self.path.stem}_选区.{default_ext}"
        filename, chosen = QFileDialog.getSaveFileName(
            self, "另存所选视频片段", str(default), ";;".join(formats), initial_filter,
            QFileDialog.Option.DontConfirmOverwrite)
        extensions = {name: extension for name, (_, extension) in formats.items()}
        target, suffix = self._target_from_dialog(filename, chosen or initial_filter, extensions)
        if not target:
            return
        selected_kind, selected_suffix = formats.get(chosen, (suffix, suffix))
        kind = selected_kind if suffix == selected_suffix else suffix
        if kind not in _ENCODERS:
            self._notice("请选择支持的视频格式。")
            return
        if target.resolve() == self.path.resolve():
            self._notice("不能用另存片段覆盖源视频。")
            return
        acceleration = self.acceleration.currentData() or "cpu"
        supported = {backend for backend, _ in hardware_acceleration_choices(self.tools.ffmpeg, kind)}
        options = Export(kind, target.parent,
                         max_edge=int(self.export_edge.currentData() or 0),
                         video_acceleration=acceleration if acceleration in supported else "cpu",
                         output_fps=int(self.output_fps.currentData() or 0))
        canvas = self._canvas_for(video)
        self._run("正在另存所选视频片段…",
                  lambda scope: render_edit(self.path, video, audio, fps, options,
                                            self.tools, scope, self.events.progress.emit,
                                            target=target, canvas=canvas,
                                            notice=self.events.notice.emit),
                  self._saved, exporting=True)

    def _locate_frame(self, frame):
        self._stop_playback()
        self.cursor.setValue(frame)

    def _entered_frame(self):
        text = self.locate_input.text()
        return int(text) if text.isdecimal() else 0

    def _update_locate_button(self, *_):
        self.locate_button.setEnabled(self._ready_video() and 1 <= self._entered_frame() <= self.cursor.maximum())

    def _jump_to_frame(self):
        if not self._ready_video():
            return
        frame = self._entered_frame()
        if 1 <= frame <= self.cursor.maximum():
            self._locate_frame(frame)
            self.locate_input.clear()
        else:
            self._notice(f"请输入 1 到 {self.cursor.maximum()} 之间的帧号。")

    def choose_directory(self):
        if self.scope is None:
            selected = QFileDialog.getExistingDirectory(self.settings_body.window(), "选择保存目录", str(self.directory))
            if selected:
                self._set_output_directory(Path(selected))

    def _reset_output_directory(self):
        self._set_output_directory(self.path.parent)

    def _set_output_directory(self, directory):
        self.directory = directory
        self.directory_path.set_path(str(directory))
        self.reset_path.setVisible(directory != self.path.parent)
        self.directory_changed.emit(directory)

    def _run(self, message, worker, on_done, *, exporting=False, on_error=None):
        if self.scope is not None or self.closing:
            return
        self._stop_playback()
        self.stop_audio()
        self._notice(message)
        self.progress.setRange(0, 100 if exporting else 0)
        if not self.embedded:
            self.progress_row.show()
        self.progress.setValue(0)
        self.scope = Processes()
        self._update_audio_toolbar()
        if self.filmstrip is not None:
            self.play_button.setEnabled(False)
            self._update_locate_button()
        self.cancel_button.setEnabled(True)
        self.picture_group.setEnabled(False)
        self.on_done = on_done
        self.on_error = on_error
        self.task_changed.emit()
        self.pool.submit(self._worker, worker, self.scope)

    def _worker(self, worker, scope):
        try:
            result = worker(scope)
        except Exception as exc:
            self.events.finished.emit(None, str(exc) or type(exc).__name__)
        else:
            self.events.finished.emit(result, "")

    @Slot(object, str)
    def _finished(self, result, error):
        if self.closing:
            return
        scope, self.scope = self.scope, None
        if scope is not None and scope is self.pending_video_insert:
            self.pending_video_insert = None
            if scope.cancelled.is_set():
                error = "已取消素材插入。"
        if self.filmstrip is not None:
            self.play_button.setEnabled(self.frames is not None)
            self._update_locate_button()
        self.cancel_button.setEnabled(False)
        self.picture_group.setEnabled(True)
        self.progress_row.hide()
        done, self.on_done = self.on_done, None
        failed, self.on_error = self.on_error, None
        if error:
            if self.listen_requested:
                self.stop_audio()
            if failed:
                failed(error)
            else:
                self._notice(error)
                self.status.setToolTip(error)
            if media_path_missing(self.path):
                self.missing_source.emit(self.path)
        else:
            self._notice("")
            self.status.clear()
            self.status.setToolTip("")
            done(result)
        self._update_audio_toolbar()
        self.task_changed.emit()
        if self.frames and self._frame_preview_panel() is not None and self.requested_frame != self.shown_frame:
            self.seek_timer.start()
        if self.scope is None and self.wave_pending and self.tabs.currentWidget() is self.audio_page:
            self.wave_reload.start()

    @Slot(int)
    def _progress(self, value):
        self.progress.setValue(value)
        self.task_changed.emit()

    def cancel_operation(self):
        if self.scope:
            self.scope.cancel()
            self.cancel_button.setEnabled(False)
            self._notice("正在取消…")

    def _load_index(self, scope):
        return index_frames(self.path, self.info, self.tools, scope)

    def _load_picture(self, scope):
        return thumbnail(self.path, self.info, self.tools, scope)

    @staticmethod
    def _display(panel, data):
        panel.set_image(data)

    def _show_image(self, data):
        self._display(self.image_view, data)

    def _indexed(self, frames):
        self.frames = frames
        self.video_sources[self.path] = VideoSource(self.info, frames, self.path.name)
        self.video = [Clip(self.path, self.info.stream, 0, frames.count)]
        self.previous_frame_button.setEnabled(True)
        self.play_button.setEnabled(True)
        self.next_frame_button.setEnabled(True)
        self.player_seek.setEnabled(True)
        self._refresh_video()
        if self.tabs.currentWidget() is self.audio_page:
            self._refresh_audio(reset=True)
            if self._audio_track():
                self.preview_audio()

    def _refresh_video(self):
        count = int(length(self.video))
        self.filmstrip.set_track(self.video, self.frames, self.video_sources)
        with QSignalBlocker(self.player_seek):
            self.player_seek.setRange(1, count)
        with QSignalBlocker(self.video_first), QSignalBlocker(self.video_last):
            for field in (self.video_first, self.video_last):
                field.setRange(1, count)
            self.video_first.setValue(1)
            self.video_last.setValue(count)
        self.video_range = None
        self.video_overrides.clear()
        self._update_range_marker()
        self.cursor.setRange(1, count)
        self.locate_input.setEnabled(True)
        self.locate_input.setToolTip(f"可定位第 1 到 {count} 帧，输入后按回车")
        self._update_locate_button()
        self._cursor_changed(self.cursor.value())

    def _cursor_changed(self, value):
        self._stop_playback()
        self._sync_cursor(value)

    def _frame_ref(self, value):
        clips = slice_track(self.video, value - 1, value)
        if clips:
            return clips[0].path, int(clips[0].start)
        return None

    def _sync_cursor(self, value, *, playing=False):
        if not self.frames:
            return
        self.filmstrip.set_current(value, playing=playing)
        with QSignalBlocker(self.player_seek):
            self.player_seek.setValue(value)
        self.player_time_current.setText(_timecode((value - 1) / self.frames.fps))
        self.player_time_short.setText(self.player_time_current.text().split(".")[0])
        self.player_time_total.setText(_timecode(length(self.video) / self.frames.fps))
        self._sync_frame_preview(playing=playing)
        self._update_output_location()

    def _seek_from_player(self, value):
        if self.frames is not None and value != self.cursor.value():
            self.cursor.setValue(value)

    def _frame_position(self, value):
        seconds = (value - 1) / self.frames.fps
        return f"第 {value} / {int(length(self.video))} 帧 · {seconds:.3f} 秒"

    def _picture_frame_number(self):
        if self.tabs.currentWidget() is self.image_page:
            return self.video_selection[0][0] if self.video_selection else 1
        return self.cursor.value()

    def _frame_preview_panel(self):
        page = self.tabs.currentWidget()
        if self.video_page is not None:
            if page is self.video_page:
                return self.frame_view
            if page is self.image_page:
                return self.image_view
        return None

    def _sync_frame_preview(self, *, playing=False):
        panel = self._frame_preview_panel()
        if panel is None:
            return
        value = self._picture_frame_number()
        frame = self._frame_ref(value)
        if frame is None:
            return
        path, index = frame
        origin = f" · 原始第 {index + 1} 帧" if index != value - 1 else ""
        if path != self.path:
            origin = f" · {self.video_sources[path].name} 第 {index + 1} 帧"
        self.picture_caption.setText(self._frame_position(value) + origin)
        self.picture_save.setText(f"保存第 {value} 帧")
        if not playing:
            self._schedule_picture_estimate()
        if frame != self.requested_frame:
            self.failed_frame = None
        self.requested_frame = frame
        if playing:
            return
        if frame in self.frame_cache:
            if self.frame_scope is not None and self.frame_inflight != frame:
                self.frame_scope.cancel()
            self.frame_cache.move_to_end(frame)
            if self.shown_frame != frame:
                self._display(panel, self.frame_cache[frame])
                self.shown_frame = frame
        else:
            if panel is self.image_view and frame not in (self.shown_frame, self.failed_frame):
                self.shown_frame = None
                panel.setText(f"正在读取第 {value} 帧…")
            self.seek_timer.start()

    def _seek(self):
        if (self.closing or self.scope is not None or self.playback is not None
                or (self.embedded and not self.isVisible())
                or self._frame_preview_panel() is None):
            return
        frame = self.requested_frame
        if frame is None or frame in (self.shown_frame, self.failed_frame):
            return
        if self.frame_scope is not None:
            if self.frame_inflight != frame:
                self.frame_scope.cancel()
            return
        self.frame_inflight = frame
        self.frame_scope = Processes()
        self.frame_pool.submit(self._decode_frame, frame, self.video_sources[frame[0]].info, self.frame_scope)

    def _decode_frame(self, frame, info, scope):
        path, index = frame
        try:
            data = frame_png(path, info, index, self.tools, scope)
        except Cancelled:
            self.events.frame_ready.emit(frame, None, "")
        except (MediaError, OSError) as exc:
            self.events.frame_ready.emit(frame, None, str(exc))
        else:
            self.events.frame_ready.emit(frame, data, "")

    @Slot(object, object, str)
    def _frame_ready(self, frame, data, error):
        self.frame_scope = self.frame_inflight = None
        if self.closing:
            return
        if data:
            self.frame_cache[frame] = data
            while len(self.frame_cache) > 12:
                self.frame_cache.popitem(last=False)
        if self.playback is not None:
            return
        panel = self._frame_preview_panel()
        if frame == self.requested_frame and panel is not None:
            if data:
                self._display(panel, data)
                self.shown_frame = frame
            elif error:
                self.failed_frame = frame
                if panel is self.image_view:
                    panel.setText("当前帧读取失败")
                self._notice("当前帧读取失败")
                self.status.setToolTip(error)
                if media_path_missing(frame[0]):
                    self.missing_source.emit(frame[0])
        if panel is not None and self.requested_frame not in (self.shown_frame, self.failed_frame):
            self.seek_timer.start()

    def _canvas_for(self, video):
        if any(clip.path != self.path for clip in video):
            return self.info.width, self.info.height
        return None

    def _toggle_playback(self):
        if self.playback is not None:
            self._stop_playback()
            self._notice("已暂停。")
            return
        if not self._ready_video():
            return
        total = int(length(self.video))
        start = self.cursor.value()
        self._play_video_ranges(((1 if start == total else start, total),))

    def eventFilter(self, watched, event):
        popup = getattr(self, "volume_popup", None)
        button = getattr(self, "volume_button", None)
        if popup is None or button is None:
            return super().eventFilter(watched, event)
        if watched is button or watched is popup:
            if event.type() in (QEvent.Type.Enter, QEvent.Type.HoverEnter):
                self.volume_hide.stop()
                self._show_volume_popup()
            elif event.type() == QEvent.Type.Leave:
                self.volume_hide.start()
        return super().eventFilter(watched, event)

    def _volume_hovered(self) -> bool:
        """鼠标是否还落在喇叭按钮或音量面板上（用全局坐标判断）。"""
        button = getattr(self, "volume_button", None)
        popup = getattr(self, "volume_popup", None)
        cursor = QCursor.pos()
        for widget in (button, popup):
            if widget is not None and widget.isVisible():
                if widget.rect().contains(widget.mapFromGlobal(cursor)):
                    return True
        return False

    def _finish_volume_hide(self) -> None:
        if self._volume_hovered():
            return
        self._hide_volume_popup()

    def _show_volume_popup(self) -> None:
        popup = getattr(self, "volume_popup", None)
        if popup is None:
            return
        self.volume_hide.stop()
        popup.set_level(self.playback_level)
        corner = self.volume_button.mapToGlobal(QPoint(0, 0))
        x = corner.x() + self.volume_button.width() // 2 - popup.width() // 2
        y = corner.y() - popup.height() - 6
        screen = self.screen().availableGeometry()
        x = max(screen.left() + 4, min(x, screen.right() - popup.width() - 4))
        if y < screen.top() + 4:
            y = self.volume_button.mapToGlobal(QPoint(0, self.volume_button.height())).y() + 6
        popup.move(x, y)
        popup.show()
        popup.raise_()

    def _hide_volume_popup(self) -> None:
        popup = getattr(self, "volume_popup", None)
        if popup is not None and popup.isVisible():
            popup.hide()

    def _show_player_volume(self, *_):
        self._show_volume_popup()

    def _set_player_volume(self, value):
        self.playback_level = value
        if value:
            self.last_audible_level = value
        self.playback_muted = False
        self._update_player_volume()
        popup = getattr(self, "volume_popup", None)
        if popup is not None and popup.isVisible():
            popup.set_level(value)

    def _toggle_player_mute(self):
        if self.playback_level == 0:
            self.player_volume.setValue(self.last_audible_level)
        else:
            self.playback_muted = not self.playback_muted
            self._update_player_volume()

    def _update_player_volume(self):
        effective = 0 if self.playback_muted else self.playback_level
        self.volume_value.setText(f"{effective}%")
        self.volume_button.setIcon(icons.icon("volume" if effective else "mute", 18, "#FFFFFF", 2))
        if self.playback is not None:
            self.playback.set_volume(effective / 100)

    def _toggle_fullscreen(self):
        if self.fullscreen_player is not None:
            self.fullscreen_player.reject()
            return
        self.fullscreen_player = FullscreenPlayer(self)
        self.fullscreen_button.setIcon(icons.icon("exitFullscreen", 18, "#FFFFFF", 2))
        self.fullscreen_button.setToolTip("退出全屏（Esc）")
        self._fit_player_controls()
        self.fullscreen_player.showFullScreen()

    def restore_player(self):
        self.fullscreen_player = None
        self.video_layout.addWidget(self.player, 1)
        self.player.show()
        self.player_controls.show()
        self.fullscreen_button.setIcon(icons.icon("fullscreen", 18, "#FFFFFF", 2))
        self.fullscreen_button.setToolTip("全屏")
        self._fit_player_controls()

    def _fit_player_controls(self):
        if self.video_page is not None and hasattr(self, "fullscreen_button"):
            room = self.fullscreen_player is not None or self.preview_split.width() >= 445
            for widget in (self.player_time_current, self.player_time_total, self.player_time_separator):
                widget.setVisible(room)
            self.player_time_short.setVisible(not room)

    def play_video_selection(self):
        if self._ready_video() and self.video_selection:
            self._play_video_ranges(self.video_selection)

    def _video_range_tracks(self, ranges):
        video = [clip for first, last in ranges for clip in slice_track(self.video, first - 1, last)]
        if self.audio is None:
            audio = original_audio(video, self.frames, self.info.audio_stream)
        else:
            fps = self.frames.fps
            audio = [clip for first, last in ranges
                     for clip in audio_segment(self.audio, (first - 1) / fps, last / fps)] if self.audio else []
        return video, audio

    def _play_video_ranges(self, ranges):
        self._stop_playback()
        self.stop_audio()
        self.seek_timer.stop()
        if self.frame_scope is not None:
            self.frame_scope.cancel()
        self.playback_ranges = tuple(ranges)
        video, audio = self._video_range_tracks(self.playback_ranges)
        self.playback = Playback(video, audio, self.frames.fps, self.tools,
                                 Path(self.scratch.name) / "timeline.wav", canvas=self._canvas_for(video),
                                 volume=0 if self.playback_muted else self.playback_level / 100)
        self._notice("正在准备剪辑预览…")
        self.status.setToolTip("")
        self._set_transport_playing(True)
        self.playback_timer.start()

    def _set_transport_playing(self, playing):
        self.play_button.setIcon(icons.icon("pause" if playing else "play", 18, "#FFFFFF", 2))
        self.play_button.setAccessibleName("暂停" if playing else "播放")
        self.play_button.setToolTip("暂停播放" if playing else "从当前帧开始播放剪辑预览")
        if not playing and self.fullscreen_player is not None:
            self.player_controls.show()

    def _playback_tick(self):
        player = self.playback
        first_frame = player.started is None
        try:
            data = player.tick()
        except (MediaError, OSError) as exc:
            self._stop_playback()
            self._notice("播放失败，详情见悬停提示。")
            self.status.setToolTip(str(exc))
            return
        if data is not None:
            offset = player.position
            for first, last in self.playback_ranges:
                count = last - first + 1
                if offset < count:
                    number = first + offset
                    break
                offset -= count
            with QSignalBlocker(self.cursor):
                self.cursor.setValue(number)
            self._sync_cursor(number, playing=True)
            self._display(self.frame_view, data)
            self.shown_frame = self.requested_frame
            if first_frame:
                self._notice("音频播放失败，正在播放画面。" if player.audio_error else "正在播放剪辑预览。")
                self.status.setToolTip(player.audio_error)
        if player.finished:
            self._stop_playback()
            self._notice("播放结束。")

    def _stop_playback(self):
        if self.playback is not None:
            self.playback_timer.stop()
            self.playback.close()
            self.playback = None
            self._set_transport_playing(False)
            self.status.clear()
            self.status.setToolTip("")
            if not self.closing:
                self.shown_frame = self.failed_frame = None
                self.seek_timer.start()

    def _previous(self):
        self.cursor.stepDown()

    def _next(self):
        self.cursor.stepUp()

    def _ready_video(self):
        return self.frames is not None and self.scope is None

    def _timeline_command(self, command):
        actions = {"copy": self.copy_video, "cut": self.cut_video,
                   "paste": self.paste_video_after, "undo": self.undo}
        if action := actions.get(command):
            action()

    def copy_video(self):
        if self._ready_video() and self.video_selection:
            self.video_clipboard = [clip for first, last in self.video_selection
                                    for clip in slice_track(self.video, first - 1, last)]
            fps = self.frames.fps
            self.video_clipboard_audio = None if self.audio is None else [
                clip for first, last in self.video_selection
                for clip in audio_segment(self.audio, (first - 1) / fps, last / fps)
            ]
            self._notice(f"已复制 {int(length(self.video_clipboard))} 帧。")

    def cut_video(self):
        self.copy_video()
        self.delete_video()

    def _remember(self):
        self.history.append((self.video, self.audio))

    def delete_video(self):
        if self._ready_video() and self.video_selection:
            self._remove_video_ranges([(first - 1, last) for first, last in self.video_selection])

    def _remove_video_ranges(self, ranges, *, audio_tail_end=None):
        """按当前时间线剪掉同一范围，一次撤销恢复两轨。"""
        if sum(end - start for start, end in ranges) >= length(self.video):
            self._notice("至少保留一帧。")
            return False
        video, audio = self.video, self.audio
        fps = self.frames.fps
        if audio is not None and audio_tail_end is not None:
            audio = splice(audio, self._video_duration(), audio_tail_end)
        for start, end in reversed(ranges):
            video = splice(video, start, end)
            if audio is not None:
                audio = splice(audio, start / fps, end / fps)
        self._stop_playback()
        self.stop_audio()
        self._remember()
        self.video, self.audio = video, audio
        self._refresh_video()
        self._refresh_audio(reset=True)
        if self.tabs.currentWidget() is self.audio_page and self._audio_track():
            self.preview_audio()
        else:
            self._notice("已裁剪所选片段。")
        return True

    def paste_video_before(self):
        self._paste_video(self.cursor.value() - 1)

    def paste_video_after(self):
        self._paste_video(self.cursor.value())

    def _paste_video(self, position):
        if self._ready_video() and self.video_clipboard:
            self._insert_video(position, self.video_clipboard, self.video_clipboard_audio)

    def _queued_source(self, key):
        return self.lookup_media(key) if key and self.lookup_media and not self.closing else None

    def _video_drop_preview(self, key):
        source = self._queued_source(key)
        if self._ready_video() and source and isinstance(source[1], (VideoInfo, ImageInfo)):
            path, _, picture = source
            return path.name, picture
        return None

    def _move_video_ranges(self, ranges, position):
        if not self._ready_video():
            return
        count = sum(end - start for start, end in ranges)
        total = length(self.video)
        if not 0 < count < total or not 0 <= position <= total - count:
            return
        if len(ranges) == 1 and position == ranges[0][0]:
            return

        def move(track, units):
            clips = [clip for start, end in ranges for clip in slice_track(track, start / units, end / units)]
            for start, end in reversed(ranges):
                track = splice(track, start / units, end / units)
            return splice(track, position / units, position / units, clips)

        video = move(self.video, 1)
        audio = self.audio
        if audio:
            fps = self.frames.fps
            audio = move(audio_segment(audio, 0, max(length(audio), total / fps)), fps)
        self._stop_playback()
        self.stop_audio()
        self._remember()
        self.video, self.audio = video, audio
        self._refresh_video()
        self._refresh_audio(reset=True)
        self._set_video_range(position + 1, position + count)
        self._locate_frame(position + 1)
        self._update_range_marker()
        self._notice(f"已移动 {count} 帧。")

    def _drop_video(self, key, position):
        if not self._video_drop_preview(key):
            return
        path, info, _ = self._queued_source(key)
        if media_path_missing(path):
            self._notice("找不到要插入的文件。")
            self.missing_source.emit(path)
            return
        cached = self.imported_images.get(path, path)

        def insert(result):
            clip_path, source = result
            self.video_sources[clip_path] = source
            if isinstance(info, ImageInfo):
                self.imported_images[path] = clip_path
            clip = Clip(clip_path, source.info.stream, 0, source.frames.count)
            self._insert_video(position, [clip], [])
            self._locate_frame(position + 1)
            self._notice(f"已在第 {position + 1} 帧处插入 {source.frames.count} 帧。")

        if cached in self.video_sources:
            insert((cached, self.video_sources[cached]))
        else:
            self._run("正在读取拖入的画面…", lambda scope: self._load_dropped_video(path, info, scope), insert)
            self.pending_video_insert = self.scope

    def _load_dropped_video(self, path, info, scope):
        if isinstance(info, VideoInfo):
            frames = index_frames(path, info, self.tools, scope)
            return path, VideoSource(info, frames, path.name)
        clip_path = Path(self.scratch.name) / f"still-{uuid4().hex}.png"
        _save_image(path, clip_path, "PNG")
        scope.check()
        fps = self.frames.fps
        frames = Frames((0.0,), 1 / fps, fps)
        video = VideoInfo(0, frames.duration, info.width, info.height, fps, "PNG", clip_path.stat().st_size)
        return clip_path, VideoSource(video, frames, path.name)

    def _insert_video(self, position, video, audio):
        self._stop_playback()
        self.stop_audio()
        self._remember()
        if self.audio is not None or audio is not None:
            time = position / self.frames.fps
            current = self._audio_track()
            copied = original_audio(video, self.frames, self.info.audio_stream) if audio is None else audio
            duration = length(video) / self.frames.fps
            self.audio = ([*audio_segment(current, 0, time), *audio_segment(copied, 0, duration),
                           *slice_track(current, time, length(current))] if current or copied else [])
        self.video = splice(self.video, position, position, video)
        self._refresh_video()
        self._refresh_audio(reset=True)
        if self.tabs.currentWidget() is self.audio_page and self._audio_track():
            self.preview_audio()

    def _audio_track(self):
        if self.audio is not None:
            return self.audio
        return original_audio(self.video, self.frames, self.info.audio_stream) if self.frames else []

    def _refresh_audio(self, *, reset=False):
        track = self._audio_track()
        total = length(track)
        video_end = self._video_duration()
        self.audio_view.set_overflow((video_end, total) if self.frames and total - video_end > .0005 else None)
        empty = length(self.video) / self.frames.fps if self.frames and not track else 0
        self.audio_view.empty_duration = empty or (1.0 if not track else 0)
        if self.audio_view.insertion is not None:
            self.audio_view.insertion = min(self.audio_view.insertion, max(total, empty))
        self.audio_available = total > 0
        for field in (self.audio_first, self.audio_last):
            field.setRange(0, total)
        self.audio_cursor.setRange(0, max(total, empty))
        if reset:
            self.audio_first.setValue(0)
            self.audio_last.setValue(0)
        self.audio_range_initialized = bool(track)
        self.audio_list.clear()
        for clip in track:
            label = f"{clip.path.name}  ·  {clip.start:.3f}–{clip.end:.3f} 秒  ·  音量 {clip.volume:.0%}" if clip.path else f"静音  ·  {clip.length:.3f} 秒"
            item = QListWidgetItem(label)
            item.setToolTip(label)
            self.audio_list.addItem(item)
        self.audio_hint.setText("单击波形标记黑线，再把视频或音频拖入音轨。剪切、删除会同步裁掉对应画面。"
                                if self.video_page else "单击波形标记黑线，再把视频或音频拖入音轨。")
        self.audio_label.setText(f"{total:.3f} 秒 · 拖动选区 · 单击定位并清除选区 · 单次预览最多 30 秒"
                                 if track else "当前无音轨，可插入音频文件。")
        self._update_audio_marker()
        if tuple(track) != self.wave_track:
            self.wave_track = tuple(track)
            self.wave_pending = bool(track)
            self.audio_view.set_waveform(None, None)
            self.audio_view.setText("正在更新波形…" if track else "暂无音轨")

    def _update_audio_overflow(self):
        view = self.audio_view
        active = view.overflow is not None
        self.overflow_label.setVisible(active)
        self.overflow_jump.setVisible(active)
        if active:
            excess = view.overflow[1] - view.overflow[0]
            marked = view.trim_mark[1] - view.trim_mark[0]
            remaining = max(0.0, excess - marked)
            self.overflow_label.setText(
                f"超出 {excess:.3f} 秒 · 标红 {marked:.3f} 秒\n删除标红后还差 {remaining:.3f} 秒")

    def _focus_audio_overflow(self):
        view = self.audio_view
        if view.trim_mark:
            position = view.trim_mark[0]
            if view.loaded_interval and view.loaded_interval[0] <= position < view.loaded_interval[1]:
                view.reset_zoom()
            else:
                start = max(0, position - 8)
                self._focus_audio_window(start, start + 30)

    def _focus_audio_window(self, start, end):
        total = length(self._audio_track())
        span = min(total, end - start)
        if span > 0:
            start = max(0, min(start, total - span))
            self._load_audio_wave(focus=(start, start + span))

    def _reset_audio_view(self):
        self._click_audio(0.0)
        loaded = self.audio_view.loaded_interval
        if self.audio_available and (not loaded or loaded[0] > 0):
            self._focus_audio_window(0, 30)
        else:
            self.audio_view.reset_zoom()

    def _delete_marked_audio(self):
        mark = self.audio_view.trim_mark
        if self.scope is None and mark:
            start, end = mark
            updated = splice(self._audio_track(), start, end)
            self._change_audio(updated)
            suffix = "，音画时长已对齐。" if abs(length(updated) - self._video_duration()) < .0005 else "。"
            self._notice(f"已删除 {end - start:.3f} 秒标红音频{suffix}")

    @Slot(float, float)
    def _wave_selected(self, start, end):
        self.audio_first.setValue(start)
        self.audio_last.setValue(end)
        self._seek_audio(start)

    def _show_audio_range_fields(self, *_):
        selected = self.audio_last.value() > self.audio_first.value()
        visible = self.edit_audio_range.isChecked() or selected
        self.audio_range_fields.setVisible(visible)
        self.edit_audio_range.setVisible(not selected)
        self.edit_audio_range.setText("收起选区输入" if self.edit_audio_range.isChecked() else "手动输入选区")

    def _update_audio_marker(self):
        start, end = self.audio_first.value(), self.audio_last.value()
        self.audio_view.selection = (start, end) if end > start else None
        self.apply_volume.setEnabled(end > start)
        self.audio_selection_note.setText(
            f"已选中 {end - start:.3f} 秒" if end > start else "拖动波形以选择片段")
        self._show_audio_range_fields()
        self.audio_view.update()

    def _click_audio(self, position):
        if not self.audio_available and not self.frames:
            position = 0.0
        self.audio_first.setValue(position)
        self.audio_last.setValue(position)
        self.audio_view.insertion = position
        self._seek_audio(position)

    def _can_drop_audio(self, key):
        source = self._queued_source(key)
        if self.scope is not None or not source or (self.video_page is not None and not self.frames):
            return False
        info = source[1]
        return bool((isinstance(info, AudioInfo) or
                     (isinstance(info, VideoInfo) and info.audio_stream is not None)) and info.duration > 0)

    def _drop_audio(self, key, position):
        if not self._can_drop_audio(key):
            return
        path, info, _ = self._queued_source(key)
        if media_path_missing(path):
            self._notice("找不到要插入的文件。")
            self.missing_source.emit(path)
            return
        stream = info.stream if isinstance(info, AudioInfo) else info.audio_stream
        clip = Clip(path, stream, 0, info.duration)
        track = self._audio_track()
        inserted = [*audio_segment(track, 0, position), clip, *slice_track(track, position, length(track))]
        if self.frames:
            duration = max(length(inserted), length(self.video) / self.frames.fps)
            inserted = audio_segment(inserted, 0, duration)
        self._change_audio(inserted)
        self._notice(f"已在 {position:.3f} 秒插入音频。")

    def _show_audio_context_menu(self, position, global_position):
        menu = self.audio_context_menu
        menu.clear()
        start, end, track = self._selection_audio()
        ready = self.scope is None
        mark = self.audio_view.trim_mark
        on_mark = self.audio_view.marked_at(position)
        if mark:
            if on_mark:
                action = menu.addAction(f"删除标红音频（{mark[1] - mark[0]:.3f} 秒）", self._delete_marked_audio)
                action.setEnabled(ready)
            else:
                menu.addAction("把待删范围移到这里", lambda: self.audio_view.move_trim_mark(position)).setEnabled(ready)
            menu.addSeparator()
        selected = ready and end > start and bool(track)
        playable = ready and bool(track) and sys.platform == "win32"
        if end > start:
            menu.addAction("播放选区", self.play_audio_selection).setEnabled(playable)
        else:
            menu.addAction("从这里播放", lambda: self._play_audio_from(position)).setEnabled(playable)
        menu.addAction("缩放到选区", self._focus_audio_selection).setEnabled(selected)
        menu.addAction("查看采样点", lambda: self.audio_view.zoom_to_samples(position)).setEnabled(
            self.audio_view.waveform is not None)
        if track and not self.audio_view.loaded_interval:
            menu.addAction("刷新波形", self.preview_audio).setEnabled(ready)
        menu.addSeparator()
        menu.addAction("复制\tCtrl+C", self.copy_audio).setEnabled(selected)
        menu.addAction("剪切\tCtrl+X", self.cut_audio).setEnabled(selected)
        if not on_mark:
            menu.addAction("删除", self.delete_audio).setEnabled(selected)
        menu.addAction("粘贴\tCtrl+V", self.paste_audio).setEnabled(ready and bool(self.audio_clipboard))
        menu.addAction("插入音频…", self.insert_audio).setEnabled(ready)
        menu.addAction("撤销\tCtrl+Z", self.undo).setEnabled(ready and bool(self.history))
        menu.popup(global_position)

    def _audio_command(self, command):
        actions = {"copy": self.copy_audio, "cut": self.cut_audio,
                   "paste": self.paste_audio, "undo": self.undo}
        actions[command]()

    def _selection_audio(self):
        first, last = self.audio_first.value(), self.audio_last.value()
        track = self._audio_track()
        total = length(track)
        if last > first and abs(last - total) <= .0005:
            last = total
        return first, last, track

    def _video_duration(self):
        return length(self.video) / self.frames.fps if self.frames else 0.0

    def _audio_frames(self, start, end):
        """按秒选中的区间覆盖整帧，容忍输入框的毫秒舍入误差。"""
        fps = self.frames.fps
        total = int(length(self.video))
        first = min(total - 1, floor((start + .0005) * fps))
        last = min(total, max(first + 1, ceil((end - .0005) * fps)))
        return first, last

    def copy_audio(self):
        if self.scope is None:
            start, end, track = self._selection_audio()
            selected = slice_track(track, start, end)
            if not selected:
                return
            self.audio_clipboard_video = []
            self.audio_clipboard = selected
            self._notice(f"已复制 {length(self.audio_clipboard):.3f} 秒。")

    def cut_audio(self):
        if self.video_page is not None and (not self.frames or self.audio_first.value() < self._video_duration() - .0005):
            self._cut_audio_with_video()
            return
        self.copy_audio()
        self.delete_audio()

    def _cut_audio_with_video(self):
        start, end, track = self._selection_audio()
        if not self._ready_video() or end <= start:
            return
        if end > self._video_duration() + .0005:
            self._notice("选区跨过视频结尾，请分开剪切，或删除标红音频。")
            return
        first, last = self._audio_frames(start, end)
        video = slice_track(self.video, first, last)
        fps = self.frames.fps
        audio = (original_audio(video, self.frames, self.info.audio_stream) if self.audio is None
                 else audio_segment(track, first / fps, last / fps))
        if self._remove_video_ranges([(first, last)]):
            self.audio_clipboard_video, self.audio_clipboard = video, audio

    def _change_audio(self, track):
        self._remember()
        self.audio = track
        self.stop_audio()
        self._refresh_audio()
        self.wave_reload.start()

    def delete_audio(self):
        start, end, track = self._selection_audio()
        if self.scope is None and end > start:
            video_end = self._video_duration()
            if self.video_page is None or (self.frames and start >= video_end - .0005):
                self._change_audio(splice(track, start, end))
            elif self.frames:
                tail = end if end > video_end + .0005 else None
                self._remove_video_ranges([self._audio_frames(start, end)], audio_tail_end=tail)

    def paste_audio(self):
        if self.scope is None and self.audio_clipboard:
            position = self.audio_cursor.value()
            if self.frames and self.audio_clipboard_video:
                frame = min(int(length(self.video)), round(position * self.frames.fps))
                self._insert_video(frame, self.audio_clipboard_video, self.audio_clipboard)
            else:
                self._change_audio(splice(self._audio_track(), position, position, self.audio_clipboard))

    def set_volume(self):
        start, end, track = self._selection_audio()
        if self.scope is None and end > start:
            clips = [replace(clip, volume=self.volume.value() / 100) for clip in slice_track(track, start, end)]
            self._change_audio(splice(track, start, end, clips))

    def insert_audio(self):
        if self.scope is not None:
            return
        filters = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))
        name, _ = QFileDialog.getOpenFileName(self, "插入音频", "", f"音频文件 ({filters});;所有文件 (*.*)")
        if not name:
            return
        path = Path(name).resolve()
        position = self.audio_cursor.value()
        track = self._audio_track()
        def load(scope):
            info = probe(path, self.tools, scope)
            if not isinstance(info, AudioInfo) or not info.duration:
                raise MediaError("请选择可读取时长的音频。")
            return splice(track, position, position, [Clip(path, info.stream, 0, info.duration)])
        self._run("正在读取新音频…", load, self._change_audio)

    def undo(self):
        if self.pending_video_insert is not None:
            self.cancel_operation()
            return
        if self.scope is None and self.history:
            self._stop_playback()
            self.video, self.audio = self.history.pop()
            if self.frames:
                self._refresh_video()
            if self.audio_page:
                self._refresh_audio(reset=True)
                self.wave_reload.start()
            self._notice("已撤销上一次编辑。")

    def preview_audio(self):
        self._load_audio_wave()

    def _reload_audio_wave(self):
        if (not self.closing and self.isVisible() and self.tabs.currentWidget() is self.audio_page
                and self.wave_pending):
            self.preview_audio()

    def _focus_audio_selection(self):
        start, end, _ = self._selection_audio()
        if end <= start:
            self._notice("请选一段音频。")
            return
        loaded = self.audio_view.loaded_interval
        if loaded and loaded[0] <= start and end <= loaded[1]:
            self.audio_view.show_interval(start, end)
        else:
            self._load_audio_wave(focus=(start, end))

    def _load_audio_wave(self, *, focus=None, on_ready=None):
        if self.scope is not None:
            self.wave_pending = self.audio_available
            return
        self.wave_pending = False
        track = self._audio_track()
        loaded = self.audio_view.loaded_interval
        start = focus[0] if focus else (loaded[0] if loaded else self.audio_view.playhead)
        total = length(track)
        if not focus and not loaded and self.audio_view.overflow:
            boundary = self.audio_view.overflow[0]
            if not start <= boundary < start + 30:
                start = max(0, boundary - 20)
        start = min(start, max(0, total - 30))
        end = min(total, start + 30)
        selected = tuple(slice_track(track, start, end))
        if not selected:
            self._notice("请选一段音频。")
            return
        files = []
        for path in dict.fromkeys(clip.path for clip in selected if clip.path is not None):
            try:
                stamp = path.stat()
                if not S_ISREG(stamp.st_mode):
                    raise FileNotFoundError(path)
            except (FileNotFoundError, NotADirectoryError):
                self._audio_wave_error(f"找不到音频文件「{path.name}」，请重新导入。", str(path))
                self.missing_source.emit(path)
                return
            except OSError as exc:
                self._audio_wave_error(f"无法读取音频文件「{path.name}」。", str(exc))
                return
            files.append((path, stamp.st_mtime_ns, stamp.st_size))
        key = selected, tuple(files)
        interval = start, end
        if waveform := self.wave_cache.get(key):
            self.wave_cache.move_to_end(key)
            if self.audio_view.waveform is None and self.status.text() == self.audio_view.text():
                self.status.clear()
                self.status.setToolTip("")
            self._audio_ready(waveform, interval, focus, on_ready)
            return

        def ready(waveform):
            if waveform is None:
                self._audio_wave_error("这个区间没有可显示的音频。")
                return
            self.wave_cache[key] = waveform
            if len(self.wave_cache) > 2:
                self.wave_cache.popitem(last=False)
            self._audio_ready(waveform, interval, focus, on_ready)

        self._run("正在生成音频波形…", lambda scope: audio_preview(selected, self.tools, scope), ready,
                  on_error=lambda error: self._audio_wave_error(
                      "波形加载已取消。" if error == "任务已取消" else "波形加载失败。右键可刷新。", error))

    def _audio_wave_error(self, message, detail=""):
        self.wave_pending = False
        self.audio_view.set_waveform(None, None)
        self.audio_view.setText(message)
        self.audio_view.setToolTip(detail)
        self._notice(message)
        self.status.setToolTip(detail)

    def _audio_ready(self, waveform, interval, focus, on_ready):
        self.wave_pending = False
        if waveform is not self.audio_view.waveform or interval != self.audio_view.loaded_interval:
            self.audio_view.set_waveform(waveform, interval)
        if waveform:
            if not interval[0] <= self.audio_view.playhead < interval[1]:
                self._set_audio_position(interval[0])
            if focus:
                self.audio_view.show_interval(focus[0], min(focus[1], interval[1]))
        self.audio_view.setToolTip("单击放置黑色插入线，拖动选取，右键打开编辑菜单。Ctrl + 滚轮缩放。")
        if on_ready and waveform and self.isVisible() and self.tabs.currentWidget() is self.audio_page:
            on_ready()

    def _update_audio_toolbar(self):
        if self.audio_page is None:
            return
        view = self.audio_view
        loaded = view.loaded_interval
        self.audio_play.setEnabled(bool(self.audio_available and sys.platform == "win32"
                                        and (self.scope is None or self.listen_requested)))
        span = view.interval[1] - view.interval[0] if loaded else 0
        ratio = (loaded[1] - loaded[0]) / span if span else 1
        factor = f"{ratio:,.0f}" if ratio >= 10 else f"{ratio:.1f}".rstrip("0").rstrip(".")
        self.zoom_ratio.setText(f"采样点 · {span * 1000:.2f} ms" if view.sample_spacing() >= 3 else f"{factor}×")
        self.zoom_in.setEnabled(bool(loaded and span > view.minimum_span() * 1.001))
        self.zoom_out.setEnabled(ratio > 1.001)
        self.zoom_reset.setEnabled(bool(self.audio_available or self.frames) and self.scope is None)

    def _seek_audio(self, position):
        self.stop_audio()
        self._set_audio_position(position)
        self.audio_cursor.setValue(position)

    def _set_audio_position(self, position):
        self.audio_view.playhead = position
        self.audio_view.reveal(position)
        self.audio_view.update()
        self.audio_position.setText(WaveView._time_label(position, 3))

    def _toggle_audio_playback(self):
        if self.playing or self.listen_requested:
            self.stop_audio()
            self._notice("已暂停。")
            return
        if self.scope is not None or not self.audio_available or sys.platform != "win32":
            return
        if not self.audio_view.loaded_interval:
            self._load_audio_wave(on_ready=self._toggle_audio_playback)
            return
        first, last = self.audio_view.loaded_interval
        start = self.audio_view.playhead
        if not first <= start < last - .001:
            start = first
        select_start, select_end, track = self._selection_audio()
        end = min(last, select_end) if select_start <= start < select_end else last
        self._play_audio_range(start, end, track)

    def play_audio_selection(self):
        start, end, track = self._selection_audio()
        if end <= start or self.scope is not None or sys.platform != "win32":
            return
        loaded = self.audio_view.loaded_interval
        if not loaded or not loaded[0] <= start < loaded[1]:
            self._load_audio_wave(focus=(start, end), on_ready=self.play_audio_selection)
            return
        self._play_audio_range(start, min(end, start + 30), track)

    def _play_audio_from(self, position):
        self._click_audio(position)
        track = self._audio_track()
        end = min(length(track), position + 30)
        self._play_audio_range(position, end, track)

    def _play_audio_range(self, start, end, track):
        if self.scope is not None or self.closing or sys.platform != "win32":
            return
        selected = slice_track(track, start, end)
        if not selected:
            return
        self._run("正在准备音频…",
                  lambda scope: render_preview_wav(selected, self.listen_wav, self.tools, scope),
                  lambda _: self._audio_playback_ready(start, end))
        self.listen_requested = True
        self.audio_play.setText("暂停")
        self._update_audio_toolbar()

    def _audio_playback_ready(self, start, end):
        if not self.listen_requested:
            return
        self.listen_requested = False
        import winsound

        try:
            winsound.PlaySound(str(self.listen_wav), winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
        except RuntimeError as exc:
            self.audio_play.setText("播放")
            self._notice(f"无法播放音频：{exc}")
            return
        self.playing = True
        self.audio_clock = start, end, monotonic()
        self._set_audio_position(start)
        self.audio_timer.start()
        self._notice("正在播放音频。")

    def _audio_tick(self):
        if self.audio_clock is None:
            return
        start, end, clock = self.audio_clock
        position = min(end, start + monotonic() - clock)
        self._set_audio_position(position)
        if position >= end:
            self.stop_audio()
            self._notice("播放结束。")

    def stop_audio(self):
        active = self.playing or self.listen_requested
        if self.listen_requested and self.scope is not None:
            self.scope.cancel()
        self.listen_requested = False
        self.audio_timer.stop()
        if self.playing:
            import winsound
            start, end, clock = self.audio_clock
            self._set_audio_position(min(end, start + monotonic() - clock))
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError:
                pass
            self.playing = False
        self.audio_clock = None
        if self.audio_page is not None:
            self.audio_play.setText("播放")
            self._update_audio_toolbar()
        if active:
            self.status.clear()

    def save_edit(self):
        if self.scope is not None:
            return
        video = self.video if self.tabs.currentWidget() is self.video_page else []
        audio = self._audio_track()
        options = self.output_options()
        if options.format in _AUDIO_ENCODERS:
            video = []
        if not video and not audio:
            self._notice("请先载入有效的帧或音频片段。")
            return
        fps = self.frames.fps if self.frames else 0
        canvas = self._canvas_for(video)
        self._run("正在保存编辑结果…",
                  lambda scope: render_edit(self.path, video, audio, fps, options, self.tools,
                                            scope, self.events.progress.emit, canvas=canvas,
                                            notice=self.events.notice.emit),
                  self._saved, exporting=True)

    def _sync_image_quality(self) -> None:
        """图片格式变化时刷新质量卡：无损格式只给说明，有损格式给百分比滑轨。"""
        if not hasattr(self, "image_quality"):
            return
        kind = self.image_format.currentData()
        lossless = kind in {"png", "gif_image", "bmp", "tiff"}
        if self.image_quality.value() and not lossless:
            self.image_quality.setValue(self.image_quality.value())
        self.image_quality.set_lossless(lossless)
        self.image_quality_card.setVisible(True)

    def _picture_encoding(self):
        index = self.picture_preset.currentIndex()
        _, quality, png_compression, _ = _PICTURE_PRESETS[index]
        if hasattr(self, "image_quality") and self.image_format.currentData() != "png":
            chosen = self.image_quality.value()
            if chosen:
                quality = int(chosen)
        limit = self.size.value() * 1000 if index == 2 else 0
        return quality, limit, png_compression

    def _picture_source(self):
        if isinstance(self.info, ImageInfo):
            return self.path, None, 0, self.info.width, self.info.height
        if not self.frames:
            return None
        reference = self._frame_ref(self._picture_frame_number())
        if reference is None:
            return None
        path, index = reference
        info = self.video_sources[path].info
        return path, info, index, info.width, info.height

    def _update_picture_dimensions(self):
        source = self._picture_source()
        if source is None:
            if hasattr(self, "picture_dimensions"):
                self.picture_dimensions.setText("等待视频帧就绪")
            return
        _, _, _, width, height = source
        if width < 1 or height < 1:
            self.picture_dimensions.setText("未知")
            return
        edge = int(self.resolution.currentData() or 0)
        ratio = min(1.0, edge / max(width, height)) if edge else 1.0
        out_width = max(1, round(width * ratio))
        out_height = max(1, round(height * ratio))
        if self.image_format.currentData() == "ico":
            out_width = out_height = 256
        self.picture_dimensions.setText(f"{out_width} × {out_height} 像素")

    def _schedule_picture_estimate(self, *_):
        if not hasattr(self, "picture_estimate"):
            return
        self.picture_estimate_generation += 1
        if self.picture_estimate_scope is not None:
            self.picture_estimate_scope.cancel()
            self.picture_estimate_scope = None
        if self.tabs.currentWidget() not in (self.video_page, self.image_page):
            return
        self._update_picture_dimensions()
        self.picture_estimate.setText("正在计算文件大小…")
        self.picture_estimate.setToolTip("")
        self.picture_estimate_timer.start()

    def _estimate_picture_size(self):
        if self.closing:
            return
        source = self._picture_source()
        if source is None:
            self.picture_estimate.setText("等待视频帧就绪")
            return
        path, video_info, index, _, _ = source
        if not path.is_file():
            self.picture_estimate.setText("找不到源文件")
            self.picture_estimate.setToolTip(str(path))
            return
        kind = self.image_format.currentData()
        quality, limit, png_compression = self._picture_encoding()
        max_edge = int(self.resolution.currentData() or 0)
        generation = self.picture_estimate_generation
        scope = Processes()
        self.picture_estimate_scope = scope
        self.picture_estimate_pool.submit(
            self._picture_size_worker, generation, path, video_info, index, kind,
            quality, limit, max_edge, png_compression, scope,
        )

    def _picture_size_worker(self, generation, path, video_info, index, kind,
                             quality, limit, max_edge, png_compression, scope):
        try:
            source = (frame_png(path, video_info, index, self.tools, scope, preview=False)
                      if video_info is not None else path)
            size = estimate_image_size(source, kind, quality=quality, max_edge=max_edge,
                                       limit=limit, png_compression=png_compression)
        except Cancelled:
            return
        except (MediaError, OSError, ValueError) as exc:
            self.events.picture_estimate.emit(generation, 0, str(exc))
        else:
            self.events.picture_estimate.emit(generation, size, "")

    @Slot(int, int, str)
    def _picture_estimated(self, generation, size, error):
        if self.closing or generation != self.picture_estimate_generation:
            return
        self.picture_estimate_scope = None
        if error:
            self.picture_estimate.setText("暂时无法估算")
            self.picture_estimate.setToolTip(error)
            if isinstance(self.info, ImageInfo) and hasattr(self, "estimate_rows"):
                for value in self.estimate_rows.values():
                    value.setText("—")
        elif size >= 1_000_000:
            self.picture_estimate.setText(f"约 {size / 1_000_000:.2f} MB（{size / 1000:.0f} KB）")
        else:
            self.picture_estimate.setText(f"约 {size / 1000:.1f} KB")
        if isinstance(self.info, ImageInfo) and hasattr(self, "estimate_rows"):
            self._apply_estimate(self.info.size or 1, size)

    def _update_file_details(self):
        if hasattr(self, "details_view"):
            picture = None
            preview = getattr(self, "preview", None)
            if preview is not None:
                pixmap = preview.pixmap()
                if pixmap is not None and not pixmap.isNull() and pixmap.width() > 16:
                    picture = pixmap
            self.details_view.set_media(self.path, self.info, picture=picture)

    def save_image(self):
        if self.scope is not None:
            return
        is_video = isinstance(self.info, VideoInfo)
        if is_video and not self.frames:
            return
        frame = self._picture_frame_number() if is_video else None
        path, index = self._frame_ref(frame) if is_video else (self.path, 0)
        video = self.video_sources[path].info if is_video else None
        kind = self.image_format.currentData()
        quality, limit, png_compression = self._picture_encoding()
        directory, max_edge = self.directory, self.resolution.currentData()
        output_stem = self._get_output_stem()
        def save(scope):
            return save_picture(self.path, video, index, kind, quality, limit, directory,
                                self.tools, scope, max_edge=max_edge, frame_path=path,
                                png_compression=png_compression, output_stem=output_stem)
        message = f"正在保存第 {frame} 帧…" if frame is not None else "正在保存图片…"
        self._run(message, save, lambda path: self._saved(path, limit, frame=frame), exporting=True)

    def _saved(self, path, limit=0, *, frame=None):
        self.saved.emit(path)
        size = path.stat().st_size
        suffix = f" · {size / 1000:.1f} KB"
        if limit and size > limit:
            suffix += " · 超出目标，已保留最小版本"
        item = f"第 {frame} 帧 · {path.name}" if frame is not None else path.name
        self._notice(f"已保存 {item}{suffix}，并加入媒体队列。")
        self.status.setToolTip(str(path))

    def suspend(self):
        if self.fullscreen_player is not None:
            self.fullscreen_player.reject()
        self._stop_playback()
        self.stop_audio()
        self.seek_timer.stop()
        self.wave_reload.stop()
        self.picture_estimate_timer.stop()
        self.picture_estimate_generation += 1
        if self.picture_estimate_scope is not None:
            self.picture_estimate_scope.cancel()
            self.picture_estimate_scope = None
        if self.frame_scope is not None:
            self.frame_scope.cancel()

    def resume(self):
        if self.frames and self._frame_preview_panel() is not None:
            self.seek_timer.start()
            self._schedule_picture_estimate()
        elif isinstance(self.info, ImageInfo) and self.tabs.currentWidget() is self.image_page:
            self._schedule_picture_estimate()
        elif self.audio_page is not None and self.tabs.currentWidget() is self.audio_page:
            if self.audio_available:
                self.preview_audio()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.suspend()
        if self.filmstrip is not None:
            self.filmstrip.shutdown()
        if self.frame_scope is not None:
            self.frame_scope.cancel()
        if self.frame_pool is not None:
            self.frame_pool.shutdown(wait=True, cancel_futures=True)
        if self.picture_estimate_scope is not None:
            self.picture_estimate_scope.cancel()
        self.picture_estimate_timer.stop()
        self.picture_estimate_pool.shutdown(wait=True, cancel_futures=True)
        for animation in self.findChildren(Motion):
            animation.stop()
        if self.scope:
            self.scope.cancel()
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.on_done = None
        self.scratch.cleanup()

    def reject(self):
        if self.embedded:
            self.return_requested.emit()
        else:
            super().reject()

    def done(self, result):
        self.shutdown()
        super().done(result)
