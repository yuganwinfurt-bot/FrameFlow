"""Exercise Qt's actual HTTP transport against a local protocol fixture."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from frameflow.agent_api import ModelClient, ModelConfig, ModelSettings, PROBE_NAME, accepted_probe, probe_model


def response(text=None, *calls):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = list(calls)
    return {"choices": [{"message": message}]}


def call(name, arguments=None, call_id="fixture_call"):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}


@contextmanager
def mock_model(handler):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body))
            status, payload = handler(body)
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .03}, daemon=True)
        thread.start()
        try:
            yield ModelConfig(f"http://127.0.0.1:{server.server_port}/v1", "fixture-model", "synthetic-test-token"), requests
        finally:
            server.shutdown()
            thread.join()


class AgentApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(10)
        self.fail("HTTP fixture did not finish")

    def test_connection_test_uses_real_tool_call_and_handles_unsupported(self):
        for supports_tools in (True, False):
            with self.subTest(supports_tools=supports_tools), mock_model(
                lambda _: (200, response(None, call(PROBE_NAME)) if supports_tools else response("普通回复"))
            ) as (config, requests):
                client = ModelClient(timeout_ms=2000)
                result = []
                client.finished.connect(lambda message, error: result.append((message, error)))
                probe_model(client, config)
                self.wait(lambda: result)
                self.assertEqual(result[0][1], "")
                self.assertEqual(accepted_probe(result[0][0]), supports_tools)
                path, auth, payload = requests[0]
                self.assertEqual((path, auth), ("/v1/chat/completions", "Bearer synthetic-test-token"))
                self.assertEqual(payload["tool_choice"]["function"]["name"], PROBE_NAME)
                self.assertEqual(payload["model"], config.model)

    def test_transport_errors_timeout_and_stop(self):
        def slow(_):
            time.sleep(.4)
            return 200, response("late reply")
        for mode, handler in (("auth", lambda _: (401, {"error": {"message": "synthetic-test-token"}})),
                              ("timeout", slow), ("stop", slow)):
            with self.subTest(mode=mode), mock_model(handler) as (config, requests):
                client = ModelClient(timeout_ms=140 if mode == "timeout" else 2000)
                result = []
                client.finished.connect(lambda message, error: result.append((message, error)))
                client.post(config, [{"role": "user", "content": "hello"}])
                if mode == "stop":
                    self.wait(lambda: requests)
                    client.abort()
                self.wait(lambda: result)
                self.assertIsNone(result[0][0])
                self.assertIn({"auth": "401", "timeout": "超时", "stop": "已停止"}[mode], result[0][1])
                self.assertNotIn(config.api_key, result[0][1])
                self.assertIsNone(client.reply)
                if mode in ("timeout", "stop"):
                    QTest.qWait(450)
                self.assertEqual(len(result), 1)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI")
    def test_settings_protect_key_and_allow_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent.ini"
            settings = ModelSettings(QSettings(str(path), QSettings.Format.IniFormat))
            config = ModelConfig("https://example.invalid/v1", "model", "synthetic-test-token")
            settings.save(config)
            self.assertNotIn(config.api_key, path.read_text(encoding="utf-8"))
            self.assertEqual(settings.load(), config)
            settings.save(ModelConfig(config.base_url, config.model))
            self.assertFalse(settings.load().api_key)

    def test_plain_http_only_allowed_on_loopback(self):
        for url in ("http://example.invalid/v1", "https://user:pass@example.invalid/v1", "https://example.invalid/?key=foo"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ModelConfig(url, "model", "synthetic-test-token").endpoint()
