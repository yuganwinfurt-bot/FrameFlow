"""Qt media fixtures for editor, queue and transition acceptance tests."""

import time

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from frameflow.window import MainWindow

from media_test_support import MediaFixture
from qt_test_support import GuiTestCase


class WorkflowFixture(MediaFixture, GuiTestCase):
    def wait_for(self, predicate, description):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.015)
        self.fail(f"等待超时：{description}")

    def dispose_editor(self, editor):
        editor.close()
        editor.deleteLater()
        QCoreApplication.sendPostedEvents(editor, QEvent.Type.DeferredDelete)

    def open_export_window(self, *paths):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(1260, 760)
        window.show()
        window.add_paths(paths)
        self.wait_for(lambda: all(job.info for job in window.jobs.values()), "导入真实素材")
        return window

    def enter_output_name(self, editor, name):
        field = editor.settings.output_stem_edit
        parent = field.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if parent is not None:
            parent.ensureWidgetVisible(field)
        QTest.mouseClick(field, Qt.MouseButton.LeftButton)
        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        event = QInputMethodEvent()
        event.setCommitString(name)
        QApplication.sendEvent(field, event)
        self.assertEqual(field.text(), name)
