"""按媒体类型显示独立的帧、音轨或图片编辑页。"""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from math import ceil, floor
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEvent, QObject, QPoint, QSignalBlocker, QSize, Qt, Signal, Slot
from PySide6.QtGui import QCursor, QIntValidator, QKeySequence, QShortcut
from PySide6.QtWidgets import (QBoxLayout, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter,
                               QTabBar, QTabWidget, QVBoxLayout, QWidget,
                               QWidgetAction)

from .edit_session import EditSession
from .editor_tasks import EditorTasks
from .editing import (Clip, Frames, VideoSource, audio_segment, frame_png, index_frames, length,
                      original_audio, render_edit, save_picture, slice_track, splice)
from .details import DetailsView
from .editor_controls import _button, _icon_button, _icon_label, _line, _spin, _text, _text_button, timecode as _timecode
from .editor_settings import ExportSettings
from .editor_export import EditorExport
from .editor_widgets import (FrameNumberEdit, InfoChip, PictureView, PreviewLabel, RunStatus,
                             SeekSlider, TransportButton, VideoPreview, VolumePopup)
from .filmstrip import Filmstrip
from . import icons
from .media import output_extension
from .media_text import detailed_size as _size_text
from .media import (AUDIO_EXTENSIONS, VIDEO_FRAME_RATES, VIDEO_RESOLUTIONS,
                    AudioInfo, Cancelled, Export, ImageInfo, MediaError, Processes, VideoInfo,
                    hardware_acceleration_choices)
from .media import (_AUDIO_ENCODERS, _ENCODERS, _IMAGE_FORMATS, _save_image, estimate_image_size,
                    media_path_missing, probe, thumbnail)
from .motion import (Button, Combo, DirectoryPathEdit, Motion, QualityRow, SpinBox, WheelSlider,
                     hide_vertical_scroll_bar)
from .playback import Playback
from .theme import STUDIO_STYLE, C, shadow
from .preview_requests import FramePreview, PictureEstimate
from .preview_footer import PreviewStatus
from .video_transport import VideoTransport
from .audio_workspace import AudioWorkspace


_IMAGE_ENCODERS = {"png", "jpg", "jpeg", "webp", "avif", "tiff", "bmp", "gif_image", "ico", "pdf"}

def _rate_text(bits):
    if not bits:
        return "—"
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.2f} Mbps"
    return f"{bits / 1_000:.0f} kbps"


