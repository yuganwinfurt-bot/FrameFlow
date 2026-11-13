"""Wide and compact media queue views and their drag interactions."""

from __future__ import annotations

import json

from PySide6.QtCore import QItemSelectionModel, QMimeData, QPoint, QRectF, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QDrag, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QPushButton, QScrollArea, QSizePolicy,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from .icons import pixmap as line_pixmap
from .media import AudioInfo, ImageInfo, VideoInfo
from .media_text import clock, file_size, summary
from .motion import (QUEUED_MEDIA, QUEUED_MEDIA_BATCH, Button, Combo, Motion,
                     hide_vertical_scroll_bar, queued_media_key)
from .queue_drag import QueueDrag
from .queue_state import Job
from .theme import C
from .widgets import ElidedLabel, fit_pixmap, label

# 队列行的列宽。表头和行共用这几个数，改一处两边一起对齐。
THUMB_SIZE = QSize(56, 36)
W_DURATION, W_SIZE, W_FORMAT, W_STATE, W_REMOVE = 66, 72, 132, 78, 28
ROW_HEIGHT = 58
ROW_SPACING = 8
ROW_MARGINS = (10, 6, 8, 6)
QUEUE_COLUMN_HEIGHT = 26


class QueueColumns(QFrame):
    """Both queue views paint the same header at every transition width."""

    columns = (("时长", W_DURATION), ("大小", W_SIZE), ("状态", W_STATE), ("输出格式", W_FORMAT))

    def __init__(self):
        super().__init__()
        self.setObjectName("columnHeader")
        self.setAccessibleName("媒体队列表头")
        self.setFixedHeight(QUEUE_COLUMN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.detail = 1.0
        self.content_span = None

    def set_detail(self, value):
        if value != self.detail:
            self.detail = value
            self.update()

    def set_content_span(self, left, width):
        if self.content_span != (left, width):
            self.content_span = left, width
            self.update()

    def column_rects(self):
        left, width = self.content_span or (0, self.width())
        x = left + width - ROW_MARGINS[2] - W_REMOVE
        rects = []
        for title, width in reversed(self.columns):
            x -= width + ROW_SPACING
            rects.append((title, QRectF(x, 0, width, self.height())))
        return reversed(rects)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        font = painter.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(C.text3)
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        left, width = self.content_span or (0, self.width())
        name_x = left + ROW_MARGINS[0] + THUMB_SIZE.width() + ROW_SPACING
        right = left + width
        painter.drawText(QRectF(name_x, 0, max(0, right - name_x), self.height()), align, "名称")
        if self.detail:
            painter.setOpacity(self.detail)
            painter.setClipRect(QRectF(name_x + 44, 0, max(0, right - name_x - 44), self.height()))
            for title, rect in self.column_rects():
                painter.drawText(rect, align, title)
        painter.end()


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


class MediaQueue(QListWidget):
    def __init__(self, lookup_media, can_reorder, reorder, *, narrow=False, timeline_drag=None, column_header=None):
        super().__init__()
        self.lookup_media = lookup_media
        self.timeline_drag = timeline_drag
        self.can_reorder, self.reorder = can_reorder, reorder
        self.pressed_media = None
        self.batch_drag = False
        self.drag_key = None
        self.drag_slot = None
        self.column_header = column_header
        if column_header is not None:
            self.row_geometry_timer = QTimer(self)
            self.row_geometry_timer.setSingleShot(True)
            self.row_geometry_timer.timeout.connect(self._sync_row_geometry)
        self.setMouseTracking(True)
        if narrow:
            self.setItemDelegate(QueueDelegate(self))
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.spring_drag = QueueDrag(self, ROW_HEIGHT)

    def set_batch_drag(self, enabled):
        if self.batch_drag != enabled:
            self.batch_drag = enabled
            self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection if enabled
                                  else QListWidget.SelectionMode.SingleSelection)
            self.clearSelection()
            if item := self.currentItem():
                item.setSelected(True)

    def mousePressEvent(self, event):
        if self.spring_drag.landing:
            self.spring_drag.reset()
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and item is not None:
            # Opening the item on press would change the target editor before dragging.
            self.pressed_media = item, event.position().toPoint()
            self.setFocus()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        motion = self.spring_drag
        if motion.active and not motion.native and event.buttons() & Qt.MouseButton.LeftButton:
            if self.viewport().rect().contains(event.position().toPoint()):
                motion.track(event.position())
            elif self.lookup_media(self.drag_key):
                self._drag_out(self.drag_key)
            else:
                motion.track(None)
            event.accept()
            return
        if self.pressed_media and event.buttons() & Qt.MouseButton.LeftButton:
            item, start = self.pressed_media
            if (event.position().toPoint() - start).manhattanLength() >= QApplication.startDragDistance():
                key = item.data(Qt.ItemDataRole.UserRole)
                if self.can_reorder() or self.lookup_media(key):
                    self.pressed_media = None
                    self.drag_key, self.drag_slot = key, None
                    if self.can_reorder():
                        grip = start - self.visualItemRect(item).topLeft()
                        motion.start(item, grip, event.position())
                    if not motion.active or not self.viewport().rect().contains(event.position().toPoint()):
                        self._drag_out(key, item)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def _drag_out(self, key, item=None):
        motion = self.spring_drag
        if motion.active:
            item = motion.items[motion.source]
        mime = QMimeData()
        mime.setData(QUEUED_MEDIA, key.encode("ascii"))
        if self.batch_drag and item is not None and item.isSelected():
            keys = [selected.data(Qt.ItemDataRole.UserRole) for selected in self.selectedItems()]
            mime.setData(QUEUED_MEDIA_BATCH, json.dumps(keys).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        if motion.active:
            motion.native = True
            motion.track(None)
        media = self.lookup_media(key) if self.timeline_drag and self.timeline_drag() else None
        if media and isinstance(media[1], (ImageInfo, VideoInfo)):
            drag.setPixmap(self._timeline_drag_pixmap(*media))
            drag.setHotSpot(QPoint(-14, -16))
        elif motion.active:
            drag.setPixmap(motion.floating)
            drag.setHotSpot(motion.hotspot)
        elif item is not None:
            drag.setPixmap(item.icon().pixmap(self.iconSize()))
        drag.exec(Qt.DropAction.CopyAction)
        self._finish_drag(self.viewport().mapFromGlobal(QCursor.pos()), self.drag_slot)
        drag.deleteLater()

    def _timeline_drag_pixmap(self, path, info, picture):
        size = QSize(96, 32)
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap(size * ratio)
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(.85)
        painter.setPen(C.border)
        painter.setBrush(C.surface)
        painter.drawRoundedRect(QRectF(1, 1, size.width() - 2, size.height() - 2), 6, 6)
        if picture is None or picture.isNull():
            picture = line_pixmap("image" if isinstance(info, ImageInfo) else "video", 18, C.accent.name())
        image_rect = QRectF(5, 4, 24, 24)
        fitted_size = picture.size().scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio)
        fitted = QRectF(0, 0, fitted_size.width(), fitted_size.height())
        fitted.moveCenter(image_rect.center())
        painter.drawPixmap(fitted, picture, QRectF(picture.rect()))
        font = painter.font()
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(C.text)
        label = QRectF(34, 0, 56, size.height())
        name = painter.fontMetrics().elidedText(path.name, Qt.TextElideMode.ElideMiddle, int(label.width()))
        painter.drawText(label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
        painter.end()
        return pixmap

    def _finish_drag(self, point, slot=None):
        motion = self.spring_drag
        if motion.active:
            if slot is None or not self.reorder(self.drag_key, slot):
                slot = motion.source
            motion.land(slot, point)
        self.drag_key, self.drag_slot = None, None

    def mouseReleaseEvent(self, event):
        motion = self.spring_drag
        if event.button() == Qt.MouseButton.LeftButton and motion.active and not motion.native:
            point = event.position().toPoint()
            valid = self.viewport().rect().contains(point)
            if valid:
                motion.track(point)
            self._finish_drag(point, motion.slot if valid else None)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.pressed_media:
            item, _ = self.pressed_media
            self.pressed_media = None
            if self.itemAt(event.position().toPoint()) is item:
                if self.batch_drag and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    item.setSelected(not item.isSelected())
                    self.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
                else:
                    self.setCurrentItem(item, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                self.itemClicked.emit(item)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.pressed_media = None
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.spring_drag.active and not self.spring_drag.native:
            self._finish_drag(self.viewport().mapFromGlobal(QCursor.pos()))
            event.accept()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        if (event.source() is self and self.spring_drag.active and self.can_reorder()
                and self.drag_key == queued_media_key(event.mimeData())):
            self.spring_drag.track(event.position())
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.spring_drag.track(None)
        event.accept()

    def dropEvent(self, event):
        self.dragMoveEvent(event)
        if event.isAccepted():
            self.drag_slot = self.spring_drag.slot

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.column_header is None:
            self.spring_drag.reflow()
        else:
            # A window resize can change the viewport twice as the inspector takes
            # its new width. Refit the index widgets after both layout passes.
            self.row_geometry_timer.start(0)

    def _sync_row_geometry(self):
        self.updateEditorGeometries()
        viewport = self.viewport()
        header = self.column_header
        left = viewport.mapTo(header.parentWidget(), QPoint()).x() - header.x()
        header.set_content_span(left, viewport.width())
        self.spring_drag.reflow()

    def hideEvent(self, event):
        self.spring_drag.reset()
        super().hideEvent(event)


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
        hide_vertical_scroll_bar(scroll)
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
