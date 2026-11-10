"""按可视范围加载的逐帧缩略图条。"""

from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from math import ceil, floor

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QApplication

from .editing import THUMB_HEIGHT, THUMB_WIDTH, frame_tiles, length, slice_track
from .media import Cancelled, MediaError, Processes
from .motion import queued_media_key
from .spring import FrameCompaction, SpringClock, edge_speed


EDIT_KEYS = {Qt.Key.Key_C: "copy", Qt.Key.Key_X: "cut",
             Qt.Key.Key_V: "paste", Qt.Key.Key_Z: "undo"}


class Filmstrip(QAbstractScrollArea):
    frame_selected = Signal(int)
    frame_clicked = Signal(int, object)
    frame_dragged = Signal(int, int, object)
    context_requested = Signal(int, object)
    edit_command = Signal(str)
    zoom_changed = Signal(int)
    media_dropped = Signal(str, int)
    frames_moved = Signal(object, int)
    _tiles_ready = Signal(object, object, bool)
    tile_width = 72

    def __init__(self, tools, drop_media_preview):
        super().__init__()
        self.tools, self.drop_media_preview = tools, drop_media_preview
        self.sources = {}
        self.track, self.total, self.current = [], 0, 1
        self.frames = None
        self.zoom = 4
        self.selection = ()
        self.cache = OrderedDict()
        self.unavailable = set()
        self.inflight = ()
        self.scope = None
        self.closing = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frameflow-tiles")
        self.timer = QTimer(self, singleShot=True, interval=75)
        self.timer.timeout.connect(self._load_visible)
        self.drag_anchor = None
        self.drag_modifiers = Qt.KeyboardModifier.NoModifier
        self.drag_direction = 0
        self.drag_scroll = QTimer(self, interval=70)
        self.drag_scroll.timeout.connect(self._scroll_drag_edge)
        self.drop_position = self.drop_x = None
        self.drop_preview = None
        self.drop_key = None
        self.drop_frames = 0
        self.drop_grip = 0.0
        self.drop_fraction = 0.0
        self.returning = False
        self.compaction = FrameCompaction(())
        self.move_press = None
        self.move_point = None
        self.springs = SpringClock(self, self._animate_gap)
        self.setAcceptDrops(True)
        self._tiles_ready.connect(self._apply_tiles)
        self.setMinimumHeight(58)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        self.setStyleSheet("QAbstractScrollArea { border: none; background: #eaf1f9; }")
        self.setToolTip("拖入视频或图片 · 左键拖选 · 右键拖动移动帧 · 右键单击打开菜单")

    def sizeHint(self):
        return QSize(0, 78)

    @property
    def tile_height(self):
        return self.viewport().height()

    def _capacity(self):
        return max(1, self.viewport().width() // self.tile_width)

    def _stride(self):
        widest = max(1, ceil(self.total / self._capacity()))
        return max(1, ceil(widest ** ((4 - self.zoom) / 4)))

    def _span(self):
        return self._capacity() * self._stride()

    def set_zoom(self, value):
        value = max(0, min(4, value))
        if self.zoom == value:
            return
        self.zoom = value
        self._set_scroll_range()
        if self.drop_x is None:
            self.set_current(self.current)
        self._retarget_gap()
        self.zoom_changed.emit(value)

    def _tile_rect(self, column, slots=None):
        width = self.viewport().width()
        slots = slots or self._capacity()
        left, right = column * width // slots, (column + 1) * width // slots
        return QRect(left, 0, right - left, self.tile_height)

    def _column_at(self, x):
        slots = self._capacity()
        width = max(1, self.viewport().width())
        column = ((int(x) + 1) * slots - 1) // width
        return min(slots - 1, max(0, column))

    def set_track(self, track, frames, sources):
        self._clear_drop(animated=False)
        self.track = track
        self.frames = frames
        self.sources = sources.copy()
        self.total = int(length(track))
        self._set_scroll_range()
        self.set_current(min(self.current, max(1, self.total)))

    def _set_scroll_range(self):
        bar = self.horizontalScrollBar()
        bar.setPageStep(self._span())
        extra = min(self.drop_frames, self._span()) if not self.compaction.count else 0
        bar.setRange(0, max(0, self.total + extra - self._span()))

    def set_current(self, frame, *, playing=False):
        self.current = frame
        bar = self.horizontalScrollBar()
        position, first = frame - 1, bar.value()
        if position < first:
            bar.setValue(position)
        elif position >= first + self._span():
            bar.setValue(position if playing else position - self._span() + 1)
        self.viewport().update()
        if not self.closing and not playing:
            self.timer.start()

    def set_selection(self, selection):
        if self.selection != selection:
            self.selection = selection
            self.viewport().update()

    def _visible(self):
        if self.compaction.count:
            return [ref for frame in self._drag_frames() if (ref := self._frame_ref(frame))]
        first = self.horizontalScrollBar().value()
        count = self._capacity()
        if self._stride() > 1:
            return [(clip.path, int(clip.start)) for frame in self._visible_frames()
                    for clip in slice_track(self.track, frame - 1, frame)]
        return [(clip.path, index) for clip in slice_track(self.track, first, first + count)
                for index in range(int(clip.start), int(clip.end))]

    def _visible_frames(self):
        first = self.horizontalScrollBar().value() + 1
        return range(first, min(self.total + 1, first + self._span()), self._stride())

    @Slot(int)
    def _scrolled(self, _value):
        self._retarget_gap()
        self.viewport().update()
        if not self.closing:
            self.timer.start()

    def _load_visible(self):
        if self.closing or not self.isVisible():
            return
        indices = set(self._visible()) - self.cache.keys() - self.unavailable
        if self.scope is not None:
            if indices and not indices.intersection(self.inflight):
                self.scope.cancel()
            return
        if indices:
            self.inflight = tuple(sorted(indices))
            self.scope = Processes()
            self.pool.submit(self._read_tiles, self.inflight, self.sources, self.scope)

    def _read_tiles(self, indices, sources, scope):
        groups = defaultdict(list)
        for path, index in indices:
            groups[path].append(index)
        pictures = {}
        try:
            for path, numbers in groups.items():
                source = sources[path]
                try:
                    tiles = frame_tiles(path, source.info, numbers, self.tools, scope)
                except Cancelled:
                    raise
                except (MediaError, OSError):
                    continue
                pictures.update({(path, index): data for index, data in tiles.items()})
        except Cancelled:
            self._tiles_ready.emit(indices, {}, True)
        else:
            self._tiles_ready.emit(indices, pictures, False)

    @Slot(object, object, bool)
    def _apply_tiles(self, indices, pictures, cancelled):
        self.scope = None
        self.inflight = ()
        if self.closing:
            return
        cache = self.cache
        for index, data in pictures.items():
            image = QImage(data, THUMB_WIDTH, THUMB_HEIGHT, THUMB_WIDTH * 3, QImage.Format.Format_RGB888)
            cache[index] = QPixmap.fromImage(image.copy())
        if not cancelled:
            self.unavailable.update(set(indices) - pictures.keys())
        visible = set(self._visible())
        for index in visible.intersection(cache):
            cache.move_to_end(index)
        while len(cache) > 160:
            cache.popitem(last=False)
        self.viewport().update()
        self._load_visible()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._set_scroll_range()
        if self.drop_x is None:
            self.set_current(self.current)
        self._retarget_gap()

    def showEvent(self, event):
        super().showEvent(event)
        self._scrolled(0)

    def hideEvent(self, event):
        self.timer.stop()
        self._stop_drag()
        self.move_press = None
        self._clear_drop(animated=False)
        if self.scope is not None:
            self.scope.cancel()
        super().hideEvent(event)

    def _frame_width(self):
        return max(1, self.viewport().width()) / self._span()

    def _boundary_at(self, x):
        first = self.horizontalScrollBar().value()
        # At overview zoom, each displayed tile is a group of frames.
        step = self._stride()
        column = floor((x / self._frame_width() - self.drop_grip) / step + .5)
        position = min(self.total - self.compaction.count, max(0, first + column * step))
        if self.drop_position is not None and abs(position - self.drop_position) == step:
            between = ((position + self.drop_position) / 2 - first + self.drop_grip) * self._frame_width()
            if abs(x - between) < 3:
                return self.drop_position
        return position

    def _drop_rect(self):
        width = self._frame_width()
        x = (self.drop_position - self.horizontalScrollBar().value()) * width
        # At the end of a full viewport, retain a visible target while auto-scroll opens room.
        x = min(self.viewport().width() - min(width * self._stride(), self.viewport().width()), max(0, x))
        size = max(1, min(self.viewport().width() - x, self.drop_frames * width))
        return QRect(round(x), 0, round(size), self.tile_height)

    def _drag_frames(self):
        frames = set(self._visible_frames())
        if self.compaction.count:
            first = self.horizontalScrollBar().value()
            step = self._stride()
            for position in range(first, min(first + self._span(), self.total - self.compaction.count), step):
                original = self.compaction.expand(position)
                frame = first + ((original - first) // step) * step + 1
                if 1 <= frame <= self.total:
                    frames.add(frame)
        return sorted(frames)

    def _frame_ref(self, frame):
        clips = slice_track(self.track, frame - 1, frame)
        return (clips[0].path, int(clips[0].start)) if clips else None

    def _retarget_gap(self):
        if self.drop_x is None and not self.returning:
            return
        scale = self._frame_width()
        if self.drop_x is not None:
            self.drop_position = self._boundary_at(self.drop_x)
            anchor = self.drop_position * scale
            self.springs.aim("anchor", anchor, anchor)
        gap = self.drop_frames * scale if self.drop_x is not None else 0
        self.springs.aim("width", 0, gap)
        visible = self._drag_frames()
        keep = set(visible)
        for frame in list(self.springs.states):
            if isinstance(frame, int) and frame not in keep:
                self.springs.states.pop(frame)
        for frame in visible:
            start = frame - 1
            compact = self.compaction.collapse(start)
            target = (compact - start) * scale if self.drop_x is not None else 0
            if gap and compact >= self.drop_position:
                target += gap
            self.springs.aim(frame, 0, target)
        self.springs.wake()
        self.viewport().update()

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        key = queued_media_key(event.mimeData())
        preview = self.drop_preview if key == self.drop_key else self.drop_media_preview(key)
        if preview:
            if self.drop_key != key:
                if self.compaction.count:
                    self._clear_drop(animated=False)
                self.returning = False
                self.drop_key = key
                self.drop_preview = preview
                self.drop_frames = preview[2]
                self._set_scroll_range()
            self.drop_x = event.position().x()
            self._retarget_gap()
            event.acceptProposedAction()
        else:
            self._clear_drop()
            event.ignore()

    def _animate_gap(self, dt, moving):
        bar = self.horizontalScrollBar()
        speed = edge_speed(self.drop_x, self.viewport().width(), band=20) if self.drop_x is not None else 0
        scrolling = (speed < 0 and bar.value() > bar.minimum()) or (speed > 0 and bar.value() < bar.maximum())
        if scrolling:
            self.drop_fraction += speed * dt / self._frame_width()
            frames = int(self.drop_fraction)
            if frames:
                self.drop_fraction -= frames
                bar.setValue(bar.value() + frames)
        else:
            self.drop_fraction = 0
        if self.returning and not moving:
            self._clear_drop(animated=False)
        self.viewport().update()
        return scrolling

    def _clear_drop(self, *, animated=True):
        active = self.drop_position is not None
        self.drop_position = self.drop_x = None
        self.drop_preview = self.drop_key = None
        self.move_point = None
        self.drop_fraction = self.drop_grip = 0
        if animated and active:
            self.returning = True
            self._retarget_gap()
        elif not animated:
            self.springs.reset()
            self.returning = False
            self.drop_frames = 0
            self.compaction = FrameCompaction(())
            self._set_scroll_range()
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self._clear_drop()
        event.accept()

    def dropEvent(self, event):
        key = queued_media_key(event.mimeData())
        x = event.position().x()
        position = self.drop_position if key == self.drop_key and self.drop_position is not None else self._boundary_at(x)
        self._clear_drop()
        if self.drop_media_preview(key):
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self.media_dropped.emit(key, position)
            event.acceptProposedAction()
        else:
            event.ignore()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            if frame := self._tile_at(event.position()):
                self.setFocus()
                if event.button() == Qt.MouseButton.RightButton:
                    self.move_press = frame, event.position().toPoint()
                else:
                    self.drag_anchor = frame
                    self.drag_modifiers = event.modifiers()
                    self.frame_clicked.emit(frame, event.modifiers())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.move_press is not None and event.buttons() & Qt.MouseButton.RightButton:
            frame, start = self.move_press
            if self.move_point is None:
                if (event.position().toPoint() - start).manhattanLength() < QApplication.startDragDistance():
                    return
                selected = self.selection if any(a <= frame <= b for a, b in self.selection) else ((frame, frame),)
                compaction = FrameCompaction(tuple((a - 1, b) for a, b in selected))
                if compaction.count == self.total:
                    self.move_press = None
                    return
                self._clear_drop(animated=False)
                self.compaction = compaction
                self.drop_frames = compaction.count
                self.drop_grip = (start.x() / self._frame_width() + self.horizontalScrollBar().value()
                                  - compaction.collapse(frame - 1))
                self.drop_preview = f"移动 {self.drop_frames} 帧", self.cache.get(self._frame_ref(frame)), self.drop_frames
            self.move_point = event.position()
            self.drop_x = event.position().x()
            self._retarget_gap()
            event.accept()
            return
        if self.drag_anchor is not None and event.buttons() & Qt.MouseButton.LeftButton:
            x = event.position().x()
            self.drag_direction = -1 if x < 12 else 1 if x >= self.viewport().width() - 12 else 0
            if self.drag_direction:
                if not self.drag_scroll.isActive():
                    self.drag_scroll.start()
            else:
                self.drag_scroll.stop()
            frame = self._frame_at(x)
            if frame:
                self.frame_dragged.emit(self.drag_anchor, frame, self.drag_modifiers)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._stop_drag()
        elif event.button() == Qt.MouseButton.RightButton:
            if self.move_press is not None:
                frame, _ = self.move_press
                self.move_press = None
                if self.move_point is not None:
                    position, ranges = self._boundary_at(event.position().x()), self.compaction.ranges
                    self._clear_drop()
                    if self.viewport().rect().contains(event.position().toPoint()):
                        self.frames_moved.emit(ranges, position)
                elif self._tile_at(event.position()):
                    self.context_requested.emit(frame, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _tile_at(self, position):
        x, y = position.x(), position.y()
        column = self._column_at(x)
        frame = self.horizontalScrollBar().value() + column * self._stride() + 1
        if 0 <= x < self.viewport().width() and 0 <= y < self.tile_height and frame <= self.total:
            return frame
        return None

    def _stop_drag(self):
        self.drag_anchor = None
        self.drag_direction = 0
        self.drag_scroll.stop()

    def _frame_at(self, x):
        if not self.total:
            return 0
        column = self._column_at(x)
        cell = self._tile_rect(column)
        fraction = min(.999, max(0, (x - cell.left()) / max(1, cell.width())))
        step = self._stride()
        return min(self.total, self.horizontalScrollBar().value() + column * step + int(fraction * step) + 1)

    def _scroll_drag_edge(self):
        if self.drag_anchor is None or not self.drag_direction:
            self.drag_scroll.stop()
            return
        bar = self.horizontalScrollBar()
        previous = bar.value()
        bar.setValue(previous + self.drag_direction * self._stride())
        if bar.value() == previous:
            self.drag_scroll.stop()
            return
        frame = (bar.value() + 1 if self.drag_direction < 0
                 else min(self.total, bar.value() + self._span()))
        self.frame_dragged.emit(self.drag_anchor, frame, self.drag_modifiers)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and self.move_press is not None:
            self.move_press = None
            self._clear_drop()
            event.accept()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if command := EDIT_KEYS.get(event.key()):
                self.edit_command.emit(command)
                event.accept()
                return
        offsets = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}
        if event.key() in offsets and self.total:
            self.frame_selected.emit(max(1, min(self.total, self.current + offsets[event.key()])))
            event.accept()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().x() or event.angleDelta().y()
        if delta:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.set_zoom(self.zoom + (1 if delta > 0 else -1))
            else:
                bar = self.horizontalScrollBar()
                bar.setValue(bar.value() + (-3 if delta > 0 else 3) * self._stride())
            event.accept()
        else:
            super().wheelEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.viewport().rect(), QColor("#eaf1f9"))
        animated = self.drop_x is not None or self.returning
        if self.drop_preview:
            self._paint_drop_preview(painter)
        thumb_height = max(1, self.tile_height - 22)
        step = self._stride()
        first = self.horizontalScrollBar().value()
        scale = self._frame_width()
        if self.compaction.count:
            visible = ((frame, self._frame_ref(frame)) for frame in self._drag_frames())
        else:
            visible = zip(self._visible_frames(), self._visible())
        for frame, index in visible:
            rect = self._tile_rect((frame - 1 - first) // step)
            if animated:
                state = self.springs.states.get(frame)
                if state:
                    rect.translate(round(state.position), 0)
                if self.compaction.count and not self.returning:
                    start = frame - 1
                    remaining = self.compaction.collapse(min(self.total, start + step)) - self.compaction.collapse(start)
                    rect.setWidth(round(remaining * scale))
            if rect.width() <= 0 or not rect.intersects(self.viewport().rect()):
                continue
            x, width = rect.left(), rect.width()
            thumb = QRect(x, 0, width, thumb_height)
            if index in self.cache:
                pixmap = self.cache[index]
                ratio = min(pixmap.width() / max(1, width), pixmap.height() / max(1, thumb_height))
                crop = QRectF(0, 0, width * ratio, thumb_height * ratio)
                crop.moveCenter(QRectF(pixmap.rect()).center())
                painter.drawPixmap(QRectF(thumb), pixmap, crop)
            else:
                painter.fillRect(thumb, QColor("#e0eaf5"))
                painter.setPen(QColor("#53677e"))
                painter.drawText(thumb, Qt.AlignmentFlag.AlignCenter, "暂无图" if index in self.unavailable else "…")
            for start, end in self.selection:
                left, right = max(start, frame), min(end + 1, frame + step)
                if left < right:
                    band = QRectF(x + width * (left - frame) / step, 0,
                                  width * (right - left) / step, thumb_height)
                    painter.fillRect(band, QColor(23, 91, 188, 64))
                    painter.fillRect(QRectF(band.x(), thumb_height - 3, band.width(), 3), QColor("#3a87e6"))
            label = QRect(x, thumb_height, width, self.tile_height - thumb_height)
            painter.fillRect(label, QColor("#F5F8FF"))
            painter.setPen(QColor("#0969DA" if frame == self.current else "#354967"))
            stamp = f"{(frame - 1) / self.frames.fps:.2f} s" if step > 1 and self.frames else f"第 {frame} 帧"
            painter.drawText(label, Qt.AlignmentFlag.AlignCenter, stamp)
            if step > 1 and frame <= self.current < frame + step:
                marker = x + (width - 2) * (self.current - frame) / step
                painter.fillRect(QRectF(marker, 0, 2, thumb_height), QColor("#1677FF"))
            if frame == self.current:
                painter.setPen(QPen(QColor("#1677FF"), 2))
                painter.drawRect(thumb.adjusted(1, 1, -1, -1))
                painter.fillRect(x + 2, self.tile_height - 3, max(1, width - 4), 3, QColor("#1677FF"))
        if not self.total:
            painter.setPen(QColor("#53677e"))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, "等待读取视频帧")
        if self.move_point is not None:
            self._paint_floating_frame(painter)
        painter.end()

    def _paint_drop_preview(self, painter):
        name, picture, _ = self.drop_preview
        anchor = self.springs.states.get("anchor")
        width = self.springs.states.get("width")
        if not anchor or not width or width.position < 5:
            return
        x = round(anchor.position - self.horizontalScrollBar().value() * self._frame_width())
        cell = QRect(x, 0, round(width.position), self.tile_height)
        cell = cell.intersected(self.viewport().rect()).adjusted(2, 2, -2, -2)
        if cell.isEmpty():
            return
        painter.setBrush(QColor("#e3eefc"))
        painter.setPen(QPen(QColor("#5387c9"), 1, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(cell, 4, 4)
        if self.move_point is not None:
            return
        self._paint_preview_contents(painter, cell, name, picture)

    def _paint_floating_frame(self, painter):
        frame, press = self.move_press
        column = self._column_at(press.x())
        tile = self._tile_rect(column)
        left = self.move_point.x() - (press.x() - tile.left())
        top = self.move_point.y() - press.y()
        card = QRect(round(left), round(top), round(tile.width() * 1.02), round(tile.height() * 1.02))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(24, 52, 91, 43))
        painter.drawRoundedRect(card.adjusted(-2, 1, 4, 5), 6, 6)
        painter.setPen(QPen(QColor("#5395ed"), 1))
        painter.setBrush(QColor("#F5F8FF"))
        painter.drawRoundedRect(card, 5, 5)
        name, picture, _ = self.drop_preview
        self._paint_preview_contents(painter, card, name if self.drop_frames > 1 else f"第 {frame} 帧", picture)

    def _paint_preview_contents(self, painter, cell, name, picture):
        image_area = cell.adjusted(4, 3, -4, -18)
        if picture is not None and not picture.isNull() and not image_area.isEmpty():
            scaled = picture.scaled(image_area.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            painter.setOpacity(.55)
            painter.drawPixmap(image_area.center().x() - scaled.width() // 2,
                               image_area.center().y() - scaled.height() // 2, scaled)
            painter.setOpacity(1)
        else:
            painter.setPen(QColor("#50749a"))
            painter.drawText(image_area, Qt.AlignmentFlag.AlignCenter, "插入素材")
        label = cell.adjusted(4, cell.height() - 16, -4, 0)
        painter.setPen(QColor("#315b88"))
        text = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, max(0, label.width()))
        painter.drawText(label, Qt.AlignmentFlag.AlignCenter, text)

    def shutdown(self):
        self.closing = True
        self.timer.stop()
        self._stop_drag()
        self.move_press = None
        self._clear_drop(animated=False)
        if self.scope is not None:
            self.scope.cancel()
        self.pool.shutdown(wait=True, cancel_futures=True)
