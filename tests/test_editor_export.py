"""Verify editor output parameters, estimates and actual files."""

import random
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from frameflow.media import Export, Processes
from frameflow.media import probe
from frameflow.window import MainWindow
from frameflow.editor import Editor, _size_text
from frameflow.editing import length

from workflow_support import WorkflowFixture


class EditorExportTest(WorkflowFixture):
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
        self.wait_for(lambda: not window.page_transition.running and editor.session.frames and editor.scope is None,
                      "预览转场和视频读取完成")
        self.assertIs(window._selected(), job)
        self.assertIs(window.pages.currentWidget(), editor)
        self.assertEqual(editor.settings.output_stem_edit.text(), "剪辑 Preview (最终)")
        destination = self.root / "编辑输出"
        destination.mkdir()
        with patch("frameflow.editor_settings.QFileDialog.getExistingDirectory", return_value=str(destination)):
            editor.settings.choose_directory()
        QTest.mouseClick(window.preview_footer.start, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(editor.scope)
        self.wait_for(lambda: editor.scope is None, "媒体预览导出完成")
        expected = destination / "剪辑 Preview (最终).mp4"
        self.assertEqual(names, [expected], editor.notice.text())
        self.assertEqual(probe(expected, self.tools, Processes()).codec, "H264")
        self.decode(expected)

        self.enter_output_name(editor, "CON")
        QTest.mouseClick(window.preview_footer.start, Qt.MouseButton.LeftButton)
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
        QTest.mouseClick(window.preview_footer.start, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(editor.scope)
        self.wait_for(lambda: editor.scope is None, "预览图片导出完成")
        expected = image.with_stem(f"{image.stem} (1)")
        self.assertEqual(names, [expected], editor.notice.text())
        self.assertEqual(image.read_bytes(), original)
        codec = Processes().run([self.tools.ffprobe, "-v", "error", "-select_streams", "v:0",
                                 "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(expected)],
                                timeout=20)
        self.assertEqual(codec.strip(), b"png")

    def test_video_image_tab_previews_and_saves_first_selected_frame(self):
        editor = Editor(self.source, self.info, self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        outputs = []
        editor.saved.connect(outputs.append)
        editor.show()
        self.wait_for(lambda: editor.session.frames and editor.scope is None, "视频可选帧")
        editor.settings.image_format.setCurrentIndex(editor.settings.image_format.findData("png"))
        self.assertEqual(editor.settings.picture_quality_label.text(), "PNG 压缩级别")
        self.assertIn("不会降低画质", editor.settings.picture_preset_hint.text())
        editor.settings.image_format.setCurrentIndex(editor.settings.image_format.findData("jpg"))
        self.assertEqual(editor.settings.picture_quality_label.text(), "画质")

        def show_picture(number):
            tab = editor.tabs.indexOf(editor.image_page)
            bar = editor.tabs.tabBar()
            QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(tab).center())
            self.assertIs(editor.tabs.currentWidget(), editor.image_page)
            self.assertTrue(editor.timeline.isHidden())
            self.assertTrue(editor.settings.export_group.isHidden())
            self.wait_for(lambda: editor.frame_preview.shown_frame == (self.source, number - 1)
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
        editor.settings.resolution.setCurrentIndex(editor.settings.resolution.findData(256))
        editor.save_image()
        self.wait_for(lambda: outputs and editor.scope is None, "保存最前面选区的首帧")
        self.assertTrue(outputs[0].stem.endswith("_帧000007"))
        with Image.open(outputs[0]) as picture:
            self.assertEqual(picture.size, (256, 144))
        editor.tabs.setCurrentWidget(editor.video_page)
        self.assertEqual(editor.cursor.value(), 32)
        pass

    def test_estimates_follow_export_tabs_and_edited_timeline(self):
        source = self.root / "estimate.mp4"
        Processes().run([
            self.tools.ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
            "testsrc2=size=640x360:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "6.76", "-c:v", "libx264", "-crf", "23", "-c:a", "aac", str(source),
        ], timeout=20)
        editor = Editor(source, probe(source, self.tools, Processes()), self.tools, self.root)
        self.addCleanup(self.dispose_editor, editor)
        editor.show()
        self.wait_for(lambda: editor.session.frames is not None and editor.scope is None, "读取视频帧")
        output = editor.settings.estimate_rows["output"]
        delta = editor.settings.estimate_rows["delta"]
        initial_video = output.text()
        self.assertIn("–", initial_video)
        self.assertIn("6.760 秒", editor.settings.estimate_card.toolTip())
        self.assertTrue(delta.isHidden())

        def select_frames(first, last):
            editor._set_video_range(first, last)
            editor._update_range_marker()

        def duplicate(last):
            select_frames(1, last)
            editor.copy_video()
            editor.cursor.setValue(int(length(editor.session.video)))
            editor.paste_video_after()

        duplicate(169)
        duplicate(99)
        self.assertEqual(length(editor.session.video), 437)
        longer_video = output.text()
        self.assertNotEqual(initial_video, longer_video)
        self.assertIn("17.480 秒", editor.settings.estimate_card.toolTip())
        select_frames(300, 310)
        editor.cut_video()
        self.assertNotEqual(output.text(), longer_video)
        editor.undo()
        self.assertEqual(output.text(), longer_video)
        select_frames(300, 310)
        editor.delete_video()
        self.assertNotEqual(output.text(), longer_video)
        editor.undo()
        self.assertEqual(output.text(), longer_video)

        editor.settings.quality.setValue(28)
        self.wait_for(lambda: output.text() != longer_video, "CRF 改变后的范围")
        crf_video = output.text()
        editor.settings.export_edge.setCurrentIndex(editor.settings.export_edge.findData(854))
        self.assertNotEqual(output.text(), crf_video)
        edge_video = output.text()
        editor.settings.output_fps.setCurrentIndex(editor.settings.output_fps.findData(30))
        self.assertNotEqual(output.text(), edge_video)
        editor.apply_output_options(Export("mp4", self.root, quality=23, audio_bitrate=192))
        self.assertEqual(output.text(), longer_video)

        exports = []
        editor.saved.connect(exports.append)
        editor.save_edit()
        self.wait_for(lambda: len(exports) == 1, "导出 H.264 MP4")
        video_info = probe(exports[0], self.tools, Processes())
        self.assertEqual(video_info.codec, "H264")
        self.assertAlmostEqual(video_info.duration, 17.480, delta=.06)
        self.assertGreater(exports[0].stat().st_size, 0)

        editor.tabs.setCurrentWidget(editor.audio_page)
        self.assertEqual(editor.settings.format.currentData(), "mp3")
        self.assertEqual(output.text(), "约 420.5 KB")
        self.assertIn("17.480 秒 × 192 kbps", editor.settings.estimate_card.toolTip())
        self.assertTrue(delta.isHidden())
        editor.settings.audio_bitrate.setCurrentIndex(editor.settings.audio_bitrate.findData(128))
        self.assertEqual(output.text(), "约 280.7 KB")
        editor.settings.audio_bitrate.setCurrentIndex(editor.settings.audio_bitrate.findData(192))
        self.wait_for(lambda: editor.scope is None, "音频预览准备完成")
        editor.save_edit()
        self.wait_for(lambda: len(exports) == 2, "导出 192 kbps MP3")
        audio_info = probe(exports[1], self.tools, Processes())
        self.assertEqual(audio_info.codec, "MP3")
        self.assertAlmostEqual(audio_info.duration, 17.480, delta=.06)
        self.assertAlmostEqual(audio_info.size, 419520 + 1024, delta=3000)

        editor.settings.format.setCurrentIndex(editor.settings.format.findData("wav"))
        self.assertEqual(output.text(), f"约 {_size_text(17.48 * 48000 * 2 * 2 + 128)}")
        editor.settings.audio_channels.setCurrentIndex(editor.settings.audio_channels.findData(1))
        editor.settings.audio_sample_rate.setCurrentIndex(editor.settings.audio_sample_rate.findData(22050))
        self.assertEqual(output.text(), f"约 {_size_text(17.48 * 22050 * 2 + 128)}")
        editor.settings.format.setCurrentIndex(editor.settings.format.findData("flac"))
        self.assertEqual(output.text(), "无法准确预估")
        self.assertTrue(delta.isHidden())

        editor.tabs.setCurrentWidget(editor.image_page)
        editor.settings.image_format.setCurrentIndex(editor.settings.image_format.findData("png"))
        self.assertEqual(output.text(), "正在计算…")
        old_request = editor.picture_estimate.generation
        select_frames(40, 40)
        self.assertGreater(editor.picture_estimate.generation, old_request)
        editor.picture_estimate._picture_estimated(old_request, 827800, "")
        self.assertEqual(output.text(), "正在计算…")
        self.wait_for(lambda: output.text().startswith("约 "), "当前帧 PNG 试编码")
        self.assertTrue(delta.isHidden())
        editor.save_image()
        self.wait_for(lambda: len(exports) == 3, "保存当前帧 PNG")
        picture_size = exports[2].stat().st_size
        self.assertEqual(output.text(), f"约 {_size_text(picture_size)}")
        self.assertIn(f"{picture_size:,} 字节", editor.settings.estimate_card.toolTip())
        self.assertEqual(probe(exports[2], self.tools, Processes()).codec, "PNG")
        current_request = editor.picture_estimate.generation
        editor.tabs.setCurrentWidget(editor.video_page)
        editor.picture_estimate._picture_estimated(current_request, picture_size, "")
        self.assertEqual(output.text(), longer_video)

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
        editor.settings.image_format.setCurrentIndex(editor.settings.image_format.findData("png"))
        preset = editor.settings.picture_preset
        self.assertEqual([preset.tabText(i) for i in range(preset.count())], ["快速", "均衡", "高压缩"])
        self.assertEqual(preset.currentIndex(), 1)
        self.assertTrue(editor.settings.size.isHidden())
        QTest.mouseClick(preset, Qt.MouseButton.LeftButton, pos=preset.tabRect(2).center())
        self.assertTrue(editor.settings.size.isVisible())
        editor.settings.size.setValue(8)
        preset.setCurrentIndex(0)
        self.assertTrue(editor.settings.size.isHidden())
        self.assertEqual(editor.settings.picture_encoding(), (95, 0, 6))
        preset.setCurrentIndex(2)
        self.assertEqual(editor.settings.size.value(), 8)
        outputs = []
        editor.saved.connect(outputs.append)
        editor.save_image()
        self.wait_for(lambda: bool(outputs), "图片按体积保存")
        self.assertLessEqual(outputs[0].stat().st_size, 8000)
        self.assertGreater(len(original), 8000)
        self.assertEqual(source.read_bytes(), original)
        with Image.open(outputs[0]) as result:
            self.assertLess(result.width, 480)
