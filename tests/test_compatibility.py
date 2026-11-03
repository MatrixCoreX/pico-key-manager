import importlib.util
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pico_key_manager import device
from pico_key_manager.identity import PROFILES, is_pico_descriptor
from pico_key_manager.uf2 import inspect_uf2

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('flasher', ROOT / 'flash-pico2w.py')
flasher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flasher)


class CompatibilityTests(unittest.TestCase):
    def descriptor(self, vid, pid, name):
        return SimpleNamespace(vid=vid, pid=pid, product_name=name,
                               serial_number='synthetic', path='/dev/synthetic')

    def test_discover_accepts_both_pico_modes(self):
        descriptors = [self.descriptor(0x2e8a, 0x10fe, 'Pico Key'),
                       self.descriptor(0x1050, 0x0407, 'Pico Key')]
        with patch.object(device, 'list_descriptors', return_value=descriptors):
            self.assertEqual([ref.vid for ref in device.discover()], [0x2e8a, 0x1050])

    def test_genuine_yubikey_is_not_managed_as_a_pico(self):
        for name in ('YubiKey OTP+FIDO+CCID', 'Yubico Security Key', '', None):
            with self.subTest(name=name):
                self.assertFalse(is_pico_descriptor(self.descriptor(0x1050, 0x0407, name)))

    def test_product_name_alone_cannot_select_unrelated_hardware(self):
        self.assertFalse(is_pico_descriptor(self.descriptor(0x1234, 0x1234, 'Pico Key')))

    def test_profile_images_match_manifests_and_keep_distinct_outputs(self):
        self.assertEqual(len({config['file'] for config in PROFILES.values()}), len(PROFILES))
        for profile, config in PROFILES.items():
            with self.subTest(profile=profile):
                data, metadata = flasher.prepared_image(profile)
                self.assertEqual(metadata['usb_identity'], config['usb_identity'])
                self.assertLessEqual(int(inspect_uf2(data)['payload_end'], 16), 0x10100000)
                self.assertFalse(metadata['tested']['hardware'])

    def test_private_data_partition_is_rejected(self):
        data = bytearray((ROOT / 'firmware' / PROFILES['native']['file']).read_bytes())
        struct.pack_into('<I', data, 512 + 12, 0x10100000)
        with self.assertRaisesRegex(ValueError, 'PIN'):
            inspect_uf2(data)

    def test_non_firmware_family_is_rejected(self):
        data = bytearray((ROOT / 'firmware' / PROFILES['native']['file']).read_bytes())
        struct.pack_into('<I', data, 512 + 28, 0xE48BFF58)
        with self.assertRaisesRegex(ValueError, 'Arm Secure'):
            inspect_uf2(data)

    def test_flashing_rejects_changed_image_before_accessing_hardware(self):
        config = PROFILES['native']
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'firmware').mkdir()
            data = bytearray((ROOT / 'firmware' / config['file']).read_bytes())
            data[512 + 40] ^= 1
            (root / 'firmware' / config['file']).write_bytes(data)
            manifest = json.loads((ROOT / 'firmware' / config['manifest']).read_text())
            (root / 'firmware' / config['manifest']).write_text(json.dumps(manifest))
            with patch.object(flasher, 'ROOT', root):
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    flasher.prepared_image('native')


if __name__ == '__main__':
    unittest.main()
