# -*- coding: utf-8 -*-
"""Tests for the read-only path guard."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from lib.safety import assert_outside, is_inside  # noqa: E402


class SafetyTest(unittest.TestCase):
    def test_is_inside(self):
        with tempfile.TemporaryDirectory() as root:
            account = os.path.join(root, "xwechat_files", "wxid_test_1234")
            os.makedirs(os.path.join(account, "db_storage"))
            self.assertTrue(is_inside(account, account))
            self.assertTrue(is_inside(account, os.path.join(account, "db_storage")))
            self.assertFalse(is_inside(account, os.path.join(root, "out")))

    def test_assert_outside_accepts_a_sibling_directory(self):
        with tempfile.TemporaryDirectory() as root:
            account = os.path.join(root, "account")
            out = os.path.join(root, "out")
            os.makedirs(account)
            os.makedirs(out)
            assert_outside(account, out)

    def test_assert_outside_rejects_a_child_directory(self):
        with tempfile.TemporaryDirectory() as root:
            account = os.path.join(root, "account")
            os.makedirs(account)
            for target in (account, os.path.join(account, "work"), os.path.join(account, "x", "y")):
                with self.assertRaises(SystemExit):
                    assert_outside(account, target)

    def test_assert_outside_handles_relative_paths(self):
        with tempfile.TemporaryDirectory() as root:
            account = os.path.join(root, "account")
            os.makedirs(os.path.join(account, "work"))
            cwd = os.getcwd()
            try:
                os.chdir(account)
                with self.assertRaises(SystemExit):
                    assert_outside(account, "work")
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
