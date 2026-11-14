"""Sample real widget geometry and verify state through transitions."""

import threading
from unittest.mock import patch
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from frameflow.media import MediaError
from frameflow.media import convert
from frameflow.window import MainWindow
from frameflow.editing import render_edit

from workflow_support import WorkflowFixture


class DesktopTransitionsTest(WorkflowFixture):
    def test_selection_animation_has_intermediate_frames(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        second = self.root / "第二个视频.mov"
        second.write_bytes(self.source.read_bytes())
        window.add_paths([self.source, second])
        first, following = window.rows.values()
        QTest.qWait(200)
        values = []
        following.selection_motion.valueChanged.connect(values.append)
        window.list.setCurrentRow(1)
        following.selection_motion.pause()
        for fraction in (.25, .50, .75):
            following.selection_motion.setCurrentTime(round(following.selection_motion.duration() * fraction))
        following.selection_motion.resume()
        self.wait_for(lambda: following.selection_motion.value == 1, "选中动画")
        self.assertGreaterEqual(len({value for value in values if 0 < value < 1}), 3)
        window.list.setCurrentRow(0)
        self.wait_for(lambda: 0 < following.selection_motion.value < 1, "反向过渡")
        before = following.selection_motion.value
        window.list.setCurrentRow(1)
        self.assertAlmostEqual(following.selection_motion.value, before, delta=0.02)
        self.wait_for(lambda: following.selection_motion.value == 1, "快速切回完成")
        self.assertEqual(first.selection_motion.value, 0)
        self.wait_for(lambda: all(job.picture for job in window.jobs.values()), "侧栏缩略图")
        page_frames = []
        transition = window.page_transition
        transition.animation.valueChanged.connect(page_frames.append)
        window.navigation.setCurrentIndex(1)
        self.assertTrue(transition.running)
        self.wait_for(lambda: .15 < transition.position < .8, "预览从右侧滑入")
        window.navigation.setCurrentIndex(0)
        self.assertFalse(transition.target_preview)
        self.wait_for(lambda: not transition.running, "中途返回格式转换")
        self.assertIs(window.pages.currentWidget(), window.conversion_page)
        window.navigation.setCurrentIndex(1)
        self.wait_for(lambda: window.navigation.position == 1 and not transition.running, "页面过渡")
        self.assertGreaterEqual(len({value for value in page_frames if 0 < value < 1}), 3)
        # 预览页固定窄队列，不再有展开/收起
        self.assertGreaterEqual(window.preview_rail.width(), 232)
        self.assertFalse(window.preview_files.currentItem().icon().isNull())
        self.assertEqual(window.preview_files.currentItem().text(), second.name)
        self.assertTrue(window.rail_count.text().endswith("个文件"))
        editor = window.pages.currentWidget()
        window.resize(window.minimumSize())
        self.app.processEvents()
        self.assertFalse(hasattr(editor, "frame_label"))
        self.assertTrue(editor.media_summary.isVisible())
        self.assertTrue(editor.settings.directory_group.isVisible())
        self.assertTrue(editor.transport.play_button.visibleRegion().boundingRect().contains(editor.transport.play_button.rect()))
        first_item = window.preview_files.item(0)
        QTest.mouseClick(window.preview_files.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.preview_files.visualItemRect(first_item).center())
        self.assertEqual(window.pages.currentWidget().path, self.source)
        window.preview_files.setCurrentRow(1)
        self.assertIs(window.pages.currentWidget(), editor)
        window.navigation.setCurrentIndex(0)
        self.wait_for(lambda: not transition.running, "返回格式转换")
        self.assertTrue(window.preview_rail.isHidden())

    def test_page_transition_moves_real_widgets_in_both_directions(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        window.add_paths([self.source])
        self.wait_for(lambda: window.mounted_editor is not None, "转换设置就绪")
        editor = window.mounted_editor
        settings = editor.settings
        host = window.page_host
        transition = window.page_transition
        start_queue = window.queue_card.geometry()
        start_inspector = window.inspector.geometry()
        self.assertIs(host.layout(), transition)
        self.assertIs(window.pages.parentWidget(), host)
        self.assertTrue(window.pages.isHidden())

        window.navigation.setCurrentIndex(1)
        transition.animation.pause()

        def sample(fraction):
            transition.animation.setCurrentTime(round(transition.animation.duration() * fraction))
            self.app.processEvents()
            rail = window.queue_card if window.preview_rail.isHidden() else window.preview_rail
            center = editor.workspace_split.widget(0)
            self.assertEqual(window.navigation.position, transition.position)
            self.assertLessEqual(rail.geometry().right(), window.pages.x())
            return (rail.width(), center.mapTo(host, QPoint()).x(),
                    editor.inspector.mapTo(host, QPoint()).x())

        initial = sample(0)
        self.assertEqual(initial[0], start_queue.width())
        self.assertIs(window.mounted_editor, editor)
        start_x = window.pages.x()
        first_motion = sample(.23)
        self.assertLess(first_motion[0], initial[0])
        self.assertLess(window.pages.x(), start_x)
        early = sample(.35)
        self.assertTrue(window.inspector.isVisible())
        self.assertIs(window.mounted_editor, editor)
        self.assertLess(early[0], first_motion[0])
        self.assertLess(early[1], first_motion[1])
        self.assertEqual(early[1] - first_motion[1], early[2] - first_motion[2])
        late = sample(.72)
        self.assertTrue(window.inspector.isHidden())
        self.assertIs(editor.settings_scroll.widget(), settings)
        self.assertLess(late[0], early[0])
        self.assertEqual(late[1] - early[1], late[2] - early[2])
        transition.animation.resume()
        self.wait_for(lambda: not transition.running, "预览完全进入")
        self.assertEqual(window.preview_rail.width(), window._rail_width())
        right = host.contentsRect().right() - transition.contentsMargins().right()
        self.assertEqual(window.pages.geometry().right(), right)
        self.assertTrue(window.queue_card.isHidden())
        self.assertIs(window.pages.currentWidget(), editor)
        point = editor.transport.frame_view.mapTo(host, editor.transport.frame_view.rect().center())
        self.assertTrue(editor.isAncestorOf(host.childAt(point)))

        window.navigation.setCurrentIndex(0)
        transition.animation.pause()
        self.assertEqual(sample(.72), late)
        self.assertEqual(sample(.35), early)
        self.assertIs(window.mounted_editor, editor)
        transition.animation.resume()
        self.wait_for(lambda: not transition.running, "转换工作区恢复")
        self.assertEqual(window.queue_card.geometry(), start_queue)
        self.assertEqual(window.inspector.geometry(), start_inspector)
        self.assertTrue(window.pages.isHidden())
        self.assertTrue(window.preview_rail.isHidden())
        self.assertIs(window.pages.currentWidget(), window.conversion_page)

    def test_page_transition_reverses_in_place_and_handles_window_resize(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        transition = window.page_transition

        def click(index):
            QTest.mouseClick(window.navigation, Qt.MouseButton.LeftButton,
                             pos=window.navigation.tabRect(index).center())

        click(1)
        transition.animation.pause()
        transition.animation.setCurrentTime(transition.animation.duration() // 2)
        self.app.processEvents()
        position = transition.position
        geometry = window.pages.geometry()
        for index in (0, 1, 0, 1, 0, 1):
            click(index)
            self.assertEqual(window.navigation.currentIndex(), index)
            self.assertEqual(transition.position, position)
            self.assertEqual(window.pages.geometry(), geometry)

        window.resize(window.minimumSize())
        self.app.processEvents()
        self.assertTrue(transition.running)
        self.assertEqual(transition.position, position)
        self.assertNotEqual(window.pages.width(), geometry.width())
        window.showMaximized()
        self.app.processEvents()
        self.assertEqual(transition.position, position)
        window.showNormal()
        self.app.processEvents()
        self.assertEqual(transition.position, position)
        transition.animation.resume()
        self.wait_for(lambda: not transition.running, "窄窗口完成切页")
        self.assertEqual(window.preview_rail.width(), window._rail_width())
        self.assertEqual(window.pages.x(), window.preview_rail.geometry().right() + 1 + transition.spacing())
        right = window.page_host.contentsRect().right() - transition.contentsMargins().right()
        self.assertEqual(window.pages.geometry().right(), right)
        self.assertIs(window.pages.currentWidget(), window.empty_preview)
        click(0)
        self.wait_for(lambda: not transition.running, "窄窗口返回")
        self.assertEqual(window.inspector.geometry().right(), right)
        self.assertEqual(window.queue_card.geometry().right() + 1, window.inspector.x())
        self.assertTrue(window.pages.isHidden())

    def test_page_transition_geometry_at_quarter_progress(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        window.add_paths([self.source])
        self.wait_for(lambda: window.mounted_editor is not None, "素材可预览")
        editor = window.mounted_editor
        host = window.page_host
        transition = window.page_transition
        animation = transition.animation
        easing = animation.easingCurve()
        levels = (0, .25, .50, .75, 1)
        times = {level: min(range(animation.duration() + 1),
                            key=lambda ms: abs(easing.valueForProgress(ms / animation.duration()) - level))
                 for level in levels}

        def click(index):
            QTest.mouseClick(window.navigation, Qt.MouseButton.LeftButton,
                             pos=window.navigation.tabRect(index).center())

        def sample(level):
            animation.setCurrentTime(times[level])
            self.app.processEvents()
            self.assertAlmostEqual(transition.position, level, delta=.01)
            self.assertIs(window.pages.parentWidget(), host)
            self.assertIs(editor.parentWidget(), window.pages)
            queue = window.queue_card if window.preview_rail.isHidden() else window.preview_rail
            wide = queue is window.queue_card
            header = window.column_header if wide else window.rail_column_header
            view = window.list if wide else window.preview_files
            self.assertTrue(header.isVisible())
            self.assertEqual(window.column_header.detail, window.rail_column_header.detail)
            first_y = view.viewport().mapTo(host, view.visualItemRect(view.item(0)).topLeft()).y()
            header_y = header.mapTo(host, QPoint()).y()
            self.assertEqual(first_y - header_y, header.height())
            self.assertLess(queue.geometry().right(), window.pages.x())
            self.assertEqual(window.pages.isHidden(), level == 0)
            if level > 0:
                self.assertIs(window.pages.currentWidget(), editor)
                center = editor.workspace_split.widget(0).mapTo(host, QPoint()).x()
                settings = editor.inspector.mapTo(host, QPoint()).x()
            else:
                center = settings = None
            return queue.width(), window.pages.x(), center, settings, header.detail, first_y

        click(1)
        animation.pause()
        forward = [sample(level) for level in levels]
        animation.resume()
        self.wait_for(lambda: not transition.running, "预览端点")
        self.assertIs(editor.settings_scroll.widget(), editor.settings)
        click(0)
        animation.pause()
        backward = [sample(level) for level in reversed(levels)]
        animation.resume()
        self.wait_for(lambda: not transition.running, "转换端点")
        self.assertEqual(forward, list(reversed(backward)))
        self.assertTrue(all(a[0] > b[0] and a[1] > b[1] for a, b in zip(forward, forward[1:])))
        self.assertEqual(len({settings - center for _, _, center, settings, _, _ in forward[1:]}), 1)
        opacity = [entry[4] for entry in forward]
        self.assertEqual((opacity[0], opacity[-1]), (1.0, 0.0))
        self.assertTrue(any(0 < value < 1 for value in opacity))
        self.assertEqual(opacity, sorted(opacity, reverse=True))
        self.assertEqual(len({entry[5] for entry in forward}), 1)
        self.assertIs(window.mounted_editor, editor)
        print(f"\n转场几何 DPR={window.devicePixelRatio():.2f}，四元组为队列宽、工作区 X、播放器栏 X、右栏 X")
        for level, geometry in zip(levels, forward):
            print(f"  {level:.0%} {geometry}")

    def test_page_transition_twenty_clicks_keep_queue_and_editor(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        second = self.root / "第二条 素材.mov"
        second.write_bytes(self.source.read_bytes())
        window.add_paths([self.source, second])
        self.wait_for(lambda: all(job.picture for job in window.jobs.values()), "两条素材就绪")
        window.list.setCurrentRow(1)
        selected = window._selected()
        editor = window.mounted_editor
        pages = window.pages.count()
        transitions = []
        window.navigation.currentChanged.connect(transitions.append)
        for index in (1, 0) * 10:
            QTest.mouseClick(window.navigation, Qt.MouseButton.LeftButton,
                             pos=window.navigation.tabRect(index).center())
            QTest.qWait(25)
            self.assertIs(window._selected(), selected)
        self.wait_for(lambda: not window.page_transition.running, "快切后停在格式转换")
        self.assertEqual(transitions, [1, 0] * 10)
        self.assertEqual(window.pages.count(), pages)
        self.assertIs(window.mounted_editor, editor)
        self.assertIs(window.pages.currentWidget(), window.conversion_page)
        self.assertEqual(window.preview_files.currentRow(), 1)
        self.assertTrue(window.pages.isHidden())
        self.assertTrue(window.preview_rail.isHidden())

    def test_page_transition_keeps_playback_and_edit_state_through_reversal(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(window.minimumSize())
        window.show()
        window.add_paths([self.source])
        self.wait_for(lambda: window.mounted_editor is not None, "选中视频")
        window.navigation.setCurrentIndex(1)
        editor = window.pages.currentWidget()
        transition = window.page_transition
        self.wait_for(lambda: editor.session.frames and editor.scope is None and not transition.running, "视频预览就绪")
        editor.settings.quality.setValue(27)
        editor.transport.player_volume.setValue(35)
        video = tuple(editor.session.video)
        with patch("frameflow.playback.WaveOutput"):
            editor.transport.play_button.click()
            player = editor.transport.playback
            self.wait_for(lambda: player.started is not None, "视频正在播放")
            window.navigation.setCurrentIndex(0)
            self.wait_for(lambda: 0 < transition.position < .85, "播放中退出预览")
            self.assertIs(editor.transport.playback, player)
            window.navigation.setCurrentIndex(1)
            self.wait_for(lambda: not transition.running, "反向回到预览")
            self.assertIs(editor.transport.playback, player)
            window.navigation.setCurrentIndex(0)
            self.wait_for(lambda: not transition.running, "完整退出预览")
            self.assertIsNone(editor.transport.playback)
        paused_at = editor.cursor.value()
        window.navigation.setCurrentIndex(1)
        self.wait_for(lambda: not transition.running, "重新进入原编辑器")
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(tuple(editor.session.video), video)
        self.assertEqual(editor.cursor.value(), paused_at)
        self.assertEqual(editor.transport.player_volume.value(), 35)
        self.assertEqual(editor.settings.quality.value(), 27)
        self.assertEqual(window._selected().path, self.source)

    def test_page_transition_during_batch_conversion_and_editor_export(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        window.add_paths([self.source])
        self.wait_for(lambda: window.mounted_editor is not None, "选中待转换视频")
        job = window._selected()
        release = threading.Event()

        def hold(operation):
            def run(*args, **kwargs):
                if not release.wait(15):
                    raise MediaError("测试未释放转换任务")
                return operation(*args, **kwargs)
            return run

        with patch("frameflow.conversion_batch.convert", side_effect=hold(convert)):
            window.start_button.click()
            try:
                scope = window.scope
                self.assertIsNotNone(scope)
                self.wait_for(lambda: job.state == "转换中", "批量转换已启动")
                for index in (1, 0):
                    window.navigation.setCurrentIndex(index)
                    self.wait_for(lambda: not window.page_transition.running, "任务中切页")
                    self.assertIs(window.scope, scope)
                    self.assertFalse(scope.cancelled.is_set())
                self.assertTrue(window.cancel_button.isEnabled())
                self.assertFalse(window.mounted_editor.settings.isEnabled())
            finally:
                release.set()
            self.wait_for(lambda: window.scope is None, "转换完成")
        self.assertTrue(job.output.is_file())
        self.decode(job.output)

        window.navigation.setCurrentIndex(1)
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.session.frames and editor.scope is None and not window.page_transition.running,
                      "可导出编辑结果")
        outputs = []
        editor.saved.connect(outputs.append)
        release.clear()
        with patch("frameflow.editor_export.render_edit", side_effect=hold(render_edit)):
            window.preview_footer.start.click()
            try:
                scope = editor.scope
                self.assertIsNotNone(scope)
                for index in (0, 1):
                    window.navigation.setCurrentIndex(index)
                    self.wait_for(lambda: not window.page_transition.running, "编辑导出中切页")
                    self.assertIs(editor.scope, scope)
                    self.assertFalse(scope.cancelled.is_set())
                self.assertTrue(window.preview_footer.cancel.isEnabled())
            finally:
                release.set()
            self.wait_for(lambda: outputs and editor.scope is None, "编辑导出完成")
        self.decode(outputs[0])
