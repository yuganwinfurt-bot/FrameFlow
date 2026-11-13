"""Format media metadata for the desktop queue and preview panels."""

from .media import AudioInfo, ImageInfo, MediaInfo


def duration(seconds: float) -> str:
    if seconds <= 0:
        return "未知"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes:02}:{rest:02}" if seconds >= 60 else f"{seconds:.1f} 秒"


def metadata(info: MediaInfo) -> str:
    if isinstance(info, ImageInfo):
        return f"{info.width}×{info.height}  ·  {info.codec}" if info.width and info.height else info.codec
    if isinstance(info, AudioInfo):
        fields = [info.codec, duration(info.duration)]
        if info.sample_rate:
            fields.append(f"{info.sample_rate / 1000:g} kHz")
        if info.channels:
            fields.append({1: "单声道", 2: "立体声"}.get(info.channels, f"{info.channels} 声道"))
        return "  ·  ".join(fields)
    rate = f"{info.fps:.2f}".rstrip("0").rstrip(".") + " 帧/秒" if info.fps else "帧率未知"
    return f"{info.width}×{info.height}  ·  {duration(info.duration)}  ·  {info.codec}  ·  {rate}"


def file_size(size: int) -> str:
    return f"{size / 1_000_000:.2f} MB" if size >= 1_000_000 else f"{size / 1_000:.1f} KB"


def clock(seconds: float) -> str:
    """表格里的时长列：统一成 00:00 形式，未知就给一个占位符。"""
    if not seconds or seconds <= 0:
        return "—"
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def rail_meta(job) -> str:
    """媒体预览左侧队列的第二行：时长 · 大小。"""
    if not job.info:
        return "正在读取…" if job.state == "读取中" else "无法读取"
    parts = []
    length = clock(getattr(job.info, "duration", 0))
    if length != "—":
        parts.append(length)
    parts.append(file_size(job.info.size))
    return " · ".join(parts)


def summary(info: MediaInfo) -> str:
    """行内的一行摘要：时长已经单独成列，这里不再重复。"""
    if isinstance(info, ImageInfo):
        return f"{info.width}×{info.height}  ·  {info.codec}" if info.width and info.height else info.codec
    if isinstance(info, AudioInfo):
        fields = [info.codec]
        if info.sample_rate:
            fields.append(f"{info.sample_rate / 1000:g} kHz")
        if info.channels:
            fields.append({1: "单声道", 2: "立体声"}.get(info.channels, f"{info.channels} 声道"))
        return "  ·  ".join(fields)
    rate = f"{info.fps:g} 帧/秒" if info.fps else "帧率未知"
    return f"{info.width}×{info.height}  ·  {info.codec}  ·  {rate}"
