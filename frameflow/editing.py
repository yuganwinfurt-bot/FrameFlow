"""非破坏性片段编辑和导出，供桌面编辑窗口的后台任务使用。"""

from __future__ import annotations

import json
import sys
import tempfile
from array import array
from dataclasses import dataclass, replace
from pathlib import Path

from .media import (HARDWARE_BACKENDS, AudioInfo, Cancelled, Export, ImageInfo, MediaError,
                    Processes, Tools, VideoInfo, audio_encoder_args, video_encoder_args)
from .media import (_AUDIO_ENCODERS, _ENCODERS, _IMAGE_FORMATS, _publish, _save_image_to_limit,
                    _with_bitrates, convert, probe, resolve_output_stem)


@dataclass(frozen=True)
class Clip:
    path: Path | None
    stream: int
    start: float
    end: float
    volume: float = 1.0
    rate: float = 1.0

    @property
    def length(self):
        return (self.end - self.start) / self.rate


def length(track):
    return sum(clip.length for clip in track)


def slice_track(track, start, end):
    result = []
    offset = 0
    for clip in track:
        left = max(start - offset, 0)
        right = min(end - offset, clip.length)
        if right - left > 1e-7:
            result.append(replace(clip, start=clip.start + left * clip.rate,
                                  end=clip.start + right * clip.rate))
        offset += clip.length
        if offset >= end:
            break
    return result


def splice(track, start, end, inserted=()):
    return [*slice_track(track, 0, start), *inserted, *slice_track(track, end, length(track))]


@dataclass(frozen=True)
class Frames:
    timestamps: tuple[float, ...]
    duration: float
    fps: float

    @property
    def count(self):
        return len(self.timestamps)

    def boundary(self, index):
        return self.timestamps[index] if index < self.count else self.duration


@dataclass(frozen=True)
class VideoSource:
    info: VideoInfo
    frames: Frames
    name: str


def index_frames(path: Path, info: VideoInfo, tools: Tools, scope: Processes) -> Frames:
    raw = scope.run([tools.ffprobe, "-v", "error", "-select_streams", str(info.stream),
                     "-show_frames", "-show_entries", "frame=best_effort_timestamp_time",
                     "-of", "json", str(path)], timeout=120)
    entries = json.loads(raw).get("frames", [])
    if not entries:
        raise MediaError("无法读取视频帧。")
    fps = info.fps or 25
    step = 1 / fps
    origin = next((float(frame["best_effort_timestamp_time"]) for frame in entries
                   if frame.get("best_effort_timestamp_time") not in (None, "N/A")), 0)
    times = []
    for index, frame in enumerate(entries):
        value = frame.get("best_effort_timestamp_time")
        time = float(value) - origin if value not in (None, "N/A") else (times[-1] + step if times else 0)
        times.append(max(0.0 if index == 0 else times[-1], time))
    tail = times[-1] - times[-2] if len(times) > 1 else step
    return Frames(tuple(times), times[-1] + (tail if tail > 0 else step), fps)


def original_audio(video_clips, frames: Frames, audio_stream: int | None):
    if audio_stream is None:
        return []
    track = []
    for clip in video_clips:
        start, end = frames.boundary(int(clip.start)), frames.boundary(int(clip.end))
        duration = clip.length / frames.fps
        track.append(Clip(clip.path, audio_stream, start, end, rate=(end - start) / duration)
                     if end > start else Clip(None, -1, 0, duration))
    return track


def audio_segment(track, start, end):
    """复制视频对应的音轨区间，保留没有音频的尾部。"""
    clips = slice_track(track, start, end)
    gap = end - start - length(clips)
    if gap > 1e-7:
        clips.append(Clip(None, -1, 0, gap))
    return clips


def frame_png(path: Path, info: VideoInfo, index: int, tools: Tools, scope: Processes,
              *, preview: bool = True) -> bytes:
    filters = f"select=eq(n\\,{index})"
    if preview:
        filters += ",scale=800:480:force_original_aspect_ratio=decrease"
    data = scope.run([tools.ffmpeg, "-v", "error", "-nostdin", "-i", str(path),
                      "-map", f"0:{info.stream}", "-vf", filters, "-frames:v", "1",
                      "-c:v", "png", "-f", "image2pipe", "pipe:1"], timeout=75)
    if not data:
        raise MediaError("找不到这帧，文件可能已变化。")
    return data


