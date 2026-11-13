"""Media size estimates independent of Qt and the editor timeline."""

from dataclasses import dataclass

from .media import Export, VideoInfo, audio_encoder_args


@dataclass(frozen=True)
class SizeEstimate:
    basis: str
    size_bytes: float | None = None
    upper_bytes: float | None = None


def estimate_audio(options: Export, *, duration: float, sample_rate: int, channels: int) -> SizeEstimate:
    if not duration:
        return SizeEstimate("当前没有可用于估算的音轨时长。")
    if options.format == "flac":
        return SizeEstimate("FLAC 的无损压缩率取决于实际采样内容。")
    if options.format == "wav":
        sample_rate = options.audio_sample_rate or sample_rate
        channels = options.audio_channels or channels
        if not sample_rate or not channels:
            return SizeEstimate("缺少 WAV 输出的采样率或声道数。")
        estimate = duration * sample_rate * channels * 2 + 128
        basis = f"{duration:.3f} 秒 × {sample_rate} Hz × {channels} 声道 × 2 字节，加上 WAV 文件头。"
    else:
        args = audio_encoder_args(options)
        if "-b:a" not in args or options.size_limit:
            return SizeEstimate("可变码率或目标体积模式的最终码率取决于实际编码。")
        rate_arg = args[args.index("-b:a") + 1]
        bitrate = int(rate_arg.removesuffix("k")) * (1000 if rate_arg.endswith("k") else 1)
        overhead = 1024 + (duration * 192 if options.format == "m4a" else 0)
        estimate = duration * bitrate / 8 + overhead
        basis = f"{duration:.3f} 秒 × {bitrate / 1000:g} kbps ÷ 8，加上少量封装开销；实际码率可能波动。"
    return SizeEstimate(basis, estimate)


def estimate_video(source: VideoInfo, options: Export, *, duration: float, timeline_fps: float,
                   audio_duration: float, original_only: bool) -> SizeEstimate:
    # 用源视频码率作参考，再根据两种编码的相对效率、画质和输出像素吞吐量调整。
    codecs = {"H264": 1.0, "HEVC": .7, "VP9": .75, "MPEG4": 1.4}
    output_codec = {"mp4": "H264", "mov": "H264", "mkv": "H264", "mp4_hevc": "HEVC",
                    "mkv_hevc": "HEVC", "webm": "VP9"}.get(options.format)
    input_factor, output_factor = codecs.get(source.codec), codecs.get(output_codec)
    if not (duration and source.bitrate and source.fps and input_factor and output_factor and
            original_only and options.video_acceleration == "cpu" and not options.size_limit):
        return SizeEstimate("缺少可比的源视频码率，或当前编码、混合素材及目标体积设置无法据此估算。")
    fps = options.output_fps or timeline_fps
    size_ratio = (options.max_edge / max(source.width, source.height)) ** 1.4 if options.max_edge else 1
    nominal_crf = {"H264": 23, "HEVC": 26, "VP9": 32}[output_codec]
    quality_ratio = 2 ** ((nominal_crf - (options.quality or nominal_crf)) / 6)
    video_bytes = (duration * source.bitrate / 8 * output_factor / input_factor *
                   size_ratio * (fps / source.fps) ** .65 * quality_ratio)
    audio_bytes = audio_duration * (options.audio_bitrate or 128) * 1000 / 8
    basis = (f"按 {duration:.3f} 秒、编码器、画质、分辨率和帧率估算，另计音轨。"
             "恒定质量编码的实际大小随画面内容变化，可能超出此范围。")
    return SizeEstimate(basis, video_bytes * .55 + audio_bytes, video_bytes * 1.8 + audio_bytes)
