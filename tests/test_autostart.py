from core.autostart import WindowsAutostart


def test_autostart_installs_and_removes_only_its_launcher(tmp_path):
    manager = WindowsAutostart(
        startup_dir=tmp_path,
        launch_command=(
            r"C:\Program Files\ActivityTracker\ActivityTracker.exe",
            "--background",
        ),
    )

    assert not manager.enabled
    manager.enable()
    assert manager.enabled
    launcher = (tmp_path / "ActivityTracker.vbs").read_text(encoding="utf-16")
    assert 'CreateObject("WScript.Shell")' in launcher
    assert "ActivityTracker.exe" in launcher
    assert "--background" in launcher
    assert manager.disable()
    assert not manager.enabled
    assert not manager.disable()
