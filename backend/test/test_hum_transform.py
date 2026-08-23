import unittest

from backend.hum_transform import TransformOptions, transform
from backend.melody import Note
from backend.models import Analysis, Bar


class HumTransformTest(unittest.TestCase):
    def setUp(self):
        self.analysis = Analysis(
            bpm=120, downbeat_offset_s=0, key="C", mode="major", duration=4,
            bars=[Bar(0, 0, 2, "C"), Bar(1, 2, 4, "G")],
        )
        self.notes = [Note(60, 0.02, 0.47), Note(64, 0.53, 1.02), Note(67, 1.1, 1.7)]

    def test_faithful_melody_preserves_one_event_per_hummed_note(self):
        source = [Note(61, 0.02, 0.47), Note(64, 0.53, 1.02)]
        notes = transform(source, self.analysis, "melody").instruments[0].notes
        self.assertEqual([(n.pitch, n.start, n.end) for n in notes], [(61, 0.02, 0.47), (64, 0.53, 1.02)])

    def test_optional_melody_corrections_are_explicit(self):
        notes = transform([Note(61, 0.02, 0.47), Note(64, 0.53, 1.02)], self.analysis, "melody", TransformOptions(snap_to_key=True, quantize=True)).instruments[0].notes
        self.assertEqual(notes[0].pitch, 60)
        self.assertEqual(notes[0].start, 0.0)

    def test_bass_is_low_and_uses_chord_tones(self):
        notes = transform(self.notes, self.analysis, "bass").instruments[0].notes
        self.assertTrue(all(24 <= note.pitch <= 55 for note in notes))
        self.assertTrue(all(note.start >= 0 and note.end > note.start for note in notes))

    def test_insufficient_pitched_notes_are_rejected(self):
        with self.assertRaises(ValueError):
            transform([self.notes[0]], self.analysis, "melody")


if __name__ == "__main__":
    unittest.main()
