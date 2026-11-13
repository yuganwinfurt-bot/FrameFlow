"""Small widget helpers shared by desktop workspaces."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy


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


def fit_pixmap(source: QPixmap, size: QSize, mode: Qt.AspectRatioMode = Qt.AspectRatioMode.KeepAspectRatio) -> QPixmap:
    """先归一化到 1.0 像素比，再按屏幕比例缩放，避免高 DPI 下缩略图被裁一截。"""
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    plain = QPixmap(source)
    plain.setDevicePixelRatio(1.0)
    target = QSize(max(1, round(size.width() * ratio)), max(1, round(size.height() * ratio)))
    scaled = plain.scaled(target, mode, Qt.TransformationMode.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled
