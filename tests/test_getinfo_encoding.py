"""Compile the actual firmware options encoder, then check its CBOR output."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from fido2 import cbor

ROOT = Path(__file__).resolve().parents[1]
TINYCBOR = ROOT / 'firmware/source/pico-keys-sdk/third-party/tinycbor/src'


@unittest.skipUnless(shutil.which('cc') and (TINYCBOR / 'cborencoder.c').exists(), 'Requires the firmware TinyCBOR build dependency and a host C compiler')
class GetInfoEncodingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.binary = Path(cls.folder.name) / 'encode-options'
        source = (ROOT / 'firmware/source/src/fido/cbor_get_info.c').read_text()
        fragment = source.split('CBOR_CHECK(cbor_encode_uint(&mapEncoder, 0x04));', 1)[1]
        fragment = fragment.split('CBOR_CHECK(cbor_encode_uint(&mapEncoder, 0x05));', 1)[0]
        fixture = Path(cls.folder.name) / 'encode-options.c'
        fixture.write_text('''
#include "cbor.h"
#include <stdlib.h>
#define FIDO2_OPT_NORK 4
#define FIDO2_OPT_AUV 2
#define FIDO2_OPT_MCUV_NOTRQD 8
#define CBOR_CHECK(expr) do { error = (expr); if (error != CborNoError) goto err; } while (0)
static unsigned get_opts(void) { return 0; }
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    bool enterprise_profile = atoi(argv[1]) != 0;
    bool pin_set = atoi(argv[2]) != 0;
    bool alwaysUv = pin_set;
    unsigned char output[1024];
    CborEncoder mapEncoder, arrayEncoder;
    CborError error = CborNoError;
#define file_has_data(unused) pin_set
    cbor_encoder_init(&mapEncoder, output, sizeof(output), 0);
''' + fragment + '''
    fwrite(output, 1, cbor_encoder_get_buffer_size(&mapEncoder, output), stdout);
    return 0;
err:
    return 1;
}
''')
        subprocess.run(['cc', '-std=c11', '-I', str(TINYCBOR), str(fixture),
                        str(TINYCBOR / 'cborencoder.c'),
                        str(TINYCBOR / 'cborencoder_close_container_checked.c'),
                        '-o', str(cls.binary)], check=True, capture_output=True)

    def test_options_use_canonical_cbor_with_and_without_enterprise_or_pin(self):
        for enterprise in (0, 1):
            for pin in (0, 1):
                with self.subTest(enterprise=enterprise, pin=pin):
                    encoded = subprocess.check_output([str(self.binary), str(enterprise), str(pin)])
                    decoded = cbor.decode(encoded)
                    self.assertEqual(encoded, cbor.encode(decoded))
                    self.assertIs(decoded['plat'], False)
                    self.assertIs(decoded['up'], True)
                    self.assertEqual(decoded['clientPin'], bool(pin))
                    self.assertEqual('ep' in decoded, bool(enterprise))


if __name__ == '__main__':
    unittest.main()
