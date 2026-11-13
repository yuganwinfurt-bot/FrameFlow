"""Native video transport, playback timers, volume, and fullscreen ownership."""

from PySide6.QtCore import QEvent, QObject, QPoint, QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QMenu, QSizePolicy, QVBoxLayout, QWidget, QWidgetAction

from . import icons
from .editing import length
from .editor_controls import _text, timecode
from .editor_widgets import SeekSlider, TransportButton, VideoPreview, VolumePopup
from .media import MediaError
from .motion import WheelSlider
from .playback import Playback


class FullscreenPlayer(QDialog):
    def __init__(self, parent, player):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.player = player
        self.setObjectName("fullscreenPlayer")
        self.setWindowTitle(player.path.name)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(player.widget)
        self.hide_timer = QTimer(self, singleShot=True, interval=2500)
        self.hide_timer.timeout.connect(lambda: player.player_controls.hide() if player.playback else None)
        for widget in (self, player.widget, player.frame_view, player.player_controls,
                       *player.player_controls.findChildren(QWidget)):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.activated.connect(self.reject)
        self.hide_timer.start()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Enter, QEvent.Type.MouseButtonPress):
            self.player.player_controls.show()
            self.hide_timer.start()
        return super().eventFilter(obj, event)

    def done(self, result):
        self.hide_timer.stop()
        self.player.restore_player()
        super().done(result)
        self.deleteLater()


