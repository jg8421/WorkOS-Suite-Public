"""Public distribution must not silently register anyone's personal folders."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from suite.server import default_roots

class PublicDefaultsTests(unittest.TestCase):
    def test_fresh_install_registers_no_personal_directories(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(default_roots(), [])

    def test_explicit_valid_directory_and_malformed_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'WORKOS_SUITE_ROOTS':json.dumps([directory])}, clear=True):
                self.assertEqual(default_roots(), [Path(directory)])
            for setting in ('not-json','{}','[1]'):
                with patch.dict(os.environ, {'WORKOS_SUITE_ROOTS':setting}, clear=True):
                    self.assertEqual(default_roots(), [])

    def test_public_catalogue_does_not_expose_private_repositories(self):
        root=Path(__file__).resolve().parents[1]
        catalogue=json.loads((root/'repositories.json').read_text(encoding='utf-8'))
        self.assertEqual(len(catalogue),19)
        self.assertTrue(all(row.get('private') is False for row in catalogue))
        sources=json.loads((root/'components.lock.json').read_text(encoding='utf-8'))
        self.assertEqual(len(sources['components']),7)
        memory=next(row for row in sources['components'] if row['id']=='memory')
        self.assertNotIn('/',memory['repository'])
