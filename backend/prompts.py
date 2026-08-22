"""Per-part text prompts for Stable Audio 3.

Two things matter here:

1. Tempo and key go in the prompt even though the guide track already
   encodes them. Reinforcing them reduces drift.
2. The isolation clause ("solo instrument, no drums, no vocals") is not
   optional. Left to itself the model renders a full mix, and we need a
   single stem.
"""

from __future__ import annotations

from .models import Analysis, Part

# What the instrument should sound like, before the user's own style text.
#
# Harmony asks for a choir pad rather than "backing vocal harmony". SA3's
# training data (AudioSparx + Freesound) is thin on isolated vocal stems
# and rich in pads, and measured against a correct A minor guide the vocal
# phrasing came back with pitch classes outside the key while the choir
# phrasing stayed in it.
INSTRUMENT_PHRASES: dict[Part, str] = {
    "bass": "warm fingered electric bass guitar, dry DI signal, clean low end",
    "piano": "acoustic grand piano chords, close-miked, natural room",
    "guitar": "clean electric guitar chords, warm amp, light room",
    "drums": "tight acoustic drum kit, punchy kick and snare, dry room",
    "harmony": "warm choir pad, sustained aahs, soft attack, close-miked",
}

# What must not appear in the stem. Everything except the target part.
ISOLATION: dict[Part, str] = {
    "bass": "solo bass only, no drums, no vocals, no piano",
    "piano": "solo piano only, no drums, no vocals, no bass",
    "guitar": "solo guitar only, no drums, no vocals, no bass",
    "drums": "drums only, no melody, no vocals, no bass",
    "harmony": "single sustained layer, no drums, no bass, no percussion",
}


def build(part: Part, analysis: Analysis, style: str = "") -> str:
    """Compose the full prompt for one part.

    `style` is free text from the user, e.g. "bossa nova" or "gritty 70s funk".
    """
    pieces = [
        INSTRUMENT_PHRASES[part],
        style.strip(),
        f"{round(analysis.bpm)} BPM",
        f"{analysis.key} {analysis.mode}",
        ISOLATION[part],
    ]
    return ", ".join(piece for piece in pieces if piece)
