"""自绘控件与转场动画。颜色统一从 theme 取，避免和样式表走样。"""


from PySide6.QtCore import (QAbstractAnimation, QEvent, QEasingCurve, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal,
                            QVariantAnimation)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QWheelEvent
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox,
                               QApplication, QComboBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QListView,
                               QPlainTextEdit, QPushButton, QScrollBar, QSizePolicy, QSlider, QSpinBox,
                               QStyle, QStyledItemDelegate, QStyleOptionButton, QStyleOptionSlider,
                               QStyleOptionSpinBox, QStyleOptionViewItem, QTabBar, QVBoxLayout, QWidget)

from .theme import C, RADIUS, RADIUS_SMALL, chevron
from .icons import pixmap as line_pixmap


class Motion(QVariantAnimation):
    def __init__(self, widget):
        super().__init__(widget)
        self.target = 0.0
        self.setStartValue(0.0)
        self.setEndValue(0.0)
        self.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.valueChanged.connect(self._redraw)

    @property
    def value(self) -> float:
        return float(self.currentValue())

    def _redraw(self, _value) -> None:
        self.parent().update()

    def move_to(self, target: float, milliseconds: int = 180) -> None:
        if target == self.target:
            return
        self.target = float(target)
        visible = self.parent().isVisible()
        start = self.value if visible else self.target
        self.stop()
        self.setStartValue(start)
        self.setEndValue(self.target)
        self.setDuration(milliseconds)
        if start != self.target:
            self.start()


def _mix(start: QColor, end: QColor, amount: float) -> QColor:
    return QColor.fromRgbF(*(a + (b - a) * amount for a, b in zip(start.getRgbF(), end.getRgbF())))


class _PixelScroll(QObject):
    def __init__(self, area):
        super().__init__(area)
        self.remainder = 0

    def eventFilter(self, viewport, event):
        if (event.type() != QEvent.Type.Wheel or not event.angleDelta().isNull()
                or event.modifiers() != Qt.KeyboardModifier.NoModifier):
            return False
        delta = event.pixelDelta()
        if abs(delta.y()) <= abs(delta.x()):
            return False
        area = self.parent()
        bar = area.verticalScrollBar()
        value = bar.value()
        if value == (bar.minimum() if delta.y() > 0 else bar.maximum()):
            self.remainder = 0
            return False
        step = max(1, area.fontMetrics().lineSpacing()) if isinstance(area, QPlainTextEdit) else 1
        distance = self.remainder + delta.y()
        units = int(distance / step)
        self.remainder = distance - units * step
        bar.setValue(value - units)
        event.accept()
        return True


def hide_vertical_scroll_bar(area):
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if isinstance(area, QAbstractItemView):
        area.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    # Qt already routes angle-based wheel events and keyboard navigation to the hidden bar.
    area.viewport().installEventFilter(_PixelScroll(area))


