"""帧转的 PySide6 桌面窗口。"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from os.path import isdir
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import (QEvent, QMimeData, QObject, QPointF, QProcess, QRectF, QSignalBlocker, QSize, Qt,
                            QUrl, Signal, Slot)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QDrag, QFont, QGuiApplication, QIcon,
                           QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut)
from PySide6.QtWidgets import (
    QDialog, QLineEdit, QMenu,QPushButton, QScrollArea, QVBoxLayout, 
    QApplication, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QScrollArea,
    QPlainTextEdit, QSizePolicy, QStackedWidget, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from .media import (AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, LOSSLESS_AUDIO_FORMATS, VIDEO_EXTENSIONS,
                    VIDEO_FRAME_RATES, VIDEO_RESOLUTIONS, WAVEFORM_SECONDS)
from .media import AudioInfo, ImageInfo, VideoInfo
from .icons import INK, icon as line_icon, pixmap as line_pixmap
from .media import (Cancelled, Export, MediaError, MediaInfo, Processes, Tools,
                    hardware_acceleration_choices)
from .media import convert, filename_problem, media_path_missing, probe, thumbnail
from .details import DetailsDialog
from .motion import (QUEUED_MEDIA, Button, Combo, DirectoryButton, DropArea, Motion,
                     NavigationBar, PageTransition, QualityRow, SpinBox, local_files)
from .editor import Editor
from .theme import STYLE, C, app_icon, brand_mark, chevron, shadow

# 队列行的列宽。表头和行共用这几个数，改一处两边一起对齐。
THUMB_SIZE = QSize(56, 36)
W_DURATION, W_SIZE, W_FORMAT, W_STATE, W_REMOVE = 66, 72, 132, 78, 28
ROW_HEIGHT = 58
ROW_SPACING = 8
ROW_MARGINS = (10, 6, 8, 6)
QUEUE_COLUMN_HEIGHT = 26
RAIL_MIN_WIDTH = 232
CONVERSION_FOOTER_HEIGHT = 65
PREVIEW_FOOTER_HEIGHT = 56

VIDEO_FORMATS = (("mp4", "MP4 · H.264"), ("mkv_hevc", "MKV · H.265"), ("mkv", "MKV · H.264"),
                 ("webm", "WebM · VP9"), ("mov", "MOV · H.264"), ("avi", "AVI · MPEG-4"), ("gif", "GIF · 动图"))
AUDIO_FORMATS = (("mp3", "MP3"), ("m4a", "M4A · AAC"), ("wav", "WAV · PCM"),
                 ("flac", "FLAC · 无损"), ("opus", "Opus"), ("ogg", "OGG · Vorbis"))
IMAGE_FORMATS = (("png", "PNG · 无损"), ("jpg", "JPEG · 照片"), ("webp", "WebP"), ("avif", "AVIF"),
                 ("tiff", "TIFF"), ("bmp", "BMP"), ("gif_image", "GIF · 静态"), ("ico", "ICO · 图标"), ("pdf", "PDF · 单页"))
RESIZABLE_FORMATS = {key for key, _ in VIDEO_FORMATS + IMAGE_FORMATS}
FRAME_RATE_FORMATS = {key for key, _ in VIDEO_FORMATS if key != "gif"}


def formats_for(info: MediaInfo | None):
    if isinstance(info, VideoInfo):
        if info.audio_stream is not None:
            return VIDEO_FORMATS + tuple((key, f"提取音频 · {key.upper()}") for key, _ in AUDIO_FORMATS)
        return VIDEO_FORMATS
    if isinstance(info, AudioInfo):
        return AUDIO_FORMATS
    if isinstance(info, ImageInfo) and info.codec != "PDF":
        return IMAGE_FORMATS
    return ()


@dataclass
class Job:
    key: str
    path: Path
    info: MediaInfo | None = None
    picture: QPixmap | None = None
    output: Path | None = None
    output_info: MediaInfo | None = None
    output_note: str = ""
    state: str = "读取中"
    detail: str = ""
    percent: int = 0
    queued: bool = True
    output_format: str = ""
    output_stem: str = ""
    export_settings: Export | None = None


class Events(QObject):
    inspected = Signal(str, object, str)
    thumb_ready = Signal(str, bytes)
    phase = Signal(str, str)
    progress = Signal(str, int)
    exported = Signal(str, object, object, str, str)
    completed = Signal(bool)


def label(text: str = "", *, muted: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if muted:
        widget.setProperty("muted", True)
    return widget


class ElidedLabel(QLabel):
    def __init__(self, text="", *, mode=Qt.TextElideMode.ElideRight):
        super().__init__()
        self.mode = mode
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    def set_full_text(self, text):
        self.full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(self.full_text, self.mode, self.contentsRect().width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


def duration(seconds: float) -> str:
    if seconds <= 0:
        return "未知"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes:02}:{rest:02}" if seconds >= 60 else f"{seconds:.1f} 秒"


def metadata(info: MediaInfo) -> str:
    if isinstance(info, ImageInfo):
        return f"{info.width}×{info.height}  ·  {info.codec}" if info.width and info.height else info.codec
    if isinstance(info, AudioInfo):
        fields = [info.codec, duration(info.duration)]
        if info.sample_rate:
            fields.append(f"{info.sample_rate / 1000:g} kHz")
        if info.channels:
            fields.append({1: "单声道", 2: "立体声"}.get(info.channels, f"{info.channels} 声道"))
        return "  ·  ".join(fields)
    rate = f"{info.fps:.2f}".rstrip("0").rstrip(".") + " 帧/秒" if info.fps else "帧率未知"
    return f"{info.width}×{info.height}  ·  {duration(info.duration)}  ·  {info.codec}  ·  {rate}"


def file_size(size: int) -> str:
    return f"{size / 1_000_000:.2f} MB" if size >= 1_000_000 else f"{size / 1_000:.1f} KB"


def icon_label(name: str, size: int = 16):
    """占位用的线框图标（不带交互）。"""
    widget = QLabel()
    widget.setObjectName("lineIcon")
    widget.setFixedSize(size, size)

    widget.setPixmap(line_pixmap(name, size, INK))
    return widget


def icon_button(name: str, callback, tooltip: str = "", size: int = 17):
    """只有图标的扁平按钮，尺寸和普通按钮的内边距对齐。"""
    button = Button("")
    button.setProperty("flat", True)
    button.setProperty("iconOnly", True)
    button.setFixedSize(28, 28)
    button.setIcon(line_icon(name, size))
    button.setIconSize(QSize(size, size))
    button.setToolTip(tooltip)
    button.clicked.connect(callback)
    return button


def field(title: str, widget):
    """带小标题的控件单元格：标题在上、控件在下，两列并排时不显得拥挤。"""
    holder = QWidget()
    holder.setObjectName("fieldCell")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    caption = QLabel(title)
    caption.setObjectName("fieldLabel")
    layout.addWidget(caption)
    layout.addWidget(widget)
    holder.field_label = caption
    return holder


def clock(seconds: float) -> str:
    """表格里的时长列：统一成 00:00 形式，未知就给一个占位符。"""
    if not seconds or seconds <= 0:
        return "—"
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def rail_meta(job) -> str:
    """媒体预览左侧队列的第二行：时长 · 大小。"""
    if not job.info:
        return "正在读取…" if job.state == "读取中" else "无法读取"
    parts = []
    length = clock(getattr(job.info, "duration", 0))
    if length != "—":
        parts.append(length)
    parts.append(file_size(job.info.size))
    return " · ".join(parts)


def summary(info: MediaInfo) -> str:
    """行内的一行摘要：时长已经单独成列，这里不再重复。"""
    if isinstance(info, ImageInfo):
        return f"{info.width}×{info.height}  ·  {info.codec}" if info.width and info.height else info.codec
    if isinstance(info, AudioInfo):
        fields = [info.codec]
        if info.sample_rate:
            fields.append(f"{info.sample_rate / 1000:g} kHz")
        if info.channels:
            fields.append({1: "单声道", 2: "立体声"}.get(info.channels, f"{info.channels} 声道"))
        return "  ·  ".join(fields)
    rate = f"{info.fps:g} 帧/秒" if info.fps else "帧率未知"
    return f"{info.width}×{info.height}  ·  {info.codec}  ·  {rate}"


def card() -> tuple[QFrame, QVBoxLayout]:
    """白底细边框的圆角卡片，主窗口两块内容区都用它。"""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    return frame, layout


def section(text: str) -> QLabel:
    widget = label(text)
    widget.setObjectName("sectionTitle")
    return widget


def column_title(text: str) -> QLabel:
    widget = label(text)
    widget.setObjectName("columnTitle")
    return widget


def divider() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: #E5EDF6; border: none;")
    return line


def state_tone(job: Job) -> str:
    if job.state in ("已完成", "成品"):
        return "success"
    if job.state in ("转换失败", "读取失败"):
        return "danger"
    if job.state == "转换中":
        return "accent"
    if job.state in ("已取消", "未完成"):
        return "warning"
    return "neutral"


PILL_TONES = {
    "neutral": (C.track, C.text2),
    "accent": (C.accent_soft, C.accent),
    "success": (C.success_soft, C.success),
    "warning": (C.warning_soft, C.warning),
    "danger": (C.danger_soft, C.danger),
}


class StatusPill(QWidget):
    """状态徽标：底色表达结果，文字保留原始状态名。"""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(W_STATE, 22)
        self.text, self.tone = "", "neutral"

    def set_state(self, text: str, tone: str) -> None:
        if (text, tone) != (self.text, self.tone):
            self.text, self.tone = text, tone
            self.update()

    def paintEvent(self, event) -> None:
        fill, ink = PILL_TONES.get(self.tone, PILL_TONES["neutral"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 3.5, -0.5, -3.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(ink)
        text = self.fontMetrics().elidedText(self.text, Qt.TextElideMode.ElideRight,
                                             int(rect.width()) - 12)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


def fit_pixmap(source: QPixmap, size: QSize, mode: Qt.AspectRatioMode = Qt.AspectRatioMode.KeepAspectRatio) -> QPixmap:
    """先归一化到 1.0 像素比，再按屏幕比例缩放，避免高 DPI 下缩略图被裁一截。"""
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    plain = QPixmap(source)
    plain.setDevicePixelRatio(1.0)
    target = QSize(max(1, round(size.width() * ratio)), max(1, round(size.height() * ratio)))
    scaled = plain.scaled(target, mode, Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled


def queue_thumbnail(job):
    thumbnail = QPixmap(THUMB_SIZE)
    thumbnail.fill(Qt.GlobalColor.transparent)
    painter = QPainter(thumbnail)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    shape = QPainterPath()
    shape.addRoundedRect(QRectF(thumbnail.rect()), 4, 4)
    painter.fillPath(shape, C.sunken)
    painter.setClipPath(shape)
    if job.picture is not None:
        image = fit_pixmap(job.picture, THUMB_SIZE)
        painter.drawPixmap((thumbnail.width() - image.width()) // 2,
                           (thumbnail.height() - image.height()) // 2, image)
    else:
        mark = {VideoInfo: "视频", AudioInfo: "音频", ImageInfo: "图片"}.get(type(job.info), "媒体")
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(C.text3)
        painter.drawText(thumbnail.rect(), Qt.AlignmentFlag.AlignCenter, mark)
    painter.end()
    return thumbnail


def paint_queue_highlight(painter, rect, selection, hovered):
    if selection <= .004 and not hovered:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(C.accent_soft if selection > .004 else C.sunken)
    painter.setOpacity(selection if selection > .004 else 1.0)
    painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
    if selection > .004:
        painter.setBrush(C.accent)
        painter.drawRoundedRect(QRectF(rect.left() + 2, rect.top() + 8, 3, rect.height() - 16), 1.5, 1.5)
    painter.restore()


class Row(QWidget):
    remove_requested = Signal(str)
    format_changed = Signal(str, str)

    def __init__(self, job: Job) -> None:
        super().__init__()
        self.job_key = job.key
        self.hovered = False
        self.selection_motion = Motion(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*ROW_MARGINS)
        layout.setSpacing(ROW_SPACING)
        self.thumb = label("媒体")
        self.thumb.setObjectName("thumb")
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setFixedSize(THUMB_SIZE)
        layout.addWidget(self.thumb)
        body = QVBoxLayout()
        body.setSpacing(1)
        body.setContentsMargins(0, 0, 0, 0)
        self.name = ElidedLabel(job.path.name, mode=Qt.TextElideMode.ElideMiddle)
        self.name.setStyleSheet("font-weight: 600;")
        body.addWidget(self.name)
        self.info = ElidedLabel()
        self.info.setProperty("muted", True)
        body.addWidget(self.info)
        layout.addLayout(body, 1)
        self.length_label = label("—")
        self.length_label.setProperty("muted", True)
        self.length_label.setFixedWidth(W_DURATION)
        layout.addWidget(self.length_label)
        self.size_label = label("—")
        self.size_label.setProperty("muted", True)
        self.size_label.setFixedWidth(W_SIZE)
        layout.addWidget(self.size_label)
        self.state = StatusPill()
        layout.addWidget(self.state)
        self.format = Combo()
        self.format.setObjectName("rowFormat")
        self.format.setFixedWidth(W_FORMAT)
        self.format.setAccessibleName(f"{job.path.name} 的输出格式")
        self.format.currentIndexChanged.connect(self._format_changed)
        layout.addWidget(self.format)
        self.remove_button = Button("×")
        self.remove_button.setProperty("flat", True)
        self.remove_button.setObjectName("rowRemove")
        self.remove_button.setFixedSize(W_REMOVE, 26)
        self.remove_button.setToolTip("从队列移除（Delete）")
        self.remove_button.setAccessibleName(f"从队列移除 {job.path.name}")
        self.remove_button.clicked.connect(self._request_remove)
        layout.addWidget(self.remove_button)
        self.update_job(job)

    def enterEvent(self, event) -> None:
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def _request_remove(self) -> None:
        self.remove_requested.emit(self.job_key)

    def _format_changed(self) -> None:
        if key := self.format.currentData():
            self.format_changed.emit(self.job_key, key)

    def set_formats(self, job: Job, choices) -> None:
        with QSignalBlocker(self.format):
            self.format.clear()
            if not job.output_format and choices:
                job.output_format = choices[0][0]
            for key, title in choices:
                self.format.addItem(title, key)
            self.format.setCurrentIndex(self.format.findData(job.output_format))

    def update_job(self, job: Job) -> None:
        self.state.set_state(f"{job.percent}%" if job.state == "转换中" else job.state, state_tone(job))
        if job.info:
            self.info.set_full_text(summary(job.info))
            self.length_label.setText(clock(getattr(job.info, "duration", 0)))
            self.size_label.setText(file_size(job.info.size))
        else:
            detail = "正在读取媒体信息…" if job.state == "读取中" else "无法读取媒体信息"
            self.info.set_full_text(detail)
            self.length_label.setText("—")
            self.size_label.setText("—")
        self.name.setToolTip(job.path.name)
        self.thumb.setPixmap(queue_thumbnail(job))

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        paint_queue_highlight(painter, QRectF(self.rect()), self.selection_motion.value, self.hovered)
        painter.end()


class QueueDelegate(QStyledItemDelegate):
    """媒体预览里的窄队列：左边缩略图，右边文件名与「时长 · 大小」。"""

    META_ROLE = Qt.ItemDataRole.UserRole + 1

    def sizeHint(self, option, index):
        return QSize(0, ROW_HEIGHT)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        paint_queue_highlight(painter, rect, float(selected), hovered)

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        left, top, right, bottom = ROW_MARGINS
        thumb = QRectF(rect.left() + left, rect.center().y() - THUMB_SIZE.height() / 2,
                       THUMB_SIZE.width(), THUMB_SIZE.height())
        if isinstance(icon, QIcon):
            pixmap = icon.pixmap(THUMB_SIZE)
            painter.drawPixmap(thumb, pixmap, QRectF(pixmap.rect()))

        text_left = thumb.right() + ROW_SPACING
        width = max(0.0, rect.right() - text_left - right)
        font = QFont(option.font)
        font.setPixelSize(13)
        bold = QFont(font)
        bold.setWeight(QFont.Weight.DemiBold)
        painter.setFont(bold)
        painter.setPen(C.text)
        name = font_metrics_elide(painter, index.data(Qt.ItemDataRole.DisplayRole) or "", width)
        painter.drawText(QRectF(text_left, rect.top() + top, width, rect.height() / 2 - top),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), name)
        painter.setFont(font)
        painter.setPen(C.text2)
        meta = font_metrics_elide(painter, index.data(self.META_ROLE) or "", width)
        painter.drawText(QRectF(text_left, rect.center().y(), width, rect.height() / 2 - bottom),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), meta)
        painter.restore()


def font_metrics_elide(painter: QPainter, text: str, width: float) -> str:
    metrics = painter.fontMetrics()
    return metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, int(width))


class PreviewFiles(QListWidget):
    def __init__(self, lookup_media):
        super().__init__()
        self.lookup_media = lookup_media
        self.pressed_media = None
        self.setMouseTracking(True)
        self.setItemDelegate(QueueDelegate(self))
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and item is not None:
            # Opening the item on press would change the target editor before dragging.
            self.pressed_media = item, event.position().toPoint()
            self.setFocus()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.pressed_media and event.buttons() & Qt.MouseButton.LeftButton:
            item, start = self.pressed_media
            if (event.position().toPoint() - start).manhattanLength() >= QApplication.startDragDistance():
                key = item.data(Qt.ItemDataRole.UserRole)
                if self.lookup_media(key):
                    self.pressed_media = None
                    mime = QMimeData()
                    mime.setData(QUEUED_MEDIA, key.encode("ascii"))
                    drag = QDrag(self)
                    drag.setMimeData(mime)
                    drag.setPixmap(item.icon().pixmap(self.iconSize()))
                    drag.exec(Qt.DropAction.CopyAction)
                    drag.deleteLater()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.pressed_media:
            item, _ = self.pressed_media
            self.pressed_media = None
            if self.itemAt(event.position().toPoint()) is item:
                self.setCurrentItem(item)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class BatchFormatMenu(QFrame):
    """批量格式用的分类弹出菜单：标题不可选、分组有分隔线、内部滚动。"""

    picked = Signal(str)

    def __init__(self, groups, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("batchMenu")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(280)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(min(392, 26 + sum(34 * len(items) + 34 for _, items in groups)))
        body = QWidget()
        inner = QVBoxLayout(body)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(2)
        for index, (title, items) in enumerate(groups):
            if index:
                line = QFrame()
                line.setObjectName("menuDivider")
                line.setFixedHeight(1)
                inner.addWidget(line)
            header = QLabel(title)
            header.setObjectName("menuGroup")
            header.setFixedHeight(28)
            inner.addWidget(header)
            for data, label in items:
                button = QPushButton(label)
                button.setObjectName("menuChoice")
                button.setProperty("formatKey", data)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setFixedHeight(34)
                button.clicked.connect(lambda _=False, key=data: self._pick(key))
                inner.addWidget(button)
        inner.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        from PySide6.QtGui import QColor
        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(28)
        glow.setOffset(0, 6)
        glow.setColor(QColor(30, 60, 110, 46))
        self.setGraphicsEffect(glow)

    def _pick(self, key: str) -> None:
        self.picked.emit(key)
        self.close()


def validate_filename(stem: str, source) -> str:
    """返回错误提示，空串表示可用。"""
    if problem := filename_problem(stem):
        return problem
    target = source.with_name(stem + source.suffix)
    if target.exists():
        return "该文件夹已存在同名文件，请使用其他名称。"
    return ""


class RenameDialog(QDialog):
    def __init__(self, stem: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("重命名文件")
        self.setModal(True)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel("文件名"))
        self.field = QLineEdit(stem)
        self.field.selectAll()
        self.field.setMinimumHeight(30)
        layout.addWidget(self.field)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = Button("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        confirm = Button("确定")
        confirm.setObjectName("primary")
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)

    def value(self) -> str:
        return self.field.text()


class MainWindow(QMainWindow):
    def __init__(self, tools: Tools | None, tool_error: str = "") -> None:
        super().__init__()
        self.tools = tools
        self.closing = False
        self.jobs: dict[str, Job] = {}
        self.rows: dict[str, Row] = {}
        self.items: dict[str, QListWidgetItem] = {}
        self.preview_items: dict[str, QListWidgetItem] = {}
        self.editors: dict[str, Editor] = {}
        self.mounted_editor: Editor | None = None
        self.details_dialog: DetailsDialog | None = None
        self.output_directory: Path | None = None
        self.scope: Processes | None = None
        self.inspections = Processes()
        self.reader_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="frameflow-read")
        self.export_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frameflow-export")
        self.events = Events(self)
        self.events.inspected.connect(self._inspected)
        self.events.thumb_ready.connect(self._show_thumbnail)
        self.events.phase.connect(self._phase)
        self.events.progress.connect(self._progress)
        self.events.exported.connect(self._exported)
        self.events.completed.connect(self._completed)
        self.setWindowTitle("帧转 FrameFlow · 媒体格式转换器")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(940, 720)
        self.resize(1510, 930)
        self.setAcceptDrops(True)
        self._build()
        self._install_queue_menus()
        if tool_error:
            self.status.setText(tool_error)
            self.status.setWordWrap(True)
        self._refresh_controls()

    def _build(self) -> None:
        base = QWidget()
        base.setObjectName("shell")
        self.setCentralWidget(base)
        self.setStyleSheet(STYLE)
        self._build_menu()
        root = QVBoxLayout(base)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        self.page_host = QWidget()
        self.page_host.setObjectName("pageWorkspace")
        self.page_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self._build_preview_rail()
        self.pages = QStackedWidget()
        self.pages.setObjectName("pageWorkspace")
        self.pages.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.preview_undo = QShortcut(QKeySequence(QKeySequence.StandardKey.Undo), self.page_host)
        self.preview_undo.setContext(Qt.ShortcutContext.WindowShortcut)
        self.preview_undo.setEnabled(False)
        self.preview_undo.activated.connect(self._undo_preview_edit)
        self.pages.currentChanged.connect(self._update_preview_undo)
        root.addWidget(self.page_host, 1)
        self.conversion_page = QWidget()
        self.pages.addWidget(self.conversion_page)
        self.empty_preview = label("请先在格式转换页选择一个媒体文件。", muted=True)
        self.empty_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pages.addWidget(self.empty_preview)
        queue, queue_layout = card()
        self.queue_card = queue
        shadow(queue)
        header_row, self.count, self.add_button = self._add_queue_header(queue_layout)
        self.queue_tools = QWidget()
        queue_tools = QHBoxLayout(self.queue_tools)
        queue_tools.setContentsMargins(0, 0, 0, 0)
        queue_tools.setSpacing(8)
        queue_tools.addWidget(label("批量格式", muted=True))
        self.batch_format = Combo()
        self.batch_format.setObjectName("batchFormat")
        self.batch_format.setAccessibleName("批量格式")
        self.batch_format.setFixedWidth(150)
        self.batch_format.addItem("选择输出格式…")
        self.batch_format.setFixedWidth(190)
        for group_title, group in (("图片", IMAGE_FORMATS), ("视频", VIDEO_FORMATS),
                                   ("音频", AUDIO_FORMATS)):
            self.batch_format.insertSeparator(self.batch_format.count())
            self.batch_format.addItem(f"— {group_title} —")
            header_index = self.batch_format.count() - 1
            item = self.batch_format.model().item(header_index)
            if item is not None:
                item.setEnabled(False)
            for key, title in group:
                self.batch_format.addItem(title, key)
        self.batch_format.activated.connect(self._apply_batch_format)
        self.batch_format.hide()
        queue_tools.addWidget(self.batch_format)
        self.batch_button = Button("选择输出格式…")
        self.batch_button.setFixedWidth(190)
        self.batch_button.clicked.connect(self._show_batch_menu)
        queue_tools.addWidget(self.batch_button)
        self.batch_settings_button = Button("批量参数")
        self.batch_settings_button.clicked.connect(self._show_batch_settings)
        queue_tools.addWidget(self.batch_settings_button)
        header_row.addWidget(self.queue_tools)

        self.column_header = QFrame()
        self.column_header.setObjectName("columnHeader")
        self.column_header.setFixedHeight(QUEUE_COLUMN_HEIGHT)
        self.column_header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.column_row = QHBoxLayout(self.column_header)
        self.column_row.setContentsMargins(ROW_MARGINS[0], 0, ROW_MARGINS[2], 0)
        self.column_row.setSpacing(ROW_SPACING)
        gap = QWidget()
        gap.setFixedWidth(THUMB_SIZE.width())
        self.column_row.addWidget(gap)
        self.column_row.addWidget(column_title("名称"), 1)
        for text, width in (("时长", W_DURATION), ("大小", W_SIZE), ("状态", W_STATE),
                            ("输出格式", W_FORMAT), ("", W_REMOVE)):
            cell = column_title(text)
            cell.setFixedWidth(width)
            self.column_row.addWidget(cell)
        self.column_header.hide()
        queue_layout.addWidget(self.column_header)

        self.drop_area = DropArea()
        self.drop_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.drop_area.clicked.connect(self.choose_files)
        self.drop_area.files_dropped.connect(self.add_paths)
        queue_layout.addWidget(self.drop_area, 1)

        self.list = QListWidget()
        self.list.setObjectName("queue")
        self.list.setAlternatingRowColors(False)
        self.list.setUniformItemSizes(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._selection_changed)
        self.list.verticalScrollBar().rangeChanged.connect(self._sync_column_header)
        self.remove_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.list)
        self.remove_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.remove_shortcut.activated.connect(self._remove_selected)
        queue_layout.addWidget(self.list, 1)
        self.clear_button = self._add_queue_footer(queue_layout)
        self.list.installEventFilter(self)
        self.preview_files.installEventFilter(self)

        inspector, right = card()
        inspector.setObjectName("conversionInspector")
        shadow(inspector)
        body = QWidget()
        body.setObjectName("inspectorBody")
        panel_layout = QVBoxLayout(body)
        panel_layout.setContentsMargins(12, 9, 12, 8)
        panel_layout.setSpacing(5)
        scroll = QScrollArea()
        scroll.setObjectName("inspectorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        body.setMinimumWidth(1)
        scroll.viewport().setAutoFillBackground(False)
        scroll.viewport().setObjectName("exportViewport")
        right.addWidget(scroll, 1)
        self.inspector_scroll = scroll

        # 模块 A：选中文件概览——缩略图、文件名、一行参数，把高度留给下面的操作
        self.preview = label("添加文件后可在此预览")
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(68, 50)
        source_summary = QHBoxLayout()
        source_summary.setSpacing(8)
        source_summary.addWidget(self.preview)
        source_meta = QVBoxLayout()
        source_meta.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.video_name = ElidedLabel("尚未选择文件", mode=Qt.TextElideMode.ElideMiddle)
        self.video_name.setObjectName("inspectorName")
        name_row.addWidget(self.video_name, 1)
        self.inspector_pill = StatusPill()
        self.inspector_pill.hide()
        name_row.addWidget(self.inspector_pill)
        source_meta.addLayout(name_row)
        self.video_info = label("选中队列里的文件后，这里会显示缩略图和媒体参数。", muted=True)
        self.video_info.setObjectName("inspectorMeta")
        self.video_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.video_info.setWordWrap(False)
        source_meta.addWidget(self.video_info)
        source_summary.addLayout(source_meta, 1)
        panel_layout.addLayout(source_summary)
        settings_heading = QHBoxLayout()
        settings_heading.setSpacing(7)
        settings_heading.addWidget(icon_label("gear", 18))
        heading = label("输出设置")
        heading.setObjectName("conversionSettingsHeading")
        settings_heading.addWidget(heading, 1)
        panel_layout.addLayout(settings_heading)
        self.shared_settings = QVBoxLayout()
        self.shared_settings.setContentsMargins(0, 0, 0, 0)
        panel_layout.addLayout(self.shared_settings)

        self.batch_settings_dialog = QDialog(self)
        self.batch_settings_dialog.setWindowTitle("批量输出参数")
        self.batch_settings_dialog.setMinimumWidth(390)
        batch_body = QVBoxLayout(self.batch_settings_dialog)
        batch_body.addWidget(label("将下面的通用参数应用到队列中所有兼容文件。", muted=True))

        # 模块 B：参数分组，两列并排，默认尺寸下不需要滚动
        fields = QGridLayout()
        fields.setHorizontalSpacing(10)
        fields.setVerticalSpacing(8)
        fields.setColumnStretch(0, 1)
        fields.setColumnStretch(1, 1)

        self.resolution = Combo()
        self.resolution.addItem("原始尺寸", 0)
        for title, edge in VIDEO_RESOLUTIONS:
            self.resolution.addItem(title, edge)
        for edge in (480, 256):
            self.resolution.addItem(f"最长边 {edge} px", edge)
        self.resolution.addItem("自定义最长边…", -1)
        self.max_edge = 0
        self.resolution.activated.connect(self._choose_resolution)
        self.resolution.setToolTip("分辨率预设以标准横屏尺寸为参考，按原画面比例缩放。")
        self.output_stem_label = QLabel("输出文件名")
        self.output_stem_label.setObjectName("fieldLabel")
        self.output_stem_row = QWidget()
        stem_layout = QHBoxLayout(self.output_stem_row)
        stem_layout.setContentsMargins(0, 0, 0, 0)
        stem_layout.setSpacing(6)
        self.output_stem_edit = QLineEdit()
        self.output_stem_edit.setPlaceholderText("输出文件名")
        self.output_stem_edit.setMinimumHeight(30)
        self.output_stem_edit.textEdited.connect(self._output_stem_edited)
        stem_layout.addWidget(self.output_stem_edit, 1)
        self.output_stem_ext = QLabel("")
        self.output_stem_ext.setObjectName("outputStemExt")
        self.output_stem_ext.setMinimumWidth(42)
        stem_layout.addWidget(self.output_stem_ext)
        self.resolution_field = field("分辨率", self.resolution)
        fields.addWidget(self.resolution_field, 0, 0)

        self.output_fps = Combo()
        self.output_fps.addItem("保持原帧率", 0)
        for rate in VIDEO_FRAME_RATES:
            self.output_fps.addItem(f"{rate} FPS", rate)
        self.output_fps.setToolTip("适用于队列中的视频输出，保持播放速度。GIF 帧率由 GIF 设置控制。")
        self.output_fps_field = field("输出帧率", self.output_fps)
        fields.addWidget(self.output_fps_field, 0, 1)

        self.acceleration = Combo()
        self.acceleration.setAccessibleName("视频编码加速")
        self.acceleration.setToolTip("只列出当前 FFmpeg 构建支持的硬件编码器；其他格式使用 CPU 软件编码。")
        self.acceleration_field = field("视频编码加速", self.acceleration)
        fields.addWidget(self.acceleration_field, 1, 0)

        self.target_size = SpinBox()
        self.target_size.setRange(0, 10_000_000)
        self.target_size.setSingleStep(100)
        self.target_size.setSuffix(" KB")
        self.target_size.setSpecialValueText("不限")
        self.target_size.setMinimumHeight(28)
        self.target_size.setToolTip("每个输出文件的体积目标。动图和图片可能缩小尺寸。WAV 和 FLAC 按原音质导出。")
        self.target_size_field = field("目标体积", self.target_size)
        fields.addWidget(self.target_size_field, 1, 1)

        self.gif_preset = Combo()
        self.gif_preset.addItem("小体积 · 8 FPS", True)
        self.gif_preset.addItem("清晰 · 12 FPS", False)
        self.gif_preset.setToolTip("选择动图的帧率和默认尺寸。也可在分辨率里指定最长边。")
        self.gif_preset_label = label("GIF 设置")
        self.gif_field = field("GIF 设置", self.gif_preset)
        fields.addWidget(self.gif_field, 2, 0, 1, 2)

        self.quality = QualityRow("质量微调", 18, 34, 23,
                                  hint="CRF 越小画质越高，文件通常越大。",
                                  icon_name="sliders")
        self.quality.changed.connect(self._quality_changed)
        self.quality.settled.connect(self._quality_changed)
        self.quality.setToolTip("CRF 越小越清晰、文件越大；22–26 是日常分享的常用区间。\n"
                                "设了「目标体积」时以体积优先，CRF 只作为起点。")
        fields.addWidget(self.quality, 3, 0, 1, 2)
        batch_body.addLayout(fields)

        # 模块 C：输出文件——文件名一行，目录一行
        self.output_location = QWidget()
        self.output_location.setObjectName("outputLocationGroup")
        output_layout = QVBoxLayout(self.output_location)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(6)
        output_heading = QHBoxLayout()
        output_heading.setSpacing(6)
        output_heading.addWidget(section("输出文件"))
        output_heading.addStretch(1)
        self.reset_destination = Button("恢复源目录")
        self.reset_destination.setProperty("flat", True)
        self.reset_destination.clicked.connect(self._reset_directory)
        output_heading.addWidget(self.reset_destination)
        output_layout.addLayout(output_heading)
        self.output_name = ElidedLabel("添加文件后显示输出文件名", mode=Qt.TextElideMode.ElideMiddle)
        self.output_name.setObjectName("outputFileName")
        self.output_name.setProperty("muted", True)
        name_card = QFrame()
        name_card.setObjectName("outputLocationCard")
        name_row = QHBoxLayout(name_card)
        name_row.setContentsMargins(9, 4, 5, 4)
        name_row.setSpacing(6)
        folder_icon = icon_label("folder", 18)
        folder_icon.setFixedSize(28, 28)
        folder_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_row.addWidget(folder_icon)
        self.destination = DirectoryButton("默认使用各文件的源目录")
        self.destination.setObjectName("outputPath")
        self.destination.setProperty("flat", True)
        self.destination.clicked.connect(self._choose_directory)
        name_row.addWidget(self.destination, 1)
        self.pick_button = icon_button("folder", self._choose_directory, "选择输出文件夹")
        name_row.addWidget(self.pick_button)
        output_layout.addWidget(name_card)
        stem_head = QHBoxLayout()
        stem_head.setSpacing(6)
        stem_head.addWidget(self.output_stem_label)
        stem_head.addStretch(1)
        output_layout.addLayout(stem_head)
        output_layout.addWidget(self.output_stem_row)
        batch_body.addWidget(self.output_location)
        apply_batch = Button("应用到队列")
        apply_batch.setObjectName("primary")
        apply_batch.clicked.connect(self._apply_batch_settings)
        batch_body.addWidget(apply_batch)

        self.details_button = Button("详情")
        self.details_button.setIcon(line_icon("file", 15))
        self.details_button.setIconSize(QSize(15, 15))
        self.details_button.setToolTip("在新窗口里查看完整路径和全部媒体参数")
        self.details_button.clicked.connect(self._show_details)
        panel_layout.addStretch()

        # 操作区固定在卡片底部，展开文件详情时不会被顶到滚动视口之外。
        actions = QFrame()
        actions.setObjectName("inspectorActions")
        action_layout = QVBoxLayout(actions)
        action_layout.setContentsMargins(12, 5, 12, 7)
        action_layout.setSpacing(5)

        self.edit_button = Button("编辑 / 预览")
        self.edit_button.setObjectName("primary")
        self.edit_button.clicked.connect(self._edit_selected)
        self.queue_result_button = Button("加入下一轮转换")
        self.queue_result_button.clicked.connect(self._queue_selected)
        self.error_button = Button("查看错误详情")
        self.error_button.clicked.connect(self._show_error)
        self.locate_button = Button("定位文件")
        self.locate_button.clicked.connect(self._locate_output)
        action_layout.addWidget(self.queue_result_button)
        action_layout.addWidget(self.error_button)
        output_actions = QHBoxLayout()
        output_actions.setSpacing(5)
        output_actions.addWidget(self.edit_button, 2)
        output_actions.addWidget(self.details_button, 1)
        output_actions.addWidget(self.locate_button, 1)
        action_layout.addLayout(output_actions)
        right.addWidget(actions)

        self.inspector = inspector
        inspector.setFixedWidth(self._settings_width())
        inspector.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.page_transition = PageTransition(
            self.page_host, queue, self.preview_rail, inspector, self.pages, self._rail_width())
        self.page_transition.settings_location_changed.connect(self._move_page_settings)
        self.page_transition.settled.connect(self._settle_page)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(150, 6)
        self.cancel_button = Button("取消转换")
        self.cancel_button.clicked.connect(self.cancel_export)
        self.start_button = Button("开始转换")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumWidth(112)
        self.start_button.setIcon(line_icon("play", 15))
        self.start_button.setIconSize(QSize(15, 15))
        self.start_button.clicked.connect(self.start_export)
        self.status = label("添加媒体文件，选择输出格式后即可转换。", muted=True)
        self.status.setObjectName("status")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        footer = QFrame()
        footer.setObjectName("footer")
        self.footer = footer
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(16, 8, 16, 10)
        footer_row.setSpacing(12)
        footer_row.addWidget(self.status, 1)
        footer_row.addWidget(self.progress)
        footer_row.addWidget(self.cancel_button)
        footer_row.addWidget(self.start_button)
        self.page_footers = QStackedWidget()
        self.page_footers.addWidget(footer)
        self.page_footers.addWidget(self._build_preview_footer())
        self.page_footers.setFixedHeight(CONVERSION_FOOTER_HEIGHT)
        self.page_transition.animation.valueChanged.connect(self._resize_page_footer)
        self.page_transition.animation.valueChanged.connect(self.navigation.set_position)
        root.addWidget(self.page_footers)

    def _build_preview_footer(self):
        """预览页底栏：和转换页同一个布局——状态文字、细进度条、取消、开始。"""
        self.preview_footer = QFrame()
        self.preview_footer.setObjectName("footer")
        line = QHBoxLayout(self.preview_footer)
        line.setContentsMargins(16, 8, 16, 10)
        line.setSpacing(12)
        self.preview_status = ElidedLabel("就绪")
        line.addWidget(self.preview_status, 1)
        self.preview_progress = QProgressBar()
        self.preview_progress.setTextVisible(False)
        self.preview_progress.setRange(0, 100)
        self.preview_progress.setValue(0)
        self.preview_progress.setFixedSize(150, 6)
        line.addWidget(self.preview_progress)
        self.preview_percent = QLabel("")
        self.preview_percent.hide()
        self.preview_cancel = Button("取消转换")
        self.preview_cancel.clicked.connect(self._cancel_preview_task)
        self.preview_cancel.setEnabled(False)
        line.addWidget(self.preview_cancel)
        self.preview_start = Button("开始导出")
        self.preview_start.setObjectName("primary")
        self.preview_start.setMinimumWidth(112)
        self.preview_start.setIcon(line_icon("export", 15, "#FFFFFF", 1.8))
        self.preview_start.setIconSize(QSize(15, 15))
        self.preview_start.clicked.connect(self.start_export)
        line.addWidget(self.preview_start)
        return self.preview_footer

    def _build_menu(self) -> None:
        """不显示菜单栏，但快捷键仍然挂在窗口上。"""
        open_action = QAction("添加文件…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.choose_files)
        self.addAction(open_action)
        quit_action = QAction("退出", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(28, 11, 28, 11)
        row.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(brand_mark(40))
        mark.setFixedSize(40, 40)
        row.addWidget(mark)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        name = label("帧转 FrameFlow")
        name.setObjectName("brand")
        subtitle = label("视频 · 音频 · 图片格式转换", muted=True)
        subtitle.setObjectName("caption")
        titles.addWidget(name)
        titles.addWidget(subtitle)
        row.addLayout(titles)
        row.setStretch(1, 1)
        self.navigation = NavigationBar()
        self.navigation.addTab("格式转换")
        self.navigation.addTab("媒体预览")
        self.navigation.currentChanged.connect(self._navigate)
        row.addWidget(self.navigation)
        # 正常时不占版面，只在找不到 FFmpeg 时冒出来提醒一句
        self.tool_chip = label("找不到 FFmpeg，请检查 bin 目录")
        self.tool_chip.setStyleSheet("font-size: 11px; color: #C42B1C;")
        self.tool_chip.setToolTip("没有找到 ffmpeg/ffprobe，转换和缩略图都无法工作。")
        self.tool_chip.setVisible(self.tools is None)
        status_holder = QWidget()
        status_line = QHBoxLayout(status_holder)
        status_line.setContentsMargins(0, 0, 0, 0)
        status_line.addStretch()
        status_line.addWidget(self.tool_chip)
        status_holder.setMinimumWidth(236)
        row.addWidget(status_holder, 1)
        return header

    def _sync_column_header(self, *_args) -> None:
        bar = self.list.verticalScrollBar()
        extra = (bar.width() or bar.sizeHint().width()) if bar.maximum() > 0 else 0
        self.column_row.setContentsMargins(ROW_MARGINS[0], 0, ROW_MARGINS[2] + extra, 0)

    def _toggle_details(self) -> None:
        self._show_details()

    def _about(self) -> None:
        QMessageBox.about(
            self, "关于帧转 FrameFlow",
            "<b>帧转 FrameFlow</b><br>本地视频、音频、图片格式转换与轻量编辑。<br><br>"
            "转换由 FFmpeg 命令行完成，全程不联网、不上传文件。")

    def _choose_resolution(self, index):
        edge = self.resolution.itemData(index)
        if edge == -1:
            edge, ok = QInputDialog.getInt(self, "自定义分辨率", "最长边（像素）",
                                           self.max_edge or 1920, 16, 8192)
            if not ok:
                edge = self.max_edge
            if self.resolution.findData(edge) < 0:
                self.resolution.insertItem(self.resolution.count() - 1, f"最长边 {edge} px", edge)
            self.resolution.setCurrentIndex(self.resolution.findData(edge))
        self.max_edge = edge

    @Slot(str, str)
    def _set_format(self, key, kind):
        if self.scope is None and (job := self.jobs.get(key)) and job.queued:
            self._change_job_format(job, kind)
            self._show_selected()
            self._refresh_controls()

    def _show_batch_menu(self) -> None:
        groups = [("图片", IMAGE_FORMATS), ("视频", VIDEO_FORMATS), ("音频", AUDIO_FORMATS)]
        menu = BatchFormatMenu(groups, self)
        menu.picked.connect(self._pick_batch_format)
        origin = self.batch_button.mapToGlobal(self.batch_button.rect().bottomLeft())
        menu.move(origin.x(), origin.y() + 4)
        menu.show()

    def _pick_batch_format(self, key: str) -> None:
        index = self.batch_format.findData(key)
        if index >= 0:
            self.batch_button.setText(self.batch_format.itemText(index))
        self._apply_batch_format(key)

    def _apply_batch_format(self, index):
        kind = index if isinstance(index, str) else self.batch_format.itemData(index)
        if self.scope is not None or not kind:
            return
        applied = 0
        for job in self.jobs.values():
            if not job.queued:
                continue
            combo = self.rows[job.key].format
            match = combo.findData(kind)
            if match >= 0:
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(match)
                self._change_job_format(job, kind)
                applied += 1
        self.batch_format.setCurrentIndex(0)
        self.status.setText(f"已设置 {applied} 个兼容文件的输出格式。" if applied else "队列中没有兼容此格式的文件。")
        self._show_selected()
        self._refresh_controls()

    def _add_queue_header(self, panel):
        frame = QFrame()
        frame.setObjectName("panelHeader")
        frame.setFixedHeight(44)
        header = QHBoxLayout(frame)
        header.setContentsMargins(9, 0, 9, 0)
        header.setSpacing(5)
        title = label("媒体队列")
        title.setObjectName("queueTitle")
        header.addWidget(title)
        count = ElidedLabel("0 个文件")
        count.setObjectName("queueCount")
        count.setProperty("muted", True)
        count.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        count.setFixedWidth(76)
        header.addWidget(count)
        add = Button("添加")
        add.setIcon(line_icon("plus", 15))
        add.setIconSize(QSize(15, 15))
        add.setFixedSize(64, 30)
        add.setToolTip("添加视频、音频或图片（Ctrl+O）")
        add.clicked.connect(self.choose_files)
        header.addWidget(add)
        header.addStretch(1)
        panel.addWidget(frame)
        return header, count, add

    def _add_queue_footer(self, panel):
        footer = QFrame()
        footer.setObjectName("queueFooter")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(12, 6, 12, 12)
        clear = Button("清空列表")
        clear.setIcon(line_icon("trash", 16))
        clear.setIconSize(QSize(16, 16))
        clear.setToolTip("移除队列里的全部文件")
        clear.clicked.connect(self._clear_jobs)
        clear.setMinimumHeight(36)
        actions.addWidget(clear)
        panel.addWidget(footer)
        return clear

    def _build_preview_rail(self):
        self.preview_rail, rail = card()
        shadow(self.preview_rail)
        self.preview_rail.hide()
        _, self.rail_count, self.rail_add = self._add_queue_header(rail)
        # 宽模式表头占用的高度留作间距，让文件行在切换时保持纵向对齐。
        rail.addSpacing(QUEUE_COLUMN_HEIGHT)

        self.preview_files = PreviewFiles(self._queued_media)
        self.preview_files.setObjectName("previewFiles")
        self.preview_files.setAccessibleName("媒体预览文件")
        self.preview_files.setToolTip("单击切换文件，按住左键拖到视频帧或音轨")
        self.preview_files.setIconSize(THUMB_SIZE)
        self.preview_files.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_files.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.preview_files.currentItemChanged.connect(self._preview_file_changed)
        rail.addWidget(self.preview_files, 1)

        self.rail_clear = self._add_queue_footer(rail)

    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Show and widget in (self.list, self.preview_files):
            previous = self.preview_files if widget is self.list else self.list
            offset = previous.verticalScrollBar().value()
            widget.doItemsLayout()
            widget.verticalScrollBar().setValue(offset)
        return super().eventFilter(widget, event)

    def _update_preview_item(self, job):
        item = self.preview_items[job.key]
        item.setIcon(QIcon(queue_thumbnail(job)))
        item.setData(QueueDelegate.META_ROLE, rail_meta(job))
        item.setToolTip(f"{job.path.name}\n{rail_meta(job)}")

    def _preview_file_changed(self, current, _previous):
        if current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if key in self.items:
            self.list.setCurrentItem(self.items[key])
            if self.page_transition.target_preview:
                self._edit_selected()

    def _queued_media(self, key):
        job = self.jobs.get(key)
        if job and job.info and job.path.suffix.lower() != ".pdf":
            return job.path, job.info, job.picture
        return None

    def choose_files(self) -> None:
        if self.scope is not None or self.tools is None:
            return
        media = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self, "添加媒体文件", "",
                                                f"视频、音频和图片 ({media});;所有文件 (*.*)")
        self.add_paths(paths)

    def dragEnterEvent(self, event) -> None:
        if self.tools and self.scope is None and local_files(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = local_files(event.mimeData())
        if self.tools and self.scope is None and paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    def add_paths(self, paths, *, generated=False) -> None:
        if self.tools is None or self.scope is not None:
            return
        seen = {job.path for job in self.jobs.values()}
        added = skipped = 0
        for value in paths:
            try:
                path = Path(value).resolve()
            except OSError:
                skipped += 1
                continue
            if path in seen or not path.is_file():
                skipped += 1
                continue
            seen.add(path)
            key = uuid4().hex
            job = Job(key, path, queued=not generated)
            if generated:
                job.output = path
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(str(path))
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            row = Row(job)
            row.remove_requested.connect(self._remove_job)
            row.format_changed.connect(self._set_format)
            row.set_formats(job, ())
            self.jobs[key], self.items[key], self.rows[key] = job, item, row
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            preview_item = QListWidgetItem(path.name)
            preview_item.setData(Qt.ItemDataRole.UserRole, key)
            preview_item.setToolTip(str(path))
            preview_item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.preview_items[key] = preview_item
            self.preview_files.addItem(preview_item)
            self._update_preview_item(job)
            if generated and path.suffix.lower() == ".pdf":
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                job.info = ImageInfo(0, 0, "PDF", size)
                job.state = "成品"
                row.update_job(job)
                self._update_preview_item(job)
            else:
                self.reader_pool.submit(self._inspect, key, path, self.tools, self.inspections)
            added += 1
        if added and self.list.currentItem() is None:
            self.list.setCurrentRow(0)
        if added or skipped:
            self.status.setText(f"已添加 {added} 个文件" + (f"，跳过 {skipped} 个重复项或非文件" if skipped else "。"))
        self._refresh_controls()

    def _inspect(self, key: str, path: Path, tools: Tools, scope: Processes) -> None:
        try:
            info = probe(path, tools, scope)
        except Cancelled:
            return
        except (MediaError, OSError) as exc:
            self.events.inspected.emit(key, None, str(exc))
            return
        self.events.inspected.emit(key, info, "")
        try:
            image = thumbnail(path, info, tools, scope)
        except (MediaError, OSError):
            image = b""
        if not scope.cancelled.is_set():
            self.events.thumb_ready.emit(key, image)

    @Slot(str, object, str)
    def _inspected(self, key: str, info: MediaInfo | None, error: str) -> None:
        if job := self.jobs.get(key):
            if info is None and media_path_missing(job.path) and self._prune_missing():
                return
            job.info, job.detail = info, error
            job.state = ("就绪" if job.queued else "成品") if info else "读取失败"
            self.rows[key].set_formats(job, formats_for(info))
            self._update_preview_item(job)
            self._update_job(job)
            if self.navigation.currentIndex() == 1 and self._selected() is job and info:
                self._edit_selected()

    @Slot(str, bytes)
    def _show_thumbnail(self, key: str, data: bytes) -> None:
        if job := self.jobs.get(key):
            pixmap = QPixmap()
            if data and pixmap.loadFromData(data):
                job.picture = pixmap
                editor = self.editors.get(key)
                if editor and isinstance(job.info, VideoInfo) and editor.frame_view.original is None:
                    editor.frame_view.set_picture(pixmap)
                self._update_preview_item(job)
            self._update_job(job)

    def _selected(self) -> Job | None:
        item = self.list.currentItem()
        return self.jobs.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _selection_changed(self, current=None, previous=None) -> None:
        for item, amount in ((previous, 0), (current, 1)):
            if item and (row := self.rows.get(item.data(Qt.ItemDataRole.UserRole))):
                row.selection_motion.move_to(amount)
        selected = self._selected()
        with QSignalBlocker(self.preview_files):
            self.preview_files.setCurrentItem(self.preview_items.get(selected.key) if selected else None)
        self._show_selected()
        self._refresh_controls()
        if self.page_transition.target_preview:
            self._show_page(self._editor_for(selected) or self.empty_preview)

    def _job_extension(self, job) -> str:
        kind = job.output_format or ""
        return {"mkv_hevc": "mkv", "gif_image": "gif", "mp4": "mp4", "mkv": "mkv",
                "webm": "webm", "mov": "mov", "avi": "avi", "gif": "gif",
                "mp3": "mp3", "m4a": "m4a", "wav": "wav", "flac": "flac",
                "opus": "opus", "ogg": "ogg", "png": "png", "jpg": "jpg",
                "webp": "webp", "avif": "avif", "tiff": "tiff", "bmp": "bmp",
                "ico": "ico", "pdf": "pdf"}.get(kind, kind or "mp4")

    def _output_stem_edited(self, text: str) -> None:
        if job := self._selected():
            self._set_output_stem(job, text)

    def _set_output_stem(self, job, text: str) -> None:
        job.output_stem = text
        if editor := self.editors.get(job.key):
            if editor.output_stem_edit.text() != text:
                editor.output_stem_edit.setText(text)
            editor._update_output_location()
        if self._selected() is job:
            if self.output_stem_edit.text() != text:
                self.output_stem_edit.setText(text)
            self._refresh_output_name_preview(job)

    def _refresh_output_name_preview(self, job) -> None:
        ext = self._job_extension(job)
        self.output_stem_ext.setText(f".{ext}")
        stem = job.output_stem or job.path.stem
        directory = self.output_directory or job.path.parent
        name = f"{stem}.{ext}"
        target = directory / name
        self.output_stem_edit.setToolTip(str(target))
        self.output_name.set_full_text(name)
        self.output_name.setToolTip(str(target))

    def _show_selected(self) -> None:
        job = self._selected()
        directory = self.output_directory or (job.path.parent if job else None)
        self.destination.set_path(str(directory) if directory else "默认使用各文件的源目录")
        if job and job.output:
            output_name = job.output.name
        elif job and job.output_format:
            output_name = f"{job.output_stem or job.path.stem}.{self._job_extension(job)}"
        else:
            output_name = "选择文件并设置格式后显示输出文件名"
        self.output_stem_edit.blockSignals(True)
        self.output_stem_edit.setText((job.output_stem or job.path.stem) if job else "")
        self.output_stem_edit.blockSignals(False)
        self.output_stem_ext.setText(f".{self._job_extension(job)}" if job and job.output_format else "")
        self.output_name.set_full_text(output_name)
        self.output_name.setToolTip(
            str(job.output) if job and job.output else
            str(directory / output_name) if directory and job and job.output_format else output_name
        )
        audio = job is not None and isinstance(job.info, AudioInfo)
        self.video_name.set_full_text(job.path.name if job else "尚未选择文件")
        if job and job.picture is not None:
            self.preview.setPixmap(fit_pixmap(job.picture, self.preview.size()))
        else:
            self.preview.setText(("暂无波形" if audio else "暂无缩略图") if job else "添加文件后可在此预览")
        if job and job.info:
            details = [f"{metadata(job.info)}  ·  {file_size(job.info.size)}"]
            if audio and job.info.duration > WAVEFORM_SECONDS:
                details.append(f"波形显示前 {WAVEFORM_SECONDS} 秒")
            if job.output and job.output != job.path:
                details.append(f"输出 {job.output.name}")
                if job.output_info:
                    details.append(f"{metadata(job.output_info)}  ·  {file_size(job.output_info.size)}")
                if job.output_note:
                    details.append(job.output_note)
            self.video_info.setText(" · ".join(details))
        elif job:
            self.video_info.setText("读取失败，请查看错误详情。" if job.detail else "正在读取媒体信息…")
        else:
            self.video_info.setText("在这里查看缩略图、波形和文件信息。")
        if job and job.info:
            self.inspector_pill.set_state(job.state, state_tone(job))
            self.inspector_pill.show()
        else:
            self.inspector_pill.hide()
        if not self.page_transition.settings_in_preview:
            self._mount_selected_settings(job)
        self.details_button.setEnabled(bool(job and job.info))
        self.details_button.setToolTip(
            "在新窗口里查看完整路径和全部媒体参数" if job and job.info else "先选择一个文件")

    def _details_extra(self, job):
        if not (job.output and job.output != job.path and job.output_info):
            return []
        rows = [
            ("文件名", job.output.name),
            ("位置", str(job.output.parent)),
            ("文件大小", file_size(job.output_info.size)),
            ("参数", metadata(job.output_info)),
        ]
        if job.output_note:
            rows.append(("说明", job.output_note))
        return [("输出成品", rows)]

    def _show_details(self) -> None:
        job = self._selected()
        if job is None or not job.info:
            return
        if self.details_dialog is None:
            self.details_dialog = DetailsDialog(self)
        self.details_dialog.show_media(job.path, job.info, self._details_extra(job), job.picture)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "inspector"):
            width = self._settings_width()
            self.inspector.setFixedWidth(width)
            for editor in self.editors.values():
                editor.inspector.setFixedWidth(width)
                editor.fit_settings_density(self.height() < 850 or editor.configuration_only)
            self.page_transition.rail_width = self._rail_width()
            self.page_transition.invalidate()
        if hasattr(self, "preview"):
            self._show_selected()

    def _update_job(self, job: Job) -> None:
        self.rows[job.key].update_job(job)
        if self._selected() is job:
            self._show_selected()
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        busy = self.scope is not None
        selected = self._selected()
        has_files = bool(self.jobs)
        self.count.set_full_text(f"{len(self.jobs)} 个文件")
        self.rail_count.set_full_text(self.count.full_text)
        for button in (self.add_button, self.rail_add):
            button.setEnabled(not busy and self.tools is not None)
        self.clear_button.parentWidget().setVisible(has_files)
        self.rail_clear.parentWidget().setVisible(has_files)
        self.rail_clear.setEnabled(has_files and not busy)
        self.queue_tools.setVisible(has_files)
        self.column_header.setVisible(has_files)
        self.list.setVisible(has_files)
        self.drop_area.setVisible(not has_files)
        self.drop_area.set_compact(has_files)
        self.drop_area.setEnabled(not busy and self.tools is not None)
        self._sync_column_header()
        for key, row in self.rows.items():
            row.remove_button.setEnabled(not busy)
            queued = self.jobs[key].queued
            # 状态（就绪/已完成）和“下一次转换想用什么参数”是两件事：
            # 只要这一行还有可选格式，就一直显示并允许修改，只有批量转换进行中才禁用。
            row.format.setVisible(row.format.count() > 0)
            row.format.setEnabled(not busy and row.format.count() > 0)
        formats = {job.output_format for job in self.jobs.values() if job.queued and job.output_format}
        video_formats = sorted({job.output_format for job in self.jobs.values()
                                if job.queued and isinstance(job.info, VideoInfo)
                                and job.output_format in {"mp4", "mkv", "mkv_hevc", "mov"}})
        current_acceleration = self.acceleration.currentData() or "cpu"
        available_by_format = [dict(hardware_acceleration_choices(self.tools.ffmpeg, kind))
                               for kind in video_formats] if self.tools and video_formats else []
        acceleration_choices = [("cpu", "CPU 软件编码")]
        if available_by_format:
            for backend, title in available_by_format[0].items():
                if backend != "cpu" and all(backend in choices for choices in available_by_format[1:]):
                    acceleration_choices.append((backend, title))
        with QSignalBlocker(self.acceleration):
            self.acceleration.clear()
            for backend, title in acceleration_choices:
                self.acceleration.addItem(title, backend)
            selected_index = self.acceleration.findData(current_acceleration)
            self.acceleration.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.acceleration.setVisible(bool(video_formats))
        self.acceleration.setVisible(bool(video_formats))
        self.acceleration_field.setVisible(bool(video_formats))
        self.acceleration.setEnabled(not busy and bool(video_formats))
        has_frame_rate_output = bool(formats & FRAME_RATE_FORMATS)
        self.output_fps.setVisible(has_frame_rate_output)
        self.output_fps.setVisible(has_frame_rate_output)
        self.output_fps_field.setVisible(has_frame_rate_output)
        self.output_fps.setEnabled(not busy and has_frame_rate_output)
        self.output_location.setVisible(selected is not None)
        self.batch_format.setEnabled(not busy and bool(formats))
        self.batch_settings_button.setEnabled(not busy and bool(formats))
        if self.mounted_editor is not None:
            self.mounted_editor.settings_body.setEnabled(not busy and selected is not None and selected.queued)
        self.clear_button.setEnabled(has_files and not busy)
        resizable = bool(formats & RESIZABLE_FORMATS)
        self.resolution.setVisible(not formats or resizable)
        self.resolution.setVisible(not formats or resizable)
        self.resolution_field.setVisible(not formats or resizable)
        self.resolution.setEnabled(not busy and resizable)
        self.gif_field.setVisible("gif" in formats)
        self.gif_preset.setEnabled(not busy)
        reading = any(job.queued and job.state == "读取中" for job in self.jobs.values())
        self.start_button.setEnabled(not busy and not reading and bool(formats))
        self.cancel_button.setEnabled(busy and not self.scope.cancelled.is_set())
        self.target_size.setEnabled(not busy and bool(formats - LOSSLESS_AUDIO_FORMATS))
        self.destination.setEnabled(not busy)
        self.reset_destination.setVisible(self.output_directory is not None)
        self.reset_destination.setEnabled(not busy)
        self.locate_button.setEnabled(bool(selected))
        self.error_button.setVisible(bool(selected and selected.detail))
        editable = bool(selected and selected.info and selected.path.suffix.lower() != ".pdf")
        self.edit_button.setVisible(editable)
        self.edit_button.setEnabled(not busy)
        self.queue_result_button.setVisible(editable and not selected.queued)
        self.queue_result_button.setEnabled(not busy)

    def _queue_selected(self) -> None:
        if self.scope is None and (job := self._selected()) and job.info and not job.queued and job.path.suffix.lower() != ".pdf":
            job.queued = True
            job.state = "就绪"
            self._update_job(job)

    def _sync_footer(self, page) -> None:
        preview = page is not self.conversion_page
        self.page_footers.setCurrentWidget(self.preview_footer if preview else self.footer)
        editor = page if isinstance(page, Editor) else None
        busy = editor is not None and editor.scope is not None
        video = editor is not None and editor.video_page is not None and editor.tabs.currentWidget() is editor.video_page
        self.preview_start.setVisible(editor is not None)
        ready = bool(editor is not None and not busy and (
            (video and editor.frames) or (not video and editor.info is not None)))
        self.preview_start.setEnabled(ready)
        self.preview_cancel.setEnabled(bool(busy and editor.cancel_button.isEnabled()))
        note = editor.notice.text() if editor is not None else "等待选择媒体"
        self.preview_status.setText(note or "就绪")
        maximum = editor.progress.maximum() if busy else 100
        value = max(0, editor.progress.value()) if busy else 0
        self.preview_progress.setRange(0, maximum)
        self.preview_progress.setValue(value)
        self.preview_percent.setText(f"{value}%" if maximum else "…")

    def _refresh_preview_footer(self, editor):
        if self.navigation.currentIndex() == 1 and self.pages.currentWidget() is editor:
            self._sync_footer(editor)

    def _cancel_preview_task(self):
        if isinstance(editor := self.pages.currentWidget(), Editor):
            editor.cancel_operation()

    def _settings_width(self):
        return max(306, min(445, round(self.width() * .255)))

    def _rail_width(self):
        return max(RAIL_MIN_WIDTH, min(320, round(self.width() * .185)))

    def _editor_for(self, job):
        if not self.tools or not job or not job.info or job.path.suffix.lower() == ".pdf":
            return None
        if editor := self.editors.get(job.key):
            return editor
        editor = Editor(job.path, job.info, self.tools, self.output_directory, self.pages,
                        embedded=True, lookup_media=self._queued_media,
                        report_missing=self._report_missing_media, defer_media=True,
                        get_output_stem=lambda: job.output_stem)
        editor.set_output_format(job.output_format)
        if job.output_format:
            editor.apply_output_options(self._job_export(job))
        if isinstance(job.info, VideoInfo) and job.picture is not None:
            editor.frame_view.set_picture(job.picture)
        editor.saved.connect(self._register_editor_output)
        editor.return_requested.connect(self._show_conversion)
        editor.task_changed.connect(lambda: self._refresh_preview_footer(editor))
        editor.settings_changed.connect(lambda: self._store_editor_settings(job, editor))
        editor.directory_changed.connect(lambda directory: self._store_directory(job, directory))
        self._attach_output_stem(editor, job)
        self.editors[job.key] = editor
        self.pages.addWidget(editor)
        return editor

    def _mount_selected_settings(self, job):
        editor = self._editor_for(job)
        if self.mounted_editor is editor:
            return
        self._unmount_settings()
        if editor is not None:
            editor.mount_settings(self.shared_settings)
            editor.fit_settings_density(True)
            if hasattr(editor, "quality"):
                # 以编辑器当前值为准，避免切页时把用户刚调好的质量打回默认
                self.quality.setValue(editor.quality.value())
            editor.settings_body.setEnabled(self.scope is None and job.queued)
            self.mounted_editor = editor

    def _attach_output_stem(self, editor, job) -> None:
        """在编辑器创建时把「输出文件名」挂到共享的保存目录卡片。"""
        layout = editor.directory_group.layout()
        hint = QLabel("输出文件名")
        hint.setObjectName("paramLabel")
        layout.addWidget(hint)
        line = QWidget()
        line_layout = QHBoxLayout(line)
        line_layout.setContentsMargins(0, 0, 0, 0)
        line_layout.setSpacing(6)
        editor.output_stem_edit = QLineEdit()
        editor.output_stem_edit.setPlaceholderText("输出文件名")
        editor.output_stem_edit.setMinimumHeight(30)
        editor.output_stem_edit.setText(job.output_stem or job.path.stem)
        editor.output_stem_edit.textEdited.connect(lambda text: self._set_output_stem(job, text))
        line_layout.addWidget(editor.output_stem_edit, 1)
        editor.output_stem_ext = QLabel("")
        editor.output_stem_ext.setMinimumWidth(46)
        line_layout.addWidget(editor.output_stem_ext)
        layout.addWidget(line)
        editor._update_output_location()

    def _quality_changed(self, value: int) -> None:
        """转换页的滑块与预览页的滑块共用一份质量参数。"""
        editor = self.mounted_editor
        if editor is not None and hasattr(editor, "quality"):
            if editor.quality.value() != int(value):
                editor.quality.setValue(int(value))

    def _unmount_settings(self):
        if editor := self.mounted_editor:
            self.mounted_editor = None
            editor.mount_settings()
            editor.fit_settings_density(self.height() < 850)
            editor.settings_body.setEnabled(True)

    def _change_job_format(self, job, kind):
        job.output_format = kind
        if job.export_settings is not None:
            job.export_settings = replace(job.export_settings, format=kind)
        if editor := self.editors.get(job.key):
            editor.set_output_format(kind)

    def _store_editor_settings(self, job, editor):
        if self.jobs.get(job.key) is not job:
            return
        options = editor.output_options()
        if not options.format or self.rows[job.key].format.findData(options.format) < 0:
            return
        job.export_settings = options
        job.output_format = options.format
        combo = self.rows[job.key].format
        with QSignalBlocker(combo):
            combo.setCurrentIndex(combo.findData(options.format))

    def _store_directory(self, job, directory):
        if self.jobs.get(job.key) is job and job.output_format:
            job.export_settings = replace(self._job_export(job), directory=directory)

    def _edit_selected(self):
        job = self._selected()
        if job and media_path_missing(job.path) and self._prune_missing():
            return
        self._show_page(self._editor_for(job) or self.empty_preview)

    def _navigate(self, index):
        if index == 0:
            self._show_conversion()
        else:
            self._edit_selected()

    def _restore_page_focus(self):
        if not self.isVisible() or self.isMinimized():
            return
        target = self.preview_files if self.navigation.currentIndex() == 1 else self.list
        if target.isVisible() and target.isEnabled():
            self.activateWindow()
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_conversion(self):
        self._show_page(self.conversion_page)

    def _update_preview_undo(self):
        self.preview_undo.setEnabled(
            self.navigation.currentIndex() == 1 and isinstance(self.pages.currentWidget(), Editor))

    def _undo_preview_edit(self):
        page = self.pages.currentWidget()
        if isinstance(page, Editor):
            page.undo()

    def _resize_page_footer(self, position):
        height = CONVERSION_FOOTER_HEIGHT + (PREVIEW_FOOTER_HEIGHT - CONVERSION_FOOTER_HEIGHT) * position
        self.page_footers.setFixedHeight(round(height))

    def _move_page_settings(self, in_preview):
        if in_preview:
            self._unmount_settings()
        else:
            self._show_selected()

    def _settle_page(self):
        if not self.page_transition.target_preview:
            if isinstance(current := self.pages.currentWidget(), Editor):
                current.suspend()
            self.pages.setCurrentWidget(self.conversion_page)
        self._restore_page_focus()

    def _show_page(self, page):
        preview = page is not self.conversion_page
        current = self.pages.currentWidget()
        with QSignalBlocker(self.navigation):
            self.navigation.setCurrentIndex(int(preview))
        if preview and page is not current:
            if isinstance(current, Editor):
                current.suspend()
            if isinstance(page, Editor):
                page.inspector.setFixedWidth(self._settings_width())
                page.fit_settings_density(self.height() < 850 or page.configuration_only)
            self.pages.setCurrentWidget(page)
            if isinstance(page, Editor):
                page.start_media()
                page.resume()
        self._sync_footer(page)
        self._update_preview_undo()
        self.page_transition.move_to(preview)

    @Slot(object)
    def _register_editor_output(self, path: Path) -> None:
        self.add_paths([path], generated=True)

    def _remove_selected(self) -> None:
        if job := self._selected():
            self._remove_job(job.key)

    @Slot(object)
    def _report_missing_media(self, path):
        if any(job.path == path for job in self.jobs.values()):
            self._prune_missing()

    def _prune_missing(self):
        if self.closing or self.scope is not None:
            return False
        missing = [job for job in self.jobs.values() if media_path_missing(job.path)]
        if not missing:
            return False
        selected = self._selected()
        active = self.pages.currentWidget()
        replace_preview = self.navigation.currentIndex() == 1 and any(
            job is selected or active is self.editors.get(job.key) for job in missing)
        names = "\n".join(job.path.name for job in missing[:8])
        if len(missing) > 8:
            names += f"\n另有 {len(missing) - 8} 个文件"
        notice = QMessageBox(QMessageBox.Icon.Warning, "找不到媒体文件",
                             f"在原路径找不到以下文件，已自动从队列移除。\n\n{names}",
                             QMessageBox.StandardButton.Ok, self)
        notice.setTextFormat(Qt.TextFormat.PlainText)
        notice.button(QMessageBox.StandardButton.Ok).setText("确定")
        folders = tuple(dict.fromkeys(job.path.parent for job in missing))
        locate = notice.addButton("打开原位置", QMessageBox.ButtonRole.ActionRole)
        locate.clicked.connect(lambda: self._open_original_folders(folders))
        notice.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        notice.open()
        for job in missing:
            self._remove_job(job.key)
        if replace_preview:
            self._edit_selected()
        self.status.setText(f"已移除 {len(missing)} 个找不到的文件。")
        return True

    def _open_original_folders(self, folders: tuple[Path, ...]) -> None:
        opened = set()
        failed = False
        opened_parent = False
        for original in folders:
            folder = next((path for path in (original, *original.parents) if isdir(path)), None)
            if folder is None:
                failed = True
                continue
            if folder not in opened:
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                    failed = True
                    continue
                opened.add(folder)
            opened_parent |= folder != original
        if failed:
            self.status.setText("有原位置无法打开，请检查磁盘是否可用。")
        elif opened_parent:
            self.status.setText("原文件夹已不存在，已打开最近的可用上级目录。")

    @Slot(str)
    # ---------------------------------------------------------------- 队列右键
    def _install_queue_menus(self) -> None:
        for widget in (self.list, self.preview_files):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget: self._queue_menu(w, pos))

    def _key_at(self, widget, pos):
        item = widget.itemAt(pos)
        if item is None:
            return None
        key = item.data(Qt.ItemDataRole.UserRole)
        return key if key in self.jobs else None

    def _queue_menu(self, widget, pos) -> None:
        key = self._key_at(widget, pos)
        if key is None:
            return
        menu = QMenu(widget)
        menu.setObjectName("queueMenu")
        remove = menu.addAction(line_icon("close", 16), "从队列移除")
        locate = menu.addAction(line_icon("folder", 16), "定位文件")
        rename = menu.addAction(line_icon("file", 16), "重命名")
        chosen = menu.exec(widget.viewport().mapToGlobal(pos))
        if chosen is remove:
            self._remove_from_queue(key)
        elif chosen is locate:
            self._locate_job_file(key)
        elif chosen is rename:
            self._rename_job(key)

    def _remove_from_queue(self, key: str) -> None:
        row = self.list.row(self.items[key])
        self._remove_job(key)
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))
        self._refresh_controls()

    def _locate_job_file(self, key: str) -> None:
        job = self.jobs.get(key)
        if job is None:
            return
        if not job.path.exists():
            self.status.setText("文件不存在或位置已发生变化")
            return
        target = job.output if job.output and job.output.is_file() else job.path
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))

    def _auto_output_for(self, job) -> bool:
        """当前成品名如果就是按源文件名自动生成的，则跟随重命名。"""
        if not job.output:
            return False
        return job.output.stem.startswith(job.path.stem)

    def _rename_job(self, key: str) -> None:
        job = self.jobs.get(key)
        if job is None:
            return
        if job.state == "转换中":
            self.status.setText("文件正在转换，暂时不能重命名")
            return
        dialog = RenameDialog(job.path.stem, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        stem = dialog.value().strip()
        problem = validate_filename(stem, job.path)
        if problem:
            self.status.setText(problem)
            return
        target = job.path.with_name(stem + job.path.suffix)
        follows = self._auto_output_for(job)
        try:
            if target.exists():
                raise FileExistsError
            job.path.rename(target)
        except FileExistsError:
            self.status.setText("该文件夹已存在同名文件，请使用其他名称。")
            return
        except OSError as error:
            self.status.setText(f"重命名失败：{error}")
            return

        job.path = target
        if follows:
            job.output = None
            job.output_info = None
            job.output_note = ""
        item = self.items.get(key)
        if item is not None:
            item.setText(target.name)
        row = self.rows.get(key)
        if row is not None:
            row.update_job(job)
        self._update_preview_item(job)
        self._selection_changed()
        self.status.setText(f"已重命名为 {target.name}")

    def _remove_job(self, key: str) -> None:
        if self.scope is not None or key not in self.jobs:
            return
        editor = self.editors.pop(key, None)
        if editor is not None:
            if self.pages.currentWidget() is editor:
                self.pages.setCurrentWidget(self.empty_preview)
            if editor is self.mounted_editor:
                self._unmount_settings()
            editor.shutdown()
            self.pages.removeWidget(editor)
            editor.deleteLater()
        item = self.items.pop(key)
        self.rows.pop(key)
        self.jobs.pop(key)
        with QSignalBlocker(self.preview_files):
            preview_item = self.preview_items.pop(key)
            self.preview_files.takeItem(self.preview_files.row(preview_item))
        self.list.takeItem(self.list.row(item))
        self._selection_changed()

    def _clear_jobs(self):
        if self.scope is not None or not self.jobs:
            return
        self.inspections.cancel()
        self.inspections = Processes()
        self._unmount_settings()
        if self.page_transition.target_preview:
            self.pages.setCurrentWidget(self.empty_preview)
        for editor in self.editors.values():
            editor.shutdown()
            self.pages.removeWidget(editor)
            editor.deleteLater()
        self.editors.clear()
        self.preview_items.clear()
        self.items.clear()
        self.rows.clear()
        self.jobs.clear()
        with QSignalBlocker(self.preview_files), QSignalBlocker(self.list):
            self.preview_files.clear()
            self.list.clear()
        self._selection_changed()
        self.progress.setValue(0)
        self.status.setText("已清空列表，磁盘上的文件已保留。")

    def _choose_directory(self) -> None:
        job = self._selected()
        current = self.output_directory or (job.path.parent if job else Path.home())
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹", str(current))
        if directory:
            self.output_directory = Path(directory)
            self._show_selected()
            self._refresh_controls()

    def _reset_directory(self) -> None:
        self.output_directory = None
        self._show_selected()
        self._refresh_controls()

    def _show_error(self) -> None:
        if job := self._selected():
            box = QMessageBox(QMessageBox.Icon.Warning, "处理失败",
                              "请检查文件是否损坏，或输出目录是否可写。", parent=self)
            box.setDetailedText(job.detail)
            box.exec()

    def _open_output(self) -> None:
        self._show_output(locate=False)

    def _locate_output(self) -> None:
        self._show_output(locate=True)

    def _show_output(self, *, locate: bool) -> None:
        job = self._selected()
        if job is None:
            self.status.setText("先在队列里选一个文件。")
            return
        path = job.output if job.output and job.output.is_file() else job.path
        if path is None or not path.exists():
            self.status.setText("文件已移走或删除。")
            return
        if locate and sys.platform == "win32":
            explorer = QProcess()
            explorer.setProgram("explorer.exe")
            explorer.setNativeArguments(f'/select,"{path.absolute()}"')
            started = explorer.startDetached()
            if isinstance(started, tuple):
                started = started[0]
            if started:
                return
        target = path.parent if locate else path
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self.status.setText("无法打开成品，请检查系统默认应用。")

    def _batch_options(self, job):
        kind = job.output_format
        supported = {key for key, _ in hardware_acceleration_choices(self.tools.ffmpeg, kind)}
        acceleration = self.acceleration.currentData() or "cpu"
        return Export(kind, self.output_directory, small_gif=bool(self.gif_preset.currentData()),
                      max_edge=max(0, self.resolution.currentData() or 0) if kind in RESIZABLE_FORMATS else 0,
                      target_bytes=self.target_size.value() * 1000,
                      video_acceleration=acceleration if acceleration in supported else "cpu",
                      output_fps=int(self.output_fps.currentData() or 0) if kind in FRAME_RATE_FORMATS else 0,
                      quality=self.quality.value() if isinstance(job.info, VideoInfo) else 85 if isinstance(job.info, ImageInfo) else 0,
                      audio_bitrate=192, png_compression=7 if isinstance(job.info, ImageInfo) else 6)

    def _job_export(self, job):
        options = job.export_settings or self._batch_options(job)
        return replace(options, format=job.output_format, output_stem=job.output_stem)

    def _show_batch_settings(self):
        before = (self.resolution.currentData(), self.output_fps.currentData(), self.acceleration.currentData(),
                  self.gif_preset.currentData(), self.target_size.value(), self.quality.value(), self.output_directory)
        if self.batch_settings_dialog.exec() == QDialog.DialogCode.Rejected:
            for combo, value in zip((self.resolution, self.output_fps, self.acceleration, self.gif_preset), before[:4]):
                combo.setCurrentIndex(combo.findData(value))
            self.target_size.setValue(before[4])
            self.quality.setValue(before[5])
            self.output_directory = before[6]
            self._show_selected()

    def _apply_batch_settings(self):
        for job in self.jobs.values():
            if not (job.queued and job.output_format):
                continue
            batch = self._batch_options(job)
            current = self._job_export(job)
            options = replace(current, directory=batch.directory, max_edge=batch.max_edge,
                              target_bytes=batch.target_bytes, video_acceleration=batch.video_acceleration,
                              output_fps=batch.output_fps, small_gif=batch.small_gif,
                              quality=batch.quality if isinstance(job.info, VideoInfo) else current.quality)
            job.export_settings = options
            if editor := self.editors.get(job.key):
                editor.apply_output_options(options)
        self.batch_settings_dialog.accept()
        self.status.setText("已将批量参数应用到队列。")

    def start_export(self) -> None:
        page = self.pages.currentWidget()
        if self.navigation.currentIndex() == 1:
            if isinstance(page, Editor):
                if page.tabs.currentWidget() is page.image_page:
                    page.save_image()
                else:
                    page.save_edit()
            return
        if self.scope is not None or self.tools is None or any(
            job.queued and job.state == "读取中" for job in self.jobs.values()
        ):
            return
        self._prune_missing()
        tasks = [(job.key, job.path, job.info, self._job_export(job))
                 for job in self.jobs.values() if job.queued and job.info and job.output_format]
        if not tasks:
            return
        self.scope = Processes()
        self.pending_outputs = []
        self.total, self.finished, self.failed = len(tasks), 0, 0
        for key, _, _, _ in tasks:
            job = self.jobs[key]
            job.state, job.percent, job.detail = "等待转换", 0, ""
            job.output, job.output_info, job.output_note = None, None, ""
            self.rows[key].update_job(job)
        self._show_selected()
        self.progress.setValue(0)
        self.status.setText(f"正在转换 {self.total} 个文件…")
        self._refresh_controls()
        self.export_pool.submit(self._run_batch, tasks, self.tools, self.scope)

    def _run_batch(self, tasks, tools: Tools, scope: Processes) -> None:
        try:
            for key, path, info, options in tasks:
                if scope.cancelled.is_set():
                    break
                self.events.phase.emit(key, "转换中")
                try:
                    output = convert(path, info, options, tools, scope,
                                     lambda pct: self.events.progress.emit(key, pct),
                                     notice=lambda message: self.events.phase.emit(key, message))
                except Cancelled:
                    break
                except (MediaError, OSError) as exc:
                    self.events.exported.emit(key, None, None, "", str(exc))
                else:
                    output_info = None
                    note = ""
                    try:
                        size = output.stat().st_size
                        if options.size_limit:
                            target = file_size(options.size_limit)
                            if size <= options.size_limit:
                                note = f"已达到 {target} 目标"
                            else:
                                note = f"超过 {target} 目标，已保存可用的最小版本"
                        if options.format == "pdf":
                            output_info = ImageInfo(0, 0, "PDF", size)
                        else:
                            output_info = probe(output, tools, scope)
                    except (MediaError, OSError):
                        pass  # 成品已保存，读取详情失败不影响结果
                    self.events.exported.emit(key, output, output_info, note, "")
        finally:
            self.events.completed.emit(scope.cancelled.is_set())

    @Slot(str, str)
    def _phase(self, key: str, state: str) -> None:
        if job := self.jobs.get(key):
            job.state = state
            self.rows[key].update_job(job)

    @Slot(str, int)
    def _progress(self, key: str, percent: int) -> None:
        if job := self.jobs.get(key):
            job.percent = max(job.percent, percent)
            self.rows[key].update_job(job)
            self.progress.setValue(round((self.finished * 100 + job.percent) / self.total))

    @Slot(str, object, object, str, str)
    def _exported(self, key: str, output: Path | None, output_info: MediaInfo | None,
                  note: str, error: str) -> None:
        job = self.jobs[key]
        job.detail = error
        job.state = "已完成" if output else "转换失败"
        job.output, job.output_info, job.output_note = output, output_info, note
        if output:
            # 保留上一轮成品信息，但让这一行重新参与下一轮转换，方便换格式再导一次。
            job.queued = True
        if output:
            self.pending_outputs.append(output)
        self.finished += 1
        self.failed += int(not output)
        self.progress.setValue(round(self.finished * 100 / self.total))
        self._update_job(job)

    @Slot(bool)
    def _completed(self, cancelled: bool) -> None:
        self.scope = None
        for job in self.jobs.values():
            if job.state in {"等待转换", "转换中"}:
                job.state = "已取消" if cancelled else "未完成"
                self.rows[job.key].update_job(job)
        complete = self.finished - self.failed
        if self.pending_outputs:
            self.add_paths(self.pending_outputs, generated=True)
        self.status.setText(f"{'已取消 · ' if cancelled else ''}完成 {complete} 个，失败 {self.failed} 个。")
        self._refresh_controls()
        self._prune_missing()

    def cancel_export(self) -> None:
        if self.scope is not None:
            self.scope.cancel()
            self.status.setText("正在停止，已完成的文件会保留。")
            self._install_queue_menus()
        self._refresh_controls()

    def closeEvent(self, event) -> None:
        self.closing = True
        self.page_transition.animation.stop()
        for editor in self.editors.values():
            editor.shutdown()
        self.editors.clear()
        for animation in self.findChildren(Motion):
            animation.stop()
        self.inspections.cancel()
        if self.scope is not None:
            self.scope.cancel()
        self.reader_pool.shutdown(wait=True, cancel_futures=True)
        self.export_pool.shutdown(wait=True, cancel_futures=True)
        super().closeEvent(event)
