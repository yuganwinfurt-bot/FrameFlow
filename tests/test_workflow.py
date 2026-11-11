"""使用真实 FFmpeg 生成并验证短视频，无需手动准备素材。"""

import gc
import os
import random
import sys
import tempfile
import threading
import time
import unittest
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from PIL import Image
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import (QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent,
                           QInputMethodEvent, QMouseEvent, QWheelEvent)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QScrollArea, QWidget

from frameflow.media import AudioInfo, Cancelled, Export, ImageInfo, MediaError, Processes, Tools
from frameflow.media import (convert, hardware_acceleration_choices, probe, thumbnail,
                             video_encoder_args)
from frameflow.window import BatchFormatMenu, MainWindow
from frameflow.editor import Editor
from frameflow.editing import Clip, audio_preview, index_frames, length, render_edit, slice_track
from frameflow.motion import Combo, QUEUED_MEDIA


class WorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = Tools.find()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        # 在启动后台任务前，于 GUI 线程回收上一用例的窗口。
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
        self.temp = tempfile.TemporaryDirectory(prefix="帧转 测试-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "样片 01.mov"
        Processes().run([
            self.tools.ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "testsrc2=size=320x180:rate=24", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=44100", "-t", "2.4", "-c:v", "mpeg4",
            "-c:a", "aac", str(self.source),
        ], timeout=20)
        self.info = probe(self.source, self.tools, Processes())

    def decode(self, file: Path):
        Processes().run([self.tools.ffmpeg, "-v", "error", "-nostdin", "-i", str(file),
                         "-f", "null", "-"], timeout=20)

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

    def test_probe_thumbnail_and_corrupted_file(self):
        self.assertEqual((self.info.width, self.info.height, self.info.fps), (320, 180, 24))
        self.assertAlmostEqual(self.info.duration, 2.4, delta=0.12)
        self.assertEqual(self.info.codec, "MPEG4")
        self.assertTrue(thumbnail(self.source, self.info, self.tools, Processes())
                        .startswith(b"\x89PNG\r\n\x1a\n"))
        invalid = self.root / "损坏视频.mp4"
        invalid.write_bytes(b"not video")
        with self.assertRaises(MediaError):
            probe(invalid, self.tools, Processes())

    def test_convert_all_formats_and_keep_existing_files(self):
        original = self.source.read_bytes()
        occupied = self.root / "样片 01.mp4"
        occupied.write_bytes(b"existing")
        codecs = {"mp4": "H264", "mkv": "H264", "mkv_hevc": "HEVC", "mov": "H264",
                  "webm": "VP9", "avi": "MPEG4", "gif": "GIF"}
        for target, codec in codecs.items():
            with self.subTest(target=target):
                progress = []
                out = convert(self.source, self.info, Export(target),
                              self.tools, Processes(), progress.append)
                self.assertNotIn(out, {occupied, self.source})
                extension = "mkv" if target == "mkv_hevc" else target
                self.assertEqual(out.suffix, f".{extension}")
                result = probe(out, self.tools, Processes())
                self.assertEqual(result.codec, codec)
                self.assertAlmostEqual(result.duration, self.info.duration, delta=0.2)
                if extension == "gif":
                    self.assertEqual(result.width, 240)
                    self.assertAlmostEqual(result.height, 240 * 180 / 320, delta=1)
                    # GIF 的帧延时以百分之一秒为单位。检查实际帧数而非探测到的标称帧率。
                    frames = Processes().run([
                        self.tools.ffprobe, "-v", "error", "-select_streams", "v:0",
                        "-count_frames", "-show_entries", "stream=nb_read_frames",
                        "-of", "csv=p=0", str(out),
                    ], timeout=20)
                    self.assertEqual(int(frames), round(self.info.duration * 8))
                self.assertTrue(progress)
                self.decode(out)
        self.assertEqual(occupied.read_bytes(), b"existing")
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".frameflow-*")), [])

    def test_hardware_encoding_falls_back_to_cpu_and_preserves_cancel(self):
        actual_run = Processes.run
        attempts = []
        notices = []

        def fail_hardware(scope, args, **kwargs):
            encoder = args[args.index("-c:v") + 1] if "-c:v" in args else ""
            attempts.append(encoder)
            if encoder == "h264_amf":
                raise MediaError("test: hardware device unavailable")
            return actual_run(scope, args, **kwargs)

        with patch.object(Processes, "run", fail_hardware):
            output = convert(self.source, self.info,
                             Export("mp4", self.root, video_acceleration="amf"),
                             self.tools, Processes(), lambda _: None, notice=notices.append)
        self.assertEqual(attempts, ["h264_amf", "libx264"])
        self.assertEqual(len(notices), 1)
        self.assertIn("回退 CPU 软件编码", notices[0])
        self.decode(output)

        attempts.clear()

        def cancel_hardware(_scope, args, **_kwargs):
            attempts.append(args[args.index("-c:v") + 1])
            raise Cancelled("test cancellation")

        with patch.object(Processes, "run", cancel_hardware):
            with self.assertRaises(Cancelled):
                convert(self.source, self.info,
                        Export("mp4", self.root, video_acceleration="amf"),
                        self.tools, Processes(), lambda _: None, notice=notices.append)
        self.assertEqual(attempts, ["h264_amf"])

    def test_extract_audio_from_video(self):
        codecs = {"mp3": "MP3", "m4a": "AAC", "wav": "PCM_S16LE", "flac": "FLAC",
                  "opus": "OPUS", "ogg": "VORBIS"}
        for target, codec in codecs.items():
            with self.subTest(target=target):
                output = convert(self.source, self.info, Export(target),
                                 self.tools, Processes(), lambda _: None)
                info = probe(output, self.tools, Processes())
                self.assertIsInstance(info, AudioInfo)
                self.assertEqual(info.codec, codec)
                self.decode(output)
        silent = self.root / "无声.mp4"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.source),
                         "-an", "-c:v", "copy", str(silent)], timeout=10)
        with self.assertRaisesRegex(MediaError, "没有音轨"):
            convert(silent, probe(silent, self.tools, Processes()), Export("mp3"),
                    self.tools, Processes(), lambda _: None)
        self.assertFalse(silent.with_suffix(".mp3").exists())

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

    def test_cancel_reaps_child_and_leaves_no_output(self):
        scope = Processes()
        run = scope.run
        started = threading.Event()

        def slow_run(args, *, on_seconds=None, **kwargs):
            args = list(args)
            args[args.index("-i"):args.index("-i")] = ["-re"]

            def progress(seconds):
                started.set()
                on_seconds(seconds)

            return run(args, on_seconds=progress, **kwargs)

        with (ThreadPoolExecutor(max_workers=1) as pool,
              patch.object(scope, "run", side_effect=slow_run)):
            result = pool.submit(convert, self.source, self.info, Export("mp4"),
                                 self.tools, scope, lambda _: None)
            try:
                self.assertTrue(started.wait(5), "FFmpeg 没有报告进度")
            finally:
                scope.cancel()
            with self.assertRaises(Cancelled):
                result.result(timeout=5)
        self.assertEqual(list(self.root.glob("*.mp4")), [])
        self.assertEqual(list(self.root.glob(".frameflow-*")), [])
        self.assertFalse(scope._active)

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
        self.assertTrue(settings.directory_group.isVisible())
        self.assertTrue(window.output_name.full_text.endswith(".mp4"))
        self.assertTrue(settings.acceleration.isVisible())
        self.assertEqual(settings.acceleration.itemData(0), "cpu")
        self.assertEqual(settings.directory_path.path, str(self.root))
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

    def open_export_window(self, *paths):
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(1260, 760)
        window.show()
        window.add_paths(paths)
        self.wait_for(lambda: all(job.info for job in window.jobs.values()), "导入真实素材")
        return window

    def enter_output_name(self, editor, name):
        field = editor.output_stem_edit
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
        with patch("frameflow.editor.QFileDialog.getExistingDirectory", return_value=str(destination)):
            editor.choose_directory()

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

    def test_media_preview_uses_custom_filename_after_switching_pages(self):
        window = self.open_export_window(self.source)
        job = window._selected()
        editor = window.mounted_editor
        names = []
        editor.saved.connect(names.append)
        QTest.mouseClick(window.navigation, Qt.MouseButton.LeftButton,
                         pos=window.navigation.tabRect(1).center())
        self.wait_for(lambda: not window.page_transition.running, "进入媒体预览")
        self.enter_output_name(editor, "剪辑 Preview (最终)")
        for index in (0, 1):
            QTest.mouseClick(window.navigation, Qt.MouseButton.LeftButton,
                             pos=window.navigation.tabRect(index).center())
            QTest.qWait(25)
        self.wait_for(lambda: not window.page_transition.running and editor.frames and editor.scope is None,
                      "预览转场和视频读取完成")
        self.assertIs(window._selected(), job)
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(editor.output_stem_edit.text(), "剪辑 Preview (最终)")
        destination = self.root / "编辑输出"
        destination.mkdir()
        with patch("frameflow.editor.QFileDialog.getExistingDirectory", return_value=str(destination)):
            editor.choose_directory()
        QTest.mouseClick(window.preview_start, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(editor.scope)
        self.wait_for(lambda: editor.scope is None, "媒体预览导出完成")
        expected = destination / "剪辑 Preview (最终).mp4"
        self.assertEqual(names, [expected], editor.notice.text())
        self.assertEqual(probe(expected, self.tools, Processes()).codec, "H264")
        self.decode(expected)

        self.enter_output_name(editor, "CON")
        QTest.mouseClick(window.preview_start, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(editor.scope)
        self.wait_for(lambda: editor.scope is None, "媒体预览非法名称校验")
        self.assertIn("Windows 保留名称", editor.notice.text())
        self.assertEqual(list(destination.iterdir()), [expected])

    def test_media_preview_image_export_uses_custom_name_and_protects_source(self):
        image = self.root / "截图 Image (原始).png"
        Image.new("RGB", (40, 30), "blue").save(image)
        original = image.read_bytes()
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(1260, 760)
        window.show()
        QTest.mouseClick(window.navigation, Qt.MouseButton.LeftButton,
                         pos=window.navigation.tabRect(1).center())
        window.add_paths([image])
        self.wait_for(lambda: isinstance(window.pages.currentWidget(), Editor)
                      and not window.page_transition.running, "在预览页导入图片")
        editor = window.pages.currentWidget()
        self.enter_output_name(editor, image.stem)
        self.assertEqual(window._selected().output_stem, image.stem)
        names = []
        editor.saved.connect(names.append)
        QTest.mouseClick(window.preview_start, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(editor.scope)
        self.wait_for(lambda: editor.scope is None, "预览图片导出完成")
        expected = image.with_stem(f"{image.stem} (1)")
        self.assertEqual(names, [expected], editor.notice.text())
        self.assertEqual(image.read_bytes(), original)
        codec = Processes().run([self.tools.ffprobe, "-v", "error", "-select_streams", "v:0",
                                 "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(expected)],
                                timeout=20)
        self.assertEqual(codec.strip(), b"png")

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
        self.wait_for(lambda: editor.frames and editor.frame_view.original and editor.scope is None, "实际帧数和预览")
        editor.fit_settings_density(True)
        self.assertTrue(editor.mode_hint.isVisible())
        self.assertIn("手动设置编码", editor.mode_hint.text())
        self.assertTrue(editor.quality.note.isVisible())
        self.assertTrue(editor.quality.ends.isVisible())
        self.assertEqual(editor.audio_codec_row.field_label.text(), "音频编码")
        self.assertEqual(editor.audio_row.field_label.text(), "音频码率")
        self.assertTrue(editor.audio_codec_row.isVisible())
        self.assertTrue(editor.audio_row.isVisible())
        self.assertIs(editor.limit_toggle.parentWidget(), editor.estimate_head)
        self.assertIn("目标大小", editor.limit_toggle.toolTip())
        # 目标体积输入行已从面板移除，开关只保留提示
        self.assertFalse(editor.limit_row.isVisible())
        editor.limit_toggle.click()
        self.assertFalse(editor.limit_row.isVisible())
        editor.limit_toggle.click()
        self.assertFalse(editor.limit_row.isVisible())
        self.assertEqual(editor.estimate_rows["output"].objectName(), "estimateValueStrong")
        total = editor.frames.count
        self.assertEqual(total, 58)
        self.assertEqual([editor.tabs.tabText(i) for i in range(editor.tabs.count())],
                         ["视频帧", "音轨", "图片", "文件详情"])
        editor.video_first.setValue(2)
        editor.video_last.setValue(4)
        editor.cut_video()
        self.assertEqual(int(sum(c.length for c in editor.video)), total - 3)
        editor.paste_video_after()
        self.assertEqual(int(sum(c.length for c in editor.video)), total)
        editor.cursor.setValue(3)
        self.wait_for(lambda: editor.shown_frame == (self.source, 2) and editor.scope is None, "重新定位帧")
        editor.image_format.setCurrentIndex(editor.image_format.findData("jpg"))
        editor.picture_preset.setCurrentIndex(2)
        editor.size.setValue(15)
        editor.save_image()
        self.wait_for(lambda: editor.scope is None and len(window.jobs) == 2, "单帧成品进入队列")
        picture = next(job.path for job in window.jobs.values() if not job.queued)
        with Image.open(picture) as saved:
            self.assertEqual(saved.format, "JPEG")
            self.assertLessEqual(picture.stat().st_size, 15_000)
        editor.video_first.setValue(1)
        editor.video_last.setValue(1)
        editor.delete_video()
        self.wait_for(lambda: editor.scope is None and editor.shown_frame == editor.requested_frame, "剪切后预览")
        editor.format.setCurrentIndex(editor.format.findData("mkv"))
        editor.save_edit()
        self.wait_for(lambda: editor.scope is None and len(window.jobs) == 3, "编辑视频成品进入队列")
        output = next(job.path for job in window.jobs.values() if job.path.suffix == ".mkv")
        info = probe(output, self.tools, Processes())
        self.assertEqual(index_frames(output, info, self.tools, Processes()).count, total - 1)
        self.assertIsNotNone(info.audio_stream)
        self.decode(output)
        self.assertEqual(self.source.read_bytes(), original)

    def test_video_image_tab_previews_and_saves_first_selected_frame(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        outputs = []
        editor.saved.connect(outputs.append)
        editor.show()
        self.wait_for(lambda: editor.frames and editor.scope is None, "视频可选帧")
        editor.image_format.setCurrentIndex(editor.image_format.findData("png"))
        self.assertEqual(editor.picture_quality_label.text(), "PNG 压缩级别")
        self.assertIn("不会降低画质", editor.picture_preset_hint.text())
        editor.image_format.setCurrentIndex(editor.image_format.findData("jpg"))
        self.assertEqual(editor.picture_quality_label.text(), "画质")

        def show_picture(number):
            tab = editor.tabs.indexOf(editor.image_page)
            bar = editor.tabs.tabBar()
            QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(tab).center())
            self.assertIs(editor.tabs.currentWidget(), editor.image_page)
            self.assertTrue(editor.timeline.isHidden())
            self.assertTrue(editor.export_group.isHidden())
            self.wait_for(lambda: editor.shown_frame == (self.source, number - 1)
                          and editor.image_view.original is not None, "图片页首帧预览")
            pass

        editor.cursor.setValue(12)
        show_picture(1)
        editor.tabs.setCurrentWidget(editor.video_page)
        self.assertEqual(editor.cursor.value(), 12)
        pass
        editor._click_frame(20, Qt.KeyboardModifier.NoModifier)
        editor._click_frame(25, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(editor.video_selection, ((20, 25),))
        show_picture(20)
        editor.tabs.setCurrentWidget(editor.video_page)
        editor._click_frame(7, Qt.KeyboardModifier.ControlModifier)
        editor._click_frame(32, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.video_selection, ((7, 7), (20, 25), (32, 32)))
        show_picture(7)
        editor.resolution.setCurrentIndex(editor.resolution.findData(256))
        editor.save_image()
        self.wait_for(lambda: outputs and editor.scope is None, "保存最前面选区的首帧")
        self.assertTrue(outputs[0].stem.endswith("_帧000007"))
        with Image.open(outputs[0]) as picture:
            self.assertEqual(picture.size, (256, 144))
        editor.tabs.setCurrentWidget(editor.video_page)
        self.assertEqual(editor.cursor.value(), 32)
        pass

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
        editor.locate_input.setText(str(editor.frames.count))
        QTest.keyClick(editor.locate_input, Qt.Key.Key_Return)
        self.assertEqual(editor.cursor.value(), editor.frames.count)
        editor.locate_input.setText("1")
        QTest.mouseClick(editor.locate_button, Qt.MouseButton.LeftButton)
        self.assertEqual(editor.cursor.value(), 1)
        self.assertTrue(editor.selection_label.isVisible())
        self.assertTrue(editor.export_group.isVisible())
        self.assertFalse(editor.picture_group.isVisible())
        strip.horizontalScrollBar().setValue(20)
        self.wait_for(lambda: (self.source, 22) in strip.cache, "滚动加载后续帧缩略图")
        QTest.mouseClick(strip.viewport(), Qt.MouseButton.LeftButton,
                         pos=strip._tile_rect(2).center())
        self.assertEqual(editor.cursor.value(), 23)
        self.assertEqual(strip.current, 23)
        self.wait_for(lambda: editor.shown_frame == (self.source, 22) and editor.scope is None, "缩略图定位大预览")
        self.assertIn("第 23 /", editor.picture_caption.text())
        pass
        self.assertEqual(editor.directory_path.path, str(self.root))
        frame_directory = self.root / "帧输出"
        with patch("frameflow.editor.QFileDialog.getExistingDirectory",
                   return_value=str(frame_directory)) as choose:
            editor.directory_path.selectAll()
            self.assertEqual(editor.directory_path.selectedText(), str(self.root))
            QTest.mouseClick(editor.directory_path, Qt.MouseButton.LeftButton)
        self.assertEqual(choose.call_args.args[2], str(self.root))
        self.assertEqual(editor.directory_path.toolTip(), str(frame_directory))
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
        self.wait_for(lambda: editor.frames and editor.scope is None, "视频帧索引")
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
            self.assertEqual(editor.playback.count, 5)
            self.wait_for(lambda: editor.playback is None, "播放到选区末尾")
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
        self.assertEqual(int(length(editor.video_clipboard)), 3)
        original_count = int(length(editor.video))
        QTest.keyClick(strip, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(int(length(editor.video)), original_count - 3)
        QTest.keyClick(strip, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(int(length(editor.video)), original_count)
        QTest.keyClick(strip, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(int(length(editor.video)), original_count + 3)

    def test_shift_extends_both_sides_and_ctrl_toggles_one_frame(self):
        source = self.root / "多帧样片.mov"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-stream_loop", "1", "-i",
                         str(self.source), "-c", "copy", str(source)], timeout=20)
        editor = Editor(source, probe(source, self.tools, Processes()), self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.frames and editor.scope is None, "多帧样片索引")
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
        self.assertEqual([(clip.start, clip.end) for clip in editor.video_clipboard], [(0, 20), (79, 80)])
        self.assertEqual([(clip.start, clip.end) for clip in editor.video], [(20, 79), (80, editor.frames.count)])
        editor.undo()
        self.assertEqual([(clip.start, clip.end) for clip in editor.video], [(0, editor.frames.count)])

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
        shared.container.setCurrentIndex(shared.container.findData("MKV"))
        shared.codec.setCurrentIndex(shared.codec.findData("mkv_hevc"))
        shared.export_edge.setCurrentIndex(shared.export_edge.findData(1280))
        shared.output_fps.setCurrentIndex(shared.output_fps.findData(30))
        self.assertEqual((first.export_settings.format, first.export_settings.max_edge,
                          first.export_settings.output_fps), ("mkv_hevc", 1280, 30))
        window.navigation.setCurrentIndex(1)
        self.assertIs(window.pages.currentWidget(), shared)
        self.wait_for(lambda: not window.page_transition.running, "预览转场结束")
        self.assertIs(shared.settings_scroll.widget(), shared.settings_body)
        self.wait_for(lambda: shared.frames and shared.scope is None, "视频读取结束")
        shared.quality.setValue(27)
        directory = self.root / "仅第一个文件"
        with patch("frameflow.editor.QFileDialog.getExistingDirectory", return_value=str(directory)):
            shared.choose_directory()
        window.navigation.setCurrentIndex(0)
        self.wait_for(lambda: not window.page_transition.running, "返回转换页")
        self.assertIs(window.mounted_editor, shared)
        self.assertEqual(shared.quality.value(), 27)
        self.assertEqual(window._job_export(first).quality, 27)
        self.assertEqual(window._job_export(first).directory, directory)
        window.list.setCurrentRow(1)
        self.assertEqual(window.mounted_editor.format.currentData(), "mp4")
        self.assertEqual(window.mounted_editor.export_edge.currentData(), 0)
        self.assertEqual(window.mounted_editor.directory, second.parent)
        window.list.setCurrentRow(0)
        self.assertIs(window.mounted_editor, shared)
        self.assertEqual(shared.codec.currentData(), "mkv_hevc")

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

        self.assertEqual(editor.export_edge.currentText(), "原始分辨率")
        self.assertEqual(editor.output_options().max_edge, 0)
        self.assertEqual(export_size(), (1280, 720))

        window.navigation.setCurrentIndex(1)
        self.wait_for(lambda: not window.page_transition.running, "进入预览工作区")
        self.assertEqual(editor.export_edge.currentText(), "原始分辨率")
        editor.export_edge.setCurrentIndex(editor.export_edge.findData(854))
        window.navigation.setCurrentIndex(0)
        self.wait_for(lambda: not window.page_transition.running, "返回转换工作区")
        self.assertEqual(editor.export_edge.currentText(), "标准 · 480P")
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

    def test_edited_timeline_plays_pauses_and_cleans_up(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.frames and editor.shown_frame == (self.source, 0), "首帧预览")
        editor.timeline_zoom.setValue(0)
        self.assertGreater(editor.filmstrip._stride(), 1)
        self.assertEqual(editor.filmstrip.horizontalScrollBar().maximum(), 0)
        editor.timeline_zoom.setValue(4)
        self.assertEqual(editor.filmstrip._stride(), 1)
        editor.video_first.setValue(3)
        editor.video_last.setValue(7)
        editor.delete_video()
        self.assertEqual([(clip.start, clip.end) for clip in editor.video], [(0, 2), (7, 58)])
        old_preview = editor.frame_view.original.cacheKey()
        sound = patch("frameflow.playback.WaveOutput") if sys.platform == "win32" else nullcontext()
        with sound as output:
            QTest.mouseClick(editor.play_button, Qt.MouseButton.LeftButton)
            player = editor.playback
            self.wait_for(lambda: editor.cursor.value() >= 5, "播放跨过已剪掉的区间")
            self.assertEqual(editor.shown_frame, (self.source, editor.cursor.value() + 4))
            self.assertNotEqual(editor.frame_view.original.cacheKey(), old_preview)
            if output is not None:
                output.assert_called_once_with(player.wav)
                editor.player_volume.setValue(35)
                self.assertEqual(player.volume, .35)
                editor.volume_button.click()
                self.assertEqual(player.volume, 0)
                editor.volume_button.click()
                self.assertEqual(player.volume, .35)
                with wave.open(str(player.wav), "rb") as audio:
                    self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 53 / 24, delta=0.01)
            editor.fullscreen_button.click()
            self.assertTrue(editor.fullscreen_player.isFullScreen())
            QTest.keyClick(editor.fullscreen_player, Qt.Key.Key_Escape)
            self.assertIsNone(editor.fullscreen_player)
            self.assertIs(editor.playback, player)
            QTest.mouseClick(editor.play_button, Qt.MouseButton.LeftButton)
            paused = editor.cursor.value()
            QTest.qWait(120)
            self.assertEqual(editor.cursor.value(), paused)
            self.assertTrue(player.video_task.done())
            QTest.mouseClick(editor.play_button, Qt.MouseButton.LeftButton)
            self.wait_for(lambda: editor.playback is None, "播放到时间线末尾")
            self.assertEqual(editor.cursor.value(), 53)
            self.assertEqual(editor.status.text(), "播放结束。")
            QTest.mouseClick(editor.play_button, Qt.MouseButton.LeftButton)
            restarted = editor.playback
            self.wait_for(lambda: editor.cursor.value() < paused, "末尾播放时回到开头")
            editor.close()
            self.assertIsNone(editor.playback)
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
        self.wait_for(lambda: editor.frames and editor.scope is None, "读取短音轨视频")
        last = editor.frames.count
        editor.video_selection = ((3, 7), (12, 15), (last - 5, last))
        with patch("frameflow.playback.WaveOutput"):
            editor.play_video_selection()
            player = editor.playback
            self.assertEqual(player.count, 15)
            self.wait_for(lambda: editor.playback is None, "不连续选区播完")
            self.assertEqual(editor.cursor.value(), last)
            self.assertEqual(editor.status.text(), "播放结束。")
            self.assertFalse(player.audio_error)
            with wave.open(str(player.wav), "rb") as audio:
                frames = audio.getnframes()
                self.assertAlmostEqual(frames / audio.getframerate(), player.count / editor.frames.fps, delta=.002)
                pcm = array("h", audio.readframes(frames))
            self.assertTrue(any(pcm[:len(pcm) // 2]))
            self.assertFalse(any(pcm[-4000:]))

    def test_audio_and_video_trim_in_both_directions(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.frames and editor.scope is None, "加载带音轨的视频")
        editor.tabs.setCurrentWidget(editor.audio_page)
        self.wait_for(lambda: editor.audio_view.interval and editor.scope is None, "音轨波形")
        frames = editor.frames.count
        fps = editor.frames.fps

        # 毫秒精度的结束值 .417 秒恰好指向第十帧边界，不多删第十一帧。
        editor.audio_first.setValue(.168)
        editor.audio_last.setValue(.417)
        editor.cut_audio()
        self.wait_for(lambda: editor.audio_view.interval and editor.scope is None, "剪切后重绘波形")
        self.assertEqual([(c.start, c.end) for c in editor.video], [(0, 4), (10, frames)])
        self.assertAlmostEqual(length(editor._audio_track()), (frames - 6) / fps)
        self.assertEqual((editor.audio_clipboard_video[0].start, editor.audio_clipboard_video[0].end), (4, 10))
        editor.audio_cursor.setValue(4 / fps)
        editor.paste_audio()
        self.wait_for(lambda: editor.audio_view.interval and editor.scope is None, "粘贴同步恢复两轨")
        self.assertEqual([(c.start, c.end) for c in editor.video], [(0, 4), (4, 10), (10, frames)])
        self.assertAlmostEqual(length(editor._audio_track()), frames / fps)
        editor.undo()
        self.assertAlmostEqual(length(editor._audio_track()), (frames - 6) / fps)
        editor.undo()
        self.assertEqual(len(editor.video), 1)
        self.assertIsNone(editor.audio)

        # 调过音量的轨道也必须按当前时间线同步裁剪，保留音量设置。
        editor.audio_first.setValue(0)
        editor.audio_last.setValue(frames / fps)
        editor.volume.setValue(35)
        editor.set_volume()
        editor.tabs.setCurrentWidget(editor.video_page)
        editor.video_first.setValue(13)
        editor.video_last.setValue(24)
        editor.delete_video()
        self.assertEqual([(c.start, c.end) for c in editor.video], [(0, 12), (24, frames)])
        self.assertEqual(editor._frame_ref(int(length(editor.video))), (self.source, frames - 1))
        self.assertAlmostEqual(length(editor.audio), (frames - 12) / fps)
        self.assertAlmostEqual(editor.audio[1].start, 1, delta=.001)
        self.assertEqual({c.volume for c in editor.audio}, {.35})
        outputs = []
        editor.saved.connect(outputs.append)
        editor.save_edit()
        self.wait_for(lambda: outputs and editor.scope is None, "导出同步裁剪成品")
        output_info = probe(outputs[0], self.tools, Processes())
        self.assertEqual(index_frames(outputs[0], output_info, self.tools, Processes()).count, frames - 12)
        self.assertIsNotNone(output_info.audio_stream)
        self.assertAlmostEqual(output_info.duration, (frames - 12) / fps, delta=.05)
        self.decode(outputs[0])

    def test_editor_rapid_seeking_keeps_preview_geometry(self):
        from frameflow.editor import frame_png

        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.shown_frame == (self.source, 0), "首帧")
        view = editor.frame_view
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

        with patch("frameflow.editor.frame_png", side_effect=slow_frame):
            editor.cursor.setValue(8)
            self.wait_for(entered.is_set, "首个后台请求开始")
            editor.cursor.setValue(18)
            self.assertEqual(view.original.cacheKey(), original_image)
            self.assertEqual(view.geometry(), original_rect)
            self.assertEqual(editor.timeline.geometry(), original_timeline)
            self.wait_for(lambda: editor.shown_frame == (self.source, 17) and editor.frame_scope is None, "显示最后选中帧")
        self.assertNotEqual(view.original.cacheKey(), original_image)
        self.assertEqual(view.geometry(), original_rect)
        self.assertEqual(editor.timeline.geometry(), original_timeline)
        self.assertEqual(view.zoom, zoom)
        wheel(-240)
        self.assertLess(view._image_rect().height(), fit_rect.height())
        QTest.mouseDClick(view, Qt.MouseButton.LeftButton)
        self.assertEqual(view._image_rect(), fit_rect)
        editor.cursor.setValue(1)
        self.assertEqual(editor.shown_frame, (self.source, 0))

    def test_audio_editor_preview_insert_remove_and_volume(self):
        source = self.root / "编辑音轨.wav"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.source),
                         "-map", "0:a:0", str(source)], timeout=10)
        info = probe(source, self.tools, Processes())
        editor = Editor(source, info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.scope is None and editor.audio_view.bands, "选区波形")
        bounds = editor.audio_view._bounds()
        self.assertGreater(bounds.height(), editor.audio_view.height() * .75)
        # 音频文件只有「音轨」一个编辑页，外加通用的「文件详情」
        self.assertEqual(editor.tabs.count(), 2)
        self.assertEqual(editor.tabs.tabText(0), "音轨")
        self.assertEqual(editor.tabs.tabText(1), "文件详情")
        self.assertGreater(editor.audio_view.waveform.frames, 0)
        editor.audio_first.setValue(0)
        editor.audio_last.setValue(.5)
        editor.cut_audio()
        self.assertAlmostEqual(sum(c.length for c in editor.audio), info.duration - .5, delta=.01)
        editor.audio_cursor.setValue(.4)
        editor.paste_audio()
        self.assertAlmostEqual(sum(c.length for c in editor.audio), info.duration, delta=.01)
        editor.volume.setValue(40)
        editor.set_volume()
        self.assertTrue(any(clip.volume == .4 for clip in editor.audio))
        with patch("frameflow.editor.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            editor.insert_audio()
        self.wait_for(lambda: editor.scope is None and sum(c.length for c in editor.audio) > info.duration, "插入音频")
        outputs = []
        editor.saved.connect(outputs.append)
        editor.format.setCurrentIndex(editor.format.findData("wav"))
        editor.save_edit()
        self.wait_for(lambda: bool(outputs), "保存编辑音频")
        self.assertAlmostEqual(probe(outputs[0], self.tools, Processes()).duration, info.duration * 2, delta=.06)
        self.decode(outputs[0])

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
        self.wait_for(lambda: editor.frames and editor.scope is None and not window.page_transition.running,
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
        self.assertTrue(editor.quality.isVisible())
        self.assertFalse(editor.quality.slider.isHidden())
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
        self.assertTrue(editor.picture_group.isHidden())
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
        self.assertAlmostEqual(length(editor.audio_clipboard), end - start, places=3)
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
        self.wait_for(lambda: editor.frames and editor.scope is None, "可调整编辑区")

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
        self.wait_for(lambda: editor.frames and editor.scope is None, "播放器和媒体信息")
        self.app.processEvents()

        self.assertTrue(editor.player.isVisible())
        self.assertTrue(editor.player_seek.isVisible())
        self.assertEqual(editor.player_seek.minimum(), 1)
        self.assertEqual(editor.player_seek.maximum(), editor.frames.count)
        self.assertEqual(editor.player_seek.value(), editor.cursor.value())
        self.assertTrue(editor.media_summary.isVisible())
        values = [label.text() for label in editor.media_summary.findChildren(QLabel)]
        joined = " ".join(values)
        self.assertIn("320 × 180", joined)
        self.assertIn("24 FPS", joined)
        self.assertGreaterEqual(editor.preview_split.indexOf(editor.media_summary), 0)
        self.assertTrue(editor.output_file_name.text().endswith("_编辑.mp4"))
        self.assertTrue(editor.acceleration.isVisible())
        self.assertEqual(editor.acceleration.itemData(0), "cpu")
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
        self.assertTrue(editor.export_group.isVisible())

        editor.player_seek.setValue(9)
        self.assertEqual(editor.cursor.value(), 9)
        self.assertEqual(editor.player_time_current.text(), "00:00.333")
        QTest.mouseClick(editor.player_seek, Qt.MouseButton.LeftButton,
                         pos=QPoint(editor.player_seek.width() - 2, editor.player_seek.height() // 2))
        self.assertEqual(editor.cursor.value(), editor.frames.count)
        editor.cursor.setValue(2)
        editor._use_current_frame(editor.video_first)
        editor.cursor.setValue(editor.cursor.maximum())
        editor._use_current_frame(editor.video_last)
        self.assertEqual(editor.video_first.value(), 2)
        self.assertEqual(editor.video_selection, ((2, editor.video_last.value()),))
        tail = min(25, editor.frames.count)
        editor._set_video_range(2, tail)
        editor._range_fields_changed()
        self.assertEqual(editor.video_selection, ((2, tail),))
        self.assertIn("1080P", editor.export_edge.itemText(editor.export_edge.findData(1920)))
        editor.export_edge.setCurrentIndex(editor.export_edge.findData(854))
        self.assertIn("854 × 480", editor.export_dimensions.text())
        editor.output_fps.setCurrentIndex(editor.output_fps.findData(60))
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

    def test_filmstrip_gap_animates_and_noncontiguous_move_undo_restores_both_tracks(self):
        image = self.root / "still.png"
        Image.new("RGB", (32, 32), "blue").save(image)
        window = MainWindow(self.tools)
        self.addCleanup(window.close)
        window.resize(1200, 780)
        window.show()
        window.add_paths([self.source, image])
        self.wait_for(lambda: all(job.picture for job in window.jobs.values()), "队列")
        window.edit_button.click()
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.frames and editor.scope is None and not window.page_transition.running, "时间轴")
        strip = editor.filmstrip
        original = editor.video
        fps = editor.frames.fps
        original_audio = [Clip(self.source, self.info.audio_stream, 0, 5 / fps)]
        editor.audio = original_audio
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
        self.assertIs(editor.video, original)

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
            self.assertIs(editor.video, original)

        drag_right()
        QTest.keyClick(strip, Qt.Key.Key_Escape)
        QTest.mouseRelease(viewport, button, pos=end)
        self.assertFalse(editor.frame_context_menu.isVisible())
        self.assertEqual(editor.video_selection, selection)
        self.assertIs(editor.video, original)
        drag_right()
        QTest.mouseRelease(viewport, button, pos=end)
        refs = [editor._frame_ref(frame)[1] for frame in range(1, 10)]
        self.assertEqual(refs, [0, 3, 6, 7, 1, 2, 4, 5, 8])
        self.assertEqual(len(editor.history), 1)
        self.assertEqual(editor.video_selection, ((5, 8),))
        self.assertAlmostEqual(length(editor.audio), length(original) / fps)
        silent = slice_track(editor.audio, 2 / fps, 4 / fps)
        self.assertTrue(silent and all(clip.path is None for clip in silent))
        moved_audio = slice_track(editor.audio, 4 / fps, 6 / fps)
        self.assertAlmostEqual(moved_audio[0].start, 1 / fps)
        editor.undo()
        self.assertIs(editor.video, original)
        self.assertIs(editor.audio, original_audio)

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
        window.edit_button.click()
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.frames and editor.scope is None and not window.page_transition.running,
                      "时间轴就绪")
        strip = editor.filmstrip
        viewport = strip.viewport()
        editor._set_video_range(3, 8)
        editor._update_range_marker()
        track, selection = editor.video, editor.video_selection
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
            self.assertIs(editor.video, track)
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
        window.edit_button.click()
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.frames and editor.scope is None and not window.page_transition.running,
                      "时间轴就绪")
        strip = editor.filmstrip
        viewport = strip.viewport()
        bar = strip.horizontalScrollBar()
        bar.setValue(16)
        original = editor.video
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
        self.assertIs(editor.video, original)
        drop = QDropEvent(QPointF(point), action, mime, button, modifier)
        self.app.sendEvent(viewport, drop)
        self.assertTrue(drop.isAccepted())
        self.wait_for(lambda: editor.scope is None and image in editor.imported_images, "图片插入")
        self.assertEqual(editor._frame_ref(21)[0], editor.imported_images[image])
        editor.undo()
        self.assertIs(editor.video, original)

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
        self.assertIs(editor.video, original)

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
        self.wait_for(lambda: editor.frames and editor.scope is None and not window.page_transition.running,
                      "编辑器就绪")
        base = editor.frames.count
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
        with patch("frameflow.window.QDrag") as drag:
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
        self.wait_for(lambda: editor.scope is None and length(editor.video) == base + 1, "图片插入一帧")
        self.assertIs(window.pages.currentWidget(), editor)
        picture_ref = editor._frame_ref(2)
        self.wait_for(lambda: editor.shown_frame == picture_ref and picture_ref in strip.cache, "插入帧预览与缩略图")
        outputs = []
        editor.saved.connect(outputs.append)
        editor.save_image()
        self.wait_for(lambda: len(outputs) == 1, "保存插入的图片帧")
        with Image.open(outputs[0]) as result:
            self.assertEqual(result.size, (64, 96))
            self.assertEqual(result.getpixel((10, 10)), (240, 40, 80))

        def drop_video():
            send_drop(video, strip.viewport(), QPoint(strip._tile_rect(2).right() - 8, 30), boundary=3, after=True)

        video_before, audio_before = editor.video, editor.audio
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
        self.assertIs(editor.video, video_before)
        self.assertIs(editor.audio, audio_before)
        self.assertNotIn(video, editor.video_sources)
        self.assertEqual(editor.status.text(), "已取消素材插入。")

        drop_video()
        self.wait_for(lambda: editor.scope is None and video in editor.video_sources, "外部视频帧插入")
        self.assertEqual(editor._frame_ref(4), (video, 0))
        total = base + 1 + editor.video_sources[video].frames.count
        self.assertEqual(length(editor.video), total)
        left.setFocus()
        QTest.keyClick(left, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertIs(editor.video, video_before)
        self.assertIs(editor.audio, audio_before)
        drop_video()
        self.assertEqual(length(editor.video), total)
        self.assertFalse(any(clip.path == video for clip in editor._audio_track()))
        with patch("winsound.PlaySound") if sys.platform == "win32" else nullcontext():
            editor._play_video_ranges(((1, 6),))
            self.wait_for(lambda: editor.playback is None, "混合来源帧连续播放")
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
        idx = next(i for i, clip in enumerate(editor.audio) if clip.path == audio)
        self.assertAlmostEqual(length(editor.audio[:idx]), marker, places=6)
        self.assertEqual(length(editor.video), total)
        self.wait_for(lambda: editor.scope is None and wave.waveform, "插入后波形")
        send_drop(video, wave, release)
        idx = next(i for i, clip in enumerate(editor.audio) if clip.path == video)
        self.assertAlmostEqual(length(editor.audio[:idx]), marker, places=6)
        self.assertEqual(length(editor.video), total)
        editor.undo()
        self.assertFalse(any(clip.path == video for clip in editor.audio))

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

    def test_video_audio_overflow_mark_can_select_move_and_delete(self):
        audio = self.root / "额外音频.wav"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                         "sine=frequency=600", "-t", "0.9", str(audio)], timeout=15)
        audio_info = probe(audio, self.tools, Processes())
        editor = Editor(self.source, self.info, self.tools, self.root,
                        lookup_media=lambda key: (audio, audio_info, None) if key == "sound" else None)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.frames and editor.scope is None, "读取视频")
        editor.tabs.setCurrentWidget(editor.audio_page)
        view = editor.audio_view
        self.wait_for(lambda: editor.scope is None and view.waveform, "原音轨")
        original_video = editor.video
        video_end = editor._video_duration()
        editor._drop_audio("sound", .25)
        self.wait_for(lambda: editor.scope is None and view.waveform, "显示超出部分波形")
        total = video_end + audio_info.duration
        self.assertAlmostEqual(view.overflow[0], video_end)
        self.assertAlmostEqual(view.loaded_interval[1], total)
        self.assertEqual(view.trim_mark, view.overflow)
        self.assertIn("超出 0.900 秒", editor.overflow_label.text())

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
            menu = editor.audio_context_menu
            self.assertTrue(menu.isVisible())
            action = next(a for a in menu.actions() if a.text().startswith("删除标红音频"))
            menu.close()
            action.trigger()

        delete_mark()
        self.assertIs(editor.video, original_video)
        self.assertAlmostEqual(length(editor._audio_track()), video_end)
        self.assertIsNone(view.overflow)
        editor.undo()
        self.wait_for(lambda: editor.scope is None and view.waveform, "撤销后恢复超出范围")
        before = editor.audio
        drag(Qt.MouseButton.LeftButton, video_end + .2, video_end + .55)
        start, end = view.trim_mark
        selected = end - start
        self.assertAlmostEqual(selected, .35, delta=.015)
        self.assertIn(f"标红 {selected:.3f} 秒", editor.overflow_label.text())
        self.assertEqual(view._overflow_after_mark(), view.overflow)
        drag(Qt.MouseButton.RightButton, (start + end) / 2, .8)
        moved_start, moved_end = view.trim_mark
        self.assertLess(moved_end, video_end)
        self.assertAlmostEqual(moved_end - moved_start, selected)
        self.assertIs(editor.audio, before)
        projected_start, projected_end = view._overflow_after_mark()
        self.assertAlmostEqual(projected_start, video_end)
        self.assertAlmostEqual(projected_end - projected_start, .9 - selected)
        self.assertIn(f"删除标红后还差 {.9 - selected:.3f} 秒", editor.overflow_label.text())
        self.assertFalse(editor.audio_context_menu.isVisible())
        delete_mark()
        self.assertAlmostEqual(length(editor._audio_track()), total - selected)
        self.assertIs(editor.video, original_video)
        self.assertAlmostEqual(view.trim_mark[1] - view.trim_mark[0], .9 - selected)

    def test_waveform_cache_and_missing_audio_recovery(self):
        source = self.root / "原路径音频.mp3"
        moved = self.root / "移走的音频.mp3"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-i", str(self.source),
                         "-map", "0:a:0", str(source)], timeout=15)
        editor = Editor(source, probe(source, self.tools, Processes()), self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        view = editor.audio_view
        self.wait_for(lambda: editor.scope is None and view.waveform, "首次波形")
        waveform = view.waveform
        view.zoom(2)
        zoomed = view.interval
        with patch("frameflow.editor.audio_preview") as decode:
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
            editor.preview_audio()
            self.assertIs(view.waveform, waveform)
            self.assertEqual(editor.status.text(), "")
            decode.assert_not_called()

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
                      video_editor.scope is None and video_editor.frame_scope is None
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

    def test_image_editor_reduces_size_without_changing_source(self):
        source = self.root / "高细节 图片.png"
        with Image.frombytes("RGB", (480, 360), random.Random(42).randbytes(480 * 360 * 3)) as image:
            image.save(source)
        original = source.read_bytes()
        editor = Editor(source, probe(source, self.tools, Processes()), self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.scope is None and editor.image_view.pixmap(), "图片编辑预览")
        self.assertEqual(editor.tabs.count(), 2)
        self.assertEqual(editor.tabs.tabText(0), "图片")
        self.assertEqual(editor.tabs.tabText(1), "文件详情")
        editor.image_format.setCurrentIndex(editor.image_format.findData("png"))
        preset = editor.picture_preset
        self.assertEqual([preset.tabText(i) for i in range(preset.count())], ["快速", "均衡", "高压缩"])
        self.assertEqual(preset.currentIndex(), 1)
        self.assertTrue(editor.size.isHidden())
        QTest.mouseClick(preset, Qt.MouseButton.LeftButton, pos=preset.tabRect(2).center())
        self.assertTrue(editor.size.isVisible())
        editor.size.setValue(8)
        preset.setCurrentIndex(0)
        self.assertTrue(editor.size.isHidden())
        self.assertEqual(editor._picture_encoding(), (95, 0, 6))
        preset.setCurrentIndex(2)
        self.assertEqual(editor.size.value(), 8)
        outputs = []
        editor.saved.connect(outputs.append)
        editor.save_image()
        self.wait_for(lambda: bool(outputs), "图片按体积保存")
        self.assertLessEqual(outputs[0].stat().st_size, 8000)
        self.assertGreater(len(original), 8000)
        self.assertEqual(source.read_bytes(), original)
        with Image.open(outputs[0]) as result:
            self.assertLess(result.width, 480)

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

    def test_gif_budget_tries_smaller_profile(self):
        src = self.root / "竖屏样片.mp4"
        Processes().run([self.tools.ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                         "testsrc2=size=720x1280:rate=24", "-t", "4", "-c:v", "libx264",
                         "-preset", "ultrafast", str(src)], timeout=20)
        info = probe(src, self.tools, Processes())
        baseline = convert(src, info, Export("gif", small_gif=False), self.tools, Processes(), lambda _: None)
        limit = 350_000
        self.assertGreater(baseline.stat().st_size, limit)
        smaller = convert(src, info, Export("gif", small_gif=False, gif_limit=limit),
                          self.tools, Processes(), lambda _: None)
        self.assertLessEqual(smaller.stat().st_size, limit)
        self.decode(smaller)

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
        self.assertTrue(editor.directory_group.isVisible())
        self.assertTrue(editor.play_button.visibleRegion().boundingRect().contains(editor.play_button.rect()))
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
        settings = editor.settings_body
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
        point = editor.frame_view.mapTo(host, editor.frame_view.rect().center())
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
        self.assertIs(editor.settings_scroll.widget(), editor.settings_body)
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
        self.wait_for(lambda: editor.frames and editor.scope is None and not transition.running, "视频预览就绪")
        editor.quality.setValue(27)
        editor.player_volume.setValue(35)
        video = tuple(editor.video)
        with patch("frameflow.playback.WaveOutput"):
            editor.play_button.click()
            player = editor.playback
            self.wait_for(lambda: player.started is not None, "视频正在播放")
            window.navigation.setCurrentIndex(0)
            self.wait_for(lambda: 0 < transition.position < .85, "播放中退出预览")
            self.assertIs(editor.playback, player)
            window.navigation.setCurrentIndex(1)
            self.wait_for(lambda: not transition.running, "反向回到预览")
            self.assertIs(editor.playback, player)
            window.navigation.setCurrentIndex(0)
            self.wait_for(lambda: not transition.running, "完整退出预览")
            self.assertIsNone(editor.playback)
        paused_at = editor.cursor.value()
        window.navigation.setCurrentIndex(1)
        self.wait_for(lambda: not transition.running, "重新进入原编辑器")
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(tuple(editor.video), video)
        self.assertEqual(editor.cursor.value(), paused_at)
        self.assertEqual(editor.player_volume.value(), 35)
        self.assertEqual(editor.quality.value(), 27)
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

        with patch("frameflow.window.convert", side_effect=hold(convert)):
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
                self.assertFalse(window.mounted_editor.settings_body.isEnabled())
            finally:
                release.set()
            self.wait_for(lambda: window.scope is None, "转换完成")
        self.assertTrue(job.output.is_file())
        self.decode(job.output)

        window.navigation.setCurrentIndex(1)
        editor = window.pages.currentWidget()
        self.wait_for(lambda: editor.frames and editor.scope is None and not window.page_transition.running,
                      "可导出编辑结果")
        outputs = []
        editor.saved.connect(outputs.append)
        release.clear()
        with patch("frameflow.editor.render_edit", side_effect=hold(render_edit)):
            window.preview_start.click()
            try:
                scope = editor.scope
                self.assertIsNotNone(scope)
                for index in (0, 1):
                    window.navigation.setCurrentIndex(index)
                    self.wait_for(lambda: not window.page_transition.running, "编辑导出中切页")
                    self.assertIs(editor.scope, scope)
                    self.assertFalse(scope.cancelled.is_set())
                self.assertTrue(window.preview_cancel.isEnabled())
            finally:
                release.set()
            self.wait_for(lambda: outputs and editor.scope is None, "编辑导出完成")
        self.decode(outputs[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
