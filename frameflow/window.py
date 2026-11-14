"""帧转的 PySide6 桌面窗口。"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from os.path import isdir
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import (QEvent, QItemSelectionModel, QModelIndex, QObject, QProcess,
                            QSignalBlocker, QSize, Qt, QUrl, Signal, Slot)
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from .agent import AgentSession
from .conversion_batch import BatchTask, inspect_media
from .conversion_run import ConversionRun
from .batch_settings import AUDIO_FORMATS, IMAGE_FORMATS, VIDEO_FORMATS, BatchSettings, formats_for
from .conversion_inspector import ConversionInspector
from .agent_tools import AgentTools
from .agent_ui import AgentPage, Attachment
from .media import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from .media import ImageInfo, VideoInfo
from .icons import icon as line_icon
from .media import (Export, MediaInfo, Processes, Tools,
                    hardware_acceleration_choices)
from .media import filename_problem, media_path_missing
from .details import DetailsDialog
from .motion import (Button, Combo, DropArea, Motion,
                     NavigationBar, PageTransition, hide_vertical_scroll_bar,
                     install_parameter_wheel_policy, local_files)
from .editor import Editor
from .preview_footer import PreviewFooter
from .media_text import file_size, metadata, rail_meta
from .queue_state import Job, QueueState
from .queue_view import (ROW_HEIGHT, THUMB_SIZE, BatchFormatMenu, MediaQueue, QueueColumns,
                         QueueDelegate, Row, queue_thumbnail)
from .theme import STYLE, app_icon, brand_mark, shadow
from .widgets import ElidedLabel, label

RAIL_MIN_WIDTH = 232
CONVERSION_FOOTER_HEIGHT = 65
PREVIEW_FOOTER_HEIGHT = 56

class Events(QObject):
    inspected = Signal(str, object, str)
    thumb_ready = Signal(str, bytes)
    phase = Signal(str, str)
    progress = Signal(str, int)
    exported = Signal(str, object, object, str, str)
    completed = Signal(bool)


def card() -> tuple[QFrame, QVBoxLayout]:
    """白底细边框的圆角卡片，主窗口两块内容区都用它。"""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    return frame, layout


def divider() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: #E5EDF6; border: none;")
    return line


def validate_filename(stem: str, source) -> str:
    """返回错误提示，空串表示可用。"""
    if problem := filename_problem(stem):
        return problem
    target = source.with_name(stem + source.suffix)
    if target.exists():
        return "该文件夹已存在同名文件，请使用其他名称。"
    return ""


class RenameDialog(QDialog):
    def __init__(self, stem: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("重命名文件")
        self.setModal(True)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel("文件名"))
        self.field = QLineEdit(stem)
        self.field.selectAll()
        self.field.setMinimumHeight(30)
        layout.addWidget(self.field)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = Button("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        confirm = Button("确定")
        confirm.setObjectName("primary")
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)

    def value(self) -> str:
        return self.field.text()


class MainWindow(QMainWindow):
    def __init__(self, tools: Tools | None, tool_error: str = "") -> None:
        super().__init__()
        install_parameter_wheel_policy()
        self.tools = tools
        self.closing = False
        self.queue = QueueState()
        self.jobs = self.queue.jobs
        self.rows: dict[str, Row] = {}
        self.items: dict[str, QListWidgetItem] = {}
        self.preview_items: dict[str, QListWidgetItem] = {}
        self.editors: dict[str, Editor] = {}
        self.mounted_editor: Editor | None = None
        self.details_dialog: DetailsDialog | None = None
        self.inspections = Processes()
        self.reader_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="frameflow-read")
        self.events = Events(self)
        self.batch = ConversionRun(self.jobs, tools, self.events)
        self.events.inspected.connect(self._inspected)
        self.events.thumb_ready.connect(self._show_thumbnail)
        self.events.phase.connect(self._phase)
        self.events.progress.connect(self._progress)
        self.events.exported.connect(self._exported)
        self.events.completed.connect(self._completed)
        self.setWindowTitle("帧转 FrameFlow · 媒体格式转换器")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(940, 720)
        self.resize(1510, 930)
        self.setAcceptDrops(True)
        self._build()
        self._install_queue_menus()
        if tool_error:
            self.status.setText(tool_error)
            self.status.setWordWrap(True)
        self._refresh_controls()

    def _build(self) -> None:
        base = QWidget()
        base.setObjectName("shell")
        self.setCentralWidget(base)
        self.setStyleSheet(STYLE)
        self._build_menu()
        root = QVBoxLayout(base)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        self.page_host = QWidget()
        self.page_host.setObjectName("pageWorkspace")
        self.page_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self._build_preview_rail()
        self.agent_page = AgentPage(self._agent_attachment)
        self.agent_page.attachments_requested.connect(self._choose_agent_attachments)
        self.agent_tools = AgentTools(self, self.agent_page, formats_for, self.events, parent=self)
        self.agent_session = AgentSession(self.agent_page, self, tools=self.agent_tools)
        self.pages = QStackedWidget()
        self.pages.setObjectName("pageWorkspace")
        self.pages.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.preview_undo = QShortcut(QKeySequence(QKeySequence.StandardKey.Undo), self.page_host)
        self.preview_undo.setContext(Qt.ShortcutContext.WindowShortcut)
        self.preview_undo.setEnabled(False)
        self.preview_undo.activated.connect(self._undo_preview_edit)
        self.pages.currentChanged.connect(self._update_preview_undo)
        root.addWidget(self.page_host, 1)
        self.conversion_page = QWidget()
        self.pages.addWidget(self.conversion_page)
        self.empty_preview = label("请先在格式转换页选择一个媒体文件。", muted=True)
        self.empty_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pages.addWidget(self.empty_preview)
        self._build_conversion_queue()
        self._build_workspace_transition()
        self._build_footers()
        root.addWidget(self.page_footers)

    def _build_conversion_queue(self):
        queue, queue_layout = card()
        self.queue_card = queue
        shadow(queue)
        header_row, self.count, self.add_button = self._add_queue_header(queue_layout)
        self.queue_tools = QWidget()
        queue_tools = QHBoxLayout(self.queue_tools)
        queue_tools.setContentsMargins(0, 0, 0, 0)
        queue_tools.setSpacing(8)
        queue_tools.addWidget(label("批量格式", muted=True))
        self.batch_format = Combo()
        self.batch_format.setObjectName("batchFormat")
        self.batch_format.setAccessibleName("批量格式")
        self.batch_format.setFixedWidth(150)
        self.batch_format.addItem("选择输出格式…")
        self.batch_format.setFixedWidth(190)
        for group_title, group in (("图片", IMAGE_FORMATS), ("视频", VIDEO_FORMATS),
                                   ("音频", AUDIO_FORMATS)):
            self.batch_format.insertSeparator(self.batch_format.count())
            self.batch_format.addItem(f"— {group_title} —")
            header_index = self.batch_format.count() - 1
            item = self.batch_format.model().item(header_index)
            if item is not None:
                item.setEnabled(False)
            for key, title in group:
                self.batch_format.addItem(title, key)
        self.batch_format.activated.connect(self._apply_batch_format)
        self.batch_format.hide()
        queue_tools.addWidget(self.batch_format)
        self.batch_button = Button("选择输出格式…")
        self.batch_button.setFixedWidth(190)
        self.batch_button.clicked.connect(self._show_batch_menu)
        queue_tools.addWidget(self.batch_button)
        self.batch_settings_button = Button("批量参数")
        self.batch_settings_button.clicked.connect(self._show_batch_settings)
        queue_tools.addWidget(self.batch_settings_button)
        header_row.addWidget(self.queue_tools)

        self.column_header = QueueColumns()
        self.column_header.hide()
        queue_layout.addWidget(self.column_header)

        self.drop_area = DropArea()
        self.drop_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.drop_area.clicked.connect(self.choose_files)
        self.drop_area.files_dropped.connect(self.add_paths)
        queue_layout.addWidget(self.drop_area, 1)

        self.list = MediaQueue(self._queued_media, self._can_reorder, self._reorder_media,
                               column_header=self.column_header)
        self.list.setObjectName("queue")
        self.list.setAlternatingRowColors(False)
        self.list.setUniformItemSizes(True)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(self.list)
        self.list.currentItemChanged.connect(self._selection_changed)
        self.remove_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.list)
        self.remove_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.remove_shortcut.activated.connect(self._remove_selected)
        queue_layout.addWidget(self.list, 1)
        self.clear_button = self._add_queue_footer(queue_layout)
        self.list.installEventFilter(self)
        self.preview_files.installEventFilter(self)

    def _build_workspace_transition(self):
        self.inspector = ConversionInspector(show_details=self._show_details, edit=self._edit_selected,
                                             queue=self._queue_selected, show_error=self._show_error,
                                             locate=self._locate_output)
        self.inspector.setFixedWidth(self._settings_width())
        self.batch_settings = BatchSettings(self, self.tools.ffmpeg if self.tools else None, self._selected)
        self.batch_settings.name_edited.connect(self._output_stem_edited)
        self.batch_settings.quality_changed.connect(self._quality_changed)
        self.batch_settings.directory_changed.connect(self._batch_directory_changed)
        self.page_transition = PageTransition(
            self.page_host, self.queue_card, self.preview_rail, self.inspector, self.pages,
            self._rail_width(), self.agent_page)
        self.page_transition.queue_columns_progress.connect(self.column_header.set_detail)
        self.page_transition.queue_columns_progress.connect(self.rail_column_header.set_detail)
        self.page_transition.settings_location_changed.connect(self._move_page_settings)
        self.page_transition.settled.connect(self._settle_page)

    def _build_footers(self):
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedSize(150, 6)
        self.cancel_button = Button("取消转换")
        self.cancel_button.clicked.connect(self.cancel_export)
        self.start_button = Button("开始转换")
        self.start_button.setObjectName("primary")
        self.start_button.setMinimumWidth(112)
        self.start_button.setIcon(line_icon("play", 15))
        self.start_button.setIconSize(QSize(15, 15))
        self.start_button.clicked.connect(self.start_export)
        self.status = label("添加媒体文件，选择输出格式后即可转换。", muted=True)
        self.status.setObjectName("status")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        footer = QFrame()
        footer.setObjectName("footer")
        self.footer = footer
        footer_row = QHBoxLayout(footer)
        footer_row.setContentsMargins(16, 8, 16, 10)
        footer_row.setSpacing(12)
        footer_row.addWidget(self.status, 1)
        footer_row.addWidget(self.progress)
        footer_row.addWidget(self.cancel_button)
        footer_row.addWidget(self.start_button)
        self.page_footers = QStackedWidget()
        self.page_footers.addWidget(footer)
        self.preview_footer = PreviewFooter(self.start_export, self._cancel_preview_task)
        self.page_footers.addWidget(self.preview_footer)
        self.agent_footer = QFrame()
        self.agent_footer.setObjectName("footer")
        agent_footer_layout = QHBoxLayout(self.agent_footer)
        agent_footer_layout.setContentsMargins(16, 0, 16, 0)
        agent_footer_layout.addWidget(label("FrameFlow  ·  媒体在本地处理", muted=True))
        self.page_footers.addWidget(self.agent_footer)
        self.page_footers.setFixedHeight(CONVERSION_FOOTER_HEIGHT)
        self.page_transition.animation.valueChanged.connect(self._resize_page_footer)
        self.page_transition.navigation_position.connect(self.navigation.set_position)

    def _build_menu(self) -> None:
        """不显示菜单栏，但快捷键仍然挂在窗口上。"""
        open_action = QAction("添加文件…", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.choose_files)
        self.addAction(open_action)
        quit_action = QAction("退出", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(28, 11, 28, 11)
        row.setSpacing(10)
        brand = QWidget()
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)
        mark = QLabel()
        mark.setPixmap(brand_mark(40))
        mark.setFixedSize(40, 40)
        brand_row.addWidget(mark)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(0)
        name = label("帧转 FrameFlow")
        name.setObjectName("brand")
        subtitle = label("视频 · 音频 · 图片格式转换", muted=True)
        subtitle.setObjectName("caption")
        titles.addWidget(name)
        titles.addWidget(subtitle)
        brand_row.addLayout(titles)
        brand_row.addStretch()
        row.addWidget(brand, 1)
        self.navigation = NavigationBar()
        self.navigation.addTab("格式转换")
        self.navigation.addTab("媒体预览")
        self.navigation.addTab("AI Agent")
        self.navigation.currentChanged.connect(self._navigate)
        row.addWidget(self.navigation)
        # 正常时不占版面，只在找不到 FFmpeg 时冒出来提醒一句
        self.tool_chip = label("找不到 FFmpeg，请检查 bin 目录")
        self.tool_chip.setStyleSheet("font-size: 11px; color: #C42B1C;")
        self.tool_chip.setToolTip("没有找到 ffmpeg/ffprobe，转换和缩略图都无法工作。")
        self.tool_chip.setVisible(self.tools is None)
        status_holder = QWidget()
        status_line = QHBoxLayout(status_holder)
        status_line.setContentsMargins(0, 0, 0, 0)
        status_line.addStretch()
        status_line.addWidget(self.tool_chip)
        row.addWidget(status_holder, 1)
        return header

    def _toggle_details(self) -> None:
        self._show_details()

    def _about(self) -> None:
        QMessageBox.about(
            self, "关于帧转 FrameFlow",
            "<b>帧转 FrameFlow</b><br>本地视频、音频、图片格式转换与轻量编辑。<br><br>"
            "转换由本地 FFmpeg 完成。AI Agent 仅在配置后向指定模型发送对话及相关元数据。")

    @Slot(str, str)
    def _set_format(self, key, kind):
        if self.scope is None and (job := self.jobs.get(key)) and job.queued:
            self._change_job_format(job, kind)
            self._show_selected()
            self._refresh_controls()

    def _show_batch_menu(self) -> None:
        groups = [("图片", IMAGE_FORMATS), ("视频", VIDEO_FORMATS), ("音频", AUDIO_FORMATS)]
        menu = BatchFormatMenu(groups, self)
        menu.picked.connect(self._pick_batch_format)
        origin = self.batch_button.mapToGlobal(self.batch_button.rect().bottomLeft())
        menu.move(origin.x(), origin.y() + 4)
        menu.show()

    def _pick_batch_format(self, key: str) -> None:
        index = self.batch_format.findData(key)
        if index >= 0:
            self.batch_button.setText(self.batch_format.itemText(index))
        self._apply_batch_format(key)

    def _apply_batch_format(self, index):
        kind = index if isinstance(index, str) else self.batch_format.itemData(index)
        if self.scope is not None or not kind:
            return
        applied = 0
        for job in self.jobs.values():
            if not job.queued:
                continue
            combo = self.rows[job.key].format
            match = combo.findData(kind)
            if match >= 0:
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(match)
                self._change_job_format(job, kind)
                applied += 1
        self.batch_format.setCurrentIndex(0)
        self.status.setText(f"已设置 {applied} 个兼容文件的输出格式。" if applied else "队列中没有兼容此格式的文件。")
        self._show_selected()
        self._refresh_controls()

    def _add_queue_header(self, panel):
        frame = QFrame()
        frame.setObjectName("panelHeader")
        frame.setFixedHeight(44)
        header = QHBoxLayout(frame)
        header.setContentsMargins(9, 0, 9, 0)
        header.setSpacing(5)
        title = label("媒体队列")
        title.setObjectName("queueTitle")
        header.addWidget(title)
        count = ElidedLabel("0 个文件")
        count.setObjectName("queueCount")
        count.setProperty("muted", True)
        count.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        count.setFixedWidth(76)
        header.addWidget(count)
        add = Button("添加")
        add.setIcon(line_icon("plus", 15))
        add.setIconSize(QSize(15, 15))
        add.setFixedSize(64, 30)
        add.setToolTip("添加视频、音频或图片（Ctrl+O）")
        add.clicked.connect(self.choose_files)
        header.addWidget(add)
        header.addStretch(1)
        panel.addWidget(frame)
        return header, count, add

    def _add_queue_footer(self, panel):
        footer = QFrame()
        footer.setObjectName("queueFooter")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(12, 6, 12, 12)
        clear = Button("清空列表")
        clear.setIcon(line_icon("trash", 16))
        clear.setIconSize(QSize(16, 16))
        clear.setToolTip("移除队列里的全部文件")
        clear.clicked.connect(self._clear_jobs)
        clear.setMinimumHeight(36)
        actions.addWidget(clear)
        panel.addWidget(footer)
        return clear

    def _build_preview_rail(self):
        self.preview_rail, rail = card()
        shadow(self.preview_rail)
        self.preview_rail.hide()
        _, self.rail_count, self.rail_add = self._add_queue_header(rail)
        self.rail_column_header = QueueColumns()
        self.rail_column_header.hide()
        rail.addWidget(self.rail_column_header)

        self.preview_files = MediaQueue(
            self._queued_media, self._can_reorder, self._reorder_media, narrow=True,
            timeline_drag=lambda: (self.navigation.currentIndex() == 1
                                   and isinstance(page := self.pages.currentWidget(), Editor)
                                   and page.timeline.isVisible()),
        )
        self.preview_files.setObjectName("previewFiles")
        self.preview_files.setAccessibleName("媒体预览文件")
        self.preview_files.setToolTip("单击切换文件。拖到时间线或 AI Agent；在 Agent 中可按 Ctrl 多选。")
        self.preview_files.setIconSize(THUMB_SIZE)
        self.preview_files.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(self.preview_files)
        self.preview_files.currentItemChanged.connect(self._preview_file_changed)
        rail.addWidget(self.preview_files, 1)

        self.rail_clear = self._add_queue_footer(rail)

    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Show and widget in (self.list, self.preview_files):
            previous = self.preview_files if widget is self.list else self.list
            offset = previous.verticalScrollBar().value()
            widget.doItemsLayout()
            widget.verticalScrollBar().setValue(offset)
        return super().eventFilter(widget, event)

    def _update_preview_item(self, job):
        item = self.preview_items[job.key]
        item.setIcon(QIcon(queue_thumbnail(job)))
        item.setData(QueueDelegate.META_ROLE, rail_meta(job))
        item.setToolTip(f"{job.path.name}\n{rail_meta(job)}")
        self.agent_page.refresh_attachments()

    def _preview_file_changed(self, current, _previous):
        if current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if key in self.items:
            self.list.setCurrentItem(self.items[key])

    def _can_reorder(self):
        return self.scope is None and not self.closing

    def _reorder_media(self, key, slot):
        if not self._can_reorder() or key not in self.jobs or not 0 <= slot < len(self.jobs):
            return False
        source = self.list.row(self.items[key])
        if source == slot:
            return True
        destination = slot + int(slot > source)
        root = QModelIndex()
        with QSignalBlocker(self.list), QSignalBlocker(self.preview_files):
            wide, rail = self.list.model(), self.preview_files.model()
            if not wide.moveRow(root, source, root, destination):
                return False
            if not rail.moveRow(root, source, root, destination):
                wide.moveRow(root, slot, root, source + int(source > slot))
                return False
        self.queue.move(key, slot)
        return True

    def _agent_attachment(self, key):
        job = self.jobs.get(key)
        if job and not media_path_missing(job.path):
            return Attachment(key, job.path.name, rail_meta(job), queue_thumbnail(job), preview=job.picture)
        return None

    def _choose_agent_attachments(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("从媒体队列添加附件")
        dialog.resize(500, 290)
        layout = QVBoxLayout(dialog)
        choices = QListWidget()
        choices.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        hide_vertical_scroll_bar(choices)
        for key, job in self.jobs.items():
            item = QListWidgetItem(QIcon(queue_thumbnail(job)), job.path.name, choices)
            item.setData(Qt.ItemDataRole.UserRole, key)
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("添加")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.agent_page.add_attachments(item.data(Qt.ItemDataRole.UserRole) for item in choices.selectedItems())
        dialog.deleteLater()

    def _queued_media(self, key):
        job = self.jobs.get(key)
        if job and job.info and job.path.suffix.lower() != ".pdf":
            return job.path, job.info, job.picture
        return None

    def choose_files(self) -> None:
        if self.scope is not None or self.tools is None:
            return
        media = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self, "添加媒体文件", "",
                                                f"视频、音频和图片 ({media});;所有文件 (*.*)")
        self.add_paths(paths)

    def dragEnterEvent(self, event) -> None:
        if self.tools and self.scope is None and local_files(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = local_files(event.mimeData())
        if self.tools and self.scope is None and paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    def add_paths(self, paths, *, generated=False) -> None:
        if self.tools is None or self.scope is not None:
            return
        seen = {job.path for job in self.jobs.values()}
        added = skipped = 0
        for value in paths:
            try:
                path = Path(value).resolve()
            except OSError:
                skipped += 1
                continue
            if path in seen or not path.is_file():
                skipped += 1
                continue
            seen.add(path)
            key = uuid4().hex
            job = Job(key, path, queued=not generated)
            if generated:
                job.output = path
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(str(path))
            item.setSizeHint(QSize(0, ROW_HEIGHT))
            row = Row(job)
            row.remove_requested.connect(self._remove_job)
            row.format_changed.connect(self._set_format)
            row.set_formats(job.output_format, ())
            self.queue.add(job)
            self.items[key], self.rows[key] = item, row
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            preview_item = QListWidgetItem(path.name)
            preview_item.setData(Qt.ItemDataRole.UserRole, key)
            preview_item.setToolTip(str(path))
            preview_item.setSizeHint(QSize(0, ROW_HEIGHT))
            self.preview_items[key] = preview_item
            self.preview_files.addItem(preview_item)
            self._update_preview_item(job)
            if generated and path.suffix.lower() == ".pdf":
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                job.info = ImageInfo(0, 0, "PDF", size)
                job.state = "成品"
                row.update_job(job)
                self._update_preview_item(job)
            else:
                self.reader_pool.submit(
                    inspect_media, key, path, self.tools, self.inspections,
                    inspected=self.events.inspected.emit, thumb_ready=self.events.thumb_ready.emit,
                )
            added += 1
        if added and self.list.currentItem() is None:
            self.list.setCurrentRow(0)
        if added or skipped:
            self.status.setText(f"已添加 {added} 个文件" + (f"，跳过 {skipped} 个重复项或非文件" if skipped else "。"))
        self._refresh_controls()

    @Slot(str, object, str)
    def _inspected(self, key: str, info: MediaInfo | None, error: str) -> None:
        if job := self.jobs.get(key):
            if info is None and media_path_missing(job.path) and self._prune_missing():
                return
            job.info, job.detail = info, error
            job.state = ("就绪" if job.queued else "成品") if info else "读取失败"
            choices = formats_for(info)
            if not job.output_format and choices:
                job.output_format = choices[0][0]
            self.rows[key].set_formats(job.output_format, choices)
            self._update_preview_item(job)
            self._update_job(job)
            if self.navigation.currentIndex() == 1 and self._selected() is job and info:
                self._edit_selected()

    @Slot(str, bytes)
    def _show_thumbnail(self, key: str, data: bytes) -> None:
        if job := self.jobs.get(key):
            pixmap = QPixmap()
            if data and pixmap.loadFromData(data):
                job.picture = pixmap
                editor = self.editors.get(key)
                if editor and editor.transport is not None:
                    editor.transport.offer_thumbnail(pixmap)
                self._update_preview_item(job)
            self._update_job(job)

    def _selected(self) -> Job | None:
        item = self.list.currentItem()
        return self.jobs.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _selection_changed(self, current=None, previous=None) -> None:
        for item, amount in ((previous, 0), (current, 1)):
            if item and (row := self.rows.get(item.data(Qt.ItemDataRole.UserRole))):
                row.selection_motion.move_to(amount)
        selected = self._selected()
        with QSignalBlocker(self.preview_files):
            item = self.preview_items.get(selected.key) if selected else None
            if self.preview_files.batch_drag:
                self.preview_files.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
            else:
                self.preview_files.setCurrentItem(item)
        self.agent_page.refresh_attachments()
        self._show_selected()
        self._refresh_controls()
        if self.navigation.currentIndex() == 1:
            self._show_page(self._editor_for(selected) or self.empty_preview)

    def _output_stem_edited(self, text: str) -> None:
        if job := self._selected():
            self._set_output_stem(job, text)

    def _set_output_stem(self, job, text: str) -> None:
        job.output_stem = text
        if editor := self.editors.get(job.key):
            if editor.settings.output_stem_edit.text() != text:
                editor.settings.output_stem_edit.setText(text)
            editor.settings.update_output_location()
        if self._selected() is job:
            if self.batch_settings.output_stem_edit.text() != text:
                self.batch_settings.output_stem_edit.setText(text)
            self.batch_settings.refresh_name(job)

    def _show_selected(self) -> None:
        job = self._selected()
        self.batch_settings.show_selection(job)
        self.inspector.show_source(job)
        if not self.page_transition.settings_in_preview:
            self._mount_selected_settings(job)

    def _details_extra(self, job):
        if not (job.output and job.output != job.path and job.output_info):
            return []
        rows = [
            ("文件名", job.output.name),
            ("位置", str(job.output.parent)),
            ("文件大小", file_size(job.output_info.size)),
            ("参数", metadata(job.output_info)),
        ]
        if job.output_note:
            rows.append(("说明", job.output_note))
        return [("输出成品", rows)]

    def _show_details(self) -> None:
        job = self._selected()
        if job is None or not job.info:
            return
        if self.details_dialog is None:
            self.details_dialog = DetailsDialog(self)
        self.details_dialog.show_media(job.path, job.info, self._details_extra(job), job.picture)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "inspector"):
            width = self._settings_width()
            self.inspector.setFixedWidth(width)
            for editor in self.editors.values():
                editor.inspector.setFixedWidth(width)
                editor.settings.refresh_density()
            self.page_transition.rail_width = self._rail_width()
            self.page_transition.invalidate()
        if hasattr(self, "page_transition"):
            self._show_selected()

    def _update_job(self, job: Job) -> None:
        self.rows[job.key].update_job(job)
        if self._selected() is job:
            self._show_selected()
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        busy = self.scope is not None
        selected = self._selected()
        availability = self.queue.availability()
        self._refresh_queue_controls(busy=busy, can_configure=availability.can_configure)
        self.batch_settings.update_availability(availability.formats, availability.video_formats,
                                                busy=busy, has_selection=selected is not None)
        self.start_button.setEnabled(not busy and not availability.reading and availability.can_configure)
        self.cancel_button.setEnabled(busy and not self.scope.cancelled.is_set())
        self.inspector.refresh_actions(selected, busy=busy)
        if self.mounted_editor is not None:
            self.mounted_editor.settings.setEnabled(not busy and selected is not None and selected.queued)

    def _refresh_queue_controls(self, *, busy, can_configure):
        has_files = bool(self.jobs)
        self.count.set_full_text(f"{len(self.jobs)} 个文件")
        self.rail_count.set_full_text(self.count.full_text)
        for button in (self.add_button, self.rail_add, self.drop_area):
            button.setEnabled(not busy and self.tools is not None)
        for clear in (self.clear_button, self.rail_clear):
            clear.parentWidget().setVisible(has_files)
            clear.setEnabled(has_files and not busy)
        for widget in (self.queue_tools, self.column_header, self.rail_column_header, self.list):
            widget.setVisible(has_files)
        self.drop_area.setVisible(not has_files)
        self.drop_area.set_compact(has_files)
        for row in self.rows.values():
            row.set_batch_running(busy)
        self.batch_format.setEnabled(not busy and can_configure)
        self.batch_settings_button.setEnabled(not busy and can_configure)

    def _queue_selected(self) -> None:
        if self.scope is None and (job := self._selected()) and job.info and not job.queued and job.path.suffix.lower() != ".pdf":
            job.queued = True
            job.state = "就绪"
            self._update_job(job)

    def _sync_footer(self, page) -> None:
        preview = page is not self.conversion_page
        self.page_footers.setCurrentWidget(self.preview_footer if preview else self.footer)
        self.preview_footer.show_task(page.export_status() if isinstance(page, Editor) else None)

    def _refresh_preview_footer(self, editor):
        if self.navigation.currentIndex() == 1 and self.pages.currentWidget() is editor:
            self._sync_footer(editor)

    def _cancel_preview_task(self):
        if isinstance(editor := self.pages.currentWidget(), Editor):
            editor.cancel_operation()

    def _settings_width(self):
        return max(306, min(445, round(self.width() * .255)))

    def _rail_width(self):
        return max(RAIL_MIN_WIDTH, min(320, round(self.width() * .185)))

    def _editor_for(self, job):
        if not self.tools or not job or not job.info or job.path.suffix.lower() == ".pdf":
            return None
        if editor := self.editors.get(job.key):
            return editor
        editor = Editor(job.path, job.info, self.tools, self.batch_settings.output_directory, self.pages,
                        embedded=True, lookup_media=self._queued_media,
                        report_missing=self._report_missing_media, defer_media=True,
                        get_output_stem=lambda: job.output_stem)
        editor.set_output_format(job.output_format)
        if job.output_format:
            editor.apply_output_options(self._job_export(job), apply_image_quality=job.export_settings is not None)
        if editor.transport is not None and job.picture is not None:
            editor.transport.offer_thumbnail(job.picture)
        editor.saved.connect(self._register_editor_output)
        editor.return_requested.connect(self._show_conversion)
        editor.task_changed.connect(lambda: self._refresh_preview_footer(editor))
        editor.settings_changed.connect(lambda: self._store_editor_settings(job, editor))
        editor.directory_changed.connect(lambda directory: self._store_directory(job, directory))
        self._attach_output_stem(editor, job)
        self.editors[job.key] = editor
        self.pages.addWidget(editor)
        return editor

    def _mount_selected_settings(self, job):
        editor = self._editor_for(job)
        if self.mounted_editor is editor:
            return
        self._unmount_settings()
        if editor is not None:
            editor.mount_settings(self.inspector.shared_settings)
            editor.settings.refresh_density()
            if hasattr(editor.settings, "quality"):
                # 以编辑器当前值为准，避免切页时把用户刚调好的质量打回默认
                self.batch_settings.quality.setValue(editor.settings.quality.value())
            editor.settings.setEnabled(self.scope is None and job.queued)
            self.mounted_editor = editor

    def _attach_output_stem(self, editor, job) -> None:
        editor.settings.bind_output_name(job.output_stem or job.path.stem,
                                         lambda text: self._set_output_stem(job, text))

    def _quality_changed(self, value: int) -> None:
        """转换页的滑块与预览页的滑块共用一份质量参数。"""
        editor = self.mounted_editor
        if editor is not None and hasattr(editor.settings, "quality"):
            if editor.settings.quality.value() != int(value):
                editor.settings.quality.setValue(int(value))

    def _unmount_settings(self):
        if editor := self.mounted_editor:
            self.mounted_editor = None
            editor.mount_settings()
            editor.settings.refresh_density()
            editor.settings.setEnabled(True)

    def _change_job_format(self, job, kind):
        job.output_format = kind
        if job.export_settings is not None:
            job.export_settings = replace(job.export_settings, format=kind)
        if editor := self.editors.get(job.key):
            editor.set_output_format(kind)

    def _store_editor_settings(self, job, editor):
        if self.jobs.get(job.key) is not job:
            return
        options = editor.output_options()
        if not options.format or self.rows[job.key].format.findData(options.format) < 0:
            return
        job.export_settings = options
        job.output_format = options.format
        combo = self.rows[job.key].format
        with QSignalBlocker(combo):
            combo.setCurrentIndex(combo.findData(options.format))

    def _store_directory(self, job, directory):
        if self.jobs.get(job.key) is job and job.output_format:
            job.export_settings = replace(self._job_export(job), directory=directory)

    def _edit_selected(self):
        job = self._selected()
        if job and media_path_missing(job.path) and self._prune_missing():
            return
        self._show_page(self._editor_for(job) or self.empty_preview)

    def _navigate(self, index):
        if index == 0:
            self._show_conversion()
        elif index == 1:
            self._edit_selected()
        else:
            self._show_agent()

    def _show_agent(self):
        with QSignalBlocker(self.navigation):
            self.navigation.setCurrentIndex(2)
        self.preview_files.set_batch_drag(True)
        self.agent_page.refresh_attachments()
        self.page_footers.setCurrentWidget(self.agent_footer)
        self._update_preview_undo()
        self.page_transition.move_to(True, agent=True)

    def _restore_page_focus(self):
        if not self.isVisible() or self.isMinimized():
            return
        index = self.navigation.currentIndex()
        target = (self.list, self.preview_files, self.agent_page.input)[index]
        if index == 2 and self.agent_page.isAncestorOf(self.focusWidget()):
            return
        if target.isVisible() and target.isEnabled():
            self.activateWindow()
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_conversion(self):
        self._show_page(self.conversion_page)

    def _update_preview_undo(self):
        self.preview_undo.setEnabled(
            self.navigation.currentIndex() == 1 and isinstance(self.pages.currentWidget(), Editor))

    def _undo_preview_edit(self):
        page = self.pages.currentWidget()
        if isinstance(page, Editor):
            page.undo()

    def _resize_page_footer(self, position):
        height = CONVERSION_FOOTER_HEIGHT + (PREVIEW_FOOTER_HEIGHT - CONVERSION_FOOTER_HEIGHT) * position
        self.page_footers.setFixedHeight(round(height))

    def _move_page_settings(self, in_preview):
        if in_preview:
            self._unmount_settings()
        else:
            self._show_selected()

    def _settle_page(self):
        if not self.page_transition.target_preview:
            if isinstance(current := self.pages.currentWidget(), Editor):
                current.suspend()
            self.pages.setCurrentWidget(self.conversion_page)
        elif self.page_transition.target_agent and isinstance(current := self.pages.currentWidget(), Editor):
            current.suspend()
        self._restore_page_focus()

    def _show_page(self, page):
        preview = page is not self.conversion_page
        current = self.pages.currentWidget()
        with QSignalBlocker(self.navigation):
            self.navigation.setCurrentIndex(int(preview))
        returning_from_agent = self.page_transition.target_agent
        self.preview_files.set_batch_drag(False)
        if preview and page is not current:
            if isinstance(current, Editor):
                current.suspend()
            if isinstance(page, Editor):
                page.inspector.setFixedWidth(self._settings_width())
                page.settings.refresh_density()
            self.pages.setCurrentWidget(page)
            if isinstance(page, Editor):
                page.start_media()
                page.resume()
        elif preview and returning_from_agent and isinstance(page, Editor):
            page.resume()
        self._sync_footer(page)
        self._update_preview_undo()
        self.page_transition.move_to(preview)

    @Slot(object)
    def _register_editor_output(self, path: Path) -> None:
        self.add_paths([path], generated=True)

    def _remove_selected(self) -> None:
        if job := self._selected():
            self._remove_job(job.key)

    @Slot(object)
    def _report_missing_media(self, path):
        if any(job.path == path for job in self.jobs.values()):
            self._prune_missing()

    def _prune_missing(self):
        if self.closing or self.scope is not None:
            return False
        missing = [job for job in self.jobs.values() if media_path_missing(job.path)]
        if not missing:
            return False
        selected = self._selected()
        active = self.pages.currentWidget()
        replace_preview = self.navigation.currentIndex() == 1 and any(
            job is selected or active is self.editors.get(job.key) for job in missing)
        names = "\n".join(job.path.name for job in missing[:8])
        if len(missing) > 8:
            names += f"\n另有 {len(missing) - 8} 个文件"
        notice = QMessageBox(QMessageBox.Icon.Warning, "找不到媒体文件",
                             f"在原路径找不到以下文件，已自动从队列移除。\n\n{names}",
                             QMessageBox.StandardButton.Ok, self)
        notice.setTextFormat(Qt.TextFormat.PlainText)
        notice.button(QMessageBox.StandardButton.Ok).setText("确定")
        folders = tuple(dict.fromkeys(job.path.parent for job in missing))
        locate = notice.addButton("打开原位置", QMessageBox.ButtonRole.ActionRole)
        locate.clicked.connect(lambda: self._open_original_folders(folders))
        notice.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        notice.open()
        for job in missing:
            self._remove_job(job.key)
        if replace_preview:
            self._edit_selected()
        self.status.setText(f"已移除 {len(missing)} 个找不到的文件。")
        return True

    def _open_original_folders(self, folders: tuple[Path, ...]) -> None:
        opened = set()
        failed = False
        opened_parent = False
        for original in folders:
            folder = next((path for path in (original, *original.parents) if isdir(path)), None)
            if folder is None:
                failed = True
                continue
            if folder not in opened:
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                    failed = True
                    continue
                opened.add(folder)
            opened_parent |= folder != original
        if failed:
            self.status.setText("有原位置无法打开，请检查磁盘是否可用。")
        elif opened_parent:
            self.status.setText("原文件夹已不存在，已打开最近的可用上级目录。")

    @Slot(str)
    # ---------------------------------------------------------------- 队列右键
    def _install_queue_menus(self) -> None:
        for widget in (self.list, self.preview_files):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget: self._queue_menu(w, pos))

    def _key_at(self, widget, pos):
        item = widget.itemAt(pos)
        if item is None:
            return None
        key = item.data(Qt.ItemDataRole.UserRole)
        return key if key in self.jobs else None

    def _queue_menu(self, widget, pos) -> None:
        key = self._key_at(widget, pos)
        if key is None:
            return
        menu = QMenu(widget)
        menu.setObjectName("queueMenu")
        remove = menu.addAction(line_icon("close", 16), "从队列移除")
        locate = menu.addAction(line_icon("folder", 16), "定位文件")
        rename = menu.addAction(line_icon("file", 16), "重命名")
        chosen = menu.exec(widget.viewport().mapToGlobal(pos))
        if chosen is remove:
            self._remove_from_queue(key)
        elif chosen is locate:
            self._locate_job_file(key)
        elif chosen is rename:
            self._rename_job(key)

    def _remove_from_queue(self, key: str) -> None:
        row = self.list.row(self.items[key])
        self._remove_job(key)
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))
        self._refresh_controls()

    def _locate_job_file(self, key: str) -> None:
        job = self.jobs.get(key)
        if job is None:
            return
        if not job.path.exists():
            self.status.setText("文件不存在或位置已发生变化")
            return
        target = job.output if job.output and job.output.is_file() else job.path
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target.parent)))

    def _auto_output_for(self, job) -> bool:
        """当前成品名如果就是按源文件名自动生成的，则跟随重命名。"""
        if not job.output:
            return False
        return job.output.stem.startswith(job.path.stem)

    def _rename_job(self, key: str) -> None:
        job = self.jobs.get(key)
        if job is None:
            return
        if job.state == "转换中":
            self.status.setText("文件正在转换，暂时不能重命名")
            return
        dialog = RenameDialog(job.path.stem, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        stem = dialog.value().strip()
        problem = validate_filename(stem, job.path)
        if problem:
            self.status.setText(problem)
            return
        target = job.path.with_name(stem + job.path.suffix)
        follows = self._auto_output_for(job)
        try:
            if target.exists():
                raise FileExistsError
            job.path.rename(target)
        except FileExistsError:
            self.status.setText("该文件夹已存在同名文件，请使用其他名称。")
            return
        except OSError as error:
            self.status.setText(f"重命名失败：{error}")
            return

        job.path = target
        if follows:
            job.output = None
            job.output_info = None
            job.output_note = ""
        item = self.items.get(key)
        if item is not None:
            item.setText(target.name)
        row = self.rows.get(key)
        if row is not None:
            row.update_job(job)
        self._update_preview_item(job)
        self._selection_changed()
        self.status.setText(f"已重命名为 {target.name}")

    def _remove_job(self, key: str) -> None:
        if self.scope is not None or key not in self.jobs:
            return
        editor = self.editors.pop(key, None)
        if editor is not None:
            if self.pages.currentWidget() is editor:
                self.pages.setCurrentWidget(self.empty_preview)
            if editor is self.mounted_editor:
                self._unmount_settings()
            editor.shutdown()
            self.pages.removeWidget(editor)
            editor.deleteLater()
        item = self.items.pop(key)
        self.rows.pop(key)
        self.queue.remove(key)
        with QSignalBlocker(self.preview_files):
            preview_item = self.preview_items.pop(key)
            self.preview_files.takeItem(self.preview_files.row(preview_item))
        self.list.takeItem(self.list.row(item))
        self._selection_changed()

    def _clear_jobs(self):
        if self.scope is not None or not self.jobs:
            return
        self.inspections.cancel()
        self.inspections = Processes()
        self._unmount_settings()
        if self.page_transition.target_preview:
            self.pages.setCurrentWidget(self.empty_preview)
        for editor in self.editors.values():
            editor.shutdown()
            self.pages.removeWidget(editor)
            editor.deleteLater()
        self.editors.clear()
        self.preview_items.clear()
        self.items.clear()
        self.rows.clear()
        self.queue.clear()
        with QSignalBlocker(self.preview_files), QSignalBlocker(self.list):
            self.preview_files.clear()
            self.list.clear()
        self._selection_changed()
        self.progress.setValue(0)
        self.status.setText("已清空列表，磁盘上的文件已保留。")

    def _batch_directory_changed(self):
        self._show_selected()
        self._refresh_controls()

    def _show_error(self) -> None:
        if job := self._selected():
            box = QMessageBox(QMessageBox.Icon.Warning, "处理失败",
                              "请检查文件是否损坏，或输出目录是否可写。", parent=self)
            box.setDetailedText(job.detail)
            box.exec()

    def _open_output(self) -> None:
        self._show_output(locate=False)

    def _locate_output(self) -> None:
        self._show_output(locate=True)

    def _show_output(self, *, locate: bool) -> None:
        job = self._selected()
        if job is None:
            self.status.setText("先在队列里选一个文件。")
            return
        path = job.output if job.output and job.output.is_file() else job.path
        if path is None or not path.exists():
            self.status.setText("文件已移走或删除。")
            return
        if locate and sys.platform == "win32":
            explorer = QProcess()
            explorer.setProgram("explorer.exe")
            explorer.setNativeArguments(f'/select,"{path.absolute()}"')
            started = explorer.startDetached()
            if isinstance(started, tuple):
                started = started[0]
            if started:
                return
        target = path.parent if locate else path
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self.status.setText("无法打开成品，请检查系统默认应用。")

    def _job_export(self, job):
        options = job.export_settings or self.batch_settings.options_for(job)
        return replace(options, format=job.output_format, output_stem=job.output_stem)

    def agent_job(self, key: str) -> Job | None:
        return self.jobs.get(key)

    def agent_jobs(self) -> tuple[Job, ...]:
        return tuple(self.jobs.values())

    def agent_selected_job(self) -> Job | None:
        return self._selected()

    def agent_select_job(self, key: str) -> None:
        self.list.setCurrentItem(self.items[key])
        self.preview_files.setCurrentItem(self.preview_items[key])

    def agent_has_export_tools(self) -> bool:
        return self.tools is not None

    def agent_batch_running(self) -> bool:
        return self.scope is not None

    def agent_export_options(self, job: Job) -> Export:
        return self._job_export(job)

    def agent_acceleration_keys(self, format_name: str) -> set[str]:
        return {key for key, _ in hardware_acceleration_choices(self.tools.ffmpeg, format_name)}

    def agent_start_batch(self, tasks: list[BatchTask]) -> object | None:
        if self._start_batch(tasks):
            return self.scope
        return None

    def agent_cancel_batch(self, scope: object) -> bool:
        if self.scope is None or self.scope is not scope:
            return False
        self.cancel_export()
        return True

    def agent_batch_status(self) -> tuple[int, tuple[Job, ...]] | None:
        if self.scope is None:
            return None
        return self.progress.value(), self.batch.active_jobs()

    def agent_apply_export_options(self, job: Job, options: Export) -> None:
        job.export_settings = options
        job.output_format = options.format
        combo = self.rows[job.key].format
        with QSignalBlocker(combo):
            combo.setCurrentIndex(combo.findData(options.format))
        if editor := self.editors.get(job.key):
            editor.apply_output_options(options, apply_image_quality=True)
        if job.output_stem != options.output_stem:
            self._set_output_stem(job, options.output_stem)
        self._show_selected()
        self._refresh_controls()

    def _show_batch_settings(self):
        if self.batch_settings.edit_defaults():
            self._apply_batch_settings()
        else:
            self._show_selected()

    def _apply_batch_settings(self):
        for job in self.jobs.values():
            if not (job.queued and job.output_format):
                continue
            batch = self.batch_settings.options_for(job)
            current = self._job_export(job)
            options = replace(current, directory=batch.directory, max_edge=batch.max_edge,
                              target_bytes=batch.target_bytes, video_acceleration=batch.video_acceleration,
                              output_fps=batch.output_fps, small_gif=batch.small_gif,
                              quality=batch.quality if isinstance(job.info, VideoInfo) else current.quality)
            job.export_settings = options
            if editor := self.editors.get(job.key):
                editor.apply_output_options(options)
        self.status.setText("已将批量参数应用到队列。")

    def start_export(self) -> None:
        page = self.pages.currentWidget()
        if self.navigation.currentIndex() == 2:
            return
        if self.navigation.currentIndex() == 1:
            if isinstance(page, Editor):
                page.export_current_page()
            return
        if self.scope is not None or self.tools is None or self.queue.availability().reading:
            return
        self._prune_missing()
        tasks = [(job.key, job.path, job.info, self._job_export(job))
                 for job in self.jobs.values() if job.queued and job.info and job.output_format]
        self._start_batch(tasks)

    def _start_batch(self, tasks) -> bool:
        if not tasks or self.closing or self.tools is None or self.scope is not None:
            return False
        self.batch.begin(tasks)
        for key in self.batch.keys:
            self.rows[key].update_job(self.jobs[key])
        self._show_selected()
        self.progress.setValue(0)
        self.status.setText(f"正在转换 {len(tasks)} 个文件…")
        self._refresh_controls()
        self.batch.submit(tasks)
        return True

    @property
    def scope(self):
        return self.batch.scope

    @Slot(str, str)
    def _phase(self, key: str, state: str) -> None:
        if job := self.batch.set_phase(key, state):
            self.rows[key].update_job(job)

    @Slot(str, int)
    def _progress(self, key: str, percent: int) -> None:
        if changed := self.batch.set_progress(key, percent):
            job, overall = changed
            self.rows[key].update_job(job)
            self.progress.setValue(overall)

    @Slot(str, object, object, str, str)
    def _exported(self, key: str, output: Path | None, output_info: MediaInfo | None,
                  note: str, error: str) -> None:
        job, overall = self.batch.record_result(key, output, output_info, note, error)
        self.progress.setValue(overall)
        self._update_job(job)

    @Slot(bool)
    def _completed(self, cancelled: bool) -> None:
        changed, outputs, completed, failed = self.batch.finish(cancelled)
        for job in changed:
            self.rows[job.key].update_job(job)
        if outputs:
            self.add_paths(outputs, generated=True)
        self.status.setText(f"{'已取消 · ' if cancelled else ''}完成 {completed} 个，失败 {failed} 个。")
        self._refresh_controls()
        self._prune_missing()

    def cancel_export(self) -> None:
        if self.scope is not None:
            self.batch.cancel()
            self.status.setText("正在停止，已完成的文件会保留。")
            self._install_queue_menus()
        self._refresh_controls()

    def closeEvent(self, event) -> None:
        self.closing = True
        self.agent_session.shutdown()
        self.page_transition.animation.stop()
        self.page_transition.agent_animation.stop()
        for editor in self.editors.values():
            editor.shutdown()
        self.editors.clear()
        for animation in self.findChildren(Motion):
            animation.stop()
        self.inspections.cancel()
        self.batch.cancel()
        self.reader_pool.shutdown(wait=True, cancel_futures=True)
        self.batch.shutdown()
        super().closeEvent(event)
