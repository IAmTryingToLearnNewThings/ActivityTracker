"""Resolve the foreground window to its owning process."""

from __future__ import annotations

import ctypes
import logging
import ntpath
import os
from dataclasses import dataclass
from ctypes import wintypes
from typing import Callable, Iterable

import psutil

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessIdentity:
    app_name: str
    process_name: str


class ProcessManager:
    """Return identity for the foreground process, never background processes."""

    def __init__(
        self,
        *,
        foreground_pid: Callable[[], int | None] | None = None,
        process_name_lookup: Callable[[int], str] | None = None,
        ignored_processes: Iterable[str] = (),
    ) -> None:
        if foreground_pid is None:
            if os.name != "nt":
                raise OSError("Foreground window detection requires Windows.")
            foreground_pid = self._windows_foreground_pid
        self._foreground_pid = foreground_pid
        self._process_name_lookup = process_name_lookup or self._lookup_process_name
        self.set_ignored_processes(ignored_processes)

    def set_ignored_processes(self, process_names: Iterable[str]) -> None:
        self._ignored_processes = {
            ntpath.splitext(ntpath.basename(name.strip()))[0].casefold()
            for name in process_names
            if name.strip()
        }

    @staticmethod
    def _windows_foreground_pid() -> int | None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        process_id = wintypes.DWORD()
        thread_id = user32.GetWindowThreadProcessId(
            hwnd, ctypes.byref(process_id)
        )
        if not thread_id:
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error))
        if not process_id.value:
            return None
        return int(process_id.value)

    @staticmethod
    def _lookup_process_name(process_id: int) -> str:
        return psutil.Process(process_id).name()

    def current_process(self) -> ProcessIdentity | None:
        process_id = self._foreground_pid()
        if not process_id or process_id == os.getpid():
            return None
        try:
            process_name = ntpath.basename(
                self._process_name_lookup(process_id)
            ).strip()
        except (psutil.Error, OSError) as exc:
            logger.debug(
                "Could not resolve foreground process %s: %s", process_id, exc
            )
            return None
        if not process_name:
            return None

        app_name = ntpath.splitext(process_name)[0]
        if (
            process_name.casefold() in self._ignored_processes
            or app_name.casefold() in self._ignored_processes
        ):
            return None
        return ProcessIdentity(app_name=app_name, process_name=process_name)
