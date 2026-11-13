"""Pending and sent queue attachments displayed in the Agent workspace."""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .icons import icon
from .motion import Button, hide_vertical_scroll_bar
from .widgets import label as text_label


@dataclass(frozen=True)
class Attachment:
    key: str
    name: str
    detail: str
    picture: QPixmap
    preview: QPixmap | None = None


class AttachmentCard(QFrame):
    def __init__(self, item):
        super().__init__()
        self.setObjectName("agentAttachment")
        self.setToolTip(item.name)
        row = QHBoxLayout(self)
        row.setContentsMargins(7, 4, 7, 4)
        row.setSpacing(8)
        thumb = QLabel()
        thumb.setPixmap(item.picture.scaled(44, 28, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation))
        row.addWidget(thumb)
        name = text_label(item.name)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        name.setMinimumWidth(52)
        row.addWidget(name, 1)
        detail = text_label(item.detail)
        detail.setProperty("muted", True)
        row.addWidget(detail)


class PendingAttachmentCard(QFrame):
    WIDTH = 144
    HEIGHT = 78

    def __init__(self, item, remove, parent):
        super().__init__(parent)
        self.setObjectName("agentPendingCard")
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(3)
        top = QGridLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.thumbnail = QLabel()
        self.thumbnail.setObjectName("agentPendingThumbnail")
        self.thumbnail.setFixedHeight(44)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.thumbnail, 0, 0)
        self.remove_button = Button("")
        self.remove_button.setFixedSize(24, 24)
        self.remove_button.setIcon(icon("close", 13))
        self.remove_button.setToolTip("移除附件")
        self.remove_button.clicked.connect(lambda _, key=item.key: remove(key))
        top.addWidget(self.remove_button, 0, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        self.name = text_label("")
        self.name.setObjectName("agentPendingName")
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.name)
        self.set_item(item)

    def set_item(self, item):
        picture = item.preview if item.preview is not None else item.picture
        signature = (item.name, item.detail, picture.cacheKey())
        if signature == self.signature:
            return
        self.signature = signature
        self.setToolTip(f"{item.name}\n{item.detail}")
        self.remove_button.setAccessibleName(f"移除附件 {item.name}")
        self.name.ensurePolished()
        name = self.name.fontMetrics().elidedText(item.name, Qt.TextElideMode.ElideMiddle, self.WIDTH - 16)
        self.name.setText(name)
        ratio = self.devicePixelRatioF()
        preview = picture.scaled(round(78 * ratio), round(42 * ratio), Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        if preview.isNull():
            preview = icon("file", 22).pixmap(22, 22)
        else:
            preview.setDevicePixelRatio(ratio)
        self.thumbnail.setPixmap(preview)


class PendingAttachments(QScrollArea):
    GAP = 8
    INSET = 1
    VISIBLE_ROWS = 2

    def __init__(self, remove):
        super().__init__()
        self.remove = remove
        self.cards = {}
        self.columns = 0
        self.preferred_height = 0
        self.setObjectName("agentPending")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(self)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.body = QWidget()
        self.body.setObjectName("agentPendingBody")
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(self.INSET, self.INSET, self.INSET, self.INSET)
        self.grid.setSpacing(self.GAP)
        self.grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.body)
        self.setMaximumHeight(0)
        self.hide()

    def set_items(self, items):
        cards = {}
        for item in items:
            card = self.cards.get(item.key)
            if card is None:
                card = PendingAttachmentCard(item, self.remove, self.body)
            else:
                card.set_item(item)
            cards[item.key] = card
        if tuple(cards) == tuple(self.cards):
            return
        for key in self.cards.keys() - cards.keys():
            card = self.cards[key]
            self.grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = cards
        self._reflow(force=True)
        if not cards:
            self.verticalScrollBar().setValue(0)
        self.setVisible(bool(cards))

    def _columns_at(self, width):
        return max(1, (width - 2 * self.INSET + self.GAP) // (PendingAttachmentCard.WIDTH + self.GAP))

    def _height_for_rows(self, rows):
        return 2 * self.INSET + rows * PendingAttachmentCard.HEIGHT + (rows - 1) * self.GAP if rows else 0

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(self.preferred_height)
        return hint

    def _reflow(self, *, force=False):
        # Keep the existing card gutter and wrapping thresholds with the scroll bar hidden.
        width = self.contentsRect().width() - self.verticalScrollBar().sizeHint().width()
        columns = self._columns_at(width)
        count = len(self.cards)
        if force or columns != self.columns:
            while self.grid.count():
                self.grid.takeAt(0)
            self.columns = columns
            for index, card in enumerate(self.cards.values()):
                self.grid.addWidget(card, *divmod(index, columns))

        # Size from the live card count, including widgets waiting for their first Show event.
        rows = (count + columns - 1) // columns
        content_height = self._height_for_rows(rows)
        if self.body.minimumHeight() != content_height:
            self.body.setFixedHeight(content_height)
        visible_height = self._height_for_rows(min(rows, self.VISIBLE_ROWS))
        if visible_height != self.preferred_height:
            self.preferred_height = visible_height
            self.setMinimumHeight(self._height_for_rows(min(rows, 1)))
            self.setMaximumHeight(visible_height)
            self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()
