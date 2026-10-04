"""Filesystem-boundary checks and an opt-in real official Tcl/Tk payload test."""
from pathlib import Path
import hashlib
import os
import tempfile
import unittest

from tools.package_suite import extract_tcltk_msi, native_tcltk_runtime_path


class NativePayloadSafetyTests(unittest.TestCase):
    def test_escaping_and_windows_alias_paths_are_rejected(self):
        for path in ('../outside', '/outside', 'C:/outside', 'tcl/../../outside',
                     'tcl/COM1.dll', 'tcl/trailing./file.tcl', 'tcl/bad|name.tcl'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                native_tcltk_runtime_path(path)

    def test_required_dlls_resources_and_license_are_selected(self):
        for path in ('DLLs/_tkinter.pyd', 'DLLs/tcl86t.dll', 'DLLs/tk86t.dll', 'DLLs/zlib1.dll',
                     'Lib/tkinter/__init__.py', 'tcl/tcl8.6/init.tcl', 'tcl/tcl8.6/encoding/utf-8.enc',
                     'tcl/tk8.6/license.terms'):
            self.assertTrue(native_tcltk_runtime_path(path), path)
        for path in ('Lib/idlelib/idle.py', 'libs/_tkinter.lib', 'tcl/tk8.6/demos/widget', 'tcl/nmake/tcl.mak'):
            self.assertFalse(native_tcltk_runtime_path(path), path)

    @unittest.skipUnless(os.name == 'nt' and os.environ.get('WORKOS_TCLTK_MSI'), 'Set WORKOS_TCLTK_MSI to the official cached input')
    def test_real_signed_payload_can_be_extracted_without_installing(self):
        source = Path(os.environ['WORKOS_TCLTK_MSI'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         'f2ddbb369a6ced92d0af547ec016ea26f2eaa97ce30100c0cecd3cf270b59e22')
        with tempfile.TemporaryDirectory(prefix='suite-native-test-') as temporary:
            root = Path(temporary)
            target = root/'python'
            target.mkdir()
            extract_tcltk_msi(source, target, root/'cab-work')
            self.assertTrue((target/'DLLs/zlib1.dll').is_file())
            self.assertTrue((target/'Lib/tkinter/__init__.py').is_file())
            self.assertTrue((target/'tcl/tcl8.6/init.tcl').is_file())
            self.assertTrue((target/'tcl/tk8.6/license.terms').is_file())
            self.assertFalse((target/'Lib/idlelib').exists())
            self.assertFalse((root/'cab-work').exists())
            self.assertTrue(all(p.resolve().is_relative_to(target) for p in target.rglob('*')))


if __name__ == '__main__':
    unittest.main()
