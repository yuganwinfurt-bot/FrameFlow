"""OpenAI-compatible Chat Completions transport and Windows-protected settings."""

import base64
import ctypes
from dataclasses import dataclass, field
import json
import sys
from ctypes import wintypes

from PySide6.QtCore import QByteArray, QObject, QSettings, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .model_providers import (
    DEFAULT_TIMEOUT, MAX_TIMEOUT, MIN_TIMEOUT, PROVIDERS, provider_for_url, tool_parameters,
    uses_kimi_reasoning,
)

MAX_RESPONSE = 1024 * 1024


@dataclass(frozen=True)
class ModelConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    timeout_ms: int | None = None

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

    def active_provider(self):
        active = self.settings.value("activeProvider", "", str)
        if active in PROVIDERS:
            return active
        legacy = self.settings.value("baseUrl", "", str)
        return provider_for_url(legacy) if legacy else "deepseek"

    def load(self, provider=None):
        provider = provider or self.active_provider()
        preset = PROVIDERS[provider]
        prefix = f"profiles/{provider}/"
        legacy = self.settings.value("baseUrl", "", str)
        if not self.settings.contains(prefix + "model") and legacy and provider_for_url(legacy) == provider:
            prefix = ""
        base_url = self.settings.value(prefix + "baseUrl", preset.url, str)
        model = self.settings.value(prefix + "model", next(iter(preset.models), ""), str)
        timeout = self.settings.value(prefix + "timeout", DEFAULT_TIMEOUT, int)
        timeout = max(MIN_TIMEOUT, min(MAX_TIMEOUT, timeout))
        key = ""
        if sys.platform == "win32" and (ciphertext := self.settings.value(prefix + "protectedKey", "", str)):
            try:
                credential = json.loads(_dpapi(base64.b64decode(ciphertext, validate=True), protect=False))
                if (isinstance(credential, dict) and credential.get("url") == base_url
                        and isinstance(credential.get("key"), str)):
                    key = credential["key"]
            except (OSError, ValueError):
                pass
        return ModelConfig(base_url, model, key, None if timeout == DEFAULT_TIMEOUT else timeout * 1000)

    def save(self, config, provider=None):
        provider = provider or provider_for_url(config.base_url)
        prefix = f"profiles/{provider}/"
        if sys.platform == "win32":
            if config.api_key:
                credential = json.dumps({"url": config.base_url, "key": config.api_key}).encode("utf-8")
                self.settings.setValue(prefix + "protectedKey", base64.b64encode(
                    _dpapi(credential, protect=True)).decode("ascii"))
            else:
                self.settings.remove(prefix + "protectedKey")
        self.settings.setValue(prefix + "baseUrl", config.base_url)
        self.settings.setValue(prefix + "model", config.model)
        self.settings.setValue(prefix + "timeout", (config.timeout_ms or DEFAULT_TIMEOUT * 1000) // 1000)
        self.settings.setValue("activeProvider", provider)
        legacy = self.settings.value("baseUrl", "", str)
        if legacy and provider_for_url(legacy) == provider:
            for name in ("baseUrl", "model", "protectedKey"):
                self.settings.remove(name)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError("无法保存设置")


def _http_error(status, raw):
    if status == 401:
        return "API Key 无效或已过期（HTTP 401），请检查该服务的凭据。"
    if status == 403:
        return "API 请求被拒绝（HTTP 403），请检查模型、项目和地区权限。"
    if status == 429:
        return "API 请求受限（HTTP 429），请检查余额、配额或稍后重试。"
    if status == 408 or status == 504:
        return f"模型服务请求超时（HTTP {status}）。"
    if status >= 500:
        return f"模型服务暂时不可用（HTTP {status}），请稍后重试。"
    if 300 <= status < 400:
        return f"API 地址返回重定向（HTTP {status}），请填写最终接口地址。"
    # Classify provider errors without displaying untrusted response bodies or credentials.
    details = ""
    try:
        error = json.loads(raw)["error"]
        if isinstance(error, dict):
            details = " ".join(str(error.get(field, "")) for field in ("code", "type", "message")).lower()
    except (ValueError, TypeError, KeyError):
        pass
    if any(term in details for term in ("model_not_found", "modelnotfound", "model_not_exist",
                                       "model does not exist", "model not found", "模型不存在")):
        return f"模型不存在、未开通或当前账户无权访问（HTTP {status}），请检查模型 ID。"
    if any(term in details for term in ("tool_choice", "tool calling", "tool_call", "function call", "工具调用")):
        return f"当前模型或接口拒绝了 Tool Calling 请求（HTTP {status}），请检查模型支持的接口和模式。"
    if status in (404, 405):
        return f"找不到 Chat Completions 接口（HTTP {status}），请检查 API Base URL。"
    return f"模型服务拒绝了请求（HTTP {status}），请检查模型 ID、接口地址和工具调用支持。"


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
            payload.update(tool_parameters(provider_for_url(config.base_url), config.model.strip()))
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        self.body.clear()
        reply = self.manager.post(request, QByteArray(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        self.reply = reply
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finish(reply))
        self.timer.start(config.timeout_ms or self.timeout_ms)

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
        if status and not 200 <= status < 300:
            self.finished.emit(None, _http_error(status, raw))
            return
        if error != QNetworkReply.NetworkError.NoError:
            network_error = QNetworkReply.NetworkError
            note = {
                network_error.HostNotFoundError: "找不到服务域名，请检查 API 地址和 DNS。",
                network_error.ConnectionRefusedError: "模型服务拒绝连接，请检查地址、端口和代理。",
                network_error.SslHandshakeFailedError: "TLS 握手失败，请检查证书和代理。",
                network_error.TimeoutError: "模型响应超时，请检查网络或增加超时时间。",
            }.get(error, "无法连接模型服务，请检查网络、代理或 TLS 证书。")
            self.finished.emit(None, note)
            return
        try:
            data = json.loads(raw)
            message = data["choices"][0]["message"]
            if not isinstance(message, dict) or (not isinstance(message.get("content"), str)
                                                 and not message.get("tool_calls")):
                raise KeyError("message")
        except (ValueError, KeyError, TypeError, IndexError):
            self.finished.emit(None, "接口未返回有效的 Chat Completions 响应，请检查 API 地址和接口类型。")
        else:
            self.finished.emit(message, "")


PROBE_NAME = "frameflow_connection_check"
PROBE_TOOL = {"type": "function", "function": {"name": PROBE_NAME, "description": "Test the tool calling protocol.",
              "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}


def probe_model(client, config):
    provider = provider_for_url(config.base_url)
    choice = {"type": "function", "function": {"name": PROBE_NAME}}
    if provider in {"glm", "kimi", "qwen", "siliconflow", "ark"}:
        choice = "auto"
    if uses_kimi_reasoning(config.base_url, config.model.strip()):
        choice = "required"
    prompt = f"Call {PROBE_NAME} now with no arguments. Use the actual tool call, without a text reply."
    client.post(config, [{"role": "user", "content": prompt}], (PROBE_TOOL,), choice)


def accepted_probe(message):
    calls = message.get("tool_calls") if message else None
    return isinstance(calls, list) and any(
        isinstance(call, dict) and isinstance(call.get("id"), str)
        and isinstance(call.get("function"), dict) and call["function"].get("name") == PROBE_NAME
        for call in calls
    )
