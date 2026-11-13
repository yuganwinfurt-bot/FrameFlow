"""Own the editor's linked tracks, source indexes, clipboards and undo history."""

from dataclasses import replace
from math import ceil, floor

from .editing import Clip, VideoSource, audio_segment, length, original_audio, slice_track, splice
from .media import AudioInfo, VideoInfo


class EditSession:
    def __init__(self, path, info):
        self.path, self.info = path, info
        self.frames = None
        self.video = []
        self.video_sources = {}
        self.imported_images = {}
        self.audio = None if isinstance(info, VideoInfo) else (
            [Clip(path, info.stream, 0, info.duration)] if isinstance(info, AudioInfo) and info.duration else [])
        self.video_clipboard = []
        self.video_clipboard_audio = None
        self.audio_clipboard = []
        self.audio_clipboard_video = []
        self.history = []

    def set_index(self, frames):
        self.frames = frames
        self.video_sources[self.path] = VideoSource(self.info, frames, self.path.name)
        self.video = [Clip(self.path, self.info.stream, 0, frames.count)]

    def source_for(self, path):
        key = self.imported_images.get(path, path)
        return (key, self.video_sources[key]) if key in self.video_sources else None

    def register_source(self, path, source, *, image_path=None):
        self.video_sources[path] = source
        if image_path is not None:
            self.imported_images[image_path] = path

    def frame_ref(self, value):
        clips = slice_track(self.video, value - 1, value)
        return (clips[0].path, int(clips[0].start)) if clips else None

    def audio_track(self):
        if self.audio is not None:
            return self.audio
        return original_audio(self.video, self.frames, self.info.audio_stream) if self.frames else []

    def video_duration(self):
        return length(self.video) / self.frames.fps if self.frames else 0.0

    def audio_frames(self, start, end):
        """Cover frames touched by the selection, allowing for millisecond rounding."""
        fps = self.frames.fps
        total = int(length(self.video))
        first = min(total - 1, floor((start + .0005) * fps))
        last = min(total, max(first + 1, ceil((end - .0005) * fps)))
        return first, last

    def copy_video(self, selection):
        self.video_clipboard = [clip for first, last in selection
                                for clip in slice_track(self.video, first - 1, last)]
        fps = self.frames.fps
        self.video_clipboard_audio = None if self.audio is None else [
            clip for first, last in selection
            for clip in audio_segment(self.audio, (first - 1) / fps, last / fps)
        ]
        return int(length(self.video_clipboard))

    def copy_audio(self, start, end, track):
        selected = slice_track(track, start, end)
        if not selected:
            return 0
        self.audio_clipboard_video = []
        self.audio_clipboard = selected
        return length(selected)

    def linked_audio_selection(self, first, last, track):
        video = slice_track(self.video, first, last)
        fps = self.frames.fps
        audio = (original_audio(video, self.frames, self.info.audio_stream) if self.audio is None
                 else audio_segment(track, first / fps, last / fps))
        return video, audio

    def set_audio_clipboard(self, video, audio):
        self.audio_clipboard_video, self.audio_clipboard = video, audio

    def cut_tracks(self, ranges, *, audio_tail_end=None):
        if sum(end - start for start, end in ranges) >= length(self.video):
            return None
        video, audio = self.video, self.audio
        fps = self.frames.fps
        if audio is not None and audio_tail_end is not None:
            audio = splice(audio, self.video_duration(), audio_tail_end)
        for start, end in reversed(ranges):
            video = splice(video, start, end)
            if audio is not None:
                audio = splice(audio, start / fps, end / fps)
        return video, audio

    def moved_tracks(self, ranges, position):
        count = sum(end - start for start, end in ranges)
        total = length(self.video)
        if not 0 < count < total or not 0 <= position <= total - count:
            return None
        if len(ranges) == 1 and position == ranges[0][0]:
            return None

        def move(track, units):
            clips = [clip for start, end in ranges for clip in slice_track(track, start / units, end / units)]
            for start, end in reversed(ranges):
                track = splice(track, start / units, end / units)
            return splice(track, position / units, position / units, clips)

        audio = self.audio
        if audio:
            fps = self.frames.fps
            audio = move(audio_segment(audio, 0, max(length(audio), total / fps)), fps)
        return count, move(self.video, 1), audio

    def replace_tracks(self, video, audio):
        self.history.append((self.video, self.audio))
        self.video, self.audio = video, audio

    def replace_audio(self, track):
        self.replace_tracks(self.video, track)

    def insert_video(self, position, video, audio):
        updated_audio = self.audio
        if updated_audio is not None or audio is not None:
            time = position / self.frames.fps
            current = self.audio_track()
            copied = original_audio(video, self.frames, self.info.audio_stream) if audio is None else audio
            duration = length(video) / self.frames.fps
            updated_audio = ([*audio_segment(current, 0, time), *audio_segment(copied, 0, duration),
                              *slice_track(current, time, length(current))] if current or copied else [])
        self.replace_tracks(splice(self.video, position, position, video), updated_audio)

    def undo(self):
        if not self.history:
            return False
        self.video, self.audio = self.history.pop()
        return True
