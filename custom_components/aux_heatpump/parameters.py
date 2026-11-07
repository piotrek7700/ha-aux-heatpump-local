"""B6 configuration mapped by controlled HMI tests on 2026-10-17.

Limits are from the technical manual pp. 48-49, except Tao_pump_on,
which is intentionally limited to the tested 3..6 degrees. No polling.
"""
from dataclasses import dataclass
import math
from .protocol import crc16

COMMAND_PREFIX = bytes.fromhex("7e e1 01 b6 0b")
REPLY_PREFIX = bytes.fromhex("7e 01 e1 03 0b")


@dataclass(frozen=True)
class Parameter:
    key: str
    name: str
    selector: int
    low: int = 0
    high: int = 255
    step: int = 1
    multiplier: int = 1
    signed: bool = False
    unit: str | None = None
    mask: int = 0
    options: tuple = ()
    range_source: str = "Technical manual, pp. 48–49"

    @property
    def kind(self):
        return "switch" if self.key in SWITCH_KEYS else "select" if self.options else "number"


NUMBERS = (
    Parameter("tao_dhwmax", "Tao_DHWMAX — maks. temperatura zewnętrzna CWU", 0x11, 35, 43, unit="°C"),
    Parameter("tao_dhwmin", "Tao_DHWMIN — min. temperatura zewnętrzna CWU", 0x12, -25, 5, signed=True, unit="°C"),
    Parameter("twt_di", "Twt_DI — temperatura dezynfekcji", 0x13, 60, 70, unit="°C"),
    Parameter("t_tbh_delay", "t_TBH_DELAY — opóźnienie grzałki CWU", 0x14, 0, 240, unit="min"),
    Parameter("t_di_hightemp", "t_DI_HIGHTEMP — czas utrzymania dezynfekcji", 0x15, 5, 60, unit="min"),
    Parameter("t_di_max", "t_DI_MAX — maks. czas dezynfekcji", 0x16, 90, 300, 10, 10, unit="min"),
    Parameter("t_dhwhp_restrict", "t_DHWHP_RESTRICT — czas grzania/chłodzenia", 0x17, 10, 600, 10, 10, unit="min"),
    Parameter("t_dhwhp_max", "t_DHWHP_MAX — maks. czas CWU", 0x18, 10, 600, 10, 10, unit="min"),
    Parameter("dhw_pump_runtime", "Czas pracy pompy CWU", 0x19, 5, 120, 5, 5, unit="min"),
    Parameter("tao_hmax", "Tao_HMAX — maks. temperatura zewnętrzna CO", 0x27, 20, 35, unit="°C"),
    Parameter("tao_hmin", "Tao_HMIN — min. temperatura zewnętrzna CO", 0x28, -25, 15, signed=True, unit="°C"),
    Parameter("tsetac_h1", "TsetAC_H1 — temperatura wody punkt 1", 0x29, 25, 60, unit="°C"),
    Parameter("tsetac_h2", "TsetAC_H2 — temperatura wody punkt 2", 0x2A, 25, 60, unit="°C"),
    Parameter("tao_h1", "Tao_H1 — temperatura zewnętrzna punkt 1", 0x2B, -25, 35, signed=True, unit="°C"),
    Parameter("tao_h2", "Tao_H2 — temperatura zewnętrzna punkt 2", 0x2C, -25, 35, signed=True, unit="°C"),
    Parameter("dtsh_off", "dTSH_OFF — różnica wyłączenia CO", 0x2D, 2, 10, unit="°C"),
    Parameter("dtsh_on", "dTSH_ON — różnica włączenia CO", 0x2E, 0, 10, unit="°C"),
    Parameter("dtsdhw_on", "dTSDHW_ON — różnica włączenia CWU", 0x37, 2, 10, unit="°C"),
    Parameter("tao_pump_on", "Tao_pump_on", 0x4F, 3, 6, unit="°C", range_source="HMI test 2026-10-17: only 3–6°C; full range unknown"),
)
SWITCHES = (
    Parameter("dhw_mode_available", "Dostępność trybu CWU", 0x10, mask=0x01, range_source="HMI test 2026-10-17: OFF/ON"),
    Parameter("disinfection_allowed", "Zezwolenie na harmonogram dezynfekcji", 0x10, mask=0x02, range_source="HMI test 2026-10-17: OFF/ON"),
    Parameter("dhw_priority", "Priorytet CWU", 0x10, mask=0x04, range_source="HMI test 2026-10-17: OFF/ON"),
    Parameter("dhw_pump_available", "Opcja pompy CWU", 0x10, mask=0x08, range_source="HMI test 2026-10-17: OFF/ON"),
    Parameter("heating_mode_available", "Dostępność trybu grzania", 0x26, 0, 1, range_source="HMI test 2026-10-17: OFF/ON"),
    Parameter("cooling_mode_available", "Dostępność trybu chłodzenia", 0x1A, 0, 1, range_source="HMI test 2026-10-17: OFF/ON"),
)
EMITTER_OPTIONS = ((1, "Kaloryfery"), (2, "Podłogówka"), (3, "Klimakonwektory"))
SELECTS = (
    Parameter("zone1_emitter", "Strefa 1 — rodzaj odbiornika", 0x30, options=EMITTER_OPTIONS, range_source="HMI test 2026-10-17: three emitter types"),
    Parameter("zone2_emitter", "Strefa 2 — rodzaj odbiornika", 0x31, options=EMITTER_OPTIONS, range_source="HMI test 2026-10-17: three emitter types"),
)
PARAMETERS = NUMBERS + SWITCHES + SELECTS
BY_KEY = {p.key: p for p in PARAMETERS}
PARAMETER_KEYS = frozenset(BY_KEY)
SWITCH_KEYS = frozenset(p.key for p in SWITCHES)
SELECTORS = frozenset(p.selector for p in PARAMETERS)
READ_BLOCKS = (0, 60)
READ_COUNT = 60


