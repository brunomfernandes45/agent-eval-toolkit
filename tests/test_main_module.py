from __future__ import annotations

import runpy
import unittest
from unittest.mock import patch


class MainModuleTest(unittest.TestCase):
    def test_module_entry_point_propagates_cli_exit_code(self) -> None:
        with patch("agent_eval_toolkit.cli.main", return_value=7) as mocked_main:
            with self.assertRaises(SystemExit) as raised:
                runpy.run_module("agent_eval_toolkit.__main__", run_name="__main__")
        self.assertEqual(raised.exception.code, 7)
        mocked_main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
