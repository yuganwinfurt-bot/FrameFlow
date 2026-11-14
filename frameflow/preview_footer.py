"""Render the preview task status received from the active editor."""
from dataclasses import dataclass

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar

from .icons import icon
from .motion import Button
from .widgets import ElidedLabel


@dataclass(frozen=True)
class PreviewStatus:
    can_export: bool
    can_cancel: bool
    message: str
    maximum: int
    progress: int


class PreviewFooter(QFrame):
    def __init__(self, start, cancel):
        super().__init__()
        self.setObjectName("footer")
        line = QHBoxLayout(self)
        line.setContentsMargins(16, 8, 16, 10)
        line.setSpacing(12)
        self.status = ElidedLabel("就绪")
        line.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedSize(150, 6)
        line.addWidget(self.progress)
        self.percent = QLabel("")
        self.percent.hide()
        self.cancel = Button("取消转换")
        self.cancel.clicked.connect(cancel)
        self.cancel.setEnabled(False)
        line.addWidget(self.cancel)
        self.start = Button("开始导出")
        self.start.setObjectName("primary")
        self.start.setMinimumWidth(112)
        self.start.setIcon(icon("export", 15, "#FFFFFF", 1.8))
        self.start.setIconSize(QSize(15, 15))
        self.start.clicked.connect(start)
        line.addWidget(self.start)

    def show_task(self, status: PreviewStatus | None):
        self.start.setVisible(status is not None)
        status = status or PreviewStatus(False, False, "等待选择媒体", 100, 0)
        self.start.setEnabled(status.can_export)
        self.cancel.setEnabled(status.can_cancel)
        self.status.setText(status.message or "就绪")
        self.progress.setRange(0, status.maximum)
        self.progress.setValue(status.progress)
        self.percent.setText(f"{status.progress}%" if status.maximum else "…")
