"""Qt widgets for the Agent workspace. The composer holds queue references only."""

from dataclasses import dataclass
import json
import math

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QPixmap, QTextCursor, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QMenu, QProgressBar, QScrollArea, QSizePolicy,
    QStackedWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from .icons import icon
from .motion import Button, QUEUED_MEDIA, QUEUED_MEDIA_BATCH, hide_vertical_scroll_bar, queued_media_key
from .theme import C

CHAT_WIDTH = 880
EXAMPLES = (
    ("video", "将视频转换为 H.265"),
    ("sliders", "把视频压缩到 20 MB"),
    ("wave", "提取视频中的音频"),
    ("layers", "批量转换队列中的文件"),
)


@dataclass(frozen=True)
class Attachment:
    key: str
    name: str
    detail: str
    picture: QPixmap
    preview: QPixmap | None = None


def text_label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


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


class MessageText(QTextBrowser):
    def __init__(self, text, markdown):
        super().__init__()
        self.setObjectName("agentMessageText")
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(0)
        if markdown:
            self.document().setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownNoHTML |
                                       QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
            block = self.document().begin()
            while block.isValid():
                style = block.blockFormat()
                if style.nonBreakableLines():
                    style.setNonBreakableLines(False)
                    QTextCursor(block).setBlockFormat(style)
                block = block.next()
        else:
            self.setPlainText(text)

    def loadResource(self, kind, url):
        # Model Markdown can refer to images and other resources. Never resolve them.
        return None

    def preferred_width(self):
        self.ensurePolished()
        document = self.document()
        wrapped_width = document.textWidth()
        # Prevent the browser from reapplying its current viewport width during measurement.
        with QSignalBlocker(document.documentLayout()):
            document.setTextWidth(-1)
            text_width = math.ceil(document.idealWidth())
            document.setTextWidth(wrapped_width)
        return text_width + self.width() - self.maximumViewportSize().width()

    def fit(self, width):
        viewport = self.maximumViewportSize()
        self.document().setTextWidth(max(1, width - (self.width() - viewport.width())))
        height = max(self.fontMetrics().height(), math.ceil(self.document().size().height()))
        self.setFixedHeight(height + self.height() - viewport.height())


class Bubble(QFrame):
    def __init__(self, text, user, attachments):
        super().__init__()
        self.user = user
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setObjectName("agentUserBubble" if user else "agentReplyBubble")
        self.setAccessibleName("用户消息" if user else "AI 回复")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(5)
        for item in attachments:
            layout.addWidget(AttachmentCard(item))
        self.content = MessageText(text, not user) if text else None
        if self.content:
            layout.addWidget(self.content)
        self.preferred_minimum = 340 if attachments else 52

    def fit(self, width):
        self.ensurePolished()
        cap = max(52, int((width - 30) * .75))
        preferred = self.preferred_minimum
        if self.content:
            margins = self.layout().contentsMargins()
            inset = self.width() - self.contentsRect().width() + margins.left() + margins.right()
            preferred = max(preferred, self.content.preferred_width() + inset)
        actual = min(cap, preferred)
        self.setFixedWidth(actual)
        if self.content:
            self.content.fit(actual - inset)