class _ParameterWheelFilter(QObject):
    def eventFilter(self, widget, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        control = widget.parentWidget() if isinstance(widget, QLineEdit) else widget
        adjusts_value = (isinstance(control, (QComboBox, QAbstractSpinBox, QAbstractSlider))
                         and not isinstance(control, QScrollBar))
        picture_preset = isinstance(widget, QTabBar) and widget.objectName() == "picturePreset"
        if not (adjusts_value or picture_preset):
            return False

        # Use the viewport's normal wheel path, including pixel-only touchpad events.
        # Stop at a dialog or popup boundary so an unscrollable dialog cannot scroll the window behind it.
        child, parent = widget, widget.parentWidget()
        while parent is not None and not child.isWindow():
            if isinstance(parent, QAbstractScrollArea) and child is parent.viewport():
                position = event.globalPosition()
                forwarded = QWheelEvent(child.mapFromGlobal(position), position,
                                        event.pixelDelta(), event.angleDelta(), event.buttons(),
                                        event.modifiers(), event.phase(), event.inverted(),
                                        event.source(), event.pointingDevice())
                QApplication.sendEvent(child, forwarded)
                event.accept()
                return True
            child, parent = parent, parent.parentWidget()
        event.ignore()
        return True


def install_parameter_wheel_policy():
    app = QApplication.instance()
    if app.findChild(_ParameterWheelFilter) is None:
        # Covers both FrameFlow widgets and Qt-created controls such as QInputDialog's spin box.
        app.installEventFilter(_ParameterWheelFilter(app))


class NavigationBar(QTabBar):
    """带蓝色滑动指示线的导航。"""

    ICON_SIZE = 18
    ICON_GAP = 6
    FONT_SIZE = 15

    def __init__(self):
        super().__init__()
        self.setExpanding(False)
        self.setUsesScrollButtons(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setDrawBase(False)
        self.position = 0.0

    def set_position(self, position):
        self.position = position
        self.update()

    def tabSizeHint(self, index):
        return self.minimumTabSizeHint(index).expandedTo(QSize(142, 43))

    def minimumTabSizeHint(self, index):
        font = QFont(self.font())
        font.setPixelSize(self.FONT_SIZE)
        font.setWeight(QFont.Weight.DemiBold)
        text_width = QFontMetrics(font).horizontalAdvance(self.tabText(index))
        return QSize(text_width + self.ICON_SIZE + self.ICON_GAP + 20, 43)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(C.sunken)
        radius = RADIUS - 2
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0, 0, -0.5, -0.5), radius, radius)
        index = min(int(self.position), self.count() - 1)
        start = self.tabRect(index)
        end = self.tabRect(min(index + 1, self.count() - 1))
        fraction = self.position - index
        active = QRectF(start).translated((end.left() - start.left()) * fraction, 0)
        active.setWidth(start.width() + (end.width() - start.width()) * fraction)
        active = active.adjusted(2, 2, -2, -2)
        painter.setBrush(C.accent_soft)
        painter.setPen(QPen(C.border, 1))
        painter.drawRoundedRect(active, RADIUS_SMALL, RADIUS_SMALL)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(C.accent)
        painter.drawRoundedRect(QRectF(active.left() + 5, active.bottom() - 1,
                                       active.width() - 10, 2.5), 1.25, 1.25)

        font = QFont(self.font())
        font.setPixelSize(self.FONT_SIZE)
        for index in range(self.count()):
            rect = self.tabRect(index)
            inside = active.contains(QRectF(rect).center())
            font.setWeight(QFont.Weight.DemiBold if inside else QFont.Weight.Normal)
            painter.setFont(font)
            ink = C.accent if inside else C.text
            title = self.tabText(index)
            width = painter.fontMetrics().horizontalAdvance(title)
            icon_space = self.ICON_SIZE + self.ICON_GAP
            left = rect.center().x() - (width + icon_space) // 2
            painter.drawPixmap(left, rect.center().y() - self.ICON_SIZE // 2,
                               line_pixmap(("gear", "video", "robot")[index], self.ICON_SIZE, ink.name(), 1.9))
            painter.setPen(ink)
            painter.drawText(rect.adjusted(left - rect.left() + icon_space, 0, 0, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(C.accent_line, 1))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -1, -1), radius, radius)
        painter.end()


