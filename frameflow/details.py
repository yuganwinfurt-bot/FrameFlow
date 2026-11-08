"""结构化的媒体详情面板。

主窗口的「文件详情…」窗口和编辑工作台的「文件详情」页共用同一个控件：

- 顶部是缩略图、文件名和一行速览（类型 · 容器 · 分辨率 · 时长）；
- 中间是三个关键指标（时长 / 大小 / 分辨率之类），一眼能扫到；
- 下面按「文件 / 视频 / 音频 / 图片」分组列字段，鼠标移到某一行，右侧会露出复制按钮。

旧版是一整块等宽文本框，信息糊成一片，也不方便复制单个字段。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from .media import AudioInfo, ImageInfo, MediaInfo, VideoInfo, detail_rows
from .motion import Button
from .theme import C

LABEL_WIDTH = 78
THUMB_SIZE = QSize(104, 64)
GLYPH_ZONE = 30


def _clock(seconds: float) -> str:
    """把秒数写成 mm:ss 或 h:mm:ss，用于速览行和指标块。"""
    total = int(round(seconds or 0))
    if total <= 0:
        return "—"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _size(size: int) -> str:
    """和队列里的 file_size() 用同一套十进制单位，避免同一个文件显示两个数。"""
    if not size:
        return "—"
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.2f} GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.2f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.1f} KB"
    return f"{size} B"


def _summary(path: Path, info: MediaInfo) -> str:
    """速览行：类型 · 扩展名 · 主要参数。"""
    suffix = path.suffix.lstrip(".").upper()
    if isinstance(info, VideoInfo):
        parts = ["视频", suffix, f"{info.width}×{info.height}" if info.width else "", _clock(info.duration)]
    elif isinstance(info, AudioInfo):
        parts = ["音频", suffix,
                 f"{info.sample_rate / 1000:g} kHz" if info.sample_rate else "", _clock(info.duration)]
    else:
        parts = ["图片", suffix, f"{info.width}×{info.height}" if info.width else ""]
    return " · ".join(part for part in parts if part)


def _stats(info: MediaInfo) -> list[tuple[str, str]]:
    """三到四个关键指标，顺序按重要性排。"""
    if isinstance(info, VideoInfo):
        return [(_clock(info.duration), "时长"), (_size(info.size), "大小"),
                (f"{info.width}×{info.height}", "分辨率"),
                (f"{info.fps:.0f}" if info.fps else "—", "帧/秒")]
    if isinstance(info, AudioInfo):
        channels = {1: "单声道", 2: "立体声"}.get(info.channels, f"{info.channels} 声道" if info.channels else "—")
        return [(_clock(info.duration), "时长"), (_size(info.size), "大小"),
                (f"{info.sample_rate / 1000:g} kHz" if info.sample_rate else "—", "采样率"), (channels, "声道")]
    return [(f"{info.width}×{info.height}" if info.width else "—", "分辨率"), (_size(info.size), "大小"),
            ((info.mode or "—").upper(), "颜色模式")]


def _rounded(pixmap: QPixmap, radius: int) -> QPixmap:
    """把缩略图裁成圆角，QLabel 的 border-radius 不会裁剪内容。"""
    ratio = pixmap.devicePixelRatio() or 1.0
    width, height = pixmap.width() / ratio, pixmap.height() / ratio
    result = QPixmap(pixmap.size())
    result.setDevicePixelRatio(ratio)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.0, 0.0, width, height), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(QRectF(0.0, 0.0, width, height), pixmap, QRectF(pixmap.rect()))
    painter.end()
    return result


class DetailRow(QWidget):
    """一行「字段 / 值」。悬停时整行浅色高亮，右端露出复制按钮。"""

    def __init__(self, name: str, value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("detailRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self._value = value
        self._hover = False
        self._flash = False

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, GLYPH_ZONE, 6)
        row.setSpacing(10)
        self.key = QLabel(name)
        self.key.setObjectName("detailLabel")
        self.key.setFixedWidth(LABEL_WIDTH)
        self.key.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.value = QLabel(value)
        self.value.setObjectName("detailValue")
        self.value.setWordWrap(True)
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.value.setToolTip(value)
        row.addWidget(self.key)
        row.addWidget(self.value, 1)

    # ------------------------------------------------------------------ 绘制
    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:
        over = event.position().x() >= self.width() - GLYPH_ZONE
        self.setCursor(Qt.CursorShape.PointingHandCursor if over else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton
                and event.position().x() >= self.width() - GLYPH_ZONE):
            self._copy()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(C.sunken.name()))
            painter.drawRoundedRect(self.rect().adjusted(0, 1, -1, -1), 4, 4)
        painter.setPen(QPen(QColor(C.border_soft.name()), 1))
        painter.drawLine(8, self.height() - 1, self.width() - 8, self.height() - 1)
        if self._flash:
            painter.setPen(QColor(C.accent.name()))
            painter.drawText(QRect(self.width() - 62, 0, 54, self.height()),
                             int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), "已复制")
        elif self._hover:
            self._paint_glyph(painter)

    def _paint_glyph(self, painter: QPainter) -> None:
        """两个错开的小圆角矩形，表示「复制」。"""
        top = (self.height() - 13) // 2
        left = self.width() - GLYPH_ZONE + 8
        painter.setPen(QPen(QColor(C.text3.name()), 1.1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRect(left + 3, top, 9, 11), 2, 2)
        painter.setPen(QPen(QColor(C.text2.name()), 1.1))
        painter.drawRoundedRect(QRect(left, top + 3, 9, 11), 2, 2)

    # ------------------------------------------------------------------ 复制
    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._value)
        self._flash = True
        self.update()
        QTimer.singleShot(900, self._clear_flash)

    def _clear_flash(self) -> None:
        self._flash = False
        self.update()


class _Group(QFrame):
    """一组字段：小标题 + 一条细分隔线 + 若干行。"""

    def __init__(self, title: str, rows: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("detailGroup")
        box = QVBoxLayout(self)
        box.setContentsMargins(7, 7, 7, 5)
        box.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 8, 4)
        head.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("detailSection")
        head.addWidget(label)
        rule = QFrame()
        rule.setObjectName("detailRule")
        rule.setFixedHeight(1)
        rule.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        head.addWidget(rule, 1)
        box.addLayout(head)

        for name, value in rows:
            box.addWidget(DetailRow(name, value))
        box.addSpacing(2)


class DetailsView(QWidget):
    """只读的媒体信息面板。set_media() 重建内容，clear() 回到空态。"""

    def __init__(self, parent=None, *, wide: bool = False):
        super().__init__(parent)
        self.setObjectName("detailsView")
        self._wide = wide
        self._info: MediaInfo | None = None
        self._path: Path | None = None
        self._rows: list[tuple[str, list[tuple[str, str]]]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.body = QWidget()
        self.body.setObjectName("detailsBody")
        content = QVBoxLayout(self.body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(9)

        # 顶部速览：缩略图 + 文件名 + 类型行 + 复制全部
        self.hero = QWidget()
        self.hero.setObjectName("detailHero")
        hero = QHBoxLayout(self.hero)
        hero.setContentsMargins(12, 12, 12, 12)
        hero.setSpacing(12)
        self.thumb = QLabel()
        self.thumb.setObjectName("detailThumb")
        self.thumb.setFixedSize(THUMB_SIZE)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setText("预览")
        hero.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(3)
        self.title = QLabel("文件详情")
        self.title.setObjectName("detailTitle")
        self.title.setWordWrap(True)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("detailSub")
        self.subtitle.setWordWrap(True)
        self.source_path = QLabel()
        self.source_path.setObjectName("detailPath")
        self.source_path.setTextFormat(Qt.TextFormat.PlainText)
        self.source_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source_path.setWordWrap(True)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        titles.addWidget(self.source_path)
        titles.addStretch(1)
        hero.addLayout(titles, 1)
        self.copy_button = Button("复制全部")
        self.copy_button.setProperty("flat", True)
        self.copy_button.setToolTip("把当前文件的全部信息复制到剪贴板")
        self.copy_button.clicked.connect(self._copy_all)
        self.copy_button.setEnabled(False)
        hero.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignTop)
        content.addWidget(self.hero)

        # 指标块
        self.stats = QWidget()
        self.stats.setObjectName("detailStats")
        self.stats_layout = QHBoxLayout(self.stats)
        self.stats_layout.setContentsMargins(12, 9, 12, 9)
        self.stats_layout.setSpacing(0)
        content.addWidget(self.stats)
        content.addSpacing(14)

        # 分组区
        self.groups = QWidget()
        self.groups.setObjectName("detailGroups")
        self.groups_layout = QGridLayout() if wide else QVBoxLayout()
        self.groups_layout.setContentsMargins(0, 0, 0, 0)
        self.groups_layout.setSpacing(14 if wide else 10)
        if wide:
            self.groups_layout.setColumnStretch(0, 1)
            self.groups_layout.setColumnStretch(1, 1)
            self.groups_layout.setHorizontalSpacing(28)
        self.groups.setLayout(self.groups_layout)
        content.addWidget(self.groups)
        content.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("detailsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.body)
        self.scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(self.scroll, 1)

        self.empty = QLabel("选择文件后显示完整信息")
        self.empty.setObjectName("detailEmpty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.empty, 1)
        self.scroll.hide()

    # ------------------------------------------------------------------ 数据
    def set_media(self, path: Path, info: MediaInfo,
                  extra: list[tuple[str, list[tuple[str, str]]]] | None = None,
                  picture: QPixmap | None = None) -> None:
        self._path, self._info = path, info
        self._rows = self._prune(detail_rows(path, info) + list(extra or ()))
        self._build(picture)
        self.copy_button.setEnabled(True)

    @staticmethod
    def _prune(rows: list[tuple[str, list[tuple[str, str]]]]
               ) -> list[tuple[str, list[tuple[str, str]]]]:
        """去掉探测不到的字段，省得整页都是「未知」。"""
        pruned = []
        for title, fields in rows:
            kept = [(name, value) for name, value in fields
                    if value not in ("", "—", "未知", "None")]
            if kept:
                pruned.append((title, kept))
        return pruned

    def clear(self) -> None:
        self._path = self._info = None
        self._rows = []
        self._build(None)
        self.copy_button.setEnabled(False)

    def text(self) -> str:
        """当前内容的纯文本形式，便于复查和复制。"""
        if self._path is None or self._info is None:
            return ""
        return "\n".join(f"{name}：{value}" for _, rows in self._rows for name, value in rows)

    def is_empty(self) -> bool:
        return not self._rows

    # ------------------------------------------------------------------ 渲染
    def _build(self, picture: QPixmap | None) -> None:
        self._clear_groups()
        self.empty.setVisible(not self._rows)
        self.scroll.setVisible(bool(self._rows))
        if not self._rows:
            return

        self.title.setText(self._path.name if self._path is not None else "文件详情")
        self.subtitle.setText(_summary(self._path, self._info)
                              if self._info is not None and self._path is not None else "")
        self.source_path.setText(str(self._path) if self._path else "")
        self._set_thumb(picture)

        while self.stats_layout.count():
            item = self.stats_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        if self._info is not None:
            for index, (value, caption) in enumerate(_stats(self._info)):
                if index:
                    spacer = QFrame()
                    spacer.setObjectName("statRule")
                    spacer.setFixedWidth(1)
                    self.stats_layout.addWidget(spacer)
                self.stats_layout.addWidget(self._stat(value, caption), 1)

        for index, (title, rows) in enumerate(self._rows):
            group = _Group(title, rows)
            if self._wide:
                self.groups_layout.addWidget(group, index // 2, index % 2,
                                             Qt.AlignmentFlag.AlignTop)
            else:
                self.groups_layout.addWidget(group, index)

    def _clear_groups(self) -> None:
        layout = self.groups_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先摘掉父子关系，否则 deleteLater 生效前旧内容还会画在原位
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _stat(self, value: str, caption: str) -> QWidget:
        box = QWidget()
        stack = QVBoxLayout(box)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(1)
        number = QLabel(value)
        number.setObjectName("statValue")
        label = QLabel(caption)
        label.setObjectName("statLabel")
        stack.addWidget(number)
        stack.addWidget(label)
        return box

    def _set_thumb(self, picture: QPixmap | None) -> None:
        if picture is None or picture.isNull():
            suffix = (self._path.suffix.lstrip(".") if self._path is not None else "").upper()
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText(suffix or "文件")
            self.thumb.setProperty("filled", False)
        else:
            source = QPixmap(picture)
            source.setDevicePixelRatio(1.0)
            scaled = source.scaled(THUMB_SIZE, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                   Qt.TransformationMode.SmoothTransformation)
            offset = QPoint(max(0, (scaled.width() - THUMB_SIZE.width()) // 2),
                            max(0, (scaled.height() - THUMB_SIZE.height()) // 2))
            self.thumb.setPixmap(_rounded(scaled.copy(QRect(offset, THUMB_SIZE)), 6))
            self.thumb.setText("")
            self.thumb.setProperty("filled", True)
        self.thumb.style().unpolish(self.thumb)
        self.thumb.style().polish(self.thumb)

    # ------------------------------------------------------------------ 复制
    def _copy_all(self) -> None:
        text = self.text()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.copy_button.setText("已复制")
        self.copy_button.setEnabled(False)
        QTimer.singleShot(1200, self._reset_copy)

    def _reset_copy(self) -> None:
        self.copy_button.setText("复制全部")
        self.copy_button.setEnabled(bool(self._rows))


class DetailsDialog(QDialog):
    """独立窗口形态的「文件详情」。属性栏只有 352px 宽，塞不下完整参数表。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("detailsDialog")
        self.setWindowTitle("文件详情")
        self.resize(620, 660)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 0)
        layout.setSpacing(10)
        self.view = DetailsView()
        layout.addWidget(self.view, 1)
        bar = QFrame()
        bar.setObjectName("dialogFooter")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 10, 0, 12)
        row.addStretch(1)
        close = Button("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addWidget(bar)

    def show_media(self, path: Path, info: MediaInfo,
                   extra: list[tuple[str, list[tuple[str, str]]]] | None = None,
                   picture: QPixmap | None = None) -> None:
        self.view.set_media(path, info, extra, picture)
        self.setWindowTitle(f"文件详情 · {path.name}")
        self.resize(620, 660)
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(30, self._fit_content)

    def _fit_content(self) -> None:
        """按最后一组字段的实际底边收窗口，免得下面留一大片空白。"""
        layout = self.view.groups_layout
        last = None
        for index in range(layout.count() - 1, -1, -1):
            item = layout.itemAt(index)
            last = item.widget() if item is not None else None
            if last is not None:
                break
        bottom = self.view.groups.y() + (last.geometry().bottom() if last is not None else 0)
        if bottom <= 1:
            bottom = sum(part.sizeHint().height() for part in
                         (self.view.hero, self.view.stats, self.view.groups)) + 34
        self.resize(620, max(380, min(800, bottom + 112)))
        self.raise_()
        self.activateWindow()


__all__ = ["DetailsView", "DetailsDialog", "DetailRow"]
