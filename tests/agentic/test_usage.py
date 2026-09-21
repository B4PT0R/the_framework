from harness_core.agent.models.usage import usage_window


def test_usage_windows_are_selected_by_duration_not_position():
    usage = {
        "rate_limit": {
            "primary_window": {
                "used_percent": 36,
                "limit_window_seconds": 7 * 24 * 60 * 60,
                "reset_at": 1_800_000_000,
            },
            "secondary_window": None,
        },
        "additional_rate_limits": [{
            "rate_limit": {
                "primary_window": {
                    "used_percent": 99,
                    "limit_window_seconds": 5 * 60 * 60,
                },
            },
        }],
    }

    assert usage_window(usage, duration_seconds=5 * 60 * 60) is None
    weekly = usage_window(usage, duration_seconds=7 * 24 * 60 * 60)
    assert weekly.used_percent == 36
    assert weekly.reset_at == 1_800_000_000


def test_legacy_unnamed_windows_use_their_documented_positions():
    usage = {
        "rate_limit": {
            "primary_window": {"used_percent": 12},
            "secondary_window": {"used_percent": 34},
        },
    }

    assert usage_window(
        usage,
        duration_seconds=5 * 60 * 60,
        fallback="primary_window",
    ).used_percent == 12
    assert usage_window(
        usage,
        duration_seconds=7 * 24 * 60 * 60,
        fallback="secondary_window",
    ).used_percent == 34