THUMB_WIDTH = 96
THUMB_HEIGHT = 56
TILE_FILTER = (f"crop=w='max(1,min(iw,ih*{THUMB_WIDTH}/{THUMB_HEIGHT}))':"
               f"h='max(1,min(ih,iw*{THUMB_HEIGHT}/{THUMB_WIDTH}))':exact=1,"
               f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}")


def frame_tiles(path: Path, info: VideoInfo, indices: tuple[int, ...],
                tools: Tools, scope: Processes) -> dict[int, bytes]:
    """按原始帧编号一次提取可视区所需图片，结果保持解码顺序。"""
    width, height = THUMB_WIDTH, THUMB_HEIGHT
    select = "+".join(f"eq(n\\,{index})" for index in indices)
    filters = f"select={select},{TILE_FILTER}"
    raw = scope.run([tools.ffmpeg, "-v", "error", "-nostdin", "-i", str(path),
                     "-map", f"0:{info.stream}", "-vf", filters, "-frames:v", str(len(indices)),
                     "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-threads", "1",
                     "-f", "rawvideo", "pipe:1"], timeout=75)
    stride = width * height * 3
    return {index: raw[offset:offset + stride]
            for index, offset in zip(indices, range(0, len(raw) - stride + 1, stride))}


def _tempo(rate):
    filters = []
    while rate < 0.5:
        filters.append("atempo=0.5")
        rate *= 2
    while rate > 2:
        filters.append("atempo=2")
        rate /= 2
    if abs(rate - 1) >= .001:
        filters.append(f"atempo={rate:.9g}")
    return "".join(f"{item}," for item in filters)


