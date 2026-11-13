"""Batch defaults, format capabilities and the conversion settings dialog."""
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QVBoxLayout, QWidget

from .conversion_inspector import field, icon_button, icon_label, section
from .media import (AudioInfo, Export, ImageInfo, LOSSLESS_AUDIO_FORMATS, MediaInfo, VideoInfo,
                    VIDEO_FRAME_RATES, VIDEO_RESOLUTIONS, hardware_acceleration_choices, output_extension)
from .motion import Button, Combo, DirectoryButton, QualityRow, SpinBox
from .widgets import ElidedLabel, label


VIDEO_FORMATS = (("mp4", "MP4 · H.264"), ("mp4_hevc", "MP4 · H.265"),
                 ("mkv_hevc", "MKV · H.265"), ("mkv", "MKV · H.264"),
                 ("webm", "WebM · VP9"), ("mov", "MOV · H.264"), ("avi", "AVI · MPEG-4"), ("gif", "GIF · 动图"))
AUDIO_FORMATS = (("mp3", "MP3"), ("m4a", "M4A · AAC"), ("wav", "WAV · PCM"),
                 ("flac", "FLAC · 无损"), ("opus", "Opus"), ("ogg", "OGG · Vorbis"))
IMAGE_FORMATS = (("png", "PNG · 无损"), ("jpg", "JPEG · 照片"), ("webp", "WebP"), ("avif", "AVIF"),
                 ("tiff", "TIFF"), ("bmp", "BMP"), ("gif_image", "GIF · 静态"), ("ico", "ICO · 图标"), ("pdf", "PDF · 单页"))
RESIZABLE_FORMATS = {key for key, _ in VIDEO_FORMATS + IMAGE_FORMATS}
FRAME_RATE_FORMATS = {key for key, _ in VIDEO_FORMATS if key != "gif"}


def formats_for(info: MediaInfo | None):
    if isinstance(info, VideoInfo):
        if info.audio_stream is not None:
            return VIDEO_FORMATS + tuple((key, f"提取音频 · {key.upper()}") for key, _ in AUDIO_FORMATS)
        return VIDEO_FORMATS
    if isinstance(info, AudioInfo):
        return AUDIO_FORMATS
    if isinstance(info, ImageInfo) and info.codec != "PDF":
        return IMAGE_FORMATS
    return ()


