"""Exercise encoding and output publication with real media and FFprobe."""

import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from frameflow.conversion_batch import run_batch
from frameflow.media import AudioInfo, Cancelled, Export, MediaError, Processes, Tools, convert, probe, thumbnail

from media_test_support import MediaFixture


class MediaConversionTest(MediaFixture, unittest.TestCase):
    def test_bundled_tool_lookup_prefers_bin_without_path(self):
        folder = self.root / "bin"
        folder.mkdir()
        suffix = ".exe" if os.name == "nt" else ""
        ffmpeg, ffprobe = (folder / (name + suffix) for name in ("ffmpeg", "ffprobe"))
        for executable in (ffmpeg, ffprobe):
            executable.touch()
        with patch("frameflow.media_runtime.PROJECT", self.root), patch(
            "frameflow.media_runtime.shutil.which", side_effect=AssertionError("bundled tools must take priority")
        ):
            self.assertEqual(Tools.find(), Tools(str(ffmpeg), str(ffprobe)))

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

    @unittest.skipUnless(sys.platform == "win32", "Windows 对拒写目录的访问检查行为")
    def test_denied_output_directory_does_not_retry_workspace_creation(self):
        attempts = []
        mkdir = os.mkdir

        def deny_workspace(path, *args, **kwargs):
            path = Path(path)
            if path.parent == self.root and path.name.startswith(".frameflow-"):
                attempts.append(path)
                raise PermissionError(13, "Access denied", str(path))
            return mkdir(path, *args, **kwargs)

        # Bound the standard library's Windows retry loop so a regression fails quickly.
        with patch("tempfile.TMP_MAX", 2), patch("os.mkdir", side_effect=deny_workspace):
            with self.assertRaises(PermissionError):
                convert(self.source, self.info, Export("mp4", self.root), self.tools, Processes(), lambda _: None)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(list(self.root.glob(".frameflow-*")), [])

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
        source, info = self.source, self.info
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
            output = convert(source, info,
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
                convert(source, info,
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

    def test_batch_continues_after_failed_item_and_leaves_no_partial_file(self):
        broken = self.root / "broken.mov"
        broken.write_bytes(b"not a video")
        output_dir = self.root / "output"
        output_dir.mkdir()
        original = self.source.read_bytes()
        events = []
        run_batch(
            [(key, path, self.info, Export("mkv", output_dir))
             for key, path in (("broken", broken), ("valid", self.source))],
            self.tools, Processes(),
            phase=lambda key, _message: events.append(("phase", key)),
            progress=lambda _key, _percent: None,
            exported=lambda *result: events.append(("export", *result)),
            completed=lambda cancelled: events.append(("completed", cancelled)),
        )
        self.assertEqual([event[0] for event in events], ["phase", "export", "phase", "export", "completed"])
        self.assertEqual(events[1][1:3], ("broken", None))
        self.assertTrue(events[1][-1])
        _, key, output, info, _, error = events[3]
        self.assertEqual((key, error, events[-1]), ("valid", "", ("completed", False)))
        self.assertEqual((info.codec, info.width, info.height), ("H264", 320, 180))
        self.assertIn("matroska", info.container.split(","))
        self.assertAlmostEqual(info.duration, self.info.duration, delta=.15)
        self.decode(output)
        self.assertEqual(list(output_dir.iterdir()), [output])
        self.assertEqual(self.source.read_bytes(), original)
