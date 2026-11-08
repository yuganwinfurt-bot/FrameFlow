"""用 FFmpeg 连续解码编辑时间线，限制预览缓冲大小。"""

import sys
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from ctypes import Structure, byref, c_size_t, c_uint16, c_uint32, c_void_p, cast, create_string_buffer, sizeof
from queue import Empty, Full, Queue
from time import monotonic

from .editing import _graph, length
from .media import MediaError, Processes

PCM_RATE = 44100
PCM_CHANNELS = 2


class WaveFormat(Structure):
    _pack_ = 1
    _fields_ = [("tag", c_uint16), ("channels", c_uint16), ("rate", c_uint32),
                ("byte_rate", c_uint32), ("alignment", c_uint16), ("bits", c_uint16),
                ("extra", c_uint16)]


class WaveHeader(Structure):
    _fields_ = [("data", c_void_p), ("size", c_uint32), ("recorded", c_uint32),
                ("user", c_size_t), ("flags", c_uint32), ("loops", c_uint32),
                ("next", c_void_p), ("reserved", c_size_t)]


class WaveOutput:
    """短 PCM 缓冲在提交前应用播放器音量，避免改动系统音量。"""

    RATE = PCM_RATE
    CHANNELS = PCM_CHANNELS
    BLOCK_FRAMES = RATE // 25
    DONE = 1

    def __init__(self, path):
        from ctypes import windll

        self.driver = windll.winmm
        self.handle = c_void_p()
        self.blocks = []
        self.stream = wave.open(str(path), "rb")
        layout = WaveFormat(1, self.CHANNELS, self.RATE, self.RATE * self.CHANNELS * 2,
                            self.CHANNELS * 2, 16, 0)
        status = self.driver.waveOutOpen(byref(self.handle), c_uint32(0xFFFFFFFF),
                                         byref(layout), c_size_t(0), c_size_t(0), 0)
        if status:
            self.stream.close()
            raise MediaError(f"无法打开音频输出（Windows 错误 {status}）。")

    def refill(self, volume):
        pending = []
        for header, memory in self.blocks:
            if header.flags & self.DONE:
                self.driver.waveOutUnprepareHeader(self.handle, byref(header), sizeof(header))
            else:
                pending.append((header, memory))
        self.blocks = pending
        while len(self.blocks) < 3:
            samples = self.stream.readframes(self.BLOCK_FRAMES)
            if not samples:
                break
            if not volume:
                samples = bytes(len(samples))
            elif volume < 1:
                samples = array("h", (int(value * volume) for value in array("h", samples))).tobytes()
            memory = create_string_buffer(samples)
            header = WaveHeader(cast(memory, c_void_p), len(samples))
            if self.driver.waveOutPrepareHeader(self.handle, byref(header), sizeof(header)):
                raise MediaError("无法准备播放器音频缓冲。")
            self.blocks.append((header, memory))
            if self.driver.waveOutWrite(self.handle, byref(header), sizeof(header)):
                raise MediaError("无法播放音频缓冲。")

    def close(self):
        self.driver.waveOutReset(self.handle)
        for header, _ in self.blocks:
            self.driver.waveOutUnprepareHeader(self.handle, byref(header), sizeof(header))
        self.driver.waveOutClose(self.handle)
        self.blocks.clear()
        self.stream.close()


class Playback:
    def __init__(self, video, audio, fps, tools, wav, *, canvas=None, volume=1.0):
        self.fps = fps
        self.count = int(length(video))
        self.scope = Processes()
        self.buffer = Queue(maxsize=8)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="frameflow-playback")
        self.wav = wav
        self.position = -1
        self.started = None
        self.audio_playing = False
        self.audio_error = ""
        self.volume = volume
        self.sink = None
        self.video_task = self.pool.submit(self._decode, video, tools, canvas)
        self.audio_task = (self.pool.submit(self._render_audio, audio, tools)
                           if audio and sys.platform == "win32" else None)

    def _decode(self, video, tools, canvas):
        inputs, graph = _graph(video, [], self.fps, canvas=canvas)
        graph.append("[video]scale=800:480:force_original_aspect_ratio=decrease[preview]")
        self.scope.run([tools.ffmpeg, "-v", "error", "-nostdin", *inputs,
                        "-filter_complex", ";".join(graph), "-map", "[preview]",
                        "-frames:v", str(self.count), "-fps_mode", "passthrough",
                        "-pix_fmt", "bgr24", "-c:v", "bmp", "-threads", "1",
                        "-f", "image2pipe", "pipe:1"],
                       read_stdout=self._buffer_frames)

    def _buffer_frames(self, stream):
        # BMP 的文件头记录了本帧大小，横屏和竖屏可以使用同一个解码管道。
        while header := stream.read(14):
            size = int.from_bytes(header[2:6], "little")
            if len(header) != 14 or header[:2] != b"BM" or not 54 <= size <= 1_540_000:
                raise MediaError("视频预览帧无法读取。")
            pixels = stream.read(size - len(header))
            if len(pixels) != size - len(header):
                raise MediaError("视频预览帧不完整。")
            data = header + pixels
            while True:
                self.scope.check()
                try:
                    self.buffer.put(data, timeout=0.05)
                    break
                except Full:
                    continue

    def _render_audio(self, audio, tools):
        inputs, graph = _graph([], audio, 0)
        graph.append(f"[audio]apad,atrim=duration={self.count / self.fps:.8f}[preview]")
        self.scope.run([tools.ffmpeg, "-v", "error", "-nostdin", "-y", *inputs,
                        "-filter_complex", ";".join(graph), "-map", "[preview]",
                        "-ar", str(PCM_RATE), "-ac", str(PCM_CHANNELS),
                        "-c:a", "pcm_s16le", str(self.wav)], timeout=25)

    def _start_audio(self):
        if self.audio_task is None:
            return
        try:
            self.audio_task.result()
            self.sink = WaveOutput(self.wav)
            self.sink.refill(self.volume)
            self.audio_playing = True
        except (MediaError, OSError, RuntimeError) as exc:
            self.audio_error = str(exc)
            if self.sink is not None:
                self.sink.close()
                self.sink = None

    def set_volume(self, volume):
        self.volume = max(0.0, min(1.0, volume))

    def tick(self):
        """返回当前应显示的帧，落后时直接追到播放时钟的位置。"""
        if self.started is None and self.audio_task is not None and not self.audio_task.done():
            return None
        if self.sink is not None:
            try:
                self.sink.refill(self.volume)
            except (MediaError, OSError) as exc:
                self.audio_error = str(exc)
                self.sink.close()
                self.sink = None
        target = (min(self.count - 1, int((monotonic() - self.started) * self.fps))
                  if self.started is not None else 0)
        data = None
        for _ in range(min(self.buffer.maxsize, target - self.position)):
            try:
                data = self.buffer.get_nowait()
            except Empty:
                if data is None and self.video_task.done() and self.buffer.empty():
                    self.video_task.result()
                    raise MediaError("视频预览提前结束。")
                break
            self.position += 1
        if data is not None and self.started is None:
            self._start_audio()
            self.started = monotonic()
        return data

    @property
    def finished(self):
        return (self.position == self.count - 1
                and monotonic() - self.started >= self.count / self.fps)

    def close(self):
        self.scope.cancel()
        try:
            if self.sink is not None:
                self.sink.close()
                self.sink = None
        finally:
            self.pool.shutdown(wait=True, cancel_futures=True)
