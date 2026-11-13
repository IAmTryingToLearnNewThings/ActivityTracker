import os

import psutil

from core.process_manager import ProcessIdentity, ProcessManager


def test_resolves_only_the_foreground_process():
    manager = ProcessManager(
        foreground_pid=lambda: 42,
        process_name_lookup=lambda process_id: r"C:\Apps\Firefox.EXE",
    )

    assert manager.current_process() == ProcessIdentity("Firefox", "Firefox.EXE")


def test_ignored_process_can_be_named_with_or_without_extension():
    manager = ProcessManager(
        foreground_pid=lambda: 42,
        process_name_lookup=lambda process_id: "code.exe",
        ignored_processes={"CODE"},
    )

    assert manager.current_process() is None


def test_ignored_processes_can_be_updated_from_settings():
    manager = ProcessManager(
        foreground_pid=lambda: 42,
        process_name_lookup=lambda process_id: "browser.exe",
    )

    assert manager.current_process() == ProcessIdentity("browser", "browser.exe")
    manager.set_ignored_processes(("browser.exe",))
    assert manager.current_process() is None


def test_does_not_count_activitytracker_itself():
    manager = ProcessManager(
        foreground_pid=os.getpid,
        process_name_lookup=lambda process_id: "activitytracker.exe",
    )

    assert manager.current_process() is None


def test_process_that_exits_during_lookup_is_ignored():
    def lookup_process_name(process_id):
        raise psutil.NoSuchProcess(process_id)

    manager = ProcessManager(
        foreground_pid=lambda: 42,
        process_name_lookup=lookup_process_name,
    )

    assert manager.current_process() is None
