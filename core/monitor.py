"""Foreground application monitoring and usage interval accounting."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from typing import Callable

from core.database import Database
from core.process_manager import ProcessIdentity, ProcessManager

logger = logging.getLogger(__name__)


class ActivityMonitor:
    """Sample foreground activity and persist elapsed usage time."""

    def __init__(
        self,
        database: Database,
        process_manager: ProcessManager,
        *,
        interval_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        is_tracking_allowed: Callable[[], bool] = lambda: True,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("Sampling interval must be greater than zero.")
        self._database = database
        self._process_manager = process_manager
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._is_tracking_allowed = is_tracking_allowed

        self._sample_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._monitoring_enabled = threading.Event()
        self._monitoring_enabled.set()
        self._thread: threading.Thread | None = None
        self._process: ProcessIdentity | None = None
        self._session_id: int | None = None
        self._duration_seconds = 0.0
        self._last_monotonic: float | None = None
        self._last_observed_at: datetime | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Activity monitor is already running.")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="ActivityTrackerMonitor", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is not None:
            self._stop_event.set()
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("Activity monitor did not stop in time.")
            self._thread = None

        with self._sample_lock:
            if self._session_id is not None:
                now_monotonic = self._monotonic()
                now = self._wall_clock()
                self._checkpoint_current(now_monotonic, now)
                self._finish_current(now_monotonic, now)

    @property
    def is_paused(self) -> bool:
        return not self._monitoring_enabled.is_set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def pause(self) -> None:
        with self._sample_lock:
            if not self._monitoring_enabled.is_set():
                return
            if self._session_id is not None:
                now_monotonic = self._monotonic()
                now = self._wall_clock()
                self._checkpoint_current(now_monotonic, now)
                self._finish_current(now_monotonic, now)
            self._monitoring_enabled.clear()

    def resume(self) -> None:
        with self._sample_lock:
            self._monitoring_enabled.set()

    def sample_once(self) -> None:
        """Perform one sample; exposed for deterministic tests and diagnostics."""
        with self._sample_lock:
            now_monotonic = self._monotonic()
            now = self._wall_clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("The wall clock must return an aware datetime.")
            if (
                self._last_monotonic is not None
                and now_monotonic < self._last_monotonic
            ):
                raise ValueError("The monotonic clock moved backwards.")
            if self._last_observed_at is not None:
                elapsed = self._elapsed_since_last_sample(now_monotonic)
                if now.astimezone(timezone.utc) < self._last_observed_at.astimezone(
                    timezone.utc
                ):
                    now = (
                        self._last_observed_at.astimezone(timezone.utc)
                        + timedelta(seconds=elapsed)
                    ).astimezone()

            try:
                process = (
                    self._process_manager.current_process()
                    if self._monitoring_enabled.is_set()
                    and self._is_tracking_allowed()
                    else None
                )
            except OSError:
                self._close_at_last_observation()
                self._last_monotonic = now_monotonic
                raise

            if self._session_id is not None and process == self._process:
                self._checkpoint_current(now_monotonic, now)
                return

            if self._session_id is not None:
                self._checkpoint_current(now_monotonic, now)
                self._finish_current(now_monotonic, now)
            else:
                self._last_monotonic = now_monotonic

            if process is not None:
                session_id = self._database.start_session(
                    app_name=process.app_name,
                    process_name=process.process_name,
                    started_at=now,
                    local_date=now.astimezone().date(),
                )
                self._process = process
                self._session_id = session_id
                self._duration_seconds = 0.0
                self._last_monotonic = now_monotonic
                self._last_observed_at = now

    def _elapsed_since_last_sample(self, now_monotonic: float) -> float:
        if self._last_monotonic is None:
            return 0.0
        elapsed = now_monotonic - self._last_monotonic
        if elapsed < 0:
            raise ValueError("The monotonic clock moved backwards.")
        return elapsed

    def _checkpoint_current(self, now_monotonic: float, observed_at: datetime) -> None:
        elapsed = self._elapsed_since_last_sample(now_monotonic)
        if self._last_observed_at is None:
            raise RuntimeError("Active session is missing its last observation.")

        previous_local = self._last_observed_at.astimezone()
        current_local = observed_at.astimezone()
        if current_local.date() <= previous_local.date():
            duration = self._duration_seconds + elapsed
            self._database.checkpoint_session(
                self._session_id, observed_at, duration
            )
            self._duration_seconds = duration
            self._last_monotonic = now_monotonic
            self._last_observed_at = observed_at
            return

        wall_start = previous_local.astimezone(timezone.utc)
        wall_end = current_local.astimezone(timezone.utc)
        wall_duration = (wall_end - wall_start).total_seconds()
        elapsed_before_boundary = 0.0
        boundary_day = previous_local.date() + timedelta(days=1)
        while boundary_day <= current_local.date():
            boundary = datetime.combine(
                boundary_day, datetime_time.min
            ).astimezone()
            fraction = (boundary.astimezone(timezone.utc) - wall_start).total_seconds()
            fraction = min(1.0, max(0.0, fraction / wall_duration))
            boundary_duration = elapsed * fraction
            segment_duration = boundary_duration - elapsed_before_boundary
            process = self._process
            if process is None:
                raise RuntimeError("Active session is missing its process identity.")

            self._database.close_session(
                self._session_id,
                boundary,
                self._duration_seconds + segment_duration,
            )
            self._clear_current()
            session_id = self._database.start_session(
                process.app_name,
                process.process_name,
                boundary,
                boundary.date(),
            )
            self._session_id = session_id
            self._process = process
            self._last_observed_at = boundary
            self._duration_seconds = 0.0
            elapsed_before_boundary = boundary_duration
            boundary_day += timedelta(days=1)

        duration = self._duration_seconds + elapsed - elapsed_before_boundary
        self._database.checkpoint_session(self._session_id, observed_at, duration)
        self._duration_seconds = duration
        self._last_monotonic = now_monotonic
        self._last_observed_at = observed_at

    def _finish_current(self, now_monotonic: float, ended_at: datetime) -> None:
        if self._session_id is None:
            return
        elapsed = self._elapsed_since_last_sample(now_monotonic)
        self._database.close_session(
            self._session_id,
            ended_at,
            self._duration_seconds + elapsed,
        )
        self._clear_current()

    def _close_at_last_observation(self) -> None:
        if self._session_id is None or self._last_observed_at is None:
            self._clear_current()
            return
        self._database.close_session(
            self._session_id, self._last_observed_at, self._duration_seconds
        )
        self._clear_current()

    def _clear_current(self) -> None:
        self._process = None
        self._session_id = None
        self._duration_seconds = 0.0
        self._last_observed_at = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            started = self._monotonic()
            try:
                self.sample_once()
            except (OSError, sqlite3.Error):
                logger.exception("Activity monitor sample failed.")
            remaining = self._interval_seconds - (self._monotonic() - started)
            self._stop_event.wait(max(0.0, remaining))
