import pytest

from core.idle_detector import IdleDetector, _tick_elapsed_milliseconds


def test_idle_detection_uses_configured_threshold():
    detector = IdleDetector(
        idle_duration_provider=lambda: 299.5,
        locked_provider=lambda: False,
    )

    assert not detector.is_idle(300)
    assert detector.is_tracking_allowed(300)

    detector = IdleDetector(
        idle_duration_provider=lambda: 300,
        locked_provider=lambda: False,
    )
    assert detector.is_idle(300)
    assert not detector.is_tracking_allowed(300)


def test_locked_desktop_disables_tracking_without_idle_check():
    idle_check_called = False

    def idle_duration():
        nonlocal idle_check_called
        idle_check_called = True
        return 0

    detector = IdleDetector(
        idle_duration_provider=idle_duration,
        locked_provider=lambda: True,
    )

    assert detector.is_locked()
    assert not detector.is_tracking_allowed(300)
    assert not idle_check_called


def test_idle_tick_calculation_handles_32_bit_wraparound():
    assert _tick_elapsed_milliseconds(5, 0xFFFFFFFE) == 7


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_invalid_idle_duration_is_rejected(duration):
    detector = IdleDetector(
        idle_duration_provider=lambda: duration,
        locked_provider=lambda: False,
    )

    with pytest.raises(ValueError, match="finite non-negative"):
        detector.idle_duration_seconds()


def test_invalid_idle_timeout_is_rejected():
    detector = IdleDetector(
        idle_duration_provider=lambda: 0,
        locked_provider=lambda: False,
    )

    with pytest.raises(ValueError, match="finite positive"):
        detector.is_idle(0)