def _graph(video, audio, fps, *, canvas=None, max_edge=0, output_fps=0):
    inputs = list(dict.fromkeys(clip.path for clip in (*video, *audio) if clip.path is not None))
    graph = []
    if video:
        fit = ""
        if canvas:
            width, height = (value + value % 2 for value in canvas)
            fit = (f",scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p")
        for i, clip in enumerate(video):
            graph.append(f"[{inputs.index(clip.path)}:{clip.stream}]"
                         f"trim=start_frame={int(clip.start)}:end_frame={int(clip.end)},"
                         f"setpts=PTS-STARTPTS{fit}[v{i}]")
        sources = "".join(f"[v{i}]" for i in range(len(video)))
        shrink = ""
        if max_edge:
            edge = max(2, int(max_edge) // 2 * 2)
            shrink = (f",scale=w='max(2,round(iw*{edge}/max(iw,ih)/2)*2)':"
                      f"h='max(2,round(ih*{edge}/max(iw,ih)/2)*2)':flags=lanczos")
        resample = f",fps={output_fps}:eof_action=pass" if output_fps else ""
        graph.append(f"{sources}concat=n={len(video)}:v=1:a=0,"
                     f"settb=1/({fps:.9g}),setpts=N,pad=ceil(iw/2)*2:ceil(ih/2)*2"
                     f"{shrink}{resample}[video]")
    if audio:
        for i, clip in enumerate(audio):
            silence = f"anullsrc=r=48000:cl=stereo:d={clip.length:.8f}"
            if clip.path is None:
                graph.append(f"{silence},aformat=sample_fmts=flt[a{i}]")
            else:
                # 有些视频的音轨先结束，截取末尾时可能没有任何音频帧。
                # 有限长的静音轨为混音提供时钟，连零采样的片段也能正常结束。
                graph.append(f"{silence}[silence{i}]")
                graph.append(f"[{inputs.index(clip.path)}:{clip.stream}]asetpts=PTS-STARTPTS,"
                             f"atrim=start={clip.start:.8f}:end={clip.end:.8f},asetpts=PTS-STARTPTS,"
                             f"{_tempo(clip.rate)}volume={clip.volume:.4f},"
                             f"aformat=sample_fmts=flt:sample_rates=48000:channel_layouts=stereo[clip{i}]")
                graph.append(f"[silence{i}][clip{i}]amix=inputs=2:duration=first:normalize=0[a{i}]")
        sources = "".join(f"[a{i}]" for i in range(len(audio)))
        graph.append(f"{sources}concat=n={len(audio)}:v=0:a=1"
                     + (",apad" if video else "") + "[audio]")
    return [arg for path in inputs for arg in ("-i", str(path))], graph


def render_preview_wav(clips, target: Path, tools: Tools, scope: Processes):
    inputs, graph = _graph([], clips, 0)
    scope.run([tools.ffmpeg, "-v", "error", "-nostdin", "-y", *inputs,
               "-filter_complex", ";".join(graph), "-map", "[audio]", "-c:a", "pcm_s16le",
               str(target)], timeout=45)


class Waveform:
    """保留预览窗的 PCM，并缓存整块峰值用于快速绘制。"""

    block_size = 64

    def __init__(self, channels, sample_rate):
        self.samples = channels
        self.sample_rate = sample_rate
        self.frames = len(channels[0])
        self.blocks = []
        peak = 16
        for samples in channels:
            chunks = (samples[i:i + self.block_size] for i in range(0, self.frames, self.block_size))
            pairs = [(min(chunk), max(chunk)) for chunk in chunks]
            lows, highs = zip(*pairs)
            self.blocks.append((lows, highs))
            peak = max(peak, -min(lows), max(highs))
        self.reference = peak

    def envelope(self, start, end, columns):
        first = min(self.frames - 1, max(0, round(start * self.sample_rate)))
        last = max(first + 1, min(self.frames, round(end * self.sample_rate)))
        count = min(last - first, max(1, columns))
        bands = []
        for samples, (lows, highs) in zip(self.samples, self.blocks):
            band = []
            for column in range(count):
                left = first + column * (last - first) // count
                right = first + (column + 1) * (last - first) // count
                begin_block = (left + self.block_size - 1) // self.block_size
                end_block = right // self.block_size
                if begin_block < end_block:
                    edges = samples[left:begin_block * self.block_size] + samples[end_block * self.block_size:right]
                    low = min(lows[begin_block:end_block])
                    high = max(highs[begin_block:end_block])
                    if edges:
                        low, high = min(low, min(edges)), max(high, max(edges))
                else:
                    values = samples[left:right]
                    low, high = min(values), max(values)
                band.append((low / self.reference, high / self.reference))
            bands.append(band)
        return bands


def audio_preview(clips, tools: Tools, scope: Processes):
    common = [tools.ffmpeg, "-v", "error", "-nostdin"]
    output = ["-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"]
    raw = b""
    if len(clips) == 1:
        clip = clips[0]
        if clip.path is not None and clip.start == 0 and clip.rate == 1:
            filters = f"asetpts=PTS-STARTPTS,volume={clip.volume:.4f},apad"
            raw = scope.run([*common, "-i", str(clip.path), "-map", f"0:{clip.stream}",
                             "-af", filters, "-t", f"{clip.length:.8f}", *output], timeout=45)
    if not raw:
        inputs, graph = _graph([], clips, 0)
        raw = scope.run([*common, *inputs, "-filter_complex", ";".join(graph),
                         "-map", "[audio]", *output], timeout=45)
    scope.check()
    samples = array("h", raw)
    if not samples:
        return None
    if sys.byteorder == "big":
        samples.byteswap()
    lanes = samples[::2], samples[1::2]
    if lanes[0] == lanes[1]:
        lanes = lanes[:1]
    waveform = Waveform(lanes, 48000)
    scope.check()
    return waveform


def render_edit(source, video, audio, fps, options: Export, tools: Tools, scope: Processes,
                progress=lambda _: None, *, target: Path | None = None, canvas=None,
                notice=lambda _message: None):
    kind = options.format
    if video and kind not in (*_ENCODERS, "gif"):
        raise MediaError("请选择视频格式。")
    if not video and (not audio or kind not in _AUDIO_ENCODERS):
        raise MediaError("需要至少一段音频和一种音频格式。")
    stem = target.stem if target is not None else resolve_output_stem(options.output_stem, f"{source.stem}_编辑")
    directory = target.parent if target is not None else options.directory or source.parent
    extension = "mkv" if kind == "mkv_hevc" else kind
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".frameflow-edit-", dir=directory) as folder:
        temp = Path(folder) / ("edit.mkv" if kind == "gif" else f"edit.{extension}")
        output_fps = options.output_fps if video and kind != "gif" else 0
        inputs, graph = _graph(video, audio, fps, canvas=canvas, max_edge=options.max_edge,
                               output_fps=output_fps)
        cmd = [tools.ffmpeg, "-v", "error", "-nostdin", "-y", "-progress", "pipe:1",
               "-nostats", *inputs, "-filter_complex", ";".join(graph)]
        if video:
            cmd += ["-map", "[video]", "-pix_fmt", "yuv420p"]
            if not output_fps:
                cmd += ["-r", f"{fps:.9g}", "-frames:v", str(int(length(video)))]
            if audio:
                cmd += ["-map", "[audio]", "-shortest"]
            encoder = (_ENCODERS["mkv"] if kind == "gif" else
                       video_encoder_args(kind, options.video_acceleration, options.quality, options.audio_bitrate))
        else:
            cmd += ["-map", "[audio]"]
            encoder = audio_encoder_args(options)
        seconds = length(video) / fps if video else length(audio)

        def encode(codec: list[str], start: int, end: int, target_path: Path) -> None:
            scope.run([*cmd, *codec, str(target_path)], on_seconds=lambda position:
                      progress(start + round((end - start) * min(1, max(0, position / seconds)))))

        limit = 0 if kind == "gif" else options.size_limit
        try:
            encode(encoder, 0, 48 if limit else 99, temp)
        except Cancelled:
            raise
        except MediaError:
            if not video or kind == "gif" or options.video_acceleration == "cpu":
                raise
            notice(f"{dict(HARDWARE_BACKENDS).get(options.video_acceleration, '硬件')}不可用，已回退 CPU 软件编码。")
            encoder = video_encoder_args(kind, "cpu", options.quality, options.audio_bitrate)
            encode(encoder, 0, 48 if limit else 99, temp)
        # 有目标体积就按实际时长反推码率，和主界面转换用同一套逼近方式
        if limit and seconds > 0:
            best = temp.stat().st_size if temp.exists() else 0
            bitrate = int(limit * 8 / seconds * .90)
            previous = None
            for attempt in range(3):
                if best and best <= limit:
                    break
                audio_rate = min(128_000, max(32_000, bitrate // 5 if video else bitrate)) if audio else 0
                video_rate = max(12_000, bitrate - audio_rate) if video else 0
                if (video_rate, audio_rate) == previous:
                    break
                previous = (video_rate, audio_rate)
                candidate = Path(folder) / f"candidate.{extension}"
                block = 48 + attempt * 17
                try:
                    encode(_with_bitrates(encoder, video_rate, audio_rate), block, block + 17, candidate)
                except Cancelled:
                    raise
                except MediaError:
                    break
                size = candidate.stat().st_size if candidate.exists() else 0
                if 0 < size < best:
                    candidate.replace(temp)
                    best = size
                bitrate = int(bitrate * min(.8, limit / max(1, size or 1) * .90))
            progress(99)
        scope.check()
        if kind == "gif":
            info = probe(temp, tools, scope)
            gif = convert(temp, info, replace(options, directory=Path(folder), output_stem=""), tools, scope,
                          lambda value: progress(60 + value * 39 // 100))
            temp = gif
        if not temp.is_file() or not temp.stat().st_size:
            raise MediaError("没有生成有效成品。")
        scope.check()
        if target is not None:
            extension = target.suffix[1:]
        return _publish(temp, directory, stem, extension)


def save_picture(source, video_info, index, kind, quality, limit, directory, tools, scope, *, max_edge=0,
                 target: Path | None = None, frame_path: Path | None = None, png_compression=6,
                 output_stem: str = ""):
    if target is not None:
        directory = target.parent
        stem = target.stem
    else:
        default = f"{source.stem}_帧{index + 1:06}" if video_info is not None else f"{source.stem}_图片"
        stem = resolve_output_stem(output_stem, default)
    directory.mkdir(parents=True, exist_ok=True)
    extension = "gif" if kind == "gif_image" else kind
    with tempfile.TemporaryDirectory(prefix=".frameflow-frame-", dir=directory) as folder:
        if video_info is not None:
            picture = Path(folder) / "frame.png"
            picture.write_bytes(frame_png(frame_path or source, video_info, index, tools, scope, preview=False))
        else:
            picture = source
        best = Path(folder) / f"best.{extension}"
        _save_image_to_limit(picture, best, _IMAGE_FORMATS[kind], scope,
                             quality=quality, max_edge=max_edge, limit=limit, png_compression=png_compression)
        scope.check()
        if target is not None:
            extension = target.suffix[1:]
        return _publish(best, directory, stem, extension)
