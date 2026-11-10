"""Model provider settings and Agent layout regressions."""

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from frameflow.agent_api import ModelConfig, ModelSettings, PROBE_NAME
from frameflow.agent_settings_ui import ModelDialog
from frameflow.agent_ui import AgentPage, CHAT_WIDTH
from frameflow.model_providers import CUSTOM, PROVIDERS, tool_parameters
from frameflow.theme import app_palette
from test_agent_api import call, mock_model, response


class AgentUxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setPalette(app_palette())
        if sys.platform == "win32":
            cls.app.setFont(QFont("Microsoft YaHei UI", 9))
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = str(Path(temp.name) / "agent.ini")
        self.settings = self.open_settings()

    def open_settings(self):
        return ModelSettings(QSettings(self.path, QSettings.Format.IniFormat))

    def dialog(self, config=None, provider=None):
        provider = provider or self.settings.active_provider()
        dialog = ModelDialog(self.settings, provider, config or self.settings.load(), None)
        self.addCleanup(dialog.close)
        dialog.show()
        self.app.processEvents()
        return dialog

    def wait(self, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(10)
        self.fail("等待状态超时")

    def capture(self, widget, name):
        if folder := os.environ.get("FRAMEFLOW_AGENT_CAPTURE_DIR"):
            self.assertTrue(widget.grab().save(str(Path(folder) / name)))

    def test_providers_restore_default_url_and_editable_profiles(self):
        saved = ModelConfig("https://example.invalid/v1", "manually-entered", timeout_ms=63000)
        self.settings.save(saved, "openai")
        dialog = self.dialog(provider="deepseek", config=self.settings.load("deepseek"))
        self.assertEqual(dialog.providers.count(), 8)
        for name, preset in PROVIDERS.items():
            dialog.providers.setCurrentIndex(dialog.providers.findData(name))
            self.assertEqual(dialog.url.text(), saved.base_url if name == "openai" else preset.url)
            self.assertEqual(dialog.url_row.isVisible(), name == CUSTOM)
            self.assertTrue(dialog.model.isEditable())
        dialog.providers.setCurrentIndex(dialog.providers.findData("openai"))
        self.assertEqual(dialog.model.currentText(), saved.model)
        self.assertEqual(dialog.timeout.value(), 63)
        initial_height = dialog.height()
        self.capture(dialog, "model-settings.png")
        dialog.advanced.click()
        self.app.processEvents()
        self.assertTrue(dialog.url_row.isVisible())
        self.assertTrue(dialog.timeout_row.isVisible())
        self.assertGreater(dialog.height(), initial_height)
        self.assertTrue(dialog.rect().contains(dialog.save_button.geometry()))
        self.capture(dialog, "model-settings-advanced.png")
        dialog.model.setEditText("new-model-id")
        dialog.providers.setCurrentIndex(dialog.providers.findData("deepseek"))
        dialog.providers.setCurrentIndex(dialog.providers.findData("openai"))
        self.assertEqual(dialog.model.currentText(), "new-model-id")
        self.assertEqual(self.open_settings().load(), saved)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_encrypted_keys_are_isolated_restored_and_bound_to_endpoint(self):
        deepseek = ModelConfig(PROVIDERS["deepseek"].url, "deepseek-flash", "fixture-secret-a")
        openai = ModelConfig(PROVIDERS["openai"].url, "gpt-5.6-luna", "fixture-secret-b", 66000)
        self.settings.save(deepseek, "deepseek")
        self.settings.save(openai, "openai")
        reopened = self.open_settings()
        self.assertEqual(reopened.active_provider(), "openai")
        self.assertEqual(reopened.load(), openai)
        self.assertEqual(reopened.load("deepseek"), deepseek)
        self.assertNotIn("fixture-secret", Path(self.path).read_text(encoding="utf-8"))
        dialog = self.dialog()
        self.assertEqual(dialog.key.echoMode(), QLineEdit.EchoMode.Password)
        dialog.reveal.click()
        self.assertEqual(dialog.key.echoMode(), QLineEdit.EchoMode.Normal)
        dialog.providers.setCurrentIndex(dialog.providers.findData("deepseek"))
        self.assertEqual(dialog.key.text(), deepseek.api_key)
        self.assertEqual(dialog.key.echoMode(), QLineEdit.EchoMode.Password)
        dialog.url.setText("https://override.example.invalid/v1")
        self.assertFalse(dialog.config().api_key)
        dialog._check_url()
        self.assertFalse(dialog.key.text())

    def test_live_connection_status_and_error_classification(self):
        handlers = (
            (lambda _: (200, response(None, call(PROBE_NAME, {}, "probe"))), "连接成功", "工具调用响应"),
            (lambda _: (200, response("plain reply")), "连接失败", "未返回工具调用"),
            (lambda _: (404, {"error": {"code": "model_not_found"}}), "连接失败", "模型不存在"),
            (lambda _: (404, {"error": {"message": "synthetic-test-token"}}), "连接失败", "Base URL"),
            (lambda _: (400, {"error": {"message": "tool_choice unsupported synthetic-test-token"}}),
             "连接失败", "Tool Calling"),
        )
        for handler, state, detail in handlers:
            with self.subTest(detail=detail), mock_model(handler) as (config, requests):
                dialog = self.dialog(config=config, provider=CUSTOM)
                dialog.test_button.click()
                self.assertEqual(dialog.status.text(), "正在测试")
                self.assertTrue(dialog.loading.isVisible())
                self.assertFalse(dialog.save_button.isEnabled())
                self.wait(lambda: dialog.status.text() == state)
                self.assertIn(detail, dialog.note.text())
                self.assertNotIn(config.api_key, dialog.note.text())
                self.assertTrue(dialog.save_button.isEnabled())
                self.assertEqual(requests[0][0], "/v1/chat/completions")
                dialog.close()

    def test_documented_tool_modes(self):
        self.assertEqual(tool_parameters("deepseek", "deepseek-flash"), {"thinking": {"type": "disabled"}})
        self.assertEqual(tool_parameters("openai", "gpt-5.6-luna"), {"reasoning_effort": "none"})
        self.assertEqual(tool_parameters("kimi", "kimi-k3.1-pro"), {"reasoning_effort": "low"})
        self.assertEqual(tool_parameters(CUSTOM, "hand-entered-model"), {})

    def test_welcome_draft_chat_column_and_sticky_composer(self):
        page = AgentPage(lambda _: None)
        self.addCleanup(page.close)
        page.resize(1190, 690)
        page.show()
        self.app.processEvents()
        self.capture(page, "agent-welcome.png")
        self.assertTrue(page.welcome.isVisible())
        page.example_buttons[0].click()
        self.assertEqual(page.input.toPlainText(), "将视频转换为 H.265")
        self.assertTrue(page.welcome.isVisible())
        page.send()
        reply = page.add_message("**媒体信息**\n\n```text\nHEVC\n```")
        self.app.processEvents()
        self.assertFalse(page.welcome.isVisible())
        self.assertGreater(page.bubbles[0].x(), reply.x())
        self.assertLessEqual(page.message_column.width(), CHAT_WIDTH)
        self.assertLessEqual(page.composer_panel.width(), CHAT_WIDTH)
        self.capture(page, "agent-chat.png")
        page.resize(660, 550)
        self.app.processEvents()
        self.assertGreater(page.composer_panel.mapTo(page, page.composer_panel.rect().bottomLeft()).y(), 450)
        self.assertLess(page.message_column.width(), 660)
        page.set_busy(True)
        self.assertTrue(page.stop.isVisible())
        self.assertFalse(page.send_button.isVisible())
        page.set_busy(False)
        page.clear_history()
        self.assertTrue(page.welcome.isVisible())
