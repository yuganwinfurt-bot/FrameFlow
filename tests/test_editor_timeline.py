"""Verify track selections, time-axis edits and linked audio."""

import sys
import wave
from array import array
from contextlib import nullcontext
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
    QWheelEvent,
)
from PySide6.QtTest import QTest
from frameflow.media import Processes
from frameflow.media import probe
from frameflow.window import MainWindow
from frameflow.editor import Editor
from frameflow.editing import Clip, audio_preview, index_frames, length, slice_track
from frameflow.motion import QUEUED_MEDIA

from workflow_support import WorkflowFixture


class EditorTimelineTest(WorkflowFixture):
    def test_video_editor_frame_cut_paste_save_and_queue(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        window.add_paths([self.source])
        self.wait_for(lambda: window._selected().info is not None, "视频可编辑")
        original = self.source.read_bytes()
        editor = Editor(self.source, self.info, self.tools, self.root, window)
        self.addCleanup(self.dispose_editor, editor)
        editor.saved.connect(window._register_editor_output)
        editor.show()
        self.wait_for(lambda: editor.session.frames and editor.transport.frame_view.original and editor.scope is None, "实际帧数和预览")
        editor.settings.refresh_density()
        self.assertTrue(editor.settings.mode_hint.isVisible())
        self.assertIn("手动设置编码", editor.settings.mode_hint.text())
        self.assertTrue(editor.settings.quality.note.isVisible())
        self.assertTrue(editor.settings.quality.ends.isVisible())
        self.assertEqual(editor.settings.audio_codec_row.field_label.text(), "音频编码")
        self.assertEqual(editor.settings.audio_row.field_label.text(), "音频码率")
        self.assertTrue(editor.settings.audio_codec_row.isVisible())
        self.assertTrue(editor.settings.audio_row.isVisible())
        self.assertIs(editor.settings.limit_toggle.parentWidget(), editor.settings.estimate_head)
        self.assertIn("目标大小", editor.settings.limit_toggle.toolTip())
        # 目标体积输入行已从面板移除，开关只保留提示
        self.assertFalse(editor.settings.limit_row.isVisible())
        editor.settings.limit_toggle.click()
        self.assertFalse(editor.settings.limit_row.isVisible())
        editor.settings.limit_toggle.click()
        self.assertFalse(editor.settings.limit_row.isVisible())
        self.assertEqual(editor.settings.estimate_rows["output"].objectName(), "estimateValueStrong")
        total = editor.session.frames.count
        self.assertEqual(total, 58)
        self.assertEqual([editor.tabs.tabText(i) for i in range(editor.tabs.count())],
                         ["视频帧", "音轨", "图片", "文件详情"])
        editor.video_first.setValue(2)
        editor.video_last.setValue(4)
        editor.cut_video()
        self.assertEqual(int(sum(c.length for c in editor.session.video)), total - 3)
        editor.paste_video_after()
        self.assertEqual(int(sum(c.length for c in editor.session.video)), total)
        editor.cursor.setValue(3)
        self.wait_for(lambda: editor.frame_preview.shown_frame == (self.source, 2) and editor.scope is None, "重新定位帧")
        editor.settings.image_format.setCurrentIndex(editor.settings.image_format.findData("jpg"))
        editor.settings.picture_preset.setCurrentIndex(2)
        editor.settings.size.setValue(15)
        editor.save_image()
        self.wait_for(lambda: editor.scope is None and len(window.jobs) == 2, "单帧成品进入队列")
        picture = next(job.path for job in window.jobs.values() if not job.queued)
        with Image.open(picture) as saved:
            self.assertEqual(saved.format, "JPEG")
            self.assertLessEqual(picture.stat().st_size, 15_000)
        editor.video_first.setValue(1)
        editor.video_last.setValue(1)
        editor.delete_video()
        self.wait_for(lambda: editor.scope is None and editor.frame_preview.shown_frame == editor.frame_preview.requested_frame, "剪切后预览")
        editor.settings.format.setCurrentIndex(editor.settings.format.findData("mkv"))
        editor.save_edit()
        self.wait_for(lambda: editor.scope is None and len(window.jobs) == 3, "编辑视频成品进入队列")
        output = next(job.path for job in window.jobs.values() if job.path.suffix == ".mkv")
        info = probe(output, self.tools, Processes())
        self.assertEqual(index_frames(output, info, self.tools, Processes()).count, total - 1)
        self.assertIsNotNone(info.audio_stream)
        self.decode(output)
        self.assertEqual(self.source.read_bytes(), original)

    def test_filmstrip_click_saves_selected_frame(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        strip = editor.filmstrip
        self.wait_for(lambda: (self.source, 0) in strip.cache and editor.scope is None, "首屏真实帧缩略图")
        self.assertEqual(strip._tile_rect(strip._capacity() - 1).right(), strip.viewport().width() - 1)
        self.assertFalse(strip.horizontalScrollBar().isVisible())
        self.assertEqual(strip.viewport().height(), strip.tile_height)
        wheel = QWheelEvent(QPointF(30, 25), QPointF(30, 25), QPoint(), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(strip.viewport(), wheel)
        self.assertEqual(strip.horizontalScrollBar().value(), 3)
        self.assertFalse(hasattr(strip, "overview"))
        editor.locate_input.setText("9999")
        self.assertFalse(editor.locate_button.isEnabled())
        editor.locate_input.setText(str(editor.session.frames.count))
        QTest.keyClick(editor.locate_input, Qt.Key.Key_Return)
        self.assertEqual(editor.cursor.value(), editor.session.frames.count)
        editor.locate_input.setText("1")
        QTest.mouseClick(editor.locate_button, Qt.MouseButton.LeftButton)
        self.assertEqual(editor.cursor.value(), 1)
        self.assertTrue(editor.selection_label.isVisible())
        self.assertTrue(editor.settings.export_group.isVisible())
        self.assertFalse(editor.settings.picture_group.isVisible())
        strip.horizontalScrollBar().setValue(20)
        self.wait_for(lambda: (self.source, 22) in strip.cache, "滚动加载后续帧缩略图")
        QTest.mouseClick(strip.viewport(), Qt.MouseButton.LeftButton,
                         pos=strip._tile_rect(2).center())
        self.assertEqual(editor.cursor.value(), 23)
        self.assertEqual(strip.current, 23)
        self.wait_for(lambda: editor.frame_preview.shown_frame == (self.source, 22) and editor.scope is None, "缩略图定位大预览")
        self.assertIn("第 23 /", editor.settings.picture_caption.text())
        pass
        self.assertEqual(editor.settings.directory_path.path, str(self.root))
        frame_directory = self.root / "帧输出"
        with patch("frameflow.editor_settings.QFileDialog.getExistingDirectory",
                   return_value=str(frame_directory)) as choose:
            editor.settings.directory_path.selectAll()
            self.assertEqual(editor.settings.directory_path.selectedText(), str(self.root))
            QTest.mouseClick(editor.settings.directory_path, Qt.MouseButton.LeftButton)
        self.assertEqual(choose.call_args.args[2], str(self.root))
        self.assertEqual(editor.settings.directory_path.toolTip(), str(frame_directory))
        outputs = []
        editor.saved.connect(outputs.append)
        editor.save_image()
        self.wait_for(lambda: bool(outputs), "从缩略图选择的帧另存")
        self.assertIn("已保存 第 23 帧", editor.status.text())
        self.assertEqual(outputs[0].name, f"{self.source.stem}_帧000023.png")
        self.assertEqual(outputs[0].parent, frame_directory)
        with Image.open(outputs[0]) as image:
            self.assertEqual(image.size, (320, 180))
        QTest.mouseClick(strip.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ShiftModifier, pos=strip._tile_rect(4).center())
        self.assertTrue(editor.selection_label.isVisible())
        self.assertEqual((editor.video_first.value(), editor.video_last.value()), (23, 25))
        self.assertEqual(editor.selection_label.text(), "· 已选 3 帧，1 段")
        self.app.processEvents()
        self.assertEqual(editor.cursor.value(), 25)
        for field, expected in ((editor.video_first, "23"), (editor.video_last, "25")):
            self.assertEqual(field.text(), expected)
            self.assertGreater(field.width(), 25)

    def test_filmstrip_drag_context_save_and_shortcuts(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        strip = editor.filmstrip
        self.wait_for(lambda: editor.session.frames and editor.scope is None, "视频帧索引")
        scroll = strip.horizontalScrollBar()
        scroll.setValue(0)
        start = strip._tile_rect(1).center()
        end = strip._tile_rect(5).center()
        QTest.mousePress(strip.viewport(), Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(end),
                           QPointF(strip.viewport().mapToGlobal(end)), Qt.MouseButton.NoButton,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(strip.viewport(), move)
        QTest.mouseRelease(strip.viewport(), Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        # 一屏能显示几格随窗口宽度变化，期望值按缩放后的实际格子换算。
        expected = ((strip._frame_at(start.x()), strip._frame_at(end.x())),)
        self.assertEqual(editor.video_selection, expected)
        self.assertEqual((editor.video_first.value(), editor.video_last.value()), expected[0])

        visible_last = strip._capacity()
        edge_start = strip._tile_rect(visible_last - 1).center()
        edge_end = QPoint(strip.viewport().width() - 3, 25)
        QTest.mousePress(strip.viewport(), Qt.MouseButton.LeftButton, pos=edge_start)
        edge_move = QMouseEvent(QEvent.Type.MouseMove, QPointF(edge_end),
                                QPointF(strip.viewport().mapToGlobal(edge_end)), Qt.MouseButton.NoButton,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(strip.viewport(), edge_move)
        self.assertTrue(strip.drag_scroll.isActive())
        strip._scroll_drag_edge()
        QTest.mouseRelease(strip.viewport(), Qt.MouseButton.LeftButton, pos=edge_end)
        self.assertGreater(scroll.value(), 0)
        self.assertGreater(editor.video_selection[-1][1], visible_last)

        scroll.setValue(0)
        clicked = strip._frame_at(strip._tile_rect(2).center().x())
        first = max(1, clicked - 2)
        last = first + 4
        editor._set_video_range(first, last)
        editor.video_overrides.clear()
        editor._update_range_marker()
        menu = editor.frame_context_menu
        QTest.mouseClick(strip.viewport(), Qt.MouseButton.RightButton,
                         pos=strip._tile_rect(2).center())
        self.assertTrue(menu.isVisible())
        actions = {action.text(): action for action in menu.actions()}
        self.assertIn("片段另存为…", actions)
        self.assertIn("复制\tCtrl+C", actions)
        self.assertIn("帧后粘贴\tCtrl+V", actions)
        expected = ((first, last),)
        self.assertEqual(editor.video_selection, expected)
        menu.close()
        sound = patch("winsound.PlaySound") if sys.platform == "win32" else nullcontext()
        with sound:
            actions["播放选区"].trigger()
            self.assertEqual(editor.transport.playback.count, 5)
            self.wait_for(lambda: editor.transport.playback is None, "播放到选区末尾")
            self.assertEqual(editor.cursor.value(), last)
        editor._show_frame_context_menu(last + 1, QPoint(20, 25))
        self.assertEqual(editor.video_selection, ((last + 1, last + 1),))
        picture_action = next(action for action in menu.actions() if action.text() == "保存为图片…")
        menu.close()

        saved = []
        editor.saved.connect(saved.append)
        picture = self.root / "选中的单帧.webp"
        picture.write_bytes(b"keep this file")
        with patch("frameflow.editor.QFileDialog.getSaveFileName",
                   return_value=(str(picture), "WebP 图片 (*.webp)")):
            picture_action.trigger()
        self.wait_for(lambda: bool(saved), "选中帧另存为图片")
        self.assertEqual(picture.read_bytes(), b"keep this file")
        self.assertEqual(saved[0], self.root / "选中的单帧 (1).webp")
        with Image.open(saved[0]) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (320, 180))

        editor._set_video_range(2, 5)
        editor.video_overrides.clear()
        editor._update_range_marker()
        clip = self.root / "选中的片段.mp4"
        with patch("frameflow.editor.QFileDialog.getSaveFileName",
                   return_value=(str(clip), "MP4 视频 (*.mp4)")):
            editor.save_video_selection_as()
        self.wait_for(lambda: editor.scope is None and clip.is_file(), "选中片段另存为视频")
        clip_info = probe(clip, self.tools, Processes())
        self.assertEqual(index_frames(clip, clip_info, self.tools, Processes()).count, 4)
        self.assertIsNotNone(clip_info.audio_stream)
        self.decode(clip)

        strip.setFocus()
        editor._set_video_range(6, 8)
        editor._update_range_marker()
        QTest.keyClick(strip, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(int(length(editor.session.video_clipboard)), 3)
        original_count = int(length(editor.session.video))
        QTest.keyClick(strip, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(int(length(editor.session.video)), original_count - 3)
        QTest.keyClick(strip, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(int(length(editor.session.video)), original_count)
        QTest.keyClick(strip, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(int(length(editor.session.video)), original_count + 3)

    def test_shift_extends_both_sides_and_ctrl_toggles_one_frame(self):
        source = self.root / "多帧样片.mov"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-stream_loop", "1", "-i",
                         str(self.source), "-c", "copy", str(source)], timeout=20)
        editor = Editor(source, probe(source, self.tools, Processes()), self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.session.frames and editor.scope is None, "多帧样片索引")
        strip = editor.filmstrip

        def click(frame, modifier=Qt.KeyboardModifier.NoModifier):
            scroll = strip.horizontalScrollBar()
            scroll.setValue(frame - 1)
            column = frame - 1 - scroll.value()
            QTest.mouseClick(strip.viewport(), Qt.MouseButton.LeftButton, modifier,
                             pos=strip._tile_rect(column).center())

        shift, ctrl = Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.ControlModifier
        click(1)
        click(10, shift)
        self.assertEqual(editor.video_selection, ((1, 10),))
        click(20, shift)
        click(80, ctrl)
        self.assertEqual(strip.selection, ((1, 20), (80, 80)))
        self.assertEqual(editor.selection_label.text(), "· 已选 21 帧，2 段")
        click(10, ctrl)
        self.assertEqual(strip.selection, ((1, 9), (11, 20), (80, 80)))
        click(10, ctrl)
        click(80, ctrl)
        self.assertEqual(strip.selection, ((1, 20),))
        click(80, ctrl)
        editor.cut_video()
        self.assertEqual([(clip.start, clip.end) for clip in editor.session.video_clipboard], [(0, 20), (79, 80)])
        self.assertEqual([(clip.start, clip.end) for clip in editor.session.video], [(20, 79), (80, editor.session.frames.count)])
        editor.undo()
        self.assertEqual([(clip.start, clip.end) for clip in editor.session.video], [(0, editor.session.frames.count)])

        click(20)
        click(30, shift)
        click(80, ctrl)
        click(25, ctrl)
        click(10, shift)
        self.assertEqual((editor.video_first.value(), editor.video_last.value()), (10, 30))
        self.assertEqual(strip.selection, ((10, 24), (26, 30), (80, 80)))
        click(35, shift)
        click(22, shift)
        self.assertEqual((editor.video_first.value(), editor.video_last.value()), (10, 35))
        self.assertEqual(strip.selection, ((10, 24), (26, 35), (80, 80)))

    def test_audio_and_video_trim_in_both_directions(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.session.frames and editor.scope is None, "加载带音轨的视频")
        editor.tabs.setCurrentWidget(editor.audio_page)
        self.wait_for(lambda: editor.audio_workspace.audio_view.interval and editor.scope is None, "音轨波形")
        frames = editor.session.frames.count
        fps = editor.session.frames.fps

        # 毫秒精度的结束值 .417 秒恰好指向第十帧边界，不多删第十一帧。
        editor.audio_workspace.audio_first.setValue(.168)
        editor.audio_workspace.audio_last.setValue(.417)
        editor.cut_audio()
        self.wait_for(lambda: editor.audio_workspace.audio_view.interval and editor.scope is None, "剪切后重绘波形")
        self.assertEqual([(c.start, c.end) for c in editor.session.video], [(0, 4), (10, frames)])
        self.assertAlmostEqual(length(editor.session.audio_track()), (frames - 6) / fps)
        self.assertEqual((editor.session.audio_clipboard_video[0].start, editor.session.audio_clipboard_video[0].end), (4, 10))
        editor.audio_workspace.audio_cursor.setValue(4 / fps)
        editor.paste_audio()
        self.wait_for(lambda: editor.audio_workspace.audio_view.interval and editor.scope is None, "粘贴同步恢复两轨")
        self.assertEqual([(c.start, c.end) for c in editor.session.video], [(0, 4), (4, 10), (10, frames)])
        self.assertAlmostEqual(length(editor.session.audio_track()), frames / fps)
        editor.undo()
        self.assertAlmostEqual(length(editor.session.audio_track()), (frames - 6) / fps)
        editor.undo()
        self.assertEqual(len(editor.session.video), 1)
        self.assertIsNone(editor.session.audio)

        # 调过音量的轨道也必须按当前时间线同步裁剪，保留音量设置。
        editor.audio_workspace.audio_first.setValue(0)
        editor.audio_workspace.audio_last.setValue(frames / fps)
        editor.audio_workspace.volume.setValue(35)
        editor.set_volume()
        editor.tabs.setCurrentWidget(editor.video_page)
        editor.video_first.setValue(13)
        editor.video_last.setValue(24)
        editor.delete_video()
        self.assertEqual([(c.start, c.end) for c in editor.session.video], [(0, 12), (24, frames)])
        self.assertEqual(editor.session.frame_ref(int(length(editor.session.video))), (self.source, frames - 1))
        self.assertAlmostEqual(length(editor.session.audio), (frames - 12) / fps)
        self.assertAlmostEqual(editor.session.audio[1].start, 1, delta=.001)
        self.assertEqual({c.volume for c in editor.session.audio}, {.35})
        outputs = []
        editor.saved.connect(outputs.append)
        editor.save_edit()
        self.wait_for(lambda: outputs and editor.scope is None, "导出同步裁剪成品")
        output_info = probe(outputs[0], self.tools, Processes())
        self.assertEqual(index_frames(outputs[0], output_info, self.tools, Processes()).count, frames - 12)
        self.assertIsNotNone(output_info.audio_stream)
        self.assertAlmostEqual(output_info.duration, (frames - 12) / fps, delta=.05)
        self.decode(outputs[0])

    def test_audio_editor_preview_insert_remove_and_volume(self):
        source = self.root / "编辑音轨.wav"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.source),
                         "-map", "0:a:0", str(source)], timeout=10)
        info = probe(source, self.tools, Processes())
        editor = Editor(source, info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.scope is None and editor.audio_workspace.audio_view.bands, "选区波形")
        bounds = editor.audio_workspace.audio_view._bounds()
        self.assertGreater(bounds.height(), editor.audio_workspace.audio_view.height() * .75)
        # 音频文件只有「音轨」一个编辑页，外加通用的「文件详情」
        self.assertEqual(editor.tabs.count(), 2)
        self.assertEqual(editor.tabs.tabText(0), "音轨")
        self.assertEqual(editor.tabs.tabText(1), "文件详情")
        self.assertGreater(editor.audio_workspace.audio_view.waveform.frames, 0)
        editor.audio_workspace.audio_first.setValue(0)
        editor.audio_workspace.audio_last.setValue(.5)
        editor.cut_audio()
        self.assertAlmostEqual(sum(c.length for c in editor.session.audio), info.duration - .5, delta=.01)
        editor.audio_workspace.audio_cursor.setValue(.4)
        editor.paste_audio()
        self.assertAlmostEqual(sum(c.length for c in editor.session.audio), info.duration, delta=.01)
        editor.audio_workspace.volume.setValue(40)
        editor.set_volume()
        self.assertTrue(any(clip.volume == .4 for clip in editor.session.audio))
        with patch("frameflow.editor.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            editor.insert_audio()
        self.wait_for(lambda: editor.scope is None and sum(c.length for c in editor.session.audio) > info.duration, "插入音频")
        outputs = []
        editor.saved.connect(outputs.append)
        editor.settings.format.setCurrentIndex(editor.settings.format.findData("wav"))
        editor.save_edit()
        self.wait_for(lambda: bool(outputs), "保存编辑音频")
        self.assertAlmostEqual(probe(outputs[0], self.tools, Processes()).duration, info.duration * 2, delta=.06)
        self.decode(outputs[0])

    def test_filmstrip_gap_animates_and_noncontiguous_move_undo_restores_both_tracks(self):
        image = self.root / "still.png"
        Image.new("RGB", (32, 32), "blue").save(image)
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(1200, 780)
        window.show()
        window.add_paths([self.source, image])
        self.wait_for(lambda: all(job.picture for job in window.jobs.values()), "队列")
        window.inspector.edit_button.click()
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.session.frames and editor.scope is None and not window.page_transition.running, "时间轴")
        strip = editor.filmstrip
        original = editor.session.video
        fps = editor.session.frames.fps
        original_audio = [Clip(self.source, self.info.audio_stream, 0, 5 / fps)]
        editor.session.audio = original_audio
        self.addCleanup(strip.springs.reset)
        mime = QMimeData()
        mime.setData(QUEUED_MEDIA, next(key for key, job in window.jobs.items() if job.path == image).encode("ascii"))
        point = QPoint(strip._tile_rect(2).left() + 4, 15)
        enter = QDragEnterEvent(point, Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(strip.viewport(), enter)
        self.assertTrue(enter.isAccepted())
        self.assertEqual(strip.drop_position, 2)
        spring = strip.springs.states[3]
        self.assertEqual(spring.position, 0)
        spring.advance(.012)
        self.assertGreater(spring.position, 0)
        self.assertLess(spring.position, spring.target)
        self.app.sendEvent(strip.viewport(), QDragLeaveEvent())
        self.wait_for(lambda: not strip.springs.timer.isActive(), "时间轴空位收回")
        self.assertIs(editor.session.video, original)

        button = Qt.MouseButton.RightButton
        viewport = strip.viewport()
        start = strip._tile_rect(1).center()
        end = strip._tile_rect(4).center()
        editor._set_video_range(2, 3)
        editor.video_overrides.update({5: True, 6: True})
        editor._update_range_marker()
        selection = ((2, 3), (5, 6))
        self.assertEqual(editor.video_selection, selection)

        def drag_right():
            QTest.mousePress(viewport, button, pos=start)
            motion = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
                                 Qt.MouseButton.NoButton, button, Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(viewport, motion)
            self.assertIsNotNone(strip.move_point)
            self.assertEqual(strip.drop_position, 4)
            self.assertEqual(strip.drop_frames, 4)
            self.assertFalse(editor.frame_context_menu.isVisible())
            self.assertIs(editor.session.video, original)

        drag_right()
        QTest.keyClick(strip, Qt.Key.Key_Escape)
        QTest.mouseRelease(viewport, button, pos=end)
        self.assertFalse(editor.frame_context_menu.isVisible())
        self.assertEqual(editor.video_selection, selection)
        self.assertIs(editor.session.video, original)
        drag_right()
        QTest.mouseRelease(viewport, button, pos=end)
        refs = [editor.session.frame_ref(frame)[1] for frame in range(1, 10)]
        self.assertEqual(refs, [0, 3, 6, 7, 1, 2, 4, 5, 8])
        self.assertEqual(len(editor.session.history), 1)
        self.assertEqual(editor.video_selection, ((5, 8),))
        self.assertAlmostEqual(length(editor.session.audio), length(original) / fps)
        silent = slice_track(editor.session.audio, 2 / fps, 4 / fps)
        self.assertTrue(silent and all(clip.path is None for clip in silent))
        moved_audio = slice_track(editor.session.audio, 4 / fps, 6 / fps)
        self.assertAlmostEqual(moved_audio[0].start, 1 / fps)
        editor.undo()
        self.assertIs(editor.session.video, original)
        self.assertIs(editor.session.audio, original_audio)

    def test_external_timeline_preview_keeps_one_tile_and_tracks_visible_boundaries(self):
        short = self.root / "1.jpg"
        long = self.root / ("很长的插入素材名称" * 8 + ".png")
        Image.new("RGB", (64, 96), "blue").save(short)
        Image.new("RGB", (64, 96), "red").save(long)
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(1500, 800)
        window.show()
        window.add_paths([self.source, short, long])
        self.wait_for(lambda: all(job.picture for job in window.jobs.values()), "素材导入")
        window.inspector.edit_button.click()
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.session.frames and editor.scope is None and not window.page_transition.running,
                      "时间轴就绪")
        strip = editor.filmstrip
        viewport = strip.viewport()
        editor._set_video_range(3, 8)
        editor._update_range_marker()
        track, selection = editor.session.video, editor.video_selection
        keys = {job.path: key for key, job in window.jobs.items()}
        widths = []

        def hover(path, x, expected, *, after=False):
            mime = QMimeData()
            mime.setData(QUEUED_MEDIA, keys[path].encode("ascii"))
            event = QDragEnterEvent(QPointF(x, 15), Qt.DropAction.CopyAction, mime,
                                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(viewport, event)
            self.assertTrue(event.isAccepted())
            self.assertEqual(strip.drop_position, expected)
            label = f"插入第 {expected} 帧后" if after else f"插入第 {expected + 1} 帧前"
            self.assertEqual(strip.drop_badge.text(), label)
            widths.append(strip.springs.states["width"].target)
            QTest.qWait(100)
            self.assertEqual(strip.drop_position, expected)
            self.assertIs(editor.session.video, track)
            self.assertEqual(editor.video_selection, selection)
            self.app.sendEvent(viewport, QDragLeaveEvent())
            self.assertTrue(strip.drop_badge.isHidden())
            self.wait_for(lambda: not strip.springs.timer.isActive(), "空位平滑收回")

        hover(short, 8, 0)
        hover(long, strip._tile_rect(3).left(), 3)
        hover(self.source, strip._tile_rect(6).left(), 6)
        hover(short, strip._tile_rect(4).right() - 6, 5, after=True)
        self.assertEqual(len(set(widths)), 1)
        self.assertAlmostEqual(widths[0], viewport.width() / strip._capacity())
        strip.horizontalScrollBar().setValue(9)
        hover(short, strip._tile_rect(2).left(), 11)
        strip.set_zoom(0)
        last_column = (strip.total - 1 - strip.horizontalScrollBar().value()) // strip._stride()
        hover(long, strip._tile_rect(last_column).right() - 2, strip.total, after=True)

    def test_external_timeline_scroll_updates_the_drop_without_moving_the_pointer(self):
        image = self.root / "拖入.jpg"
        Image.new("RGB", (48, 48), "blue").save(image)
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(1300, 780)
        window.show()
        window.add_paths([self.source, image])
        self.wait_for(lambda: all(job.picture for job in window.jobs.values()), "素材导入")
        window.inspector.edit_button.click()
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.session.frames and editor.scope is None and not window.page_transition.running,
                      "时间轴就绪")
        strip = editor.filmstrip
        viewport = strip.viewport()
        bar = strip.horizontalScrollBar()
        bar.setValue(16)
        original = editor.session.video
        point = QPoint(strip._tile_rect(3).left() + 8, 20)
        mime = QMimeData()
        mime.setData(QUEUED_MEDIA, next(key for key, job in window.jobs.items() if job.path == image).encode("ascii"))
        action = Qt.DropAction.CopyAction
        button = Qt.MouseButton.LeftButton
        modifier = Qt.KeyboardModifier.NoModifier
        enter = QDragEnterEvent(QPointF(point), action, mime, button, modifier)
        self.app.sendEvent(viewport, enter)
        self.assertTrue(enter.isAccepted())
        self.assertEqual(strip.drop_position, 19)

        def wheel(delta):
            event = QWheelEvent(QPointF(point), QPointF(viewport.mapToGlobal(point)), QPoint(), QPoint(0, delta),
                                button, Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
            self.app.sendEvent(viewport, event)
            self.assertTrue(event.isAccepted())

        wheel(-120)
        self.assertEqual((bar.value(), strip.drop_position), (19, 22))
        self.assertEqual(strip.drop_badge.text(), "插入第 23 帧前")
        self.assertEqual(strip.zoom, 4)
        strip.set_current(1, playing=True)
        self.assertEqual((bar.value(), strip.drop_position), (19, 22))
        wheel(120)
        self.assertEqual((bar.value(), strip.drop_position), (16, 19))
        wheel(-20)
        self.assertEqual(bar.value(), 16)
        wheel(-20)
        self.assertEqual((bar.value(), strip.drop_position), (17, 20))
        self.assertIs(editor.session.video, original)
        drop = QDropEvent(QPointF(point), action, mime, button, modifier)
        self.app.sendEvent(viewport, drop)
        self.assertTrue(drop.isAccepted())
        self.wait_for(lambda: editor.scope is None and image in editor.session.imported_images, "图片插入")
        self.assertEqual(editor.session.frame_ref(21)[0], editor.session.imported_images[image])
        editor.undo()
        self.assertIs(editor.session.video, original)

        def drag_at(x):
            self.app.sendEvent(viewport, QDragMoveEvent(QPointF(x, 20), action, mime, button, modifier))

        bar.setValue(16)
        self.app.sendEvent(viewport, QDragEnterEvent(QPointF(point), action, mime, button, modifier))
        drag_at(viewport.width() - 34)
        self.wait_for(lambda: bar.value() > 16, "进入右边缘后自动向右滚动")
        drag_at(point.x())
        offset = bar.value()
        QTest.qWait(180)
        self.assertEqual(bar.value(), offset)
        drag_at(34)
        self.wait_for(lambda: bar.value() < offset, "进入左边缘后自动向左滚动")
        self.app.sendEvent(viewport, QDragLeaveEvent())
        self.assertTrue(strip.drop_badge.isHidden())
        self.assertIs(editor.session.video, original)

    def test_video_audio_overflow_mark_can_select_move_and_delete(self):
        audio = self.root / "额外音频.wav"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                         "sine=frequency=600", "-t", "0.9", str(audio)], timeout=15)
        audio_info = probe(audio, self.tools, Processes())
        editor = Editor(self.source, self.info, self.tools, self.root,
                        lookup_media=lambda key: (audio, audio_info, None) if key == "sound" else None)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.session.frames and editor.scope is None, "读取视频")
        editor.tabs.setCurrentWidget(editor.audio_page)
        view = editor.audio_workspace.audio_view
        self.wait_for(lambda: editor.scope is None and view.waveform, "原音轨")
        original_video = editor.session.video
        video_end = editor.session.video_duration()
        editor._drop_audio("sound", .25)
        self.wait_for(lambda: editor.scope is None and view.waveform, "显示超出部分波形")
        total = video_end + audio_info.duration
        self.assertAlmostEqual(view.overflow[0], video_end)
        self.assertAlmostEqual(view.loaded_interval[1], total)
        self.assertEqual(view.trim_mark, view.overflow)
        self.assertIn("超出 0.900 秒", editor.audio_workspace.overflow_label.text())

        def point(position):
            bounds = view._bounds()
            start, end = view.interval
            return QPoint(round(bounds.left() + (position - start) * bounds.width() / (end - start)),
                          round(bounds.center().y()))

        def drag(button, start, end):
            start, end = point(start), point(end)
            QTest.mousePress(view, button, pos=start)
            event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(view.mapToGlobal(end)),
                                Qt.MouseButton.NoButton, button, Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(view, event)
            QTest.mouseRelease(view, button, pos=end)

        def delete_mark():
            start, end = view.trim_mark
            QTest.mouseClick(view, Qt.MouseButton.RightButton, pos=point((start + end) / 2))
            menu = editor.audio_workspace.audio_context_menu
            self.assertTrue(menu.isVisible())
            action = next(a for a in menu.actions() if a.text().startswith("删除标红音频"))
            menu.close()
            action.trigger()

        delete_mark()
        self.assertIs(editor.session.video, original_video)
        self.assertAlmostEqual(length(editor.session.audio_track()), video_end)
        self.assertIsNone(view.overflow)
        editor.undo()
        self.wait_for(lambda: editor.scope is None and view.waveform, "撤销后恢复超出范围")
        before = editor.session.audio
        drag(Qt.MouseButton.LeftButton, video_end + .2, video_end + .55)
        start, end = view.trim_mark
        selected = end - start
        self.assertAlmostEqual(selected, .35, delta=.015)
        self.assertIn(f"标红 {selected:.3f} 秒", editor.audio_workspace.overflow_label.text())
        self.assertEqual(view._overflow_after_mark(), view.overflow)
        drag(Qt.MouseButton.RightButton, (start + end) / 2, .8)
        moved_start, moved_end = view.trim_mark
        self.assertLess(moved_end, video_end)
        self.assertAlmostEqual(moved_end - moved_start, selected)
        self.assertIs(editor.session.audio, before)
        projected_start, projected_end = view._overflow_after_mark()
        self.assertAlmostEqual(projected_start, video_end)
        self.assertAlmostEqual(projected_end - projected_start, .9 - selected)
        self.assertIn(f"删除标红后还差 {.9 - selected:.3f} 秒", editor.audio_workspace.overflow_label.text())
        self.assertFalse(editor.audio_workspace.audio_context_menu.isVisible())
        delete_mark()
        self.assertAlmostEqual(length(editor.session.audio_track()), total - selected)
        self.assertIs(editor.session.video, original_video)
        self.assertAlmostEqual(view.trim_mark[1] - view.trim_mark[0], .9 - selected)

    def test_waveform_cache_and_missing_audio_recovery(self):
        source = self.root / "原路径音频.mp3"
        moved = self.root / "移走的音频.mp3"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.source),
                         "-map", "0:a:0", str(source)], timeout=15)
        editor = Editor(source, probe(source, self.tools, Processes()), self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        view = editor.audio_workspace.audio_view
        self.wait_for(lambda: editor.scope is None and view.waveform, "首次波形")
        waveform = view.waveform
        view.zoom(2)
        zoomed = view.interval
        with patch("frameflow.audio_workspace.audio_preview") as decode:
            editor.suspend()
            editor.resume()
            self.assertIs(view.waveform, waveform)
            self.assertEqual(view.interval, zoomed)
            source.rename(moved)
            editor.resume()
            self.assertIsNone(editor.scope)
            self.assertIsNone(view.waveform)
            self.assertIn("找不到音频文件", view.text())
            self.assertIn(source.name, editor.status.text())
            moved.rename(source)
            editor.audio_workspace.load_preview()
            self.assertIs(view.waveform, waveform)
            self.assertEqual(editor.status.text(), "")
            decode.assert_not_called()

    def test_quiet_waveform_expands_visually_and_preserves_pcm(self):
        quiet = self.root / "低音量与静音.wav"
        samples = array("h", [0] * 12000 + ([12] * 24 + [-12] * 24) * 500 + [0] * 12000)
        with wave.open(str(quiet), "wb") as recording:
            recording.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            recording.writeframes(samples.tobytes())
        waveform = audio_preview([Clip(quiet, 0, 0, 1)], self.tools, Processes())
        bands = waveform.envelope(0, 1, 1600)
        self.assertEqual(len(bands), 1)
        self.assertEqual(bands[0][100], (0.0, 0.0))
        self.assertGreater(max(bands[0][800]), .4)
        detail = waveform.envelope(.25, .251, 800)[0]
        self.assertEqual(len(detail), 48)
        self.assertTrue(any(low == high and high > 0 for low, high in detail))
        self.assertTrue(any(low == high and low < 0 for low, high in detail))
        pcm = waveform.samples[0]
        self.assertGreater(max(pcm), 0)
        self.assertLessEqual(max(pcm), 12)
        self.assertEqual(max(bands[0][800]), max(pcm) / waveform.reference)
