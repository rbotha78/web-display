"""Public display geometry shared by local scenes and X11 overlays."""

DEFAULT_SAFE_AREA_PERCENT = 5


def validate_safe_area(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 10:
        raise ValueError("Overscan safe area must be a whole percentage from 0 to 10")
    return value


def countdown_position(width: int, height: int, percent: int) -> tuple[int, int]:
    return (
        max(0, width - 48 - round(width * percent / 100) - 16),
        round(height * percent / 100) + 16,
    )
