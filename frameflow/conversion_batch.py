"""Background probing and batch export. Callbacks bridge results to the caller."""

from collections.abc import Callable, Sequence
from pathlib import Path

from .media import Cancelled, Export, ImageInfo, MediaError, MediaInfo, Processes, Tools, convert, probe, thumbnail
from .media_text import file_size

BatchTask = tuple[str, Path, MediaInfo, Export]


def inspect_media(key: str, path: Path, tools: Tools, scope: Processes, *,
                  inspected: Callable[[str, MediaInfo | None, str], None],
                  thumb_ready: Callable[[str, bytes], None]) -> None:
    try:
        info = probe(path, tools, scope)
    except Cancelled:
        return
    except (MediaError, OSError) as exc:
        inspected(key, None, str(exc))
        return
    inspected(key, info, "")
    try:
        image = thumbnail(path, info, tools, scope)
    except (MediaError, OSError):
        image = b""
    if not scope.cancelled.is_set():
        thumb_ready(key, image)


def run_batch(tasks: Sequence[BatchTask], tools: Tools, scope: Processes, *,
              phase: Callable[[str, str], None], progress: Callable[[str, int], None],
              exported: Callable[[str, Path | None, MediaInfo | None, str, str], None],
              completed: Callable[[bool], None]) -> None:
    try:
        for key, path, info, options in tasks:
            if scope.cancelled.is_set():
                break
            phase(key, "转换中")
            try:
                output = convert(path, info, options, tools, scope,
                                 lambda pct: progress(key, pct),
                                 notice=lambda message: phase(key, message))
            except Cancelled:
                break
            except (MediaError, OSError) as exc:
                exported(key, None, None, "", str(exc))
            else:
                output_info = None
                note = ""
                try:
                    size = output.stat().st_size
                    if options.size_limit:
                        target = file_size(options.size_limit)
                        if size <= options.size_limit:
                            note = f"已达到 {target} 目标"
                        else:
                            note = f"超过 {target} 目标，已保存可用的最小版本"
                    if options.format == "pdf":
                        output_info = ImageInfo(0, 0, "PDF", size)
                    else:
                        output_info = probe(output, tools, scope)
                except (MediaError, OSError):
                    pass  # 成品已保存，读取详情失败不影响结果
                exported(key, output, output_info, note, "")
    finally:
        completed(scope.cancelled.is_set())
