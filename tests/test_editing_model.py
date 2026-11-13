"""Exercise timeline bounds without constructing a player or editor."""

from pathlib import Path
from unittest import TestCase

from frameflow.editing import Clip, audio_segment, length, slice_track


class EditingModelTest(TestCase):
    def test_out_of_bounds_audio_selection_ends_with_silence(self):
        source = Path("fixture.wav")
        track = [Clip(source, 0, 0, 2)]

        self.assertEqual(slice_track([], 0, 1), [])
        self.assertEqual(slice_track(track, 2, 4), [])
        segment = audio_segment(track, 1.5, 4)
        self.assertEqual(segment, [Clip(source, 0, 1.5, 2), Clip(None, -1, 0, 2)])
        self.assertEqual(length(segment), 2.5)
        self.assertEqual(track, [Clip(source, 0, 0, 2)])
