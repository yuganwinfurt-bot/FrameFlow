"""Verify keyboard selection and popup geometry through Qt."""

import time
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from frameflow.motion import Combo

from qt_test_support import GuiTestCase


class DesktopControlsTest(GuiTestCase):
    def test_combo_popup_does_not_cover_source_and_keeps_keyboard_selection(self):
        combo = Combo()
        combo.addItems(["原始分辨率", "720P", "1080P", "1440P", "4K"])
        combo.setGeometry(120, 120, 180, 32)
        combo.show()
        self.addCleanup(combo.close)
        self.app.processEvents()
        original = combo.currentIndex()
        combo.showPopup()
        QTest.qWait(25)
        popup = combo.view().window()
        control_top = combo.mapToGlobal(QPoint(0, 0))
        popup_rect = popup.frameGeometry()
        screen = combo.screen()
        self.assertGreaterEqual(popup_rect.width(), combo.width())
        self.assertGreater(popup_rect.height(), 100)
        if screen and screen.availableGeometry().bottom() - (control_top.y() + combo.height()) >= popup_rect.height() + 3:
            self.assertGreaterEqual(popup_rect.top(), control_top.y() + combo.height() + 3)
        else:
            self.assertLess(popup_rect.bottom(), control_top.y())
        QTest.keyClick(combo.view(), Qt.Key.Key_Down)
        QTest.keyClick(combo.view(), Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertNotEqual(combo.currentIndex(), original)

    def test_combo_popup_matches_input_width_and_shows_every_row(self):
        """下拉弹窗宽度等于输入框，1 个选项也要完整显示一行（不能是一条细缝）。"""
        from frameflow.motion import Combo

        for count in (1, 3, 12):
            combo = Combo()
            combo.resize(220, 30)
            for index in range(count):
                combo.addItem(f"选项 {index + 1}")
            combo.show()
            combo.showPopup()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
                if combo.view().window().isVisible():
                    break
            view = combo.view()
            popup = view.window()
            self.assertEqual(popup.width(), combo.width(), "弹窗宽度应与输入框一致")
            self.assertGreaterEqual(view.viewport().height(), 34, "至少完整显示一行")
            self.assertGreaterEqual(popup.height(), 40, "弹窗不能只有一条细缝")
            rows = min(count, combo.maxVisibleItems())
            self.assertGreaterEqual(view.viewport().height(), rows * 32,
                                    "可见行数应完整显示，不做裁剪")
            combo.hidePopup()
            combo.close()
            combo.deleteLater()
            self.app.processEvents()
