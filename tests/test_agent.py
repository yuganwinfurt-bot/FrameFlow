"""Agent regressions through the Qt workspace and FrameFlow's real queue."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDrag, QDragEnterEvent, QDropEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from frameflow.agent_api import PROBE_NAME
from frameflow.agent_ui import AttachmentCard
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

    def test_video_size_example_reaches_confirmable_plan(self):
        self.nav(2)
        self.settle()
        self.capture("agent-workspace.png")
        self.page.example_buttons[1].click()
        self.assertEqual(self.page.input.toPlainText(), "把视频压缩到 20 MB")
        agent = self.window.agent_tools
        key = self.keys[0]
        result = agent.dispatch("set_quality", json.dumps({"id": key, "target_mb": 20}))
        self.assertTrue(result["ok"])
        self.assertEqual(agent.get_media_info(key)["output"]["target_bytes"], 20_000_000)
        self.assertFalse(agent.dispatch("set_quality", json.dumps({"id": key}))["ok"])
        agent.prepare_conversion([key])
        self.assertEqual(agent.pending.entries[0].options.size_limit, 20_000_000)
        labels = [agent.pending.card.content.itemAt(i).widget() for i in range(agent.pending.card.content.count())]
        self.assertTrue(any("目标 20 MB" in label.text() for label in labels if hasattr(label, "text")))
        self.wait(agent.pending.card.confirm.isVisible)

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

    def test_top_navigation_fits_and_keeps_full_hit_areas(self):
        window = self.window
        nav = window.navigation

        def check_tabs():
            self.assertFalse(nav.usesScrollButtons())
            self.assertFalse(any(button.isVisible() for button in nav.findChildren(QToolButton)))
            self.assertEqual([nav.tabText(i) for i in range(nav.count())], ["格式转换", "媒体预览", "AI Agent"])
            for index in range(nav.count()):
                rect = nav.tabRect(index)
                self.assertTrue(nav.rect().contains(rect))
                self.assertGreaterEqual(rect.width(), nav.minimumTabSizeHint(index).width())
                self.assertEqual(nav.tabAt(rect.center()), index)

        for width, height in ((1177, 940), (942, 752), (940, 720)):
            window.resize(width, height)
            QTest.qWait(80)
            check_tabs()

        # Exercise the shrink path even when the main header has enough space for the preferred widths.
        original_maximum = nav.maximumWidth()
        nav.setMaximumWidth(nav.minimumSizeHint().width() + 12)
        QTest.qWait(80)
        check_tabs()
        self.assertLess(nav.width(), nav.sizeHint().width())
        for index in (1, 2, 0):
            self.nav(index)
            self.settle()
            self.assertEqual(nav.currentIndex(), index)
            check_tabs()
        nav.setMaximumWidth(original_maximum)
        for change_state in (window.showMaximized, window.showNormal):
            change_state()
            QTest.qWait(100)
            check_tabs()

    def test_hidden_scroll_bars_keep_wheel_keyboard_and_content_access(self):
        keys = self.add_attachment_samples(14)
        window, page = self.window, self.page
        window.resize(940, 720)
        QTest.qWait(80)

        def check_scroll(area):
            bar = area.verticalScrollBar()
            self.wait(lambda: bar.maximum() > 0)
            self.assertEqual(area.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.assertFalse(bar.isVisible())
            return bar

        def wheel(area, angle, pixels=0):
            viewport = area.viewport()
            local = viewport.rect().center()
            event = QWheelEvent(QPointF(local), QPointF(viewport.mapToGlobal(local)), QPoint(0, pixels),
                                QPoint(0, angle), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.ScrollUpdate if pixels else Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(viewport, event)

        for area in (window.list, window.inspector_scroll):
            bar = check_scroll(area)
            bar.setValue(0)
            wheel(area, -120)
            self.assertGreater(bar.value(), 0)

        self.nav(1)
        self.settle()
        editor = window.editors[keys[0]]
        settings = editor.settings_scroll
        bar = check_scroll(settings)
        bar.setValue(0)
        for _ in range(30):
            wheel(settings, -120)
        self.assertEqual(bar.value(), bar.maximum())
        field = editor.output_stem_edit
        self.assertTrue(field.isVisibleTo(settings))
        self.assertTrue(settings.viewport().rect().contains(field.mapTo(settings.viewport(), field.rect().center())))

        self.nav(2)
        self.settle()
        rail_bar = check_scroll(window.preview_files)
        rail_bar.setValue(0)
        wheel(window.preview_files, -120)
        self.assertGreater(rail_bar.value(), 0)

        for index in range(25):
            page.add_message(f"历史消息 {index} " * 16)
        history_bar = check_scroll(page.history)
        self.wait(lambda: history_bar.value() == history_bar.maximum())
        wheel(page.history, 120)
        self.assertLess(history_bar.value(), history_bar.maximum())
        old_value = history_bar.value()
        wheel(page.history, 0, 36)
        self.assertLess(history_bar.value(), old_value)

        page.input.setPlainText("a long draft\n" * 70)
        edit_bar = check_scroll(page.input)
        QTest.keyClick(page.input, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(edit_bar.value(), 0)
        wheel(page.input, -120)
        self.assertGreater(edit_bar.value(), 0)
        old_value = edit_bar.value()
        wheel(page.input, 0, -36)
        self.assertGreater(edit_bar.value(), old_value)
        QTest.keyClick(page.input, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(page.input.textCursor().position(), page.input.document().characterCount() - 1)
        self.assertTrue(page.input.viewport().rect().contains(page.input.cursorRect().center()))

        self.drop(keys, batch=True)
        attachment_bar = check_scroll(page.pending_area)
        wheel(page.pending_area, -120)
        self.assertGreater(attachment_bar.value(), 0)
        draft = page.input.toPlainText()
        QTest.mouseClick(page.clear_attachments_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: page.pending_area.isHidden() and page.input.height() == page.input.maximumHeight())
        self.assertEqual(page.input.toPlainText(), draft)
        self.nav(0)
        self.settle()
        check_scroll(window.list)
        self.nav(2)
        self.settle()
        check_scroll(page.history)
        check_scroll(page.input)

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

    def test_pending_cards_wrap_and_removal_preserves_editor_geometry(self):
        keys = self.add_attachment_samples(4, mixed=True)
        self.nav(2)
        self.settle()
        page = self.page
        area = page.pending_area
        editor = page.input
        QTest.keyClicks(editor, "first line")
        QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClicks(editor, "second line")
        QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        QTest.keyClicks(editor, "third line")
        self.wait(lambda: editor.height() > editor.minimumHeight())
        draft, text_height = editor.toPlainText(), editor.height()
        self.assertFalse(area.isVisible())
        empty_height = page.composer_panel.height()
        self.assertGreaterEqual(text_height, 42)
        cursor = editor.textCursor()
        cursor.setPosition(5)
        editor.setTextCursor(cursor)

        self.drop(keys[:1])
        self.wait(lambda: area.cards[keys[0]].isVisible())
        single_height = area.height()
        self.assertEqual(editor.height(), text_height)
        self.drop(keys[:2], batch=True)
        self.wait(lambda: area.cards[keys[1]].x() > area.cards[keys[0]].x())
        self.assertEqual(area.cards[keys[0]].y(), area.cards[keys[1]].y())
        self.assertEqual(area.height(), single_height)
        self.assertLess(area.cards[keys[0]].width(), area.width() // 2)

        self.drop(keys, batch=True)
        self.wait(lambda: area.height() > single_height and area.cards[keys[-1]].isVisible())
        self.assertGreater(area.cards[keys[-1]].y(), area.cards[keys[0]].y())
        self.assertIn("…", area.cards[keys[2]].name.text())
        portrait = area.cards[keys[-2]].thumbnail.pixmap()
        self.assertLess(portrait.width(), portrait.height())
        waveform = area.cards[keys[-1]].thumbnail.pixmap()
        self.assertGreater(waveform.width(), waveform.height())
        self.capture("attachment-wrap.png")

        retained = area.cards[keys[-1]]
        for key in (keys[2], keys[0]):
            QTest.mouseClick(area.cards[key].remove_button, Qt.MouseButton.LeftButton)
            self.wait(lambda: key not in area.cards)
            self.assertIs(area.cards[keys[-1]], retained)
            self.assertEqual(editor.height(), text_height)
            self.assertEqual(editor.toPlainText(), draft)
            self.assertEqual(editor.textCursor().position(), 5)
        self.capture("attachment-after-remove.png")

        self.window.resize(940, 720)
        self.wait(lambda: self.window.width() == 940)
        self.assertGreater(retained.y(), area.cards[keys[1]].y())
        for key in page.pending:
            QTest.mouseClick(area.cards[key].remove_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: not area.isVisible() and page.composer_panel.height() == empty_height)
        self.assertEqual(editor.height(), text_height)
        self.assertEqual(editor.toPlainText(), draft)
        self.assertEqual(editor.textCursor().position(), 5)
        self.assertTrue(all(job.path.is_file() for job in self.window.jobs.values()))
        QTest.keyClicks(editor, "!")
        self.assertEqual(editor.toPlainText(), draft[:5] + "!" + draft[5:])
        geometry = (area.height(), editor.height(), page.composer_panel.height())
        self.nav(1)
        self.settle()
        self.nav(2)
        self.settle()
        self.assertEqual((area.height(), editor.height(), page.composer_panel.height()), geometry)

    def test_pending_scroll_bulk_removal_and_readding_after_send(self):
        keys = self.add_attachment_samples(11)
        self.nav(2)
        self.settle()
        self.window.resize(940, 720)
        page, area = self.page, self.page.pending_area
        page.input.setPlainText("draft\n" * 12)
        self.wait(lambda: page.input.height() == page.input.maximumHeight())
        self.drop(keys, batch=True)
        bar = area.verticalScrollBar()
        self.wait(lambda: bar.maximum() > 0)
        cap = area.height()
        self.assertLess(cap, area.body.height())
        self.assertGreaterEqual(page.input.height(), page.input.fontMetrics().lineSpacing() * 4 + 10)
        self.assertGreater(page.input.y(), area.geometry().bottom())
        self.assertLess(page.input.geometry().bottom(), page.send_button.mapTo(page.composer_panel, QPoint()).y())
        self.capture("attachment-scroll-narrow.png")
        bar.setValue(bar.maximum())
        self.wait(lambda: area.cards[keys[-1]].mapTo(area.viewport(), QPoint(0, 0)).y() >= 0)
        QTest.mouseClick(area.cards[keys[-1]].remove_button, Qt.MouseButton.LeftButton)
        self.assertNotIn(keys[-1], page.pending)

        for maximized in (True, False):
            if maximized:
                self.window.showMaximized()
            else:
                self.window.showNormal()
            self.wait(lambda: self.window.isMaximized() == maximized)
            # Windows can report the new state before the native resize and button layout arrive.
            QTest.qWait(80)
            QTest.mouseClick(page.clear_attachments_button, Qt.MouseButton.LeftButton)
            self.wait(lambda: not area.isVisible() and area.height() == 0)
            self.assertEqual(page.input.toPlainText(), "draft\n" * 12)
            self.assertEqual(page.input.height(), page.input.maximumHeight())
            self.drop(keys[:2], batch=True)
            self.wait(lambda: area.isVisible())
            self.assertEqual(bar.value(), 0)
            self.assertLess(area.height(), cap)

        page.submitted.disconnect(self.window.agent_session.submit)
        page.before_submit = lambda: True
        sent = []
        page.submitted.connect(lambda text, items: sent.append((text, tuple(item.key for item in items))))
        QTest.mouseClick(page.send_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: not area.isVisible() and bool(sent))
        self.assertEqual(sent, [("draft\n" * 11 + "draft", keys[:2])])
        self.assertEqual(len(page.bubbles[-1].findChildren(AttachmentCard)), 2)
        self.assertEqual(page.input.toPlainText(), "")
        self.drop(keys[2:3])
        self.wait(lambda: area.isVisible() and page.pending == keys[2:3])
        self.assertEqual(tuple(area.cards), keys[2:3])
        self.assertEqual(bar.value(), 0)

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
