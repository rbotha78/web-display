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


def now_playing_geometry(width: int, height: int, percent: int) -> tuple[int, int, int, int]:
    margin_x = round(width * percent / 100)
    margin_y = round(height * percent / 100)
    card_height = max(32, min(52, round(height * 0.04)))
    card_width = min(width - 2 * margin_x, 640, max(240, round(width * 0.36)))
    return margin_x, margin_y, card_width, card_height