class PageTransition(QLayout):
    """同一布局负责静止和转场时的几何，预览区由父窗口自然裁剪。"""

    settings_location_changed = Signal(bool)
    settled = Signal()
    navigation_position = Signal(float)
    queue_columns_progress = Signal(float)

    def __init__(self, parent, queue, rail, conversion_settings, workspace, rail_width, agent):
        super().__init__(parent)
        self._items = []
        self.queue = queue
        self.rail = rail
        self.conversion_settings = conversion_settings
        self.workspace = workspace
        self.agent = agent
        self.rail_width = rail_width
        self.target_preview = False
        self.target_agent = False
        self.settings_in_preview = False
        self.setContentsMargins(18, 12, 18, 12)
        self.setSpacing(10)
        for widget in (queue, rail, conversion_settings, workspace, agent):
            self.addWidget(widget)
        self.workspace.hide()
        self.workspace.raise_()
        self.agent.hide()
        self.agent.raise_()

        self.animation = self._animation()
        self.agent_animation = self._animation()

    def _animation(self):
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(360)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.valueChanged.connect(self._relayout)
        animation.finished.connect(self._settle)
        return animation

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def sizeHint(self):
        return QSize(0, 0)

    @property
    def position(self):
        return float(self.animation.currentValue())

    @property
    def running(self):
        return any(animation.state() != QAbstractAnimation.State.Stopped
                   for animation in (self.animation, self.agent_animation))

    @property
    def agent_position(self):
        return float(self.agent_animation.currentValue())

    def move_to(self, preview, agent=False):
        if (self.target_preview, self.target_agent) == (preview, agent):
            return
        self.target_preview = preview
        self.target_agent = agent
        if not self.parentWidget().isVisible():
            self._finish()
            return
        if not preview:
            # 两个可见的右侧工作区一起随外层退出。
            self.agent_animation.stop()
        elif not self.position:
            self.agent_animation.stop()
            self.agent_animation.setCurrentTime(self.agent_animation.duration() if agent else 0)
        else:
            self._drive(self.agent_animation, agent)
        self._drive(self.animation, preview)

    @staticmethod
    def _drive(animation, forward):
        current = animation.currentTime()
        animation.setDirection(QAbstractAnimation.Direction.Forward if forward
                               else QAbstractAnimation.Direction.Backward)
        endpoint = animation.duration() if forward else 0
        if animation.state() == QAbstractAnimation.State.Stopped and current != endpoint:
            animation.start()
            animation.setCurrentTime(current)

    def _settle(self):
        if not self.running:
            if not self.target_preview:
                self.agent_animation.setCurrentTime(0)
            self.settled.emit()

    def _finish(self):
        self.animation.stop()
        self.agent_animation.stop()
        self.agent_animation.setCurrentTime(self.agent_animation.duration() if self.target_agent else 0)
        self.animation.setCurrentTime(self.animation.duration() if self.target_preview else 0)
        self._relayout(self.position)
        self.settled.emit()

    def _relayout(self, _position):
        self.setGeometry(self.parentWidget().contentsRect())

    def setGeometry(self, bounds):
        super().setGeometry(bounds)
        if not bounds.isValid() or not hasattr(self, "agent_animation"):
            return
        area = self.contentsRect()
        position = self.position
        settings_width = self.conversion_settings.width()
        workspace_width = max(0, area.width() - self.rail_width - self.spacing())
        travelled = round(workspace_width * position)
        leading_edge = area.right() + 1 - travelled

        # 缩窄旧右栏的可见部分，并在新右栏露面前移交共用的设置控件。
        center_width = workspace_width - settings_width
        handoff = settings_width + round(.6 * max(0, center_width - settings_width))
        exposed = round(settings_width * max(0, 1 - travelled / handoff))
        old_settings_x = leading_edge - exposed
        gap = round(self.spacing() * position)
        queue_width = max(0, old_settings_x - area.left() - gap)
        # 表格工具栏放不下时换成紧凑队列，两套视图的边界始终相同。
        compact_width = max(self.queue.minimumSizeHint().width(), self.rail_width + 80)
        compact_width = min(compact_width, area.width() - settings_width - 1)
        compact = position > 0 and queue_width <= compact_width
        queue_bounds = QRect(area.left(), area.top(), queue_width, area.height())
        self.queue.setGeometry(queue_bounds)
        self.rail.setGeometry(queue_bounds)
        self.conversion_settings.setGeometry(old_settings_x, area.top(), settings_width, area.height())
        self.workspace.setGeometry(leading_edge, area.top(), workspace_width, area.height())
        agent_position = self.agent_position
        agent_edge = area.right() + 1 - round(workspace_width * position * agent_position)
        self.agent.setGeometry(agent_edge, area.top(), workspace_width, area.height())
        expanded_width = area.width() - settings_width
        fade_span = max(1, expanded_width - compact_width)
        self.queue_columns_progress.emit(min(1.0, max(0.0, (queue_width - compact_width) / fade_span)))

        in_preview = position > 0 and exposed == 0
        if in_preview != self.settings_in_preview:
            self.settings_in_preview = in_preview
            self.settings_location_changed.emit(in_preview)
        self.queue.setVisible(not compact)
        self.rail.setVisible(compact)
        self.conversion_settings.setVisible(not in_preview)
        self.workspace.setVisible(position > 0 and agent_position < 1)
        self.agent.setVisible(position > 0 and agent_position > 0)
        self.navigation_position.emit(position * (1 + agent_position))


