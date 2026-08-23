"""Shared contract for hum pitch trackers.

Trackers may use pYIN, Basic Pitch, or a neural F0 model, but callers should
receive the same note events and enough provenance to explain why audio did or
did not become MIDI.  This module intentionally has no model dependency.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Note:
    """A discrete MIDI note extracted from a monophonic recording."""

    pitch: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def pitch_class(self) -> int:
        return self.pitch % 12


@dataclass(frozen=True)
class TrackingFrame:
    """One tracker analysis frame, retained for diagnosis rather than playback."""

    time: float
    f0_hz: float | None
    midi: float | None
    voiced: bool
    confidence: float | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrackingDiagnostics:
    """Counts explaining how raw pitch frames became the final note list."""

    total_frames: int = 0
    voiced_frames: int = 0
    rejected_unvoiced: int = 0
    rejected_low_confidence: int = 0
    segmented_notes: int = 0
    discarded_short_notes: int = 0
    merged_notes: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TrackingResult:
    """Normalized output of any pitch tracker."""

    tracker_id: str
    tracker_version: str = ""
    notes: list[Note] = field(default_factory=list)
    frames: list[TrackingFrame] = field(default_factory=list)
    diagnostics: TrackingDiagnostics = field(default_factory=TrackingDiagnostics)

    def summary(self) -> dict:
        """Small API/session-safe summary; frame data stays opt-in."""
        return {
            "tracker_id": self.tracker_id,
            "tracker_version": self.tracker_version,
            "note_count": len(self.notes),
            "diagnostics": self.diagnostics.to_dict(),
        }


class PitchTracker(Protocol):
    """Implementation-neutral audio-to-note interface."""

    id: str
    version: str

    def track(self, mono: np.ndarray, sr: int) -> TrackingResult:
        """Return normalized notes, raw frame evidence, and diagnostics."""
