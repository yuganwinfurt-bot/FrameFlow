"""FFmpeg 命令、媒体信息和安全输出。界面线程不直接调用这些函数。"""

from __future__ import annotations

import io
import json
import math
import os
import shutil
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from stat import S_ISREG
from uuid import uuid4

from PIL import Image, ImageOps

from .media_runtime import Cancelled, MediaError, Processes, Tools

# 在启动时注册图片编解码器，避免后台任务首次导出时并发导入插件。
Image.init()

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".flv",
                    ".ts", ".m4v", ".mpeg", ".mpg", ".mts", ".gif"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".wav", ".flac", ".aac", ".opus", ".ogg",
                    ".oga", ".wma", ".aif", ".aiff", ".ape", ".amr", ".mka", ".caf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".tif", ".tiff", ".bmp", ".gif", ".ico"}
LOSSLESS_AUDIO_FORMATS = {"wav", "flac"}
BAD_NAME_CHARS = set('<>:"/\\|?*')
RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {
    f"lpt{i}" for i in range(1, 10)}


def media_path_missing(path: Path) -> bool:
    try:
        return not S_ISREG(path.stat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False


def filename_problem(stem: str) -> str:
    name = stem.strip()
    if not name:
        return "文件名不能为空"
    if name in {".", ".."}:
        return "文件名不能是 . 或 .."
    if any(ch in BAD_NAME_CHARS or ord(ch) < 32 for ch in name):
        return "文件名不能包含 < > : \" / \\ | ? * 或控制字符"
    if name != stem:
        return "文件名首尾不能是空格"
    if name.endswith("."):
        return "文件名不能以句点结尾"
    if name.split(".")[0].lower() in RESERVED_NAMES:
        return "该名称是 Windows 保留名称，请换一个"
    return ""


def resolve_output_stem(custom: str, default: str) -> str:
    if not custom:
        return default
    if problem := filename_problem(custom):
        raise MediaError(problem)
    return custom


def output_extension(kind: str) -> str:
    return {"mp4_hevc": "mp4", "mkv_hevc": "mkv", "gif_image": "gif"}.get(kind, kind)


@dataclass(frozen=True)
class VideoInfo:
    stream: int
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    size: int
    audio_stream: int | None = None
    container: str = ""
    bitrate: int = 0
    pixel_format: str = ""
    profile: str = ""
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    channel_layout: str = ""
    audio_duration: float = 0


@dataclass(frozen=True)
class AudioInfo:
    stream: int
    duration: float
    codec: str
    size: int
    sample_rate: int
    channels: int
    container: str = ""
    bitrate: int = 0
    channel_layout: str = ""
    sample_format: str = ""
    profile: str = ""


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    codec: str
    size: int
    mode: str = ""


MediaInfo = VideoInfo | AudioInfo | ImageInfo
WAVEFORM_SECONDS = 30


@dataclass(frozen=True)
class Export:
    format: str
    directory: Path | None = None
    small_gif: bool = True
    gif_limit: int = 0
    max_edge: int = 0
    target_bytes: int = 0
    video_acceleration: str = "cpu"
    output_fps: int = 0
    quality: int = 0
    audio_bitrate: int = 0
    audio_sample_rate: int = 0
    audio_channels: int = 0
    png_compression: int = 6
    output_stem: str = ""

    @property
    def size_limit(self) -> int:
        if self.format in LOSSLESS_AUDIO_FORMATS:
            return 0
        return self.target_bytes or (self.gif_limit if self.format == "gif" else 0)


VIDEO_RESOLUTIONS = (("4K", 3840), ("1440P", 2560), ("1080P", 1920),
                     ("720P", 1280), ("480P", 854), ("360P", 640))
VIDEO_FRAME_RATES = (24, 25, 30, 50, 60)


def _number(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) and result > 0 else 0.0


def probe(path: Path, tools: Tools, scope: Processes) -> MediaInfo:
    scope.check()
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        try:
            with Image.open(path) as source:
                if source.format != "GIF" or not source.is_animated:
                    width, height = source.size
                    if source.getexif().get(274) in (5, 6, 7, 8):
                        width, height = height, width
                    return ImageInfo(width, height, source.format or "图片", path.stat().st_size,
                                     source.mode)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise MediaError("无法读取图片，文件可能损坏或像素过大。") from exc
    raw = scope.run([tools.ffprobe, "-v", "error", "-show_streams", "-show_format",
                     "-of", "json", str(path)], timeout=25)
    try:
        data = json.loads(raw)
        streams = data.get("streams", [])
        media_format = data.get("format", {})
        duration = _number(media_format.get("duration"))
        size = path.stat().st_size
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        stream = next((item for item in streams
                       if item.get("codec_type") == "video"
                       and not item.get("disposition", {}).get("attached_pic")), None)
        if stream is None:
            if audio is None:
                raise MediaError("文件里没有可转换的视频或音频轨道。")
            return AudioInfo(
                int(audio["index"]), _number(audio.get("duration")) or duration,
                str(audio.get("codec_name") or "未知").upper(), size,
                int(_number(audio.get("sample_rate"))), int(_number(audio.get("channels"))),
                str(media_format.get("format_name") or ""),
                int(_number(audio.get("bit_rate")) or _number(media_format.get("bit_rate"))),
                str(audio.get("channel_layout") or ""),
                str(audio.get("sample_fmt") or ""), str(audio.get("profile") or ""),
            )
        rate = stream.get("avg_frame_rate", "")
        try:
            fps = float(Fraction(rate))
        except (TypeError, ValueError, ZeroDivisionError):
            fps = 0.0
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        for side in stream.get("side_data_list", []):
            if abs(int(side.get("rotation") or 0)) % 180 == 90:
                width, height = height, width
                break
        if width < 1 or height < 1:
            raise MediaError("无法读取视频分辨率。")
        return VideoInfo(
            int(stream["index"]),
            duration or _number(stream.get("duration")),
            width, height, fps, str(stream.get("codec_name") or "未知").upper(),
            size,
            int(audio["index"]) if audio is not None else None,
            str(media_format.get("format_name") or ""),
            int(_number(stream.get("bit_rate")) or _number(media_format.get("bit_rate"))),
            str(stream.get("pix_fmt") or ""), str(stream.get("profile") or ""),
            str(audio.get("codec_name") or "未知").upper() if audio else "",
            int(_number(audio.get("sample_rate"))) if audio else 0,
            int(_number(audio.get("channels"))) if audio else 0,
            str(audio.get("channel_layout") or "") if audio else "",
            _number(audio.get("duration")) if audio else 0,
        )
    except (ValueError, KeyError, OSError) as exc:
        raise MediaError("无法读取媒体信息。文件可能损坏或格式不受支持。") from exc


def detail_rows(path: Path, info: MediaInfo) -> list[tuple[str, list[tuple[str, str]]]]:
    """把媒体参数整理成分组的 (字段, 值)，界面按组渲染，不再自己拼字符串。"""
    try:
        full_path = str(path.resolve())
    except OSError:
        full_path = str(path)
    container = getattr(info, "container", "") or path.suffix.lstrip(".").upper() or "未知"
    is_pdf = path.suffix.lower() == ".pdf" or (isinstance(info, ImageInfo) and info.codec.upper() == "PDF")
    rows = [
        ("文件名", path.name),
        ("原始路径", full_path),
        ("媒体类型", "PDF 文档" if is_pdf else
                    "视频" if isinstance(info, VideoInfo) else
                    "音频" if isinstance(info, AudioInfo) else "图片"),
        ("文件格式", container),
        ("文件大小", _display_size(info.size)),
    ]
    groups = [("文件", rows)]
    if isinstance(info, VideoInfo):
        video = [
            ("分辨率", f"{info.width} × {info.height} 像素"),
            ("时长", f"{info.duration:.3f} 秒" if info.duration else "未知"),
            ("帧率", f"{info.fps:.3f} 帧/秒" if info.fps else "未知"),
            ("视频编码", info.codec or "未知"),
            ("编码档次", info.profile or "未知"),
            ("像素格式", info.pixel_format or "未知"),
            ("平均码率", _display_bitrate(info.bitrate)),
        ]
        audio = [("音轨", "无" if info.audio_stream is None else
                  f"{info.audio_codec or '未知'} · " +
                  (f"{info.sample_rate / 1000:g} kHz" if info.sample_rate else "采样率未知") + " · " +
                  ({1: "单声道", 2: "立体声"}.get(info.channels, f"{info.channels} 声道")
                   if info.channels else "声道数未知") +
                  # mono / stereo 已经在上一句里说过了，别重复一遍英文
                  (f" · {info.channel_layout}" if info.channel_layout
                   and info.channel_layout not in ("mono", "stereo") else ""))]
        groups.extend((("视频", video), ("音频", audio)))
        return groups
    if isinstance(info, AudioInfo):
        groups.append(("音频", [
            ("时长", f"{info.duration:.3f} 秒" if info.duration else "未知"),
            ("音频编码", info.codec or "未知"),
            ("编码档次", info.profile or "未知"),
            ("采样率", f"{info.sample_rate / 1000:g} kHz" if info.sample_rate else "未知"),
            ("声道", {1: "单声道", 2: "立体声"}.get(info.channels, f"{info.channels} 声道")
             if info.channels else "未知"),
            ("声道布局", info.channel_layout or "未知"),
            ("采样格式", info.sample_format or "未知"),
            ("平均码率", _display_bitrate(info.bitrate)),
        ]))
        return groups
    if isinstance(info, ImageInfo) and not is_pdf:
        groups.append(("图片", [
            ("分辨率", f"{info.width} × {info.height} 像素" if info.width and info.height else "未知"),
            ("颜色模式", info.mode or "未知"),
        ]))
    return groups


def media_details(path: Path, info: MediaInfo) -> str:
    """纯文本版本，用于日志和弹窗；界面用 detail_rows 分组渲染。"""
    return "\n".join(f"{name}：{value}"
                     for _, rows in detail_rows(path, info) for name, value in rows)


def _display_size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.2f} MB（{size:,} 字节）"
    return f"{size / 1_000:.1f} KB（{size:,} 字节）"


