import importlib.util
import pathlib
import json
import sys
import tempfile
import unittest
from unittest import mock

SESSION = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('verify_e1_native', SESSION / 'verify-e1-native.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class NativeEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (SESSION / 'native/serial.log').read_text(encoding='utf-8')

    def test_preserved_raw_capture_passes(self):
        rows, evidence = verify.verify_serial(self.text)
        self.assertEqual(len(rows), 30)
        self.assertEqual(evidence['boot_id'], '46da5751fa67bcfc')
        self.assertEqual(evidence['independent_updates'], 0)

    def test_empty_wrong_checksum_status_or_missing_row_rejected(self):
        first_row = next(line for line in self.text.splitlines() if 'WASMHOME_CSV,native,1,' in line)
        variants = ('', self.text.replace('0xDBB2F0D4', '0x00000000', 1),
                    self.text.replace(first_row, first_row.replace(',ok', ',invalid')),
                    self.text.replace(first_row, ''), self.text.replace(verify.START, verify.START.replace('warmup=5', 'warmup=4')),
                    self.text.replace('E1 scenario=native status=ok', 'E1 scenario=native status=invalid'))
        for number, text in enumerate(variants):
            with self.subTest(number=number), self.assertRaises(ValueError):
                verify.verify_serial(text)

    def test_duplicate_boot_reordered_rows_and_wrong_elf_rejected(self):
        first = next(line for line in self.text.splitlines() if 'WASMHOME_CSV,native,1,' in line)
        second = next(line for line in self.text.splitlines() if 'WASMHOME_CSV,native,2,' in line)
        reordered = self.text.replace(first, '__first__').replace(second, first).replace('__first__', second)
        boot = next(line for line in self.text.splitlines() if 'boot_observed' in line)
        variants = (self.text + '\n' + boot, reordered,
                    self.text.replace('ELF file SHA256:  35ed6729a', 'ELF file SHA256:  000000000'))
        for number, text in enumerate(variants):
            with self.subTest(number=number), self.assertRaises(ValueError):
                verify.verify_serial(text)

    def test_failed_verification_preserves_existing_derivatives(self):
        with tempfile.TemporaryDirectory() as directory:
            session = pathlib.Path(directory)
            native = session / 'native'
            native.mkdir()
            (native / 'build-inventory.json').write_text(json.dumps({'schema': 'wasmhome-native-e1-evidence-v1'}))
            expected = {'derived-native.json': b'{"previous": "checked"}\n',
                        'derived-native.csv': b'previous,checked\r\n'}
            for name, data in expected.items():
                (native / name).write_bytes(data)
            arguments = ['verify-e1-native.py', '--artifact', str(session / 'missing.bin')]
            with mock.patch.object(verify, 'SESSION', session), mock.patch.object(sys, 'argv', arguments):
                with self.assertRaises(SystemExit) as exit_result:
                    verify.main()
            self.assertEqual(exit_result.exception.code, 1)
            self.assertEqual({name: (native / name).read_bytes() for name in expected}, expected)


if __name__ == '__main__':
    unittest.main()
