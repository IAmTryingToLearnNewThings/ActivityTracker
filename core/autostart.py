"""Manage the current user's Windows Startup folder launcher."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence


class WindowsAutostart:
    """Create or remove only ActivityTracker's own Startup folder script."""

    def __init__(
        self,
        *,
        startup_dir: str | Path | None = None,
        launch_command: Sequence[str] | None = None,
    ) -> None:
        if startup_dir is None:
            if os.name != "nt":
                raise OSError("Windows autostart is only available on Windows.")
            app_data = os.environ.get("APPDATA")
            if not app_data:
                raise OSError("The current user's APPDATA directory is unavailable.")
            startup_dir = (
                Path(app_data)
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
                / "Startup"
            )
        self._startup_dir = Path(startup_dir)
        self._launch_command = (
            tuple(launch_command) if launch_command is not None else None
        )
        self._launcher_path = self._startup_dir / "ActivityTracker.vbs"

    @property
    def enabled(self) -> bool:
        return self._launcher_path.is_file()

    def enable(self) -> None:
        command = self._launch_command or self._default_launch_command()
        escaped_command = " ".join(
            f'"{argument.replace(chr(34), chr(34) * 2)}"'
            for argument in command
        )
        vb_command = escaped_command.replace('"', '""')
        script = (
            'Set activityTrackerShell = CreateObject("WScript.Shell")\r\n'
            f'activityTrackerShell.Run "{vb_command}", 0, False\r\n'
        )
        self._startup_dir.mkdir(parents=True, exist_ok=True)
        self._launcher_path.write_text(script, encoding="utf-16")

    def disable(self) -> bool:
        try:
            self._launcher_path.unlink()
        except FileNotFoundError:
            return False
        return True

    @staticmethod
    def _default_launch_command() -> tuple[str, ...]:
        if getattr(sys, "frozen", False):
            return sys.executable, "--background"

        executable = Path(sys.executable).with_name("pythonw.exe")
        if not executable.is_file():
            raise FileNotFoundError(
                f"Windowless Python executable was not found: {executable}"
            )
        script = Path(__file__).resolve().parents[1] / "main.py"
        if not script.is_file():
            raise FileNotFoundError(f"Application entry point was not found: {script}")
        return str(executable), str(script), "--background"
