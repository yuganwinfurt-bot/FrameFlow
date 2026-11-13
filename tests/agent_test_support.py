"""Local Chat Completions fixtures using synthetic credentials and real Qt HTTP transport."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from frameflow.agent_api import ModelConfig


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