class Button(QPushButton):
    """三段（常态 / 悬停 / 按下）自绘按钮，主按钮、次按钮和扁平按钮共用一套配色。"""

    def __init__(self, text: str):
        super().__init__(text)
        self.setProperty("standardButton", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hover_motion = Motion(self)
        self.press_motion = Motion(self)
        self.pressed.connect(self._press)
        self.released.connect(self._release)

    def _press(self) -> None:
        self.press_motion.move_to(1, 80)

    def _release(self) -> None:
        self.press_motion.move_to(0, 150)

    def enterEvent(self, event) -> None:
        self.hover_motion.move_to(1)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.hover_motion.move_to(0)
        super().leaveEvent(event)

    def _palette(self):
        primary = self.objectName() == "primary"
        flat = bool(self.property("flat"))
        if not self.isEnabled():
            return (C.surface_alt, C.surface_alt, C.surface_alt, C.border_soft, C.text3)
        if primary:
            return (C.accent, C.accent_hover, C.accent_press, C.accent, QColor("#FFFFFF"))
        if flat:
            return (QColor(Qt.GlobalColor.transparent), C.surface_alt, C.track,
                    QColor(Qt.GlobalColor.transparent), C.text2)
        return C.surface_alt, C.track, C.border, C.border, C.text

    def paintEvent(self, event) -> None:
        normal, hover, pressed, border, text = self._palette()
        amount = self.hover_motion.value if self.isEnabled() else 0.0
        press = self.press_motion.value if self.isEnabled() else 0.0
        if normal.alpha() == 0:
            # 透明底不能参与 RGB 混色，否则会混出一块灰黑；改成按悬停进度叠一层半透明填充
            fill = QColor(hover)
            fill.setAlphaF(min(1.0, amount + press * .6))
            if press:
                fill = QColor(pressed)
                fill.setAlphaF(min(1.0, press))
        else:
            fill = _mix(_mix(normal, hover, amount), pressed, press)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1) if border.alpha() else Qt.PenStyle.NoPen)
        radius = 10 if self.property("largeAction") else RADIUS_SMALL + 1
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), radius, radius)
        option.palette.setColor(option.palette.ColorRole.ButtonText, text)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        self._text_color = text
        self._paint_label(painter, option)
        if self.hasFocus() and self.isEnabled():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(C.accent_line, 1, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -2.5, -2.5),
                                    RADIUS_SMALL, RADIUS_SMALL)
        painter.end()

    def _paint_label(self, painter, option):
        if self.property("alignLeft"):
            # 属性面板里的「展开 / 收起」这类行，文字贴左更符合桌面习惯。
            painter.setPen(getattr(self, "_text_color", C.text))
            painter.drawText(QRectF(option.rect).adjusted(4, 0, -4, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())
            return
        self.style().drawControl(QStyle.ControlElement.CE_PushButtonLabel, option, painter, self)


class DirectoryButton(Button):
    """在同一个控件中显示并选择保存目录。"""

    def __init__(self, path):
        super().__init__("")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("选择保存目录")
        self.setStyleSheet("text-align: left;")
        self.set_path(path)

    def set_path(self, path):
        self.path = path
        self.setAccessibleDescription(path)
        self.setToolTip(path)
        self._update_label()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_label()

    def _update_label(self):
        compact = self.objectName() == "outputPath"
        self.visible_path = self.fontMetrics().elidedText(
            self.path, Qt.TextElideMode.ElideMiddle, max(0, self.width() - (8 if compact else 38)))
        self.setText(self.visible_path.replace("&", "&&"))

    def _paint_label(self, painter, option):
        painter.setPen(C.text if self.isEnabled() else C.text3)
        compact = self.objectName() == "outputPath"
        painter.drawText(option.rect.adjusted(3, 0, -4, 0) if compact else option.rect.adjusted(10, 0, -26, 0),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.visible_path)
        if not compact:
            chevron(painter, QPointF(option.rect.right() - 14, option.rect.center().y() + .5),
                    C.text3, "right", 3.6)


class DirectoryPathEdit(QLineEdit):
    """只读路径：单击整块就是「选择目录」，文字仍可框选复制。"""

    clicked = Signal()

    def __init__(self, path):
        super().__init__()
        self.path = ""
        self.setReadOnly(True)
        self.setAccessibleName("保存目录路径")
        self.setObjectName("outputPath")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_path(str(path))

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def set_path(self, path):
        self.path = str(path)
        self.setText(self.path)
        self.setToolTip(self.path)
        self.setCursorPosition(0)


QUEUED_MEDIA = "application/x-frameflow-queued-media"
QUEUED_MEDIA_BATCH = "application/x-frameflow-queued-media-batch"


def queued_media_key(mime_data):
    return bytes(mime_data.data(QUEUED_MEDIA)).decode("ascii", errors="ignore")


def local_files(mime_data) -> list[str]:
    return [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()]


class DropArea(Button):
    """空列表时的投放区：图标 + 说明 + 一个画出来的「选择文件」按钮。"""

    files_dropped = Signal(list)
    empty_height = 194
    compact_height = 74

    def __init__(self):
        super().__init__("添加文件")
        self.setProperty("standardButton", False)
        self.setToolTip("点击选择视频、音频和图片，或将文件拖到这里")
        self.setAcceptDrops(True)
        self.setMinimumHeight(240)
        self.drag_motion = Motion(self)
        self.height_motion = Motion(self)

    def set_compact(self, compact: bool) -> None:
        # 队列非空时投放区整体隐藏，高度不再变化，接口保留以免调用方改动。
        self.height_motion.move_to(int(compact))

    def dragEnterEvent(self, event) -> None:
        if self.isEnabled() and local_files(event.mimeData()):
            self.drag_motion.move_to(1)
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if self.isEnabled() and local_files(event.mimeData()):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.drag_motion.move_to(0)
        event.accept()

    def dropEvent(self, event) -> None:
        self.drag_motion.move_to(0)
        paths = local_files(event.mimeData())
        if self.isEnabled() and paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)

    def paintEvent(self, event) -> None:
        enabled = self.isEnabled()
        hover = self.hover_motion.value if enabled else 0.0
        drag = self.drag_motion.value if enabled else 0.0
        # 悬停只淡淡提亮，真正拖着文件进来才变成蓝色虚线框
        fill = _mix(C.surface_alt, C.accent_soft, max(hover * 0.45, drag))
        border = _mix(C.border, C.accent, max(hover * 0.55, drag))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(fill)
        painter.setPen(QPen(border, 1, Qt.PenStyle.DashLine if drag > 0.5 else Qt.PenStyle.SolidLine))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS, RADIUS)
        self._paint_empty(painter, drag)
        painter.end()

    def _paint_empty(self, painter, active):
        enabled = self.isEnabled()
        center = self.height() / 2
        icon = 46
        box = QRectF((self.width() - icon) / 2, center - 74, icon, icon)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_mix(C.accent_soft, C.accent_line, active))
        painter.drawRoundedRect(box, 12, 12)
        painter.setPen(QPen(_mix(C.accent, C.accent_hover, active), 1.8, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        plus = icon / 2 - 8
        painter.drawLine(QPointF(box.center().x() - plus, box.center().y()),
                         QPointF(box.center().x() + plus, box.center().y()))
        painter.drawLine(QPointF(box.center().x(), box.center().y() - plus),
                         QPointF(box.center().x(), box.center().y() + plus))

        font = QFont(self.font())
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(C.text if enabled else C.text3)
        painter.drawText(QRectF(0, center - 20, self.width(), 22),
                         Qt.AlignmentFlag.AlignCenter, "把视频、音频或图片拖到这里")
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(C.text3)
        painter.drawText(QRectF(0, center + 4, self.width(), 20),
                         Qt.AlignmentFlag.AlignCenter,
                         "支持 MP4 / MKV / MOV / MP3 / FLAC / PNG / GIF · 全部在本机处理")

        pill = QRectF((self.width() - 100) / 2, center + 34, 100, 30)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_mix(C.accent, C.accent_hover, active) if enabled else C.track)
        painter.drawRoundedRect(pill, RADIUS_SMALL + 1, RADIUS_SMALL + 1)
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF") if enabled else C.text3)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, "选择文件…")


class ChoiceDelegate(QStyledItemDelegate):
    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    def sizeHint(self, option, index):
        if not index.flags() & Qt.ItemFlag.ItemIsEnabled:
            return QSize(130, 8)
        note = index.data(Qt.ItemDataRole.ToolTipRole)
        return QSize(max(166, super().sizeHint(option, index).width() + 37), 48 if note else 34)

    def paint(self, painter, option, index):
        box = option.rect.adjusted(3, 1, -3, -1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not index.flags() & Qt.ItemFlag.ItemIsEnabled:
            painter.fillRect(QRect(box.left() + 8, box.center().y(), box.width() - 16, 1), C.divider)
        else:
            chosen = index.row() == self.combo.currentIndex()
            active = bool(option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver))
            if chosen or active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(C.accent_soft if chosen else C.sunken)
                painter.drawRoundedRect(box, 7, 7)
            decoration = index.data(Qt.ItemDataRole.DecorationRole)
            if isinstance(decoration, QIcon) and not decoration.isNull():
                decoration.paint(painter, QRect(box.left() + 8, box.center().y() - 8, 16, 16))
            elif chosen:
                painter.drawPixmap(box.left() + 8, box.center().y() - 8, line_pixmap("check", 16, C.accent.name()))
            body = box.adjusted(32, 0, -7, 0)
            note = index.data(Qt.ItemDataRole.ToolTipRole)
            title = index.data(Qt.ItemDataRole.DisplayRole) or ""
            font = QFont(option.font)
            font.setPixelSize(13)
            font.setWeight(QFont.Weight.DemiBold if chosen else QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(C.accent_press if chosen else C.text)
            painter.drawText(body.adjusted(0, 3, 0, -20) if note else body,
                             Qt.AlignmentFlag.AlignVCenter, QFontMetrics(font).elidedText(title, Qt.TextElideMode.ElideRight, body.width()))
            if note:
                font.setPixelSize(11)
                font.setWeight(QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(C.text2)
                painter.drawText(body.adjusted(0, 21, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                                 QFontMetrics(font).elidedText(note, Qt.TextElideMode.ElideRight, body.width()))
        painter.restore()


class Combo(QComboBox):
    def __init__(self):
        super().__init__()
        self.arrow_motion = Motion(self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        choices = QListView(self)
        choices.setObjectName("choiceList")
        choices.setMouseTracking(True)
        choices.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        choices.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setView(choices)
        self.setItemDelegate(ChoiceDelegate(self))
        self.setMaxVisibleItems(8)

    POPUP_ROW = 34
    POPUP_MAX_ROWS = 8

    def _popup_metrics(self) -> tuple[int, int]:
        """弹窗宽度与输入框一致，只有选项文字更长时才加宽；高度按行数算。"""
        model = self.model()
        metrics = QFontMetrics(self.font())
        widest = 0
        for row in range(self.count()):
            text = model.index(row, self.modelColumn()).data(Qt.ItemDataRole.DisplayRole) or ""
            widest = max(widest, metrics.horizontalAdvance(str(text)))
        width = max(self.width(), min(widest + 64, 440))
        total = 0
        for row in range(min(self.count(), self.POPUP_MAX_ROWS)):
            note = model.index(row, self.modelColumn()).data(Qt.ItemDataRole.ToolTipRole)
            total += 48 if note else self.POPUP_ROW
        return int(width), int(max(self.POPUP_ROW, total) + 2)

    def showPopup(self) -> None:
        view = self.view()
        width, height = self._popup_metrics()
        # height 只算行高，边框与内边距留给样式，避免视口被吃掉
        view.setFixedSize(width, height + 12)
        self._position_tries = 0
        super().showPopup()
        if view.isVisible():
            self.arrow_motion.move_to(1)
            QTimer.singleShot(0, self._position_popup)

    def _position_popup(self) -> None:
        """把弹窗锚到控件下沿：视口按行数定高，容器只包住视口。"""
        view = self.view()
        popup = view.window()
        if not popup.isVisible():
            if getattr(self, '_position_tries', 0) < 3:
                self._position_tries = getattr(self, '_position_tries', 0) + 1
                QTimer.singleShot(20, self._position_popup)
            return
        width, rows_height = self._popup_metrics()
        chrome = max(0, view.height() - view.viewport().height())
        view.setFixedSize(width, rows_height + chrome)
        frame = max(0, popup.frameWidth()) * 2
        popup_height = rows_height + chrome + frame
        popup_width = max(1, width)
        view.setFixedWidth(max(1, popup_width - frame * 2))

        top_left = self.mapToGlobal(QPoint(0, 0))
        center = self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))
        screen = QGuiApplication.screenAt(center) or self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        gap = 3
        below = max(0, area.bottom() + 1 - (top_left.y() + self.height() + gap))
        above = max(0, top_left.y() - area.top() - gap)
        if below >= popup_height or (below >= above and above < popup_height):
            y = top_left.y() + self.height() + gap
            if popup_height > below:
                popup_height = max(self.POPUP_ROW + chrome, below)
                view.setFixedHeight(max(self.POPUP_ROW, popup_height - frame - chrome) + chrome)
        else:
            if popup_height > above:
                popup_height = max(self.POPUP_ROW + chrome, above)
                view.setFixedHeight(max(self.POPUP_ROW, popup_height - frame - chrome) + chrome)
            y = top_left.y() - popup_height - gap
        popup_width = min(area.width(), popup_width)
        x = max(area.left(), min(top_left.x(), area.right() + 1 - popup_width))
        popup.setFixedWidth(popup_width)
        popup.setMaximumHeight(popup_height)
        popup.setGeometry(x, y, popup_width, popup_height)
        view.setFixedHeight(max(self.POPUP_ROW, popup_height - frame - chrome) + chrome)

    def hidePopup(self) -> None:
        super().hidePopup()
        self.arrow_motion.move_to(0)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() - 13, self.height() / 2)
        painter.rotate(self.arrow_motion.value * 180)
        chevron(painter, QPointF(0, 0), C.text2 if self.isEnabled() else C.text3, "down", 3.6)
        painter.end()


class SpinBox(QSpinBox):
    """数字输入框：自己画上下箭头，不用 QSS 的图片箭头。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        self.setMouseTracking(True)
        self._hot = 0

    def _button_rects(self):
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        up = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                                         QStyle.SubControl.SC_SpinBoxUp, self)
        down = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                                           QStyle.SubControl.SC_SpinBoxDown, self)
        return up, down

    def mouseMoveEvent(self, event) -> None:
        up, down = self._button_rects()
        position = event.position().toPoint()
        hot = 1 if up.contains(position) else (-1 if down.contains(position) else 0)
        if hot != self._hot:
            self._hot = hot
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if hot else Qt.CursorShape.IBeamCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hot:
            self._hot = 0
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        up, down = self._button_rects()
        if up.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = C.text2 if self.isEnabled() else C.text3
        for rect, direction, hot in ((up, "up", 1), (down, "down", -1)):
            if self._hot == hot and self.isEnabled():
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(C.sunken)
                painter.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 3, 3)
            # 箭头按格子高度收放，两个箭头之间要留出空隙，不然挤成一个「>」
            size = max(2.0, min(3.4, rect.height() / 4.5))
            chevron(painter, QPointF(rect.center()), colour, direction, size)
        painter.end()


class ClickSlider(QSlider):
    """点击轨道立即定位，支持按住拖动和键盘操作。"""

    def _value_from_position(self, position) -> int:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                      QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                      QStyle.SubControl.SC_SliderHandle, self)
        if self.orientation() == Qt.Orientation.Horizontal:
            span = max(1, groove.width() - handle.width())
            offset = int(position.x() - handle.width() / 2 - groove.x())
        else:
            span = max(1, groove.height() - handle.height())
            offset = int(groove.bottom() - position.y() - handle.height() / 2)
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                              max(0, min(span, offset)), span)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            value = self._value_from_position(event.position().toPoint())
            self.setSliderDown(True)
            self.setSliderPosition(value)
            self.setValue(value)
            self.sliderMoved.emit(value)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown() and event.buttons() & Qt.MouseButton.LeftButton:
            value = self._value_from_position(event.position().toPoint())
            self.setSliderPosition(value)
            self.setValue(value)
            self.sliderMoved.emit(value)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.isSliderDown():
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class QualityRow(QWidget):
    """质量微调：标题 + 当前值 + 说明 + 滑轨 + 两端提示。

    三个页面共用同一方向：最左 = 高质量（体积大），最右 = 小体积（质量低）。
    底层参数按编码器映射：
      * 视频 CRF：18 → 34 递增（左高右低）
      * 音频码率：320 → 96 递减（左高右低）
      * 图片质量：100 → 40 递减（左高右低）
    无损格式不伪造滑块，改为一行无损说明。
    """

    changed = Signal(int)
    settled = Signal(int)
    STEPS = 1000

    def __init__(self, label: str = "质量微调", minimum: int = 18, maximum: int = 34,
                 value: int = 23, parent=None, hint: str = "", icon_name: str = "",
                 detailed: bool = False, *, values=None, unit: str = "",
                 lossless_hint: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("qualityRow")
        self.detailed = detailed
        self.measure = "CRF"
        self.unit = unit
        self._values = [int(item) for item in values] if values else None
        self._lossless = False
        self._plain_hint = hint
        self._lossless_hint = lossless_hint or "无损格式，不提供有损质量调节。"
        self.note = None

        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(7)
        if icon_name:
            mark = QLabel()
            mark.setPixmap(line_pixmap(icon_name, 16, C.accent.name()))
            head.addWidget(mark)
        self.title = QLabel(label)
        self.title.setObjectName("qualityTitle")
        self.readout = QLabel()
        self.readout.setObjectName("qualityValue")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.readout)
        box.addLayout(head)

        self.note = QLabel(self._plain_hint)
        self.note.setObjectName("qualityHint")
        self.note.setWordWrap(True)
        box.addWidget(self.note)

        self.slider = ClickSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("qualitySlider")
        self._minimum = int(minimum)
        self._maximum = int(maximum)
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(200)
        self._settle_timer.timeout.connect(self._emit_settled)
        # 内部 0~1000 连续坐标：手柄跟手，读数再映射到 CRF / 码率 / 质量
        self.slider.setRange(0, self.STEPS)
        self.slider.setSingleStep(4)
        self.slider.setPageStep(50)
        self.slider.setValue(self._domain_to_position(int(value)))
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.setTracking(True)
        self.slider.valueChanged.connect(self._refresh)
        self.slider.sliderReleased.connect(self._emit_settled)
        box.addWidget(self.slider)

        self.ends = QWidget()
        ends = QHBoxLayout(self.ends)
        ends.setContentsMargins(0, 0, 0, 0)
        ends.setSpacing(8)
        low = QLabel("高质量")
        low.setObjectName("qualityHintEnd")
        high = QLabel("小体积")
        high.setObjectName("qualityHintEnd")
        ends.addWidget(low)
        ends.addStretch(1)
        ends.addWidget(high)
        box.addWidget(self.ends)

        self._refresh(self.slider.value(), emit=False)

    # ---- 取值 / 设值 ----------------------------------------------------
    def _index_of(self, domain: int) -> int:
        if not self._values:
            return int(domain)
        best = 0
        gap = abs(self._values[0] - int(domain))
        for index, item in enumerate(self._values):
            if abs(item - int(domain)) < gap:
                best, gap = index, abs(item - int(domain))
        return best

    def _position_to_domain(self, position: int) -> int:
        ratio = max(0.0, min(1.0, position / self.STEPS))
        if self._values:
            index = int(round(ratio * (len(self._values) - 1)))
            return int(self._values[index])
        span = self._maximum - self._minimum
        return int(round(self._minimum + ratio * span))

    def _domain_to_position(self, domain: int) -> int:
        if self._values:
            best, gap = 0, None
            for index, item in enumerate(self._values):
                delta = abs(int(item) - int(domain))
                if gap is None or delta < gap:
                    best, gap = index, delta
            if len(self._values) <= 1:
                return 0
            return int(round(best / (len(self._values) - 1) * self.STEPS))
        span = self._maximum - self._minimum
        if span <= 0:
            return 0
        ratio = (max(self._minimum, min(self._maximum, int(domain))) - self._minimum) / span
        return int(round(ratio * self.STEPS))

    def value(self) -> int:
        if self._lossless:
            return 0
        return self._position_to_domain(self.slider.value())

    def setValue(self, value: int) -> None:
        position = self._domain_to_position(int(value))
        if position != self.slider.value():
            self.slider.setValue(position)

    def _refresh(self, position: int, *, emit: bool = True) -> None:
        if self._lossless:
            self.readout.setText("—")
            return
        domain = self._position_to_domain(position)
        self.readout.setText(self._text(domain))
        if emit:
            self.changed.emit(int(domain))
            if not self.slider.isSliderDown():
                self._settle_timer.start()

    def _emit_settled(self, *_):
        if not self._lossless:
            self.settled.emit(int(self._position_to_domain(self.slider.value())))

    def set_measure(self, measure: str) -> None:
        self.measure = measure
        self._refresh(self.slider.value(), emit=False)

    def set_lossless(self, lossless: bool) -> None:
        self._lossless = bool(lossless)
        self.slider.setVisible(not lossless)
        self.ends.setVisible(not lossless)
        self.note.setText(self._lossless_hint if lossless else self._plain_hint)
        self._refresh(self.slider.value(), emit=False)

    def set_hint_visible(self, visible: bool) -> None:
        if self.note is not None:
            self.note.setVisible(visible)

    def set_compact(self, compact: bool) -> None:
        if self.note is not None:
            self.note.setVisible(True)
        self.ends.setVisible(not self._lossless)

    def _text(self, value: int) -> str:
        if self.unit == "kbps":
            return f"{value} kbps"
        if self.unit == "%":
            return f"{value}%"
        return f"CRF {value}"
