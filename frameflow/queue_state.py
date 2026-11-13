"""Media jobs shared by the queue and its consumers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from .media import Export, MediaInfo

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap


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


class QueueState:
    """Own job membership and the order shared by the views and batch export."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self.jobs = MappingProxyType(self._jobs)

    def add(self, job: Job) -> None:
        self._jobs[job.key] = job

    def remove(self, key: str) -> None:
        del self._jobs[key]

    def clear(self) -> None:
        self._jobs.clear()

    def move(self, key: str, slot: int) -> None:
        keys = list(self._jobs)
        keys.remove(key)
        keys.insert(slot, key)
        ordered = [(key, self._jobs[key]) for key in keys]
        self._jobs.clear()
        self._jobs.update(ordered)
