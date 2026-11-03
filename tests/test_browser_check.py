import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from fido2.cose import ES256
from fido2.utils import websafe_encode
from fido2.webauthn import Aaguid, AttestedCredentialData, AttestationObject, AuthenticatorData, CollectedClientData

spec = importlib.util.spec_from_file_location('browser_check', Path(__file__).resolve().parents[1] / 'browser-key-check.py')
browser_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser_check)


class BrowserCheckTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.report = Path(self.directory.name) / 'result.json'
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential = AttestedCredentialData.create(
            Aaguid(bytes.fromhex('00112233445566778899aabbccddeeff')),
            b'synthetic-credential-not-for-logging', ES256.from_cryptography_key(self.key.public_key()),
        )
        patches = [patch.object(browser_check, 'REPORT', self.report),
                   patch.object(browser_check, 'discover', return_value=[object()]),
                   patch.object(browser_check, 'describe', return_value={
                       'aaguid': str(self.credential.aaguid), 'usb_id': '2e8a:10fe', 'pin_set': True})]
        for context in patches:
            context.start()
            self.addCleanup(context.stop)
        self.check = browser_check.Check()

    def register(self):
        options = self.check.begin('register')['publicKey']
        client_data = CollectedClientData.create('webauthn.create', options['challenge'], 'http://localhost:12345')
        auth_data = AuthenticatorData.create(hashlib.sha256(b'localhost').digest(), 0x45, 1, self.credential)
        signature = self.key.sign(bytes(auth_data) + client_data.hash, ec.ECDSA(hashes.SHA256()))
        attestation = AttestationObject.create('packed', auth_data, {'alg': -7, 'sig': signature})
        return self.check.finish('register', {
            'id': websafe_encode(self.credential.credential_id), 'rawId': websafe_encode(self.credential.credential_id),
            'type': 'public-key', 'authenticatorAttachment': 'cross-platform', 'browserTransports': ['usb'],
            'clientExtensionResults': {'credProps': {'rk': False}},
            'response': {'clientDataJSON': websafe_encode(client_data), 'attestationObject': websafe_encode(attestation)},
        })

    def authentication(self):
        options = self.check.begin('authenticate')['publicKey']
        client_data = CollectedClientData.create('webauthn.get', options['challenge'], 'http://localhost:12345')
        auth_data = AuthenticatorData.create(hashlib.sha256(b'localhost').digest(), 5, 2)
        signature = self.key.sign(bytes(auth_data) + client_data.hash, ec.ECDSA(hashes.SHA256()))
        return {'id': websafe_encode(self.credential.credential_id), 'rawId': websafe_encode(self.credential.credential_id),
                'type': 'public-key', 'clientExtensionResults': {}, 'response': {
                    'clientDataJSON': websafe_encode(client_data), 'authenticatorData': websafe_encode(auth_data),
                    'signature': websafe_encode(signature)}}

    def test_real_crypto_round_trip_and_redacted_report(self):
        registration = self.register()
        self.assertEqual(registration['attestation_type'], 'SELF')
        self.assertTrue(registration['external_usb_reported'])
        self.assertFalse(registration['backup_eligible'])
        self.assertFalse(registration['manufacturer_trust_verified'])
        self.assertTrue(self.check.finish('authenticate', self.authentication())['signature_verified'])
        saved = self.report.read_text()
        self.assertNotIn(websafe_encode(self.credential.credential_id), saved)
        self.assertNotIn('clientDataJSON', saved)
        self.assertTrue(json.loads(saved)['completed'])

    def test_altered_signature_cannot_complete_check(self):
        self.register()
        response = self.authentication()
        response['response']['signature'] = websafe_encode(b'invalid-signature')
        with self.assertRaises(ValueError):
            self.check.finish('authenticate', response)
        self.assertFalse(self.check.result['completed'])


if __name__ == '__main__':
    unittest.main()
