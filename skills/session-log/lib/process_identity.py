#!/usr/bin/env python3
# universal-session-log: managed
"""The identity of a running process: its id and its start time, so a later process that reuses the id never
passes for it.

macOS and Linux read the start time with ``ps -o lstart=``. Git Bash's ps has no ``-o``, and its shells see a
native Windows parent such as Claude Code as pid 1, so on Windows the id is the Windows process id and the start
time is its creation time (GetProcessTimes).

Usage:
    python3 process_identity.py start <pid>              # its start time; nothing when the process is gone
    python3 process_identity.py harness <Windows pid>    # Windows: "<pid>\\t<start>" of its nearest ancestor
                                                         # that is no shell
"""
import subprocess
import sys

WINDOWS = sys.platform == "win32"
# Between a hook's Git Bash processes and the harness that started them, bash.exe can sit more than once: a
# launcher (Git for Windows' bin\bash.exe, a scoop shim) or the stub a Git Bash exec leaves for a native
# parent. sh.exe stands in where a release ignores "shell": "bash".
SHELL_IMAGES = frozenset({"bash.exe", "sh.exe"})
MAX_ANCESTORS = 32


def _posix_start_time(pid):
    try:
        value = subprocess.check_output(["ps", "-p", str(pid), "-o", "lstart="], text=True,
                                        stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return value or None


if WINDOWS:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
    MAX_PATH = 260

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * MAX_PATH),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, *(ctypes.POINTER(wintypes.FILETIME),) * 4)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))
    _kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ProcessEntry))

    def _windows_start_time(pid):
        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            # A process that exited lives on as long as anyone holds a handle to it.
            exit_code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != STILL_ACTIVE:
                return None
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not _kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                             ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return str(created.dwHighDateTime << 32 | created.dwLowDateTime)
        finally:
            _kernel32.CloseHandle(handle)

    def _windows_processes():
        """``{pid: (parent pid, image name)}`` of every running process."""
        snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ProcessEntry(dwSize=ctypes.sizeof(_ProcessEntry))
            processes = {}
            more = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                processes[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
                more = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            return processes
        finally:
            _kernel32.CloseHandle(snapshot)

    def _windows_harness(pid):
        """The nearest ancestor of process ``pid`` that is no shell."""
        processes = _windows_processes()
        for _ in range(MAX_ANCESTORS):
            if pid not in processes:
                break
            pid = processes[pid][0]
            if pid in processes and processes[pid][1].lower() not in SHELL_IMAGES:
                return pid
        raise SystemExit("process_identity: no running ancestor above the hook's shells")


def start_time(pid):
    """The start time of process ``pid``, or None when it is gone."""
    return _windows_start_time(pid) if WINDOWS else _posix_start_time(pid)


def main(argv):
    if argv[:1] == ["start"] and len(argv) == 2:
        value = start_time(int(argv[1]))
        if value:
            print(value)
    elif argv[:1] == ["harness"] and len(argv) == 2 and WINDOWS:
        harness = _windows_harness(int(argv[1]))
        print(f"{harness}\t{start_time(harness) or ''}")
    else:
        raise SystemExit("process_identity: usage: process_identity.py start <pid> | harness <Windows pid>")


if __name__ == "__main__":
    main(sys.argv[1:])
