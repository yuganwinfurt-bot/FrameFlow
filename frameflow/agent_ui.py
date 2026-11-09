"""Qt widgets for the Agent workspace. The composer holds queue references only."""

from dataclasses import dataclass
import json
import math

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QSizePolicy, QTextBrowser,
    QTextEdit, QVBoxLayout, QWidget,
)

from .icons import icon
from .motion import Button, QUEUED_MEDIA, QUEUED_MEDIA_BATCH, queued_media_key
from .theme import C


@dataclass(frozen=True)
class Attachment:
    key: str
    name: str
    detail: str
    picture: QPixmap


def text_label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class AttachmentCard(QFrame):
    def __init__(self, item, remove=None):
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
        if remove:
            button = Button("")
            button.setFixedSize(26, 26)
            button.setIcon(icon("close", 14))
            button.setAccessibleName(f"移除附件 {item.name}")
            button.clicked.connect(remove)
            row.addWidget(button)


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
        else:
            self.setPlainText(text)

    def loadResource(self, kind, url):
        # Model Markdown can refer to images and other resources. Never resolve them.
        return None

    def fit(self, width):
        self.document().setTextWidth(width)
        self.setFixedHeight(max(self.fontMetrics().height(), math.ceil(self.document().size().height()) + 2))


class Bubble(QFrame):
    def __init__(self, text, user, attachments):
        super().__init__()
        self.user = user
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
        self.preferred = max([52, 340 if attachments else 0] + [
            self.fontMetrics().horizontalAdvance(line) + 27 for line in text.splitlines()
        ])

    def fit(self, width):
        cap = max(52, int((width - 30) * .75))
        actual = min(cap, self.preferred)
        self.setFixedWidth(actual)
        if self.content:
            self.content.fit(actual - 26)


