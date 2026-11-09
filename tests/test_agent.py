"""Agent regressions through the Qt workspace and FrameFlow's real queue."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDrag, QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from frameflow.media import Processes, Tools
from frameflow.motion import QUEUED_MEDIA, QUEUED_MEDIA_BATCH
from frameflow.window import MainWindow


class AgentWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.tools = Tools.find()

    def setUp(self):
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

    def test_agent_transition_preserves_queue_and_preview(self):
        window = self.window
        transition = window.page_transition
        self.nav(2)
        transition.animation.pause()
        transition.animation.setCurrentTime(180)
        self.assertGreater(transition.position, 0)
        self.assertLess(window.agent_page.x(), window.page_host.width())
        self.assertTrue(window.pages.isHidden())
        self.assertGreater(window.preview_rail.width(), window._rail_width())
        transition.animation.resume()
        self.settle()
        self.assertEqual(window.preview_rail.width(), window._rail_width())

        self.nav(1)
        self.settle()
        editor = window.pages.currentWidget()
        self.wait(lambda: editor.frames and editor.scope is None)
        editor._locate_frame(12)
        rail = window.preview_rail.geometry()
        self.nav(2)
        transition.agent_animation.pause()
        transition.agent_animation.setCurrentTime(180)
        self.assertEqual(window.preview_rail.geometry(), rail)
        self.assertTrue(window.pages.isVisible())
        self.assertGreater(self.page.x(), window.pages.x())
        middle = self.page.geometry()
        self.nav(1)
        self.assertEqual(self.page.geometry(), middle)
        transition.agent_animation.resume()
        self.settle()
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(editor.cursor.value(), 12)

        for index in (2, 0, 1, 2, 1) * 4:
            self.nav(index)
            QTest.qWait(25)
        window.resize(940, 720)
        window.showMaximized()
        window.showNormal()
        self.settle()
        self.assertEqual(window.navigation.currentIndex(), 1)
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(window._selected().key, self.keys[0])
        self.assertTrue(self.page.isHidden())

    def test_attachment_drag_and_messages(self):
        self.nav(2)
        self.settle()
        rail = self.window.preview_files
        first, second = (rail.visualItemRect(rail.item(index)).center() for index in (0, 1))
        QTest.mouseClick(rail.viewport(), Qt.MouseButton.LeftButton, pos=second)
        self.assertEqual(self.window._selected().key, self.keys[1])
        self.assertFalse(self.page.pending)
        QTest.mouseClick(rail.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier, pos=first)
        seen = []

        def drag_started(drag, _actions):
            seen.extend(json.loads(bytes(drag.mimeData().data(QUEUED_MEDIA_BATCH))))
            return Qt.DropAction.CopyAction

        QTest.mousePress(rail.viewport(), Qt.MouseButton.LeftButton, pos=second)
        move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(second + QPoint(40, 0)),
                           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        with patch.object(QDrag, "exec", drag_started):
            self.app.sendEvent(rail.viewport(), move)
        QTest.mouseRelease(rail.viewport(), Qt.MouseButton.LeftButton, pos=second)
        self.assertCountEqual(seen, self.keys)

        self.drop(self.keys, batch=True)
        self.drop(self.keys)
        self.assertEqual(self.page.pending, self.keys)
        self.assertTrue(self.page.pending_area.isVisible())
        self.assertFalse(self.page.bubbles)
        self.page.remove_attachment(self.keys[1])
        self.assertTrue(self.second.is_file())
        self.assertIn(self.keys[1], self.window.jobs)
        submitted = []
        self.page.submitted.connect(lambda text, items: submitted.append((text, items)))
        QTest.keyClick(self.page.input, Qt.Key.Key_Return)
        self.assertEqual((submitted[0][0], submitted[0][1][0].key), ("", self.keys[0]))
        self.assertFalse(self.page.pending_area.isVisible())
        self.assertIsNone(self.page.bubbles[0].content)

        QTest.keyClicks(self.page.input, "1")
        QTest.keyClick(self.page.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertIn("\n", self.page.input.toPlainText())
        QTest.keyClick(self.page.input, Qt.Key.Key_Return)
        user = self.page.bubbles[-1]
        assistant = self.page.add_message("**已读取**\n\n- 媒体信息\n\n```\nmp4\n```")
        self.app.processEvents()
        self.assertLess(user.width(), 100)
        self.assertGreater(user.x(), assistant.x())
        self.assertIn("媒体信息", assistant.content.toPlainText())

        self.page.add_attachments(self.keys)
        self.nav(0)
        self.settle()
        self.nav(2)
        self.settle()
        self.assertEqual(self.page.pending, self.keys)
        self.assertEqual(len(self.page.bubbles), 3)
        self.window._remove_job(self.keys[0])
        self.assertEqual(self.page.pending, (self.keys[1],))
        self.assertTrue(self.first.is_file())
