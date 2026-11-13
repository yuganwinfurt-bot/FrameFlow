"""Locate bundled tools and manage owned FFmpeg/FFprobe subprocesses."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
# PyInstaller 冻结后 __file__ 指向 _MEIPASS，优先用解包目录定位随包资源
PROJECT = Path(getattr(sys, "_MEIPASS", _SOURCE_ROOT))


class MediaError(RuntimeError):
    pass


class Cancelled(MediaError):
    pass


@dataclass(frozen=True)
class Tools:
    ffmpeg: str
    ffprobe: str

    @classmethod
    def find(cls) -> Tools:
        paths = []
        for name in ("ffmpeg", "ffprobe"):
            local = PROJECT / "bin" / (f"{name}.exe" if os.name == "nt" else name)
            paths.append(str(local) if local.is_file() else shutil.which(name))
        if not all(paths):
            raise MediaError("未找到 FFmpeg 和 FFprobe。请在项目 bin 文件夹放入两个工具，或将它们加入 PATH。")
        return cls(*paths)


class Processes:
    """一个可取消的任务范围，只持有自己启动的子进程。"""

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._active: set[subprocess.Popen] = set()

    def check(self) -> None:
        if self.cancelled.is_set():
            raise Cancelled("任务已取消")

    @staticmethod
    def _kill(child: subprocess.Popen) -> None:
        if child.poll() is None:
            try:
                child.kill()
            except OSError:
                pass

    def cancel(self) -> None:
        self.cancelled.set()
        with self._lock:
            for child in self._active:
                self._kill(child)

    def run(
        self, args: Sequence[str], *, timeout: float | None = None,
        on_seconds: Callable[[float], None] | None = None,
        read_stdout: Callable[[BinaryIO], None] | None = None,
    ) -> bytes:
        # 单独保存错误输出，避免 FFmpeg 写满 stderr 管道后阻塞进度读取。
        with tempfile.TemporaryFile() as stderr:
            with self._lock:
                self.check()
                try:
                    child = subprocess.Popen(
                        list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=stderr,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except OSError as exc:
                    raise MediaError(f"无法启动媒体工具：{exc}") from exc
                self._active.add(child)
            try:
                if read_stdout is not None:
                    assert child.stdout is not None
                    read_stdout(child.stdout)
                    child.wait()
                    data = b""
                elif on_seconds is None:
                    data, _ = child.communicate(timeout=timeout)
                else:
                    assert child.stdout is not None
                    for line in child.stdout:
                        key, _, value = line.strip().partition(b"=")
                        if key == b"out_time_us":
                            try:
                                seconds = int(value) / 1_000_000
                            except ValueError:
                                continue
                            on_seconds(seconds)
                    child.wait()
                    data = b""
                self.check()
                if child.returncode:
                    end = stderr.seek(0, os.SEEK_END)
                    stderr.seek(max(0, end - 2400))
                    detail = stderr.read().decode("utf-8", errors="replace").strip()
                    raise MediaError(detail or f"媒体工具退出代码：{child.returncode}")
                return data
            except subprocess.TimeoutExpired as exc:
                raise MediaError(f"媒体工具未在 {timeout:g} 秒内响应") from exc
            finally:
                self._kill(child)
                child.wait()
                if child.stdout is not None:
                    child.stdout.close()
                with self._lock:
                    self._active.discard(child)
