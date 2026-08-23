"""Optional Spotify Basic Pitch adapter; imported only when explicitly selected."""
from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import soundfile as sf

from .pitch_tracking import Note, TrackingDiagnostics, TrackingResult


class BasicPitchTracker:
    id = "basic-pitch"

    def __init__(self) -> None:
        try:
            import basic_pitch  # type: ignore[import-not-found]
            from basic_pitch.inference import predict  # type: ignore[import-not-found]
        except ImportError as error:
            raise RuntimeError("Basic Pitch is not installed; run `uv sync --extra pitch-basic`") from error
        self.version = getattr(basic_pitch, "__version__", "unknown")
        self._predict = predict

    def track(self, mono: np.ndarray, sr: int) -> TrackingResult:
        # Basic Pitch's stable public interface accepts an audio path.
        with NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = Path(handle.name)
        try:
            sf.write(path, mono, sr)
            _, _, events = self._predict(str(path))
        finally:
            path.unlink(missing_ok=True)
        notes = [
            Note(pitch=int(round(event[2])), start=float(event[0]), end=float(event[1]))
            for event in events
            if float(event[1]) > float(event[0])
        ]
        return TrackingResult(
            tracker_id=self.id, tracker_version=self.version, notes=notes,
            diagnostics=TrackingDiagnostics(segmented_notes=len(notes)),
        )
