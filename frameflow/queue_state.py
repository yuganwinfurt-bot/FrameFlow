"""Media jobs shared by the queue and its consumers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QPixmap

from .media import Export, MediaInfo


@dataclass
class Job:
    key: str
    path: Path
    info: MediaInfo | None = None
    picture: QPixmap | None = None
    output: Path | None = None
    output_info: MediaInfo | None = None
    output_note: str = ""
    state: str = "读取中"
    detail: str = ""
    percent: int = 0
    queued: bool = True
    output_format: str = ""
    output_stem: str = ""
    export_settings: Export | None = None
