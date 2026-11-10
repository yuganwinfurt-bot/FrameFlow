"""Read wheel messages from the Windows GUI thread during an OLE drag."""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QTimer


WH_GETMESSAGE, HC_ACTION, PM_REMOVE = 3, 0, 1
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
HookProc = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32 = ctypes.WinDLL("user32")
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HookProc, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
get_thread_id = ctypes.WinDLL("kernel32").GetCurrentThreadId
get_thread_id.argtypes = ()
get_thread_id.restype = wintypes.DWORD


class DragWheel:
    def __init__(self, parent, scroll):
        self.scroll = scroll
        self.handle = None
        self.pending = []
        self.timer = QTimer(parent, singleShot=True, interval=0)
        self.timer.timeout.connect(self._flush)
        self.callback = HookProc(self._message)
        parent.destroyed.connect(self.stop)

    def start(self):
        if not self.handle:
            # A nonzero thread ID keeps the hook in this application's GUI thread.
            self.handle = user32.SetWindowsHookExW(WH_GETMESSAGE, self.callback, None, get_thread_id())

    def stop(self):
        if self.handle:
            user32.UnhookWindowsHookEx(self.handle)
            self.handle = None
        self.timer.stop()
        self.pending.clear()

    def _message(self, code, removal, address):
        if code == HC_ACTION and removal == PM_REMOVE:
            message = wintypes.MSG.from_address(address)
            if message.message in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                delta = ctypes.c_short(message.wParam >> 16).value
                self.pending.append(-delta if message.message == WM_MOUSEWHEEL else delta)
                if len(self.pending) == 1:
                    self.timer.start()
        # Let OLE continue processing the original message and the drag.
        return user32.CallNextHookEx(None, code, removal, address)

    def _flush(self):
        pending, self.pending = self.pending, []
        for delta in pending:
            self.scroll(delta)
