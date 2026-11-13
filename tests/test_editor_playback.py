"""Verify playback state, preview geometry and player cleanup."""

import sys
import threading
import time
import unittest
import wave
from array import array
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget
from frameflow.media import Processes
from frameflow.media import hardware_acceleration_choices, probe, video_encoder_args
from frameflow.window import MainWindow
from frameflow.editor import Editor
from frameflow.editing import length

from workflow_support import WorkflowFixture


class EditorPlaybackTest(WorkflowFixture):
    def test_edited_timeline_plays_pauses_and_cleans_up(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.session.frames and editor.frame_preview.shown_frame == (self.source, 0), "首帧预览")
        editor.timeline_zoom.setValue(0)
        self.assertGreater(editor.filmstrip._stride(), 1)
        self.assertEqual(editor.filmstrip.horizontalScrollBar().maximum(), 0)
        editor.timeline_zoom.setValue(4)
        self.assertEqual(editor.filmstrip._stride(), 1)
        editor.video_first.setValue(3)
        editor.video_last.setValue(7)
        editor.delete_video()
        self.assertEqual([(clip.start, clip.end) for clip in editor.session.video], [(0, 2), (7, 58)])
        old_preview = editor.transport.frame_view.original.cacheKey()
        sound = patch("frameflow.playback.WaveOutput") if sys.platform == "win32" else nullcontext()
        with sound as output:
            QTest.mouseClick(editor.transport.play_button, Qt.MouseButton.LeftButton)
            player = editor.transport.playback
            self.wait_for(lambda: editor.cursor.value() >= 5, "播放跨过已剪掉的区间")
            self.assertEqual(editor.frame_preview.shown_frame, (self.source, editor.cursor.value() + 4))
            self.assertNotEqual(editor.transport.frame_view.original.cacheKey(), old_preview)
            if output is not None:
                output.assert_called_once_with(player.wav)
                editor.transport.player_volume.setValue(35)
                self.assertEqual(player.volume, .35)
                editor.transport.volume_button.click()
                self.assertEqual(player.volume, 0)
                editor.transport.volume_button.click()
                self.assertEqual(player.volume, .35)
                with wave.open(str(player.wav), "rb") as audio:
                    self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 53 / 24, delta=0.01)
            editor.transport.fullscreen_button.click()
            self.assertTrue(editor.transport.fullscreen_player.isFullScreen())
            QTest.keyClick(editor.transport.fullscreen_player, Qt.Key.Key_Escape)
            self.assertIsNone(editor.transport.fullscreen_player)
            self.assertIs(editor.transport.playback, player)
            QTest.mouseClick(editor.transport.play_button, Qt.MouseButton.LeftButton)
            paused = editor.cursor.value()
            QTest.qWait(120)
            self.assertEqual(editor.cursor.value(), paused)
            self.assertTrue(player.video_task.done())
            QTest.mouseClick(editor.transport.play_button, Qt.MouseButton.LeftButton)
            self.wait_for(lambda: editor.transport.playback is None, "播放到时间线末尾")
            self.assertEqual(editor.cursor.value(), 53)
            self.assertEqual(editor.status.text(), "播放结束。")
            QTest.mouseClick(editor.transport.play_button, Qt.MouseButton.LeftButton)
            restarted = editor.transport.playback
            self.wait_for(lambda: editor.cursor.value() < paused, "末尾播放时回到开头")
            editor.close()
            self.assertIsNone(editor.transport.playback)
            self.assertTrue(restarted.video_task.done())
            self.assertFalse(Path(editor.scratch.name).exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows 播放使用本地音轨")
    def test_disjoint_playback_when_audio_ends_before_video(self):
        source = self.root / "音轨先结束.mov"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.source),
                         "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                         "-af", "atrim=duration=0.8", "-c:a", "aac", str(source)], timeout=10)
        editor = Editor(source, probe(source, self.tools, Processes()), self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.session.frames and editor.scope is None, "读取短音轨视频")
        last = editor.session.frames.count
        editor.video_selection = ((3, 7), (12, 15), (last - 5, last))
        with patch("frameflow.playback.WaveOutput"):
            editor.transport.play_video_selection()
            player = editor.transport.playback
            self.assertEqual(player.count, 15)
            self.wait_for(lambda: editor.transport.playback is None, "不连续选区播完")
            self.assertEqual(editor.cursor.value(), last)
            self.assertEqual(editor.status.text(), "播放结束。")
            self.assertFalse(player.audio_error)
            with wave.open(str(player.wav), "rb") as audio:
                frames = audio.getnframes()
                self.assertAlmostEqual(frames / audio.getframerate(), player.count / editor.session.frames.fps, delta=.002)
                pcm = array("h", audio.readframes(frames))
            self.assertTrue(any(pcm[:len(pcm) // 2]))
            self.assertFalse(any(pcm[-4000:]))

    def test_editor_rapid_seeking_keeps_preview_geometry(self):
        from frameflow.editor import frame_png

        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.frame_preview.shown_frame == (self.source, 0), "首帧")
        view = editor.transport.frame_view
        original_image = view.original.cacheKey()
        original_rect = view.geometry()
        original_timeline = editor.timeline.geometry()
        fit_rect = view._image_rect()
        point = fit_rect.center()

        def wheel(delta):
            event = QWheelEvent(point, view.mapToGlobal(point), QPoint(), QPoint(0, delta),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(view, event)

        wheel(120)
        self.assertGreater(view._image_rect().height(), fit_rect.height())
        self.assertEqual(editor.cursor.value(), 1)
        zoom = view.zoom
        entered = threading.Event()

        def slow_frame(path, info, index, tools, scope):
            if index == 7:
                entered.set()
                time.sleep(.22)
            return frame_png(path, info, index, tools, scope)

        with patch("frameflow.preview_requests.frame_png", side_effect=slow_frame):
            editor.cursor.setValue(8)
            self.wait_for(entered.is_set, "首个后台请求开始")
            editor.cursor.setValue(18)
            self.assertEqual(view.original.cacheKey(), original_image)
            self.assertEqual(view.geometry(), original_rect)
            self.assertEqual(editor.timeline.geometry(), original_timeline)
            self.wait_for(lambda: editor.frame_preview.shown_frame == (self.source, 17) and editor.frame_preview.scope is None, "显示最后选中帧")
        self.assertNotEqual(view.original.cacheKey(), original_image)
        self.assertEqual(view.geometry(), original_rect)
        self.assertEqual(editor.timeline.geometry(), original_timeline)
        self.assertEqual(view.zoom, zoom)
        wheel(-240)
        self.assertLess(view._image_rect().height(), fit_rect.height())
        QTest.mouseDClick(view, Qt.MouseButton.LeftButton)
        self.assertEqual(view._image_rect(), fit_rect)
        editor.cursor.setValue(1)
        self.assertEqual(editor.frame_preview.shown_frame, (self.source, 0))

    def test_embedded_media_preview_audio_zoom_pan_and_playback(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(window.minimumSize())
        window.show()
        window.add_paths([self.source])
        self.wait_for(lambda: window._selected() and window._selected().info, "媒体队列导入")
        QTest.mouseClick(window.edit_button, Qt.MouseButton.LeftButton)
        editor = window.pages.currentWidget()
        self.assertIsInstance(editor, Editor)
        self.assertFalse(editor.isWindow())
        self.assertEqual(window.navigation.currentIndex(), 1)
        self.wait_for(lambda: editor.session.frames and editor.scope is None and not window.page_transition.running,
                      "内嵌视频就绪")
        self.app.processEvents()
        inspector = editor.findChild(QWidget, "studioInspector")
        video_inspector = inspector.geometry()
        self.assertLess(editor.timeline.mapTo(editor.workspace_split, editor.timeline.rect().topRight()).x(),
                        video_inspector.left())
        # 时间线下面还有一条媒体信息，它才是中间栏的底边
        self.assertLess(editor.timeline.geometry().bottom(), editor.media_summary.geometry().top())
        self.assertLess(editor.media_summary.mapTo(editor.workspace_split,
                                                   editor.media_summary.rect().bottomLeft()).y(),
                        video_inspector.bottom())
        # 设置可以滚动，主任务栏的导出按钮始终可用。
        self.assertTrue(editor.settings.quality.isVisible())
        self.assertFalse(editor.settings.quality.slider.isHidden())
        self.assertTrue(window.preview_start.isEnabled())
        self.assertTrue(window.preview_start.visibleRegion().boundingRect().contains(window.preview_start.rect()))
        editor.cursor.setValue(8)
        window.navigation.setCurrentIndex(0)
        self.wait_for(lambda: not window.page_transition.running, "返回转换页")
        self.assertIs(window.pages.currentWidget(), window.conversion_page)
        window.navigation.setCurrentIndex(1)
        self.wait_for(lambda: not window.page_transition.running, "返回预览页")
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(editor.cursor.value(), 8)

        editor.tabs.setCurrentWidget(editor.audio_page)
        self.wait_for(lambda: editor.audio_view.interval and editor.scope is None, "内嵌音轨波形")
        view = editor.audio_view
        self.app.processEvents()
        self.assertEqual(inspector.geometry(), video_inspector)
        self.assertTrue(editor.timeline.isHidden())
        self.assertTrue(editor.settings.picture_group.isHidden())
        self.assertLess(editor.zoom_reset.geometry().right(), editor.audio_play.geometry().left())
        initial = view.interval
        selection = view.selection
        point = QPointF(view._bounds().center())

        def wheel(delta, modifier=Qt.KeyboardModifier.NoModifier):
            event = QWheelEvent(point, point, QPoint(), QPoint(0, delta),
                                Qt.MouseButton.NoButton, modifier, Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(view, event)

        wheel(120, Qt.KeyboardModifier.ControlModifier)
        zoomed = view.interval
        self.assertLess(zoomed[1] - zoomed[0], initial[1] - initial[0])
        wheel(-120)
        self.assertGreater(view.interval[0], zoomed[0])
        self.assertAlmostEqual(view.interval[1] - view.interval[0], zoomed[1] - zoomed[0])
        self.assertEqual(view.selection, selection)
        QTest.mouseClick(editor.zoom_reset, Qt.MouseButton.LeftButton)
        self.assertEqual(view.interval, initial)
        view.zoom_to_samples((initial[0] + initial[1]) / 2)
        self.assertGreaterEqual(view.sample_spacing(), 3)
        self.assertIn("采样点", editor.zoom_ratio.text())
        view.reset_zoom()
        drag_start = QPoint(round(view._bounds().left() + view._bounds().width() * .20), round(point.y()))
        drag_end = QPoint(round(view._bounds().left() + view._bounds().width() * .40), round(point.y()))
        QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=drag_start)
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=drag_end)
        self.assertIsNotNone(view.selection)
        QTest.mouseClick(view, Qt.MouseButton.RightButton, pos=drag_start)
        menu = editor.audio_context_menu
        self.assertTrue(menu.isVisible())
        actions = {action.text(): action for action in menu.actions()}
        self.assertIn("缩放到选区", actions)
        self.assertIn("剪切\tCtrl+X", actions)
        self.assertIn("粘贴\tCtrl+V", actions)
        self.assertIn("插入音频…", actions)
        start, end = view.selection
        menu.close()
        QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertAlmostEqual(length(editor.session.audio_clipboard), end - start, places=3)
        if sys.platform == "win32":
            with patch("winsound.PlaySound"):
                actions["播放选区"].trigger()
                self.wait_for(lambda: editor.playing, "右键播放音频选区")
                self.assertEqual(editor.audio_clock[:2], (start, end))
                editor.stop_audio()
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=point.toPoint())
        self.assertIsNone(view.selection)
        self.assertEqual(editor.audio_first.value(), editor.audio_last.value())
        self.assertFalse(editor.apply_volume.isEnabled())
        position = view.playhead
        self.assertGreater(position, initial[0])

        if sys.platform == "win32":
            with patch("winsound.PlaySound") as play_sound:
                QTest.mouseClick(editor.audio_play, Qt.MouseButton.LeftButton)
                self.wait_for(lambda: editor.playing and view.playhead > position + .06, "音频播放头前进")
                self.assertEqual(play_sound.call_args.args[0], str(editor.listen_wav))
                QTest.mouseClick(editor.audio_play, Qt.MouseButton.LeftButton)
                paused = view.playhead
                QTest.qWait(60)
                self.assertEqual(view.playhead, paused)
                QTest.mouseClick(editor.audio_play, Qt.MouseButton.LeftButton)
                self.wait_for(lambda: editor.playing and view.playhead > paused, "从暂停位置续播")
                window.navigation.setCurrentIndex(0)
                self.wait_for(lambda: not window.page_transition.running, "退出音频预览")
                self.assertFalse(editor.playing)
                window.navigation.setCurrentIndex(1)
                self.wait_for(lambda: not window.page_transition.running, "返回音频预览")

        self.assertEqual(view.interval, initial)
        self.assertGreater(view.insertion, 0)
        QTest.mouseClick(editor.zoom_reset, Qt.MouseButton.LeftButton)
        self.assertEqual(view.interval, initial)
        self.assertEqual((view.insertion, view.playhead, editor.audio_cursor.value()), (0, 0, 0))
        self.assertEqual((editor.audio_first.value(), editor.audio_last.value()), (0, 0))
        self.assertIsNone(view.selection)
        self.assertEqual(editor.audio_position.text(), "0:00.000")

        self.assertFalse(editor.audio_tools.isVisible())
        self.assertIs(window.pages.currentWidget(), editor)
        window.close()
        self.assertTrue(editor.closing)
        self.assertFalse(Path(editor.scratch.name).exists())

    def test_editor_panes_resize_and_status_wraps(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.resize(1160, 820)
        editor.show()
        self.wait_for(lambda: editor.session.frames and editor.scope is None, "可调整编辑区")

        def drag(splitter, offset):
            handle = splitter.handle(1)
            point = handle.rect().center()
            button = Qt.MouseButton.LeftButton
            QTest.mousePress(handle, button, pos=point)
            QTest.mouseMove(handle, point + offset)
            QTest.mouseRelease(handle, button, pos=point)
            self.app.processEvents()

        width = editor.workspace_split.widget(1).width()
        drag(editor.workspace_split, QPoint(-40, 0))
        self.assertGreater(editor.workspace_split.widget(1).width(), width)
        height = editor.filmstrip.viewport().height()
        drag(editor.preview_split, QPoint(0, -35))
        self.assertGreater(editor.filmstrip.viewport().height(), height)
        self.assertEqual(editor.filmstrip._tile_rect(0).height(), editor.filmstrip.viewport().height())
        self.assertGreaterEqual(editor.filmstrip.viewport().height(), 100)
        message = "已保存 " + "很长的输出文件名" * 32 + "，并加入媒体队列。"
        editor.status.setText(message)
        self.app.processEvents()
        self.assertEqual(editor.status.text(), message)
        self.assertTrue(editor.status.isReadOnly())
        self.assertTrue(editor.notice.isVisible() or not editor.notice.text())

    def test_video_frame_page_has_player_transport_media_info_and_tools(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.resize(1160, 820)
        editor.show()
        self.wait_for(lambda: editor.session.frames and editor.scope is None, "播放器和媒体信息")
        self.app.processEvents()

        self.assertTrue(editor.transport.widget.isVisible())
        self.assertTrue(editor.transport.player_seek.isVisible())
        self.assertEqual(editor.transport.player_seek.minimum(), 1)
        self.assertEqual(editor.transport.player_seek.maximum(), editor.session.frames.count)
        self.assertEqual(editor.transport.player_seek.value(), editor.cursor.value())
        self.assertTrue(editor.media_summary.isVisible())
        values = [label.text() for label in editor.media_summary.findChildren(QLabel)]
        joined = " ".join(values)
        self.assertIn("320 × 180", joined)
        self.assertIn("24 FPS", joined)
        self.assertGreaterEqual(editor.preview_split.indexOf(editor.media_summary), 0)
        self.assertTrue(editor.settings.output_file_name.text().endswith("_编辑.mp4"))
        self.assertTrue(editor.settings.acceleration.isVisible())
        self.assertEqual(editor.settings.acceleration.itemData(0), "cpu")
        self.assertTrue(editor.progress_row.isHidden())
        self.assertFalse(hasattr(editor, "frame_label"))
        self.assertIn("CPU 软件编码", [title for _, title in hardware_acceleration_choices(
            self.tools.ffmpeg, "mp4"
        )])
        for backend, _title in hardware_acceleration_choices(self.tools.ffmpeg, "mp4")[1:]:
            args = video_encoder_args("mp4", backend)
            self.assertEqual(args[0:2], ["-c:v", f"h264_{backend}"])
        self.assertTrue(editor.inspector.isVisible())
        self.assertTrue(editor.selection_label.isVisible())
        self.assertTrue(editor.settings.export_group.isVisible())

        editor.transport.player_seek.setValue(9)
        self.assertEqual(editor.cursor.value(), 9)
        self.assertEqual(editor.transport.player_time_current.text(), "00:00.333")
        QTest.mouseClick(editor.transport.player_seek, Qt.MouseButton.LeftButton,
                         pos=QPoint(editor.transport.player_seek.width() - 2, editor.transport.player_seek.height() // 2))
        self.assertEqual(editor.cursor.value(), editor.session.frames.count)
        editor.cursor.setValue(2)
        editor._use_current_frame(editor.video_first)
        editor.cursor.setValue(editor.cursor.maximum())
        editor._use_current_frame(editor.video_last)
        self.assertEqual(editor.video_first.value(), 2)
        self.assertEqual(editor.video_selection, ((2, editor.video_last.value()),))
        tail = min(25, editor.session.frames.count)
        editor._set_video_range(2, tail)
        editor._range_fields_changed()
        self.assertEqual(editor.video_selection, ((2, tail),))
        self.assertIn("1080P", editor.settings.export_edge.itemText(editor.settings.export_edge.findData(1920)))
        editor.settings.export_edge.setCurrentIndex(editor.settings.export_edge.findData(854))
        self.assertIn("854 × 480", editor.settings.export_dimensions.text())
        editor.settings.output_fps.setCurrentIndex(editor.settings.output_fps.findData(60))
        result = self.root / "片段60FPS.mp4"
        with patch("frameflow.editor.QFileDialog.getSaveFileName",
                   return_value=(str(result), "MP4 视频 (*.mp4)")):
            editor.save_video_selection_as()
        self.assertTrue(editor.progress_row.isVisible())
        self.assertIs(editor.progress_row.layout().itemAt(1).widget(), editor.cancel_button)
        self.wait_for(lambda: editor.scope is None and result.is_file(), "导出 60 FPS 片段")
        self.assertTrue(editor.progress_row.isHidden())
        exported = probe(result, self.tools, Processes())
        self.assertEqual((exported.width, exported.height), (854, 480))
        self.assertAlmostEqual(exported.fps, 60, delta=.1)
        self.assertAlmostEqual(exported.duration, 1.0, delta=.05)
