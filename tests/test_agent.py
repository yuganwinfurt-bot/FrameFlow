"""Agent tool execution, confirmation and authorization through the desktop."""

import json
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from frameflow.agent_api import PROBE_NAME
from frameflow.media import Processes, probe
from agent_test_support import call, mock_model, response
from agent_workspace_support import AgentWorkspaceFixture


class AgentToolsWorkflowTest(AgentWorkspaceFixture):
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
        self.assertTrue(any("目标 20 MB" in label.text() for label in agent.pending.card.findChildren(QLabel)))
        self.wait(agent.pending.card.confirm.isVisible)

    def test_model_tools_require_confirmation_then_export_real_hevc(self):
        self.nav(1)
        self.settle()
        editor = self.window.pages.currentWidget()
        self.wait(lambda: editor.session.frames and editor.scope is None)
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
            self.assertEqual(editor.settings.output_stem_edit.text(), "测试 Agent (最终)")
            self.assertEqual(editor.settings.directory, out)
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

    def test_duplicate_tool_ids_abort_reply_before_any_tool_side_effect(self):
        self.nav(2)
        self.settle()
        agent = self.window.agent_tools
        key = self.keys[0]
        before = agent.get_media_info(key)["output"]
        quality = 18 if before["quality"] != 18 else 30

        def model(body):
            if body.get("tool_choice"):
                return 200, response(None, call(PROBE_NAME))
            return 200, response(None, call("set_quality", {"id": key, "quality": quality}, "duplicate"),
                                 call("prepare_conversion", {"ids": [key]}, "duplicate"))

        with mock_model(model) as (config, requests):
            self.window.agent_session.config = config
            self.page.input.setPlainText("改画质并生成转换计划")
            QTest.mouseClick(self.page.send_button, Qt.MouseButton.LeftButton)
            self.wait(lambda: len(requests) >= 2 and not self.page.busy)
            self.assertEqual(len(requests), 2)
            self.assertIn("不合法", self.page.activity.text())
        self.assertEqual(agent.get_media_info(key)["output"], before)
        self.assertIsNone(agent.pending)
        self.assertIsNone(self.window.scope)

    def test_cancelled_plan_cannot_be_replayed_by_model(self):
        self.nav(2)
        self.settle()
        agent = self.window.agent_tools
        key = self.keys[0]
        self.assertTrue(agent.dispatch("prepare_conversion", json.dumps({"ids": [key]}))["ok"])
        plan = agent.pending
        before = set(self.root.iterdir())
        QTest.mouseClick(plan.card.cancel, Qt.MouseButton.LeftButton)
        self.assertIsNone(agent.pending)
        result = agent.dispatch("start_conversion", json.dumps({"plan_id": plan.id}))
        self.assertFalse(result["ok"])
        self.assertIn("找不到", result["error"])
        self.assertIsNone(self.window.scope)
        self.assertEqual(set(self.root.iterdir()), before)
