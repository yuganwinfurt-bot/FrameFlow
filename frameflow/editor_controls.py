"""Small controls shared by the editor and its settings panel."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton

from . import icons
from .motion import Button, Combo, SpinBox


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


def _spin(*, seconds=False):
    field = QDoubleSpinBox() if seconds else SpinBox()
    if seconds:
        field.setDecimals(3)
        field.setSingleStep(.1)
        field.setSuffix(" 秒")
    return field


def timecode(seconds):
    seconds = max(0.0, float(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, remainder = divmod(remainder, 60)
    if hours:
        return f"{int(hours)}:{int(minutes):02d}:{remainder:06.3f}"
    return f"{int(minutes):02d}:{remainder:06.3f}"