class Composer(QTextEdit):
    send_requested = Signal()

    def __init__(self):
        super().__init__()
        self.composing = False
        self.setAcceptRichText(False)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.setTabChangesFocus(True)
        self.setPlaceholderText("输入消息，或从左侧队列拖入媒体…")
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().documentLayout().documentSizeChanged.connect(self.fit)
        self.fit()

    def fit(self, *_):
        height = max(42, min(132, math.ceil(self.document().size().height()) + 6))
        if self.height() != height:
            self.setFixedHeight(height)

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
    directory_requested = Signal()

    def __init__(self, lookup):
        super().__init__()
        self.lookup = lookup
        self.pending = ()
        self.pending_signature = ()
        self.bubbles = []
        self.busy = False
        self.setObjectName("agentWorkspace")
        self.setAcceptDrops(True)
        self.setStyleSheet(f"""
            QFrame#agentWorkspace {{ background: {C.surface.name()}; border: 1px solid {C.border.name()};
                                    border-radius: 12px; }}
            QFrame#agentWorkspace[dragging="true"] {{ border: 2px solid {C.accent.name()}; }}
            QFrame#agentUserBubble {{ background: {C.accent_soft.name()}; border-radius: 12px; }}
            QFrame#agentReplyBubble, QFrame#agentAttachment {{ background: {C.sunken.name()}; border-radius: 9px; }}
            QTextBrowser#agentMessageText {{ background: transparent; border: 0; padding: 0; }}
            QFrame#agentComposer {{ background: {C.surface.name()}; border: 1px solid {C.border.name()}; border-radius: 12px; }}
            QFrame#agentComposer QTextEdit {{ background: transparent; border: 0; }}
            QScrollArea#agentHistory, QScrollArea#agentPending {{ background: transparent; border: 0; }}
            QScrollArea#agentHistory QWidget#agentHistoryBody, QWidget#agentPendingBody {{ background: transparent; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)
        heading = QHBoxLayout()
        badge = QLabel()
        badge.setPixmap(icon("robot", 23).pixmap(23, 23))
        heading.addWidget(badge)
        title = text_label("AI 助手")
        title.setObjectName("brand")
        heading.addWidget(title)
        heading.addStretch()
        self.directory_button = Button("选择输出目录")
        self.directory_button.clicked.connect(self.directory_requested)
        heading.addWidget(self.directory_button)
        clear = Button("清空会话")
        clear.clicked.connect(self.clear_requested)
        heading.addWidget(clear)
        self.api_button = Button("API 设置")
        self.api_button.setIcon(icon("gear", 15))
        self.api_button.clicked.connect(self.settings_requested)
        heading.addWidget(self.api_button)
        layout.addLayout(heading)

        self.history = QScrollArea()
        self.history.setObjectName("agentHistory")
        self.history.setWidgetResizable(True)
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history.viewport().installEventFilter(self)
        body = QWidget()
        body.setObjectName("agentHistoryBody")
        self.messages = QVBoxLayout(body)
        self.messages.setContentsMargins(7, 5, 7, 5)
        self.messages.setSpacing(9)
        self.welcome = text_label("拖入媒体或直接提问。转换方案会在这里等待你的确认。")
        self.welcome.setProperty("muted", True)
        self.welcome.setWordWrap(True)
        self.messages.addWidget(self.welcome)
        self.messages.addStretch()
        self.history.setWidget(body)
        layout.addWidget(self.history, 1)

        self.activity = text_label("")
        self.activity.setWordWrap(True)
        self.activity.setProperty("muted", True)
        self.activity.hide()
        layout.addWidget(self.activity)
        panel = QFrame()
        panel.setObjectName("agentComposer")
        composer = QVBoxLayout(panel)
        composer.setContentsMargins(8, 5, 8, 7)
        composer.setSpacing(4)
        self.pending_area = QScrollArea()
        self.pending_area.setObjectName("agentPending")
        self.pending_area.setWidgetResizable(True)
        self.pending_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pending_body = QWidget()
        pending_body.setObjectName("agentPendingBody")
        self.pending_layout = QVBoxLayout(pending_body)
        self.pending_layout.setContentsMargins(1, 1, 1, 1)
        self.pending_layout.setSpacing(3)
        self.pending_area.setWidget(pending_body)
        self.pending_area.hide()
        composer.addWidget(self.pending_area)
        self.input = Composer()
        self.input.textChanged.connect(self._update_send)
        self.input.send_requested.connect(self.send)
        composer.addWidget(self.input)
        actions = QHBoxLayout()
        attach = Button("添加附件")
        attach.setIcon(icon("plus", 15))
        attach.clicked.connect(self.attachments_requested)
        actions.addWidget(attach)
        actions.addStretch()
        privacy = text_label("文字和查询到的元数据会发送给模型，媒体文件在本地处理")
        privacy.setProperty("muted", True)
        actions.addWidget(privacy)
        self.stop = Button("停止生成")
        self.stop.clicked.connect(self.stop_requested)
        self.stop.hide()
        actions.addWidget(self.stop)
        self.send_button = Button("发送")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.send)
        actions.addWidget(self.send_button)
        composer.addLayout(actions)
        layout.addWidget(panel)
        self._update_send()

    def _update_send(self):
        self.send_button.setEnabled(not self.busy and bool(self.pending or self.input.toPlainText().strip()))

    def set_busy(self, busy):
        self.busy = busy
        self.stop.setVisible(busy)
        self.api_button.setEnabled(not busy)
        self._update_send()

    def set_activity(self, text):
        self.activity.setText(text)
        self.activity.setVisible(bool(text))

    def add_attachments(self, keys):
        self.pending = tuple(dict.fromkeys((*self.pending, *(key for key in keys if self.lookup(key)))))
        self.refresh_attachments()

    def remove_attachment(self, key):
        self.pending = tuple(item for item in self.pending if item != key)
        self.refresh_attachments()

    def refresh_attachments(self):
        items = tuple(item for key in self.pending if (item := self.lookup(key)))
        self.pending = tuple(item.key for item in items)
        signature = tuple((item.key, item.name, item.detail, item.picture.cacheKey()) for item in items)
        if signature != self.pending_signature:
            self.pending_signature = signature
            while self.pending_layout.count():
                self.pending_layout.takeAt(0).widget().deleteLater()
            for item in items:
                self.pending_layout.addWidget(AttachmentCard(item, lambda _, key=item.key: self.remove_attachment(key)))
            self.pending_area.setFixedHeight(min(122, self.pending_layout.sizeHint().height() + 2))
            self.pending_area.setVisible(bool(items))
        self._update_send()

    def send(self):
        self.refresh_attachments()
        if not self.send_button.isEnabled():
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
        bubble.fit(self.history.viewport().width())
        return bubble

    def add_history_widget(self, widget, *, right=False):
        bar = self.history.verticalScrollBar()
        follow = bar.maximum() - bar.value() < 16
        self.welcome.hide()
        self.messages.insertWidget(self.messages.count() - 1, widget, alignment=(
            Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft))
        if follow:
            QTimer.singleShot(0, self, lambda: bar.setValue(bar.maximum()))

    def clear_history(self):
        while self.messages.count() > 2:
            widget = self.messages.takeAt(1).widget()
            widget.hide()
            widget.deleteLater()
        self.bubbles.clear()
        self.welcome.show()

    def eventFilter(self, obj, event):
        if obj is self.history.viewport() and event.type() == QEvent.Type.Resize:
            for bubble in self.bubbles:
                bubble.fit(event.size().width())
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
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

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
