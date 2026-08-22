"""Shared data types.

These are the contract between pipeline stages. Every stage takes and
returns one of these rather than loose dicts, so a change to the shape
shows up as an error instead of a silent KeyError three modules later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

# The four backing parts we generate. Adding a part means: add it here,
# add an arranger in arrange.py, and add a prompt template in prompts.py.
Part = Literal["bass", "piano", "guitar", "drums", "harmony"]
PARTS: tuple[Part, ...] = ("bass", "piano", "guitar", "drums", "harmony")


@dataclass
class Bar:
    """One bar of the song, with the chord we think is playing over it."""

    index: int
    start: float  # seconds
    end: float  # seconds
    chord: str  # e.g. "Am", "F", "C"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Analysis:
    """Everything we infer from the input vocal.

    This is the source of truth for the whole pipeline: arrangers read it
    to place notes, prompts read it to describe tempo and key, and the
    frontend renders it so the user can correct the chords.
    """

    bpm: float
    downbeat_offset_s: float  # where bar 1 starts, in seconds
    key: str  # e.g. "A"
    mode: str  # "major" | "minor"
    duration: float  # seconds
    bars: list[Bar] = field(default_factory=list)

    @property
    def beats_per_bar(self) -> int:
        # We assume 4/4 throughout. Enough for a hackathon; revisit if
        # someone hums in 3/4 on stage.
        return 4

    @property
    def seconds_per_beat(self) -> float:
        return 60.0 / self.bpm

    @property
    def seconds_per_bar(self) -> float:
        return self.seconds_per_beat * self.beats_per_bar

    def to_dict(self) -> dict:
        return {
            "bpm": self.bpm,
            "downbeat_offset_s": self.downbeat_offset_s,
            "key": self.key,
            "mode": self.mode,
            "duration": self.duration,
            "bars": [b.to_dict() for b in self.bars],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Analysis":
        return cls(
            bpm=d["bpm"],
            downbeat_offset_s=d["downbeat_offset_s"],
            key=d["key"],
            mode=d["mode"],
            duration=d["duration"],
            bars=[Bar(**b) for b in d.get("bars", [])],
        )


@dataclass
class StemResult:
    """One generated backing stem, plus the provenance to reproduce it."""

    part: Part
    wav_path: str
    midi_path: str
    backend_used: str  # may differ from the request, if fallback fired
    prompt: str
    noise: float
    seed: int
    duration: float = 0.0  # length of the generated audio, seconds
    n_bars: int = 0  # bars the stem spans (may exceed the input vocal)

    def to_dict(self) -> dict:
        return asdict(self)
