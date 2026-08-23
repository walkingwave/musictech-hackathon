import tempfile
import unittest
from pathlib import Path

import pretty_midi

from backend import pipeline
from backend.hum_transform import TransformOptions
from backend.models import Analysis, Bar
from backend.pitch_tracking import Note


class _Session:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "midi").mkdir()
        self.analysis = Analysis(120, 0, "C", "major", 2, [Bar(0, 0, 2, "C")])
        self.hum_notes = [Note(61, 0.02, 0.47), Note(64, 0.53, 1.02)]
        self.saved = {}

    def midi_path(self, name):
        return self.root / "midi" / f"{name}.mid"

    def save_transform(self, name, transform):
        self.saved[name] = transform

    def to_dict(self):
        return {"pitch_tracking": {"tracker_id": "pyin"}}


class MidiOnlyPipelineTest(unittest.TestCase):
    def test_writes_midi_without_guide_or_audio(self):
        with tempfile.TemporaryDirectory() as root:
            session = _Session(root)
            result = pipeline.transform_hum_to_midi(session, "melody", "take", TransformOptions())
            path = Path(root) / result["midi_path"]
            self.assertTrue(path.is_file())
            self.assertFalse((Path(root) / "guides").exists())
            notes = pretty_midi.PrettyMIDI(str(path)).instruments[0].notes
            self.assertEqual([note.pitch for note in notes], [61, 64])
            self.assertIn("take", session.saved)


if __name__ == "__main__":
    unittest.main()
