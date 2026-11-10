"""Allow-listed Agent tools over the original FrameFlow queue and export worker."""

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from stat import S_ISREG
from uuid import uuid4

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QFileDialog

from .agent_ui import PlanCard
from .media import (
    AudioInfo, Export, ImageInfo, VideoInfo, VIDEO_RESOLUTIONS, filename_problem,
    hardware_acceleration_choices, output_extension,
)


def spec(name, description, properties=None, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": list(required),
                       "additionalProperties": False},
    }}


TEXT = {"type": "string", "maxLength": 320}
ID = {"type": "string", "maxLength": 64, "description": "Opaque FrameFlow queue ID, not a path."}
CODECS = {"mp4": "h264", "mp4_hevc": "hevc", "mkv": "h264", "mkv_hevc": "hevc",
          "mov": "h264", "webm": "vp9", "avi": "mpeg4"}
PRESETS = {name.lower(): edge for name, edge in VIDEO_RESOLUTIONS} | {"original": 0}
PICTURE_QUALITY_FORMATS = {"jpg", "webp", "avif"}


@dataclass(frozen=True)
class PlanEntry:
    key: str
    source: Path
    info: object
    options: Export
    source_stamp: tuple
    destination_stamp: tuple


@dataclass
class Plan:
    id: str
    entries: tuple[PlanEntry, ...]
    card: PlanCard
    rows: dict
    state: str = "awaiting_confirmation"


class ToolError(ValueError):
    pass