class Editor(QDialog):
    saved = Signal(object)
    missing_source = Signal(object)
    return_requested = Signal()
    task_changed = Signal()
    settings_changed = Signal()
    directory_changed = Signal(object)

    def __init__(self, path, info, tools, directory=None, parent=None, *, embedded=False,
                 lookup_media=None, report_missing=None, defer_media=False, get_output_stem=lambda: ""):
        super().__init__(parent)
        self.embedded = embedded
        self.media_started = False
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.path, self.info, self.tools = path, info, tools
        self._get_output_stem = get_output_stem
        self._initial_directory = directory
        self.lookup_media = lookup_media
        if report_missing is not None:
            self.missing_source.connect(report_missing, Qt.ConnectionType.QueuedConnection)
        self.transport = None
        self.session = EditSession(path, info)
        self.filmstrip = None
        self.video_range = None
        self.video_overrides = {}
        self.video_selection = ()
        self.video_drag_origin = (None, {})
        self.audio_workspace = None
        self.closing = False
        self.frame_preview = FramePreview(
            self, tools, panel=self._frame_preview_panel, is_image_panel=lambda: self._page_kind() == "image",
            can_decode=lambda: self.scope is None and not self._video_is_playing()
            and (not self.embedded or self.isVisible()),
            is_playing=self._video_is_playing, source_info=lambda path: self.session.video_sources[path].info,
            report_error=self._preview_error, missing_source=self.missing_source.emit,
        ) if isinstance(info, VideoInfo) else None
        self.scratch = tempfile.TemporaryDirectory(prefix="frameflow-preview-")
        self.tasks = EditorTasks(self)
        self.tasks.completed.connect(self._finished)
        self.tasks.progress.connect(self._progress)
        self.setWindowTitle(f"编辑与预览 · {path.name}")
        self.resize(1010, 740)
        self._build()
        self.exporter = EditorExport(
            self, self.path, self.session, self.settings, self.tools,
            canvas_for=self._canvas_for, run_task=self._run, progress=self.tasks.progress.emit,
            notice=self._notice, worker_notice=self.tasks.notice.emit, saved=self._saved,
            output_stem=self._get_output_stem,
        )
        self.tasks.notice.connect(self._notice)
        if not defer_media:
            self.start_media()

    def start_media(self):
        if self.media_started:
            return
        self.media_started = True
        if isinstance(self.info, VideoInfo):
            self._run("正在读取真实帧数…", self._load_index, self._indexed)
        elif isinstance(self.info, ImageInfo):
            self.picture_estimate.schedule()
            self._run("正在读取图片…", self._load_picture, self._show_image)
        elif self.audio_page is not None:
            self._tab_changed()

    def _build(self):
        self.setObjectName("studio")
        self.setStyleSheet(STUDIO_STYLE)
        base = QVBoxLayout(self)
        base.setContentsMargins(*((0, 0, 0, 0) if self.embedded else (16, 12, 16, 10)))
        base.setSpacing(0)

        self.editor_split = self._splitter(Qt.Orientation.Vertical)
        self.workspace_split = self._splitter(Qt.Orientation.Horizontal)
        self.preview_split = self._splitter(Qt.Orientation.Vertical)
        self.workspace_split.setHandleWidth(0)
        preview = QFrame()
        preview.setObjectName("mediaPreviewPanel")
        shadow(preview, 12, 2, 19)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(11, 9, 11, 10)
        preview_layout.setSpacing(4)
        self.file_title = _text(self.path.name, name="studioTitle")
        self.file_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.file_title.setToolTip(self.path.name)
        preview_layout.addWidget(self.file_title)
        preview_layout.addWidget(self.preview_split, 1)
        self.workspace_split.addWidget(preview)
        self.editor_split.addWidget(self.workspace_split)
        base.addWidget(self.editor_split, 1)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mediaTabs")
        self.tabs.tabBar().setObjectName("mediaTabBar")
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.setIconSize(QSize(18, 18))
        self.preview_split.addWidget(self.tabs)
        self.video_page = self.audio_page = self.image_page = self.details_page = None
        if isinstance(self.info, VideoInfo):
            self._build_video_page()
        if isinstance(self.info, (VideoInfo, AudioInfo)):
            self._build_audio_page()
        if isinstance(self.info, (VideoInfo, ImageInfo)):
            self._build_image_page()
        self._build_details_page()
        inspector_layout = self._build_inspector()
        self._build_timeline()
        self._build_progress(inspector_layout)
        self._initialize_layout_and_settings()

    def _build_video_page(self):
        self.video_page, layout = self._page("视频" if self.embedded else "视频帧", "video")
        layout.setContentsMargins(0, 6, 0, 0)
        self.cursor = _spin()
        self.cursor.setSuffix(" 帧")
        self.cursor.setFixedWidth(88)
        self.cursor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cursor.valueChanged.connect(self._cursor_changed)

        self.transport = VideoTransport(
            self, layout, self.cursor, self.session, self.tools, Path(self.scratch.name) / "timeline.wav",
            ready=self._ready_video, selection=lambda: self.video_selection, before_play=self.stop_audio,
            suspend_seek=self._suspend_seek, playback_stopped=self._playback_stopped,
            frame_played=self._video_played, preview_width=self.preview_split.width,
            notice=self._notice, notice_detail=lambda detail: self.status.setToolTip(detail),
            clear_status=lambda: self.status.clear(), canvas_for=self._canvas_for,
        )

    def _build_audio_page(self):
        self.audio_page, layout = self._page("音频" if self.embedded and self.video_page else "音轨", "wave")
        self.audio_workspace = AudioWorkspace(
            self, layout, self.session, self.tools, Path(self.scratch.name),
            linked_video=self.video_page is not None, run_task=self._run, task_scope=lambda: self.scope,
            is_active=lambda: not self.closing and self.isVisible() and self.tabs.currentWidget() is self.audio_page,
            is_closing=lambda: self.closing, notice=self._notice, status=lambda: self.status,
            report_missing=self.missing_source.emit, can_drop=self._can_drop_audio, drop_media=self._drop_audio,
            edit_command=self._audio_command,
            actions={"delete_marked_audio": self._delete_marked_audio, "copy_audio": self.copy_audio,
                     "cut_audio": self.cut_audio, "delete_audio": self.delete_audio, "paste_audio": self.paste_audio,
                     "insert_audio": self.insert_audio, "undo": self.undo},
            apply_selected_volume=self.set_volume,
        )

    def _build_image_page(self):
        self.image_page, layout = self._page("图片", "image")
        layout.setContentsMargins(0, 6, 0, 0)
        self.image_view = PictureView()
        self.image_view.setStyleSheet("")
        self.image_view.setObjectName("imageCanvas")
        layout.addWidget(self.image_view, 1)
        if self.video_page is not None:
            self.tabs.setTabToolTip(self.tabs.indexOf(self.image_page), "预览最靠前选区的首帧，未选帧时预览视频首帧")

    def _build_details_page(self):
        self.details_page, details_layout = self._page("文件详情", "file")
        details_layout.setContentsMargins(12, 12, 12, 12)
        self.details_view = DetailsView(wide=True)
        self.details_view.setAccessibleName("文件详情")
        self.details_view.setToolTip("查看文件原始路径、格式、大小和媒体参数；字段值可以选中，行尾悬停可单独复制。")
        details_layout.addWidget(self.details_view, 1)
        self._update_file_details()

    def _build_inspector(self):
        inspector = QFrame()
        inspector.setObjectName("studioInspector")
        inspector.setMinimumWidth(306)
        inspector.setMaximumWidth(500)
        shadow(inspector, 12, 2, 19)
        self.inspector = inspector
        side = QVBoxLayout(inspector)
        side.setContentsMargins(12, 9, 12, 12)
        side.setSpacing(6)
        self.media_summary = QFrame()
        self.media_summary.setObjectName("mediaInfoCard")
        if self.video_page is None:
            self.media_summary.hide()
        info = self.info
        description = (f"{info.width} × {info.height}   ·   {info.codec}"
                       if isinstance(info, (VideoInfo, ImageInfo)) else
                       f"{info.sample_rate / 1000:g} kHz   ·   {info.codec}")
        if self.video_page is None:
            side.addWidget(_text(description, muted=True))
        self.settings_scroll = side_scroll = QScrollArea()
        side_scroll.setObjectName("studioScroll")
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.Shape.NoFrame)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(side_scroll)
        side_scroll.viewport().setAutoFillBackground(False)
        side_scroll.viewport().setObjectName("exportViewport")
        side.addWidget(side_scroll, 1)

        # 选帧、剪切、另存都在时间线的右键菜单里，侧栏不再重复摆一份。
        # 这两个输入框只是为了保留区间状态的载体（拖选缩略图时会同步），不参与布局。
        self.video_first, self.video_last = FrameNumberEdit(), FrameNumberEdit()
        for field in (self.video_first, self.video_last):
            field.valueChanged.connect(self._range_fields_changed)
        self._selection_note = ""

        self.settings = ExportSettings(
            self.path, self.info, self.tools, self._initial_directory, side_scroll, side,
            output_stem=self._get_output_stem, page_kind=self._page_kind,
            picture_frame_number=self._picture_frame_number, picture_source=self._picture_source,
            audio_duration=lambda: length(self.session.audio_track()), video_timing=self._video_estimate_timing,
            task_running=lambda: self.scope is not None,
        )
        side_scroll.setWidget(self.settings)
        self.picture_estimate = PictureEstimate(self, self.tools, self.settings, self._picture_source,
                                               source_is_image=isinstance(self.info, ImageInfo))
        self.settings.picture_inputs_changed.connect(self.picture_estimate.schedule)
        self.settings.save_picture_requested.connect(self.save_image)
        self.settings.settings_changed.connect(self.settings_changed)
        self.settings.directory_changed.connect(self.directory_changed)
        self.workspace_split.addWidget(inspector)
        self.workspace_split.setStretchFactor(0, 1)
        self.workspace_split.setSizes([max(420, self.width() - 394), 394])
        return side

    def _build_timeline(self):
        self.timeline = QFrame()
        self.timeline.setObjectName("timelineCard")
        timeline = QVBoxLayout(self.timeline)
        timeline.setContentsMargins(9, 5, 9, 7)
        timeline.setSpacing(6)
        if self.video_page is not None:
            self.timeline_header = QBoxLayout(QBoxLayout.Direction.LeftToRight)
            self.timeline_header.setSpacing(3)
            timeline_title = QHBoxLayout()
            timeline_title.addWidget(_icon_label("timeline", 17, C.accent.name()))
            timeline_title.addWidget(_text("时间轴", name="studioSection"))
            self.timeline_hint = _text("左键拖选 · 右键拖动移动帧", name="studioHint")
            self.timeline_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            self.timeline_hint.setToolTip(self.timeline_hint.text())
            timeline_title.addWidget(self.timeline_hint, 2)
            self.selection_label = _text("", name="studioKicker")
            self.selection_label.setToolTip("拖动或按住 Ctrl 点缩略图选帧；右键可复制、剪切、另存")
            timeline_title.addWidget(self.selection_label, 1)
            self.timeline_header.addLayout(timeline_title, 1)
            locate = QHBoxLayout()
            locate.setSpacing(5)
            self.timeline_zoom = WheelSlider(Qt.Orientation.Horizontal)
            self.timeline_zoom.setObjectName("timelineZoom")
            self.timeline_zoom.setRange(0, 4)
            self.timeline_zoom.setValue(4)
            self.timeline_zoom.setFixedWidth(61)
            self.timeline_zoom.setAccessibleName("时间轴缩放")
            self.timeline_zoom.setToolTip("左侧全片概览，右侧连续逐帧。也可按住 Ctrl 滚轮缩放。")
            locate.addWidget(_text("缩放", name="studioHint"))
            locate.addWidget(self.timeline_zoom)
            locate.addWidget(_text("定位", name="studioSection"))
            self.locate_input = QLineEdit()
            self.locate_input.setObjectName("frameLocate")
            self.locate_input.setPlaceholderText("输入帧号")
            self.locate_input.setAccessibleName("定位帧号")
            self.locate_input.setValidator(QIntValidator(1, 2_147_483_647, self.locate_input))
            self.locate_input.setFixedWidth(86)
            self.locate_input.setEnabled(False)
            self.locate_button = _button("跳转", self._jump_to_frame)
            self.locate_button.setEnabled(False)
            self.locate_input.returnPressed.connect(self._jump_to_frame)
            self.locate_input.textChanged.connect(self._update_locate_button)
            locate.addWidget(self.locate_input)
            locate.addWidget(self.locate_button)
            self.timeline_header.addLayout(locate)
            timeline.addLayout(self.timeline_header)
            self.filmstrip = Filmstrip(self.tools, self._video_drop_preview, parent=self.timeline)
            self.timeline_zoom.valueChanged.connect(self.filmstrip.set_zoom)
            self.filmstrip.zoom_changed.connect(self.timeline_zoom.setValue)
            self.filmstrip.media_dropped.connect(self._drop_video)
            self.filmstrip.frames_moved.connect(self._move_video_ranges)
            self.frame_context_menu = QMenu(self.filmstrip)
            self.filmstrip.frame_selected.connect(self._locate_frame)
            self.filmstrip.frame_clicked.connect(self._click_frame)
            self.filmstrip.frame_dragged.connect(self._drag_select_frames)
            self.filmstrip.context_requested.connect(self._show_frame_context_menu)
            self.filmstrip.edit_command.connect(self._timeline_command)
            timeline.addWidget(self.filmstrip)
        self.preview_split.addWidget(self.timeline)
        if self.video_page is not None:
            self._build_media_summary()
            self.preview_split.addWidget(self.media_summary)
        self.preview_split.setStretchFactor(0, 1)
        self.preview_split.setStretchFactor(1, 0)
        self.preview_split.setStretchFactor(2, 0)

    def _build_progress(self, side):
        self.progress_row = QWidget()
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        progress_layout.addWidget(self.progress, 1)
        self.cancel_button = _button("取消任务", self.cancel_operation)
        self.cancel_button.setEnabled(False)
        progress_layout.addWidget(self.cancel_button)
        self.progress_row.hide()
        # 运行日志不再占一条底栏：默认只留一行提示，进度条只在导出时出现。
        self.status = RunStatus()
        self.status.setReadOnly(True)
        self.status.hide()
        self.notice = _text("", name="editorNotice")
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.notice.hide()
        if self.embedded:
            self.notice.setParent(self)
            self.progress_row.setParent(self)
        else:
            side.addWidget(self.progress_row)
            side.insertWidget(1, self.notice)

    def _initialize_layout_and_settings(self):
        for splitter, tooltip, cursor in (
            (self.workspace_split, "拖动边界调整预览区和右侧设置的宽度", Qt.CursorShape.SplitHCursor),
            (self.preview_split, "拖动边界调整预览区和时间线的高度", Qt.CursorShape.SplitVCursor),
        ):
            handle = splitter.handle(1)
            handle.setToolTip(tooltip)
            handle.setCursor(cursor)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.workspace_split.splitterMoved.connect(self._update_timeline_header)
        self._tab_changed()
        self.preview_split.setSizes([900, 110, 90] if self.embedded else [900, 132, 108])
        self.settings.initialize_options()

    def _build_media_summary(self):
        self.media_summary.setObjectName("mediaInfoCard")
        self.media_summary.setAccessibleName("媒体信息")
        layout = QVBoxLayout(self.media_summary)
        layout.setContentsMargins(0, 0, 0, 5)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("mediaInfoHeader")
        header.setFixedHeight(29)
        title = QHBoxLayout(header)
        title.setContentsMargins(10, 0, 10, 0)
        title.setSpacing(6)
        title.addWidget(_icon_label("info", 18))
        title.addWidget(_text("媒体信息", name="cardTitle"))
        title.addStretch(1)
        layout.addWidget(header)

        info = self.info
        audio_codec = (info.audio_codec if info.audio_stream is not None else None) or "无音轨"
        bitrate = _rate_text(info.bitrate)
        sample_rate = f"{info.sample_rate / 1000:g} kHz" if info.sample_rate else "—"
        rows = (
            ("image", "基本信息",
             (f"{info.width} × {info.height}", f"{info.fps:g} FPS" if info.fps else "—",
              f"{info.duration:.3f} 秒", _size_text(info.size))),
            ("gear", "编码信息",
             (info.codec or "—", bitrate, audio_codec, sample_rate)),
        )
        self.media_info_rows = []
        for mark, label, values in rows:
            row = QHBoxLayout()
            row.setContentsMargins(10, 4, 10, 4)
            row.setSpacing(7)
            icon = _icon_label(mark, 16)
            row.addWidget(icon)
            heading = _text(label, name="mediaInfoHeading")
            heading.setFixedWidth(54)
            row.addWidget(heading)
            for index, value in enumerate(values):
                if index:
                    rule = QFrame()
                    rule.setObjectName("mediaInfoRule")
                    rule.setFrameShape(QFrame.Shape.VLine)
                    rule.setFixedSize(1, 13)
                    row.addWidget(rule)
                chip = InfoChip(value)
                chip.setObjectName("mediaInfoValue")
                chip.setToolTip(value if mark == "image" else
                                f"{('视频编码', '视频码率', '音频编码', '采样率')[index]}：{value}")
                chip.setMinimumWidth(0)
                chip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
                chip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                row.addWidget(chip, 1)
            row.addStretch(0)
            self.media_info_rows.append((row, icon, heading))
            layout.addLayout(row)

    @staticmethod
    def _splitter(orientation):
        splitter = QSplitter(orientation)
        splitter.setHandleWidth(0)
        splitter.setChildrenCollapsible(False)
        return splitter

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "file_title"):
            self.file_title.setText(self.file_title.fontMetrics().elidedText(
                self.path.name, Qt.TextElideMode.ElideMiddle, self.file_title.width()))
        self._update_timeline_header()

    def _update_timeline_header(self, *_):
        compact = self.preview_split.width() < 415
        if self.transport is not None:
            self.transport.fit_controls()
        if hasattr(self, "timeline_header"):
            direction = (QBoxLayout.Direction.TopToBottom if compact
                         else QBoxLayout.Direction.LeftToRight)
            self.timeline_header.setDirection(direction)
            self.timeline_hint.setVisible(not compact)
        tab_bar = self.tabs.tabBar()
        if tab_bar.property("compact") != compact:
            tab_bar.setProperty("compact", compact)
            tab_bar.style().unpolish(tab_bar)
            tab_bar.style().polish(tab_bar)
            tab_bar.updateGeometry()
        if getattr(self, "_compact_media_info", None) != compact:
            self._compact_media_info = compact
            for row, icon, label in getattr(self, "media_info_rows", ()):
                icon.setVisible(not compact)
                label.setFixedWidth(46 if compact else 54)
                row.setSpacing(3 if compact else 7)
                for index in range(row.count()):
                    widget = row.itemAt(index).widget()
                    if isinstance(widget, InfoChip):
                        row.setStretch(index, 0 if compact else 1)
                row.setStretch(row.count() - 1, 1 if compact else 0)
        if hasattr(self, "settings"):
            self.settings.update_export_dimensions()

    def _notice(self, message: str) -> None:
        """一行提示替代原来的底部日志栏：只在有话说的时候出现。"""
        self.status.setText(message)
        self.notice.setText(message)
        self.notice.setVisible(bool(message) and not self.embedded)
        self.task_changed.emit()

    def _page(self, title, mark):
        page = QWidget()
        page.setProperty("mediaIcon", mark)
        self.tabs.addTab(page, icons.icon(mark, 18), title)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        return page, layout

    def _tab_changed(self, *_):
        page = self.tabs.currentWidget()
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            color = C.accent.name() if tab is page else icons.INK
            self.tabs.setTabIcon(index, icons.icon(tab.property("mediaIcon"), 18, color, 1.9))
        self._stop_video()
        self._sync_panels()
        if page is self.audio_page:
            if self.frame_preview is not None:
                self.frame_preview.cancel_decode()
            self.settings.refresh_estimate()
            self.audio_workspace.enter(self.media_started)
        else:
            self.stop_audio()
            if self.session.frames:
                self.frame_preview.invalidate()
                self._sync_frame_preview()
            if page is self.details_page:
                self._update_file_details()
        self.settings.refresh_acceleration()
        self.settings.update_output_location()
        self.task_changed.emit()

    def _page_kind(self):
        page = self.tabs.currentWidget()
        for kind, tab in (("video", self.video_page), ("audio", self.audio_page),
                          ("image", self.image_page), ("details", self.details_page)):
            if page is not None and page is tab:
                return kind
        return ""

    def _video_estimate_timing(self):
        duration = self.session.video_duration()
        fps = self.session.frames.fps if self.session.frames else 0
        audio_duration = duration if self.session.audio_track() else 0
        return duration, fps, audio_duration, all(clip.path == self.path for clip in self.session.video)

    def mount_settings(self, layout=None):
        if self.settings.mount(layout):
            self._sync_panels()

    def output_options(self):
        return self.settings.output_options()

    def apply_output_options(self, options, *, apply_image_quality=False):
        self.settings.apply_output_options(options, apply_image_quality=apply_image_quality)

    def set_output_format(self, kind):
        self.settings.set_output_format(kind)

    def _sync_panels(self):
        page = self._page_kind()
        self.timeline.setVisible(page == "video")
        self.media_summary.setVisible(page == "video")
        self.inspector.setVisible(page != "details")
        self.settings.refresh_mode()

    def _update_range_marker(self):
        if self.filmstrip is None:
            return
        spans = [(frame, frame) for frame, selected in self.video_overrides.items() if selected]
        invalid = False
        if self.video_range is not None:
            first, last = self.video_range
            invalid = first > last
            for frame in sorted(self.video_overrides):
                if first <= frame <= last and not self.video_overrides[frame]:
                    if first < frame:
                        spans.append((first, frame - 1))
                    first = frame + 1
            if first <= last:
                spans.append((first, last))
        merged = []
        for first, last in sorted(spans):
            if merged and first <= merged[-1][1] + 1:
                merged[-1] = merged[-1][0], max(merged[-1][1], last)
            else:
                merged.append((first, last))
        previous_selection = self.video_selection
        self.video_selection = () if invalid else tuple(merged)
        self.filmstrip.set_selection(self.video_selection)
        if not self.session.frames:
            message = "等待读取帧数"
        elif invalid:
            message = "结束帧应大于或等于起始帧"
        elif not merged:
            message = ""
        else:
            count = sum(last - first + 1 for first, last in merged)
            message = f"已选 {count} 帧，{len(merged)} 段"
        self._selection_note = "" if message == "等待读取帧数" else message
        if hasattr(self, "selection_label"):
            self.selection_label.setText(f"· {self._selection_note}" if self._selection_note else "")
        if self.video_selection != previous_selection and self.tabs.currentWidget() is self.image_page:
            self._sync_frame_preview()
        self._update_timeline_header()

    def _range_fields_changed(self):
        self.video_overrides.clear()
        self.video_range = self.video_first.value(), self.video_last.value()
        self._update_range_marker()

    def _use_current_frame(self, field):
        if self._ready_video():
            with QSignalBlocker(field):
                field.setValue(self.cursor.value())
            self._range_fields_changed()

    def _set_video_range(self, first, last):
        self.video_range = first, last
        with QSignalBlocker(self.video_first), QSignalBlocker(self.video_last):
            self.video_first.setValue(first)
            self.video_last.setValue(last)

    def _click_frame(self, frame, modifiers):
        self.video_drag_origin = self.video_range, dict(self.video_overrides)
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            selected = any(first <= frame <= last for first, last in self.video_selection)
            in_range = self.video_range is not None and self.video_range[0] <= frame <= self.video_range[1]
            if selected != in_range:
                self.video_overrides.pop(frame, None)
            else:
                self.video_overrides[frame] = not selected
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            current = self.cursor.value()
            first, last = self.video_range or (current, current)
            if first > last:
                first = last = current
            self._set_video_range(min(first, frame), max(last, frame))
        else:
            self.video_overrides.clear()
            self._set_video_range(frame, frame)
        self._locate_frame(frame)
        self._update_range_marker()

    def _drag_select_frames(self, anchor, frame, modifiers):
        if not self._ready_video():
            return
        first, last = sorted((anchor, frame))
        base_range, base_overrides = self.video_drag_origin
        self.video_overrides = dict(base_overrides)
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            for selected_frame in range(first, last + 1):
                in_range = base_range is not None and base_range[0] <= selected_frame <= base_range[1]
                was_selected = base_overrides.get(selected_frame, in_range)
                if not was_selected:
                    self.video_overrides[selected_frame] = True
                elif in_range:
                    self.video_overrides[selected_frame] = False
                else:
                    self.video_overrides.pop(selected_frame, None)
            self.video_range = base_range
        elif modifiers & Qt.KeyboardModifier.ShiftModifier and base_range is not None:
            self.video_range = min(base_range[0], first), max(base_range[1], last)
        else:
            self.video_overrides.clear()
            self.video_range = first, last
        with QSignalBlocker(self.video_first), QSignalBlocker(self.video_last):
            self.video_first.setValue(first if self.video_range is None else self.video_range[0])
            self.video_last.setValue(last if self.video_range is None else self.video_range[1])
        self._update_range_marker()
        self._locate_frame(frame)

    def _show_frame_context_menu(self, frame, position):
        if not self._ready_video():
            return
        selected = any(first <= frame <= last for first, last in self.video_selection)
        if not selected:
            self.video_overrides.clear()
            self._set_video_range(frame, frame)
        self._locate_frame(frame)
        self._update_range_marker()
        count = sum(last - first + 1 for first, last in self.video_selection)
        menu = self.frame_context_menu
        menu.clear()
        menu.addAction("播放选区", self.transport.play_video_selection)
        menu.addSeparator()
        menu.addAction("复制\tCtrl+C", self.copy_video)
        menu.addAction("剪切\tCtrl+X", self.cut_video)
        menu.addAction("删除", self.delete_video)
        menu.addSeparator()
        if count == 1:
            menu.addAction("保存为图片…", self.save_selected_frame_as)
        elif count > 1:
            menu.addAction("片段另存为…", self.save_video_selection_as)
        menu.addSeparator()
        menu.addAction("帧前粘贴", self.paste_video_before).setEnabled(bool(self.session.video_clipboard))
        menu.addAction("帧后粘贴\tCtrl+V", self.paste_video_after).setEnabled(bool(self.session.video_clipboard))
        menu.addAction("撤销\tCtrl+Z", self.undo).setEnabled(bool(self.session.history))
        menu.popup(position)

    def save_selected_frame_as(self):
        if self._ready_video():
            self.exporter.save_selected_frame_as(self.video_selection)

    def save_video_selection_as(self):
        if self._ready_video() and self.video_selection:
            self.exporter.save_video_selection_as(self.video_selection)

    def _locate_frame(self, frame):
        self._stop_video()
        self.cursor.setValue(frame)

    def _entered_frame(self):
        text = self.locate_input.text()
        return int(text) if text.isdecimal() else 0

    def _update_locate_button(self, *_):
        self.locate_button.setEnabled(self._ready_video() and 1 <= self._entered_frame() <= self.cursor.maximum())

    def _jump_to_frame(self):
        if not self._ready_video():
            return
        frame = self._entered_frame()
        if 1 <= frame <= self.cursor.maximum():
            self._locate_frame(frame)
            self.locate_input.clear()
        else:
            self._notice(f"请输入 1 到 {self.cursor.maximum()} 之间的帧号。")

    @property
    def scope(self):
        return self.tasks.scope

    def _run(self, message, worker, on_done, *, exporting=False, on_error=None, kind=""):
        if self.scope is not None or self.closing:
            return
        self._stop_video()
        self.stop_audio()
        self._notice(message)
        self.progress.setRange(0, 100 if exporting else 0)
        if not self.embedded:
            self.progress_row.show()
        self.progress.setValue(0)
        self.tasks.reserve(on_done, on_error, kind)
        if self.audio_workspace is not None:
            self.audio_workspace.refresh_toolbar()
        if self.filmstrip is not None:
            self.transport.play_button.setEnabled(False)
            self._update_locate_button()
        self.cancel_button.setEnabled(True)
        self.settings.picture_group.setEnabled(False)
        self.task_changed.emit()
        self.tasks.submit(worker)

    @Slot(object, str, object, object)
    def _finished(self, result, error, done, failed):
        if self.filmstrip is not None:
            self.transport.play_button.setEnabled(self.session.frames is not None)
            self._update_locate_button()
        self.cancel_button.setEnabled(False)
        self.settings.picture_group.setEnabled(True)
        self.progress_row.hide()
        if error:
            if self.audio_workspace is not None and self.audio_workspace.listen_requested:
                self.stop_audio()
            if failed:
                failed(error)
            else:
                self._notice(error)
                self.status.setToolTip(error)
            if media_path_missing(self.path):
                self.missing_source.emit(self.path)
        else:
            self._notice("")
            self.status.clear()
            self.status.setToolTip("")
            done(result)
        self.task_changed.emit()
        if self.session.frames and self._frame_preview_panel() is not None and self.frame_preview.requested_frame != self.frame_preview.shown_frame:
            self.frame_preview.resume()
        if self.audio_workspace is not None:
            self.audio_workspace.on_task_finished()

    @Slot(int)
    def _progress(self, value):
        self.progress.setValue(value)
        self.task_changed.emit()

    def cancel_operation(self):
        if self.scope:
            self.scope.cancel()
            self.cancel_button.setEnabled(False)
            self._notice("正在取消…")

    def _load_index(self, scope):
        return index_frames(self.path, self.info, self.tools, scope)

    def _load_picture(self, scope):
        return thumbnail(self.path, self.info, self.tools, scope)

    @staticmethod
    def _display(panel, data):
        panel.set_image(data)

    def _show_image(self, data):
        self._display(self.image_view, data)

    def _indexed(self, frames):
        self.session.set_index(frames)
        self.transport.enable_indexed_controls()
        self._refresh_video()
        if self.tabs.currentWidget() is self.audio_page:
            self._refresh_audio(reset=True)
            if self.session.audio_track():
                self.audio_workspace.load_preview()

    def _refresh_video(self):
        count = int(length(self.session.video))
        self.filmstrip.set_track(self.session.video, self.session.frames, self.session.video_sources)
        self.transport.set_range(count)
        with QSignalBlocker(self.video_first), QSignalBlocker(self.video_last):
            for field in (self.video_first, self.video_last):
                field.setRange(1, count)
            self.video_first.setValue(1)
            self.video_last.setValue(count)
        self.video_range = None
        self.video_overrides.clear()
        self._update_range_marker()
        self.cursor.setRange(1, count)
        self.locate_input.setEnabled(True)
        self.locate_input.setToolTip(f"可定位第 1 到 {count} 帧，输入后按回车")
        self._update_locate_button()
        self._cursor_changed(self.cursor.value())
        self.settings.refresh_estimate()

    def _cursor_changed(self, value):
        self._stop_video()
        self._sync_cursor(value)

    def _sync_cursor(self, value, *, playing=False):
        if not self.session.frames:
            return
        self.filmstrip.set_current(value, playing=playing)
        fps = self.session.frames.fps
        self.transport.set_position(value, (value - 1) / fps, length(self.session.video) / fps)
        self._sync_frame_preview(playing=playing)
        self.settings.update_output_location()

    def _frame_position(self, value):
        seconds = (value - 1) / self.session.frames.fps
        return f"第 {value} / {int(length(self.session.video))} 帧 · {seconds:.3f} 秒"

    def _picture_frame_number(self):
        if self.tabs.currentWidget() is self.image_page:
            return self.video_selection[0][0] if self.video_selection else 1
        return self.cursor.value()

    def _frame_preview_panel(self):
        page = self.tabs.currentWidget()
        if self.video_page is not None:
            if page is self.video_page:
                return self.transport.frame_view
            if page is self.image_page:
                return self.image_view
        return None

    def _sync_frame_preview(self, *, playing=False):
        panel = self._frame_preview_panel()
        if panel is None:
            return
        value = self._picture_frame_number()
        frame = self.session.frame_ref(value)
        if frame is None:
            return
        path, index = frame
        origin = f" · 原始第 {index + 1} 帧" if index != value - 1 else ""
        if path != self.path:
            origin = f" · {self.session.video_sources[path].name} 第 {index + 1} 帧"
        self.settings.picture_caption.setText(self._frame_position(value) + origin)
        self.settings.picture_save.setText(f"保存第 {value} 帧")
        if not playing and panel is self.image_view:
            self.picture_estimate.schedule()
        self.frame_preview.request(frame, value, playing=playing)

    def _preview_error(self, message, detail):
        self._notice(message)
        self.status.setToolTip(detail)

    def _video_is_playing(self):
        return self.transport is not None and self.transport.playback is not None

    def _canvas_for(self, video):
        if any(clip.path != self.path for clip in video):
            return self.info.width, self.info.height
        return None

    def _stop_video(self):
        if self.transport is not None:
            self.transport.stop()

    def _suspend_seek(self):
        self.frame_preview.suspend()

    def _playback_stopped(self):
        if not self.closing:
            self.frame_preview.invalidate()
            self.frame_preview.resume()

    def _video_played(self, number, data):
        self._sync_cursor(number, playing=True)
        self.frame_preview.present_played(data)

    def _ready_video(self):
        return self.session.frames is not None and self.scope is None

    def _timeline_command(self, command):
        actions = {"copy": self.copy_video, "cut": self.cut_video,
                   "paste": self.paste_video_after, "undo": self.undo}
        if action := actions.get(command):
            action()

    def copy_video(self):
        if self._ready_video() and self.video_selection:
            count = self.session.copy_video(self.video_selection)
            self._notice(f"已复制 {count} 帧。")

    def cut_video(self):
        self.copy_video()
        self.delete_video()

    def delete_video(self):
        if self._ready_video() and self.video_selection:
            self._remove_video_ranges([(first - 1, last) for first, last in self.video_selection])

    def _remove_video_ranges(self, ranges, *, audio_tail_end=None):
        """按当前时间线剪掉同一范围，一次撤销恢复两轨。"""
        tracks = self.session.cut_tracks(ranges, audio_tail_end=audio_tail_end)
        if tracks is None:
            self._notice("至少保留一帧。")
            return False
        self._stop_video()
        self.stop_audio()
        self.session.replace_tracks(*tracks)
        self._refresh_video()
        self._refresh_audio(reset=True)
        if self.tabs.currentWidget() is self.audio_page and self.session.audio_track():
            self.audio_workspace.load_preview()
        else:
            self._notice("已裁剪所选片段。")
        return True

    def paste_video_before(self):
        self._paste_video(self.cursor.value() - 1)

    def paste_video_after(self):
        self._paste_video(self.cursor.value())

    def _paste_video(self, position):
        if self._ready_video() and self.session.video_clipboard:
            self._insert_video(position, self.session.video_clipboard, self.session.video_clipboard_audio)

    def _queued_source(self, key):
        return self.lookup_media(key) if key and self.lookup_media and not self.closing else None

    def _video_drop_preview(self, key):
        source = self._queued_source(key)
        if self._ready_video() and source and isinstance(source[1], (VideoInfo, ImageInfo)):
            path, _, picture = source
            return path.name, picture
        return None

    def _move_video_ranges(self, ranges, position):
        if not self._ready_video():
            return
        tracks = self.session.moved_tracks(ranges, position)
        if tracks is None:
            return
        count, video, audio = tracks
        self._stop_video()
        self.stop_audio()
        self.session.replace_tracks(video, audio)
        self._refresh_video()
        self._refresh_audio(reset=True)
        self._set_video_range(position + 1, position + count)
        self._locate_frame(position + 1)
        self._update_range_marker()
        self._notice(f"已移动 {count} 帧。")

    def _drop_video(self, key, position):
        if not self._video_drop_preview(key):
            return
        path, info, _ = self._queued_source(key)
        if media_path_missing(path):
            self._notice("找不到要插入的文件。")
            self.missing_source.emit(path)
            return
        cached = self.session.source_for(path)

        def insert(result):
            clip_path, source = result
            self.session.register_source(clip_path, source, image_path=path if isinstance(info, ImageInfo) else None)
            clip = Clip(clip_path, source.info.stream, 0, source.frames.count)
            self._insert_video(position, [clip], [])
            self._locate_frame(position + 1)
            self._notice(f"已在第 {position + 1} 帧处插入 {source.frames.count} 帧。")

        if cached is not None:
            insert(cached)
        else:
            self._run("正在读取拖入的画面…", lambda scope: self._load_dropped_video(path, info, scope),
                      insert, kind="video_insert")

    def _load_dropped_video(self, path, info, scope):
        if isinstance(info, VideoInfo):
            frames = index_frames(path, info, self.tools, scope)
            return path, VideoSource(info, frames, path.name)
        clip_path = Path(self.scratch.name) / f"still-{uuid4().hex}.png"
        _save_image(path, clip_path, "PNG")
        scope.check()
        fps = self.session.frames.fps
        frames = Frames((0.0,), 1 / fps, fps)
        video = VideoInfo(0, frames.duration, info.width, info.height, fps, "PNG", clip_path.stat().st_size)
        return clip_path, VideoSource(video, frames, path.name)

    def _insert_video(self, position, video, audio):
        self._stop_video()
        self.stop_audio()
        self.session.insert_video(position, video, audio)
        self._refresh_video()
        self._refresh_audio(reset=True)
        if self.tabs.currentWidget() is self.audio_page and self.session.audio_track():
            self.audio_workspace.load_preview()

    def _refresh_audio(self, *, reset=False):
        self.settings.refresh_estimate()
        self.audio_workspace.refresh_track(reset=reset)

    def _delete_marked_audio(self):
        mark = self.audio_workspace.marked_range()
        if self.scope is None and mark:
            start, end = mark
            updated = splice(self.session.audio_track(), start, end)
            self._change_audio(updated)
            suffix = "，音画时长已对齐。" if abs(length(updated) - self.session.video_duration()) < .0005 else "。"
            self._notice(f"已删除 {end - start:.3f} 秒标红音频{suffix}")

    def _can_drop_audio(self, key):
        source = self._queued_source(key)
        if self.scope is not None or not source or (self.video_page is not None and not self.session.frames):
            return False
        info = source[1]
        return bool((isinstance(info, AudioInfo) or
                     (isinstance(info, VideoInfo) and info.audio_stream is not None)) and info.duration > 0)

    def _drop_audio(self, key, position):
        if not self._can_drop_audio(key):
            return
        path, info, _ = self._queued_source(key)
        if media_path_missing(path):
            self._notice("找不到要插入的文件。")
            self.missing_source.emit(path)
            return
        stream = info.stream if isinstance(info, AudioInfo) else info.audio_stream
        clip = Clip(path, stream, 0, info.duration)
        track = self.session.audio_track()
        inserted = [*audio_segment(track, 0, position), clip, *slice_track(track, position, length(track))]
        if self.session.frames:
            duration = max(length(inserted), length(self.session.video) / self.session.frames.fps)
            inserted = audio_segment(inserted, 0, duration)
        self._change_audio(inserted)
        self._notice(f"已在 {position:.3f} 秒插入音频。")

    def _audio_command(self, command):
        actions = {"copy": self.copy_audio, "cut": self.cut_audio,
                   "paste": self.paste_audio, "undo": self.undo}
        actions[command]()

    def copy_audio(self):
        if self.scope is None:
            start, end, track = self.audio_workspace.selection()
            if duration := self.session.copy_audio(start, end, track):
                self._notice(f"已复制 {duration:.3f} 秒。")

    def cut_audio(self):
        if self.video_page is not None and (not self.session.frames or self.audio_workspace.selection_start() < self.session.video_duration() - .0005):
            self._cut_audio_with_video()
            return
        self.copy_audio()
        self.delete_audio()

    def _cut_audio_with_video(self):
        start, end, track = self.audio_workspace.selection()
        if not self._ready_video() or end <= start:
            return
        if end > self.session.video_duration() + .0005:
            self._notice("选区跨过视频结尾，请分开剪切，或删除标红音频。")
            return
        first, last = self.session.audio_frames(start, end)
        video, audio = self.session.linked_audio_selection(first, last, track)
        if self._remove_video_ranges([(first, last)]):
            self.session.set_audio_clipboard(video, audio)

    def _change_audio(self, track):
        self.session.replace_audio(track)
        self.stop_audio()
        self._refresh_audio()
        self.audio_workspace.schedule_reload()

    def delete_audio(self):
        start, end, track = self.audio_workspace.selection()
        if self.scope is None and end > start:
            video_end = self.session.video_duration()
            if self.video_page is None or (self.session.frames and start >= video_end - .0005):
                self._change_audio(splice(track, start, end))
            elif self.session.frames:
                tail = end if end > video_end + .0005 else None
                self._remove_video_ranges([self.session.audio_frames(start, end)], audio_tail_end=tail)

    def paste_audio(self):
        if self.scope is None and self.session.audio_clipboard:
            position = self.audio_workspace.insertion_position()
            if self.session.frames and self.session.audio_clipboard_video:
                frame = min(int(length(self.session.video)), round(position * self.session.frames.fps))
                self._insert_video(frame, self.session.audio_clipboard_video, self.session.audio_clipboard)
            else:
                self._change_audio(splice(self.session.audio_track(), position, position, self.session.audio_clipboard))

    def set_volume(self):
        start, end, track = self.audio_workspace.selection()
        if self.scope is None and end > start:
            clips = [replace(clip, volume=self.audio_workspace.selected_volume()) for clip in slice_track(track, start, end)]
            self._change_audio(splice(track, start, end, clips))

    def insert_audio(self):
        if self.scope is not None:
            return
        filters = " ".join(f"*{ext}" for ext in sorted(AUDIO_EXTENSIONS))
        name, _ = QFileDialog.getOpenFileName(self, "插入音频", "", f"音频文件 ({filters});;所有文件 (*.*)")
        if not name:
            return
        path = Path(name).resolve()
        position = self.audio_workspace.insertion_position()
        track = self.session.audio_track()
        def load(scope):
            info = probe(path, self.tools, scope)
            if not isinstance(info, AudioInfo) or not info.duration:
                raise MediaError("请选择可读取时长的音频。")
            return splice(track, position, position, [Clip(path, info.stream, 0, info.duration)])
        self._run("正在读取新音频…", load, self._change_audio)

    def undo(self):
        if self.tasks.kind == "video_insert":
            self.cancel_operation()
            return
        if self.scope is None and self.session.history:
            self._stop_video()
            self.session.undo()
            if self.session.frames:
                self._refresh_video()
            if self.audio_page:
                self._refresh_audio(reset=True)
                self.audio_workspace.schedule_reload()
            self._notice("已撤销上一次编辑。")

    def stop_audio(self):
        if self.audio_workspace is not None:
            self.audio_workspace.stop()

    def export_status(self) -> PreviewStatus:
        busy = self.scope is not None
        ready = self.session.frames is not None if self._page_kind() == "video" else self.info is not None
        return PreviewStatus(
            can_export=not busy and ready,
            can_cancel=busy and self.cancel_button.isEnabled(),
            message=self.notice.text(),
            maximum=self.progress.maximum() if busy else 100,
            progress=max(0, self.progress.value()) if busy else 0,
        )

    def export_current_page(self):
        if self.tabs.currentWidget() is self.image_page:
            self.save_image()
        else:
            self.save_edit()

    def save_edit(self):
        if self.scope is None:
            self.exporter.save_edit(include_video=self.tabs.currentWidget() is self.video_page)

    def _picture_source(self):
        if isinstance(self.info, ImageInfo):
            return self.path, None, 0, self.info.width, self.info.height
        if not self.session.frames:
            return None
        reference = self.session.frame_ref(self._picture_frame_number())
        if reference is None:
            return None
        path, index = reference
        info = self.session.video_sources[path].info
        return path, info, index, info.width, info.height

    def _update_file_details(self):
        if hasattr(self, "details_view"):
            picture = None
            preview = getattr(self, "preview", None)
            if preview is not None:
                pixmap = preview.pixmap()
                if pixmap is not None and not pixmap.isNull() and pixmap.width() > 16:
                    picture = pixmap
            self.details_view.set_media(self.path, self.info, picture=picture)

    def save_image(self):
        if self.scope is not None:
            return
        if isinstance(self.info, VideoInfo):
            if self.session.frames:
                self.exporter.save_image(self._picture_frame_number())
        else:
            self.exporter.save_image()

    def _saved(self, path, limit=0, *, frame=None):
        self.saved.emit(path)
        size = path.stat().st_size
        suffix = f" · {size / 1000:.1f} KB"
        if limit and size > limit:
            suffix += " · 超出目标，已保留最小版本"
        item = f"第 {frame} 帧 · {path.name}" if frame is not None else path.name
        self._notice(f"已保存 {item}{suffix}，并加入媒体队列。")
        self.status.setToolTip(str(path))

    def suspend(self):
        if self.transport is not None:
            self.transport.close_fullscreen()
        self._stop_video()
        if self.audio_workspace is not None:
            self.audio_workspace.suspend()
        if self.frame_preview is not None:
            self.frame_preview.suspend()

        if not self.settings.configuration_only:
            self.picture_estimate.cancel()

    def resume(self):
        if self.session.frames and self._frame_preview_panel() is not None:
            self.frame_preview.resume()
            if self.settings.uses_picture_estimate():
                self.picture_estimate.schedule()
        elif isinstance(self.info, ImageInfo) and self.tabs.currentWidget() is self.image_page:
            self.picture_estimate.schedule()
        elif self.audio_page is not None and self.tabs.currentWidget() is self.audio_page:
            self.audio_workspace.resume()

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.suspend()
        if self.filmstrip is not None:
            self.filmstrip.shutdown()
        if self.frame_preview is not None:
            self.frame_preview.shutdown()
        self.picture_estimate.shutdown()
        for animation in self.findChildren(Motion):
            animation.stop()
        self.tasks.shutdown()
        self.scratch.cleanup()

    def reject(self):
        if self.embedded:
            self.return_requested.emit()
        else:
            super().reject()

    def done(self, result):
        self.shutdown()
        super().done(result)