class VideoTransport(QObject):
    def __init__(self, host, layout, cursor, session, tools, audio_path, *,
                 ready, selection, before_play, suspend_seek, playback_stopped,
                 frame_played, preview_width, notice, notice_detail, clear_status, canvas_for):
        super().__init__(host)
        self._host, self._layout, self.cursor = host, layout, cursor
        self.session, self.tools, self.audio_path = session, tools, audio_path
        self.path, self.info = session.path, session.info
        self.ready, self.selection = ready, selection
        self.before_play, self.suspend_seek = before_play, suspend_seek
        self.playback_stopped, self.frame_played = playback_stopped, frame_played
        self.preview_width, self.notice, self.canvas_for = preview_width, notice, canvas_for
        self.notice_detail, self.clear_status = notice_detail, clear_status
        self.playback_level = self.last_audible_level = 100
        self.playback_muted = False
        self.fullscreen_player = self.playback = None
        self.playback_ranges = ()
        self.playback_timer = QTimer(self, interval=12, timerType=Qt.TimerType.PreciseTimer)
        self.playback_timer.timeout.connect(self._playback_tick)
        self._build()

    def _build(self):
        self.widget = QFrame()
        self.widget.setObjectName("videoPlayer")
        player_layout = QVBoxLayout(self.widget)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.setSpacing(0)
        self.frame_view = VideoPreview()
        self.frame_view.setText("等待预览")
        self.frame_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.frame_view.setObjectName("videoCanvas")
        self.frame_view.setMinimumHeight(150)
        badges = QVBoxLayout(self.frame_view)
        badges.setContentsMargins(10, 9, 10, 0)
        player_header = QHBoxLayout()
        player_header.setSpacing(5)
        container = self.path.suffix.lstrip(".").upper() or "VIDEO"
        player_header.addWidget(_text(container, name="playerBadge"))
        player_header.addWidget(_text(self.info.codec, name="playerBadge"))
        player_header.addWidget(_text(f"{self.info.width} × {self.info.height}", name="playerBadge"))
        player_header.addWidget(_text(f"{self.info.fps:g} FPS", name="playerBadge"))
        player_header.addStretch(1)
        badges.addLayout(player_header)
        badges.addStretch(1)
        player_layout.addWidget(self.frame_view, 1)

        controls = QFrame()
        controls.setObjectName("playerControls")
        self.player_controls = controls
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(10, 7, 10, 8)
        controls_layout.setSpacing(6)
        self.previous_frame_button = TransportButton("previous", "上一帧", self.cursor.stepDown)
        self.play_button = TransportButton("play", "播放", self.toggle, primary=True)
        self.play_button.setEnabled(False)
        self.next_frame_button = TransportButton("next", "下一帧", self.cursor.stepUp)
        for widget in (self.previous_frame_button, self.play_button, self.next_frame_button):
            widget.setEnabled(False)
            controls_layout.addWidget(widget)
        self.player_time_current = _text("00:00.000", name="playerTime")
        self.player_time_current.setContentsMargins(5, 0, 0, 0)
        controls_layout.addWidget(self.player_time_current)
        self.player_time_short = _text("00:00", name="playerTime")
        self.player_time_short.hide()
        controls_layout.addWidget(self.player_time_short)
        self.player_time_separator = _text("/", name="playerTime")
        controls_layout.addWidget(self.player_time_separator)
        self.player_time_total = _text("00:00.000", name="playerTime")
        controls_layout.addWidget(self.player_time_total)
        self.player_seek = SeekSlider(Qt.Orientation.Horizontal)
        self.player_seek.setObjectName("playerSeek")
        self.player_seek.setAccessibleName("视频播放位置")
        self.player_seek.setRange(1, 1)
        self.player_seek.setSingleStep(1)
        self.player_seek.setPageStep(1)
        self.player_seek.setMinimumWidth(42)
        self.player_seek.setEnabled(False)
        self.player_seek.valueChanged.connect(self._seek_from_player)
        controls_layout.addWidget(self.player_seek, 1)
        self.volume_button = TransportButton("volume", "静音 / 恢复音量", self._toggle_player_mute)
        controls_layout.addWidget(self.volume_button)
        self.volume_button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.volume_button.customContextMenuRequested.connect(self._show_player_volume)
        self.volume_button.installEventFilter(self)
        self.volume_hide = QTimer(self)
        self.volume_hide.setSingleShot(True)
        self.volume_hide.setInterval(260)
        self.volume_hide.timeout.connect(lambda: self.volume_menu.hide()
                                         if self.volume_menu.isVisible() else None)
        self.volume_menu = QMenu(self.volume_button)
        volume_action = QWidgetAction(self.volume_menu)
        volume_panel = QWidget()
        volume_layout = QHBoxLayout(volume_panel)
        volume_layout.addWidget(_text("音量"))
        self.player_volume = WheelSlider(Qt.Orientation.Horizontal, wheel_step=5)
        self.player_volume.setRange(0, 100)
        self.player_volume.setValue(self.playback_level)
        self.player_volume.setAccessibleName("播放音量")
        self.player_volume.setFixedWidth(110)
        self.player_volume.valueChanged.connect(self._set_player_volume)
        volume_layout.addWidget(self.player_volume)
        self.volume_value = _text("100%")
        self.volume_value.setMinimumWidth(36)
        volume_layout.addWidget(self.volume_value)
        volume_action.setDefaultWidget(volume_panel)
        self.volume_menu.addAction(volume_action)
        self.volume_options = TransportButton("down", "调整播放音量", self._show_player_volume)
        self.volume_options.setFixedSize(24, 32)
        self.volume_options.hide()
        self.volume_button.setToolTip("")
        self.volume_popup = VolumePopup(self._host)
        self.volume_popup.changed.connect(self._set_player_volume)
        self.volume_popup.set_level(self.playback_level)
        self.volume_button.installEventFilter(self)
        self.volume_popup.installEventFilter(self)
        self.volume_hide = QTimer(self)
        self.volume_hide.setSingleShot(True)
        self.volume_hide.setInterval(250)
        self.volume_hide.timeout.connect(self.hide_volume_popup)
        self.fullscreen_button = TransportButton("fullscreen", "全屏", self.toggle_fullscreen)
        controls_layout.addWidget(self.fullscreen_button)
        player_layout.addWidget(controls)
        self._layout.addWidget(self.widget, 1)

    def toggle(self):
        if self.playback is not None:
            self.stop()
            self.notice("已暂停。")
            return
        if not self.ready():
            return
        total = int(length(self.session.video))
        start = self.cursor.value()
        self._play_video_ranges(((1 if start == total else start, total),))

    def eventFilter(self, watched, event):
        popup = getattr(self, "volume_popup", None)
        button = getattr(self, "volume_button", None)
        if popup is None or button is None:
            return super().eventFilter(watched, event)
        if watched is button or watched is popup:
            if event.type() in (QEvent.Type.Enter, QEvent.Type.HoverEnter):
                self.volume_hide.stop()
                self._show_volume_popup()
            elif event.type() == QEvent.Type.Leave:
                self.volume_hide.start()
        return super().eventFilter(watched, event)

    def _volume_hovered(self) -> bool:
        """鼠标是否还落在喇叭按钮或音量面板上（用全局坐标判断）。"""
        button = getattr(self, "volume_button", None)
        popup = getattr(self, "volume_popup", None)
        cursor = QCursor.pos()
        for widget in (button, popup):
            if widget is not None and widget.isVisible():
                if widget.rect().contains(widget.mapFromGlobal(cursor)):
                    return True
        return False

    def _finish_volume_hide(self) -> None:
        if self._volume_hovered():
            return
        self.hide_volume_popup()

    def _show_volume_popup(self) -> None:
        popup = getattr(self, "volume_popup", None)
        if popup is None:
            return
        self.volume_hide.stop()
        popup.set_level(0 if self.playback_muted else self.playback_level)
        corner = self.volume_button.mapToGlobal(QPoint(0, 0))
        x = corner.x() + self.volume_button.width() // 2 - popup.width() // 2
        y = corner.y() - popup.height() - 6
        screen = self.widget.screen().availableGeometry()
        x = max(screen.left() + 4, min(x, screen.right() - popup.width() - 4))
        if y < screen.top() + 4:
            y = self.volume_button.mapToGlobal(QPoint(0, self.volume_button.height())).y() + 6
        popup.move(x, y)
        popup.show()
        popup.raise_()

    def hide_volume_popup(self) -> None:
        popup = getattr(self, "volume_popup", None)
        if popup is not None and popup.isVisible():
            popup.hide()

    def _show_player_volume(self, *_):
        self._show_volume_popup()

    def _set_player_volume(self, value):
        self.playback_level = value
        if value:
            self.last_audible_level = value
        self.playback_muted = False
        with QSignalBlocker(self.player_volume):
            self.player_volume.setValue(value)
        self._update_player_volume()

    def _toggle_player_mute(self):
        if self.playback_level == 0:
            self.player_volume.setValue(self.last_audible_level)
        else:
            self.playback_muted = not self.playback_muted
            self._update_player_volume()

    def _update_player_volume(self):
        effective = 0 if self.playback_muted else self.playback_level
        self.volume_value.setText(f"{effective}%")
        self.volume_button.setIcon(icons.icon("volume" if effective else "mute", 18, "#FFFFFF", 2))
        if self.volume_popup.isVisible():
            self.volume_popup.set_level(effective)
        if self.playback is not None:
            self.playback.set_volume(effective / 100)

    def toggle_fullscreen(self):
        if self.fullscreen_player is not None:
            self.fullscreen_player.reject()
            return
        self.fullscreen_player = FullscreenPlayer(self._host, self)
        self.fullscreen_button.setIcon(icons.icon("exitFullscreen", 18, "#FFFFFF", 2))
        self.fullscreen_button.setToolTip("退出全屏（Esc）")
        self.fit_controls()
        self.fullscreen_player.showFullScreen()

    def restore_player(self):
        self.fullscreen_player = None
        self._layout.addWidget(self.widget, 1)
        self.widget.show()
        self.player_controls.show()
        self.fullscreen_button.setIcon(icons.icon("fullscreen", 18, "#FFFFFF", 2))
        self.fullscreen_button.setToolTip("全屏")
        self.fit_controls()

    def fit_controls(self):
        if hasattr(self, "fullscreen_button"):
            room = self.fullscreen_player is not None or self.preview_width() >= 445
            for widget in (self.player_time_current, self.player_time_total, self.player_time_separator):
                widget.setVisible(room)
            self.player_time_short.setVisible(not room)

    def play_video_selection(self):
        if self.ready() and self.selection():
            self._play_video_ranges(self.selection())

    def _play_video_ranges(self, ranges):
        self.stop()
        self.before_play()
        self.suspend_seek()
        self.playback_ranges = tuple(ranges)
        video, audio = self.session.tracks_for_selection(self.playback_ranges)
        self.playback = Playback(video, audio, self.session.frames.fps, self.tools,
                                 self.audio_path, canvas=self.canvas_for(video),
                                 volume=0 if self.playback_muted else self.playback_level / 100)
        self.notice("正在准备剪辑预览…")
        self.notice_detail("")
        self._set_transport_playing(True)
        self.playback_timer.start()

    def _set_transport_playing(self, playing):
        self.play_button.setIcon(icons.icon("pause" if playing else "play", 18, "#FFFFFF", 2))
        self.play_button.setAccessibleName("暂停" if playing else "播放")
        self.play_button.setToolTip("暂停播放" if playing else "从当前帧开始播放剪辑预览")
        if not playing and self.fullscreen_player is not None:
            self.player_controls.show()

    def _playback_tick(self):
        player = self.playback
        first_frame = player.started is None
        try:
            data = player.tick()
        except (MediaError, OSError) as exc:
            self.stop()
            self.notice("播放失败，详情见悬停提示。")
            self.notice_detail(str(exc))
            return
        if data is not None:
            offset = player.position
            for first, last in self.playback_ranges:
                count = last - first + 1
                if offset < count:
                    number = first + offset
                    break
                offset -= count
            with QSignalBlocker(self.cursor):
                self.cursor.setValue(number)
            self.frame_played(number, data)
            if first_frame:
                self.notice("音频播放失败，正在播放画面。" if player.audio_error else "正在播放剪辑预览。")
                self.notice_detail(player.audio_error)
        if player.finished:
            self.stop()
            self.notice("播放结束。")

    def stop(self):
        if self.playback is not None:
            self.playback_timer.stop()
            self.playback.close()
            self.playback = None
            self._set_transport_playing(False)
            self.clear_status()
            self.notice_detail("")
            self.playback_stopped()

    def _seek_from_player(self, value):
        if self.session.frames is not None and value != self.cursor.value():
            self.cursor.setValue(value)

    def close_fullscreen(self):
        if self.fullscreen_player is not None:
            self.fullscreen_player.reject()

    def offer_thumbnail(self, pixmap):
        if self.frame_view.original is None:
            self.frame_view.set_picture(pixmap)

    def set_position(self, number, elapsed, duration):
        with QSignalBlocker(self.player_seek):
            self.player_seek.setValue(number)
        self.player_time_current.setText(timecode(elapsed))
        self.player_time_short.setText(self.player_time_current.text().split(".")[0])
        self.player_time_total.setText(timecode(duration))

    def set_range(self, count):
        with QSignalBlocker(self.player_seek):
            self.player_seek.setRange(1, count)

    def enable_indexed_controls(self):
        for widget in (self.previous_frame_button, self.next_frame_button, self.play_button, self.player_seek):
            widget.setEnabled(True)
