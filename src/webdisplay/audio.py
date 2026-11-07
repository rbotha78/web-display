from __future__ import annotations

import re
from pathlib import Path

DEFAULT_AUDIO_OUTPUT = "alsa/hdmi:CARD=vc4hdmi,DEV=0"


def validate_audio_output(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"alsa/(?:hdmi|plughw):CARD=[A-Za-z0-9_-]{1,64},DEV=[0-9]{1,2}", value
    ):
        raise ValueError("Invalid audio output")
    return value


def audio_outputs(root: Path = Path("/proc/asound")) -> list[dict[str, str]]:
    if not root.exists():
        return []
    cards = {}
    for line in (root / "cards").read_text().splitlines():
        match = re.match(r"\s*(\d+)\s+\[([A-Za-z0-9_-]+)\s*\]:.*? - (.+)", line)
        if match:
            cards[int(match[1])] = (match[2], match[3])
    outputs = []
    for line in (root / "pcm").read_text().splitlines():
        match = re.match(r"(\d+)-(\d+):.*\bplayback \d+", line)
        if not match or int(match[1]) not in cards:
            continue
        card, name = cards[int(match[1])]
        device = int(match[2])
        if card == "vc4hdmi" and device == 0:
            output, label = DEFAULT_AUDIO_OUTPUT, "HDMI"
        else:
            output = f"alsa/plughw:CARD={card},DEV={device}"
            label = "3.5 mm headphones" if card == "Headphones" else name
        outputs.append({"id": output, "label": f"{label} ({card}, device {device})"})
    return outputs