class Composer(QTextEdit):
    send_requested = Signal()
    MIN_HEIGHT = 42
    MAX_HEIGHT = 132

    def __init__(self):
        super().__init__()
        self.composing = False
        self.preferred_height = self.MIN_HEIGHT
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMaximumHeight(self.MAX_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAcceptRichText(False)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.setTabChangesFocus(True)
        hide_vertical_scroll_bar(self)
        self.setPlaceholderText("输入消息，或从左侧队列拖入媒体…")
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_document)

    def sizeHint(self):
        hint = super().sizeHint()
        hint.setHeight(self.preferred_height)
        return hint

    def _fit_document(self, size):
        height = max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, math.ceil(size.height()) + 6))
        if height != self.preferred_height:
            self.preferred_height = height
            # Preserve short drafts in full, and keep at least four lines for a long draft in a short window.
            self.setMinimumHeight(min(height, self.fontMetrics().lineSpacing() * 4 + 10))
            self.updateGeometry()

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier and not self.composing):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AgentPage(QFrame):
    submitted = Signal(str, object)
    settings_requested = Signal()
    attachments_requested = Signal()
    stop_requested = Signal()
    clear_requested = Signal()
    new_requested = Signal()
    directory_requested = Signal()

    def __init__(self, lookup):
        super().__init__()
        self.lookup = lookup
        self.pending = ()
        self.bubbles = []
        self.follow_history = True
        self.busy = False
        self.before_submit = lambda: True
        self.setObjectName("agentWorkspace")
        self.setAcceptDrops(True)
        self.setStyleSheet(f"""
            QFrame#agentWorkspace {{ background: {C.surface_alt.name()}; border: 1px solid {C.border.name()};
                                    border-radius: 12px; }}
            QLabel#agentBadge {{ background: {C.accent_soft.name()}; border-radius: 12px; }}
            QLabel#agentBrand {{ font-size: 20px; font-weight: 700; }}
            QLabel#agentWelcomeTitle {{ font-size: 24px; font-weight: 700; }}
            QLabel#agentModel {{ font-weight: 600; }}
            QFrame#agentModelBadge {{ background: {C.sunken.name()}; border-radius: 8px; }}
            QFrame#agentUserBubble {{ background: {C.accent_soft.name()}; border: 1px solid {C.accent_line.name()};
                                      border-radius: 12px; }}
            QFrame#agentReplyBubble, QFrame#agentPlan {{
                background: {C.surface.name()}; border: 1px solid {C.border_soft.name()}; border-radius: 12px; }}
            QFrame#agentAttachment {{ background: {C.sunken.name()}; border-radius: 8px; }}
            QFrame#agentPendingCard {{ background: {C.surface_alt.name()}; border: 1px solid {C.border.name()};
                                      border-radius: 9px; }}
            QFrame#agentPendingCard:hover {{ background: {C.accent_soft.name()}; border-color: {C.accent_line.name()}; }}
            QLabel#agentPendingThumbnail {{ background: {C.sunken.name()}; border-radius: 5px; }}
            QLabel#agentPendingName {{ font-size: 12px; font-weight: 600; }}
            QFrame#agentToolStatus {{ background: {C.sunken.name()}; border-radius: 8px; }}
            QTextBrowser#agentMessageText {{ background: transparent; border: 0; padding: 0; }}
            QFrame#agentComposer {{ background: {C.surface.name()}; border: 1px solid {C.border.name()}; border-radius: 14px; }}
            QFrame#agentComposer[dragging="true"] {{ border-color: {C.accent.name()}; }}
            QFrame#agentComposer QTextEdit {{ background: transparent; border: 0; }}
            QScrollArea#agentHistory, QScrollArea#agentPending {{ background: transparent; border: 0; }}
            QScrollArea#agentHistory QWidget#agentHistoryBody, QWidget#agentPendingBody {{ background: transparent; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 13)
        layout.setSpacing(11)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(self._badge(40, 22))
        title_group = QVBoxLayout()
        title_group.setSpacing(1)
        title = text_label("FrameFlow AI")
        title.setObjectName("agentBrand")
        title_group.addWidget(title)
        title_group.addWidget(self._muted("通过对话处理视频、音频和图片"))
        heading.addLayout(title_group, 1)
        model_badge = QFrame()
        model_badge.setObjectName("agentModelBadge")
        model_layout = QVBoxLayout(model_badge)
        model_layout.setContentsMargins(10, 4, 10, 4)
        model_layout.setSpacing(1)
        self.model_name = text_label("")
        self.model_name.setObjectName("agentModel")
        self.model_status = self._muted("未连接")
        model_layout.addWidget(self.model_name)
        model_layout.addWidget(self.model_status)
        heading.addWidget(model_badge)
        new = Button("新建会话")
        new.setIcon(icon("plus", 15))
        new.clicked.connect(self.new_requested)
        heading.addWidget(new)
        self.api_button = Button("API 设置")
        self.api_button.setIcon(icon("gear", 15))
        self.api_button.clicked.connect(self.settings_requested)
        heading.addWidget(self.api_button)
        more = Button("")
        more.setFixedWidth(34)
        more.setIcon(icon("more", 16))
        more.setAccessibleName("更多会话操作")
        more.setToolTip("更多会话操作")
        menu = QMenu(more)
        menu.addAction(icon("folder", 16), "选择输出目录", self.directory_requested.emit)
        menu.addAction(icon("trash", 16), "清空会话", self.clear_requested.emit)
        more.clicked.connect(lambda: menu.popup(more.mapToGlobal(more.rect().bottomLeft())))
        heading.addWidget(more)
        layout.addLayout(heading)

        self.welcome = self._welcome()
        self.conversation = QStackedWidget()
        self.conversation.addWidget(self.welcome)
        layout.addWidget(self.conversation, 1)
        self.history = QScrollArea()
        self.history.setObjectName("agentHistory")
        self.history.setWidgetResizable(True)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(self.history)
        body = QWidget()
        body.setObjectName("agentHistoryBody")
        center = QHBoxLayout(body)
        center.setContentsMargins(0, 0, 0, 0)
        self.message_column = QWidget()
        self.message_column.setObjectName("agentHistoryBody")
        self.messages = QVBoxLayout(self.message_column)
        self.messages.setContentsMargins(8, 9, 8, 9)
        self.messages.setSpacing(12)
        self.messages.addStretch()
        center.addWidget(self.message_column, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.history.setWidget(body)
        self.history.viewport().installEventFilter(self)
        scroll_bar = self.history.verticalScrollBar()
        scroll_bar.valueChanged.connect(self._history_scrolled)
        scroll_bar.rangeChanged.connect(self._history_range_changed)
        self.conversation.addWidget(self.history)

        self.activity = self._muted("")
        self.activity.setWordWrap(True)
        self.activity.hide()
        footer = QWidget()
        self.footer_layout = QVBoxLayout(footer)
        self.footer_layout.setContentsMargins(0, 0, 0, 0)
        self.footer_layout.setSpacing(6)
        self.footer_layout.addWidget(self.activity)
        panel = self.composer_panel = QFrame()
        panel.setObjectName("agentComposer")
        composer = QVBoxLayout(panel)
        composer.setContentsMargins(11, 7, 11, 9)
        composer.setSpacing(6)
        self.pending_area = PendingAttachments(self.remove_attachment)
        composer.addWidget(self.pending_area)
        self.input = Composer()
        self.input.textChanged.connect(self._update_send)
        self.input.send_requested.connect(self.send)
        composer.addWidget(self.input)
        actions = QHBoxLayout()
        attach = Button("添加附件")
        attach.setIcon(icon("attach", 16))
        attach.clicked.connect(self.attachments_requested)
        actions.addWidget(attach)
        self.directory_button = Button("")
        self.directory_button.setFixedWidth(34)
        self.directory_button.setIcon(icon("folder", 16))
        self.directory_button.setAccessibleName("选择输出目录")
        self.directory_button.setToolTip("选择输出目录")
        self.directory_button.clicked.connect(self.directory_requested)
        actions.addWidget(self.directory_button)
        self.clear_attachments_button = Button("清空附件")
        self.clear_attachments_button.setProperty("flat", True)
        self.clear_attachments_button.setIcon(icon("trash", 15))
        self.clear_attachments_button.clicked.connect(lambda: self._remove_attachments(self.pending))
        self.clear_attachments_button.hide()
        actions.addWidget(self.clear_attachments_button)
        actions.addStretch()
        self.stop = Button("停止生成")
        self.stop.setIcon(icon("stop", 15))
        self.stop.clicked.connect(self.stop_requested)
        self.stop.hide()
        actions.addWidget(self.stop)
        self.send_button = Button("发送")
        self.send_button.setObjectName("primary")
        self.send_button.setIcon(icon("send", 15, "#FFFFFF"))
        self.send_button.setMinimumWidth(72)
        self.send_button.clicked.connect(self.send)
        actions.addWidget(self.send_button)
        composer.addLayout(actions)
        self.footer_layout.addWidget(panel)
        privacy = self._muted("文字和相关媒体信息会发送给模型，文件在本机处理")
        privacy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_layout.addWidget(privacy)
        layout.addWidget(footer)
        self.set_model_status("", "idle")
        self._update_send()

    @staticmethod
    def _muted(text):
        label = text_label(text)
        label.setProperty("muted", True)
        return label

    @staticmethod
    def _badge(size, glyph):
        badge = QLabel()
        badge.setObjectName("agentBadge")
        badge.setFixedSize(size, size)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(icon("spark", glyph, C.accent_text.name()).pixmap(glyph, glyph))
        return badge

    def _welcome(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(11)
        align = Qt.AlignmentFlag.AlignHCenter
        outer.addStretch()
        outer.addWidget(self._badge(52, 26), alignment=align)
        title = text_label("你好，我是 FrameFlow AI")
        title.setObjectName("agentWelcomeTitle")
        outer.addWidget(title, alignment=align)
        outer.addWidget(self._muted("拖入媒体文件，或者直接告诉我你想如何处理。"), alignment=align)
        examples = QWidget()
        grid = QGridLayout(examples)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setSpacing(8)
        self.example_buttons = []
        for index, (glyph, prompt) in enumerate(EXAMPLES):
            button = Button(prompt)
            button.setIcon(icon(glyph, 16, C.accent_text.name()))
            button.setMinimumSize(184, 38)
            button.clicked.connect(lambda _, text=prompt: self._fill_example(text))
            grid.addWidget(button, index // 2, index % 2)
            self.example_buttons.append(button)
        outer.addWidget(examples, alignment=align)
        outer.addStretch()
        return page

    def _fill_example(self, text):
        self.input.setPlainText(text)
        self.input.moveCursor(QTextCursor.MoveOperation.End)
        self.input.setFocus()

    def set_model_status(self, model, state):
        model = model or "未配置模型"
        self.model_name.setText(self.model_name.fontMetrics().elidedText(model, Qt.TextElideMode.ElideRight, 140))
        self.model_name.setToolTip(model)
        name, color = {
            "idle": ("未连接", C.text2),
            "testing": ("正在连接", C.accent_text),
            "ok": ("已连接", C.success),
            "error": ("连接失败", C.danger),
        }[state]
        self.model_status.setText(name)
        self.model_status.setStyleSheet(f"color: {color.name()};")

    def _update_send(self):
        self.send_button.setEnabled(not self.busy and bool(self.pending or self.input.toPlainText().strip()))

    def set_busy(self, busy):
        self.busy = busy
        self.stop.setVisible(busy)
        self.send_button.setVisible(not busy)
        self.api_button.setEnabled(not busy)
        self._update_send()

    def set_activity(self, text):
        self.activity.setText(text)
        self.activity.setVisible(bool(text))

    def add_attachments(self, keys):
        self.pending = tuple(dict.fromkeys((*self.pending, *(key for key in keys if self.lookup(key)))))
        self.refresh_attachments()

    def remove_attachment(self, key):
        self._remove_attachments((key,))

    def _remove_attachments(self, keys):
        removed = set(keys)
        self.pending = tuple(key for key in self.pending if key not in removed)
        self.refresh_attachments()
        if self.input.isVisible():
            self.input.setFocus(Qt.FocusReason.OtherFocusReason)

    def refresh_attachments(self):
        items = tuple(item for key in self.pending if (item := self.lookup(key)))
        self.pending = tuple(item.key for item in items)
        self.pending_area.set_items(items)
        self.clear_attachments_button.setVisible(len(items) > 1)
        self._update_send()

    def send(self):
        self.refresh_attachments()
        if not self.send_button.isEnabled() or not self.before_submit():
            return
        text = self.input.toPlainText().strip()
        items = tuple(self.lookup(key) for key in self.pending)
        self.add_message(text, user=True, attachments=items)
        self.input.clear()
        self.pending = ()
        self.refresh_attachments()
        self.submitted.emit(text, items)

    def add_message(self, text, *, user=False, attachments=()):
        bubble = Bubble(text, user, attachments)
        self.bubbles.append(bubble)
        self.add_history_widget(bubble, right=user)
        return bubble

    def add_tool_status(self):
        card = ToolStatusCard()
        self.add_history_widget(card)
        return card

    def add_history_widget(self, widget, *, right=False):
        self.conversation.setCurrentWidget(self.history)
        width = min(CHAT_WIDTH, self.history.viewport().width())
        if isinstance(widget, PlanCard):
            widget.fit(width)
        self.messages.insertWidget(self.messages.count() - 1, widget, alignment=(
            Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft))
        if isinstance(widget, Bubble):
            widget.fit(width)

    def _history_scrolled(self, value):
        self.follow_history = self.history.verticalScrollBar().maximum() - value < 12

    def _history_range_changed(self, _minimum, maximum):
        if self.follow_history:
            self.history.verticalScrollBar().setValue(maximum)

    def clear_history(self, keep=None):
        self.follow_history = True
        for index in range(self.messages.count() - 2, -1, -1):
            if self.messages.itemAt(index).widget() is not keep:
                widget = self.messages.takeAt(index).widget()
                widget.hide()
                widget.deleteLater()
        self.bubbles.clear()
        if keep is None:
            self.conversation.setCurrentWidget(self.welcome)

    def resizeEvent(self, event):
        inset = max(0, (event.size().width() - 36 - CHAT_WIDTH) // 2)
        self.footer_layout.setContentsMargins(inset, 0, inset, 0)
        super().resizeEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.history.viewport() and event.type() == QEvent.Type.Resize:
            width = min(CHAT_WIDTH, event.size().width())
            self.message_column.setFixedWidth(width)
            for index in range(self.messages.count() - 1):
                widget = self.messages.itemAt(index).widget()
                if isinstance(widget, (Bubble, PlanCard)):
                    widget.fit(width)
        return super().eventFilter(obj, event)

    def _drag_keys(self, mime):
        if mime.hasFormat(QUEUED_MEDIA_BATCH):
            try:
                keys = json.loads(bytes(mime.data(QUEUED_MEDIA_BATCH)))
            except (ValueError, UnicodeError):
                return ()
            if isinstance(keys, list) and len(keys) <= 100:
                return tuple(key for key in keys if isinstance(key, str) and self.lookup(key))
            return ()
        if mime.hasFormat(QUEUED_MEDIA):
            key = queued_media_key(mime)
            return (key,) if self.lookup(key) else ()
        return ()

    def _drag_style(self, enabled):
        self.setProperty("dragging", enabled)
        panel = self.composer_panel
        panel.setProperty("dragging", enabled)
        panel.style().unpolish(panel)
        panel.style().polish(panel)
        panel.update()

    def dragEnterEvent(self, event):
        if self._drag_keys(event.mimeData()):
            self._drag_style(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drag_style(False)
        event.accept()

    def dropEvent(self, event):
        self._drag_style(False)
        keys = self._drag_keys(event.mimeData())
        if keys:
            self.add_attachments(keys)
            event.acceptProposedAction()
        else:
            event.ignore()


class ToolStatusCard(QFrame):
    def __init__(self):
        super().__init__()
        self.completed = self.failed = 0
        self.setObjectName("agentToolStatus")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        self.badge = QLabel()
        self.summary = text_label("")
        self.summary.setProperty("muted", True)
        row.addWidget(self.badge)
        row.addWidget(self.summary)

    def record(self, completed, failed):
        self.completed += completed
        self.failed += failed
        glyph, color = ("info", C.warning) if self.failed else ("check", C.accent_text)
        self.badge.setPixmap(icon(glyph, 15, color.name()).pixmap(15, 15))
        summary = f"工具执行 · {self.completed} 项成功"
        if self.failed:
            summary += f"，{self.failed} 项失败"
        self.summary.setText(summary)


class PlanCard(QFrame):
    confirm_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, rows):
        super().__init__()
        self.setObjectName("agentPlan")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.rows = {}
        self.paths = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 9, 12, 9)
        self.title = text_label(f"转换计划 · {len(rows)} 个文件 · 保留原文件")
        self.title.setObjectName("fieldLabel")
        outer.addWidget(self.title)
        body = QWidget()
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(0, 0, 5, 0)
        self.content.setSpacing(3)
        for key, text, directory in rows:
            summary = text_label(text)
            summary.setWordWrap(True)
            self.content.addWidget(summary)
            path = text_label("")
            path.setProperty("muted", True)
            path.setToolTip(directory)
            path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.content.addWidget(path)
            self.paths.append((path, directory))
            status = text_label("")
            status.setWordWrap(True)
            status.setProperty("muted", True)
            status.hide()
            self.content.addWidget(status)
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setFixedHeight(6)
            progress.setTextVisible(False)
            progress.hide()
            self.content.addWidget(progress)
            self.rows[key] = status, progress
        self.scroll = QScrollArea()
        self.scroll.setObjectName("agentPending")
        body.setObjectName("agentPendingBody")
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(self.scroll)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll)
        buttons = QHBoxLayout()
        self.confirm = Button("确认执行")
        self.confirm.setObjectName("primary")
        self.confirm.clicked.connect(self.confirm_requested)
        buttons.addWidget(self.confirm)
        self.cancel = Button("取消")
        self.cancel.clicked.connect(self.cancel_requested)
        buttons.addWidget(self.cancel)
        buttons.addStretch()
        outer.addLayout(buttons)

    def fit(self, width):
        self.setFixedWidth(min(650, max(330, int(width * .75))))
        for label, directory in self.paths:
            label.setText(label.fontMetrics().elidedText(directory, Qt.TextElideMode.ElideMiddle, self.width() - 50))
        self._fit_rows()

    def _fit_rows(self):
        self.content.activate()
        height = self.content.heightForWidth(max(300, self.width() - 45))
        self.scroll.setFixedHeight(min(220, max(48, height + 6)))

    def started(self):
        self.title.setText("正在转换…")
        self.confirm.hide()
        self.cancel.setText("取消转换")

    def finished(self, text):
        self.title.setText(text)
        self.confirm.hide()
        self.cancel.hide()

    def update_row(self, key, text, percent):
        label, progress = self.rows[key]
        label.setText(text)
        label.show()
        progress.setValue(percent)
        progress.show()
        self._fit_rows()