def _display_bitrate(bitrate: int) -> str:
    return f"{bitrate / 1000:.0f} kb/s" if bitrate else "未知"


def thumbnail(path: Path, info: MediaInfo, tools: Tools, scope: Processes) -> bytes:
    if isinstance(info, ImageInfo):
        scope.check()
        with Image.open(path) as source, io.BytesIO() as buffer:
            preview = ImageOps.exif_transpose(source).convert("RGBA")
            preview.thumbnail((480, 480))
            preview.save(buffer, "PNG")
            scope.check()
            return buffer.getvalue()
    common = [tools.ffmpeg, "-v", "error", "-nostdin"]
    image = ["-frames:v", "1", "-c:v", "png", "-f", "image2pipe", "pipe:1"]
    if isinstance(info, AudioInfo):
        return scope.run([*common, "-t", str(WAVEFORM_SECONDS), "-i", str(path),
                          "-filter_complex",
                          f"[0:{info.stream}]showwavespic=s=640x180:colors=0x438bd0[out]",
                          "-map", "[out]", *image], timeout=20)
    position = min(info.duration / 4, 1.0)
    output = ["-i", str(path), "-map", f"0:{info.stream}",
              "-vf", "scale=480:480:force_original_aspect_ratio=decrease", *image]
    data = scope.run([*common, "-ss", f"{position:.3f}", *output], timeout=20)
    return data or scope.run([*common, *output], timeout=20)


