import pytest

from backend.dividend.position_levels import position_status, validate_position_levels


@pytest.mark.parametrize("grade", ["S", "A", "B"])
def test_supported_grades_are_valid(grade):
    validate_position_levels(grade, 5.0, 5.5, 6.0)


def test_invalid_level_order_is_rejected():
    with pytest.raises(ValueError, match="建仓"):
        validate_position_levels("S", 7.0, 6.0, 8.0)
    with pytest.raises(ValueError, match="加仓"):
        validate_position_levels("S", 5.0, 8.0, 7.0)


@pytest.mark.parametrize(("current_yield", "expected"), [
    (4.9, "watch"), (5.0, "entry"), (5.5, "add"), (6.0, "heavy"),
])
def test_position_status_uses_highest_reached_level(current_yield, expected):
    assert position_status(current_yield, 5.0, 5.5, 6.0) == expected


def test_position_status_without_configured_levels_is_watch():
    assert position_status(20.0, None, None, None) == "watch"
