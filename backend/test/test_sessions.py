"""Offline lifecycle tests; temporary roots ensure no real session is touched."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.session import Session


class SessionLifecycleTests(unittest.TestCase):
    def test_summary_and_delete_are_confined_to_session_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sessions"
            with patch("backend.session.SESSIONS_DIR", root):
                session = Session.create(np.zeros(32, dtype=np.float32), 44100)
                session.set_display_name("Test project")
                summary = session.summary()
                self.assertEqual(summary["display_name"], "Test project")
                self.assertEqual(summary["track_names"], [])
                session.delete()
                self.assertFalse(session.root.exists())

    def test_malformed_id_cannot_load(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("backend.session.SESSIONS_DIR", Path(temporary)):
                with self.assertRaises(FileNotFoundError):
                    Session.load("../../outside")


if __name__ == "__main__":
    unittest.main()
