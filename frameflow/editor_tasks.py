"""The editor's single foreground worker and its cancellation scope."""
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal, Slot

from .media import Processes


class EditorTasks(QObject):
    completed = Signal(object, str, object, object)
    progress = Signal(int)
    notice = Signal(str)
    _worker_finished = Signal(object, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.scope = None
        self.kind = ""
        self._callbacks = (None, None)
        self._closing = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="frameflow-editor")
        self._worker_finished.connect(self._finish)

    def reserve(self, done, failed, kind):
        """Mark the editor busy before it updates controls or sends a UI notification."""
        self.scope = Processes()
        self.kind = kind
        self._callbacks = (done, failed)

    def submit(self, worker):
        self._pool.submit(self._execute, worker, self.scope)

    def _execute(self, worker, scope):
        try:
            result = worker(scope)
        except Exception as exc:
            self._worker_finished.emit(None, str(exc) or type(exc).__name__)
        else:
            self._worker_finished.emit(result, "")

    @Slot(object, str)
    def _finish(self, result, error):
        if self._closing:
            return
        if self.kind == "video_insert" and self.scope.cancelled.is_set():
            error = "已取消素材插入。"
        done, failed = self._callbacks
        self.scope = None
        self.kind = ""
        self._callbacks = (None, None)
        self.completed.emit(result, error, done, failed)

    def shutdown(self):
        self._closing = True
        if self.scope is not None:
            self.scope.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)
        self.scope = None
        self.kind = ""
        self._callbacks = (None, None)
