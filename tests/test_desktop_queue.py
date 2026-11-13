"""Exercise media import, queue operations and desktop batch export."""

import os
import sys
import threading
import wave
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QScrollArea
from frameflow.media import AudioInfo, ImageInfo, Processes
from frameflow.media import probe
from frameflow.window import BatchFormatMenu, MainWindow
from frameflow.editing import index_frames, length
from frameflow.motion import QUEUED_MEDIA

from workflow_support import WorkflowFixture


class DesktopQueueTest(WorkflowFixture):
    def test_desktop_image_outputs(self):
        source = self.root / "透明 图片.png"
        Image.new("RGBA", (64, 48), (255, 0, 0, 0)).save(source)
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        window.add_paths([source])
        job = next(iter(window.jobs.values()))
        self.wait_for(lambda: job.picture is not None, "图片缩略图")
        self.assertIsInstance(job.info, ImageInfo)
        format_picker = window.rows[job.key].format
        self.assertTrue(format_picker.isVisible())
        for target, codec in (("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP"),
                              ("avif", "AVIF"), ("tiff", "TIFF"), ("bmp", "BMP"),
                              ("gif_image", "GIF"), ("ico", "ICO"), ("pdf", "PDF")):
            with self.subTest(target=target):
                format_picker.setCurrentIndex(format_picker.findData(target))
                window.start_button.click()
                self.wait_for(lambda: window.scope is None, f"保存 {target}")
                self.assertEqual(job.state, "已完成", job.detail)
                self.assertNotEqual(job.output, source)
                self.assertEqual(job.output_info.codec, codec)
                if codec == "PDF":
                    self.assertTrue(job.output.read_bytes().startswith(b"%PDF-"))
                else:
                    with Image.open(job.output) as result:
                        result.load()
                        self.assertEqual(result.format, codec)
                        self.assertEqual(result.size, (256, 256) if codec == "ICO" else (64, 48))
                        if codec == "PNG":
                            self.assertEqual(result.getpixel((0, 0))[3], 0)
                        if codec == "JPEG":
                            self.assertEqual(result.getpixel((0, 0)), (255, 255, 255))
        format_picker.setCurrentIndex(format_picker.findData("bmp"))
        window.target_size.setValue(1)
        window.start_button.click()
        self.wait_for(lambda: window.scope is None, "图片体积限制")
        self.assertLessEqual(job.output.stat().st_size, 1_000)
        self.assertIn("已达到", job.output_note)
        window.target_size.setValue(0)
        window.resolution.setCurrentIndex(window.resolution.findData(256))
        format_picker.setCurrentIndex(format_picker.findData("png"))
        window.start_button.click()
        self.wait_for(lambda: window.scope is None, "按指定最长边放大图片")
        self.assertEqual((job.output_info.width, job.output_info.height), (256, 192))

    def test_desktop_import_preview_and_batch_conversion(self):
        second = self.root / "样片 02.mov"
        second.write_bytes(self.source.read_bytes())
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        window.add_paths([self.source, second, self.source])
        self.assertEqual(len(window.jobs), 2)
        self.wait_for(lambda: all(job.info and job.picture for job in window.jobs.values()), "桌面缩略图")
        self.assertFalse(window.preview.pixmap().isNull())
        self.assertIn("320×180", window.video_info.text())
        settings = window.mounted_editor
        # 转换页复用的只是参数区：保存目录保留（就是转换页的目的地），
        # 「保存编辑结果」由主窗口自己的底栏承担。
        self.assertTrue(settings.settings.directory_group.isVisible())
        self.assertTrue(window.output_name.full_text.endswith(".mp4"))
        self.assertTrue(settings.settings.acceleration.isVisible())
        self.assertEqual(settings.settings.acceleration.itemData(0), "cpu")
        self.assertEqual(settings.settings.directory_path.path, str(self.root))
        output_directory = self.root / ("保存位置" * 13) / "输出"
        with patch("frameflow.window.QFileDialog.getExistingDirectory",
                   return_value=str(output_directory)) as choose:
            window._choose_directory()
        self.assertEqual(choose.call_args.args[2], str(self.root))
        self.assertEqual(window.destination.toolTip(), str(output_directory))
        first, following = (job for job in window.jobs.values() if job.queued)
        batch_index = window.batch_format.findData("mkv")
        window.batch_format.setCurrentIndex(batch_index)
        window.batch_format.activated.emit(batch_index)
        self.assertEqual((first.output_format, following.output_format), ("mkv", "mkv"))
        self.assertEqual(window.batch_format.currentIndex(), 0)
        following_format = window.rows[following.key].format
        following_format.setCurrentIndex(following_format.findData("mp4"))
        with patch("frameflow.window.QInputDialog.getInt", return_value=(160, True)):
            custom = window.resolution.findData(-1)
            window.resolution.setCurrentIndex(custom)
            window.resolution.activated.emit(custom)
        window.target_size.setValue(45)
        window.output_fps.setCurrentIndex(window.output_fps.findData(30))
        window._apply_batch_settings()
        window.start_button.click()
        self.wait_for(lambda: window.scope is None, "批量转换完成")
        self.assertEqual(window.progress.value(), 100)
        self.assertEqual(sum(not job.queued for job in window.jobs.values()), 2)
        self.assertEqual((first.output.suffix, following.output.suffix), (".mkv", ".mp4"))
        for job in (first, following):
            self.assertEqual(job.state, "已完成", job.detail)
            self.assertEqual(job.output.parent, output_directory)
            self.assertEqual((job.output_info.width, job.output_info.height), (160, 90))
            self.assertAlmostEqual(job.output_info.fps, 30, delta=.1)
            self.assertLessEqual(job.output.stat().st_size, 45_000)
            self.assertIn("已达到", job.output_note)
            self.decode(job.output)
        outputs = [first.output, following.output]
        window.clear_button.click()
        self.assertEqual(window.list.count(), 0)
        self.assertEqual(window.preview_files.count(), 0)
        self.assertFalse(window.jobs)
        self.assertTrue(all(path.is_file() for path in (*outputs, self.source, second)))

    def test_batch_format_menu_exports_only_compatible_rows(self):
        second = self.root / "第二条.mov"
        second.write_bytes(self.source.read_bytes())
        audio = self.root / "音频.wav"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.source),
                         "-vn", "-c:a", "pcm_s16le", str(audio)], timeout=20)
        image = self.root / "图片.png"
        Image.new("RGB", (40, 30), "blue").save(image)
        window = self.open_export_window(self.source, second, audio, image)
        jobs = list(window.jobs.values())
        previous = [job.output_format for job in jobs]
        self.assertEqual(previous[:2], ["mp4", "mp4"])
        QTest.mouseClick(window.batch_button, Qt.MouseButton.LeftButton)
        menu = window.findChild(BatchFormatMenu)
        self.assertIsNotNone(menu)
        self.assertTrue(menu.isVisible())
        choice = next(button for button in menu.findChildren(QPushButton)
                      if button.property("formatKey") == "mkv")
        menu.findChild(QScrollArea).ensureWidgetVisible(choice)
        QTest.mouseClick(choice, Qt.MouseButton.LeftButton)
        expected = ["mkv", "mkv", *previous[2:]]
        self.assertEqual([job.output_format for job in jobs], expected)
        self.assertEqual([window.rows[job.key].format.currentData() for job in jobs], expected)

        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(window.scope)
        self.wait_for(lambda: window.scope is None, "混合队列转换完成")
        for job, extension in zip(jobs, expected):
            self.assertEqual(job.state, "已完成", job.detail)
            self.assertEqual(job.output.suffix, f".{extension}")
            self.decode(job.output)
        for job in jobs[:2]:
            result = probe(job.output, self.tools, Processes())
            self.assertIn("matroska", result.container.split(","))
            self.assertEqual(result.codec, "H264")

    def test_conversion_uses_custom_filename_and_rejects_invalid_name(self):
        original = self.source.read_bytes()
        window = self.open_export_window(self.source)
        job = window._selected()
        editor = window.mounted_editor
        destination = self.root / "转换输出"
        destination.mkdir()
        with patch("frameflow.editor_settings.QFileDialog.getExistingDirectory", return_value=str(destination)):
            editor.settings.choose_directory()

        self.enter_output_name(editor, "坏/名称")
        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(window.scope)
        self.wait_for(lambda: window.scope is None, "非法名称校验完成")
        self.assertIn("文件名不能包含", job.detail)
        self.assertIsNone(job.output)
        self.assertEqual(list(destination.iterdir()), [])

        name = "转换 Export (校对)"
        occupied = destination / f"{name}.mp4"
        occupied.write_bytes(b"existing")
        self.enter_output_name(editor, name)
        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(window.scope)
        self.wait_for(lambda: window.scope is None, "自定义名称转换完成")
        expected = destination / f"{name} (1).mp4"
        self.assertEqual(job.output, expected, job.detail)
        self.assertEqual(probe(expected, self.tools, Processes()).codec, "H264")
        self.decode(expected)
        self.assertEqual(occupied.read_bytes(), b"existing")
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(set(destination.iterdir()), {occupied, expected})

    def test_output_settings_are_shared_per_file(self):
        second = self.root / "第二个文件.mov"
        second.write_bytes(self.source.read_bytes())
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        window.add_paths([self.source, second])
        self.wait_for(lambda: all(job.info for job in window.jobs.values()), "文件读取完成")
        first = window._selected()
        shared = window.mounted_editor
        self.assertFalse(shared.media_started)
        shared.settings.container.setCurrentIndex(shared.settings.container.findData("MKV"))
        shared.settings.codec.setCurrentIndex(shared.settings.codec.findData("mkv_hevc"))
        shared.settings.export_edge.setCurrentIndex(shared.settings.export_edge.findData(1280))
        shared.settings.output_fps.setCurrentIndex(shared.settings.output_fps.findData(30))
        self.assertEqual((first.export_settings.format, first.export_settings.max_edge,
                          first.export_settings.output_fps), ("mkv_hevc", 1280, 30))
        window.navigation.setCurrentIndex(1)
        self.assertIs(window.pages.currentWidget(), shared)
        self.wait_for(lambda: not window.page_transition.running, "预览转场结束")
        self.assertIs(shared.settings_scroll.widget(), shared.settings)
        self.wait_for(lambda: shared.session.frames and shared.scope is None, "视频读取结束")
        shared.settings.quality.setValue(27)
        directory = self.root / "仅第一个文件"
        with patch("frameflow.editor_settings.QFileDialog.getExistingDirectory", return_value=str(directory)):
            shared.settings.choose_directory()
        window.navigation.setCurrentIndex(0)
        self.wait_for(lambda: not window.page_transition.running, "返回转换页")
        self.assertIs(window.mounted_editor, shared)
        self.assertEqual(shared.settings.quality.value(), 27)
        self.assertEqual(window._job_export(first).quality, 27)
        self.assertEqual(window._job_export(first).directory, directory)
        window.list.setCurrentRow(1)
        self.assertEqual(window.mounted_editor.settings.format.currentData(), "mp4")
        self.assertEqual(window.mounted_editor.settings.export_edge.currentData(), 0)
        self.assertEqual(window.mounted_editor.settings.directory, second.parent)
        window.list.setCurrentRow(0)
        self.assertIs(window.mounted_editor, shared)
        self.assertEqual(shared.settings.codec.currentData(), "mkv_hevc")

    def test_video_resolution_label_and_export_are_shared_across_workspaces(self):
        source = self.root / "分辨率样片.mp4"
        Processes().run([
            self.tools.ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "testsrc2=size=1280x720:rate=24", "-t", "0.5", "-c:v", "libx264",
            "-preset", "ultrafast", str(source),
        ], timeout=20)
        window = self.open_export_window(source)
        job = window._selected()
        editor = window.mounted_editor

        def export_size():
            QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
            self.assertIsNotNone(window.scope)
            self.wait_for(lambda: window.scope is None, "分辨率成品已导出")
            self.assertEqual(job.state, "已完成", job.detail)
            info = probe(job.output, self.tools, Processes())
            return info.width, info.height

        self.assertEqual(editor.settings.export_edge.currentText(), "原始分辨率")
        self.assertEqual(editor.output_options().max_edge, 0)
        self.assertEqual(export_size(), (1280, 720))

        window.navigation.setCurrentIndex(1)
        self.wait_for(lambda: not window.page_transition.running, "进入预览工作区")
        self.assertEqual(editor.settings.export_edge.currentText(), "原始分辨率")
        editor.settings.export_edge.setCurrentIndex(editor.settings.export_edge.findData(854))
        window.navigation.setCurrentIndex(0)
        self.wait_for(lambda: not window.page_transition.running, "返回转换工作区")
        self.assertEqual(editor.settings.export_edge.currentText(), "标准 · 480P")
        self.assertEqual(window._job_export(job).max_edge, 854)
        self.assertEqual(export_size(), (854, 480))

        window.navigation.setCurrentIndex(2)
        self.wait_for(lambda: not window.page_transition.running, "进入 Agent 工作区")
        agent = window.agent_tools
        self.assertEqual(agent.set_resolution(job.key, preset="original")["long_edge"], 0)
        agent.prepare_conversion([job.key])
        plan = agent.pending
        self.assertTrue(any("原始尺寸" in label.text() for label in plan.card.findChildren(QLabel)))
        plan.card.cancel.click()
        self.assertEqual(agent.set_resolution(job.key, long_edge=640)["long_edge"], 640)
        agent.prepare_conversion([job.key])
        agent.pending.card.confirm.click()
        self.assertIsNotNone(agent.active)
        self.wait_for(lambda: agent.active is None, "Agent 分辨率成品已导出")
        self.assertEqual(job.state, "已完成", job.detail)
        info = probe(job.output, self.tools, Processes())
        self.assertEqual((info.width, info.height), (640, 360))

    def test_queue_drag_retains_jobs_and_only_commits_on_drop(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(1200, 780)
        window.show()
        paths = [self.source]
        for i in range(5):
            path = self.root / f"图片 {i}.png"
            Image.new("RGB", (32, 32), (i * 30, 60, 80)).save(path)
            paths.append(path)
        window.add_paths(paths)
        self.wait_for(lambda: len(window.jobs) == 6 and all(job.picture for job in window.jobs.values()), "队列")
        original = dict(window.jobs)
        keys = list(original)
        key = keys[0]
        view = window.list
        item = window.items[key]
        row = view.itemWidget(item)
        view.setCurrentItem(item)
        rect = view.visualItemRect(item)
        motion = view.spring_drag
        motion.start(item, rect.center() - rect.topLeft(), rect.center())
        motion.track(view.visualItemRect(view.item(3)).center())
        self.assertEqual(motion.slot, 3)
        spring = motion.clock.states[1]
        initial = spring.position
        self.assertNotEqual(spring.target, initial)
        spring.advance(.012)
        self.assertLess(spring.target, spring.position)
        self.assertLess(spring.position, initial)
        self.assertEqual(list(window.jobs), keys)
        motion.track(None)
        motion.land(0, rect.center())
        self.wait_for(lambda: not motion.isVisible(), "取消后落回")
        self.assertEqual(list(window.jobs), keys)

        self.assertTrue(window._reorder_media(key, 5))
        expected = keys[1:] + [key]
        self.assertEqual(list(window.jobs), expected)
        for queue in (window.list, window.preview_files):
            self.assertEqual([queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(6)], expected)
            self.assertEqual(queue.currentItem().data(Qt.ItemDataRole.UserRole), key)
        self.assertIs(view.itemWidget(item), row)
        self.assertTrue(all(window.jobs[key] is job for key, job in original.items()))
        with patch.object(window, "_start_batch") as start:
            window.start_export()
            self.assertEqual([task[0] for task in start.call_args.args[0]], expected)

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
        window.edit_button.click()
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
        wave = editor.audio_view
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

    def test_missing_queued_media_shows_notice_and_removes_stale_rows(self):
        audio_dir = self.root / "原音频位置"
        audio_dir.mkdir()
        source = audio_dir / "队列音频.mp3"
        moved = self.root / "移动后的音频.mp3"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.source),
                         "-map", "0:a:0", str(source)], timeout=15)
        video_dir = self.root / "原视频位置"
        video_dir.mkdir()
        video = self.source.rename(video_dir / self.source.name)
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        window.add_paths([source, video])
        self.wait_for(lambda: all(job.info and job.picture for job in window.jobs.values()), "队列素材就绪")
        window.edit_button.click()
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.scope is None and editor.audio_view.waveform, "预览音频波形")
        source.rename(moved)
        editor.preview_audio()
        self.wait_for(lambda: len(window.jobs) == 1, "自动移除已移动的文件")
        notice = window.findChild(QMessageBox)
        self.assertTrue(notice.isVisible())
        self.assertIn(source.name, notice.text())
        self.assertEqual(window.list.count(), 1)
        self.assertEqual(window.preview_files.count(), 1)
        self.assertEqual(window.pages.currentWidget().path, video)
        self.assertTrue(editor.closing)
        self.assertTrue(moved.is_file())
        locate = next(button for button in notice.buttons() if button.text() == "打开原位置")
        with patch("frameflow.window.QDesktopServices.openUrl", return_value=True) as launch:
            locate.click()
            launch.assert_called_once_with(QUrl.fromLocalFile(str(audio_dir)))
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        video_editor = window.pages.currentWidget()
        window.navigation.setCurrentIndex(0)
        self.wait_for(lambda: not window.page_transition.running and
                      video_editor.scope is None and video_editor.frame_preview.scope is None
                      and video_editor.filmstrip.scope is None, "视频预览已释放文件")
        moved_video = self.root / "移动后的视频.mov"
        video.rename(moved_video)
        video_dir.rmdir()
        window.start_button.click()
        self.assertEqual(window.list.count(), 0)
        self.assertEqual(window.preview_files.count(), 0)
        self.assertIsNone(window.scope)
        notice = window.findChild(QMessageBox)
        self.assertTrue(notice.isVisible())
        locate = next(button for button in notice.buttons() if button.text() == "打开原位置")
        with patch("frameflow.window.QDesktopServices.openUrl", return_value=True) as launch:
            locate.click()
            launch.assert_called_once_with(QUrl.fromLocalFile(str(self.root)))
        self.assertIn("上级目录", window.status.text())
        self.assertTrue(moved_video.is_file())

    def test_desktop_audio_formats_and_mixed_queue(self):
        source = self.root / "单声道 音频.wav"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                         "sine=frequency=440:sample_rate=44100", "-t", "1.5", str(source)], timeout=10)
        occupied = self.root / "单声道 音频.mp3"
        occupied.write_bytes(b"existing")
        original = source.read_bytes()
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        with patch("frameflow.window.QFileDialog.getOpenFileNames",
                   return_value=([str(source), str(self.source)], "")) as dialog:
            QTest.mouseClick(window.drop_area, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
            self.assertIn("*.wav", dialog.call_args.args[3])
            self.assertIn("*.mov", dialog.call_args.args[3])
        self.assertFalse(window.start_button.isEnabled())
        self.wait_for(lambda: all(job.info and job.picture for job in window.jobs.values()), "媒体和波形")
        audio, video = window.jobs.values()
        self.assertIsInstance(audio.info, AudioInfo)
        self.assertEqual((audio.info.sample_rate, audio.info.channels), (44100, 1))
        self.assertIn("44.1 kHz", window.video_info.text())
        self.assertIn("单声道", window.video_info.text())
        self.assertFalse(window.preview.pixmap().isNull())
        video_format = window.rows[video.key].format
        audio_format = window.rows[audio.key].format
        self.assertTrue(video_format.isVisible() and audio_format.isVisible())
        batch_index = window.batch_format.findData("mkv")
        window.batch_format.setCurrentIndex(batch_index)
        window.batch_format.activated.emit(batch_index)
        self.assertEqual((audio.output_format, video.output_format), ("mp3", "mkv"))
        video_format.setCurrentIndex(video_format.findData("mp4"))

        window.start_button.click()
        self.wait_for(lambda: window.scope is None, "混合队列转换")
        self.assertEqual(video.output.suffix, ".mp4")
        self.assertEqual(audio.output.suffix, ".mp3")
        self.assertNotEqual(audio.output, occupied)
        self.assertEqual(audio.output_info.codec, "MP3")
        self.decode(video.output)
        self.decode(audio.output)
        with patch("frameflow.window.QDesktopServices.openUrl", return_value=True) as launch:
            window._open_output()
            self.assertEqual(Path(launch.call_args.args[0].toLocalFile()), audio.output)
        if os.name == "nt":
            with patch("frameflow.window.QProcess") as explorer:
                explorer.return_value.startDetached.return_value = True
                window.locate_button.click()
                explorer.return_value.setNativeArguments.assert_called_once_with(
                    f'/select,"{audio.output.absolute()}"')

        video_row = window.rows[video.key]
        QTest.mouseClick(video_row.thumb, Qt.MouseButton.LeftButton)
        self.assertIs(window._selected(), video)
        QTest.mouseClick(video_row.remove_button, Qt.MouseButton.LeftButton)
        self.assertTrue(window.resolution.isHidden())
        target = self.root / "音频 成品"
        with patch("frameflow.window.QFileDialog.getExistingDirectory", return_value=str(target)):
            window.destination.click()
        for ext, codec in (("m4a", "AAC"), ("wav", "PCM_S16LE"), ("flac", "FLAC"),
                           ("opus", "OPUS"), ("ogg", "VORBIS")):
            with self.subTest(extension=ext):
                audio_format.setCurrentIndex(audio_format.findData(ext))
                self.assertEqual(window.target_size.isEnabled(), ext not in ("wav", "flac"))
                window.start_button.click()
                self.wait_for(lambda: window.scope is None, f"保存 {ext}")
                self.assertEqual(audio.state, "已完成", audio.detail)
                self.assertEqual(audio.output.parent, target)
                self.assertEqual(audio.output.suffix, f".{ext}")
                self.assertEqual(audio.output_info.codec, codec)
                self.assertAlmostEqual(audio.output_info.duration, 1.5, delta=0.12)
                self.decode(audio.output)
        audio_format.setCurrentIndex(audio_format.findData("mp3"))
        window.target_size.setValue(16)
        window.start_button.click()
        self.wait_for(lambda: window.scope is None, "按体积目标转换 MP3")
        self.assertLessEqual(audio.output.stat().st_size, 16_000)
        self.assertIn("已达到", audio.output_note)
        self.decode(audio.output)
        self.assertEqual(occupied.read_bytes(), b"existing")
        self.assertEqual(source.read_bytes(), original)

    def test_drop_area_accepts_files_and_restores_empty_height(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.show()
        area = window.drop_area
        self.assertTrue(area.isVisible())
        self.assertGreater(area.height(), 200)
        self.assertTrue(window.list.isHidden())
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.source))] * 2)
        point = QPointF(area.rect().center())
        action = Qt.DropAction.CopyAction
        button = Qt.MouseButton.LeftButton
        modifier = Qt.KeyboardModifier.NoModifier
        enter = QDragEnterEvent(point, action, mime, button, modifier)
        self.app.sendEvent(area, enter)
        self.assertTrue(enter.isAccepted())
        move = QDragMoveEvent(point, action, mime, button, modifier)
        self.app.sendEvent(area, move)
        self.assertTrue(move.isAccepted())
        drop = QDropEvent(point, action, mime, button, modifier)
        self.app.sendEvent(area, drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(len(window.jobs), 1)
        # 队列里有文件之后，整块投放区收起，把空间让给文件列表
        self.wait_for(lambda: not area.isVisible(), "导入区收起")
        self.assertTrue(window.list.isVisible())
        QTest.mouseClick(next(iter(window.rows.values())).remove_button, button)
        self.assertFalse(window.jobs)
        self.wait_for(area.isVisible, "导入区恢复")

    def test_queue_columns_stay_inside_viewport_after_repeated_window_resize(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        # Qt's offscreen backend can maximize below the application's minimum size.
        # Use a wide client resize there; native runs exercise real window states.
        native_window_states = self.app.platformName() != "offscreen"

        def expand_window():
            if native_window_states:
                window.showMaximized()
                self.wait_for(window.isMaximized, "最大化窗口")
            else:
                window.resize(1510, 930)
                window.show()

        expand_window()

        audio = self.root / "音频.wav"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.source),
                         "-vn", str(audio)], timeout=20)
        media = (self.source.read_bytes(), audio.read_bytes())
        paths = []
        for index in range(18):
            kind = index % 2
            name = f"{index:02} " + "长文件名测试 Long media filename " * 3
            path = self.root / (name + (".mov", ".wav")[kind])
            path.write_bytes(media[kind])
            paths.append(path)
        window.add_paths(paths)
        self.wait_for(lambda: len(window.jobs) == len(paths) and
                      all(job.info is not None for job in window.jobs.values()), "视频与音频导入完成")
        view = window.list
        viewport = view.viewport()
        header = window.column_header
        sizes = ((940, 720), (1177, 900), (1366, 768))

        def check_visible_rows():
            self.app.processEvents()
            bounds = viewport.rect()
            viewport_x = viewport.mapTo(window.queue_card, QPoint()).x()
            header_x = header.mapTo(window.queue_card, QPoint()).x()
            columns = tuple(rect for _, rect in header.column_rects())
            visible_count = 0
            for index in range(view.count()):
                item = view.item(index)
                rect = view.visualItemRect(item)
                if rect.height() <= 0 or rect.top() < bounds.top() or rect.bottom() > bounds.bottom():
                    continue
                visible_count += 1
                row = view.itemWidget(item)
                self.assertEqual(row.geometry(), rect, f"队列第 {index + 1} 行几何未同步")
                self.assertEqual(row.width(), bounds.width())
                self.assertEqual(row.name.width(), row.length_label.x() - row.name.x() - row.layout().spacing())
                for field, column in zip((row.length_label, row.size_label, row.state, row.format), columns):
                    self.assertEqual(field.mapTo(viewport, QPoint()).x() + viewport_x, column.left() + header_x)
                    self.assertEqual(field.width(), column.width())
                for control in (row.format, row.remove_button):
                    self.assertTrue(control.isVisible())
                    self.assertTrue(bounds.contains(control.rect().translated(control.mapTo(viewport, QPoint()))))
                    self.assertTrue(control.visibleRegion().contains(control.rect()))
            self.assertGreater(visible_count, 0)

        check_visible_rows()
        for cycle in range(10):
            width, height = sizes[cycle % len(sizes)]
            with self.subTest(cycle=cycle + 1, normal_size=(width, height)):
                if native_window_states:
                    window.showNormal()
                    self.wait_for(lambda: not window.isMaximized(), "还原窗口")
                window.resize(width, height)
                QTest.qWait(30)
                check_visible_rows()
                expand_window()
                QTest.qWait(30)
                check_visible_rows()
                scroll = view.verticalScrollBar()
                self.assertGreater(scroll.maximum(), 0)
                scroll.setValue(scroll.maximum())
                check_visible_rows()
                scroll.setValue(0)

        # A visible native scrollbar reduces the viewport without narrowing the card.
        hidden_scrollbar_width = viewport.width()
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.wait_for(lambda: viewport.width() < hidden_scrollbar_width, "队列为滚动条留出空间")
        check_visible_rows()

    def test_queue_styles_keep_header_row_height_and_scroll_aligned(self):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(window.minimumSize())
        window.show()
        files = [self.root / f"附件 {index:02}.pdf" for index in range(16)]
        for path in files:
            path.write_bytes(b"%PDF-1.4")
        window.add_paths(files, generated=True)
        self.app.processEvents()
        table, rail = window.list, window.preview_files
        table.setCurrentRow(5)
        table.verticalScrollBar().setValue(95)
        before = table.currentItem().data(Qt.ItemDataRole.UserRole)
        add_position = window.add_button.mapTo(window.page_host, QPoint())
        table_row = table.visualItemRect(table.currentItem())
        table_y = table.viewport().mapTo(window.page_host, table_row.topLeft()).y()
        self.assertTrue(window.batch_button.isVisible())
        self.assertTrue(window.rows[before].size_label.isVisible())
        self.assertEqual(window.count.text(), "16 个文件")
        self.assertLess(window.count.geometry().right(), window.add_button.geometry().left())

        window.navigation.setCurrentIndex(1)
        self.wait_for(lambda: not window.page_transition.running, "窄队列就绪")
        self.assertEqual(window.rail_add.mapTo(window.page_host, QPoint()), add_position)
        self.assertEqual(rail.currentItem().data(Qt.ItemDataRole.UserRole), before)
        self.assertEqual(rail.verticalScrollBar().value(), 95)
        rail_row = rail.visualItemRect(rail.currentItem())
        self.assertEqual(rail_row.height(), table_row.height())
        self.assertEqual(rail.viewport().mapTo(window.page_host, rail_row.topLeft()).y(), table_y)
        self.assertEqual(window.count.text(), window.rail_count.text())
        self.assertLess(window.rail_count.geometry().right(), window.rail_add.geometry().left())
        rail.verticalScrollBar().setValue(130)

        window.navigation.setCurrentIndex(0)
        self.wait_for(lambda: not window.page_transition.running, "宽队列恢复")
        self.assertEqual(table.verticalScrollBar().value(), 130)
        self.assertEqual(table.currentItem().data(Qt.ItemDataRole.UserRole), before)
        self.assertTrue(window.batch_button.isVisible())
