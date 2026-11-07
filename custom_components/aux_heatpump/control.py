"""Build E2 from an observed (possibly restored) template and fresh known states.

Preserve all unknown settings. Never use a hard-coded default frame.
"""
import math
from .protocol import crc16

SETTINGS_PREFIX = bytes.fromhex("7e e1 00 e2 1b")
LIMITS = {"heating_setpoint": (20, 60), "dhw_setpoint": (30, 60)}
OFFSETS = {"heating_setpoint": 14, "dhw_setpoint": 16}
SWITCH_FIELDS = {"heating_enabled": (13, 0x80), "dhw_enabled": (13, 0x01),
                 "fast_dhw_enabled": (19, 0x02)}
CONTROL_KEYS = (*OFFSETS, *SWITCH_FIELDS)


def is_settings_frame(frame):
    return (len(frame) == 27 and frame.startswith(SETTINGS_PREFIX)
            and crc16(frame[1:-2]) == int.from_bytes(frame[-2:], "little"))


def settings_value(frame, key):
    if key in SWITCH_FIELDS:
        offset, mask = SWITCH_FIELDS[key]
        return bool(frame[offset] & mask)
    offset = OFFSETS[key]
    return int.from_bytes(frame[offset:offset + 2], "big") / 10


def make_command(template, key, value, current_values=None):
    if not is_settings_frame(template):
        raise ValueError("No valid settings frame from the HMI")
    frame = bytearray(template)
    if current_values is not None:
        # Preserve known settings confirmed by current telemetry, including the
        # other two switches. All unknown bits remain from the live E2 template.
        for known_key in CONTROL_KEYS:
            frame = bytearray(make_command(bytes(frame), known_key, current_values[known_key]))
    if key in SWITCH_FIELDS:
        if not isinstance(value, bool):
            raise ValueError("Switch requires an ON/OFF value")
        offset, mask = SWITCH_FIELDS[key]
        frame[offset] = (frame[offset] & ~mask) | (mask if value else 0)
    elif key in OFFSETS:
        low, high = LIMITS[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not low <= value <= high
                or abs(value - round(value)) > 0.000001):
            raise ValueError(f"Setpoint must be a whole number in the range {low}–{high}°C")
        offset = OFFSETS[key]
        frame[offset:offset + 2] = int(round(value * 10)).to_bytes(2, "big")
    else:
        raise ValueError("Unknown control field")
    frame[-2:] = crc16(frame[1:-2]).to_bytes(2, "little")
    return bytes(frame)
