"""Navigation, scrolling and input behavior across workspaces."""

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QInputDialog, QSpinBox, QToolButton
from frameflow.agent_settings_ui import ModelDialog
from frameflow.media import Processes
from agent_workspace_support import AgentWorkspaceFixture


class WorkspaceControlsTest(AgentWorkspaceFixture):
    def test_top_navigation_fits_and_keeps_full_hit_areas(self):
        window = self.window
        nav = window.navigation

        def check_tabs():
            self.assertFalse(nav.usesScrollButtons())
            self.assertFalse(any(button.isVisible() for button in nav.findChildren(QToolButton)))
            self.assertEqual([nav.tabText(i) for i in range(nav.count())], ["格式转换", "媒体预览", "AI Agent"])
            for index in range(nav.count()):
                rect = nav.tabRect(index)
                self.assertTrue(nav.rect().contains(rect))
                self.assertGreaterEqual(rect.width(), nav.minimumTabSizeHint(index).width())
                self.assertEqual(nav.tabAt(rect.center()), index)

        for width, height in ((1177, 940), (942, 752), (940, 720)):
            window.resize(width, height)
            QTest.qWait(80)
            check_tabs()

        # Exercise the shrink path even when the main header has enough space for the preferred widths.
        original_maximum = nav.maximumWidth()
        nav.setMaximumWidth(nav.minimumSizeHint().width() + 12)
        QTest.qWait(80)
        check_tabs()
        self.assertLess(nav.width(), nav.sizeHint().width())
        for index in (1, 2, 0):
            self.nav(index)
            self.settle()
            self.assertEqual(nav.currentIndex(), index)
            check_tabs()
        nav.setMaximumWidth(original_maximum)
        for change_state in (window.showMaximized, window.showNormal):
            change_state()
            QTest.qWait(100)
            check_tabs()

    def test_hidden_scroll_bars_keep_wheel_keyboard_and_content_access(self):
        keys = self.add_attachment_samples(14)
        window, page = self.window, self.page
        window.resize(940, 720)
        QTest.qWait(80)

        def check_scroll(area):
            bar = area.verticalScrollBar()
            self.wait(lambda: bar.maximum() > 0)
            self.assertEqual(area.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.assertFalse(bar.isVisible())
            return bar

        def wheel(area, angle, pixels=0):
            viewport = area.viewport()
            local = viewport.rect().center()
            event = QWheelEvent(QPointF(local), QPointF(viewport.mapToGlobal(local)), QPoint(0, pixels),
                                QPoint(0, angle), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.ScrollUpdate if pixels else Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(viewport, event)

        for area in (window.list, window.inspector.inspector_scroll):
            bar = check_scroll(area)
            bar.setValue(0)
            wheel(area, -120)
            self.assertGreater(bar.value(), 0)

        self.nav(1)
        self.settle()
        editor = window.editors[keys[0]]
        settings = editor.settings_scroll
        bar = check_scroll(settings)
        bar.setValue(0)
        for _ in range(30):
            wheel(settings, -120)
        self.assertEqual(bar.value(), bar.maximum())
        field = editor.settings.output_stem_edit
        self.assertTrue(field.isVisibleTo(settings))
        self.assertTrue(settings.viewport().rect().contains(field.mapTo(settings.viewport(), field.rect().center())))

        self.nav(2)
        self.settle()
        rail_bar = check_scroll(window.preview_files)
        rail_bar.setValue(0)
        wheel(window.preview_files, -120)
        self.assertGreater(rail_bar.value(), 0)

        for index in range(25):
            page.add_message(f"历史消息 {index} " * 16)
        history_bar = check_scroll(page.history)
        self.wait(lambda: history_bar.value() == history_bar.maximum())
        wheel(page.history, 120)
        self.assertLess(history_bar.value(), history_bar.maximum())
        old_value = history_bar.value()
        wheel(page.history, 0, 36)
        self.assertLess(history_bar.value(), old_value)

        page.input.setPlainText("a long draft\n" * 70)
        edit_bar = check_scroll(page.input)
        QTest.keyClick(page.input, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(edit_bar.value(), 0)
        wheel(page.input, -120)
        self.assertGreater(edit_bar.value(), 0)
        old_value = edit_bar.value()
        wheel(page.input, 0, -36)
        self.assertGreater(edit_bar.value(), old_value)
        QTest.keyClick(page.input, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(page.input.textCursor().position(), page.input.document().characterCount() - 1)
        self.assertTrue(page.input.viewport().rect().contains(page.input.cursorRect().center()))

        self.drop(keys, batch=True)
        attachment_bar = check_scroll(page.pending_area)
        wheel(page.pending_area, -120)
        self.assertGreater(attachment_bar.value(), 0)
        draft = page.input.toPlainText()
        QTest.mouseClick(page.clear_attachments_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: page.pending_area.isHidden() and page.input.height() == page.input.maximumHeight())
        self.assertEqual(page.input.toPlainText(), draft)
        self.nav(0)
        self.settle()
        check_scroll(window.list)
        self.nav(2)
        self.settle()
        check_scroll(page.history)
        check_scroll(page.input)

    def send_parameter_wheel(self, widget, *, angle=0, pixels=0, point=None):
        if point is None:
            point = widget.rect().center()
        event = QWheelEvent(QPointF(point), QPointF(widget.mapToGlobal(point)), QPoint(0, pixels),
                            QPoint(0, angle), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.ScrollUpdate if pixels else Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(widget, event)

    def test_wheel_over_parameters_scrolls_both_output_panels(self):
        path = self.root / "带音轨.mp4"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.first), "-f", "lavfi",
                         "-i", "sine=frequency=440", "-c:v", "copy", "-c:a", "aac", "-shortest", str(path)])
        window = self.window
        window.add_paths([path])
        key = next(key for key, job in window.jobs.items() if job.path == path)
        self.wait(lambda: window.jobs[key].info and window.jobs[key].picture)
        window.list.setCurrentItem(window.items[key])
        window.resize(940, 720)
        self.wait(lambda: key in window.editors)
        editor = window.editors[key]

        def check_control(area, control, receiver=None):
            self.assertTrue(control.isVisibleTo(area))
            self.assertTrue(control.isEnabled())
            bar = area.verticalScrollBar()
            self.wait(lambda: bar.maximum() > 0)
            self.assertFalse(bar.isVisible())
            value = control.currentIndex if isinstance(control, QComboBox) else control.value
            control.setFocus()
            area.ensureWidgetVisible(control)
            original = value()
            for use_pixels in (False, True):
                before = bar.value()
                direction = -1 if before < bar.maximum() else 1
                wheel = {"pixels": 32 * direction} if use_pixels else {"angle": 120 * direction}
                self.send_parameter_wheel(receiver or control, **wheel)
                self.assertEqual(value(), original)
                self.assertGreater((before - bar.value()) * direction, 0)

        controls = (editor.settings.preset, editor.settings.container, editor.settings.codec, editor.settings.acceleration,
                    editor.settings.export_edge, editor.settings.output_fps, editor.settings.audio_bitrate)
        for page in (0, 1, 2, 0):
            self.nav(page)
            self.settle()
            if page == 2:
                continue
            area = editor.settings_scroll if page == 1 else window.inspector.inspector_scroll
            for control in controls:
                with self.subTest(page=page, control=control.toolTip()):
                    check_control(area, control)
            field = editor.settings.output_stem_edit
            self.assertTrue(area.isAncestorOf(field) and field.isVisible())
            area.ensureWidgetVisible(field)
            self.assertTrue(area.viewport().rect().contains(field.mapTo(area.viewport(), field.rect().center())))
            self.assertTrue((window.preview_footer.start if page == 1 else window.start_button).isVisible())

        # Exercise an inline numeric editor, whose wheel initially arrives at its QLineEdit child.
        double = QDoubleSpinBox()
        editor.settings.layout().addWidget(double)
        double.setValue(12.5)
        self.wait(double.isVisible)
        check_control(window.inspector.inspector_scroll, double, double.lineEdit())
        QTest.keyClick(double, Qt.Key.Key_Up)
        self.assertGreater(double.value(), 12.5)

        fps = editor.settings.output_fps.currentIndex()
        QTest.keyClick(editor.settings.output_fps, Qt.Key.Key_Down)
        self.assertGreater(editor.settings.output_fps.currentIndex(), fps)
        slider = editor.settings.quality.slider
        quality = slider.value()
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width() - 15, slider.height() // 2))
        self.assertNotEqual(slider.value(), quality)

    def test_hover_wheel_changes_only_video_crf_player_volume_and_timeline_zoom(self):
        window = self.window
        for page in (0, 1):
            self.nav(page)
            self.settle()
            editor = window.editors[self.keys[0]]
            quality = editor.settings.quality
            quality.setValue(23)
            editor.settings.refresh_estimate()
            before = editor.settings.estimate_rows["output"].text()
            self.send_parameter_wheel(quality.slider, angle=120)
            self.assertEqual(quality.readout.text(), "CRF 22")
            self.assertNotEqual(editor.settings.estimate_rows["output"].text(), before)
            self.send_parameter_wheel(quality.slider, angle=-120, pixels=-32)
            self.assertEqual(quality.value(), 23)
            self.send_parameter_wheel(quality.slider, angle=120, point=QPoint(-2, 4))
            self.assertEqual(quality.value(), 23)
            quality.set_measure("CQ")
            self.send_parameter_wheel(quality.slider, angle=120)
            self.assertEqual(quality.value(), 23)
            quality.set_measure("CRF")

        self.wait(lambda: editor.session.frames is not None and editor.scope is None)
        strip = editor.filmstrip
        zoom = editor.timeline_zoom
        bar = strip.horizontalScrollBar()
        bar.setValue(18)
        cursor = editor.cursor.value()
        self.send_parameter_wheel(zoom, angle=-120)
        self.assertEqual((zoom.value(), strip.zoom), (3, 3))
        self.assertEqual(editor.cursor.value(), cursor)
        self.assertEqual(bar.value(), 18)
        self.send_parameter_wheel(zoom, angle=120)
        self.assertEqual((zoom.value(), strip.zoom), (4, 4))

        editor.transport._show_volume_popup()
        slider = editor.transport.volume_popup.slider
        slider.setValue(50)
        self.send_parameter_wheel(slider, angle=120)
        self.assertEqual((editor.transport.playback_level, editor.transport.player_volume.value()), (55, 55))
        self.assertEqual(editor.transport.volume_popup.label.text(), "55%")
        self.send_parameter_wheel(slider, pixels=-16)
        self.assertEqual(editor.transport.playback_level, 55)
        self.send_parameter_wheel(slider, pixels=-16)
        self.assertEqual(editor.transport.playback_level, 50)
        self.assertEqual(editor.transport.player_seek.value(), cursor)

    def test_wheel_leaves_model_and_native_dialog_fields_unchanged(self):
        self.nav(2)
        self.settle()
        session = self.window.agent_session
        dialog = ModelDialog(session.settings, session.provider, session.config, self.window)
        self.addCleanup(dialog.close)
        dialog.show()
        dialog.advanced.click()
        self.wait(dialog.timeout.isVisible)
        for widget, value in ((dialog.providers, dialog.providers.currentIndex),
                              (dialog.model, dialog.model.currentText),
                              (dialog.timeout.lineEdit(), dialog.timeout.value)):
            widget.setFocus()
            before = value()
            self.send_parameter_wheel(widget, angle=120)
            self.send_parameter_wheel(widget, pixels=-40)
            self.assertEqual(value(), before)
        dialog.timeout.setValue(30)
        QTest.keyClick(dialog.timeout, Qt.Key.Key_Up)
        self.assertEqual(dialog.timeout.value(), 31)
        dialog.close()

        prompt = QInputDialog(self.window)
        self.addCleanup(prompt.close)
        prompt.setInputMode(QInputDialog.InputMode.IntInput)
        prompt.setIntRange(16, 8192)
        prompt.setIntValue(1280)
        prompt.show()
        spin = prompt.findChild(QSpinBox)
        self.wait(spin.isVisible)
        self.send_parameter_wheel(spin, angle=-120)
        self.assertEqual(prompt.intValue(), 1280)
        QTest.keyClick(spin, Qt.Key.Key_Up)
        self.assertEqual(prompt.intValue(), 1281)

    def test_agent_transition_preserves_queue_and_preview(self):
        window = self.window
        transition = window.page_transition
        self.nav(2)
        transition.animation.pause()
        transition.animation.setCurrentTime(180)
        self.assertGreater(transition.position, 0)
        self.assertLess(window.agent_page.x(), window.page_host.width())
        self.assertTrue(window.pages.isHidden())
        self.assertGreater(window.preview_rail.width(), window._rail_width())
        transition.animation.resume()
        self.settle()
        self.assertEqual(window.preview_rail.width(), window._rail_width())

        self.nav(1)
        self.settle()
        editor = window.pages.currentWidget()
        self.wait(lambda: editor.session.frames and editor.scope is None)
        editor._locate_frame(12)
        rail = window.preview_rail.geometry()
        self.nav(2)
        transition.agent_animation.pause()
        transition.agent_animation.setCurrentTime(180)
        self.assertEqual(window.preview_rail.geometry(), rail)
        self.assertTrue(window.pages.isVisible())
        self.assertGreater(self.page.x(), window.pages.x())
        self.capture("02-Agent-转场中间帧.png")
        middle = self.page.geometry()
        self.nav(1)
        self.assertEqual(self.page.geometry(), middle)
        transition.agent_animation.resume()
        self.settle()
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(editor.cursor.value(), 12)

        editor.transport.toggle()
        self.wait(lambda: editor.transport.playback is not None)
        self.nav(2)
        self.settle()
        paused_frame = editor.cursor.value()
        self.assertIsNone(editor.transport.playback)
        self.nav(1)
        self.settle()
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(editor.cursor.value(), paused_frame)

        for index in (2, 0, 1, 2, 1) * 4:
            self.nav(index)
            QTest.qWait(25)
        window.resize(940, 720)
        window.showMaximized()
        window.showNormal()
        self.settle()
        self.assertEqual(window.navigation.currentIndex(), 1)
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(window._selected().key, self.keys[0])
        self.assertTrue(self.page.isHidden())
