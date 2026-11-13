"""Own paused-frame and picture-size workers, caching and stale-result checks."""

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .editing import frame_png
from .media import Cancelled, MediaError, Processes, estimate_image_size, media_path_missing
from .media_text import detailed_size as _size_text


class FramePreview(QObject):
    decoded = Signal(object, object, str)

    def __init__(self, parent, tools, *, panel, is_image_panel, can_decode, is_playing,
                 source_info, report_error, missing_source):
        super().__init__(parent)
        self.tools, self.panel, self.is_image_panel = tools, panel, is_image_panel
        self.can_decode, self.is_playing, self.source_info = can_decode, is_playing, source_info
        self.report_error, self.missing_source = report_error, missing_source
        self.scope = self._inflight = None
        self.requested_frame = self.shown_frame = self._failed_frame = None
        self._cache = OrderedDict()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frameflow-seek")
        self._timer = QTimer(self, singleShot=True, interval=55)
        self._timer.timeout.connect(self._seek)
        self.decoded.connect(self._frame_ready)
        self.closing = False

    def request(self, frame, number, *, playing=False):
        panel = self.panel()
        if frame != self.requested_frame:
            self._failed_frame = None
        self.requested_frame = frame
        if playing:
            return
        if frame in self._cache:
            if self.scope is not None and self._inflight != frame:
                self.scope.cancel()
            self._cache.move_to_end(frame)
            if self.shown_frame != frame:
                panel.set_image(self._cache[frame])
                self.shown_frame = frame
        else:
            if self.is_image_panel() and frame not in (self.shown_frame, self._failed_frame):
                self.shown_frame = None
                panel.setText(f"正在读取第 {number} 帧…")
            self.resume()

    def resume(self):
        self._timer.start()

    def invalidate(self):
        self.shown_frame = self._failed_frame = None

    def present_played(self, data):
        self.panel().set_image(data)
        self.shown_frame = self.requested_frame

    def cancel_decode(self):
        if self.scope is not None:
            self.scope.cancel()

    def suspend(self):
        self._timer.stop()
        self.cancel_decode()

    def shutdown(self):
        self.closing = True
        self.suspend()
        self._pool.shutdown(wait=True, cancel_futures=True)

    def _seek(self):
        if self.closing or not self.can_decode() or self.panel() is None:
            return
        frame = self.requested_frame
        if frame is None or frame in (self.shown_frame, self._failed_frame):
            return
        if self.scope is not None:
            if self._inflight != frame:
                self.scope.cancel()
            return
        self._inflight = frame
        self.scope = Processes()
        self._pool.submit(self._decode_frame, frame, self.source_info(frame[0]), self.scope)

    def _decode_frame(self, frame, info, scope):
        path, index = frame
        try:
            data = frame_png(path, info, index, self.tools, scope)
        except Cancelled:
            self.decoded.emit(frame, None, "")
        except (MediaError, OSError) as exc:
            self.decoded.emit(frame, None, str(exc))
        else:
            self.decoded.emit(frame, data, "")

    @Slot(object, object, str)
    def _frame_ready(self, frame, data, error):
        self.scope = self._inflight = None
        if self.closing:
            return
        if data:
            self._cache[frame] = data
            while len(self._cache) > 12:
                self._cache.popitem(last=False)
        if self.is_playing():
            return
        panel = self.panel()
        if frame == self.requested_frame and panel is not None:
            if data:
                panel.set_image(data)
                self.shown_frame = frame
            elif error:
                self._failed_frame = frame
                if self.is_image_panel():
                    panel.setText("当前帧读取失败")
                self.report_error("当前帧读取失败", error)
                if media_path_missing(frame[0]):
                    self.missing_source(frame[0])
        if panel is not None and self.requested_frame not in (self.shown_frame, self._failed_frame):
            self._timer.start()


class PictureEstimate(QObject):
    estimated = Signal(int, int, str)

    def __init__(self, parent, tools, settings, source, *, source_is_image):
        super().__init__(parent)
        self.tools, self.settings, self.source = tools, settings, source
        self.source_is_image = source_is_image
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frameflow-image-size")
        self._scope = None
        self.generation = 0
        self._timer = QTimer(self, singleShot=True, interval=260)
        self._timer.timeout.connect(self._estimate_picture_size)
        self.estimated.connect(self._picture_estimated)
        self.closing = False

    def shutdown(self):
        self.closing = True
        self.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)

    def schedule(self, *_):
        self.cancel()
        if not self.settings.uses_picture_estimate():
            return
        self.settings.update_picture_dimensions()
        if self.source() is None:
            self.settings.show_estimate("等待视频帧就绪", "当前帧载入后会在后台编码并计算字节数。")
            return
        self.settings.show_estimate("正在计算…", "按当前图片格式及设置在后台编码，计算输出字节数。")
        self._timer.start()

    def cancel(self):
        self._timer.stop()
        self.generation += 1
        if self._scope is not None:
            self._scope.cancel()
            self._scope = None

    def _estimate_picture_size(self):
        if self.closing or not self.settings.uses_picture_estimate():
            return
        source = self.source()
        if source is None:
            self.settings.show_estimate("等待视频帧就绪", "当前帧载入后会在后台编码并计算字节数。")
            return
        path, video_info, index, _, _ = source
        if not path.is_file():
            self.settings.show_estimate("无法准确预估", "找不到用于编码的源文件。")
            return
        kind = self.settings.image_format.currentData()
        quality, limit, png_compression = self.settings.picture_encoding()
        max_edge = int(self.settings.resolution.currentData() or 0)
        generation = self.generation
        scope = Processes()
        self._scope = scope
        self._pool.submit(
            self._picture_size_worker, generation, path, video_info, index, kind,
            quality, limit, max_edge, png_compression, self.settings.configuration_only and kind != "ico", scope,
        )

    def _picture_size_worker(self, generation, path, video_info, index, kind,
                             quality, limit, max_edge, png_compression, upscale, scope):
        try:
            scope.check()
            source = (frame_png(path, video_info, index, self.tools, scope, preview=False)
                      if video_info is not None else path)
            size = estimate_image_size(source, kind, quality=quality, max_edge=max_edge,
                                       limit=limit, png_compression=png_compression, upscale=upscale)
            scope.check()
        except Cancelled:
            return
        except (MediaError, OSError, ValueError, KeyError) as exc:
            self.estimated.emit(generation, 0, str(exc))
        else:
            self.estimated.emit(generation, size, "")

    @Slot(int, int, str)
    def _picture_estimated(self, generation, size, error):
        if self.closing or generation != self.generation or not self.settings.uses_picture_estimate():
            return
        self._scope = None
        if error or not size:
            self.settings.show_estimate("无法准确预估", error or "编码未生成有效图片。")
            return
        self.settings.show_estimate(f"约 {_size_text(size)}", f"按当前设置实际编码得到 {size:,} 字节。",
                            comparable_size=size if self.source_is_image else None)
