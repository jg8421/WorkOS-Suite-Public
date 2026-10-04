"""Hold one local packaged-Core writer per canonical data directory."""
import hashlib
import os
from pathlib import Path


class CoreLease:
    def __init__(self, directory):
        self.handle = None
        self.file = None
        identity = os.path.normcase(str(Path(directory).resolve()))
        key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            self.api = ctypes.WinDLL('kernel32', use_last_error=True)
            self.api.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            self.api.CreateMutexW.restype = wintypes.HANDLE
            self.api.CloseHandle.argtypes = [wintypes.HANDLE]
            self.api.CloseHandle.restype = wintypes.BOOL
            handle = self.api.CreateMutexW(None, False, 'Local\\WorkOSSuiteCore-' + key)
            error = ctypes.get_last_error()
            if not handle:
                raise ValueError('无法确认研究数据是否已被占用，请稍后重试')
            if error == 183:
                self.api.CloseHandle(handle)
                raise ValueError('此研究数据已有运行中的 Suite，请使用已有入口')
            self.handle = handle
        else:
            import fcntl
            folder = Path(directory)
            folder.mkdir(parents=True, exist_ok=True)
            stream = (folder / '.suite-core.lock').open('a+b')
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                stream.close()
                raise ValueError('此研究数据已有运行中的 Suite，请使用已有入口') from None
            self.file = stream

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None
        if self.file is not None:
            self.file.close()
            self.file = None

    def __del__(self):
        self.close()
