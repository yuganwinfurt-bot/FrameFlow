"""Own output controls and their mode, presets and per-file export parameters."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QPushButton, QTabBar, QVBoxLayout, QWidget)

from .editor_controls import _button, _combo, _icon_label, _spin, _text, _text_button
from .estimates import estimate_audio, estimate_video
from .media import (AudioInfo, Export, ImageInfo, VideoInfo, VIDEO_FRAME_RATES, VIDEO_RESOLUTIONS,
                    _AUDIO_ENCODERS, _ENCODERS, _IMAGE_FORMATS, hardware_acceleration_choices, output_extension)
from .media_text import detailed_size as _size_text
from .motion import DirectoryPathEdit, QualityRow
from .theme import C, STUDIO_STYLE


_IMAGE_SIZE_CHOICES = (
    ("超大尺寸（适合高分辨率图片）", 2160, "最长边 2160 px"),
    ("高清（适合日常保存）", 1920, "最长边 1920 px"),
    ("标准（适合分享）", 1280, "最长边 1280 px"),
    ("小尺寸（节省空间）", 720, "最长边 720 px"),
    ("超小尺寸（缩略图用）", 256, "最长边 256 px"),
)


_PICTURE_PRESETS = (
    ("画质优先", 95, 6, "保留更多细节，文件通常更大。"),
    ("均衡", 85, 7, "兼顾清晰度和文件大小。"),
    ("文件体积", 72, 9, "优先缩小文件，可填写目标大小。"),
)


def _friendly_edge_title(edge: int) -> str:
    """把 3840/1920 这类像素数字翻译成人话，专业信息放在括号里。"""
    names = {0: "原始分辨率", 3840: "超大尺寸 · 4K", 2560: "超大尺寸 · 1440P",
             1920: "高清 · 1080P", 1280: "高清 · 720P",
             854: "标准 · 480P", 640: "小尺寸 · 360P"}
    return names.get(edge, f"最长边 {edge} px")


class _ExportCells:
    """按控件找到它所在的单元格，保持老的 setRowVisible 调用方式。"""

    def __init__(self, cells: dict):
        self._cells = cells

    def cell(self, widget):
        return self._cells.get(widget, widget)

    def setRowVisible(self, widget, visible: bool) -> None:
        self.cell(widget).setVisible(visible)


def _parameter_row(cells, title: str, widget, width: int = 81) -> QWidget:
    holder = QWidget()
    holder.setObjectName("paramRow")
    holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    holder.setMinimumHeight(30)
    line = QHBoxLayout(holder)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(10)
    label = QLabel(title)
    label.setObjectName("paramLabel")
    label.setFixedWidth(width)
    line.addWidget(label)
    line.addWidget(widget, 1)
    holder.field_label = label
    cells[widget] = holder
    return holder


def _caption(icon: str, title: str, extra=None) -> QWidget:
    holder = QWidget()
    holder.setObjectName("paramCaption")
    holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    line = QHBoxLayout(holder)
    line.setContentsMargins(0, 0, 0, 0)
    line.setSpacing(7)
    line.addWidget(_icon_label(icon, 19))
    line.addWidget(_text(title, name="cardTitle"))
    line.addStretch(1)
    if extra is not None:
        line.addWidget(extra)
    return holder


class ExportSettings(QWidget):
    settings_changed = Signal()
    directory_changed = Signal(object)
    picture_inputs_changed = Signal()
    save_picture_requested = Signal()

    def __init__(self, path, info, tools, directory, scroll, inspector_layout, *,
                 output_stem, page_kind, picture_frame_number, picture_source,
                 audio_duration, video_timing, task_running):
        super().__init__()
        self.path, self.info, self.tools = path, info, tools
        self.directory = directory or path.parent
        self._get_output_stem = output_stem
        self.page_kind = page_kind
        self.picture_frame_number = picture_frame_number
        self.picture_source = picture_source
        self.audio_duration = audio_duration
        self.video_timing = video_timing
        self.task_running = task_running
        self._scroll = scroll
        self.configuration_only = False
        self.media_format = "mp4" if isinstance(info, VideoInfo) else "mp3"
        self.audio_tab_format = "mp3"
        self.selected_acceleration = "cpu"
        self.setObjectName("exportBody")
        self.setMinimumWidth(1)
        stack = QVBoxLayout(self)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(7)
        self._build_picture_export(stack)
        self._build_output_settings(inspector_layout, stack)

    def _build_picture_export(self, stack):
        self.picture_group = QFrame()
        self.picture_group.setObjectName("pictureExportCard")
        self.picture_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        picture = QVBoxLayout(self.picture_group)
        picture.setContentsMargins(10, 8, 10, 9)
        picture.setSpacing(6)
        self.picture_frame = _text("导出图片", name="studioSection")
        self.picture_frame.setObjectName("cardTitle")
        picture_heading = QHBoxLayout()
        picture_heading.setSpacing(7)
        picture_heading.addWidget(_icon_label("image", 18, C.accent.name()))
        picture_heading.addWidget(self.picture_frame)
        picture_heading.addStretch(1)
        picture.addLayout(picture_heading)
        self.picture_caption = _text("", muted=True)
        self.picture_caption.setObjectName("pictureCaption")
        picture.addWidget(self.picture_caption)
        picture_fields = QFormLayout()
        picture_fields.setContentsMargins(0, 0, 0, 0)
        picture_fields.setHorizontalSpacing(8)
        picture_fields.setVerticalSpacing(5)
        picture_fields.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        def picture_row(title, control):
            picture_fields.addRow(title, control)
            if label := picture_fields.labelForField(control):
                label.setObjectName("pictureFieldLabel")

        self.image_format = _combo()
        for key, label in _IMAGE_FORMATS.items():
            self.image_format.addItem("GIF · 静态" if key == "gif_image" else label, key)
        picture_row("格式", self.image_format)
        self.picture_preset = QTabBar()
        self.picture_preset.setObjectName("picturePreset")
        self.picture_preset.setAccessibleName("画质与体积")
        self.picture_preset.setDrawBase(False)
        self.picture_preset.setCursor(Qt.CursorShape.PointingHandCursor)
        for title, _, _, hint in _PICTURE_PRESETS:
            self.picture_preset.setTabToolTip(self.picture_preset.addTab(title), hint)
        self.picture_preset.setCurrentIndex(1)
        self.picture_quality_label = _text("画质", name="pictureFieldLabel")
        self.picture_preset.hide()
        self.picture_quality_label.hide()
        self.picture_preset_hint = _text("", name="pictureHint")
        self.picture_preset_hint.setWordWrap(True)
        self.picture_preset_hint.hide()
        self.resolution = _combo()
        self.resolution.addItem("原始尺寸（推荐，不缩放）", 0)
        for title, edge, hint in _IMAGE_SIZE_CHOICES:
            self.resolution.addItem(title, edge)
            self.resolution.setItemData(self.resolution.count() - 1,
                                        f"{hint} · 最长边 {edge} px", Qt.ItemDataRole.ToolTipRole)
        self.resolution.setToolTip("保持画面比例缩放，不会拉伸或压扁；括号内为最长边像素。")
        picture_row("分辨率", self.resolution)
        self.picture_dimensions = _text("正在读取…", name="pictureValue")
        picture_row("输出尺寸", self.picture_dimensions)
        self.size = _spin()
        self.size.setRange(0, 1_000_000)
        self.size.setSpecialValueText("自动")
        self.size.setSuffix(" KB")
        self.size.setToolTip("指定 KB 时会尝试进一步压缩，必要时缩小分辨率。")
        picture_row("目标大小", self.size)
        def update_picture_preset():
            index = self.picture_preset.currentIndex()
            compact = index == 2
            kind = self.image_format.currentData()
            supports_quality = kind in {"jpg", "webp", "avif"}
            lossless_png = kind == "png"
            self.picture_quality_label.setText("PNG 压缩级别" if lossless_png else "画质")
            names = (("快速", "均衡", "高压缩") if lossless_png else
                     ("画质优先", "均衡", "文件体积"))
            for tab_index, name in enumerate(names):
                self.picture_preset.setTabText(tab_index, name)
            if lossless_png:
                hint = ("PNG 为无损格式，压缩级别不会降低画质。" if not compact else
                        "PNG 保持无损；设定体积上限时可能缩小输出尺寸。")
            elif supports_quality:
                hint = _PICTURE_PRESETS[index][3]
            else:
                hint = "此格式不使用有损画质滑块；可通过分辨率调整文件大小。"
            self.picture_preset_hint.setText(hint)
            self.picture_quality_label.setVisible(False)
            self.picture_preset.setVisible(False)
            self.picture_preset_hint.setVisible(False)
            picture_fields.setRowVisible(self.size, compact)

        self.picture_preset.currentChanged.connect(update_picture_preset)
        self.image_format.currentIndexChanged.connect(update_picture_preset)
        self.image_format.currentIndexChanged.connect(self.update_output_location)
        self.picture_preset.currentChanged.connect(self._picture_input_changed)
        self.image_format.currentIndexChanged.connect(self._picture_input_changed)
        self.image_format.currentIndexChanged.connect(
            lambda *_: self._sync_image_quality())
        self.resolution.currentIndexChanged.connect(self._picture_input_changed)
        self.size.valueChanged.connect(self._picture_input_changed)
        update_picture_preset()
        picture.addLayout(picture_fields)
        if isinstance(self.info, ImageInfo):
            self.picture_caption.setText(f"当前图片 · {self.info.width} × {self.info.height} 像素")
        self.picture_save = _button("保存图片", self.save_picture_requested.emit)
        self.picture_save.hide()
        stack.addWidget(self.picture_group)

    def _build_output_settings(self, side, stack):
        self.export_group = QWidget()
        self.export_group.setObjectName("sharedExportBody")
        self.export_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        export = QVBoxLayout(self.export_group)
        export.setContentsMargins(0, 0, 0, 0)
        export.setSpacing(6)
        cells: dict[QWidget, QWidget] = {}

        self._build_mode(export, cells)
        self._build_video_fields(export, cells)
        self._build_quality(export, cells)
        audio_box = self._build_audio_fields(export, cells)
        self._build_image_quality()
        self._build_size_limit(audio_box, cells)
        self._build_estimate(cells)
        self._build_directory()

        cells[self.preset] = self.mode_card
        cells[self.export_edge] = self.resolution_row
        cells[self.quality] = self.quality_card
        cells[self.output_fps] = self.fps_row
        cells[self.acceleration] = self.acceleration_row
        self.export_fields = _ExportCells(cells)
        self.format.currentIndexChanged.connect(self.refresh_acceleration)
        self.format.currentIndexChanged.connect(self._refresh_audio_codec)
        self.format.currentIndexChanged.connect(self.refresh_estimate)
        self.format.currentIndexChanged.connect(self.update_output_location)
        self.format.currentIndexChanged.connect(self._format_changed)
        self.acceleration.currentIndexChanged.connect(self._sync_quality_encoder)
        self.acceleration.currentIndexChanged.connect(self.refresh_estimate)
        self.format.setMinimumContentsLength(12)
        self.format.setSizeAdjustPolicy(self.format.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.gif_profile = _combo()
        self.gif_profile.addItem("小体积 · 8 FPS", True)
        self.gif_profile.addItem("清晰 · 12 FPS", False)
        self.gif_row = _parameter_row(cells, "GIF 设置", self.gif_profile)
        self.gif_row.hide()
        export.insertWidget(2, self.gif_row)
        export.addWidget(self.limit_row)

        self.panel = QFrame()
        self.panel.setObjectName("exportSettingsPanel")
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel_box = QVBoxLayout(self.panel)
        panel_box.setContentsMargins(0, 0, 0, 0)
        panel_box.setSpacing(6)
        self.inspector_title = _caption("gear", "输出设置")
        self.inspector_title.setObjectName("exportPanelHeader")
        self.inspector_title.setMinimumHeight(27)
        side.insertWidget(0, self.inspector_title)
        panel_box.addWidget(self.export_group)
        stack.addWidget(self.panel)
        stack.addWidget(self.estimate_card)
        stack.addWidget(self.directory_group)
        stack.addStretch(1)

    def refresh_acceleration(self, *_):
        if not hasattr(self, "acceleration"):
            return
        kind = self.format.currentData()
        current = self.selected_acceleration
        choices = hardware_acceleration_choices(
            self.tools.ffmpeg if self.tools is not None else "", kind or ""
        )
        with QSignalBlocker(self.acceleration):
            self.acceleration.clear()
            for key, title in choices:
                self.acceleration.addItem(title, key)
            index = self.acceleration.findData(current)
            self.acceleration.setCurrentIndex(index if index >= 0 else 0)
        video_format = kind in _ENCODERS and kind != "gif"
        self.export_fields.setRowVisible(self.acceleration, video_format)
        self.export_fields.setRowVisible(self.output_fps, video_format)
        self.export_fields.setRowVisible(self.export_edge, video_format or kind == "gif")
        self.update_export_dimensions()

    def _remember_acceleration(self, *_):
        self.selected_acceleration = self.acceleration.currentData() or "cpu"

    def update_export_dimensions(self, *_):
        edge = self.export_edge.currentData()
        kind = self.format.currentData() or self.codec.currentData()
        show = kind in _ENCODERS and bool(edge and edge > 0) and isinstance(self.info, VideoInfo)
        if show:
            width, height = self.info.width, self.info.height
            scale = edge / max(1, width, height)
            out_width = max(2, int(width * scale / 2 + .5) * 2)
            out_height = max(2, int(height * scale / 2 + .5) * 2)
            self.dimension_note.setText(f"实际输出 {out_width} × {out_height}")
        self.export_edge.setAccessibleDescription(self.dimension_note.text() if show else "")
        self.export_fields.setRowVisible(self.export_dimensions, show and self.width() >= 337)
        self.refresh_estimate()

    _CONTAINER_TITLES = {"mp4": "MP4", "mp4_hevc": "MP4", "mov": "MOV", "mkv": "MKV", "mkv_hevc": "MKV",
                         "webm": "WEBM", "avi": "AVI"}

    _CODEC_TITLES = {"mp4": "H.264 (AVC)", "mov": "H.264 (AVC)", "mkv": "H.264 (AVC)",
                     "mkv_hevc": "H.265 (HEVC)", "mp4_hevc": "H.265 (HEVC)", "webm": "VP9", "avi": "MPEG-4"}

    def _uses_video_picker(self):
        return isinstance(self.info, VideoInfo) and (
            self.configuration_only or self.page_kind() == "video")

    @classmethod
    def _container_for(cls, key):
        return cls._CONTAINER_TITLES.get(key, "GIF" if key == "gif" else "提取音频")

    def _load_formats(self):
        video = self._uses_video_picker()
        audio = not video or self.info.audio_stream is not None
        keys = [*_ENCODERS, "gif"] if video else []
        if audio:
            keys.extend(_AUDIO_ENCODERS)
        key = self.media_format if video or isinstance(self.info, AudioInfo) else self.audio_tab_format
        with QSignalBlocker(self.format):
            self.format.clear()
            for item in keys:
                self.format.addItem({"m4a": "M4A · AAC", "ogg": "OGG · Vorbis"}.get(item, item.upper()), item)
            self.format.setCurrentIndex(max(0, self.format.findData(key)))

    def set_output_format(self, kind):
        if isinstance(self.info, ImageInfo):
            index = self.image_format.findData(kind)
            if index >= 0 and index != self.image_format.currentIndex():
                with QSignalBlocker(self.image_format):
                    self.image_format.setCurrentIndex(index)
                self.update_output_location()
                self.picture_inputs_changed.emit()
            return
        if kind == self.media_format:
            return
        self.media_format = kind
        self._load_formats()
        self._sync_export_rows()

    def _format_changed(self, *_):
        kind = self.format.currentData()
        if self._uses_video_picker() or isinstance(self.info, AudioInfo):
            self.media_format = kind
        else:
            self.audio_tab_format = kind
        self._sync_export_rows()

    def _sync_format_pickers(self):
        video = self._uses_video_picker()
        self.container.setVisible(video)
        self.format.setVisible(not video)
        if not video:
            self.codec_row.hide()
            return
        key = self.format.currentData()
        groups = dict.fromkeys(self._container_for(self.format.itemData(i)) for i in range(self.format.count()))
        self._syncing_format = True
        try:
            with QSignalBlocker(self.container):
                self.container.clear()
                for title in groups:
                    self.container.addItem(title, title)
                self.container.setCurrentIndex(self.container.findData(self._container_for(key)))
            self._fill_codec()
            with QSignalBlocker(self.codec):
                self.codec.setCurrentIndex(max(0, self.codec.findData(key)))
        finally:
            self._syncing_format = False
        self.codec_row.layout().itemAt(0).widget().setText("音频格式" if key in _AUDIO_ENCODERS else "视频编码")
        self.codec_row.setVisible(key != "gif")

    def _fill_codec(self):
        group = self.container.currentData()
        with QSignalBlocker(self.codec):
            self.codec.clear()
            for index in range(self.format.count()):
                kind = self.format.itemData(index)
                if self._container_for(kind) == group:
                    self.codec.addItem(self._CODEC_TITLES.get(kind, kind.upper()), kind)

    def _pick_container(self, *_):
        if getattr(self, "_syncing_format", False):
            return
        self._fill_codec()
        self._pick_codec()

    def _pick_codec(self, *_):
        if getattr(self, "_syncing_format", False):
            return
        key = self.codec.currentData()
        index = self.format.findData(key) if key else -1
        if index >= 0 and index != self.format.currentIndex():
            self.format.setCurrentIndex(index)

    def _refresh_audio_codec(self, *_):
        kind = self.format.currentData()
        codec = {"webm": "Opus", "avi": "MP3", "gif": "无音轨", "mp3": "MP3",
                 "wav": "PCM", "flac": "FLAC", "opus": "Opus", "ogg": "Vorbis"}.get(kind, "AAC")
        if hasattr(self, "audio_codec_text"):
            self.audio_codec_text.setText(codec)
            self.audio_bitrate.setEnabled(codec != "无音轨")
        if hasattr(self, "audio_sample_rate"):
            previous = self.audio_sample_rate.currentData()
            rates = (24000, 48000) if kind == "opus" else (22050, 44100, 48000)
            with QSignalBlocker(self.audio_sample_rate):
                self.audio_sample_rate.clear()
                self.audio_sample_rate.addItem("保持源采样率", 0)
                for rate in rates:
                    self.audio_sample_rate.addItem(f"{rate / 1000:g} kHz", rate)
                self.audio_sample_rate.setCurrentIndex(max(0, self.audio_sample_rate.findData(previous)))

    _VIDEO_PRESETS = (("轻量 · 720P", 1280, 28), ("均衡（推荐）", 1920, 23),
                      ("高画质 · 原始分辨率", 0, 18))

    _AUDIO_PRESETS = (("小体积 · 128k", 128), ("标准 · 192k", 192), ("高音质 · 320k", 320))

    def _fill_presets(self):
        """按当前媒体类型重建导出模式下拉，避免音频看到视频档位。"""
        if not hasattr(self, "preset"):
            return
        audio = not isinstance(self.info, VideoInfo) and isinstance(self.info, AudioInfo)
        blocker = QSignalBlocker(self.preset)
        self.preset.clear()
        if audio:
            for title, bitrate in self._AUDIO_PRESETS:
                self.preset.addItem(title, ("audio", bitrate))
        else:
            for title, edge, crf in self._VIDEO_PRESETS:
                self.preset.addItem(title, (edge, crf))
        self.preset.addItem("自定义", None)
        self.preset.setCurrentIndex(1 if self.preset.count() > 2 else 0)
        del blocker

    def _apply_preset(self, *_):
        data = self.preset.currentData()
        if isinstance(data, tuple) and data and data[0] == "audio":
            self.audio_bitrate.setCurrentIndex(
                max(0, self.audio_bitrate.findData(data[1])))
            self.refresh_estimate()
            return
        data = self.preset.currentData()
        if not data or not hasattr(self, "quality"):
            return
        edge, crf = data
        with QSignalBlocker(self.export_edge), QSignalBlocker(self.quality), \
                QSignalBlocker(self.preset):
            index = self.export_edge.findData(edge)
            if index >= 0:
                self.export_edge.setCurrentIndex(index)
            self.quality.setValue(crf)
        self.update_export_dimensions()
        self._notify_settings()

    def _update_mode_hint(self, *_):
        if not hasattr(self, "mode_hint"):
            return
        index = self.preset.currentIndex()
        if isinstance(self.info, AudioInfo) and not isinstance(self.info, VideoInfo):
            descriptions = {
                0: "128 kbps，体积最小，适合语音与人声。",
                1: "192 kbps，通用音乐与视频配乐都够用。",
                2: "320 kbps，接近无损听感，文件更大。",
            }
        else:
            descriptions = {
                0: "降低输出分辨率并提高压缩率，通常可减小文件。",
                1: "兼顾画质、文件体积与常用播放器兼容性。",
                2: "保留原始分辨率并优先画面细节，文件通常更大。",
            }
        hint = descriptions.get(index, "手动设置编码、分辨率与帧率")
        if hasattr(self, "acceleration") and self.acceleration.currentData() == "mf":
            hint += " 当前硬件编码器使用固定质量控制。"
        self.mode_hint.setText(hint)

    def _sync_quality_encoder(self, *_):
        if not hasattr(self, "quality"):
            return
        kind = self.format.currentData()
        backend = self.acceleration.currentData() or "cpu"
        measure = {"cpu": "CRF", "nvenc": "CQ", "amf": "QP", "qsv": "质量"}.get(backend, "")
        supported = kind in _ENCODERS and kind != "avi" and bool(measure)
        self.quality.set_measure(measure or "质量")
        if hasattr(self, "quality_card"):
            self.export_fields.setRowVisible(self.quality_card, supported and kind != "avi")
        self._update_mode_hint()

    def _sync_preset(self, *_):
        """手动改分辨率或质量时，预设跟着落到最接近的一项，否则显示「自定义」。"""
        if not hasattr(self, "preset"):
            return
        edge = self.export_edge.currentData() or 0
        crf = self.quality.value()
        for index in range(self.preset.count() - 1):
            data = self.preset.itemData(index)
            if data and data[0] == edge and data[1] == crf:
                if self.preset.currentIndex() != index:
                    with QSignalBlocker(self.preset):
                        self.preset.setCurrentIndex(index)
                self._update_mode_hint()
                return
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(self.preset.count() - 1)
        self._update_mode_hint()

    def _schedule_estimate_tick(self, *_value) -> None:
        """拖动过程中按 120ms 节流重算体积，松手时由 settled 再算一次。"""
        timer = getattr(self, "estimate_throttle", None)
        if timer is not None:
            timer.start()

    def refresh_estimate(self, *_):
        if not hasattr(self, "estimate_rows"):
            return
        if self.uses_picture_estimate() or (not self.configuration_only and self.page_kind() == "details"):
            return
        options = self.output_options()
        kind = options.format
        if kind in _AUDIO_ENCODERS:
            self._estimate_audio(options)
        elif kind in _ENCODERS:
            self._estimate_video(options)
        else:
            self.show_estimate("无法准确预估", "GIF 体积取决于画面变化、调色板和帧率。")

    def _estimate_audio(self, options):
        source_audio = isinstance(self.info, AudioInfo)
        if self.configuration_only:
            duration = self.info.duration if source_audio else self.info.audio_duration
            sample_rate, channels = self.info.sample_rate, self.info.channels
        else:
            duration = self.audio_duration()
            # 编辑导出把各段统一成 48 kHz 立体声，之后再应用用户的输出设置。
            sample_rate, channels = 48000, 2
        estimate = estimate_audio(options, duration=duration, sample_rate=sample_rate, channels=channels)
        self._display_size_estimate(estimate, comparable=source_audio)

    def _estimate_video(self, options):
        source = self.info
        if self.configuration_only:
            duration = source.duration
            audio_duration = (source.audio_duration or duration) if source.audio_stream is not None else 0
            fps = source.fps
            original_only = True
        else:
            duration, fps, audio_duration, original_only = self.video_timing()
        estimate = estimate_video(source, options, duration=duration, timeline_fps=fps,
                                  audio_duration=audio_duration, original_only=original_only)
        self._display_size_estimate(estimate)

    def _display_size_estimate(self, estimate, *, comparable=False):
        if estimate.size_bytes is None:
            self.show_estimate("无法准确预估", estimate.basis)
            return
        text = f"约 {_size_text(estimate.size_bytes)}"
        if estimate.upper_bytes is not None:
            text += f"–{_size_text(estimate.upper_bytes)}"
        self.show_estimate(text, estimate.basis, comparable_size=estimate.size_bytes if comparable else None)

    def uses_picture_estimate(self):
        if self.configuration_only:
            return isinstance(self.info, ImageInfo)
        return self.page_kind() == "image"

    def show_estimate(self, text, basis, *, comparable_size=None):
        source = self.info.size
        self.estimate_rows["source"].setText(_size_text(source))
        self.estimate_rows["output"].setText(text)
        self.estimate_card.setToolTip(basis)
        delta = self.estimate_rows["delta"]
        comparable = comparable_size is not None and source > 0
        delta.setVisible(comparable)
        self.estimate_delta_label.setVisible(comparable)
        if not comparable:
            delta.clear()
            return
        change = (comparable_size - source) / source * 100
        if abs(change) < .5:
            change_text, tone = "基本不变", "same"
        elif change < 0:
            change_text, tone = f"减少约 {-change:.0f}%", "down"
        else:
            change_text, tone = f"增加约 {change:.0f}%", "up"
        delta.setText(change_text)
        delta.setProperty("tone", tone)
        delta.style().unpolish(delta)
        delta.style().polish(delta)

    def update_output_location(self, *_):
        if not hasattr(self, "output_file_name"):
            return
        page = self.page_kind()
        kind = self.image_format.currentData() if page == "image" else self.format.currentData()
        extension = output_extension(kind or "mp4")
        if page == "image" and isinstance(self.info, VideoInfo):
            stem = f"{self.path.stem}_帧{self.picture_frame_number():06}"
        elif page == "image":
            stem = f"{self.path.stem}_图片"
        else:
            stem = f"{self.path.stem}_编辑"
        stem = self._get_output_stem() or stem
        if hasattr(self, "output_stem_ext"):
            self.output_stem_ext.setText(f".{extension}")
        name = f"{stem}.{extension}"
        self.output_file_name.setText(name)

    def mount(self, layout=None):
        if layout is None and not self.configuration_only:
            return False
        if layout is not None and self.configuration_only:
            return False
        self.configuration_only = layout is not None
        # 转换页复用的只是参数区：保存目录与「保存编辑结果」由主窗口自己的底栏承担
        if layout is not None:
            self._scroll.takeWidget()
            self.setStyleSheet(STUDIO_STYLE)
            layout.addWidget(self)
            self.show()
        else:
            self.setStyleSheet("")
            self._scroll.setWidget(self)
            self.show()
        return True

    def refresh_density(self):
        # 说明始终保留；窗口变矮时先收掉体积估算卡，避免右栏出现滚动条。
        compact_page = getattr(self, "configuration_only", False)
        self.mode_hint.setVisible(not compact_page)
        if hasattr(self, "quality"):
            self.quality.set_compact(compact_page)
        self.quality.set_compact(False)
        if hasattr(self, "estimate_card"):
            self._sync_export_rows()

    def _toggle_video_limit(self):
        self.limit_open = not self.limit_open
        toggle_text = "收起" if self.limit_open else "目标体积"
        self.limit_toggle.setText(toggle_text)
        self.audio_limit_toggle.setText(toggle_text)
        self._sync_export_rows()

    def _audio_quality_changed(self, kbps: int) -> None:
        """质量滑块的码率写回下拉，导出时仍走同一份 audio_bitrate 参数。"""
        if not kbps:
            return
        index = self.audio_bitrate.findData(int(kbps))
        if index >= 0 and index != self.audio_bitrate.currentIndex():
            blocked = self.audio_bitrate.blockSignals(True)
            self.audio_bitrate.setCurrentIndex(index)
            self.audio_bitrate.blockSignals(blocked)
        self.refresh_estimate()

    def _sync_export_rows(self):
        self._sync_format_pickers()
        kind = self.format.currentData()
        video = kind in _ENCODERS
        gif = kind == "gif"
        audio_only = kind in _AUDIO_ENCODERS
        self.export_fields.setRowVisible(self.mode_card, kind != "avi")
        self.export_fields.setRowVisible(self.quality_card, video and kind != "avi")
        picture_tab = self.page_kind() == "image"
        is_image = bool(getattr(self, "_panel_picture", False)) or picture_tab or (
            getattr(self, "configuration_only", False) and isinstance(self.info, ImageInfo))
        if hasattr(self, "audio_quality_card"):
            self.audio_quality_card.setVisible(audio_only)
            self.audio_quality.set_lossless(kind in {"wav", "flac", "pcm"})
        if hasattr(self, "image_quality_card"):
            self.image_quality_card.setVisible(is_image)
            picture_kind = self.image_format.currentData()
            self.image_quality.set_lossless(picture_kind in {"png", "gif_image", "bmp", "tiff"})
        self.estimate_card.setVisible(True)
        self.gif_row.setVisible(gif)
        self.audio_advanced.setVisible(audio_only)
        self.sample_row.setVisible(audio_only)
        self.channels_row.setVisible(audio_only)
        has_video_audio = video and isinstance(self.info, VideoInfo) and self.info.audio_stream is not None
        self.audio_codec_row.setVisible(has_video_audio or audio_only)
        self.audio_row.setVisible((has_video_audio or audio_only) and kind not in {"wav", "flac"} and not audio_only)
        self.limit_row.setVisible(False)
        self.limit_toggle.setVisible(False)
        self.audio_limit_toggle.setVisible(False)
        self._refresh_audio_codec()
        self.refresh_acceleration()
        self._sync_quality_encoder()
        if hasattr(self, 'estimate_card'):
            self.refresh_estimate()
        if getattr(self, "configuration_only", False):
            self.mode_hint.hide()
            if hasattr(self, "quality"):
                self.quality.set_compact(True)

    def _notify_settings(self, *_):
        if self.configuration_only or self.page_kind() == "video" or isinstance(self.info, (AudioInfo, ImageInfo)):
            self.settings_changed.emit()

    def output_options(self):
        if isinstance(self.info, ImageInfo):
            quality, limit, compression = self.picture_encoding()
            return Export(self.image_format.currentData(), self.directory, max_edge=int(self.resolution.currentData() or 0),
                          target_bytes=limit, quality=quality, png_compression=compression,
                          output_stem=self._get_output_stem())
        return Export(self.format.currentData(), self.directory,
                      small_gif=bool(self.gif_profile.currentData()),
                      max_edge=max(0, int(self.export_edge.currentData() or 0)),
                      target_bytes=self.export_limit.value() * 1000,
                      video_acceleration=self.acceleration.currentData() or "cpu",
                      output_fps=int(self.output_fps.currentData() or 0), quality=self.quality.value(),
                      audio_bitrate=int(self.audio_bitrate.currentData() or 0),
                      audio_sample_rate=int(self.audio_sample_rate.currentData() or 0),
                      audio_channels=int(self.audio_channels.currentData() or 0),
                      output_stem=self._get_output_stem())

    def apply_output_options(self, options, *, apply_image_quality=False):
        with QSignalBlocker(self):
            self.set_output_format(options.format)
            image = isinstance(self.info, ImageInfo)
            edge_combo = self.resolution if image else self.export_edge
            edge_index = edge_combo.findData(options.max_edge)
            if edge_index < 0 and options.max_edge > 0:
                edge_index = edge_combo.count() if image else edge_combo.count() - 1
                edge_combo.insertItem(edge_index, f"最长边 {options.max_edge} px", options.max_edge)
            edge_combo.setCurrentIndex(max(0, edge_index))
            if image:
                self.size.setValue(options.target_bytes // 1000)
                if apply_image_quality and options.quality and hasattr(self, "image_quality"):
                    self.image_quality.setValue(options.quality)
                if options.target_bytes:
                    self.picture_preset.setCurrentIndex(2)
            else:
                self.export_limit.setValue(options.target_bytes // 1000)
                for combo, value in ((self.output_fps, options.output_fps), (self.acceleration, options.video_acceleration),
                                     (self.gif_profile, options.small_gif), (self.audio_bitrate, options.audio_bitrate),
                                     (self.audio_sample_rate, options.audio_sample_rate), (self.audio_channels, options.audio_channels)):
                    if (index := combo.findData(value)) >= 0:
                        combo.setCurrentIndex(index)
                if options.quality:
                    self.quality.setValue(options.quality)
            self.directory = options.directory or self.path.parent
            self.directory_path.set_path(str(self.directory))
            self.reset_path.setVisible(self.directory != self.path.parent)
            self.update_output_location()
            if image:
                self.picture_inputs_changed.emit()
            else:
                self.refresh_estimate()

    def _custom_video_edge(self, index):
        if self.export_edge.itemData(index) != -1:
            self._last_video_edge = index
            return
        edge, accepted = QInputDialog.getInt(self, "输出分辨率", "最长边（像素）", 1280, 16, 8192)
        if accepted:
            target = self.export_edge.findData(edge)
            if target < 0:
                self.export_edge.insertItem(index, f"最长边 {edge} px", edge)
                target = index
            self.export_edge.setCurrentIndex(target)
            self._last_video_edge = target
        else:
            self.export_edge.setCurrentIndex(getattr(self, "_last_video_edge", 0))

    def choose_directory(self):
        if not self.task_running():
            selected = QFileDialog.getExistingDirectory(self.window(), "选择保存目录", str(self.directory))
            if selected:
                self._set_output_directory(Path(selected))

    def _reset_output_directory(self):
        self._set_output_directory(self.path.parent)

    def _set_output_directory(self, directory):
        self.directory = directory
        self.directory_path.set_path(str(directory))
        self.reset_path.setVisible(directory != self.path.parent)
        self.directory_changed.emit(directory)

    def _sync_image_quality(self) -> None:
        """图片格式变化时刷新质量卡：无损格式只给说明，有损格式给百分比滑轨。"""
        if not hasattr(self, "image_quality"):
            return
        kind = self.image_format.currentData()
        lossless = kind in {"png", "gif_image", "bmp", "tiff"}
        if self.image_quality.value() and not lossless:
            self.image_quality.setValue(self.image_quality.value())
        self.image_quality.set_lossless(lossless)
        self.image_quality_card.setVisible(True)

    def picture_encoding(self):
        index = self.picture_preset.currentIndex()
        _, quality, png_compression, _ = _PICTURE_PRESETS[index]
        if hasattr(self, "image_quality") and self.image_format.currentData() != "png":
            chosen = self.image_quality.value()
            if chosen:
                quality = int(chosen)
        limit = self.size.value() * 1000 if index == 2 else 0
        return quality, limit, png_compression

    def update_picture_dimensions(self):
        source = self.picture_source()
        if source is None:
            if hasattr(self, "picture_dimensions"):
                self.picture_dimensions.setText("等待视频帧就绪")
            return
        _, _, _, width, height = source
        if width < 1 or height < 1:
            self.picture_dimensions.setText("未知")
            return
        edge = int(self.resolution.currentData() or 0)
        ratio = edge / max(width, height) if edge else 1.0
        if not self.configuration_only:
            ratio = min(1.0, ratio)
        out_width = max(1, round(width * ratio))
        out_height = max(1, round(height * ratio))
        if self.image_format.currentData() == "ico":
            out_width = out_height = 256
        self.picture_dimensions.setText(f"{out_width} × {out_height} 像素")

    def _picture_input_changed(self, *_):
        self.picture_inputs_changed.emit()

    def refresh_mode(self):
        self._scroll.verticalScrollBar().setValue(0)
        picture = self.page_kind() == "image"
        self.picture_group.setVisible(isinstance(self.info, ImageInfo) if self.configuration_only else picture)
        self.export_group.setVisible(not isinstance(self.info, ImageInfo) if self.configuration_only
                                     else self.page_kind() in {"video", "audio"})
        self.directory_group.setVisible(self.configuration_only or self.page_kind() != "details")
        self._panel_picture = picture
        self._load_formats()
        self._sync_export_rows()
        self.picture_inputs_changed.emit()

    def initialize_options(self):
        self.update_output_location()
        self.refresh_acceleration()
        self._fill_presets()
        self._refresh_audio_codec()
        self.refresh_estimate()
        self._sync_preset()
        for combo in (self.format, self.export_edge, self.output_fps, self.acceleration, self.audio_bitrate,
                      self.image_format, self.resolution, self.gif_profile,
                      self.audio_sample_rate, self.audio_channels):
            combo.currentIndexChanged.connect(self._notify_settings)
        for signal in (self.export_limit.valueChanged, self.size.valueChanged,
                       self.picture_preset.currentChanged, self.quality.changed):
            signal.connect(self._notify_settings)

    def bind_output_name(self, name, on_edit):
        layout = self.directory_group.layout()
        hint = QLabel("输出文件名")
        hint.setObjectName("paramLabel")
        layout.addWidget(hint)
        line = QWidget()
        fields = QHBoxLayout(line)
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(6)
        self.output_stem_edit = QLineEdit()
        self.output_stem_edit.setPlaceholderText("输出文件名")
        self.output_stem_edit.setMinimumHeight(30)
        self.output_stem_edit.setText(name)
        self.output_stem_edit.textEdited.connect(on_edit)
        fields.addWidget(self.output_stem_edit, 1)
        self.output_stem_ext = QLabel("")
        self.output_stem_ext.setMinimumWidth(46)
        fields.addWidget(self.output_stem_ext)
        layout.addWidget(line)
        self.update_output_location()


    def _build_mode(self, export, cells):
        self.format = _combo()
        self.preset = _combo()
        for title, edge, crf in (("轻量 · 720P", 1280, 28), ("均衡（推荐）", 1920, 23),
                                 ("高画质 · 原始分辨率", 0, 18)):
            self.preset.addItem(title, (edge, crf))
            self.preset.setItemData(self.preset.count() - 1, f"CRF {crf} · {'原始尺寸' if not edge else f'最长边 {edge} px'}",
                                    Qt.ItemDataRole.ToolTipRole)
        self.preset.addItem("自定义", None)
        self.preset.setCurrentIndex(1)
        self.preset.setMinimumWidth(150)
        self.preset.setToolTip("一次套用分辨率与质量；手动改参数后会自动落到「自定义」。")
        self.preset.currentIndexChanged.connect(self._apply_preset)
        self.preset.currentIndexChanged.connect(self._update_mode_hint)

        self.mode_card = QFrame()
        self.mode_card.setObjectName("exportModeCard")
        self.mode_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        mode = QVBoxLayout(self.mode_card)
        mode.setContentsMargins(10, 6, 10, 6)
        mode.setSpacing(3)
        mode_head = QHBoxLayout()
        mode_head.setSpacing(8)
        mode_head.addWidget(_icon_label("layers", 22, C.accent.name()))
        mode_head.addWidget(_text("导出模式", name="cardTitle"))
        mode_head.addStretch(1)
        mode_head.addWidget(self.preset)
        mode.addLayout(mode_head)
        self.mode_hint = _text("快速应用一组分辨率与质量参数", name="cardHint")
        self.mode_hint.setContentsMargins(29, 0, 0, 0)
        self.mode_hint.setWordWrap(True)
        mode.addWidget(self.mode_hint)
        self._update_mode_hint()
        cells[self.preset] = self.mode_card
        export.addWidget(self.mode_card)

    def _build_video_fields(self, export, cells):
        basic = QVBoxLayout()
        basic.setContentsMargins(0, 0, 0, 0)
        basic.setSpacing(2)
        self.container = _combo()
        self.container.setToolTip("输出容器：决定文件后缀与兼容性")
        self.codec = _combo()
        self.codec.setToolTip("视频编码器：H.264 兼容性最好，H.265 体积更小")
        self.container.currentIndexChanged.connect(self._pick_container)
        self.codec.currentIndexChanged.connect(self._pick_codec)
        self.format.hide()
        format_holder = QWidget()
        format_holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        format_line = QHBoxLayout(format_holder)
        format_line.setContentsMargins(0, 0, 0, 0)
        format_line.setSpacing(6)
        format_line.addWidget(self.container, 1)
        format_line.addWidget(self.format, 1)
        self.format_row = _parameter_row(cells, "输出格式", format_holder)
        self.codec_row = _parameter_row(cells, "视频编码", self.codec)
        basic.addWidget(self.format_row)
        basic.addWidget(self.codec_row)
        self.acceleration = _combo()
        self.acceleration.setAccessibleName("硬件加速")
        self.acceleration.setToolTip("只列出当前 FFmpeg 构建包含的硬件编码器；设备或驱动不可用时会自动回退 CPU。")
        self.acceleration.currentIndexChanged.connect(self._remember_acceleration)
        self.acceleration_label = _text("硬件加速")
        self.acceleration_row = _parameter_row(cells, "硬件加速", self.acceleration)
        basic.addWidget(self.acceleration_row)
        self.export_edge = _combo()
        for title, edge in (("原始分辨率", 0), *VIDEO_RESOLUTIONS):
            self.export_edge.addItem(f"{_friendly_edge_title(edge)}", edge)
            self.export_edge.setItemData(self.export_edge.count() - 1,
                                          f"最长边 {edge} px，保持比例" if edge else "保留视频的原始像素尺寸",
                                          Qt.ItemDataRole.ToolTipRole)
        for edge in (480, 256):
            self.export_edge.addItem(f"最长边 {edge} px", edge)
        self.export_edge.addItem("自定义最长边…", -1)
        self.export_edge.activated.connect(self._custom_video_edge)
        self.export_edge.setToolTip("按原画面比例缩放，竖屏视频保持竖屏")
        self.export_edge.currentIndexChanged.connect(self.update_export_dimensions)
        self.resolution_row = _parameter_row(cells, "分辨率", self.export_edge)
        basic.addWidget(self.resolution_row)
        self.dimension_note = _text("", name="paramHint", muted=True)
        self.export_dimensions = self.dimension_note  # 兼容旧属性名
        self.resolution_row.layout().addWidget(self.dimension_note)
        self.dimension_note.setVisible(False)
        self.output_fps = _combo()
        fps_label = f"原帧率 · {self.info.fps:g} FPS" if isinstance(self.info, VideoInfo) and self.info.fps else "保持原帧率"
        self.output_fps.addItem(fps_label, 0)
        for rate in VIDEO_FRAME_RATES:
            self.output_fps.addItem(f"{rate} FPS", rate)
        self.output_fps.setToolTip("设置导出视频的每秒帧数，保持播放速度")
        self.output_fps.currentIndexChanged.connect(self.refresh_estimate)
        self.fps_row = _parameter_row(cells, "帧率", self.output_fps)
        basic.addWidget(self.fps_row)
        cells[self.export_edge] = self.resolution_row
        cells[self.dimension_note] = self.dimension_note
        export.addLayout(basic)

    def _build_quality(self, export, cells):
        self.quality = QualityRow("质量微调", 18, 34, 23,
                                  hint="CRF 越小画质越高，文件通常越大。",
                                  icon_name="sliders", crf_wheel=True)
        self.quality.setToolTip("CRF 越小越清晰、文件越大；22–26 是日常分享的常用区间。")
        self.estimate_throttle = QTimer(self)
        self.estimate_throttle.setSingleShot(True)
        self.estimate_throttle.setInterval(120)
        self.estimate_throttle.timeout.connect(self.refresh_estimate)
        self.quality.changed.connect(self._schedule_estimate_tick)
        self.quality.settled.connect(self.estimate_throttle.stop)
        self.quality.settled.connect(self.refresh_estimate)
        self.quality.settled.connect(self._sync_preset)
        self.export_edge.currentIndexChanged.connect(self._sync_preset)
        self.quality_card = QFrame()
        self.quality_card.setObjectName("qualityCard")
        self.quality_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        quality_layout = QVBoxLayout(self.quality_card)
        quality_layout.setContentsMargins(10, 6, 10, 6)
        quality_layout.setSpacing(1)
        quality_layout.addWidget(self.quality)
        cells[self.quality] = self.quality_card
        export.addWidget(self.quality_card)

    def _build_audio_fields(self, export, cells):
        audio_box = QVBoxLayout()
        audio_box.setContentsMargins(0, 0, 0, 0)
        audio_box.setSpacing(2)
        self.audio_codec_text = _text("AAC", name="paramValue")
        self.audio_codec_holder = QWidget()
        self.audio_codec_holder.setObjectName("codecDisplay")
        self.audio_codec_holder.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        codec_line = QHBoxLayout(self.audio_codec_holder)
        codec_line.setContentsMargins(9, 0, 9, 0)
        self.audio_codec_holder.setMinimumHeight(30)
        codec_line.addWidget(self.audio_codec_text)
        codec_line.addStretch(1)
        self.audio_codec_row = _parameter_row(cells, "音频编码", self.audio_codec_holder)
        audio_box.addWidget(self.audio_codec_row)
        self.audio_bitrate = _combo()
        for rate in (96, 128, 192, 256, 320):
            self.audio_bitrate.addItem(f"{rate} kbps", rate)
        self.audio_bitrate.setCurrentIndex(self.audio_bitrate.findData(192))
        self.audio_bitrate.setToolTip("只影响带音轨的视频；无损容器不受影响。")
        self.audio_bitrate.currentIndexChanged.connect(self.refresh_estimate)
        self.audio_row = _parameter_row(cells, "音频码率", self.audio_bitrate)
        audio_box.addWidget(self.audio_row)
        self.audio_sample_rate = _combo()
        self.audio_sample_rate.addItem("保持源采样率", 0)
        for rate in (22050, 44100, 48000):
            self.audio_sample_rate.addItem(f"{rate / 1000:g} kHz", rate)
        self.audio_channels = _combo()
        for title, count in (("保持源声道", 0), ("单声道", 1), ("立体声", 2)):
            self.audio_channels.addItem(title, count)
        self.sample_row = _parameter_row(cells, "采样率", self.audio_sample_rate)
        self.channels_row = _parameter_row(cells, "声道", self.audio_channels)
        self.audio_sample_rate.currentIndexChanged.connect(self.refresh_estimate)
        self.audio_channels.currentIndexChanged.connect(self.refresh_estimate)
        self.audio_advanced = QWidget()
        self.audio_advanced.setObjectName("audioAdvancedOptions")
        audio_advanced_layout = QVBoxLayout(self.audio_advanced)
        audio_advanced_layout.setContentsMargins(0, 0, 0, 0)
        audio_advanced_layout.setSpacing(2)
        audio_advanced_layout.addWidget(self.sample_row)
        audio_advanced_layout.addWidget(self.channels_row)
        audio_box.addWidget(self.audio_advanced)
        cells[self.audio_bitrate] = self.audio_row
        export.addLayout(audio_box)

        self.audio_quality = QualityRow(
            "质量微调", values=[320, 256, 192, 128, 96], unit="kbps", value=192,
            hint="码率越高通常音质越好，文件体积越大。", icon_name="wave",
            lossless_hint="WAV / FLAC 为无损格式，不提供有损质量调节。")
        self.audio_quality.changed.connect(self._schedule_estimate_tick)
        self.audio_quality.settled.connect(self._audio_quality_changed)
        self.audio_quality_card = QFrame()
        self.audio_quality_card.setObjectName("qualityCard")
        self.audio_quality_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        audio_quality_layout = QVBoxLayout(self.audio_quality_card)
        audio_quality_layout.setContentsMargins(11, 10, 11, 10)
        audio_quality_layout.setSpacing(6)
        audio_quality_layout.addWidget(self.audio_quality)
        self.audio_quality_card.hide()
        export.addWidget(self.audio_quality_card)
        return audio_box

    def _build_estimate(self, cells):
        self.estimate_card = QFrame()
        self.estimate_card.setObjectName("estimatedSizeCard")
        self.estimate_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        estimate = QVBoxLayout(self.estimate_card)
        estimate.setContentsMargins(0, 0, 0, 0)
        estimate.setSpacing(0)
        estimate_head = QFrame()
        estimate_head.setObjectName("estimatedSizeHeader")
        estimate_head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.estimate_head = estimate_head
        estimate_head.setFixedHeight(24)
        head_line = QHBoxLayout(estimate_head)
        head_line.setContentsMargins(12, 0, 12, 0)
        head_line.setSpacing(7)
        head_line.addWidget(_icon_label("layers", 17))
        head_line.addWidget(_text("预计输出体积", name="cardTitle"))
        head_line.addStretch(1)
        head_line.addWidget(self.limit_toggle)
        estimate.addWidget(estimate_head)
        self.estimate_rows = {}
        body = QGridLayout()
        body.setContentsMargins(10, 3, 10, 5)
        body.setHorizontalSpacing(8)
        body.setVerticalSpacing(2)
        body.setColumnStretch(0, 1)
        body.setColumnStretch(1, 1)
        for row_index, (key, title) in enumerate((("source", "原始大小"), ("output", "预计输出"),
                                                  ("delta", "与源文件相比"))):
            label = _text(title, name="estimateLabel")
            value = _text("—", name="estimateValueStrong" if key == "output" else "estimateValue")
            if key == "delta":
                self.estimate_delta_label = label
                value.setObjectName("estimateDelta")
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.estimate_rows[key] = value
            body.addWidget(label, row_index, 0)
            body.addWidget(value, row_index, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        estimate.addLayout(body)
        cells[self.estimate_card] = self.estimate_card

    def _build_directory(self):
        self.directory_group = QFrame()
        self.directory_group.setObjectName("saveDirectoryCard")
        self.directory_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        directory = QVBoxLayout(self.directory_group)
        directory.setContentsMargins(0, 0, 0, 0)
        directory.setSpacing(5)
        self.output_file_name = _text("正在准备输出文件名…", name="cardHint")
        self.output_file_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.output_file_name.setToolTip("如果同名文件已存在，程序会自动追加序号，不会覆盖原文件。")
        self.reset_path = _text_button("恢复源目录", self._reset_output_directory)
        self.reset_path.setVisible(self.directory != self.path.parent)
        directory.addWidget(_caption("folder", "保存目录", self.reset_path))
        path_card = QFrame()
        path_card.setObjectName("outputPathCard")
        path_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        path_card.setFixedHeight(36)
        card_layout = QHBoxLayout(path_card)
        card_layout.setContentsMargins(10, 0, 6, 0)
        card_layout.setSpacing(8)
        self.output_icon = _icon_label("folder", 18)
        self.output_icon.setFixedSize(28, 28)
        self.output_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.output_icon)
        self.directory_path = DirectoryPathEdit(str(self.directory))
        card_layout.addWidget(self.directory_path, 1)
        self.directory_path.clicked.connect(self.choose_directory)
        self.directory_path.setToolTip("单击选择输出目录")
        directory.addWidget(path_card)
        self.output_file_name.hide()

    def _build_image_quality(self):
        self.image_quality = QualityRow(
            "质量微调", values=[100, 90, 80, 60, 40], unit="%", value=90,
            hint="质量越高通常细节越清晰，文件体积越大。", icon_name="image",
            lossless_hint="PNG 为无损格式，不提供有损质量调节。")
        self.image_quality.changed.connect(self._picture_input_changed)
        self.image_quality_card = QFrame()
        self.image_quality_card.setObjectName("qualityCard")
        self.image_quality_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        image_quality_layout = QVBoxLayout(self.image_quality_card)
        image_quality_layout.setContentsMargins(11, 10, 11, 10)
        image_quality_layout.setSpacing(6)
        image_quality_layout.addWidget(self.image_quality)
        self.image_quality_card.hide()
        # 图片页展示的是 picture_group，质量卡挂在它内部才看得见
        self.picture_group.layout().addWidget(self.image_quality_card)

    def _build_size_limit(self, audio_box, cells):
        self.export_limit = _spin()
        self.export_limit.setRange(0, 10_000_000)
        self.export_limit.setSingleStep(100)
        self.export_limit.setSuffix(" KB")
        self.export_limit.setSpecialValueText("不限制")
        self.export_limit.valueChanged.connect(self.refresh_estimate)
        self.limit_toggle = QPushButton("目标体积")
        self.limit_toggle.setObjectName("estimateLimitToggle")
        self.limit_toggle.setAutoDefault(False)
        self.limit_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.limit_toggle.setFixedHeight(20)
        self.limit_toggle.setToolTip("可选设置输出体积上限。编码器会尽量接近目标大小，实际结果可能有差异。")
        self.limit_toggle.clicked.connect(self._toggle_video_limit)
        self.audio_limit_toggle = QPushButton("目标体积")
        self.audio_limit_toggle.setObjectName("estimateLimitToggle")
        self.audio_limit_toggle.setAutoDefault(False)
        self.audio_limit_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.audio_limit_toggle.setFixedHeight(20)
        self.audio_limit_toggle.setToolTip(self.limit_toggle.toolTip())
        self.audio_limit_toggle.clicked.connect(self._toggle_video_limit)
        self.audio_limit_toggle.hide()
        audio_box.addWidget(self.audio_limit_toggle)
        self.audio_limit_toggle.hide()
        self.limit_toggle.hide()
        self.limit_row = _parameter_row(cells, "目标体积", self.export_limit)
        self.limit_open = False