def make_parameter_read(start):
    if start not in READ_BLOCKS:
        raise ValueError("Untested B3 read block")
    payload = bytes.fromhex("7e e1 01 b3 0c") + start.to_bytes(2, "big") + bytes((READ_COUNT, 0, 0))
    return payload + crc16(payload[1:]).to_bytes(2, "little")


def block_reply_payload(frame):
    """Validated 60-byte configuration pages, tested live on 2026-10-18."""
    if (len(frame) != READ_COUNT + 10 or not frame.startswith(bytes.fromhex("7e 01 e1 03 46"))
            or frame[7] != READ_COUNT
            or crc16(frame[1:-2]) != int.from_bytes(frame[-2:], "little")):
        return None
    start = int.from_bytes(frame[5:7], "big")
    return (start, frame[8:-2]) if start in READ_BLOCKS else None


def decode_raw(parameter, raw):
    if parameter.key in SWITCH_KEYS:
        if parameter.mask:
            return bool(raw & parameter.mask)
        return bool(raw) if raw in (0, 1) else None
    if parameter.options:
        return dict(parameter.options).get(raw)
    value = (raw - 256 if parameter.signed and raw >= 128 else raw) * parameter.multiplier
    return value if parameter.low <= value <= parameter.high else None


def reply_payload(frame):
    """Only CRC-valid responses can confirm a value; B6 commands are not state."""
    if (len(frame) != 11 or not frame.startswith(REPLY_PREFIX) or frame[7] != 1
            or crc16(frame[1:-2]) != int.from_bytes(frame[-2:], "little")):
        return None
    selector = int.from_bytes(frame[5:7], "big")
    return (selector, frame[8]) if selector in SELECTORS else None


def values_for_raw(selector, raw):
    return {p.key: decode_raw(p, raw) for p in PARAMETERS if p.selector == selector}


def make_parameter_command(key, value, previous_raw=None):
    p = BY_KEY[key]
    if key in SWITCH_KEYS:
        if not isinstance(value, bool):
            raise ValueError("Switch requires ON/OFF")
        if p.mask:
            if previous_raw is None or not 0 <= previous_raw <= 255:
                raise ValueError("No confirmed full DHW configuration byte")
            raw = (previous_raw & ~p.mask) | (p.mask if value else 0)
        else:
            raw = int(value)
    elif p.options:
        choices = {label: code for code, label in p.options}
        if value not in choices:
            raise ValueError("Unknown heat emitter type")
        raw = choices[value]
    else:
        if (isinstance(value, bool) or not isinstance(value, (float, int))
                or not math.isfinite(value) or value != int(value)
                or not p.low <= value <= p.high or (value - p.low) % p.step):
            raise ValueError(f"Required range {p.low}–{p.high}, step {p.step} {p.unit}")
        raw = (int(value) // p.multiplier) & 0xFF
    payload = COMMAND_PREFIX + p.selector.to_bytes(2, "big") + bytes((1, raw))
    return payload + crc16(payload[1:]).to_bytes(2, "little")
