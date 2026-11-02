from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from fido2.ctap import CtapError

from pico_key_manager import device


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.ref = device.DeviceRef('/dev/test-pico', 'Test Pico', 'test-only', 0x2e8a, 0x10fe)

    def test_utf8_pin_limit_and_confirmation(self):
        device.validate_pin('valid-test-pin', 'valid-test-pin')
        for pin, confirmation in [('short', 'short'), ('different1', 'different2'), ('é' * 32, 'é' * 32)]:
            with self.subTest(pin_length=len(pin)), self.assertRaises(device.DeviceError):
                device.validate_pin(pin, confirmation)

    def test_changed_device_rejected_before_opening(self):
        other = device.DeviceRef(self.ref.path, 'Another Pico', 'other-device', self.ref.vid, self.ref.pid)
        with patch.object(device, 'discover', return_value=[other]), patch.object(device, 'CtapHidDevice') as opened:
            with self.assertRaises(device.DeviceError):
                with device.session(self.ref):
                    self.fail('Changed hardware must not be accepted')
            opened.assert_not_called()

    def test_pin_change_uses_existing_and_new_pin_without_reset(self):
        ctap = Mock()
        ctap.get_info.return_value.min_pin_length = 4
        @contextmanager
        def fake_session(_):
            yield ctap
        with patch.object(device, 'session', fake_session), patch.object(device, 'ClientPin') as pin_client:
            device.change_pin(self.ref, 'old-test-pin', 'new-test-pin')
            pin_client.return_value.change_pin.assert_called_once_with('old-test-pin', 'new-test-pin')
            ctap.reset.assert_not_called()

    def test_cancellation_prevents_signing(self):
        cancelled = threading.Event()
        cancelled.set()
        operation = Mock()
        with self.assertRaises(device.DeviceError):
            device._timed(operation, cancelled, lambda _: None, 'test')
        operation.assert_not_called()

    def test_real_built_uf2_and_corrupted_file(self):
        path = Path(__file__).resolve().parents[1] / 'firmware/pico_fido_pico2_w_EXPERIMENTAL.uf2'
        result = device.validate_uf2(path)
        self.assertEqual(result['bytes'], path.stat().st_size)
        with tempfile.TemporaryDirectory() as folder:
            invalid = Path(folder) / 'invalid.uf2'
            data = bytearray(path.read_bytes())
            data[0] = 0
            invalid.write_bytes(data)
            with self.assertRaises(device.DeviceError):
                device.validate_uf2(invalid)

    def test_modified_firmware_rejected_before_target_access(self):
        path = Path(__file__).resolve().parents[1] / 'firmware/pico_fido_pico2_w_EXPERIMENTAL.uf2'
        with patch.object(device, 'boot_volume') as target:
            with self.assertRaises(device.DeviceError):
                device.flash_uf2(path, 'incorrect-checksum', lambda _: None)
            target.assert_not_called()

    def test_empty_credential_list_is_valid(self):
        @contextmanager
        def fake_session(_):
            yield Mock()
        manager = Mock()
        manager.get_metadata.return_value = {1: 0, 2: 100}
        manager.enumerate_rps.return_value = []
        with patch.object(device, 'session', fake_session), patch.object(device, '_manager', return_value=manager):
            result = device.list_credentials(self.ref, 'test-only-pin')
        self.assertEqual(result, {'credentials': [], 'count': 0, 'remaining': 100})

    def test_no_reset_without_unplug(self):
        clock = iter([0, 0, 61])
        event = Mock()
        event.wait.return_value = False
        with patch.object(device.time, 'monotonic', side_effect=lambda: next(clock)), patch.object(device, 'discover', return_value=[self.ref]), patch.object(device, 'session') as session:
            with self.assertRaises(device.DeviceError):
                device.reset_when_reconnected(self.ref, event, lambda _: None)
            session.assert_not_called()

    def test_pin_errors_distinguish_missing_and_temporarily_blocked(self):
        self.assertIn('先设置', device.friendly_error(CtapError(CtapError.ERR.PIN_NOT_SET)))
        self.assertIn('拔下设备', device.friendly_error(CtapError(CtapError.ERR.PIN_AUTH_BLOCKED)))
        self.assertIn('重新插入', device.friendly_error(CtapError(CtapError.ERR.NOT_ALLOWED)))


if __name__ == '__main__':
    unittest.main()