_ENCODERS = {
    "mp4": ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac",
            "-movflags", "+faststart"],
    "mp4_hevc": ["-c:v", "libx265", "-preset", "fast", "-crf", "26", "-c:a", "aac",
                 "-tag:v", "hvc1", "-movflags", "+faststart"],
    "mov": ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac"],
    "mkv": ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac"],
    "mkv_hevc": ["-c:v", "libx265", "-preset", "fast", "-crf", "26", "-c:a", "aac"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-c:a", "libopus"],
    "avi": ["-c:v", "mpeg4", "-q:v", "4", "-c:a", "libmp3lame", "-b:a", "192k"],
}
HARDWARE_BACKENDS = (
    ("nvenc", "NVIDIA NVENC"),
    ("amf", "AMD AMF"),
    ("qsv", "Intel Quick Sync"),
    ("mf", "Windows MediaFoundation"),
)
_HARDWARE_ENCODER_NAMES = {
    "nvenc": {"h264": "h264_nvenc", "hevc": "hevc_nvenc"},
    "amf": {"h264": "h264_amf", "hevc": "hevc_amf"},
    "qsv": {"h264": "h264_qsv", "hevc": "hevc_qsv"},
    "mf": {"h264": "h264_mf", "hevc": "hevc_mf"},
}