class BatchSettings(QDialog):
    name_edited = Signal(str)
    quality_changed = Signal(int)
    directory_changed = Signal()

    def __init__(self, parent, ffmpeg, selected):
        super().__init__(parent)
        self.ffmpeg = ffmpeg
        self.selected = selected
        self.output_directory = None
        self.setWindowTitle("批量输出参数")
        self.setMinimumWidth(390)
        batch_body = QVBoxLayout(self)
        batch_body.addWidget(label("将下面的通用参数应用到队列中所有兼容文件。", muted=True))

        # 模块 B：参数分组，两列并排，默认尺寸下不需要滚动
        fields = QGridLayout()
        fields.setHorizontalSpacing(10)
        fields.setVerticalSpacing(8)
        fields.setColumnStretch(0, 1)
        fields.setColumnStretch(1, 1)

        self.resolution = Combo()
        self.resolution.addItem("原始尺寸", 0)
        for title, edge in VIDEO_RESOLUTIONS:
            self.resolution.addItem(title, edge)
        for edge in (480, 256):
            self.resolution.addItem(f"最长边 {edge} px", edge)
        self.resolution.addItem("自定义最长边…", -1)
        self.max_edge = 0
        self.resolution.activated.connect(self._choose_resolution)
        self.resolution.setToolTip("分辨率预设以标准横屏尺寸为参考，按原画面比例缩放。")
        self.resolution_field = field("分辨率", self.resolution)
        fields.addWidget(self.resolution_field, 0, 0)

        self.output_fps = Combo()
        self.output_fps.addItem("保持原帧率", 0)
        for rate in VIDEO_FRAME_RATES:
            self.output_fps.addItem(f"{rate} FPS", rate)
        self.output_fps.setToolTip("适用于队列中的视频输出，保持播放速度。GIF 帧率由 GIF 设置控制。")
        self.output_fps_field = field("输出帧率", self.output_fps)
        fields.addWidget(self.output_fps_field, 0, 1)

        self.acceleration = Combo()
        self.acceleration.setAccessibleName("视频编码加速")
        self.acceleration.setToolTip("只列出当前 FFmpeg 构建支持的硬件编码器；其他格式使用 CPU 软件编码。")
        self.acceleration_field = field("视频编码加速", self.acceleration)
        fields.addWidget(self.acceleration_field, 1, 0)

        self.target_size = SpinBox()
        self.target_size.setRange(0, 10_000_000)
        self.target_size.setSingleStep(100)
        self.target_size.setSuffix(" KB")
        self.target_size.setSpecialValueText("不限")
        self.target_size.setMinimumHeight(28)
        self.target_size.setToolTip("每个输出文件的体积目标。动图和图片可能缩小尺寸。WAV 和 FLAC 按原音质导出。")
        self.target_size_field = field("目标体积", self.target_size)
        fields.addWidget(self.target_size_field, 1, 1)

        self.gif_preset = Combo()
        self.gif_preset.addItem("小体积 · 8 FPS", True)
        self.gif_preset.addItem("清晰 · 12 FPS", False)
        self.gif_preset.setToolTip("选择动图的帧率和默认尺寸。也可在分辨率里指定最长边。")
        self.gif_field = field("GIF 设置", self.gif_preset)
        fields.addWidget(self.gif_field, 2, 0, 1, 2)

        self.quality = QualityRow("质量微调", 18, 34, 23,
                                  hint="CRF 越小画质越高，文件通常越大。",
                                  icon_name="sliders")
        self.quality.changed.connect(self.quality_changed.emit)
        self.quality.settled.connect(self.quality_changed.emit)
        self.quality.setToolTip("CRF 越小越清晰、文件越大；22–26 是日常分享的常用区间。\n"
                                "设了「目标体积」时以体积优先，CRF 只作为起点。")
        fields.addWidget(self.quality, 3, 0, 1, 2)
        batch_body.addLayout(fields)
        self._build_location(batch_body)

    def _build_location(self, batch_body):
        self.output_stem_label = QLabel("输出文件名")
        self.output_stem_label.setObjectName("fieldLabel")
        self.output_stem_row = QWidget()
        stem_layout = QHBoxLayout(self.output_stem_row)
        stem_layout.setContentsMargins(0, 0, 0, 0)
        stem_layout.setSpacing(6)
        self.output_stem_edit = QLineEdit()
        self.output_stem_edit.setPlaceholderText("输出文件名")
        self.output_stem_edit.setMinimumHeight(30)
        self.output_stem_edit.textEdited.connect(self.name_edited.emit)
        stem_layout.addWidget(self.output_stem_edit, 1)
        self.output_stem_ext = QLabel("")
        self.output_stem_ext.setObjectName("outputStemExt")
        self.output_stem_ext.setMinimumWidth(42)
        stem_layout.addWidget(self.output_stem_ext)

        # 模块 C：输出文件——文件名一行，目录一行
        self.output_location = QWidget()
        self.output_location.setObjectName("outputLocationGroup")
        output_layout = QVBoxLayout(self.output_location)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(6)
        output_heading = QHBoxLayout()
        output_heading.setSpacing(6)
        output_heading.addWidget(section("输出文件"))
        output_heading.addStretch(1)
        self.reset_destination = Button("恢复源目录")
        self.reset_destination.setProperty("flat", True)
        self.reset_destination.clicked.connect(self._reset_directory)
        output_heading.addWidget(self.reset_destination)
        output_layout.addLayout(output_heading)
        self.output_name = ElidedLabel("添加文件后显示输出文件名", mode=Qt.TextElideMode.ElideMiddle)
        self.output_name.setObjectName("outputFileName")
        self.output_name.setProperty("muted", True)
        name_card = QFrame()
        name_card.setObjectName("outputLocationCard")
        name_row = QHBoxLayout(name_card)
        name_row.setContentsMargins(9, 4, 5, 4)
        name_row.setSpacing(6)
        folder_icon = icon_label("folder", 18)
        folder_icon.setFixedSize(28, 28)
        folder_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_row.addWidget(folder_icon)
        self.destination = DirectoryButton("默认使用各文件的源目录")
        self.destination.setObjectName("outputPath")
        self.destination.setProperty("flat", True)
        self.destination.clicked.connect(self._choose_directory)
        name_row.addWidget(self.destination, 1)
        self.pick_button = icon_button("folder", self._choose_directory, "选择输出文件夹")
        name_row.addWidget(self.pick_button)
        output_layout.addWidget(name_card)
        stem_head = QHBoxLayout()
        stem_head.setSpacing(6)
        stem_head.addWidget(self.output_stem_label)
        stem_head.addStretch(1)
        output_layout.addLayout(stem_head)
        output_layout.addWidget(self.output_stem_row)
        batch_body.addWidget(self.output_location)
        apply_batch = Button("应用到队列")
        apply_batch.setObjectName("primary")
        apply_batch.clicked.connect(self.accept)
        batch_body.addWidget(apply_batch)


    def _choose_resolution(self, index):
        edge = self.resolution.itemData(index)
        if edge == -1:
            edge, ok = QInputDialog.getInt(self, "自定义分辨率", "最长边（像素）",
                                           self.max_edge or 1920, 16, 8192)
            if not ok:
                edge = self.max_edge
            if self.resolution.findData(edge) < 0:
                self.resolution.insertItem(self.resolution.count() - 1, f"最长边 {edge} px", edge)
            self.resolution.setCurrentIndex(self.resolution.findData(edge))
        self.max_edge = edge

    def _choose_directory(self) -> None:
        job = self.selected()
        current = self.output_directory or (job.path.parent if job else Path.home())
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹", str(current))
        if directory:
            self.output_directory = Path(directory)
            self.directory_changed.emit()

    def _reset_directory(self) -> None:
        self.output_directory = None
        self.directory_changed.emit()

    def refresh_name(self, job) -> None:
        ext = output_extension(job.output_format or "mp4")
        self.output_stem_ext.setText(f".{ext}")
        stem = job.output_stem or job.path.stem
        directory = self.output_directory or job.path.parent
        name = f"{stem}.{ext}"
        target = directory / name
        self.output_stem_edit.setToolTip(str(target))
        self.output_name.set_full_text(name)
        self.output_name.setToolTip(str(target))

    def show_selection(self, job) -> None:
        directory = self.output_directory or (job.path.parent if job else None)
        self.destination.set_path(str(directory) if directory else "默认使用各文件的源目录")
        if job and job.output:
            output_name = job.output.name
        elif job and job.output_format:
            output_name = f"{job.output_stem or job.path.stem}.{output_extension(job.output_format or "mp4")}"
        else:
            output_name = "选择文件并设置格式后显示输出文件名"
        self.output_stem_edit.blockSignals(True)
        self.output_stem_edit.setText((job.output_stem or job.path.stem) if job else "")
        self.output_stem_edit.blockSignals(False)
        self.output_stem_ext.setText(f".{output_extension(job.output_format or "mp4")}" if job and job.output_format else "")
        self.output_name.set_full_text(output_name)
        self.output_name.setToolTip(
            str(job.output) if job and job.output else
            str(directory / output_name) if directory and job and job.output_format else output_name
        )

    def edit_defaults(self):
        combos = (self.resolution, self.output_fps, self.acceleration, self.gif_preset)
        before = tuple(combo.currentData() for combo in combos)
        target, quality, directory = self.target_size.value(), self.quality.value(), self.output_directory
        if self.exec() == QDialog.DialogCode.Accepted:
            return True
        for combo, value in zip(combos, before):
            combo.setCurrentIndex(combo.findData(value))
        self.target_size.setValue(target)
        self.quality.setValue(quality)
        self.output_directory = directory
        return False

    def update_availability(self, formats, video_formats, *, busy, has_selection):
        previous = self.acceleration.currentData() or "cpu"
        by_format = [dict(hardware_acceleration_choices(self.ffmpeg, kind))
                     for kind in video_formats] if self.ffmpeg else []
        choices = [("cpu", "CPU 软件编码")]
        if by_format:
            choices.extend((key, title) for key, title in by_format[0].items()
                           if key != "cpu" and all(key in available for available in by_format[1:]))
        with QSignalBlocker(self.acceleration):
            self.acceleration.clear()
            for key, title in choices:
                self.acceleration.addItem(title, key)
            self.acceleration.setCurrentIndex(max(0, self.acceleration.findData(previous)))

        self._set_field(self.acceleration, self.acceleration_field, bool(video_formats), not busy)
        self._set_field(self.output_fps, self.output_fps_field, bool(formats & FRAME_RATE_FORMATS), not busy)
        resizable = bool(formats & RESIZABLE_FORMATS)
        self._set_field(self.resolution, self.resolution_field, not formats or resizable, not busy and resizable)
        self.gif_field.setVisible("gif" in formats)
        self.gif_preset.setEnabled(not busy)
        self.target_size.setEnabled(not busy and bool(formats - LOSSLESS_AUDIO_FORMATS))
        self.output_location.setVisible(has_selection)
        self.destination.setEnabled(not busy)
        self.reset_destination.setVisible(self.output_directory is not None)
        self.reset_destination.setEnabled(not busy)

    @staticmethod
    def _set_field(control, holder, visible, enabled):
        control.setVisible(visible)
        holder.setVisible(visible)
        control.setEnabled(visible and enabled)

    def options_for(self, job):
        kind = job.output_format
        supported = {key for key, _ in hardware_acceleration_choices(self.ffmpeg, kind)}
        acceleration = self.acceleration.currentData() or "cpu"
        return Export(kind, self.output_directory, small_gif=bool(self.gif_preset.currentData()),
                      max_edge=max(0, self.resolution.currentData() or 0) if kind in RESIZABLE_FORMATS else 0,
                      target_bytes=self.target_size.value() * 1000,
                      video_acceleration=acceleration if acceleration in supported else "cpu",
                      output_fps=int(self.output_fps.currentData() or 0) if kind in FRAME_RATE_FORMATS else 0,
                      quality=self.quality.value() if isinstance(job.info, VideoInfo) else 85 if isinstance(job.info, ImageInfo) else 0,
                      audio_bitrate=192, png_compression=7 if isinstance(job.info, ImageInfo) else 6)
