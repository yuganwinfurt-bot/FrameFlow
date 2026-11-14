"""Agent message rendering and media attachments."""

import json
import time
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDrag, QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from frameflow.agent_api import PROBE_NAME
from frameflow.agent_ui import AttachmentCard
from frameflow.media import Processes
from frameflow.motion import QUEUED_MEDIA, QUEUED_MEDIA_BATCH
from agent_test_support import call, mock_model, response
from agent_workspace_support import AgentWorkspaceFixture


class AgentChatTest(AgentWorkspaceFixture):
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
        outside = QPoint(rail.viewport().width() + 12, second.y())
        move = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(outside),
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
