"""Assert units, uncertainty and input sensitivity of media size estimates."""

from dataclasses import replace
from unittest import TestCase

from frameflow.estimates import estimate_audio, estimate_video
from frameflow.media import Export, VideoInfo


class ExportEstimateTest(TestCase):
    def test_edited_mp3_uses_seconds_and_decimal_bitrate(self):
        options = Export("mp3", audio_bitrate=192)
        original = estimate_audio(options, duration=6.76, sample_rate=48000, channels=2)
        edited = estimate_audio(options, duration=17.48, sample_rate=48000, channels=2)
        self.assertAlmostEqual(edited.size_bytes - original.size_bytes, (17.48 - 6.76) * 192_000 / 8)
        self.assertAlmostEqual(edited.size_bytes, 419_520 + 1_024)
        self.assertIsNone(edited.upper_bytes)

    def test_pcm_override_and_formats_without_reliable_size(self):
        wav = estimate_audio(Export("wav", audio_sample_rate=44100, audio_channels=1),
                             duration=2.5, sample_rate=48000, channels=2)
        self.assertEqual(wav.size_bytes, 2.5 * 44100 * 2 + 128)
        for options, hint in ((Export("flac"), "无损压缩率"), (Export("ogg"), "可变码率"),
                              (Export("mp3", target_bytes=8000), "目标体积")):
            with self.subTest(options=options):
                result = estimate_audio(options, duration=2.5, sample_rate=48000, channels=2)
                self.assertIsNone(result.size_bytes)
                self.assertIn(hint, result.basis)

    def test_video_estimate_is_a_range_and_can_decline(self):
        source = VideoInfo(0, 7, 1280, 720, 24, "H264", 2_000_000, bitrate=2_400_000)
        options = Export("mp4", audio_bitrate=192)

        def estimate(export=options, audio_duration=0, original_only=True):
            return estimate_video(source, export, duration=4, timeline_fps=24,
                                  audio_duration=audio_duration, original_only=original_only)

        video = estimate()
        self.assertGreater(video.upper_bytes, video.size_bytes)
        with_audio = estimate(audio_duration=4)
        for edge in ("size_bytes", "upper_bytes"):
            self.assertAlmostEqual(getattr(with_audio, edge) - getattr(video, edge), 4 * 192_000 / 8)
        self.assertLess(estimate(replace(options, quality=29, max_edge=854)).upper_bytes, video.upper_bytes)
        self.assertGreater(estimate(replace(options, output_fps=48)).upper_bytes, video.upper_bytes)
        self.assertIsNone(estimate(original_only=False).size_bytes)
        self.assertIsNone(estimate(replace(options, target_bytes=100_000)).size_bytes)
