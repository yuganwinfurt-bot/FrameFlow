"""Shared real-media setup for the Agent workspace tests."""

import json
import os
import tempfile
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from frameflow.media import Processes, Tools
from frameflow.motion import QUEUED_MEDIA, QUEUED_MEDIA_BATCH
from frameflow.window import MainWindow
from qt_test_support import GuiTestCase


class AgentWorkspaceFixture(GuiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tools = Tools.find()

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory(prefix="FrameFlow Agent ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = self.root / "第一段.mp4"
        self.second = self.root / "第二段.mp4"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                         "testsrc2=size=160x90:rate=24", "-t", "2", "-c:v", "libx264", str(self.first)])
        self.second.write_bytes(self.first.read_bytes())
        self.window = MainWindow(self.tools)
        self.addCleanup(self.window.close)
        self.window.resize(1200, 780)
        self.window.show()
        self.window.add_paths([self.first, self.second])
        self.wait(lambda: all(job.info and job.picture for job in self.window.jobs.values()))
        self.keys = tuple(self.window.jobs)
        self.page = self.window.agent_page

    def wait(self, condition, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(15)
        self.fail("等待状态超时")

    def nav(self, index):
        nav = self.window.navigation
        QTest.mouseClick(nav, Qt.MouseButton.LeftButton, pos=nav.tabRect(index).center())

    def settle(self):
        self.wait(lambda: not self.window.page_transition.running)

    def capture(self, name):
        if output := os.environ.get("FRAMEFLOW_AGENT_CAPTURE_DIR"):
            self.app.processEvents()
            self.assertTrue(self.window.grab().save(str(Path(output) / name)))

    def drop(self, keys, *, batch=False):
        mime = QMimeData()
        mime.setData(QUEUED_MEDIA, keys[0].encode("ascii"))
        if batch:
            mime.setData(QUEUED_MEDIA_BATCH, json.dumps(keys).encode("ascii"))
        enter = QDragEnterEvent(QPoint(90, 90), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(self.page, enter)
        self.assertTrue(enter.isAccepted())
        self.assertTrue(self.page.property("dragging"))
        event = QDropEvent(QPointF(90, 90), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(self.page, event)
        self.assertTrue(event.isAccepted())
        self.assertFalse(self.page.property("dragging"))

    def add_attachment_samples(self, video_count, *, mixed=False):
        extra = []
        for index in range(video_count):
            path = self.root / f"一段文件名称很长的演示视频-{index:02d}.mp4"
            path.write_bytes(self.first.read_bytes())
            extra.append(path)
        if mixed:
            image = self.root / "竖屏图片.png"
            Image.new("RGB", (120, 200), "#277ad9").save(image)
            audio = self.root / "音频波形.wav"
            Processes().run([self.tools.ffmpeg, "-v", "error", "-f", "lavfi",
                             "-i", "sine=frequency=440", "-t", "1", str(audio)])
            extra.extend((image, audio))
        self.window.add_paths(extra)
        self.wait(lambda: len(self.window.jobs) == len(self.keys) + len(extra)
                  and all(job.info and job.picture for job in self.window.jobs.values()))
        return tuple(self.window.jobs)
