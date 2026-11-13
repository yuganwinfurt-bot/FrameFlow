"""Own per-test Qt cleanup and isolate settings from the Windows user profile."""

import gc
import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from frameflow.agent_api import ModelSettings
from frameflow.theme import app_palette


def desktop_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        app.setStyle("Fusion")
        app.setPalette(app_palette())
        if os.name == "nt":
            app.setFont(QFont("Microsoft YaHei UI", 9))
    return app


class GuiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = desktop_app()

    def setUp(self):
        super().setUp()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
        # Keep workspace tests independent of the local user's provider and credentials.
        self._model_store = self.enterContext(TemporaryDirectory())
        settings = ModelSettings(QSettings(os.path.join(self._model_store, "agent.ini"), QSettings.Format.IniFormat))
        self.enterContext(patch("frameflow.agent.ModelSettings", return_value=settings))
