"""Translate preview export actions into the existing cancellable media jobs."""
from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from .editing import render_edit, save_picture
from .media import Export, _AUDIO_ENCODERS, _ENCODERS, _IMAGE_FORMATS, hardware_acceleration_choices


class EditorExport:
    """Capture the edit state, selected output options and destination before scheduling an export."""

    def __init__(self, owner, path, session, settings, tools, *, canvas_for, run_task, progress,
                 notice, worker_notice, saved, output_stem):
        self.owner = owner
        self.path, self.session, self.settings, self.tools = path, session, settings, tools
        self.canvas_for, self.run_task, self.progress = canvas_for, run_task, progress
        self.notice, self.worker_notice, self.saved = notice, worker_notice, saved
        self._get_output_stem = output_stem

    @staticmethod
    def _target_from_dialog(filename, selected_filter, extensions):
        if not filename:
            return None, None
        target = Path(filename).expanduser()
        suffix = target.suffix.lower().lstrip(".")
        if not suffix:
            suffix = extensions.get(selected_filter)
            if not suffix:
                return None, None
            target = target.with_suffix(f".{suffix}")
        return target, suffix

    def save_selected_frame_as(self, selection):
        if len(selection) != 1:
            return
        frame, last = selection[0]
        if frame != last:
            return
        path, index = self.session.frame_ref(frame)
        filters = {
            "PNG 图片 (*.png)": "png", "JPEG 图片 (*.jpg *.jpeg)": "jpg",
            "WebP 图片 (*.webp)": "webp", "AVIF 图片 (*.avif)": "avif",
            "TIFF 图片 (*.tiff)": "tiff", "BMP 图片 (*.bmp)": "bmp",
            "静态 GIF 图片 (*.gif)": "gif", "ICO 图标 (*.ico)": "ico",
            "PDF 文档 (*.pdf)": "pdf",
        }
        current = self.settings.image_format.currentData() or "png"
        extension = "gif" if current == "gif_image" else current
        selected_filter = next((name for name, suffix in filters.items() if suffix == extension),
                               "PNG 图片 (*.png)")
        default = self.settings.directory / f"{self.path.stem}_帧{frame:06}.{extension}"
        filename, chosen = QFileDialog.getSaveFileName(
            self.owner, "保存当前帧为图片", str(default), ";;".join(filters), selected_filter,
            QFileDialog.Option.DontConfirmOverwrite)
        target, suffix = self._target_from_dialog(filename, chosen or selected_filter, filters)
        if not target:
            return
        kinds = {"jpeg": "jpg", "jpg": "jpg", "gif": "gif_image"}
        kind = kinds.get(suffix, suffix)
        if kind not in _IMAGE_FORMATS:
            self.notice("请选择支持的图片格式。")
            return
        if target.resolve() == self.path.resolve():
            self.notice("不能用保存图片覆盖源视频。")
            return
        quality, limit, png_compression = self.settings.picture_encoding()
        max_edge, video = self.settings.resolution.currentData(), self.session.video_sources[path].info
        self.run_task("正在保存所选帧…",
                  lambda scope: save_picture(self.path, video, index, kind, quality, limit,
                                             target.parent, self.tools, scope,
                                             max_edge=max_edge, target=target, frame_path=path,
                                             png_compression=png_compression),
                  lambda path: self.saved(path, limit, frame=frame), exporting=True)

    def save_video_selection_as(self, selection):
        video, audio = self.session.tracks_for_selection(selection)
        fps = self.session.frames.fps
        formats = {
            "MP4 视频 (*.mp4)": ("mp4", "mp4"),
            "MP4 视频 H.265 (*.mp4)": ("mp4_hevc", "mp4"),
            "MKV 视频 H.264 (*.mkv)": ("mkv", "mkv"),
            "MKV 视频 H.265 (*.mkv)": ("mkv_hevc", "mkv"),
            "MOV 视频 (*.mov)": ("mov", "mov"),
            "WebM 视频 (*.webm)": ("webm", "webm"),
            "AVI 视频 (*.avi)": ("avi", "avi"),
        }
        current = self.settings.format.currentData() or "mp4"
        initial_filter = next((name for name, (kind, _) in formats.items() if kind == current),
                              "MP4 视频 (*.mp4)")
        default_ext = formats[initial_filter][1]
        default = self.settings.directory / f"{self.path.stem}_选区.{default_ext}"
        filename, chosen = QFileDialog.getSaveFileName(
            self.owner, "另存所选视频片段", str(default), ";;".join(formats), initial_filter,
            QFileDialog.Option.DontConfirmOverwrite)
        extensions = {name: extension for name, (_, extension) in formats.items()}
        target, suffix = self._target_from_dialog(filename, chosen or initial_filter, extensions)
        if not target:
            return
        selected_kind, selected_suffix = formats.get(chosen, (suffix, suffix))
        kind = selected_kind if suffix == selected_suffix else suffix
        if kind not in _ENCODERS:
            self.notice("请选择支持的视频格式。")
            return
        if target.resolve() == self.path.resolve():
            self.notice("不能用另存片段覆盖源视频。")
            return
        acceleration = self.settings.acceleration.currentData() or "cpu"
        supported = {backend for backend, _ in hardware_acceleration_choices(self.tools.ffmpeg, kind)}
        options = Export(kind, target.parent,
                         max_edge=int(self.settings.export_edge.currentData() or 0),
                         video_acceleration=acceleration if acceleration in supported else "cpu",
                         output_fps=int(self.settings.output_fps.currentData() or 0))
        canvas = self.canvas_for(video)
        self.run_task("正在另存所选视频片段…",
                  lambda scope: render_edit(self.path, video, audio, fps, options,
                                            self.tools, scope, self.progress,
                                            target=target, canvas=canvas,
                                            notice=self.worker_notice),
                  self.saved, exporting=True)

    def save_edit(self, *, include_video):
        video = self.session.video if include_video else []
        audio = self.session.audio_track()
        options = self.settings.output_options()
        if options.format in _AUDIO_ENCODERS:
            video = []
        if not video and not audio:
            self.notice("请先载入有效的帧或音频片段。")
            return
        fps = self.session.frames.fps if self.session.frames else 0
        canvas = self.canvas_for(video)
        self.run_task("正在保存编辑结果…",
                  lambda scope: render_edit(self.path, video, audio, fps, options, self.tools,
                                            scope, self.progress, canvas=canvas,
                                            notice=self.worker_notice),
                  self.saved, exporting=True)

    def save_image(self, frame=None):
        is_video = frame is not None
        path, index = self.session.frame_ref(frame) if is_video else (self.path, 0)
        video = self.session.video_sources[path].info if is_video else None
        kind = self.settings.image_format.currentData()
        quality, limit, png_compression = self.settings.picture_encoding()
        directory, max_edge = self.settings.directory, self.settings.resolution.currentData()
        output_stem = self._get_output_stem()
        def save(scope):
            return save_picture(self.path, video, index, kind, quality, limit, directory,
                                self.tools, scope, max_edge=max_edge, frame_path=path,
                                png_compression=png_compression, output_stem=output_stem)
        message = f"正在保存第 {frame} 帧…" if frame is not None else "正在保存图片…"
        self.run_task(message, save, lambda path: self.saved(path, limit, frame=frame), exporting=True)
