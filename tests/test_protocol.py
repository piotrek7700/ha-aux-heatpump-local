"""Self-contained protocol checks; no HA, gateway or private capture files required."""
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'custom_components/aux_heatpump'
spec = importlib.util.spec_from_file_location('aux_protocol_tests', COMPONENT / 'protocol.py')
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)


def hydraulic(length=45, enabled=True, inlet=-125):
    f = bytearray(length)
    f[:5] = bytes([0x7e, 1, 0xf1, 5, length])
    values = {7: b'\x03' if enabled else b'\x00', 8: b'\x02' if enabled else b'\x00',
              9: (520).to_bytes(2, 'big'), 13: b'\xff', 14: b'\x08',
              17: (169).to_bytes(2, 'big'), 19: (603).to_bytes(2, 'big'),
              21: inlet.to_bytes(2, 'big', signed=True), 23: (599).to_bytes(2, 'big'),
              25: (548).to_bytes(2, 'big'), 29: b'\x07', 30: b'\x30',
              33: (256).to_bytes(2, 'big'), 37: (319).to_bytes(2, 'big')}
    for offset, data in values.items():
        if offset + len(data) <= length-2:
            f[offset:offset+len(data)] = data
    f[-2:] = p.crc16(f[1:-2]).to_bytes(2, 'little')
    return bytes(f)


class ProtocolTests(unittest.TestCase):
    def test_known_crc_vector(self):
        self.assertEqual(p.crc16(b'123456789'), 0x4b37)

    def test_current_and_variable_length_telemetry(self):
        for length in (41, 45, 47, 64):
            f = hydraulic(length)
            values = p.decode_frame(p.StreamParser().feed(f)[0])
            self.assertEqual(values['dhw'], 54.8)
            self.assertEqual(values['dhw_setpoint'], 52)
            self.assertEqual(values['water_inlet'], -12.5)
            self.assertEqual(values['water_flow'], 1.69)
            self.assertEqual(values['buffer'], 31.9)
            self.assertTrue(values['dhw_enabled'])
            self.assertTrue(values['fast_dhw_enabled'])
            for key in ('ibh','tbh','mv1_1','mv1_2','mv2','mv3_1','mv3_2',
                        'pump_i','pump_o','pump_d','pump_flh','pump_s','et_heater','plate_heater'):
                self.assertTrue(values[key])

    def test_off_states_and_missing_sensor(self):
        values = p.decode_frame(hydraulic(41, False, -999))
        self.assertFalse(values['dhw_enabled'])
        self.assertFalse(values['fast_dhw_enabled'])
        self.assertIsNone(values['water_inlet'])

    def test_short_payload_never_reads_crc_as_data(self):
        for length in range(7, 65):
            frame = hydraulic(length)
            self.assertEqual(p.StreamParser().feed(frame), [frame])
            values = p.decode_frame(frame)
            for field in p.FIELDS:
                if field.prefix == p.B and field.offset + 2 > length-2:
                    self.assertNotIn(field.key, values)
            for field in p.BINARY_FIELDS:
                if field.prefix == p.B and field.offset >= length-2:
                    self.assertNotIn(field.key, values)

    def test_bad_crc_and_fragmented_tcp(self):
        frames = [hydraulic(41),hydraulic(),hydraulic(47)]
        bad = bytearray(frames[0]); bad[9] ^= 1
        raw = b'noise' + bad + b''.join(frames)
        for size in (1,2,7,20,4096):
            parser=p.StreamParser(); received=[]
            for i in range(0,len(raw),size):
                received.extend(parser.feed(raw[i:i+size]))
            self.assertEqual(received,frames)
            self.assertEqual(parser.buffer,b'')
            self.assertGreater(parser.discarded_bytes,0)

    def test_incomplete_frame_waits(self):
        frame=hydraulic(47); parser=p.StreamParser()
        self.assertEqual(parser.feed(frame[:-1]),[])
        self.assertEqual(parser.feed(frame[-1:]),[frame])

    def test_pressure_estimate_range(self):
        for raw,kpa in ((70,894),(140,1121),(500,2966),(630,3984)):
            self.assertLessEqual(abs(p.estimate_pressure(raw)-kpa),1.3)
        for raw in (0,69,631,65535):self.assertIsNone(p.estimate_pressure(raw))

    def test_source_and_json_files(self):
        for f in COMPONENT.rglob('*.py'):compile(f.read_text(encoding='utf-8-sig'),str(f),'exec')
        for f in COMPONENT.rglob('*.json'):json.loads(f.read_text(encoding='utf-8-sig'))
        manifest=json.loads((COMPONENT/'manifest.json').read_text(encoding='utf-8-sig'))
        const=(COMPONENT/'const.py').read_text(encoding='utf-8')
        self.assertEqual(manifest['version'],re.search(r'VERSION = "([^"]+)"',const)[1])
        self.assertEqual(manifest['domain'],'aux_heatpump')

    def test_readme_local_links_exist(self):
        text=(ROOT/'README.md').read_text(encoding='utf-8-sig')
        for link in re.findall(r'\]\(([^)]+)\)',text):
            if '://' not in link and not link.startswith('#'):
                self.assertTrue((ROOT/link).is_file(),link)
        self.assertEqual(len(list((ROOT/'docs/images').glob('*.jpg'))),5)

if __name__ == '__main__':unittest.main()
