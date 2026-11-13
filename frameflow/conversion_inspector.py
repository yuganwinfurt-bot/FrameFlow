"""Selected-source summary and conversion actions in the right sidebar."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .icons import INK, icon as line_icon, pixmap as line_pixmap
from .media import AudioInfo, WAVEFORM_SECONDS
from .media_text import file_size, metadata
from .motion import Button, hide_vertical_scroll_bar
from .queue_view import StatusPill, state_tone
from .theme import shadow
from .widgets import ElidedLabel, fit_pixmap, label


def icon_label(name: str, size: int = 16):
    """占位用的线框图标（不带交互）。"""
    widget = QLabel()
    widget.setObjectName("lineIcon")
    widget.setFixedSize(size, size)

    widget.setPixmap(line_pixmap(name, size, INK))
    return widget


def icon_button(name: str, callback, tooltip: str = "", size: int = 17):
    """只有图标的扁平按钮，尺寸和普通按钮的内边距对齐。"""
    button = Button("")
    button.setProperty("flat", True)
    button.setProperty("iconOnly", True)
    button.setFixedSize(28, 28)
    button.setIcon(line_icon(name, size))
    button.setIconSize(QSize(size, size))
    button.setToolTip(tooltip)
    button.clicked.connect(callback)
    return button


def field(title: str, widget):
    """带小标题的控件单元格：标题在上、控件在下，两列并排时不显得拥挤。"""
    holder = QWidget()
    holder.setObjectName("fieldCell")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    caption = QLabel(title)
    caption.setObjectName("fieldLabel")
    layout.addWidget(caption)
    layout.addWidget(widget)
    holder.field_label = caption
    return holder


def section(text: str) -> QLabel:
    widget = label(text)
    widget.setObjectName("sectionTitle")
    return widget


class ConversionInspector(QFrame):
    def __init__(self, *, show_details, edit, queue, show_error, locate):
        super().__init__()
        right = QVBoxLayout(self)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.setObjectName("conversionInspector")
        shadow(self)
        body = QWidget()
        body.setObjectName("inspectorBody")
        panel_layout = QVBoxLayout(body)
        panel_layout.setContentsMargins(12, 9, 12, 8)
        panel_layout.setSpacing(5)
        scroll = QScrollArea()
        scroll.setObjectName("inspectorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(scroll)
        scroll.setWidget(body)
        body.setMinimumWidth(1)
        scroll.viewport().setAutoFillBackground(False)
        scroll.viewport().setObjectName("exportViewport")
        right.addWidget(scroll, 1)
        self.inspector_scroll = scroll

        # 模块 A：选中文件概览——缩略图、文件名、一行参数，把高度留给下面的操作
        self.preview = label("添加文件后可在此预览")
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(68, 50)
        source_summary = QHBoxLayout()
        source_summary.setSpacing(8)
        source_summary.addWidget(self.preview)
        source_meta = QVBoxLayout()
        source_meta.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.video_name = ElidedLabel("尚未选择文件", mode=Qt.TextElideMode.ElideMiddle)
        self.video_name.setObjectName("inspectorName")
        name_row.addWidget(self.video_name, 1)
        self.inspector_pill = StatusPill()
        self.inspector_pill.hide()
        name_row.addWidget(self.inspector_pill)
        source_meta.addLayout(name_row)
        self.video_info = label("选中队列里的文件后，这里会显示缩略图和媒体参数。", muted=True)
        self.video_info.setObjectName("inspectorMeta")
        self.video_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.video_info.setWordWrap(False)
        source_meta.addWidget(self.video_info)
        source_summary.addLayout(source_meta, 1)
        panel_layout.addLayout(source_summary)
        settings_heading = QHBoxLayout()
        settings_heading.setSpacing(7)
        settings_heading.addWidget(icon_label("gear", 18))
        heading = label("输出设置")
        heading.setObjectName("conversionSettingsHeading")
        settings_heading.addWidget(heading, 1)
        panel_layout.addLayout(settings_heading)
        self.shared_settings = QVBoxLayout()
        self.shared_settings.setContentsMargins(0, 0, 0, 0)
        panel_layout.addLayout(self.shared_settings)

        self.details_button = Button("详情")
        self.details_button.setIcon(line_icon("file", 15))
        self.details_button.setIconSize(QSize(15, 15))
        self.details_button.setToolTip("在新窗口里查看完整路径和全部媒体参数")
        self.details_button.clicked.connect(show_details)
        panel_layout.addStretch()

        # 操作区固定在卡片底部，展开文件详情时不会被顶到滚动视口之外。
        actions = QFrame()
        actions.setObjectName("inspectorActions")
        action_layout = QVBoxLayout(actions)
        action_layout.setContentsMargins(12, 5, 12, 7)
        action_layout.setSpacing(5)

        self.edit_button = Button("编辑 / 预览")
        self.edit_button.setObjectName("primary")
        self.edit_button.clicked.connect(edit)
        self.queue_result_button = Button("加入下一轮转换")
        self.queue_result_button.clicked.connect(queue)
        self.error_button = Button("查看错误详情")
        self.error_button.clicked.connect(show_error)
        self.locate_button = Button("定位文件")
        self.locate_button.clicked.connect(locate)
        action_layout.addWidget(self.queue_result_button)
        action_layout.addWidget(self.error_button)
        output_actions = QHBoxLayout()
        output_actions.setSpacing(5)
        output_actions.addWidget(self.edit_button, 2)
        output_actions.addWidget(self.details_button, 1)
        output_actions.addWidget(self.locate_button, 1)
        action_layout.addLayout(output_actions)
        right.addWidget(actions)

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def show_source(self, job):
        audio = job is not None and isinstance(job.info, AudioInfo)
        self.video_name.set_full_text(job.path.name if job else "尚未选择文件")
        if job and job.picture is not None:
            self.preview.setPixmap(fit_pixmap(job.picture, self.preview.size()))
        else:
            self.preview.setText(("暂无波形" if audio else "暂无缩略图") if job else "添加文件后可在此预览")
        if job and job.info:
            details = [f"{metadata(job.info)}  ·  {file_size(job.info.size)}"]
            if audio and job.info.duration > WAVEFORM_SECONDS:
                details.append(f"波形显示前 {WAVEFORM_SECONDS} 秒")
            if job.output and job.output != job.path:
                details.append(f"输出 {job.output.name}")
                if job.output_info:
                    details.append(f"{metadata(job.output_info)}  ·  {file_size(job.output_info.size)}")
                if job.output_note:
                    details.append(job.output_note)
            self.video_info.setText(" · ".join(details))
        elif job:
            self.video_info.setText("读取失败，请查看错误详情。" if job.detail else "正在读取媒体信息…")
        else:
            self.video_info.setText("在这里查看缩略图、波形和文件信息。")
        if job and job.info:
            self.inspector_pill.set_state(job.state, state_tone(job))
            self.inspector_pill.show()
        else:
            self.inspector_pill.hide()
        self.details_button.setEnabled(bool(job and job.info))
        self.details_button.setToolTip(
            "在新窗口里查看完整路径和全部媒体参数" if job and job.info else "先选择一个文件")

    def refresh_actions(self, selected, *, busy):
        self.locate_button.setEnabled(bool(selected))
        self.error_button.setVisible(bool(selected and selected.detail))
        editable = bool(selected and selected.info and selected.path.suffix.lower() != ".pdf")
        self.edit_button.setVisible(editable)
        self.edit_button.setEnabled(not busy)
        self.queue_result_button.setVisible(editable and not selected.queued)
        self.queue_result_button.setEnabled(not busy)
