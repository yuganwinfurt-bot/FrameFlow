"""Waveform loading, audio edits and linked video edits."""

import wave
from array import array
from unittest.mock import patch
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from frameflow.media import Processes
from frameflow.media import probe
from frameflow.editor import Editor
from frameflow.editing import Clip, audio_preview, index_frames, length
from workflow_support import WorkflowFixture


class AudioWorkspaceTest(WorkflowFixture):
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
