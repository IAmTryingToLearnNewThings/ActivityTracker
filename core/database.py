"""SQLite persistence for usage sessions, settings, and report history."""

from __future__ import annotations

import ntpath
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Iterator, Mapping

from core.applications import display_name_for_process


_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class UsageTotal:
    app_name: str
    process_name: str
    duration_seconds: float


@dataclass(frozen=True)
class DailyActivity:
    day: date
    duration_seconds: float


@dataclass(frozen=True)
class HourlyActivity:
    hour: int
    duration_seconds: float


@dataclass(frozen=True)
class ReportHistory:
    id: int
    report_type: str
    period_start: date
    period_end: date
    recipient: str
    status: str
    attempted_at: datetime
    sent_at: datetime | None
    error_message: str | None


def _as_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _as_date(value: date) -> str:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError("Expected a date, not a datetime.")
    return value.isoformat()


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


class Database:
    """Thread-safe SQLite repository; callers own the instance lifecycle."""

    def __init__(self, path: str | Path) -> None:
        database_path = str(path)
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            database_path,
            check_same_thread=False,
            isolation_level=None,
            timeout=5,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        if database_path != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version > _SCHEMA_VERSION:
            self._connection.close()
            raise RuntimeError(
                f"Database schema version {version} is newer than supported "
                f"version {_SCHEMA_VERSION}."
            )
        if version == _SCHEMA_VERSION:
            return

        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS usage_sessions (
                id INTEGER PRIMARY KEY,
                app_name TEXT NOT NULL CHECK (length(trim(app_name)) > 0),
                process_name TEXT NOT NULL CHECK (length(trim(process_name)) > 0),
                local_date TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                last_observed_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL DEFAULT 0
                    CHECK (duration_seconds >= 0),
                CHECK (last_observed_at >= started_at),
                CHECK (ended_at IS NULL OR ended_at >= started_at)
            );

            CREATE INDEX IF NOT EXISTS idx_usage_sessions_date_process
                ON usage_sessions(local_date, process_name);

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY CHECK (length(trim(key)) > 0),
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS report_history (
                id INTEGER PRIMARY KEY,
                report_type TEXT NOT NULL
                    CHECK (report_type IN ('daily', 'weekly')),
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                recipient TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('sent', 'failed')),
                attempted_at TEXT NOT NULL,
                sent_at TEXT,
                error_message TEXT,
                CHECK (period_end >= period_start),
                CHECK (
                    (status = 'sent' AND sent_at IS NOT NULL)
                    OR status = 'failed'
                )
            );

            CREATE INDEX IF NOT EXISTS idx_report_history_attempted
                ON report_history(attempted_at DESC);

            PRAGMA user_version = 1;
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def start_session(
        self,
        app_name: str,
        process_name: str,
        started_at: datetime,
        local_date: date,
    ) -> int:
        if not app_name.strip() or not process_name.strip():
            raise ValueError("Application and process names must not be empty.")
        timestamp = _as_utc(started_at)
        day = _as_date(local_date)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO usage_sessions (
                    app_name, process_name, local_date, started_at,
                    last_observed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (app_name, process_name, day, timestamp, timestamp),
            )
            return int(cursor.lastrowid)

    def checkpoint_session(
        self,
        session_id: int,
        observed_at: datetime,
        duration_seconds: float,
    ) -> None:
        timestamp = _as_utc(observed_at)
        if duration_seconds < 0:
            raise ValueError("Duration must not be negative.")
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT started_at, last_observed_at, duration_seconds, ended_at
                FROM usage_sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None or row["ended_at"] is not None:
                raise ValueError(f"No open usage session with id {session_id}.")
            if timestamp < row["last_observed_at"] or timestamp < row["started_at"]:
                raise ValueError("Observation time cannot precede the session.")
            if duration_seconds < row["duration_seconds"]:
                raise ValueError("Session duration cannot decrease.")
            connection.execute(
                """
                UPDATE usage_sessions
                SET last_observed_at = ?, duration_seconds = ?
                WHERE id = ?
                """,
                (timestamp, duration_seconds, session_id),
            )

    def close_session(
        self,
        session_id: int,
        ended_at: datetime,
        duration_seconds: float,
    ) -> None:
        timestamp = _as_utc(ended_at)
        if duration_seconds < 0:
            raise ValueError("Duration must not be negative.")
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT started_at, last_observed_at, duration_seconds, ended_at
                FROM usage_sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None or row["ended_at"] is not None:
                raise ValueError(f"No open usage session with id {session_id}.")
            if timestamp < row["last_observed_at"]:
                raise ValueError("End time cannot precede the last observation.")
            if duration_seconds < row["duration_seconds"]:
                raise ValueError("Session duration cannot decrease.")
            connection.execute(
                """
                UPDATE usage_sessions
                SET ended_at = ?, last_observed_at = ?, duration_seconds = ?
                WHERE id = ?
                """,
                (timestamp, timestamp, duration_seconds, session_id),
            )

    def recover_open_sessions(self) -> int:
        """Close crash-left sessions at their last persisted observation."""
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE usage_sessions
                SET ended_at = last_observed_at
                WHERE ended_at IS NULL
                """
            )
            return cursor.rowcount

    def set_setting(self, key: str, value: str) -> None:
        self.set_settings({key: value})

    def set_settings(self, values: Mapping[str, str]) -> None:
        if not values:
            return
        if any(not key.strip() for key in values):
            raise ValueError("Setting keys must not be empty.")
        if any(not isinstance(value, str) for value in values.values()):
            raise TypeError("Setting values must be strings.")
        updated_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with self._transaction() as connection:
            connection.executemany(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at
                """,
                [
                    (key, value, updated_at)
                    for key, value in values.items()
                ],
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return default if row is None else str(row["value"])

    def get_usage_totals(
        self, start_date: date, end_date: date
    ) -> list[UsageTotal]:
        start = _as_date(start_date)
        end = _as_date(end_date)
        if end < start:
            raise ValueError("End date must not precede start date.")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT app_name, process_name, SUM(duration_seconds) AS duration
                FROM usage_sessions
                WHERE local_date BETWEEN ? AND ?
                GROUP BY app_name, process_name
                HAVING SUM(duration_seconds) > 0
                ORDER BY duration DESC, app_name COLLATE NOCASE
                """,
                (start, end),
            ).fetchall()
        totals: dict[tuple[str, str], float] = {}
        for row in rows:
            process_name = str(row["process_name"])
            process_stem = ntpath.splitext(ntpath.basename(process_name))[0]
            app_name = str(row["app_name"])
            display_name = display_name_for_process(process_name)
            if (
                display_name.casefold() != process_stem.casefold()
                and app_name.casefold()
                in {process_stem.casefold(), display_name.casefold()}
            ):
                app_name = display_name
            key = (app_name, process_name)
            totals[key] = totals.get(key, 0.0) + float(row["duration"])

        return [
            UsageTotal(
                app_name=app_name,
                process_name=process_name,
                duration_seconds=duration,
            )
            for (app_name, process_name), duration in sorted(
                totals.items(),
                key=lambda item: (-item[1], item[0][0].casefold()),
            )
        ]

    def get_daily_activity(
        self, start_date: date, end_date: date
    ) -> list[DailyActivity]:
        start = _as_date(start_date)
        end = _as_date(end_date)
        if end < start:
            raise ValueError("End date must not precede start date.")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT local_date, SUM(duration_seconds) AS duration
                FROM usage_sessions
                WHERE local_date BETWEEN ? AND ?
                GROUP BY local_date
                ORDER BY local_date
                """,
                (start, end),
            ).fetchall()
        return [
            DailyActivity(
                day=date.fromisoformat(row["local_date"]),
                duration_seconds=float(row["duration"]),
            )
            for row in rows
        ]

    def get_hourly_activity(self, day: date) -> list[HourlyActivity]:
        day_text = _as_date(day)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT started_at, ended_at, last_observed_at, duration_seconds
                FROM usage_sessions
                WHERE local_date = ?
                """,
                (day_text,),
            ).fetchall()

        totals = [0.0] * 24
        for row in rows:
            duration = float(row["duration_seconds"])
            if duration == 0:
                continue
            start = datetime.fromisoformat(row["started_at"]).astimezone()
            end_value = row["ended_at"] or row["last_observed_at"]
            end = datetime.fromisoformat(end_value).astimezone()
            start_utc = start.astimezone(timezone.utc)
            end_utc = end.astimezone(timezone.utc)
            wall_duration = (end_utc - start_utc).total_seconds()
            if wall_duration <= 0:
                if start.date() == day:
                    totals[start.hour] += duration
                continue

            for hour in range(24):
                bucket_start = datetime.combine(
                    day, datetime_time(hour=hour)
                ).astimezone().astimezone(timezone.utc)
                bucket_end = datetime.combine(
                    day + timedelta(days=1)
                    if hour == 23
                    else day,
                    datetime_time.min
                    if hour == 23
                    else datetime_time(hour=hour + 1),
                ).astimezone().astimezone(timezone.utc)
                overlap = max(
                    0.0,
                    (
                        min(end_utc, bucket_end) - max(start_utc, bucket_start)
                    ).total_seconds(),
                )
                if overlap:
                    totals[hour] += duration * overlap / wall_duration

        return [
            HourlyActivity(hour=hour, duration_seconds=duration)
            for hour, duration in enumerate(totals)
        ]

    def record_report_attempt(
        self,
        report_type: str,
        period_start: date,
        period_end: date,
        recipient: str,
        status: str,
        attempted_at: datetime,
        sent_at: datetime | None = None,
        error_message: str | None = None,
    ) -> int:
        if report_type not in {"daily", "weekly"}:
            raise ValueError("Report type must be 'daily' or 'weekly'.")
        if status not in {"sent", "failed"}:
            raise ValueError("Report status must be 'sent' or 'failed'.")
        start = _as_date(period_start)
        end = _as_date(period_end)
        if end < start:
            raise ValueError("Report end date must not precede start date.")
        attempt_timestamp = _as_utc(attempted_at)
        sent_timestamp = None if sent_at is None else _as_utc(sent_at)
        if status == "sent" and sent_timestamp is None:
            raise ValueError("A successful report must have a sent timestamp.")
        if status == "failed" and sent_timestamp is not None:
            raise ValueError("A failed report must not have a sent timestamp.")
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO report_history (
                    report_type, period_start, period_end, recipient, status,
                    attempted_at, sent_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_type,
                    start,
                    end,
                    recipient,
                    status,
                    attempt_timestamp,
                    sent_timestamp,
                    error_message,
                ),
            )
            return int(cursor.lastrowid)

    def get_report_history(self, limit: int = 50) -> list[ReportHistory]:
        if limit < 1:
            raise ValueError("Limit must be greater than zero.")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT id, report_type, period_start, period_end, recipient,
                       status, attempted_at, sent_at, error_message
                FROM report_history
                ORDER BY attempted_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            ReportHistory(
                id=int(row["id"]),
                report_type=row["report_type"],
                period_start=date.fromisoformat(row["period_start"]),
                period_end=date.fromisoformat(row["period_end"]),
                recipient=row["recipient"],
                status=row["status"],
                attempted_at=_parse_utc(row["attempted_at"]),
                sent_at=None if row["sent_at"] is None else _parse_utc(row["sent_at"]),
                error_message=row["error_message"],
            )
            for row in rows
        ]

    def has_report_attempt(
        self, report_type: str, period_start: date, period_end: date
    ) -> bool:
        if report_type not in {"daily", "weekly"}:
            raise ValueError("Report type must be 'daily' or 'weekly'.")
        start = _as_date(period_start)
        end = _as_date(period_end)
        if end < start:
            raise ValueError("Report end date must not precede start date.")
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM report_history
                WHERE report_type = ? AND period_start = ? AND period_end = ?
                LIMIT 1
                """,
                (report_type, start, end),
            ).fetchone()
            return row is not None
