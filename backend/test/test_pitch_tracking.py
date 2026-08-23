import unittest

from backend.melody import Note, PyinTracker
from backend.pitch_tracking import (
    TrackingDiagnostics,
    TrackingFrame,
    TrackingResult,
)


class PitchTrackingContractTest(unittest.TestCase):
    def test_result_summary_excludes_raw_frames(self):
        result = TrackingResult(
            tracker_id="test",
            tracker_version="1",
            notes=[Note(60, 0.0, 0.5)],
            frames=[TrackingFrame(0.0, 261.63, 60.0, True, 0.9)],
            diagnostics=TrackingDiagnostics(total_frames=1, voiced_frames=1),
        )
        self.assertEqual(result.summary()["note_count"], 1)
        self.assertNotIn("frames", result.summary())
        self.assertEqual(result.summary()["diagnostics"]["voiced_frames"], 1)

    def test_note_remains_available_from_melody_module(self):
        note = Note(60, 0.0, 0.5)
        self.assertEqual(note.pitch_class, 0)
        self.assertEqual(note.duration, 0.5)

    def test_tracker_reports_frame_and_rejection_diagnostics(self):
        import soundfile as sf
        audio, sr = sf.read("samples/fixtures/amin_100.wav", dtype="float32")
        result = PyinTracker().track(audio, sr)
        self.assertGreater(len(result.frames), 0)
        self.assertEqual(result.diagnostics.total_frames, len(result.frames))
        self.assertGreaterEqual(result.diagnostics.rejected_unvoiced, 0)


if __name__ == "__main__":
    unittest.main()
