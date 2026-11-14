"""Waveform navigation and audio audition for an edit session."""
import sys
from collections import OrderedDict
from stat import S_ISREG
from time import monotonic

from PySide6.QtCore import QObject, Qt, QTimer, Slot
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem, QMenu, QSizePolicy, QVBoxLayout, QWidget

from . import icons
from .editing import audio_preview, length, render_preview_wav, slice_track
from .editor_controls import _button, _icon_label, _line, _spin, _text, _text_button
from .motion import hide_vertical_scroll_bar
from .theme import C
from .wave_view import WaveView


class AudioWorkspace(QObject):
    """Own waveform controls, audio cursor, preview cache and Windows playback."""

    def __init__(self, parent, layout, session, tools, scratch, *, linked_video,
                 run_task, task_scope, is_active, is_closing, notice, status,
                 report_missing, can_drop, drop_media, edit_command, actions, apply_selected_volume):
        super().__init__(parent)
        self.session, self.tools = session, tools
        self.linked_video = linked_video
        self.run_task, self.task_scope = run_task, task_scope
        self.is_active, self.is_closing = is_active, is_closing
        self.notice, self.status, self.report_missing = notice, status, report_missing
        self.can_drop, self.drop_media = can_drop, drop_media
        self.actions, self.edit_command = actions, edit_command
        self.apply_selected_volume = apply_selected_volume
        self.audio_range_initialized = False
        self.audio_available = False
        self.wave_track = ()
        self.wave_pending = False
        self.wave_cache = OrderedDict()
        self.listen_wav = scratch / "listen.wav"
        self.playing = False
        self.listen_requested = False
        self.audio_clock = None
        self.audio_timer = QTimer(self, interval=30)
        self.audio_timer.timeout.connect(self._audio_tick)
        self.wave_reload = QTimer(self, singleShot=True)
        self.wave_reload.timeout.connect(self._reload_audio_wave)
        self._build_view(parent, layout)
        self._build_audio_tools()

    def enter(self, media_started):
        self.refresh_track(reset=not self.audio_range_initialized)
        if media_started and self.session.audio_track():
            self.load_preview()

    def resume(self):
        if self.audio_available:
            self.load_preview()

    def suspend(self):
        self.stop()
        self.wave_reload.stop()

    def on_task_finished(self):
        self.refresh_toolbar()
        if self.task_scope() is None and self.wave_pending and self.is_active():
            self.schedule_reload()

    def schedule_reload(self):
        self.wave_reload.start()

    def _build_view(self, parent, layout):
        layout.setContentsMargins(0, 6, 0, 0)
        self.audio_view = WaveView(self.can_drop)
        self.audio_view.setText("等待预览")
        self.audio_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_view.setMinimumHeight(140)
        self.audio_view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.audio_view.setStyleSheet("")
        self.audio_view.setObjectName("waveCanvas")
        self.audio_view.media_dropped.connect(self.drop_media)
        self.audio_view.trim_changed.connect(self._update_audio_overflow)
        self.audio_view.window_requested.connect(self._focus_audio_window)
        self.audio_view.range_selected.connect(self._wave_selected)
        self.audio_view.seek.connect(self.place_cursor)
        self.audio_view.view_changed.connect(self.refresh_toolbar)
        self.audio_view.context_requested.connect(self._show_context_menu)
        self.audio_view.edit_command.connect(self.edit_command)
        self.audio_context_menu = QMenu(parent)
        audio_header = QFrame()
        audio_header.setObjectName("waveToolbar")
        transport = QHBoxLayout(audio_header)
        transport.setContentsMargins(9, 3, 7, 3)
        self.audio_play = _button("播放", self._toggle_audio_playback)
        self.audio_play.setObjectName("primary")
        self.audio_play.setProperty("studioAccent", True)
        self.audio_play.setMinimumWidth(64)
        self.audio_play.setEnabled(False)
        self.audio_play.setIcon(icons.icon("play", 16, "#FFFFFF"))
        self.audio_position = _text("0:00.000", muted=True)
        transport.addWidget(_icon_label("wave", 18, C.accent.name()))
        transport.addWidget(_text("波形", name="studioSection"))
        transport.addWidget(self.audio_position)
        transport.addStretch(1)
        self.zoom_out = _button("−", lambda: self.audio_view.zoom(.5))
        self.zoom_out.setToolTip("缩小波形时间范围")
        self.zoom_in = _button("+", lambda: self.audio_view.zoom(2))
        self.zoom_in.setToolTip("放大波形时间范围")
        self.zoom_reset = _button("复位", self._reset_audio_view)
        self.zoom_reset.setToolTip("恢复波形缩放，将播放头和黑色插入线移到开头")
        self.zoom_ratio = _text("1×", muted=True)
        self.zoom_ratio.setToolTip("时间轴缩放比例。放大到采样级时显示当前时间跨度。")
        for widget in (self.zoom_out, self.zoom_ratio, self.zoom_in, self.zoom_reset):
            transport.addWidget(widget)
        transport.addWidget(self.audio_play)
        layout.addWidget(audio_header)
        layout.addWidget(self.audio_view, 1)
        self.audio_label = _text("", muted=True)
        self.audio_label.setWordWrap(True)
        layout.addWidget(self.audio_label)
        self.audio_list = QListWidget()
        self.audio_list.setMaximumHeight(72)
        self.audio_list.setObjectName("audioClips")
        self.audio_list.setToolTip("组成当前音轨的片段")
        self.audio_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hide_vertical_scroll_bar(self.audio_list)
        layout.addWidget(self.audio_list)

    def _build_audio_tools(self):
        self.audio_tools = QFrame()
        self.audio_tools.setObjectName("audioSelectionCard")
        self.audio_tools.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        audio = QVBoxLayout(self.audio_tools)
        audio.setContentsMargins(10, 8, 10, 9)
        audio.setSpacing(6)
        audio_title = QHBoxLayout()
        audio_title.setSpacing(7)
        audio_title.addWidget(_icon_label("wave", 18, C.accent.name()))
        audio_title.addWidget(_text("音频选区", name="cardTitle"))
        audio_title.addStretch(1)
        audio.addLayout(audio_title)
        self.audio_first, self.audio_last, self.audio_cursor = (_spin(seconds=True) for _ in range(3))
        self.audio_selection_note = _text("拖动波形以选择片段", name="audioSelectionStatus")
        self.audio_selection_note.setToolTip("在波形上拖动鼠标即可选择音频片段")
        selection_status = QHBoxLayout()
        selection_status.addWidget(self.audio_selection_note, 1)
        self.edit_audio_range = _button("手动输入选区", self._show_audio_range_fields)
        self.edit_audio_range.setObjectName("secondaryAction")
        self.edit_audio_range.setFixedHeight(27)
        self.edit_audio_range.setCheckable(True)
        selection_status.addWidget(self.edit_audio_range)
        audio.addLayout(selection_status)

        self.audio_range_fields = QWidget()
        range_fields = QGridLayout(self.audio_range_fields)
        range_fields.setContentsMargins(0, 0, 0, 0)
        range_fields.setHorizontalSpacing(8)
        range_fields.setVerticalSpacing(4)
        for column, (title, control) in enumerate((("起点", self.audio_first), ("终点", self.audio_last))):
            field_label = _text(title, name="audioFieldLabel")
            range_fields.addWidget(field_label, 0, column)
            range_fields.addWidget(control, 1, column)
        range_fields.setColumnStretch(0, 1)
        range_fields.setColumnStretch(1, 1)
        self.audio_range_fields.setVisible(False)
        self.audio_first.valueChanged.connect(self._update_audio_marker)
        self.audio_last.valueChanged.connect(self._update_audio_marker)
        audio.addWidget(self.audio_range_fields)
        self.audio_insert_row = QWidget()
        self.audio_insert_row.setObjectName("audioInsertRow")
        insert_line = QHBoxLayout(self.audio_insert_row)
        insert_line.setContentsMargins(0, 0, 0, 0)
        insert_line.setSpacing(8)
        insert_label = _text("插入位置", name="audioFieldLabel")
        insert_line.addWidget(insert_label)
        insert_line.addStretch(1)
        self.audio_cursor.setFixedWidth(128)
        insert_line.addWidget(self.audio_cursor)
        audio.addWidget(self.audio_insert_row)
        self.overflow_label = _text("")
        self.overflow_label.setWordWrap(True)
        self.overflow_label.setStyleSheet("color: #a62c36; font-weight: 650;")
        self.overflow_jump = _text_button("定位", self._focus_audio_overflow)
        self.overflow_jump.setToolTip("跳到标红的待删区域")
        overflow_row = QHBoxLayout()
        overflow_row.addWidget(self.overflow_label, 1)
        overflow_row.addWidget(self.overflow_jump)
        audio.addLayout(overflow_row)
        self.overflow_label.hide()
        self.overflow_jump.hide()
        self.audio_hint = _text("右键波形可剪辑或插入音频。", muted=True)
        self.audio_hint.setToolTip("右键波形可剪辑、删除选区或插入队列中的音频。")
        self.audio_hint.hide()
        self.volume = _spin()
        self.volume.setRange(0, 300)
        self.volume.setValue(100)
        self.volume.setSuffix("%")
        self.volume.setToolTip("仅调整选区。100% 为原音量，50% 为一半，0% 为静音。")
        self.apply_volume = _button("应用到选区", self.apply_selected_volume)
        self.apply_volume.setObjectName("secondaryAction")
        self.apply_volume.setFixedHeight(29)
        self.apply_volume.setToolTip(self.volume.toolTip())
        audio.addLayout(_line(_text("选区音量"), self.volume, self.apply_volume))
        self.audio_tools.hide()

    def refresh_track(self, *, reset=False):
        track = self.session.audio_track()
        total = length(track)
        video_end = self.session.video_duration()
        self.audio_view.set_overflow((video_end, total) if self.session.frames and total - video_end > .0005 else None)
        empty = length(self.session.video) / self.session.frames.fps if self.session.frames and not track else 0
        self.audio_view.empty_duration = empty or (1.0 if not track else 0)
        if self.audio_view.insertion is not None:
            self.audio_view.insertion = min(self.audio_view.insertion, max(total, empty))
        self.audio_available = total > 0
        for field in (self.audio_first, self.audio_last):
            field.setRange(0, total)
        self.audio_cursor.setRange(0, max(total, empty))
        if reset:
            self.audio_first.setValue(0)
            self.audio_last.setValue(0)
        self.audio_range_initialized = bool(track)
        self.audio_list.clear()
        for clip in track:
            label = f"{clip.path.name}  ·  {clip.start:.3f}–{clip.end:.3f} 秒  ·  音量 {clip.volume:.0%}" if clip.path else f"静音  ·  {clip.length:.3f} 秒"
            item = QListWidgetItem(label)
            item.setToolTip(label)
            self.audio_list.addItem(item)
        self.audio_hint.setText("单击波形标记黑线，再把视频或音频拖入音轨。剪切、删除会同步裁掉对应画面。"
                                if self.linked_video else "单击波形标记黑线，再把视频或音频拖入音轨。")
        self.audio_label.setText(f"{total:.3f} 秒 · 拖动选区 · 单击定位并清除选区 · 单次预览最多 30 秒"
                                 if track else "当前无音轨，可插入音频文件。")
        self._update_audio_marker()
        if tuple(track) != self.wave_track:
            self.wave_track = tuple(track)
            self.wave_pending = bool(track)
            self.audio_view.set_waveform(None, None)
            self.audio_view.setText("正在更新波形…" if track else "暂无音轨")

    def _update_audio_overflow(self):
        view = self.audio_view
        active = view.overflow is not None
        self.overflow_label.setVisible(active)
        self.overflow_jump.setVisible(active)
        if active:
            excess = view.overflow[1] - view.overflow[0]
            marked = view.trim_mark[1] - view.trim_mark[0]
            remaining = max(0.0, excess - marked)
            self.overflow_label.setText(
                f"超出 {excess:.3f} 秒 · 标红 {marked:.3f} 秒\n删除标红后还差 {remaining:.3f} 秒")

    def _focus_audio_overflow(self):
        view = self.audio_view
        if view.trim_mark:
            position = view.trim_mark[0]
            if view.loaded_interval and view.loaded_interval[0] <= position < view.loaded_interval[1]:
                view.reset_zoom()
            else:
                start = max(0, position - 8)
                self._focus_audio_window(start, start + 30)

    def _focus_audio_window(self, start, end):
        total = length(self.session.audio_track())
        span = min(total, end - start)
        if span > 0:
            start = max(0, min(start, total - span))
            self._load_audio_wave(focus=(start, start + span))

    def _reset_audio_view(self):
        self.place_cursor(0.0)
        loaded = self.audio_view.loaded_interval
        if self.audio_available and (not loaded or loaded[0] > 0):
            self._focus_audio_window(0, 30)
        else:
            self.audio_view.reset_zoom()

    @Slot(float, float)
    def _wave_selected(self, start, end):
        self.audio_first.setValue(start)
        self.audio_last.setValue(end)
        self._seek_audio(start)

    def _show_audio_range_fields(self, *_):
        selected = self.audio_last.value() > self.audio_first.value()
        visible = self.edit_audio_range.isChecked() or selected
        self.audio_range_fields.setVisible(visible)
        self.edit_audio_range.setVisible(not selected)
        self.edit_audio_range.setText("收起选区输入" if self.edit_audio_range.isChecked() else "手动输入选区")

    def _update_audio_marker(self):
        start, end = self.audio_first.value(), self.audio_last.value()
        self.audio_view.selection = (start, end) if end > start else None
        self.apply_volume.setEnabled(end > start)
        self.audio_selection_note.setText(
            f"已选中 {end - start:.3f} 秒" if end > start else "拖动波形以选择片段")
        self._show_audio_range_fields()
        self.audio_view.update()

    def place_cursor(self, position):
        if not self.audio_available and not self.session.frames:
            position = 0.0
        self.audio_first.setValue(position)
        self.audio_last.setValue(position)
        self.audio_view.insertion = position
        self._seek_audio(position)

    def selection(self):
        first, last = self.audio_first.value(), self.audio_last.value()
        track = self.session.audio_track()
        total = length(track)
        if last > first and abs(last - total) <= .0005:
            last = total
        return first, last, track

    def load_preview(self):
        self._load_audio_wave()

    def _reload_audio_wave(self):
        if (self.is_active()
                and self.wave_pending):
            self.load_preview()

    def focus_selection(self):
        start, end, _ = self.selection()
        if end <= start:
            self.notice("请选一段音频。")
            return
        loaded = self.audio_view.loaded_interval
        if loaded and loaded[0] <= start and end <= loaded[1]:
            self.audio_view.show_interval(start, end)
        else:
            self._load_audio_wave(focus=(start, end))

    def _load_audio_wave(self, *, focus=None, on_ready=None):
        if self.task_scope() is not None:
            self.wave_pending = self.audio_available
            return
        self.wave_pending = False
        track = self.session.audio_track()
        loaded = self.audio_view.loaded_interval
        start = focus[0] if focus else (loaded[0] if loaded else self.audio_view.playhead)
        total = length(track)
        if not focus and not loaded and self.audio_view.overflow:
            boundary = self.audio_view.overflow[0]
            if not start <= boundary < start + 30:
                start = max(0, boundary - 20)
        start = min(start, max(0, total - 30))
        end = min(total, start + 30)
        selected = tuple(slice_track(track, start, end))
        if not selected:
            self.notice("请选一段音频。")
            return
        files = []
        for path in dict.fromkeys(clip.path for clip in selected if clip.path is not None):
            try:
                stamp = path.stat()
                if not S_ISREG(stamp.st_mode):
                    raise FileNotFoundError(path)
            except (FileNotFoundError, NotADirectoryError):
                self._audio_wave_error(f"找不到音频文件「{path.name}」，请重新导入。", str(path))
                self.report_missing(path)
                return
            except OSError as exc:
                self._audio_wave_error(f"无法读取音频文件「{path.name}」。", str(exc))
                return
            files.append((path, stamp.st_mtime_ns, stamp.st_size))
        key = selected, tuple(files)
        interval = start, end
        if waveform := self.wave_cache.get(key):
            self.wave_cache.move_to_end(key)
            if self.audio_view.waveform is None and self.status().text() == self.audio_view.text():
                self.status().clear()
                self.status().setToolTip("")
            self._audio_ready(waveform, interval, focus, on_ready)
            return

        def ready(waveform):
            if waveform is None:
                self._audio_wave_error("这个区间没有可显示的音频。")
                return
            self.wave_cache[key] = waveform
            if len(self.wave_cache) > 2:
                self.wave_cache.popitem(last=False)
            self._audio_ready(waveform, interval, focus, on_ready)

        self.run_task("正在生成音频波形…", lambda scope: audio_preview(selected, self.tools, scope), ready,
                  on_error=lambda error: self._audio_wave_error(
                      "波形加载已取消。" if error == "任务已取消" else "波形加载失败。右键可刷新。", error))

    def _audio_wave_error(self, message, detail=""):
        self.wave_pending = False
        self.audio_view.set_waveform(None, None)
        self.audio_view.setText(message)
        self.audio_view.setToolTip(detail)
        self.notice(message)
        self.status().setToolTip(detail)

    def _audio_ready(self, waveform, interval, focus, on_ready):
        self.wave_pending = False
        if waveform is not self.audio_view.waveform or interval != self.audio_view.loaded_interval:
            self.audio_view.set_waveform(waveform, interval)
        if waveform:
            if not interval[0] <= self.audio_view.playhead < interval[1]:
                self._set_audio_position(interval[0])
            if focus:
                self.audio_view.show_interval(focus[0], min(focus[1], interval[1]))
        self.audio_view.setToolTip("单击放置黑色插入线，拖动选取，右键打开编辑菜单。Ctrl + 滚轮缩放。")
        if on_ready and waveform and self.is_active():
            on_ready()

    def refresh_toolbar(self):
        view = self.audio_view
        loaded = view.loaded_interval
        self.audio_play.setEnabled(bool(self.audio_available and sys.platform == "win32"
                                        and (self.task_scope() is None or self.listen_requested)))
        span = view.interval[1] - view.interval[0] if loaded else 0
        ratio = (loaded[1] - loaded[0]) / span if span else 1
        factor = f"{ratio:,.0f}" if ratio >= 10 else f"{ratio:.1f}".rstrip("0").rstrip(".")
        self.zoom_ratio.setText(f"采样点 · {span * 1000:.2f} ms" if view.sample_spacing() >= 3 else f"{factor}×")
        self.zoom_in.setEnabled(bool(loaded and span > view.minimum_span() * 1.001))
        self.zoom_out.setEnabled(ratio > 1.001)
        self.zoom_reset.setEnabled(bool(self.audio_available or self.session.frames) and self.task_scope() is None)

    def _seek_audio(self, position):
        self.stop()
        self._set_audio_position(position)
        self.audio_cursor.setValue(position)

    def _set_audio_position(self, position):
        self.audio_view.playhead = position
        self.audio_view.reveal(position)
        self.audio_view.update()
        self.audio_position.setText(WaveView._time_label(position, 3))

    def _toggle_audio_playback(self):
        if self.playing or self.listen_requested:
            self.stop()
            self.notice("已暂停。")
            return
        if self.task_scope() is not None or not self.audio_available or sys.platform != "win32":
            return
        if not self.audio_view.loaded_interval:
            self._load_audio_wave(on_ready=self._toggle_audio_playback)
            return
        first, last = self.audio_view.loaded_interval
        start = self.audio_view.playhead
        if not first <= start < last - .001:
            start = first
        select_start, select_end, track = self.selection()
        end = min(last, select_end) if select_start <= start < select_end else last
        self.play_range(start, end, track)

    def play_audio_selection(self):
        start, end, track = self.selection()
        if end <= start or self.task_scope() is not None or sys.platform != "win32":
            return
        loaded = self.audio_view.loaded_interval
        if not loaded or not loaded[0] <= start < loaded[1]:
            self._load_audio_wave(focus=(start, end), on_ready=self.play_audio_selection)
            return
        self.play_range(start, min(end, start + 30), track)

    def play_range(self, start, end, track):
        if self.task_scope() is not None or self.is_closing() or sys.platform != "win32":
            return
        selected = slice_track(track, start, end)
        if not selected:
            return
        self.run_task("正在准备音频…",
                  lambda scope: render_preview_wav(selected, self.listen_wav, self.tools, scope),
                  lambda _: self._audio_playback_ready(start, end))
        self.listen_requested = True
        self.audio_play.setText("暂停")
        self.refresh_toolbar()

    def _audio_playback_ready(self, start, end):
        if not self.listen_requested:
            return
        self.listen_requested = False
        import winsound

        try:
            winsound.PlaySound(str(self.listen_wav), winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
        except RuntimeError as exc:
            self.audio_play.setText("播放")
            self.notice(f"无法播放音频：{exc}")
            return
        self.playing = True
        self.audio_clock = start, end, monotonic()
        self._set_audio_position(start)
        self.audio_timer.start()
        self.notice("正在播放音频。")

    def _audio_tick(self):
        if self.audio_clock is None:
            return
        start, end, clock = self.audio_clock
        position = min(end, start + monotonic() - clock)
        self._set_audio_position(position)
        if position >= end:
            self.stop()
            self.notice("播放结束。")

    def stop(self):
        active = self.playing or self.listen_requested
        if self.listen_requested and self.task_scope() is not None:
            self.task_scope().cancel()
        self.listen_requested = False
        self.audio_timer.stop()
        if self.playing:
            import winsound
            start, end, clock = self.audio_clock
            self._set_audio_position(min(end, start + monotonic() - clock))
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError:
                pass
            self.playing = False
        self.audio_clock = None
        self.audio_play.setText("播放")
        self.refresh_toolbar()
        if active:
            self.status().clear()

    def _show_context_menu(self, position, global_position):
        menu = self.audio_context_menu
        menu.clear()
        start, end, track = self.selection()
        ready = self.task_scope() is None
        mark = self.audio_view.trim_mark
        on_mark = self.audio_view.marked_at(position)
        if mark:
            if on_mark:
                action = menu.addAction(f"删除标红音频（{mark[1] - mark[0]:.3f} 秒）", self.actions["delete_marked_audio"])
                action.setEnabled(ready)
            else:
                menu.addAction("把待删范围移到这里", lambda: self.audio_view.move_trim_mark(position)).setEnabled(ready)
            menu.addSeparator()
        selected = ready and end > start and bool(track)
        playable = ready and bool(track) and sys.platform == "win32"
        if end > start:
            menu.addAction("播放选区", self.play_audio_selection).setEnabled(playable)
        else:
            menu.addAction("从这里播放", lambda: self.play_from(position)).setEnabled(playable)
        menu.addAction("缩放到选区", self.focus_selection).setEnabled(selected)
        menu.addAction("查看采样点", lambda: self.audio_view.zoom_to_samples(position)).setEnabled(
            self.audio_view.waveform is not None)
        if track and not self.audio_view.loaded_interval:
            menu.addAction("刷新波形", self.load_preview).setEnabled(ready)
        menu.addSeparator()
        menu.addAction("复制\tCtrl+C", self.actions["copy_audio"]).setEnabled(selected)
        menu.addAction("剪切\tCtrl+X", self.actions["cut_audio"]).setEnabled(selected)
        if not on_mark:
            menu.addAction("删除", self.actions["delete_audio"]).setEnabled(selected)
        menu.addAction("粘贴\tCtrl+V", self.actions["paste_audio"]).setEnabled(ready and bool(self.session.audio_clipboard))
        menu.addAction("插入音频…", self.actions["insert_audio"]).setEnabled(ready)
        menu.addAction("撤销\tCtrl+Z", self.actions["undo"]).setEnabled(ready and bool(self.session.history))
        menu.popup(global_position)

    def play_from(self, position):
        self.place_cursor(position)
        track = self.session.audio_track()
        end = min(length(track), position + 30)
        self.play_range(position, end, track)

    def selection_start(self):
        return self.audio_first.value()

    def insertion_position(self):
        return self.audio_cursor.value()

    def selected_volume(self):
        return self.volume.value() / 100

    def marked_range(self):
        return self.audio_view.trim_mark
