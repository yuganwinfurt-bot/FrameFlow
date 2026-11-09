"""OpenAI-compatible Chat Completions transport and Windows-protected settings."""

import base64
import ctypes
from dataclasses import dataclass, field
import json
import sys
from ctypes import wintypes

from PySide6.QtCore import QByteArray, QObject, QSettings, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

MAX_RESPONSE = 1024 * 1024


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)

    def completion_url(self):
        url = QUrl(self.base_url.strip())
        if (not url.isValid() or not url.host() or url.userInfo() or url.hasQuery() or url.hasFragment()
                or not (url.scheme() == "https" or
                        url.scheme() == "http" and url.host().lower() in {"localhost", "127.0.0.1", "::1"})):
            raise ValueError("请输入 HTTPS API Base URL。本机环回地址可使用 HTTP。")
        path = url.path().rstrip("/")
        if not path.endswith("/chat/completions"):
            url.setPath(path + "/chat/completions")
        return url

    def endpoint(self):
        url = self.completion_url()
        if not self.model.strip():
            raise ValueError("请输入 Model 名称。")
        if not self.api_key or not all(33 <= ord(char) <= 126 for char in self.api_key):
            raise ValueError("请输入有效的 API Key。")
        return url


class _Blob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, *, protect):
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree.restype = ctypes.c_void_p
    process = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    process.argtypes = (ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob))
    process.restype = wintypes.BOOL
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = _Blob(len(data), buffer), _Blob()
    if not process(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.length)
    finally:
        kernel.LocalFree(result.data)


class ModelSettings:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings("FrameFlow", "AI Agent")

    def load(self):
        base_url = self.settings.value("baseUrl", "", str)
        model = self.settings.value("model", "", str)
        key = ""
        if sys.platform == "win32" and (ciphertext := self.settings.value("protectedKey", "", str)):
            try:
                credential = json.loads(_dpapi(base64.b64decode(ciphertext, validate=True), protect=False))
                if (isinstance(credential, dict) and credential.get("url") == base_url
                        and isinstance(credential.get("key"), str)):
                    key = credential["key"]
            except (OSError, ValueError):
                pass
        return ModelConfig(base_url, model, key)

    def save(self, config):
        if sys.platform == "win32":
            if config.api_key:
                credential = json.dumps({"url": config.base_url, "key": config.api_key}).encode("utf-8")
                self.settings.setValue("protectedKey", base64.b64encode(
                    _dpapi(credential, protect=True)).decode("ascii"))
            else:
                self.settings.remove("protectedKey")
        self.settings.setValue("baseUrl", config.base_url)
        self.settings.setValue("model", config.model)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("无法保存设置")


class ModelClient(QObject):
    finished = Signal(object, str)

    def __init__(self, parent=None, *, timeout_ms=45000):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.reply = None
        self.body = bytearray()
        self.timeout_ms = timeout_ms
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.abort("模型响应超时，请检查网络或稍后重试。"))

    def post(self, config, messages, tools=(), tool_choice=None):
        if self.reply is not None:
            raise RuntimeError("已有请求正在进行")
        request = QNetworkRequest(config.endpoint())
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Authorization", b"Bearer " + config.api_key.encode("ascii"))
        payload = {"model": config.model.strip(), "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        self.body.clear()
        reply = self.manager.post(request, QByteArray(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        self.reply = reply
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finish(reply))
        self.timer.start(self.timeout_ms)

    def _read(self, reply):
        if self.reply is reply:
            if len(self.body) + reply.bytesAvailable() > MAX_RESPONSE:
                self.abort("模型返回过大，已停止接收。")
            else:
                self.body.extend(bytes(reply.readAll()))

    def abort(self, reason="已停止生成。"):
        reply, self.reply = self.reply, None
        self.timer.stop()
        if reply is not None:
            self.body.clear()
            reply.abort()
            self.finished.emit(None, reason)

    def _finish(self, reply):
        if self.reply is not reply:
            reply.deleteLater()
            return
        self._read(reply)
        if self.reply is not reply:
            reply.deleteLater()
            return
        self.reply = None
        self.timer.stop()
        raw = bytes(self.body)
        self.body.clear()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        reply.deleteLater()
        try:
            if status in (401, 403):
                raise ValueError(f"API 鉴权失败（HTTP {status}），请检查 Key 和模型权限。")
            if status == 429:
                raise ValueError("API 请求受限（HTTP 429），请检查余额、配额或稍后重试。")
            if status and not 200 <= status < 300:
                raise ValueError(f"API 返回 HTTP {status}。请检查地址、模型和 Tool Calling 支持。")
            if error != QNetworkReply.NetworkError.NoError:
                raise ValueError("无法连接模型服务，请检查网络、代理或 TLS 证书。")
            data = json.loads(raw)
            message = data["choices"][0]["message"]
            if not isinstance(message, dict) or (not isinstance(message.get("content"), str)
                                                 and not message.get("tool_calls")):
                raise KeyError("message")
        except ValueError as exc:
            # Decoder errors can quote payload data. Present only our own fixed messages.
            note = str(exc) if type(exc) is ValueError and str(exc).startswith(("API ", "无法连接")) else "模型返回了无法识别的响应。"
            self.finished.emit(None, note)
        except (KeyError, TypeError, IndexError):
            self.finished.emit(None, "模型返回了无法识别的响应。")
        else:
            self.finished.emit(message, "")


PROBE_NAME = "frameflow_connection_check"
PROBE_TOOL = {"type": "function", "function": {"name": PROBE_NAME, "description": "Test the tool calling protocol.",
              "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}


def probe_model(client, config):
    client.post(config, [{"role": "user", "content": f"Call {PROBE_NAME} with no arguments."}],
                (PROBE_TOOL,), {"type": "function", "function": {"name": PROBE_NAME}})


def accepted_probe(message):
    calls = message.get("tool_calls") if message else None
    return isinstance(calls, list) and any(
        isinstance(call, dict) and isinstance(call.get("id"), str)
        and isinstance(call.get("function"), dict) and call["function"].get("name") == PROBE_NAME
        for call in calls
    )