@lru_cache(maxsize=8)
def _available_encoder_names(ffmpeg: str) -> frozenset[str]:
    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[0].startswith("V"):
            names.add(fields[1])
    return frozenset(names)


def hardware_acceleration_choices(ffmpeg: str, format_name: str) -> list[tuple[str, str]]:
    """返回当前 FFmpeg 构建支持的硬件编码器；设备或驱动不可用时导出会回退 CPU。"""
    codec = _hardware_codec(format_name)
    choices = [("cpu", "CPU 软件编码")]
    if codec is None:
        return choices
    available = _available_encoder_names(ffmpeg)
    choices.extend((backend, title) for backend, title in HARDWARE_BACKENDS
                    if _HARDWARE_ENCODER_NAMES[backend][codec] in available)
    return choices


def _hardware_codec(format_name: str) -> str | None:
    if format_name in {"mp4_hevc", "mkv_hevc"}:
        return "hevc"
    return "h264" if format_name in {"mp4", "mov", "mkv"} else None


def video_encoder_args(format_name: str, acceleration: str = "cpu", quality: int = 0,
                       audio_bitrate: int = 0) -> list[str]:
    """按容器和加速后端生成真实 FFmpeg 编码参数。

    ``quality`` 是恒定质量（CRF/CQ）值，18–32 之间，越小越清晰；传 0 用容器默认值。
    ``audio_bitrate`` 单位 kbps，0 表示沿用容器默认音轨参数。
    """
    args = _video_encoder_args(format_name, acceleration, quality)
    if audio_bitrate and "-c:a" in args and "-b:a" not in args:
        args.extend(("-b:a", f"{int(audio_bitrate)}k"))
    elif audio_bitrate and "-b:a" in args:
        args[args.index("-b:a") + 1] = f"{int(audio_bitrate)}k"
    return args


def _video_encoder_args(format_name: str, acceleration: str = "cpu", quality: int = 0) -> list[str]:
    base = list(_ENCODERS.get(format_name) or ())
    if not base:
        raise MediaError("此输出格式不支持视频编码加速。")
    if acceleration == "cpu":
        if quality and "-crf" in base:
            base[base.index("-crf") + 1] = str(int(quality))
        return base
    codec = _hardware_codec(format_name)
    if codec is None or acceleration not in _HARDWARE_ENCODER_NAMES:
        raise MediaError("所选输出格式不支持此编码加速。")
    video_encoder = _HARDWARE_ENCODER_NAMES[acceleration][codec]
    audio_start = base.index("-c:a") if "-c:a" in base else len(base)
    audio_args = base[audio_start:]
    level = int(quality) if quality else 23
    if acceleration == "nvenc":
        video_args = ["-c:v", video_encoder, "-preset", "p4", "-rc", "vbr", "-cq", str(level), "-b:v", "0"]
    elif acceleration == "amf":
        video_args = ["-c:v", video_encoder, "-quality", "balanced", "-rc", "cqp",
                      "-qp_i", str(level), "-qp_p", str(level)]
    elif acceleration == "qsv":
        video_args = ["-c:v", video_encoder, "-global_quality", str(level)]
    else:
        video_args = ["-c:v", video_encoder, "-hw_encoding", "1", "-rate_control", "quality",
                      "-quality", "80"]
    return [*video_args, *audio_args]


_IMAGE_FORMATS = {
    "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "avif": "AVIF", "tiff": "TIFF",
    "bmp": "BMP", "gif_image": "GIF", "ico": "ICO", "pdf": "PDF",
}


