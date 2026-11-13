"""Exercise timeline bounds without constructing a player or editor."""

from pathlib import Path
from unittest import TestCase

from frameflow.edit_session import EditSession
from frameflow.editing import Clip, Frames, audio_segment, length, slice_track
from frameflow.media import VideoInfo


class EditingModelTest(TestCase):
    def test_disjoint_move_and_undo_restore_both_tracks(self):
        path = Path("fixture.mp4")
        session = EditSession(path, VideoInfo(0, 5, 320, 180, 2, "H264", 1, audio_stream=1))
        session.set_index(Frames(tuple(i / 2 for i in range(10)), 5, 2))
        original_video = session.video.copy()
        original_audio = [Clip(path, 1, 0, 3)]
        session.replace_audio(original_audio)
        self.assertIsNone(session.cut_tracks([(0, 10)]))
        self.assertIsNone(session.moved_tracks([(1, 3)], 1))

        count, video, audio = session.moved_tracks([(1, 3), (5, 7)], 6)
        self.assertEqual(count, 4)
        session.replace_tracks(video, audio)
        self.assertEqual([session.frame_ref(i)[1] for i in range(1, 11)], [0, 3, 4, 7, 8, 9, 1, 2, 5, 6])
        self.assertEqual(length(audio), 5)
        self.assertTrue(any(clip.path is None for clip in audio))
        self.assertTrue(session.undo())
        self.assertEqual((session.video, session.audio), (original_video, original_audio))

    def test_out_of_bounds_audio_selection_ends_with_silence(self):
        source = Path("fixture.wav")
        track = [Clip(source, 0, 0, 2)]

        self.assertEqual(slice_track([], 0, 1), [])
        self.assertEqual(slice_track(track, 2, 4), [])
        segment = audio_segment(track, 1.5, 4)
        self.assertEqual(segment, [Clip(source, 0, 1.5, 2), Clip(None, -1, 0, 2)])
        self.assertEqual(length(segment), 2.5)
        self.assertEqual(track, [Clip(source, 0, 0, 2)])
