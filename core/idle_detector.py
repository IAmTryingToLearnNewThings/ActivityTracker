"""Windows user-idle and workstation-lock detection."""

from __future__ import annotations

import ctypes
import math
import os
from ctypes import wintypes
from typing import Callable


_ERROR_INSUFFICIENT_BUFFER = 122
_DESKTOP_READOBJECTS = 0x0001
_UOI_NAME = 2
_UINT32_MASK = 0xFFFFFFFF


class _LastInputInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


def _tick_elapsed_milliseconds(current_tick: int, last_input_tick: int) -> int:
    return (current_tick - last_input_tick) & _UINT32_MASK


class IdleDetector:
    """Detect inactivity and secure desktop state for the current session."""

    def __init__(
        self,
        *,
        idle_duration_provider: Callable[[], float] | None = None,
        locked_provider: Callable[[], bool] | None = None,
    ) -> None:
        self._user32 = None
        self._kernel32 = None
        if idle_duration_provider is None or locked_provider is None:
            if os.name != "nt":
                raise OSError("Idle and lock detection requires Windows.")
            self._initialize_windows_api()

        self._idle_duration_provider = (
            idle_duration_provider or self._read_idle_duration
        )
        self._locked_provider = locked_provider or self._read_locked_state

    def _initialize_windows_api(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.GetLastInputInfo.argtypes = [
            ctypes.POINTER(_LastInputInfo)
        ]
        self._user32.GetLastInputInfo.restype = wintypes.BOOL
        self._kernel32.GetTickCount.argtypes = []
        self._kernel32.GetTickCount.restype = wintypes.DWORD

        self._user32.OpenInputDesktop.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._user32.OpenInputDesktop.restype = wintypes.HANDLE
        self._user32.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.GetUserObjectInformationW.restype = wintypes.BOOL
        self._user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        self._user32.CloseDesktop.restype = wintypes.BOOL

    @staticmethod
    def _raise_last_error(operation: str) -> OSError:
        error = ctypes.get_last_error()
        return OSError(error, f"{operation}: {ctypes.FormatError(error)}")

    def _read_idle_duration(self) -> float:
        last_input = _LastInputInfo()
        last_input.cbSize = ctypes.sizeof(_LastInputInfo)
        if not self._user32.GetLastInputInfo(ctypes.byref(last_input)):
            raise self._raise_last_error("GetLastInputInfo failed")
        current_tick = int(self._kernel32.GetTickCount())
        idle_milliseconds = _tick_elapsed_milliseconds(
            current_tick, int(last_input.dwTime)
        )
        return idle_milliseconds / 1000.0

    def _read_locked_state(self) -> bool:
        desktop = self._user32.OpenInputDesktop(
            0, False, _DESKTOP_READOBJECTS
        )
        if not desktop:
            raise self._raise_last_error("OpenInputDesktop failed")

        try:
            name_buffer = ctypes.create_unicode_buffer(256)
            required_size = wintypes.DWORD()
            succeeded = self._user32.GetUserObjectInformationW(
                desktop,
                _UOI_NAME,
                ctypes.cast(name_buffer, wintypes.LPVOID),
                ctypes.sizeof(name_buffer),
                ctypes.byref(required_size),
            )
            if not succeeded:
                error = ctypes.get_last_error()
                if error != _ERROR_INSUFFICIENT_BUFFER:
                    raise OSError(
                        error,
                        f"GetUserObjectInformationW failed: "
                        f"{ctypes.FormatError(error)}",
                    )
                characters = required_size.value // ctypes.sizeof(
                    ctypes.c_wchar
                )
                name_buffer = ctypes.create_unicode_buffer(characters + 1)
                succeeded = self._user32.GetUserObjectInformationW(
                    desktop,
                    _UOI_NAME,
                    ctypes.cast(name_buffer, wintypes.LPVOID),
                    ctypes.sizeof(name_buffer),
                    ctypes.byref(required_size),
                )
                if not succeeded:
                    raise self._raise_last_error(
                        "GetUserObjectInformationW failed"
                    )
            return name_buffer.value.casefold() != "default"
        finally:
            if not self._user32.CloseDesktop(desktop):
                raise self._raise_last_error("CloseDesktop failed")

    def idle_duration_seconds(self) -> float:
        duration = self._idle_duration_provider()
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("Idle duration must be a finite non-negative value.")
        return duration

    def is_idle(self, timeout_seconds: float = 300.0) -> bool:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Idle timeout must be a finite positive value.")
        return self.idle_duration_seconds() >= timeout_seconds

    def is_locked(self) -> bool:
        return bool(self._locked_provider())

    def is_tracking_allowed(self, idle_timeout_seconds: float = 300.0) -> bool:
        if self.is_locked():
            return False
        return not self.is_idle(idle_timeout_seconds)