def _write_image(source: Path | bytes, output, format_name: str, *,
                 quality: int = 90, scale: float = 1.0, max_edge: int = 0, upscale: bool = False,
                 png_compression: int = 6) -> None:
    try:
        image_source = io.BytesIO(source) if isinstance(source, bytes) else source
        with Image.open(image_source) as image:
            pixels = ImageOps.exif_transpose(image)
            if max_edge:
                ratio = max_edge / max(pixels.size)
                scale *= ratio if upscale else min(1, ratio)
            if scale != 1:
                pixels = pixels.resize((max(1, round(pixels.width * scale)),
                                        max(1, round(pixels.height * scale))), Image.Resampling.LANCZOS)
            if format_name in {"JPEG", "BMP", "PDF"}:
                layer = pixels.convert("RGBA")
                pixels = Image.new("RGB", layer.size, "white")
                pixels.paste(layer, mask=layer.getchannel("A"))
            elif format_name == "ICO":
                layer = ImageOps.contain(pixels.convert("RGBA"), (256, 256), Image.Resampling.LANCZOS)
                pixels = Image.new("RGBA", (256, 256))
                pixels.paste(layer, ((256 - layer.width) // 2, (256 - layer.height) // 2))
            elif format_name in {"WEBP", "AVIF", "GIF"} or (
                format_name == "PNG" and pixels.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I;16"}
            ):
                pixels = pixels.convert("RGBA")
            options = {"quality": quality} if format_name in {"JPEG", "WEBP", "AVIF"} else {}
            if format_name == "PNG":
                options["compress_level"] = png_compression
            pixels.save(output, format_name, **options)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise MediaError(f"无法保存为 {format_name}：{exc}") from exc


def _save_image(source: Path, output: Path, format_name: str, *,
                quality: int = 90, scale: float = 1.0, max_edge: int = 0, upscale: bool = False,
                png_compression: int = 6) -> None:
    _write_image(source, output, format_name, quality=quality, scale=scale,
                 max_edge=max_edge, upscale=upscale, png_compression=png_compression)


def _image_profiles(quality: int, limit: int):
    profiles = [(quality, 1.0)]
    if limit:
        profiles.extend([(min(quality, 75), .85), (min(quality, 60), .65),
                         (min(quality, 45), .45), (min(quality, 30), .25),
                         (min(quality, 20), .12)])
    return profiles


def estimate_image_size(source: Path | bytes, kind: str, *, quality: int = 90,
                        max_edge: int = 0, limit: int = 0, upscale: bool = False,
                        png_compression: int = 6) -> int:
    """用实际图片编码器估算当前设置生成的字节数，和保存时采用相同压缩档位。"""
    format_name = _IMAGE_FORMATS[kind]
    best = None
    for level, scale in _image_profiles(quality, limit):
        candidate = io.BytesIO()
        _write_image(source, candidate, format_name, quality=level, scale=scale,
                     max_edge=max_edge, upscale=upscale, png_compression=png_compression)
        size = candidate.tell()
        if best is None or size < best:
            best = size
        if not limit or best <= limit:
            break
    return best or 0


_AUDIO_ENCODERS = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    "m4a": ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "opus": ["-c:a", "libopus", "-b:a", "128k"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
}


def _save_image_to_limit(source: Path, output: Path, format_name: str, scope: Processes, *,
                         quality: int = 90, max_edge: int = 0, limit: int = 0,
                         upscale: bool = False, png_compression: int = 6) -> None:
    candidate = output.with_name(f"candidate{output.suffix}")
    best = None
    for level, scale in _image_profiles(quality, limit):
        scope.check()
        _save_image(source, candidate, format_name, quality=level, scale=scale,
                    max_edge=max_edge, upscale=upscale, png_compression=png_compression)
        size = candidate.stat().st_size
        if best is None or size < best:
            candidate.replace(output)
            best = size
        if not limit or best <= limit:
            break


def _with_bitrates(args: list[str], video_rate: int, audio_rate: int) -> list[str]:
    overrides = set()
    if video_rate:
        overrides.update(("-crf", "-q:v", "-b:v", "-cq", "-qp", "-qp_i", "-qp_p", "-qp_b",
                          "-global_quality", "-rc"))
    if audio_rate:
        overrides.update(("-q:a", "-b:a"))
    # Encoder presets consist of FFmpeg option/value pairs.
    options = [arg for flag, value in zip(args[::2], args[1::2])
               if flag not in overrides for arg in (flag, value)]
    if video_rate:
        options.extend(("-b:v", str(video_rate)))
    if audio_rate:
        options.extend(("-b:a", str(audio_rate)))
    return options


def audio_encoder_args(options: Export) -> list[str]:
    args = list(_AUDIO_ENCODERS[options.format])
    if options.audio_bitrate and options.format not in LOSSLESS_AUDIO_FORMATS:
        args = _with_bitrates(args, 0, options.audio_bitrate * 1000)
    if options.audio_sample_rate:
        rate = options.audio_sample_rate
        if options.format == "opus" and rate not in {8000, 12000, 16000, 24000, 48000}:
            rate = 48000
        args += ["-ar", str(rate)]
    if options.audio_channels:
        args += ["-ac", str(options.audio_channels)]
    return args


def _publish(temporary: Path, directory: Path, stem: str, extension: str) -> Path:
    for number in range(10_000):
        suffix = f" ({number})" if number else ""
        final = directory / f"{stem}{suffix}.{extension}"
        try:
            if os.name == "nt":
                temporary.rename(final)  # Windows 上 rename 不会替换已有文件
            else:
                os.link(temporary, final)
            return final
        except FileExistsError:
            continue
    raise MediaError("同名输出文件过多，请改用其他输出文件夹。")


@contextmanager
def _output_workspace(directory: Path, prefix: str):
    directory.mkdir(parents=True, exist_ok=True)
    folder = directory / f"{prefix}{uuid4().hex}"
    # On Windows, tempfile.mkdtemp may retry ACL permission errors as name collisions.
    folder.mkdir(mode=0o700)
    try:
        yield folder
    finally:
        shutil.rmtree(folder)


def convert(
    source: Path, info: MediaInfo, options: Export, tools: Tools, scope: Processes,
    progress: Callable[[int], None], *, notice: Callable[[str], None] = lambda _message: None,
) -> Path:
    kind = options.format
    extension = output_extension(kind)
    if isinstance(info, ImageInfo):
        if kind not in _IMAGE_FORMATS:
            raise MediaError("请选择支持的图片输出格式。")
    elif isinstance(info, AudioInfo):
        if kind not in _AUDIO_ENCODERS:
            raise MediaError("请选择支持的音频输出格式。")
    elif kind not in (*_ENCODERS, *_AUDIO_ENCODERS, "gif"):
        raise MediaError("请选择支持的视频输出格式。")
    stem = resolve_output_stem(options.output_stem, source.stem)
    directory = options.directory or source.parent
    scope.check()
    with _output_workspace(directory, ".frameflow-") as folder:
        temporary = folder / f"output.{extension}"
        common = [tools.ffmpeg, "-v", "error", "-nostdin", "-y",
                  "-progress", "pipe:1", "-nostats"]

        def run(args: list[str], start: int = 0, end: int = 99) -> None:
            def report(seconds: float) -> None:
                if info.duration > 0:
                    fraction = min(1.0, max(0.0, seconds / info.duration))
                    progress(start + round((end - start) * fraction))

            scope.run([*common, *args], on_seconds=report)

        limit = options.size_limit

        def encode(inputs: list[str], encoder: list[str], *, video: bool, audio: bool) -> None:
            retry = bool(limit and info.duration > 0)
            run([*inputs, *encoder, str(temporary)], 0, 48 if retry else 99)
            if not retry:
                return
            best = temporary.stat().st_size
            candidate = temporary.with_name(f"candidate.{extension}")
            bitrate = int(limit * 8 / info.duration * .90)
            previous_rates = None
            for attempt in range(3):
                if best <= limit:
                    break
                audio_rate = min(128_000, max(32_000, bitrate // 5 if video else bitrate)) if audio else 0
                video_rate = max(12_000, bitrate - audio_rate) if video else 0
                rates = video_rate, audio_rate
                if rates == previous_rates:
                    break
                previous_rates = rates
                tuned = _with_bitrates(encoder, *rates)
                start = 48 + attempt * 17
                try:
                    run([*inputs, *tuned, str(candidate)], start, start + 17)
                except Cancelled:
                    raise
                except MediaError:
                    # The baseline remains playable if the encoder rejects a low bitrate.
                    break
                size = candidate.stat().st_size
                if 0 < size < best:
                    candidate.replace(temporary)
                    best = size
                bitrate = int(bitrate * min(.8, limit / max(1, size) * .90))

        if isinstance(info, ImageInfo):
            _save_image_to_limit(source, temporary, _IMAGE_FORMATS[kind], scope,
                                 max_edge=options.max_edge, limit=limit, upscale=kind != "ico",
                                 quality=options.quality or 90, png_compression=options.png_compression)
        elif kind in _AUDIO_ENCODERS:
            track = info.stream if isinstance(info, AudioInfo) else info.audio_stream
            if track is None:
                raise MediaError("这个视频没有音轨，无法提取音频。")
            encode(["-i", str(source), "-map", f"0:{track}"], audio_encoder_args(options), video=False, audio=True)
        elif kind == "gif":
            stream_map = f"0:{info.stream}"
            small = (8, 240, 480, 48)
            profiles = [small if options.small_gif else (12, 480, 720, 96)]
            if limit:
                if not options.small_gif:
                    profiles.append(small)
                profiles.extend([(7, 160, 320, 40), (6, 120, 240, 32), (5, 96, 192, 24)])
            palette = Path(folder) / "palette.png"
            candidate = Path(folder) / "candidate.gif"
            best = None
            for index, (fps, width, height, colors) in enumerate(profiles):
                if options.max_edge:
                    ratio = (1, .75, .5, .33, .25)[index]
                    width = height = max(2, round(options.max_edge * ratio))
                start = index * 99 // len(profiles)
                end = (index + 1) * 99 // len(profiles)
                middle = start + round((end - start) * 0.35)
                fps_filter = f"fps={fps}:round=up" if index else f"fps={fps}"
                width_expr = str(width) if options.max_edge else f"'min(iw,{width})'"
                height_expr = str(height) if options.max_edge else f"'min(ih,{height})'"
                filters = (f"{fps_filter},scale=w={width_expr}:h={height_expr}:"
                           "force_original_aspect_ratio=decrease:"
                           "force_divisible_by=2:flags=lanczos")
                run(["-i", str(source), "-map", stream_map, "-vf",
                     f"{filters},palettegen=max_colors={colors}:stats_mode=diff",
                     "-frames:v", "1", "-update", "1", str(palette)], start, middle)
                run(["-i", str(source), "-i", str(palette), "-filter_complex",
                     f"[{stream_map}]{filters}[frames];"
                     "[frames][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle[out]",
                     "-map", "[out]", "-loop", "0", str(candidate)], middle, end)
                size = candidate.stat().st_size
                if size and (best is None or size < best):
                    candidate.replace(temporary)
                    best = size
                if best is not None and (not limit or best <= limit):
                    break
        else:
            filters = "pad=ceil(iw/2)*2:ceil(ih/2)*2"
            if options.max_edge:
                edge = max(2, options.max_edge // 2 * 2)
                width = f"max(2,round(iw*{edge}/max(iw,ih)/2)*2)"
                height = f"max(2,round(ih*{edge}/max(iw,ih)/2)*2)"
                filters = f"scale=w='{width}':h='{height}':flags=lanczos"
            if options.output_fps:
                filters += f",fps={options.output_fps}:eof_action=pass"
            inputs = ["-i", str(source), "-map", f"0:{info.stream}", "-map", "0:a:0?",
                      "-vf", filters, "-pix_fmt", "yuv420p"]
            encoder = video_encoder_args(kind, options.video_acceleration, options.quality, options.audio_bitrate)
            try:
                encode(inputs, encoder, video=True, audio=info.audio_stream is not None)
            except Cancelled:
                raise
            except MediaError:
                if options.video_acceleration == "cpu":
                    raise
                notice(f"{dict(HARDWARE_BACKENDS).get(options.video_acceleration, '硬件')}不可用，已回退 CPU 软件编码。")
                encode(inputs, video_encoder_args(kind, "cpu", options.quality, options.audio_bitrate), video=True,
                       audio=info.audio_stream is not None)
        scope.check()
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise MediaError("转换没有生成有效的输出文件。")
        return _publish(temporary, directory, stem, extension)
