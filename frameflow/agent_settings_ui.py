"""Provider-aware Qt configuration dialog for the Agent."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QVBoxLayout, QWidget,
)

from .agent_api import ModelClient, ModelConfig, accepted_probe, probe_model
from .icons import icon
from .model_providers import CUSTOM, DEFAULT_TIMEOUT, MAX_TIMEOUT, MIN_TIMEOUT, PROVIDERS
from .motion import Button, Combo, SpinBox
from .theme import C
from .widgets import label as text_label


class ModelDialog(QDialog):
    def __init__(self, settings, active_provider, config, parent):
        super().__init__(parent)
        self.setWindowTitle("模型服务配置")
        self.setMinimumWidth(540)
        self.settings = settings
        self.drafts = {active_provider: config}
        self.provider = None
        self.tested = None
        self.key_endpoint = ""
        self.client = ModelClient(self)
        self.client.finished.connect(self._tested)
        self.setObjectName("modelDialog")
        self.setStyleSheet(f"""
            QDialog#modelDialog {{ background: {C.surface.name()}; }}
            QDialog#modelDialog QLabel {{ color: {C.text.name()}; }}
            QDialog#modelDialog QLabel[muted="true"] {{ color: {C.text2.name()}; }}
            QLabel#modelTitle {{ font-size: 20px; font-weight: 700; }}
            QDialog#modelDialog QLabel#fieldLabel {{ font-weight: 600; }}
            QFrame#modelFields {{ background: {C.surface_alt.name()}; border: 1px solid {C.border.name()};
                                 border-radius: 12px; }}
            QDialog#modelDialog QComboBox, QDialog#modelDialog QLineEdit, QDialog#modelDialog QSpinBox {{
                background: {C.surface.name()}; color: {C.text.name()}; border: 1px solid {C.border.name()};
                border-radius: 7px; padding: 3px 8px;
            }}
            QDialog#modelDialog QComboBox:focus, QDialog#modelDialog QLineEdit:focus,
            QDialog#modelDialog QSpinBox:focus {{ border-color: {C.accent.name()}; }}
            QDialog#modelDialog QComboBox::drop-down {{ width: 24px; border: 0; }}
            QDialog#modelDialog QComboBox::down-arrow {{ image: none; }}
            QDialog#modelDialog QComboBox QLineEdit, QDialog#modelDialog QSpinBox QLineEdit {{
                border: 0; background: transparent; padding: 0;
            }}
            QDialog#modelDialog QSpinBox::up-arrow, QDialog#modelDialog QSpinBox::down-arrow {{ image: none; }}
            QDialog#modelDialog QPushButton[standardButton="true"] {{ min-height: 30px; }}
            QLabel#modelStatus {{ font-weight: 600; color: {C.text2.name()}; }}
            QLabel#modelStatus[state="ok"] {{ color: {C.success.name()}; }}
            QLabel#modelStatus[state="error"] {{ color: {C.danger.name()}; }}
            QLabel#modelStatus[state="testing"] {{ color: {C.accent_text.name()}; }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)
        heading = QHBoxLayout()
        badge = QLabel()
        badge.setPixmap(icon("gear", 25, C.accent_text.name()).pixmap(25, 25))
        heading.addWidget(badge)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title = text_label("模型服务配置")
        title.setObjectName("modelTitle")
        titles.addWidget(title)
        subtitle = text_label("选择 AI 服务商并配置连接")
        subtitle.setProperty("muted", True)
        titles.addWidget(subtitle)
        heading.addLayout(titles, 1)
        root.addLayout(heading)

        card = QFrame()
        card.setObjectName("modelFields")
        fields = QVBoxLayout(card)
        fields.setContentsMargins(14, 12, 14, 12)
        fields.setSpacing(5)
        root.addWidget(card)
        self.providers = Combo()
        for provider_id, preset in PROVIDERS.items():
            self.providers.addItem(icon(preset.icon, 16, C.accent_text.name()), preset.name, provider_id)
        self._field(fields, "模型厂商", self.providers)
        self.model = Combo()
        self.model.setEditable(True)
        self.model.setInsertPolicy(Combo.InsertPolicy.NoInsert)
        self.model.lineEdit().setPlaceholderText("选择或手动输入模型 ID")
        self._field(fields, "模型选择", self.model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("输入服务商提供的 API Key")
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(6)
        key_layout.addWidget(self.key, 1)
        self.reveal = Button("")
        self.reveal.setFixedSize(38, 32)
        self.reveal.setCheckable(True)
        self.reveal.toggled.connect(self._show_key)
        self._show_key(False)
        key_layout.addWidget(self.reveal)
        self._field(fields, "API Key", key_row)
        self.hint = text_label("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("muted", True)
        fields.addWidget(self.hint)

        self.advanced = Button("高级设置")
        self.advanced.setCheckable(True)
        self.advanced.setIcon(icon("sliders", 15))
        fields.addWidget(self.advanced, alignment=Qt.AlignmentFlag.AlignLeft)
        self.url = QLineEdit()
        self.url.setPlaceholderText("例如 https://api.example.com/v1")
        self.url_row = QWidget()
        url_layout = QVBoxLayout(self.url_row)
        url_layout.setContentsMargins(0, 0, 0, 0)
        url_layout.setSpacing(4)
        self._field(url_layout, "API Base URL", self.url)
        fields.addWidget(self.url_row)
        self.timeout = SpinBox()
        self.timeout.setRange(MIN_TIMEOUT, MAX_TIMEOUT)
        self.timeout.setSuffix(" 秒")
        self.timeout_row = QWidget()
        timeout_layout = QHBoxLayout(self.timeout_row)
        timeout_layout.setContentsMargins(0, 0, 0, 0)
        timeout_layout.addWidget(text_label("请求超时"))
        timeout_layout.addWidget(self.timeout)
        timeout_layout.addStretch()
        fields.addWidget(self.timeout_row)
        self.advanced.toggled.connect(self._show_advanced)

        self.note = text_label("")
        self.note.setProperty("muted", True)
        self.note.setWordWrap(True)
        self.note.hide()
        root.addWidget(self.note)
        self.loading = QProgressBar()
        self.loading.setRange(0, 0)
        self.loading.setTextVisible(False)
        self.loading.setFixedHeight(4)
        self.loading.hide()
        root.addWidget(self.loading)
        footer = QHBoxLayout()
        self.status = text_label("未连接")
        self.status.setObjectName("modelStatus")
        footer.addWidget(self.status, 1)
        self.test_button = Button("测试连接")
        self.test_button.clicked.connect(self._test)
        cancel = Button("取消")
        cancel.clicked.connect(self.reject)
        self.save_button = Button("保存")
        self.save_button.setObjectName("primary")
        self.save_button.setToolTip("保存当前厂商的配置")
        self.save_button.clicked.connect(self.accept)
        for button in (self.test_button, cancel, self.save_button):
            button.setMinimumWidth(68)
            footer.addWidget(button)
        root.addLayout(footer)

        self.providers.setCurrentIndex(self.providers.findData(active_provider))
        self.providers.currentIndexChanged.connect(self._switch_provider)
        self._switch_provider()
        self.key.textEdited.connect(self._key_edited)
        self.url.editingFinished.connect(self._check_url)
        for signal in (self.url.textEdited, self.model.currentTextChanged, self.timeout.valueChanged):
            signal.connect(self._changed)

    @staticmethod
    def _field(layout, title, field):
        label = text_label(title)
        label.setObjectName("fieldLabel")
        label.setBuddy(field)
        layout.addWidget(label)
        field.setMinimumHeight(32)
        layout.addWidget(field)
        layout.addSpacing(2)

    def _show_key(self, reveal):
        self.key.setEchoMode(QLineEdit.EchoMode.Normal if reveal else QLineEdit.EchoMode.Password)
        self.reveal.setIcon(icon("eyeOff" if reveal else "eye", 16))
        label = "隐藏 API Key" if reveal else "显示 API Key"
        self.reveal.setToolTip(label)
        self.reveal.setAccessibleName(label)

    def _show_advanced(self, expanded):
        self.url_row.setVisible(expanded or self.provider == CUSTOM)
        self.timeout_row.setVisible(expanded)
        self.advanced.setText("收起高级设置" if expanded else "高级设置")
        self.adjustSize()

    def _switch_provider(self):
        if self.provider is not None:
            self.drafts[self.provider] = self.config()
        self.provider = self.providers.currentData()
        preset = PROVIDERS[self.provider]
        config = self.drafts.get(self.provider)
        if config is None:
            config = self.settings.load(self.provider)
        self.model.clear()
        self.model.addItems(preset.models)
        self.model.setCurrentText(config.model)
        self.url.setText(config.base_url)
        self.key.setText(config.api_key)
        self.key_endpoint = self._endpoint(config.base_url)
        self.reveal.setChecked(False)
        self.timeout.setValue((config.timeout_ms or DEFAULT_TIMEOUT * 1000) // 1000)
        self.hint.setText(preset.hint or "支持填写其他模型 ID。请测试该模型是否能调用 FrameFlow 工具。")
        self._show_advanced(self.advanced.isChecked())
        self._changed()

    @staticmethod
    def _endpoint(base_url):
        try:
            return ModelConfig(base_url).completion_url().toString()
        except ValueError:
            return ""

    def _key_edited(self):
        self.key_endpoint = ""
        self._changed()

    def _check_url(self):
        if self.key.text() and self.key_endpoint and self.key_endpoint != self._endpoint(self.url.text()):
            self.key.clear()
            self.key_endpoint = ""
            self._set_status("未连接", "API 地址已更改，请填写新地址对应的 API Key。")

    def config(self):
        url = self.url.text().strip()
        key = self.key.text().strip()
        if self.key_endpoint and self.key_endpoint != self._endpoint(url):
            key = ""
        timeout = self.timeout.value()
        return ModelConfig(url, self.model.currentText().strip(), key,
                           None if timeout == DEFAULT_TIMEOUT else timeout * 1000)

    def _changed(self):
        self.tested = None
        self._set_status("未连接")

    def _set_status(self, text, detail="", state=""):
        self.status.setText(text)
        self.status.setProperty("state", state)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.note.setText(detail)
        self.note.setVisible(bool(detail))
        self.adjustSize()

    def accept(self):
        self._check_url()
        config = self.config()
        try:
            if config.api_key:
                config.endpoint()
            elif config.base_url:
                config.completion_url()
        except ValueError as exc:
            self._set_status("连接失败", str(exc), "error")
            return
        super().accept()

    def done(self, result):
        self.client.finished.disconnect(self._tested)
        self.client.abort()
        super().done(result)

    def _test(self):
        self._check_url()
        self.tested = None
        try:
            probe_model(self.client, self.config())
        except ValueError as exc:
            self._set_status("连接失败", str(exc), "error")
            return
        self._enable_fields(False)
        self._set_status("正在测试", "正在检查模型连接与工具调用能力…", "testing")

    def _enable_fields(self, enabled):
        for widget in (self.providers, self.model, self.key, self.url, self.timeout, self.test_button, self.save_button):
            widget.setEnabled(enabled)
        self.loading.setVisible(not enabled)

    def _tested(self, message, error):
        self._enable_fields(True)
        if error:
            self._set_status("连接失败", error, "error")
        elif accepted_probe(message):
            self.tested = self.config()
            self.show_verified()
        else:
            self._set_status("连接失败", "服务可访问，但模型未返回工具调用。请更换支持 Tool Calling 的模型。", "error")

    def show_verified(self):
        self._set_status("连接成功", "已收到模型的工具调用响应。", "ok")