class AgentTools(QObject):
    specs = (
        spec("list_media", "List media already in the queue, without local paths.",
             {"offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
        spec("get_media_info", "Read media properties and compatible output settings.", {"id": ID}, ("id",)),
        spec("get_current_selection", "Get the selected media and its queue ID."),
        spec("get_task_status", "Read actual conversion progress and results.", {"task_id": ID}),
        spec("select_media", "Select a media item in the shared queue.", {"id": ID}, ("id",)),
        spec("set_output_format", "Set a supported format. To output MP4 H.265 use format mp4 and codec hevc.",
             {"id": ID, "format": TEXT, "codec": {"type": "string", "enum": sorted(set(CODECS.values()))}},
             ("id", "format")),
        spec("set_resolution", "Set a preset, or an exact long edge in pixels. Source aspect ratio is preserved.",
             {"id": ID, "preset": {"type": "string", "enum": list(PRESETS)},
              "long_edge": {"type": "integer", "minimum": 0, "maximum": 8192}}, ("id",)),
        spec("set_quality", "Set video CRF/CQ (lower is clearer), lossy image quality, or a best-effort video size target.",
             {"id": ID, "quality": {"type": "integer", "minimum": 1, "maximum": 100},
              "target_mb": {"type": "integer", "minimum": 0, "maximum": 1000,
                            "description": "Target size in decimal MB. Use 0 to clear the target."}}, ("id",)),
        spec("set_output_directory", "Use the source directory, the user-approved directory, or an exact already-authorized path. "
             "Ask the user to pick a folder with the folder button next to the attachment button if a path is rejected.",
             {"id": ID, "directory": {"type": "string", "maxLength": 2048,
                                      "description": "source, approved, or the exact authorized directory."}}, ("id", "directory")),
        spec("set_output_filename", "Set a filename for one item. A matching extension is optional.",
             {"id": ID, "filename": {"type": "string", "maxLength": 220}}, ("id", "filename")),
        spec("prepare_conversion", "Show a plan for these queue IDs with their current settings and wait for the user to confirm.",
             {"ids": {"type": "array", "items": ID, "minItems": 1, "maxItems": 100}}, ("ids",)),
        spec("start_conversion", "Check the confirmation state for a plan. Only the user's Confirm button can start it.",
             {"plan_id": ID}, ("plan_id",)),
        spec("cancel_conversion", "Cancel a running Agent conversion by its task ID.",
             {"task_id": ID}, ("task_id",)),
    )
    _schemas = {item["function"]["name"]: item["function"]["parameters"] for item in specs}

    def __init__(self, window, formats_for):
        super().__init__(window)
        self.window = window
        self.page = window.agent_page
        self.formats_for = formats_for
        self.approved_directory = None
        self.pending = None
        self.active = None
        self.active_scope = None
        self.recent = {}
        self.page.directory_requested.connect(self.choose_directory)
        events = window.events
        events.phase.connect(self._phase)
        events.progress.connect(self._progress)
        events.exported.connect(self._exported)
        events.completed.connect(self._completed)

    def dispatch(self, name, arguments):
        schema = self._schemas.get(name)
        if schema is None:
            return {"ok": False, "error": "未知工具。"}
        try:
            values = json.loads(arguments)
            if not isinstance(values, dict):
                raise ToolError("参数必须是 JSON 对象。")
            if set(values) - schema["properties"].keys() or set(schema["required"]) - values.keys():
                raise ToolError("参数包含未知字段或缺少必填字段。")
            for key, value in values.items():
                self._validate(key, value, schema["properties"][key])
            data = getattr(self, name)(**values)
        except ToolError as exc:
            return {"ok": False, "error": str(exc)}
        except (ValueError, TypeError, RecursionError):
            return {"ok": False, "error": "参数格式不合法。"}
        except OSError:
            return {"ok": False, "error": "无法读取相关媒体或输出目录，请在本机检查文件。"}
        return {"ok": True, "result": data}

    @classmethod
    def _validate(cls, key, value, rule):
        kind = rule["type"]
        if kind == "string":
            valid = isinstance(value, str) and 0 < len(value) <= rule.get("maxLength", 320)
            valid = valid and ("enum" not in rule or value in rule["enum"])
        elif kind == "integer":
            valid = type(value) is int and rule.get("minimum", -8192) <= value <= rule.get("maximum", 2**31-1)
        else:
            valid = isinstance(value, list) and rule["minItems"] <= len(value) <= rule["maxItems"]
            if valid:
                for item in value:
                    cls._validate(key, item, rule["items"])
        if not valid:
            raise ToolError(f"参数 {key} 的类型或取值不受支持。")

    def _job(self, id, *, editable=False):
        job = self.window.jobs.get(id)
        if job is None:
            raise ToolError("这个媒体已经不在队列中，请重新读取队列。")
        if editable:
            if not self.window.tools or not job.info or not job.output_format:
                raise ToolError("媒体尚未就绪或没有可用输出格式。")
            if self.window.scope is not None:
                raise ToolError("批量转换进行中，请等待任务结束。")
        return job

    @staticmethod
    def _summary(job):
        kind = ("video" if isinstance(job.info, VideoInfo) else
                "audio" if isinstance(job.info, AudioInfo) else
                "image" if isinstance(job.info, ImageInfo) else "unknown")
        return {"id": job.key, "name": job.path.name, "kind": kind, "queued": job.queued,
                "state": job.state, "format": job.output_format}

    def list_media(self, offset=0, limit=50):
        jobs = self.window.jobs
        return {"total": len(jobs), "offset": offset,
                "items": [self._summary(job) for job in list(jobs.values())[offset:offset + limit]]}

    def get_media_info(self, id):
        job = self._job(id)
        result = self._summary(job)
        if job.info:
            result["media"] = {name: getattr(job.info, name)
                               for name in ("codec", "duration", "size", "width", "height", "fps",
                                            "audio_codec", "sample_rate", "channels", "bitrate")
                               if hasattr(job.info, name)}
            result["formats"] = [{"id": key, "label": title} for key, title in self.formats_for(job.info)]
        if self.window.tools and job.output_format:
            export = self.window._job_export(job)
            result["output"] = {"format": export.format, "long_edge": export.max_edge, "quality": export.quality,
                                "filename": export.output_stem, "target_bytes": export.target_bytes,
                                "agent_directory_selected": self.approved_directory is not None}
        return result

    def get_current_selection(self):
        job = self.window._selected()
        return self._summary(job) if job else None

    def select_media(self, id):
        job = self._job(id)
        self.window.list.setCurrentItem(self.window.items[id])
        self.window.preview_files.setCurrentItem(self.window.preview_items[id])
        return self._summary(job)

    def _apply(self, job, **changes):
        options = replace(self.window._job_export(job), **changes)
        self.window._apply_agent_options(job, options)
        return self.get_media_info(job.key)["output"]

    def set_output_format(self, id, format, codec=None):
        job = self._job(id, editable=True)
        kind = format.lower()
        if codec == "hevc" and kind in {"mkv", "mp4"}:
            kind += "_hevc"
        if (codec and CODECS.get(kind) != codec) or kind not in dict(self.formats_for(job.info)):
            raise ToolError("此媒体不支持所选容器和编码组合。")
        available = dict(hardware_acceleration_choices(self.window.tools.ffmpeg, kind))
        current = self.window._job_export(job)
        acceleration = current.video_acceleration if current.video_acceleration in available else "cpu"
        return self._apply(job, format=kind, video_acceleration=acceleration)

    def set_resolution(self, id, preset=None, long_edge=None):
        job = self._job(id, editable=True)
        if (preset is None) == (long_edge is None):
            raise ToolError("请选择一个分辨率预设或指定最长边。")
        if job.output_format not in CODECS and job.output_format != "gif" and not isinstance(job.info, ImageInfo):
            raise ToolError("当前输出格式没有分辨率参数。")
        return self._apply(job, max_edge=PRESETS[preset] if preset is not None else long_edge)

    def set_quality(self, id, quality=None, target_mb=None):
        job = self._job(id, editable=True)
        options = {}
        if quality is not None:
            if job.output_format in CODECS and job.output_format != "avi":
                if not 18 <= quality <= 34:
                    raise ToolError("视频质量范围为 18–34，数值越小越清晰。")
            elif job.output_format in PICTURE_QUALITY_FORMATS:
                if not 40 <= quality <= 100:
                    raise ToolError("图片质量范围为 40–100。")
            else:
                raise ToolError("此格式没有可调的画质参数。")
            options["quality"] = quality
        if target_mb is not None:
            if job.output_format not in CODECS:
                raise ToolError("此工具的目标体积适用于常规视频格式。")
            options["target_bytes"] = target_mb * 1_000_000
        if not options:
            raise ToolError("请指定画质或视频目标体积。")
        return self._apply(job, **options)

    def choose_directory(self):
        path = QFileDialog.getExistingDirectory(self.page, "授权 Agent 使用此输出目录",
                                                str(self.approved_directory or Path.home()))
        if path:
            try:
                directory = Path(path).resolve(strict=True)
                if not directory.is_dir():
                    raise OSError
            except OSError:
                self.page.set_activity("无法使用该输出目录，请重新选择。")
                return
            self.approved_directory = directory
            self.page.directory_button.setToolTip(str(directory))
            self.page.set_activity("已授权输出目录。可以请 Agent 把输出位置设为这个目录。")

    def set_output_directory(self, id, directory):
        job = self._job(id, editable=True)
        source = job.path.parent
        existing = self.window._job_export(job).directory or source
        allowed = [source, existing]
        if self.approved_directory is not None:
            allowed.append(self.approved_directory)
        if directory == "source":
            chosen = source
        elif directory == "approved":
            chosen = self.approved_directory
        else:
            candidate = os.path.normcase(os.path.normpath(directory))
            # Compare text first. Untrusted paths are never opened or resolved.
            chosen = next((path for path in allowed if os.path.normcase(os.path.normpath(path)) == candidate), None)
        if chosen is None:
            raise ToolError("该目录尚未授权。请点击输入框左下方的文件夹按钮选择目录。")
        return self._apply(job, directory=chosen)

    def set_output_filename(self, id, filename):
        job = self._job(id, editable=True)
        extension = "." + output_extension(job.output_format)
        if filename.lower().endswith(extension):
            filename = filename[:-len(extension)]
        if problem := filename_problem(filename):
            raise ToolError(problem)
        return self._apply(job, output_stem=filename)

    def _entry(self, job):
        if not job.queued:
            raise ToolError("这项是已生成的成品，请先在队列中加入待转换。")
        options = self.window._job_export(job)
        if options.format not in dict(self.formats_for(job.info)):
            raise ToolError("当前输出格式与媒体类型不兼容。")
        if options.output_stem and (problem := filename_problem(options.output_stem)):
            raise ToolError(problem)
        source = job.path.stat()
        if not S_ISREG(source.st_mode):
            raise ToolError("队列中的源文件已失效。")
        directory = (options.directory or job.path.parent).resolve(strict=True)
        if not directory.is_dir():
            raise ToolError("保存目录已失效，请重新选择。")
        stamp = directory.stat()
        return PlanEntry(job.key, job.path, job.info, replace(options, directory=directory),
                         (source.st_dev, source.st_ino, source.st_size, source.st_mtime_ns),
                         (stamp.st_dev, stamp.st_ino))

    def prepare_conversion(self, ids):
        if self.pending:
            raise ToolError("已有待确认的计划。请先确认或取消现有计划。")
        entries = tuple(self._entry(self._job(id, editable=True)) for id in dict.fromkeys(ids))
        rows = []
        for item in entries:
            options = item.options
            title = dict(self.formats_for(item.info))[options.format]
            edge = next((label for label, size in VIDEO_RESOLUTIONS if size == options.max_edge),
                        f"最长边 {options.max_edge} px") if options.max_edge else "原始尺寸"
            output = f"{options.output_stem or item.source.stem}.{output_extension(options.format)}"
            quality = (f" · CRF/CQ {options.quality}" if options.quality and options.format in CODECS
                       else f" · 质量 {options.quality}%" if options.quality and options.format in PICTURE_QUALITY_FORMATS
                       else "")
            target = f" · 目标 {options.size_limit / 1_000_000:.3g} MB" if options.size_limit else ""
            rows.append((item.key, f"{item.source.name} → {output}\n{title} · {edge}{quality}{target}",
                         str(options.directory)))
        card = PlanCard(rows)
        plan = Plan(uuid4().hex, entries, card, {
            entry.key: {"id": entry.key, "name": entry.source.name, "state": "等待确认", "percent": 0}
            for entry in entries
        })
        card.confirm_requested.connect(lambda: self._confirm(plan))
        card.cancel_requested.connect(lambda: self._cancel_card(plan))
        self.pending = plan
        self.page.add_history_widget(card)
        return {"plan_id": plan.id, "status": plan.state, "files": len(entries)}

    def start_conversion(self, plan_id):
        plan = self.pending if self.pending and self.pending.id == plan_id else self._find_task(plan_id)
        if plan is None:
            raise ToolError("找不到转换计划。")
        return {"task_id": plan.id, "status": plan.state, "user_confirmation_required": plan is self.pending}

    def _confirm(self, plan):
        if plan is not self.pending:
            return
        if self.window.scope is not None:
            self.page.set_activity("当前有转换任务，请等任务结束后再确认。")
            return
        try:
            current = tuple(self._entry(self._job(entry.key, editable=True)) for entry in plan.entries)
        except (ToolError, OSError):
            current = ()
        if current != plan.entries:
            self._discard("计划已失效。媒体、目录或参数发生变化，请重新生成。")
            return
        tasks = [(entry.key, entry.source, entry.info, entry.options) for entry in plan.entries]
        if not self.window._start_batch(tasks):
            self.page.set_activity("当前有转换任务，请等任务结束后再确认。")
            return
        self.pending = None
        self.active = plan
        self.active_scope = self.window.scope
        plan.state = "running"
        plan.card.started()
        for key in plan.rows:
            self._update(key, "等待转换", 0)

    def _discard(self, reason):
        if self.pending:
            self.pending.card.finished(reason)
            self.pending = None

    def clear_pending(self):
        self._discard("计划已取消。")
        self.recent.clear()
        return self.active.card if self.active else None

    def _find_task(self, task_id):
        if self.active and self.active.id == task_id:
            return self.active
        return self.recent.get(task_id)

    def get_task_status(self, task_id=None):
        plan = self._find_task(task_id) if task_id else self.active or self.pending
        if plan is None and task_id and self.pending and self.pending.id == task_id:
            plan = self.pending
        if task_id and plan is None:
            raise ToolError("未找到该任务。")
        if plan is None and self.window.scope is not None:
            return {"status": "manual_conversion", "percent": self.window.progress.value(),
                    "items": [self._summary(self.window.jobs[key]) for key in self.window.batch_keys]}
        if plan is None and self.recent:
            plan = next(reversed(self.recent.values()))
        return {"task_id": plan.id, "status": plan.state, "items": list(plan.rows.values())} if plan else {"status": "idle"}

    def cancel_conversion(self, task_id):
        if (not self.active or self.active.id != task_id or self.window.scope is not self.active_scope):
            raise ToolError("只能取消由 Agent 确认且正在运行的任务。")
        self.window.cancel_export()
        self.active.state = "cancelling"
        self.active.card.cancel.setEnabled(False)
        return {"task_id": task_id, "status": "cancelling"}

    def _cancel_card(self, plan):
        if plan is self.pending:
            self._discard("计划已取消。")
        elif plan is self.active:
            self.cancel_conversion(plan.id)

    def _update(self, key, state, percent, *, local="", output=None):
        plan = self.active
        if plan and key in plan.rows:
            row = plan.rows[key]
            row.update(state=state, percent=percent)
            if output:
                row["output"] = output.name
            plan.card.update_row(key, local or f"{state} · {percent}%", percent)

    @Slot(str, str)
    def _phase(self, key, state):
        if self.active and key in self.active.rows:
            self._update(key, state, self.active.rows[key]["percent"])

    @Slot(str, int)
    def _progress(self, key, percent):
        if self.active and key in self.active.rows:
            row = self.active.rows[key]
            self._update(key, row["state"], max(row["percent"], percent))

    @Slot(str, object, object, str, str)
    def _exported(self, key, output, _info, note, error):
        if self.active and key in self.active.rows:
            if output:
                self._update(key, "已完成", 100, local=f"已完成 · {output.name}" + (f" · {note}" if note else ""),
                             output=output)
            else:
                self._update(key, "转换失败", self.active.rows[key]["percent"], local=f"转换失败 · {error}")

    @Slot(bool)
    def _completed(self, cancelled):
        plan = self.active
        if not plan:
            return
        for key, row in plan.rows.items():
            if row["state"] not in {"已完成", "转换失败"}:
                self._update(key, "已取消" if cancelled else "未完成", row["percent"])
        done = sum(row["state"] == "已完成" for row in plan.rows.values())
        failed = sum(row["state"] == "转换失败" for row in plan.rows.values())
        plan.state = "cancelled" if cancelled else "finished_with_errors" if done != len(plan.rows) else "finished"
        summary = f"已完成 {done} 个，失败 {failed} 个"
        plan.card.finished(f"已取消 · {summary}" if cancelled else summary)
        self.recent[plan.id] = plan
        while len(self.recent) > 8:
            self.recent.pop(next(iter(self.recent)))
        self.active = self.active_scope = None
