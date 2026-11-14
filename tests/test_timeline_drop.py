"""Drag real queue media onto the video timeline and audio waveform."""

import sys
import threading
import wave
from contextlib import nullcontext
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from frameflow.media import Processes
from frameflow.media import probe
from frameflow.window import MainWindow
from frameflow.editing import index_frames, length
from frameflow.motion import QUEUED_MEDIA
from workflow_support import WorkflowFixture


class TimelineDropTest(WorkflowFixture):
    def test_drag_queued_media_onto_frame_boundary_or_audio_marker(self):
        image = self.root / "插入图片.png"
        Image.new("RGB", (64, 96), (240, 40, 80)).save(image)
        video = self.root / "插入视频.mp4"
        audio = self.root / "插入声音.wav"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                         "testsrc2=size=160x120:rate=12", "-f", "lavfi", "-i",
                         "sine=frequency=640", "-t", "0.5", "-c:v", "mpeg4",
                         "-c:a", "aac", str(video)], timeout=15)
        Processes().run([self.tools.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                         "sine=frequency=840", "-t", "0.3", str(audio)], timeout=15)
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        window.add_paths([self.source, image, video, audio])
        self.wait_for(lambda: all(job.info and job.picture for job in window.jobs.values()), "素材导入")
        window.inspector.edit_button.click()
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.session.frames and editor.scope is None and not window.page_transition.running,
                      "编辑器就绪")
        base = editor.session.frames.count
        keys = {job.path: key for key, job in window.jobs.items()}
        left = window.preview_files
        strip = editor.filmstrip
        button = Qt.MouseButton.LeftButton
        modifier = Qt.KeyboardModifier.NoModifier
        copy = Qt.DropAction.CopyAction

        def send_drop(path, target, point, *, expected=True, mime=None, boundary=None, after=False):
            point = QPointF(point)
            if mime is None:
                mime = QMimeData()
                mime.setData(QUEUED_MEDIA, keys[path].encode("ascii"))
            enter = QDragEnterEvent(point, copy, mime, button, modifier)
            self.app.sendEvent(target, enter)
            self.assertEqual(enter.isAccepted(), expected)
            if expected:
                move = QDragMoveEvent(point, copy, mime, button, modifier)
                self.app.sendEvent(target, move)
                self.assertTrue(move.isAccepted())
                if boundary is not None:
                    self.assertEqual(strip.drop_position, boundary)
                    self.assertEqual(strip.drop_preview[0], path.name)
                    self.assertFalse(strip.drop_preview[1].isNull())
                    self.assertAlmostEqual(strip.springs.states["width"].target,
                                           strip.viewport().width() / strip._capacity())
                    label = f"插入第 {boundary} 帧后" if after else f"插入第 {boundary + 1} 帧前"
                    self.assertEqual(strip.drop_badge.text(), label)
                    self.assertTrue(strip.drop_badge.isVisible())
                drop = QDropEvent(point, copy, mime, button, modifier)
                self.app.sendEvent(target, drop)
                self.assertTrue(drop.isAccepted())
                if boundary is not None:
                    self.assertIsNone(strip.drop_position)
                    self.assertIsNone(strip.drop_preview)
                    self.assertTrue(strip.drop_badge.isHidden())

        send_drop(audio, strip.viewport(), QPoint(20, 30), expected=False)
        item = window.preview_items[keys[image]]
        start = left.visualItemRect(item).center()
        move = QPoint(left.viewport().width() + 12, start.y())
        with patch("frameflow.queue_view.QDrag") as drag:
            def execute(_action):
                self.assertIs(window.pages.currentWidget(), editor)
                mime = drag.return_value.setMimeData.call_args.args[0]
                pixmap = drag.return_value.setPixmap.call_args.args[0]
                self.assertEqual(pixmap.deviceIndependentSize().toSize().toTuple(), (96, 32))
                self.assertLess(pixmap.toImage().pixelColor(pixmap.width() // 2, 4).alpha(), 255)
                self.assertEqual(drag.return_value.setHotSpot.call_args.args[0], QPoint(-14, -16))
                send_drop(image, strip.viewport(), QPoint(strip._tile_rect(1).left() + 12, 30),
                          mime=mime, boundary=1)
                return copy

            drag.return_value.exec.side_effect = execute
            QTest.mousePress(left.viewport(), button, pos=start)
            motion = QMouseEvent(QEvent.Type.MouseMove, QPointF(move),
                                 QPointF(left.viewport().mapToGlobal(move)),
                                 Qt.MouseButton.NoButton, button, modifier)
            self.app.sendEvent(left.viewport(), motion)
            QTest.mouseRelease(left.viewport(), button, pos=move)
            drag.return_value.exec.assert_called_once_with(copy)
        self.wait_for(lambda: editor.scope is None and length(editor.session.video) == base + 1, "图片插入一帧")
        self.assertIs(window.pages.currentWidget(), editor)
        picture_ref = editor.session.frame_ref(2)
        self.wait_for(lambda: editor.frame_preview.shown_frame == picture_ref and picture_ref in strip.cache, "插入帧预览与缩略图")
        outputs = []
        editor.saved.connect(outputs.append)
        editor.save_image()
        self.wait_for(lambda: len(outputs) == 1, "保存插入的图片帧")
        with Image.open(outputs[0]) as result:
            self.assertEqual(result.size, (64, 96))
            self.assertEqual(result.getpixel((10, 10)), (240, 40, 80))

        def drop_video():
            send_drop(video, strip.viewport(), QPoint(strip._tile_rect(2).right() - 8, 30), boundary=3, after=True)

        video_before, audio_before = editor.session.video, editor.session.audio
        prepared = editor._load_dropped_video(video, window.jobs[keys[video]].info, Processes())
        started = threading.Event()

        def finish_after_cancel(_path, _info, scope):
            started.set()
            scope.cancelled.wait(3)
            return prepared

        with patch.object(editor, "_load_dropped_video", side_effect=finish_after_cancel):
            drop_video()
            self.wait_for(started.is_set, "等待拖入视频准备")
            left.setFocus()
            QTest.keyClick(left, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            self.wait_for(lambda: editor.scope is None, "撤销尚未提交的视频插入")
        self.assertIs(editor.session.video, video_before)
        self.assertIs(editor.session.audio, audio_before)
        self.assertNotIn(video, editor.session.video_sources)
        self.assertEqual(editor.status.text(), "已取消素材插入。")

        drop_video()
        self.wait_for(lambda: editor.scope is None and video in editor.session.video_sources, "外部视频帧插入")
        self.assertEqual(editor.session.frame_ref(4), (video, 0))
        total = base + 1 + editor.session.video_sources[video].frames.count
        self.assertEqual(length(editor.session.video), total)
        left.setFocus()
        QTest.keyClick(left, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertIs(editor.session.video, video_before)
        self.assertIs(editor.session.audio, audio_before)
        drop_video()
        self.assertEqual(length(editor.session.video), total)
        self.assertFalse(any(clip.path == video for clip in editor.session.audio_track()))
        with patch("winsound.PlaySound") if sys.platform == "win32" else nullcontext():
            editor.transport._play_video_ranges(((1, 6),))
            self.wait_for(lambda: editor.transport.playback is None, "混合来源帧连续播放")
            self.assertEqual(editor.status.text(), "播放结束。")

        editor.tabs.setCurrentWidget(editor.audio_page)
        wave = editor.audio_workspace.audio_view
        self.wait_for(lambda: editor.scope is None and wave.waveform, "插入前波形")
        bounds = wave._bounds()
        release = QPoint(round(bounds.left() + bounds.width() * .87), round(bounds.center().y()))
        send_drop(audio, wave, release, expected=False)
        QTest.mouseClick(wave, button, pos=QPoint(round(bounds.left() + bounds.width() * .23), release.y()))
        marker = wave.insertion
        self.assertGreater(marker, 0)
        send_drop(image, wave, release, expected=False)
        send_drop(audio, wave, release)
        idx = next(i for i, clip in enumerate(editor.session.audio) if clip.path == audio)
        self.assertAlmostEqual(length(editor.session.audio[:idx]), marker, places=6)
        self.assertEqual(length(editor.session.video), total)
        self.wait_for(lambda: editor.scope is None and wave.waveform, "插入后波形")
        send_drop(video, wave, release)
        idx = next(i for i, clip in enumerate(editor.session.audio) if clip.path == video)
        self.assertAlmostEqual(length(editor.session.audio[:idx]), marker, places=6)
        self.assertEqual(length(editor.session.video), total)
        editor.undo()
        self.assertFalse(any(clip.path == video for clip in editor.session.audio))

        editor.tabs.setCurrentWidget(editor.video_page)
        self.wait_for(lambda: editor.scope is None, "视频导出就绪")
        editor.save_edit()
        self.wait_for(lambda: editor.scope is None, "混合来源视频导出")
        self.assertEqual(len(outputs), 2, editor.status.text())
        result = probe(outputs[-1], self.tools, Processes())
        self.assertEqual((result.width, result.height), (self.info.width, self.info.height))
        self.assertEqual(index_frames(outputs[-1], result, self.tools, Processes()).count, total)
        self.assertIsNotNone(result.audio_stream)
        self.decode(outputs[-1])
