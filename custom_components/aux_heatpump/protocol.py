"""Receive-only AUX framing and experimentally verified field mappings.

Offsets are zero-based from the whole frame. TCP reads are not frame boundaries.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Field:
    key: str
    name: str
    prefix: bytes
    offset: int
    scale: int = 10
    signed: bool = True
    kind: str = "temperature"
    unit: str = "°C"
    enabled: bool = True
    diagnostic: bool = False

A = bytes.fromhex("7e f1 f1 12 2d 00 12")
# Older JW firmware uses a different B length; StreamParser validates length/CRC.
# Field offsets remain bounds-checked against the payload, excluding the CRC.
B = bytes.fromhex("7e 01 f1 05")
C = bytes.fromhex("7e 01 f1 02 31")
D = bytes.fromhex("7e f1 f1 12 2d 12 12")
FIELDS = (
    Field("suction", "Temperatura ssania", A, 23),
    Field("discharge", "Temperatura tłoczenia", A, 11),
    Field("outdoor", "Temperatura zewnętrzna", A, 25),
    Field("exchanger", "Temperatura wymiennika", A, 31),
    Field("inverter", "Temperatura inwertera", A, 35),
    Field("dhw", "Temperatura zbiornika CWU", B, 25),
    Field("flh_inlet", "Temperatura FLH W-IN", B, 33),
    Field("buffer", "Temperatura bufora", B, 37),
    Field("ibh_outlet", "Temperatura IBH W-OUT", B, 19),
    Field("water_inlet", "Temperatura wejścia wody", B, 21),
    Field("water_outlet", "Temperatura wyjścia wody", B, 23),
    Field("refrigerant_inlet", "Temperatura wejścia czynnika", C, 23),
    Field("refrigerant_outlet", "Temperatura wyjścia czynnika", C, 25),
    Field("heating_setpoint", "Nastawa CO", C, 43, signed=False),
    Field("dhw_setpoint", "Nastawa CWU", B, 9, signed=False),
    Field("water_flow", "Przepływ wody", B, 17, 100, False, "volume_flow_rate", "m³/h"),
    Field("compressor_current", "Prąd sprężarki", A, 37, 10, False, "current", "A"),
    Field("current", "Prąd", D, 7, 10, False, "current", "A"),
    Field("dc_voltage", "Napięcie szyny DC", D, 9, 1, False, "voltage", "V"),
    Field("expansion_valve", "Pozycja zaworu rozprężnego", D, 21, 1, False, "", "PLS"),
    Field("fan_speed", "Prędkość wentylatora", D, 17, 1, False, "", "rpm"),
    # HMI 81 Hz corresponds to 810 here; D33=86 is a different, unassigned field.
    Field("target_frequency", "Docelowa częstotliwość sprężarki (kandydat)", D, 15, 10, False, "frequency", "Hz"),
    Field("actual_frequency", "Aktualna częstotliwość sprężarki (kandydat D13)", D, 13, 10, False, "frequency", "Hz"),
    Field("frequency_d11", "Częstotliwość D11 (kandydat)", D, 11, 10, False, "frequency", "Hz", False, True),
    Field("frequency_d37", "Częstotliwość D37 (kandydat)", D, 37, 10, False, "frequency", "Hz", False, True),
    Field("pressure_raw", "Ciśnienie — surowe A7–A8", A, 7, 1, False, "", None, True, True),
    Field("pressure_estimated", "Ciśnienie szacowane", A, 7, 1, True, "pressure", "kPa"),
)

@dataclass(frozen=True)
class BinaryField:
    key: str
    name: str
    prefix: bytes
    offset: int
    mask: int
    enabled: bool = True
    diagnostic: bool = False

BINARY_FIELDS = (
    BinaryField("mv1_1", "Zawór MV1_1 — CWU", B, 13, 0x02),
    BinaryField("mv1_2", "Zawór MV1_2", B, 13, 0x01),
    BinaryField("mv2", "Zawór MV2", B, 14, 0x08),
    BinaryField("mv3_1", "Zawór MV3_1", B, 29, 0x01),
    BinaryField("mv3_2", "Zawór MV3_2", B, 29, 0x02),
    BinaryField("pump_i", "Pompa wewnętrzna PUMP_I", B, 13, 0x10),
    BinaryField("pump_o", "Pompa podłogówki PUMP_O", B, 13, 0x04),
    BinaryField("pump_d", "Pompa PUMP_D", B, 13, 0x08),
    BinaryField("pump_flh", "Pompa PUMP_FLH", B, 29, 0x04),
    BinaryField("pump_s", "Pompa PUMP_S", B, 13, 0x20),
    BinaryField("ibh", "Grzałka przepływowa IBH", B, 13, 0x80),
    BinaryField("tbh", "Grzałka zbiornika TBH", B, 13, 0x40),
    BinaryField("et_heater", "Grzałka ET E-HEATER", B, 30, 0x20),
    BinaryField("plate_heater", "Grzałka PLATE E-HEATER", B, 30, 0x10),
    BinaryField("heating_enabled", "CO włączone", bytes.fromhex("7e 01 f1 01 0d"), 5, 0x80),
    # B7 low bits track CWU permission even when idle (observed 03/13/53).
    # Bits 0 and 1 have always matched in captures; use bit0, preserve raw diagnostic.
    BinaryField("dhw_enabled", "CWU włączone", B, 7, 0x01),
    BinaryField("dhw_activity_d31", "Status CWU D31 (znaczenie niepotwierdzone)", D, 31, 0x40, diagnostic=True),
    BinaryField("fast_dhw_enabled", "Fast hot water włączone", B, 8, 0x02),
)

# Firmware dates match the photographed display; version codes are still uncertain.
TEXT_FIELDS = (
    Field("firmware_jw_date", "Data oprogramowania JW", C, 35, kind="", unit=None),
    Field("firmware_jz_date", "Data oprogramowania JZ", bytes.fromhex("7e f1 f1 12 2d 24 12"), 33, kind="", unit=None),
)

def decode_bcd_date(data: bytes) -> str | None:
    from datetime import date
    if len(data) != 3 or any((b >> 4) > 9 or (b & 15) > 9 for b in data):
        return None
    year, month, day = [(b >> 4) * 10 + (b & 15) for b in data]
    try:
        return date(2000 + year, month, day).isoformat()
    except ValueError:
        return None


def crc16(data: bytes) -> int:
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value


def estimate_pressure(raw: int) -> float | None:
    """Empirical estimate from signed A7-A8; no extrapolation beyond anchors.

    Raw -70 = HMI 536 kPa (2026-11-07). The sparsely calibrated lower segment
    interpolates to the unchanged 2023 high-range approximation at raw 70.
    The pressure_raw diagnostic intentionally retains the unsigned wire word.
    """
    if not -70 <= raw <= 630:
        return None
    if raw < 70:
        upper = ((0.000002039384559 * 70 + 0.002925489799) * 70
                 + 2.560974567) * 70 + 699.504062722
        return round(536.0 + (raw + 70) * (upper - 536.0) / 140, 1)
    return round(((0.000002039384559 * raw + 0.002925489799) * raw
                  + 2.560974567) * raw + 699.504062722, 1)

class StreamParser:
    """Bounded parser that recovers after noise, partial frames or bad CRC."""
    def __init__(self):
        self.buffer = bytearray()
        self.valid_frames = 0
        self.discarded_bytes = 0

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 5:
            if self.buffer[0] != 0x7E:
                del self.buffer[0]
                self.discarded_bytes += 1
                continue
            length = self.buffer[4]
            if length < 7:
                del self.buffer[0]
                self.discarded_bytes += 1
                continue
            if len(self.buffer) < length:
                break
            frame = bytes(self.buffer[:length])
            if crc16(frame[1:-2]) != int.from_bytes(frame[-2:], "little"):
                del self.buffer[0]
                self.discarded_bytes += 1
                continue
            del self.buffer[:length]
            self.valid_frames += 1
            frames.append(frame)
        return frames

def decode_frame(frame: bytes) -> dict[str, float | int | bool | str | None]:
    result = {}
    for field in FIELDS:
        if not frame.startswith(field.prefix) or field.offset + 2 > len(frame) - 2:
            continue
        raw = int.from_bytes(frame[field.offset:field.offset + 2], "big", signed=field.signed)
        if field.key == "pressure_estimated":
            result[field.key] = estimate_pressure(raw)
        elif field.kind == "temperature" and raw == -999:
            result[field.key] = None
        else:
            result[field.key] = raw / field.scale if field.scale != 1 else raw
    for field in BINARY_FIELDS:
        if frame.startswith(field.prefix) and field.offset < len(frame) - 2:
            result[field.key] = bool(frame[field.offset] & field.mask)
    for field in TEXT_FIELDS:
        if frame.startswith(field.prefix) and field.offset + 3 <= len(frame) - 2:
            result[field.key] = decode_bcd_date(frame[field.offset:field.offset + 3])
    return result
