"""Transient rendering for the wide and narrow media lists."""

from math import ceil, floor

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem, QWidget

from .spring import SpringClock, edge_speed, insertion_slot
from .theme import C


class QueueDrag(QWidget):
    def __init__(self, view, row_height):
        super().__init__(view.viewport())
        self.view = view
        self.row_height = row_height
        self.clock = SpringClock(self, self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self.items = []
        self.images = {}
        self.pointer = None
        self.slot = None
        self.active = self.landing = False
        self.native = False
        view.verticalScrollBar().valueChanged.connect(self.reflow)

    def start(self, item, grip, point):
        self.reset()
        self.items = [self.view.item(i) for i in range(self.view.count())]
        self.source = self.view.row(item)
        self.grip = grip
        self.setGeometry(self.view.viewport().rect())
        rect = self.view.visualItemRect(item)
        self.origin = rect.top() + self.view.verticalScrollBar().value() - self.source * self.row_height
        self.floating = self._lift(self._image(self.source))
        self.hotspot = QPoint(round(grip.x() * 1.02) + 7, round(grip.y() * 1.02) + 5)
        self.active = True
        self.scroll_fraction = 0.0
        self.track(point)
        self.show()
        self.raise_()

    def _lift(self, image):
        ratio = image.devicePixelRatio()
        size = image.deviceIndependentSize()
        width, height = size.width() * 1.02, size.height() * 1.02
        pixmap = QPixmap(ceil((width + 14) * ratio), ceil((height + 15) * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        card = QRectF(7, 5, width, height)
        for margin, opacity in ((5, 10), (3, 16), (1, 24)):
            painter.setBrush(QColor(30, 55, 92, opacity))
            painter.drawRoundedRect(card.adjusted(-margin, -margin + 3, margin, margin + 3), 8, 8)
        painter.setBrush(C.surface)
        painter.drawRoundedRect(card, 6, 6)
        painter.drawPixmap(card, image, QRectF(image.rect()))
        painter.end()
        return pixmap

    def _image(self, index):
        if index not in self.images:
            item = self.items[index]
            row = self.view.itemWidget(item)
            if row is not None:
                image = row.grab()
            else:
                ratio = self.devicePixelRatioF()
                image = QPixmap(ceil(self.width() * ratio), ceil(self.row_height * ratio))
                image.setDevicePixelRatio(ratio)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                option = QStyleOptionViewItem()
                option.initFrom(self.view)
                option.rect = self.rect().adjusted(0, 0, 0, self.row_height - self.height())
                if item.isSelected():
                    option.state |= QStyle.StateFlag.State_Selected
                self.view.itemDelegate().paint(painter, option, self.view.indexFromItem(item))
                painter.end()
            self.images[index] = image
        return self.images[index]

    def track(self, point):
        self.pointer = QPointF(point) if point is not None else None
        if point is None:
            self.slot = None
        self.reflow()

    def _visible_indices(self):
        offset = self.view.verticalScrollBar().value() - self.origin
        first = max(0, floor(offset / self.row_height) - 3)
        last = min(len(self.items), ceil((offset + self.height()) / self.row_height) + 4)
        return range(first, last)

    def reflow(self, *_):
        if not (self.active or self.landing):
            return
        if self.view.count() != len(self.items):
            self.reset()
            return
        viewport = self.view.viewport().rect()
        if self.width() != viewport.width():
            self.images.clear()
        self.setGeometry(viewport)
        self.raise_()
        offset = self.view.verticalScrollBar().value()
        if self.pointer is not None and self.active:
            center = self.pointer.y() - self.grip.y() + self.row_height / 2 + offset - self.origin
            self.slot = insertion_slot(center, self.row_height, len(self.items), self.slot)
        visible = self._visible_indices()
        for i in list(self.clock.states):
            if isinstance(i, int) and i not in visible:
                self.clock.states.pop(i)
                self.images.pop(i, None)
        for i in visible:
            if i == self.source:
                continue
            slot = i - int(i > self.source)
            slot += int(self.slot is not None and slot >= self.slot)
            self.clock.aim(i, self.origin + i * self.row_height, self.origin + slot * self.row_height)
        self.update()
        self.clock.wake()

    def _tick(self, dt, moving):
        speed = edge_speed(self.pointer.y(), self.height()) if self.active and self.pointer is not None else 0
        bar = self.view.verticalScrollBar()
        scrolling = (speed < 0 and bar.value() > bar.minimum()) or (speed > 0 and bar.value() < bar.maximum())
        if scrolling:
            self.scroll_fraction += speed * dt
            pixels = int(self.scroll_fraction)
            if pixels:
                self.scroll_fraction -= pixels
                bar.setValue(bar.value() + pixels)
        else:
            self.scroll_fraction = 0
        if self.landing and not moving:
            self.reset()
        else:
            self.update()
        return scrolling

    def land(self, slot, point):
        if not self.active:
            return
        self.active, self.landing = False, True
        self.slot, self.pointer = slot, None
        offset = self.view.verticalScrollBar().value()
        self.clock.aim("float_x", max(-self.width() / 2, min(self.width() / 2, point.x() - self.hotspot.x())), -7)
        self.clock.aim("float_y", offset + max(-self.row_height, min(self.height(), point.y() - self.hotspot.y())),
                       self.origin + slot * self.row_height - 5)
        self.reflow()

    def reset(self):
        self.active = self.landing = False
        self.native = False
        self.clock.reset()
        self.items.clear()
        self.images.clear()
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), C.surface)
        offset = self.view.verticalScrollBar().value()
        if self.active and self.slot is not None:
            y = self.origin + self.slot * self.row_height - offset
            painter.setPen(QPen(C.accent_line, 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(3, y + 3, self.width() - 6, self.row_height - 6), 6, 6)
        for i in self._visible_indices():
            if i == self.source or i not in self.clock.states:
                continue
            y = self.clock.states[i].position - offset
            if -self.row_height < y < self.height():
                pixmap = self._image(i)
                painter.drawPixmap(QRectF(0, y, self.width(), self.row_height), pixmap, QRectF(pixmap.rect()))
        if self.landing:
            x = self.clock.states["float_x"].position
            y = self.clock.states["float_y"].position - offset
            painter.drawPixmap(QPointF(x, y), self.floating)
        elif self.active and not self.native and self.pointer is not None:
            painter.drawPixmap(self.pointer - QPointF(self.hotspot), self.floating)
        painter.end()
