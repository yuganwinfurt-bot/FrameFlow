"""Batch execution, progress and job-state transitions for the desktop workspaces."""
from concurrent.futures import ThreadPoolExecutor

from .conversion_batch import run_batch
from .media import Processes


class ConversionRun:
    """Own the active batch and ordered results. Consume progress on the GUI thread."""

    def __init__(self, jobs, tools, events):
        self.jobs, self.tools, self.events = jobs, tools, events
        self.scope = None
        self.keys = ()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frameflow-export")

    def begin(self, tasks):
        self.scope = Processes()
        self.keys = tuple(key for key, _, _, _ in tasks)
        self._outputs = []
        self._finished = self._failed = 0
        for job in self.active_jobs():
            job.state, job.percent, job.detail = "等待转换", 0, ""
            job.output, job.output_info, job.output_note = None, None, ""

    def submit(self, tasks):
        self._pool.submit(
            run_batch, tasks, self.tools, self.scope,
            phase=self.events.phase.emit, progress=self.events.progress.emit,
            exported=self.events.exported.emit, completed=self.events.completed.emit,
        )

    def active_jobs(self):
        return tuple(self.jobs[key] for key in self.keys)

    def set_phase(self, key, state):
        if job := self.jobs.get(key):
            job.state = state
            return job
        return None

    def set_progress(self, key, percent):
        if job := self.jobs.get(key):
            job.percent = max(job.percent, percent)
            return job, round((self._finished * 100 + job.percent) / len(self.keys))
        return None

    def record_result(self, key, output, info, note, error):
        job = self.jobs[key]
        job.detail = error
        job.state = "已完成" if output else "转换失败"
        job.output, job.output_info, job.output_note = output, info, note
        if output:
            job.queued = True
            self._outputs.append(output)
        self._finished += 1
        self._failed += int(not output)
        return job, round(self._finished * 100 / len(self.keys))

    def finish(self, cancelled):
        self.scope = None
        unfinished = []
        for job in self.active_jobs():
            if job.state in {"等待转换", "转换中"}:
                job.state = "已取消" if cancelled else "未完成"
                unfinished.append(job)
        return unfinished, self._outputs, self._finished - self._failed, self._failed

    def cancel(self):
        if self.scope is not None:
            self.scope.cancel()

    def shutdown(self):
        self.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)
