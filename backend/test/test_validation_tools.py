"""Offline contract tests for developer validation tools.

These tests never require a DeepSeek key or make network requests.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import interpret
from backend.test.deepseek_test_cli import context_from_metadata, validate_plan
from backend.test.run_artifacts import create_run_directory


class RunArtifactTests(unittest.TestCase):
    def test_timestamped_directory_is_unique(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with patch("backend.test.run_artifacts.TEST_RUNS_DIR", Path(temporary)):
                first = create_run_directory("analysis_test")
                second = create_run_directory("analysis_test")
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())
            self.assertNotEqual(first, second)
            self.assertTrue(first.name.startswith("analysis_test_"))

    def test_nonempty_explicit_output_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            output.mkdir()
            (output / "existing.json").write_text("{}")
            with self.assertRaises(FileExistsError):
                create_run_directory("analysis_test", output=output)


class DeepSeekValidationContractTests(unittest.TestCase):
    def test_metadata_becomes_bounded_context(self) -> None:
        context = context_from_metadata({"analysis": {
            "tempo_bpm": 100.0, "key": "A", "mode": "minor", "chords": [{}, {}],
        }}, ["bass"], "bossa nova")
        self.assertEqual(context.bpm, 100.0)
        self.assertEqual(context.key, "A")
        self.assertEqual(context.bars, 2)
        self.assertEqual(context.existing_parts, ["bass"])

    def test_valid_plan_passes_contract_checks(self) -> None:
        plan = interpret.Plan.model_validate({
            "tracks": [{"part": "bass", "name": "upright bass", "instrument": "upright bass", "style": ""}],
            "style": "bossa nova", "groove": "bossa", "bpm": 100, "key": "A",
            "mode": "minor", "bars": 4, "notes": "",
        })
        self.assertEqual(validate_plan(plan, expect_tracks=True), [])

    def test_empty_tracks_fail_when_required(self) -> None:
        plan = interpret.Plan.model_validate({
            "tracks": [], "style": "", "groove": "straight", "bpm": None,
            "key": None, "mode": None, "bars": None, "notes": "",
        })
        self.assertTrue(validate_plan(plan, expect_tracks=True))

    def test_interpreter_failure_uses_rules_fallback(self) -> None:
        with patch("backend.interpret._interpret_with_deepseek", side_effect=RuntimeError("bad JSON")):
            plan, source = interpret.interpret_with_source("add bass")
        self.assertEqual(source, "rules")
        self.assertTrue(plan.tracks)


if __name__ == "__main__":
    unittest.main()
