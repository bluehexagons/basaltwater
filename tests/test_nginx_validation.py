from __future__ import annotations

import unittest

from lib.nginx_validation import validate_nginx_path


class TestNginxValidation(unittest.TestCase):
    def test_safe_paths(self):
        for path in ('/', '/docs/', '/var/www/site-1.0_assets'):
            self.assertEqual(validate_nginx_path(path), path)

    def test_unsafe_paths(self):
        for path in (None, [], '', '.', '/.', '/..', '/a/../b', '/a//b', '//a', '/a\n', '/a"', '/a#', '/a\\', '/a b', '/$uri', '/x;', '/' + 'a' * 4096):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_nginx_path(path)
