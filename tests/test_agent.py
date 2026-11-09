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

from frameflow.agent_api import PROBE_NAME
from frameflow.media import Processes, Tools, probe
from frameflow.motion import QUEUED_MEDIA, QUEUED_MEDIA_BATCH
from frameflow.window import MainWindow
from test_agent_api import call, mock_model, response


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
        self.capture("02-Agent-转场中间帧.png")
        middle = self.page.geometry()
        self.nav(1)
        self.assertEqual(self.page.geometry(), middle)
        transition.agent_animation.resume()
        self.settle()
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(editor.cursor.value(), 12)

        editor._toggle_playback()
        self.wait(lambda: editor.playback is not None)
        self.nav(2)
        self.settle()
        paused_frame = editor.cursor.value()
        self.assertIsNone(editor.playback)
        self.nav(1)
        self.settle()
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(editor.cursor.value(), paused_frame)

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
        self.page.submitted.disconnect(self.window.agent_session.submit)
        self.page.before_submit = lambda: True
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
        self.assertLess(user.height(), 65)
        self.assertLess(assistant.height(), 160)
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
        self.window.agent_session.clear()
        self.assertEqual(tuple(self.window.jobs), (self.keys[1],))
        self.assertFalse(self.page.bubbles)

    def test_markdown_wrap_and_scrolling_respect_user_position(self):
        self.nav(2)
        self.settle()
        block = self.page.add_message("```text\n" + "unbroken_code_" * 100 + "\n```")
        self.app.processEvents()
        self.assertGreater(block.content.document().begin().layout().lineCount(), 1)
        for _ in range(18):
            self.page.add_message("历史消息 " * 24)
            self.app.processEvents()
        bar = self.page.history.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        self.wait(lambda: bar.maximum() == bar.value())
        bar.setValue(0)
        self.page.add_message("这条消息不应把正在看历史的用户拉到底部。")
        QTest.qWait(50)
        self.assertEqual(bar.value(), 0)

    def test_stop_button_aborts_model_turn(self):
        def model(body):
            if body.get("tool_choice"):
                return 200, response(None, call(PROBE_NAME))
            time.sleep(.5)
            return 200, response("被停止的回复")

        self.nav(2)
        self.settle()
        with mock_model(model) as (config, requests):
            self.window.agent_session.config = config
            self.page.input.setPlainText("读取附件")
            self.page.send()
            self.wait(lambda: len(requests) == 2)
            QTest.mouseClick(self.page.stop, Qt.MouseButton.LeftButton)
            self.wait(lambda: not self.page.busy)
            QTest.qWait(550)
            self.assertEqual(len(self.page.bubbles), 1)
            self.assertIn("已停止", self.page.activity.text())
            self.assertIsNone(self.window.scope)

    def test_model_tools_require_confirmation_then_export_real_hevc(self):
        self.nav(1)
        self.settle()
        editor = self.window.pages.currentWidget()
        self.wait(lambda: editor.frames and editor.scope is None)
        self.nav(2)
        self.settle()
        agent = self.window.agent_tools
        key = self.keys[0]
        out = self.root / "授权输出"
        out.mkdir()
        with patch("frameflow.agent_tools.QFileDialog.getExistingDirectory", return_value=str(out)):
            self.page.directory_button.click()

        def model(body):
            if body.get("tool_choice"):
                return 200, response(None, call(PROBE_NAME))
            tail = body["messages"][-1]
            if tail["role"] == "user":
                return 200, response(None, call("list_media", call_id="list"),
                                     call("get_media_info", {"id": key}, "info"))
            if tail["tool_call_id"] == "info":
                return 200, response(None,
                    call("select_media", {"id": key}, "select"),
                    call("set_output_format", {"id": key, "format": "mp4", "codec": "hevc"}, "format"),
                    call("set_resolution", {"id": key, "long_edge": 160}, "size"),
                    call("set_quality", {"id": key, "quality": 19}, "quality"),
                    call("set_output_directory", {"id": key, "directory": "approved"}, "directory"),
                    call("set_output_filename", {"id": key, "filename": "测试 Agent (最终).mp4"}, "filename"))
            if tail["tool_call_id"] == "filename":
                return 200, response(None, call("prepare_conversion", {"ids": [key]}, "prepare"))
            if tail["tool_call_id"] == "prepare":
                plan_id = json.loads(tail["content"])["result"]["plan_id"]
                return 200, response(None, call("start_conversion", {"plan_id": plan_id}, "start"))
            return 200, response("已生成 **MP4 / H.265** 计划，请确认。")

        with mock_model(model) as (config, requests):
            self.window.agent_session.config = config
            self.drop([key])
            self.page.input.setPlainText("把附件转成 MP4 H.265，保留清晰度")
            self.page.send()
            self.wait(lambda: not self.page.busy and agent.pending is not None)
            plan = agent.pending
            packets = [payload for _, _, payload in requests]
            self.assertEqual(len(packets), 6)
            sent = json.loads(packets[1]["messages"][-1]["content"])
            self.assertEqual(sent["attachments"], [{"id": key, "name": self.first.name}])
            self.assertNotIn(str(self.root), str(packets))
            self.assertIn("HEVC", str(packets[-1]).upper())
            result = json.loads(packets[-1]["messages"][-1]["content"])
            self.assertTrue(result["result"]["user_confirmation_required"])
            self.assertIsNone(self.window.scope)
            self.assertEqual(list(out.iterdir()), [])
            self.assertEqual(self.window.rows[key].format.currentData(), "mp4_hevc")
            self.capture("01-Agent-待确认计划.png")
            self.nav(0)
            self.settle()
            self.assertIs(self.window.mounted_editor, editor)
            self.assertEqual(editor.output_options().quality, 19)
            self.nav(1)
            self.settle()
            self.assertEqual(editor.output_options().max_edge, 160)
            self.assertEqual(editor.output_stem_edit.text(), "测试 Agent (最终)")
            self.assertEqual(editor.directory, out)
            self.nav(2)
            self.settle()
            QTest.mouseClick(plan.card.confirm, Qt.MouseButton.LeftButton)
            self.assertIsNotNone(self.window.scope)
            self.wait(lambda: agent.active is None, seconds=20)
            expected = out / "测试 Agent (最终).mp4"
            media = probe(expected, self.tools, Processes())
            self.assertEqual((media.codec, media.width, media.height), ("HEVC", 160, 90))
            self.assertIn("mp4", media.container.split(","))
            self.assertEqual(plan.rows[key]["output"], expected.name)
            self.assertEqual(agent.get_task_status(plan.id)["status"], "finished")
            self.assertIsNone(self.window.jobs[self.keys[1]].output)
            self.capture("03-Agent-真实转换完成.png")

    def test_agent_rejects_paths_invalid_parameters_and_stale_confirmation(self):
        agent = self.window.agent_tools
        key = self.keys[0]
        for name, data in (
            ("get_media_info", {"id": str(self.first)}),
            ("set_output_directory", {"id": key, "directory": str(self.root / "unauthorized")}),
            ("set_output_filename", {"id": key, "filename": "../outside.mp4"}),
            ("set_resolution", {"id": key, "long_edge": True}),
            ("set_output_format", {"id": key, "format": "mp4", "command": "untrusted"}),
            ("run_shell", {}),
        ):
            with self.subTest(name=name):
                self.assertFalse(agent.dispatch(name, json.dumps(data))["ok"])
        agent.prepare_conversion([key])
        plan = agent.pending
        agent.set_quality(key, 20)
        plan.card.confirm.click()
        self.assertIsNone(self.window.scope)
        self.assertIsNone(agent.pending)
        self.assertIn("失效", plan.card.title.text())

    def test_agent_reports_failed_and_cancelled_jobs(self):
        self.nav(2)
        self.settle()
        agent = self.window.agent_tools
        key = self.keys[0]
        self.first.write_bytes(b"invalid video content")
        agent.prepare_conversion([key])
        failed = agent.pending
        failed.card.confirm.click()
        self.wait(lambda: agent.active is None)
        self.assertEqual(agent.get_task_status(failed.id)["status"], "finished_with_errors")
        self.assertIn("转换失败", failed.card.rows[key][0].text())
        self.assertIsNone(self.window.jobs[key].output)

        agent.set_output_format(self.keys[1], "webm")
        agent.set_resolution(self.keys[1], long_edge=3840)
        agent.prepare_conversion([self.keys[1]])
        cancelled = agent.pending
        cancelled.card.confirm.click()
        self.assertIsNotNone(self.window.scope)
        self.nav(1)
        self.nav(0)
        self.nav(2)
        self.assertEqual(agent.cancel_conversion(cancelled.id)["status"], "cancelling")
        self.wait(lambda: agent.active is None)
        self.assertEqual(agent.get_task_status(cancelled.id)["status"], "cancelled")
        self.assertFalse(list(self.root.glob(".frameflow-*")))
        self.assertTrue(self.second.is_file())
