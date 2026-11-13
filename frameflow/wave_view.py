"""Interactive waveform display and selection in the media preview."""

from __future__ import annotations

from math import ceil, floor, log10

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

from .editor_widgets import PreviewLabel
from .filmstrip import EDIT_KEYS
from .motion import queued_media_key


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
